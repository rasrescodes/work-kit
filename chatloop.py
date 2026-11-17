#!/usr/bin/env python3
"""chatloop: finish a build with a chat-only AI. You are the courier.

  python chatloop.py                     EASY MODE: packs once, then watches the clipboard. Your whole job:
                                         copy the chat's reply, wait for the beep, paste into the chat.
  python chatloop.py status              where things stand: rounds so far, last test result, files touched
  python chatloop.py log                 the full history, round by round
  python chatloop.py undo                put back the files the last round changed
  python chatloop.py pack [paths...]     by hand: project -> one text file + clipboard
  python chatloop.py apply [-t "cmd"]    by hand: chat reply (clipboard) -> files changed, tests run, report

No installs needed. Works on Windows, Mac and Linux. Run it from the project folder.
Everything it keeps lives in the .chatloop folder (hidden from git).
"""
import difflib, json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path.cwd()
DIR = ROOT / ".chatloop"
MAX_FILE = 200_000
SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "Cargo.lock", "uv.lock"}
SKIP_DIRS = {".git", "node_modules", ".chatloop", "dist", "build", ".next", "__pycache__", ".venv", "venv", "target", "bin", "obj"}

RULES = """You are finishing a half-built software project. You cannot run anything yourself.
I run a small tool that applies your reply to the real files and sends you back the results.
So every reply must use ONLY these blocks. Put the whole reply inside one code block fenced
with four backticks so I can copy it cleanly. Text outside blocks is ignored by the tool.

=== FILE: path/from/project/root ===
(the complete new content of the file; use for new files and small files)
=== END FILE ===

=== EDIT: path/from/project/root ===
<<<< FIND
(exact lines that exist once in the file, copied character for character)
==== REPLACE
(the lines to put there instead)
>>>> END
=== END EDIT ===
(an EDIT block may hold several FIND/REPLACE pairs; use EDIT for big files)

=== DELETE: path ===
=== NEED: path ===          (ask to see a file you were not shown; you get it next round)
=== RUN: command ===        (ask me to run a command; you get its output next round)
=== NOTE ===
(one or two plain sentences for the human: what you did this round and what is left)
=== END NOTE ===

Rules: never guess at a file you have not seen, ask with NEED. Make small steps that each
leave the tests passing. Never shorten a file with "rest unchanged" comments. Every reply
must have a NOTE. When the whole job is done and tests pass, say DONE in the NOTE.
"""

# ---------- look ----------

if sys.platform == "win32":
    os.system("")  # switches on colour in the Windows console
COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(code, text):
    return f"\033[{code}m{text}\033[0m" if COLOUR else text


def green(t): return c("32", t)
def red(t): return c("31", t)
def yellow(t): return c("33", t)
def dim(t): return c("2", t)
def bold(t): return c("1", t)


def clock():
    return time.strftime("%H:%M:%S")


def rule(title=""):
    line = "-" * 64
    print("\n" + bold(f"--- {title} " + "-" * max(4, 58 - len(title))) if title else dim(line))


def step(text):
    print(f"{dim(clock())}  {text}")


# ---------- plumbing ----------

def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def clip_set(text):
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16le"), check=True)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def clip_get():
    try:
        if sys.platform == "win32":
            r = sh(["powershell", "-NoProfile", "-Command",
                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"])
        elif sys.platform == "darwin":
            r = sh(["pbpaste"])
        else:
            r = sh(["xclip", "-selection", "clipboard", "-o"])
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def load(name, default):
    p = DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(name, data):
    DIR.mkdir(exist_ok=True)
    (DIR / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def hide_from_git():
    ex = ROOT / ".git" / "info" / "exclude"
    if ex.parent.exists():
        cur = ex.read_text(encoding="utf-8") if ex.exists() else ""
        if ".chatloop/" not in cur:
            ex.write_text(cur.rstrip("\n") + "\n.chatloop/\n", encoding="utf-8")


def project_files():
    r = sh(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT) if (ROOT / ".git").exists() else None
    if r and r.returncode == 0:
        files = [f for f in r.stdout.splitlines() if f]
    else:
        files = []
        for d, dirs, names in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            files += [str((Path(d) / n).relative_to(ROOT)).replace("\\", "/") for n in names]
    return sorted(f for f in files if not set(Path(f).parts) & SKIP_DIRS and (ROOT / f).is_file())


def read_text(path):
    """Return file text, or None if it is binary, too big or a lock file."""
    p = ROOT / path
    if p.name in SKIP_NAMES or p.stat().st_size > MAX_FILE:
        return None
    raw = p.read_bytes()
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def dump(paths):
    parts = []
    for f in paths:
        t = read_text(f)
        if t is not None:
            parts.append(f"=== FILE: {f} ===\n{t.replace(chr(13), '')}\n=== END FILE ===\n")
    return "\n".join(parts)


def safe(path):
    p = (ROOT / path.strip()).resolve()
    if ROOT.resolve() not in p.parents:
        raise ValueError("outside the project, refused")
    return p


def tail(text, n=150):
    ls = text.rstrip().split("\n")
    return ("(first lines cut)\n" if len(ls) > n else "") + "\n".join(ls[-n:])


def delta(old, new):
    """Lines added and removed between two texts."""
    add = rem = 0
    for ln in difflib.ndiff(old.split("\n") if old else [], new.split("\n") if new else []):
        add += ln.startswith("+ ")
        rem += ln.startswith("- ")
    return add, rem


# ---------- pack ----------

def cmd_pack(args):
    hide_from_git()
    DIR.mkdir(exist_ok=True)
    files = project_files()
    wanted = files
    if args:
        keys = [a.replace("\\", "/").rstrip("/") for a in args]
        wanted = [f for f in files if any(f == k or f.startswith(k + "/") for k in keys)]
    skipped = [f for f in wanted if read_text(f) is None]
    text = (RULES + "\n\nPROJECT FILE LIST (every file, including ones not shown below):\n"
            + "\n".join(files) + "\n\nFILE CONTENTS:\n\n" + dump(wanted)
            + "\n\nMY GOAL: (write here what is finished, what is left, and how to run the tests)\n")
    (DIR / "pack.txt").write_text(text, encoding="utf-8")
    ok = clip_set(text)
    step(f"packed {len(wanted) - len(skipped)} files, {len(text):,} characters (about {len(text)//4:,} tokens)")
    if skipped:
        step(dim(f"left out {len(skipped)} binary, lock or very large files"))
    step(green("copied to the clipboard") if ok else red("clipboard failed") + ", open .chatloop/pack.txt and copy it")
    if len(text) > 400_000:
        step(yellow("This is big. Upload .chatloop/pack.txt as a file instead, or pack only some folders: pack src tests"))


# ---------- reading the reply ----------

def parse(reply):
    """Turn the chat reply into a list of (kind, arg, body) actions."""
    lines = reply.replace("\r\n", "\n").split("\n")
    acts, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        i += 1
        if not (s.startswith("=== ") and s.endswith(" ===")):
            continue
        kind, _, arg = s[4:-4].partition(":")
        kind, arg = kind.strip().upper(), arg.strip()
        if kind in ("FILE", "EDIT", "NOTE"):
            end, body = f"=== END {kind} ===", []
            while i < len(lines) and lines[i].strip() != end:
                body.append(lines[i])
                i += 1
            i += 1
            acts.append((kind, arg, "\n".join(body)))
        elif kind in ("DELETE", "NEED", "RUN"):
            acts.append((kind, arg, ""))
    return acts


def pairs(body):
    res, find, rep, mode = [], [], [], None
    for ln in body.split("\n"):
        t = ln.strip()
        if t == "<<<< FIND":
            find, rep, mode = [], [], "f"
        elif t == "==== REPLACE" and mode == "f":
            mode = "r"
        elif t == ">>>> END" and mode == "r":
            res.append(("\n".join(find), "\n".join(rep)))
            mode = None
        elif mode == "f":
            find.append(ln)
        elif mode == "r":
            rep.append(ln)
    return res


def loose_find(text, find):
    """Match ignoring spaces at line ends. Return (start, end) or None if not exactly one match."""
    tl = text.split("\n")
    fl = [x.rstrip() for x in find.split("\n")]
    hits = [k for k in range(len(tl) - len(fl) + 1) if [x.rstrip() for x in tl[k:k + len(fl)]] == fl]
    if len(hits) != 1:
        return None
    start = sum(len(x) + 1 for x in tl[:hits[0]])
    return start, start + len("\n".join(tl[hits[0]:hits[0] + len(fl)]))


def write(p, text, crlf):
    p.parent.mkdir(parents=True, exist_ok=True)
    if text and not text.endswith("\n"):
        text += "\n"
    p.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


# ---------- one round ----------

def cmd_apply(args, reply=None):
    hide_from_git()
    DIR.mkdir(exist_ok=True)
    cfg = load("config.json", {})
    if args[:1] == ["-t"] and len(args) > 1:
        cfg["test"] = args[1]
        save("config.json", cfg)
    if reply is None:
        reply = clip_get()
        if "=== " not in reply and (DIR / "reply.txt").exists():
            reply = (DIR / "reply.txt").read_text(encoding="utf-8")
    acts = parse(reply)
    if not acts:
        sys.exit("No blocks found. Copy the chat's whole reply first, or save it as .chatloop/reply.txt")

    hist = load("history.json", [])
    n = len(hist) + 1
    rounds = DIR / "rounds"
    rounds.mkdir(exist_ok=True)
    (rounds / f"{n:03d}-reply.txt").write_text(reply, encoding="utf-8")

    rule(f"ROUND {n}")
    count = {}
    for kind, _, _ in acts:
        count[kind] = count.get(kind, 0) + 1
    names = {"FILE": "whole file", "EDIT": "edit", "DELETE": "delete", "NEED": "file request", "RUN": "command request"}
    summary = ", ".join(f"{v} {names[k]}{'s' if v > 1 else ''}" for k, v in count.items() if k in names)
    step("chat sent: " + (summary or "a note only"))

    backup = DIR / "backup"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    created, log, extra, changed, fails, note = [], [], [], [], 0, ""

    def keep(p):
        rel = p.relative_to(ROOT.resolve())
        if p.exists():
            dst = backup / rel
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
        elif str(rel) not in created:
            created.append(str(rel).replace("\\", "/"))

    def ok(verb, path, add=0, rem=0):
        log.append(f"OK    {verb} {path}")
        changed.append(path)
        nums = f"{green('+' + str(add))} {red('-' + str(rem))}" if add or rem else ""
        step(f"{green('ok  ')} {verb:8}{path}  {nums}")

    def fail(what, why):
        nonlocal fails
        fails += 1
        log.append(f"FAIL  {what}: {why}")
        step(f"{red('FAIL')} {what}: {why}")

    for kind, arg, body in acts:
        try:
            if kind == "NOTE":
                note = " ".join(body.split())
            elif kind == "FILE":
                p = safe(arg)
                old = p.read_bytes().decode("utf-8", "replace") if p.exists() else ""
                is_new = not p.exists()
                keep(p)
                write(p, body, "\r\n" in old)
                ok("created" if is_new else "rewrote", arg, *delta(old.replace("\r\n", "\n").rstrip("\n"), body.rstrip("\n")))
            elif kind == "DELETE":
                p = safe(arg)
                if p.exists():
                    keep(p)
                    p.unlink()
                    ok("deleted", arg)
                else:
                    fail(f"delete {arg}", "no such file")
            elif kind == "EDIT":
                p = safe(arg)
                if not p.exists():
                    fail(f"edit {arg}", "no such file")
                    continue
                raw = p.read_bytes()
                crlf, text = b"\r\n" in raw, raw.decode("utf-8").replace("\r\n", "\n")
                before, ps, bad = text, pairs(body), 0
                if not ps:
                    fail(f"edit {arg}", "no FIND/REPLACE pair understood")
                    continue
                for k, (find, rep) in enumerate(ps, 1):
                    cnt = text.count(find) if find else 0
                    if cnt == 1:
                        text = text.replace(find, rep)
                        continue
                    span = loose_find(text, find) if find else None
                    if span:
                        text = text[:span[0]] + rep + text[span[1]:]
                    else:
                        bad += 1
                        fail(f"edit {arg} pair {k}", f"FIND text matched {cnt} times, need exactly 1. Nothing in this file was changed.")
                if not bad:
                    keep(p)
                    write(p, text, crlf)
                    ok("edited", arg, *delta(before, text))
            elif kind == "NEED":
                hits = [f for f in project_files() if f == arg or f.startswith(arg.rstrip("/") + "/")]
                extra.append(dump(hits) if hits else f"NEED {arg}: no such file or folder")
                step(f"{yellow('send')} {arg}  " + dim(f"{len(hits)} file{'s' if len(hits) != 1 else ''} added to the report"))
            elif kind == "RUN":
                print("\a", end="")
                if input(f"{clock()}  {yellow('ASK ')} chat wants to run:  {bold(arg)}\n          allow? [y/N] ").strip().lower() == "y":
                    r = subprocess.run(arg, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
                    extra.append(f"RUN {arg}\nexit code {r.returncode}\n" + tail(r.stdout + r.stderr))
                    step(f"ran it, exit code {r.returncode}")
                else:
                    extra.append(f"RUN {arg}: the human refused to run this")
                    step("refused, the chat will be told")
        except Exception as e:
            fail(f"{kind.lower()} {arg}", str(e))

    (backup / "_created.json").write_text(json.dumps(created))
    report = ["RESULT OF YOUR LAST REPLY", *log]
    verdict, secs = "none", 0
    if cfg.get("test"):
        step(f"running tests: {cfg['test']} ...")
        t0 = time.time()
        r = subprocess.run(cfg["test"], shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
        secs = time.time() - t0
        verdict = "PASSED" if r.returncode == 0 else "FAILED"
        output = r.stdout + r.stderr
        report += ["", f"TESTS {verdict}: `{cfg['test']}` exit code {r.returncode}, {secs:.0f}s", tail(output)]
        step(f"tests {green('PASSED') if verdict == 'PASSED' else red('FAILED')} in {secs:.0f}s")
        if verdict == "FAILED":
            for ln in tail(output, 8).split("\n"):
                print("          " + dim(ln[:110]))
    else:
        report += ["", "No test command set, nothing was run."]
        step(dim("no test command set, nothing run"))
    if extra:
        report += ["", *extra]
    report += ["", "Reply with the next blocks, same format as before."]
    text = "\n".join(report)
    (DIR / "report.txt").write_text(text, encoding="utf-8")
    (rounds / f"{n:03d}-report.txt").write_text(text, encoding="utf-8")
    copied = clip_set(text)

    done = "DONE" in note and verdict in ("PASSED", "none") and not fails
    hist.append({"round": n, "time": time.strftime("%Y-%m-%d %H:%M"), "note": note, "changed": changed,
                 "fails": fails, "tests": verdict, "secs": round(secs), "done": done})
    save("history.json", hist)

    if note:
        print(f"\n          {bold('chat says:')} {note}")
    print()
    if done:
        step(green(bold("FINISHED: the chat says the job is done and nothing failed.")) + "  Check it yourself: git diff")
    elif copied:
        step(bold("NEXT: paste into the chat (Ctrl+V).") + dim(f"  report is {len(text):,} characters"))
    else:
        step(red("clipboard failed.") + " Open .chatloop/report.txt, copy it, paste into the chat.")
    return done


# ---------- watching ----------

def ours(text):
    t = text.lstrip()
    return t.startswith("RESULT OF YOUR LAST REPLY") or t.startswith("You are finishing a half-built")


def cmd_go(args):
    DIR.mkdir(exist_ok=True)
    cfg = load("config.json", {})
    hist = load("history.json", [])
    rule("chatloop")
    print(f"  project   {ROOT.name}  ({len(project_files())} files)")
    if "test" not in cfg:
        cfg["test"] = input("  Command that runs your tests (Enter for none): ").strip()
        save("config.json", cfg)
    print(f"  tests     {cfg['test'] or 'none set'}")
    print(f"  rounds    {len(hist)} so far" + (f", last tests {hist[-1]['tests']}" if hist else ""))
    print(dim("  stop with Ctrl+C.  Other window: python chatloop.py status | log | undo"))
    rule()
    fresh = not hist
    if input(f"Pack the project and copy it for the chat? [{'Y/n' if fresh else 'y/N'}] ").strip().lower() in (["", "y"] if fresh else ["y"]):
        cmd_pack(args)
        step(bold("NEXT: paste into the chat (or upload .chatloop/pack.txt), and write your goal at the bottom."))
    seen = clip_get()
    waiting_since = None
    try:
        while True:
            if waiting_since is None:
                waiting_since = time.time()
                step(dim("waiting: copy the chat's whole reply and I take it from there..."))
            time.sleep(1.5)
            cur = clip_get()
            if cur == seen or not cur.strip():
                continue
            seen = cur
            if ours(cur):
                continue
            if not parse(cur):
                if "===" in cur:
                    step(yellow("that copy had no complete blocks, ignored. Copy the whole reply."))
                continue
            done = cmd_apply([], reply=cur)
            seen = clip_get()
            print("\a", end="", flush=True)
            waiting_since = None
            if done:
                break
    except KeyboardInterrupt:
        print()
        step("stopped. Start again any time; history is kept.")


# ---------- looking back ----------

def cmd_status(_):
    hist, cfg = load("history.json", []), load("config.json", {})
    rule("chatloop status")
    print(f"  project   {ROOT.name}")
    print(f"  tests     {cfg.get('test') or 'none set'}")
    if not hist:
        print("  no rounds yet. Start with: python chatloop.py")
        return
    last = hist[-1]
    touched = sorted({f for h in hist for f in h["changed"]})
    passed = sum(h["tests"] == "PASSED" for h in hist)
    colour = green if last["tests"] == "PASSED" else red if last["tests"] == "FAILED" else dim
    print(f"  rounds    {len(hist)}  ({passed} passed tests, {sum(h['fails'] > 0 for h in hist)} had failed changes)")
    print(f"  last      round {last['round']} at {last['time']}, tests {colour(last['tests'])}")
    print(f"  finished  {green('yes') if last.get('done') else 'not yet'}")
    if last["note"]:
        print(f"  chat said {last['note']}")
    print(f"  files touched so far ({len(touched)}):")
    for f in touched:
        print("    " + f)
    print(dim("  full history: python chatloop.py log     see the code changes: git diff"))


def cmd_log(_):
    hist = load("history.json", [])
    if not hist:
        sys.exit("No rounds yet.")
    for h in hist:
        colour = green if h["tests"] == "PASSED" else red if h["tests"] == "FAILED" else dim
        bad = red(f"  {h['fails']} failed change{'s' if h['fails'] > 1 else ''}") if h["fails"] else ""
        print(f"{bold('round ' + str(h['round']))}  {dim(h['time'])}  tests {colour(h['tests'])}{bad}")
        if h["note"]:
            print(f"    {h['note']}")
        for f in h["changed"]:
            print(dim(f"    changed {f}"))
    print(dim("\nexact text of every round is in .chatloop/rounds/"))


def cmd_undo(_):
    backup = DIR / "backup"
    if not backup.exists():
        sys.exit("Nothing to undo.")
    meta = backup / "_created.json"
    for f in json.loads(meta.read_text()) if meta.exists() else []:
        if (ROOT / f).exists():
            (ROOT / f).unlink()
            step("removed " + f)
    for d, _, names in os.walk(backup):
        for name in names:
            if name == "_created.json":
                continue
            src = Path(d) / name
            dst = ROOT / src.relative_to(backup)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            step("restored " + str(src.relative_to(backup)))
    shutil.rmtree(backup)
    step("last round undone. Tell the chat you undid it, so it does not build on those changes.")


if __name__ == "__main__":
    cmds = {"go": cmd_go, "pack": cmd_pack, "apply": cmd_apply, "undo": cmd_undo, "status": cmd_status, "log": cmd_log}
    name = sys.argv[1] if len(sys.argv) > 1 else "go"
    if name not in cmds:
        sys.exit(__doc__)
    cmds[name](sys.argv[2:])
