# claude-note — zero-context-pollution idea capture for Claude Code

Jot down random ideas during a Claude Code conversation using "/note" without adding anything to the current session's context. Ideas persist in a local SQLite DB you can list/view/remove later.

e.g. In Claude Code:

```
/note meals Use less salt

UserPromptSubmit operation blocked by hook:
  saved #9 to meals

  Original prompt: /note meals Use less salt
```

```
/note list
UserPromptSubmit operation blocked by hook:
  9  2026-04-25 03:21:06  meals           Use less salt
  8  2026-04-25 03:20:21  meals           Try that new recipe that Pete gave me
  7  2026-04-25 03:19:47  ShowerThoughts  Look into a new approach to world peace
  6  2026-04-25 03:18:57  MyProject       Improve test coverage across the new code
  4 notes
```

NOTE: When you run the "/note" command it always prints out "UserPromptSubmit operation blocked by hook:" which is annoying but can't be avoided (that I know of) so you gotta live with that.

PRs welcome!

## How it works

A `UserPromptSubmit` hook intercepts prompts that start with `/note`. It saves the note to SQLite and emits `{"decision":"block","reason":"saved #N to <topic>"}`. Claude Code:
- erases the prompt from context (Claude never sees it)
- shows the `reason` to you only (confirmation line in the UI)

Same mechanism `/btw` uses internally for its "don't pollute context" behavior, adapted for persistent note-taking.

## Install

```
python3 install.py              # or: py -3 install.py on Windows
python3 install.py --run-tests  # also run the unittest suite
python3 install.py --uninstall  # reverse
```

Installer is idempotent. It:
1. Merges a hook entry into `~/.claude/settings.json` (backs up to `settings.json.bak`)
2. Creates a stub `~/.claude/commands/note.md` so `/note` autocompletes
3. Seeds the SQLite DB schema

## Usage

```
/note <topic> <idea>       add a note
/note add <topic> <idea>   explicit add (bypasses subcommand detection)
/note list                 list all, newest first (default limit 20)
/note list <topic>         list filtered by topic (case-insensitive)
/note view <id>            show full note
/note rm <id>              delete
/note                      show usage help
```

e.g. `/note myproject Add a clever feature to do XYZ`

Also works as a standalone CLI:
```
python3 note.py add myproject "Add a clever feature to do XYZ"
python3 note.py list
python3 note.py view 1
python3 note.py rm 1
```

## Storage

- Default path: sibling of this script's parent dir, i.e. `<note.py's parent>/../notes.db`.
- Override: set `NOTE_DB` env var to any path.
- **Linux:** install at `~/.claude/note/note.py`; DB at `~/.claude/notes.db`.
- **Windows:** install at `C:\Users\<you>\.claude\note\note.py`; DB at `C:\Users\<you>\.claude\notes.db`.
- **WSL+Windows sharing:** install lives at `C:\Users\<you>\.claude\note\note.py` = `/mnt/c/Users/<you>/.claude/note/note.py`. Both platforms invoke the same script, both compute `…/notes.db` and end up at the same NTFS file. No env var needed.

Schema: `id, topic, idea, created_at, project_dir` (project_dir captured from the session's `cwd` for context).

## Reserved-topic collision handling

- `/note list` → list subcommand
- `/note list audiobook` → list filtered by topic `audiobook`
- `/note list buy milk tomorrow` (3+ tokens) → **add** with topic=`list`
- `/note rm 42` (numeric) → rm subcommand; `/note rm foo bar` → add with topic=`rm`
- `/note add <topic> <idea>` → explicit-add escape hatch

## Failure policy

Any exception in hook mode → emit `{}` (passthrough, prompt goes to Claude normally) + append trace to `hook-errors.log`. A broken note subsystem must never eat a real prompt.

## Threat model

The DB is plaintext on disk. Don't jot secrets. On the home machine the DB lives on NTFS, shared between WSL and Windows — rollback journal only (no WAL), busy_timeout=2000ms, single-writer-at-a-time assumption.

## Tests

```
python3 -m unittest -v test_note
```

48 tests covering CRUD, parsing, hook-mode I/O, end-to-end subprocess calls, and path resolution.
