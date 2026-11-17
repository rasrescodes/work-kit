# chatloop

Finish a half-built project when all you have is a chat AI in a browser. One Python file, nothing to install.

The chat cannot touch your files or run your tests. This tool does that part, and you carry the text between the two: copy the chat's reply, wait for the beep, paste the result back.

## Get it

Windows (PowerShell), inside your project folder:

```
curl.exe -O https://raw.githubusercontent.com/rasrescodes/work-kit/main/chatloop.py
```

Mac or Linux:

```
curl -O https://raw.githubusercontent.com/rasrescodes/work-kit/main/chatloop.py
```

Needs Python 3.8 or newer. If `python` is not found on Windows, try `py chatloop.py`.

## Use it

```
python chatloop.py
```

1. It asks for the command that runs your tests, packs the project into one text and copies it.
2. Paste that into the chat (or upload `.chatloop/pack.txt` if it is large) and write your goal at the bottom.
3. Copy the chat's whole reply. The tool sees it, changes the files, runs the tests and copies a report.
4. Paste the report into the chat. Repeat 3 and 4 until it says FINISHED.

## Other commands

| command | what it does |
|---|---|
| `python chatloop.py status` | rounds so far, last test result, files touched |
| `python chatloop.py log` | the history, round by round |
| `python chatloop.py undo` | puts back the files the last round changed |
| `python chatloop.py pack src tests` | pack only some folders, for big projects |

## Good to know

- Everything it keeps is in the `.chatloop` folder, which it hides from git. The exact text of every round is in `.chatloop/rounds/`.
- It never writes outside the project folder, and it asks you before running any command the chat requests.
- `git diff` shows what really changed. Read it before you commit.
- Only paste code into a chat tool your workplace has approved for that code.
- Tested on macOS. Windows support is written but not yet tested.
