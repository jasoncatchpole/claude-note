# /note — zero-context-pollution idea capture for Claude Code

Jot down random ideas during a Claude Code conversation without adding anything to the current session's context. Ideas persist in a local SQLite DB you can list/view/remove later.

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

Also works as a standalone CLI:
```
python3 note.py add audiobook "Test Gemini image model"
python3 note.py list
python3 note.py view 1
python3 note.py rm 1
```

## Storage

- Default path: sibling of this script's parent dir, i.e. `<note.py's parent>/../notes.db`.
- Override: set `NOTE_DB` env var to any path.
- **Home machine (WSL+Windows sharing):** install lives at `C:\Users\<you>\.claude\note\note.py` = `/mnt/c/Users/<you>/.claude/note/note.py`. Both platforms invoke the same script, both compute `…/notes.db` next to it, both end up at the same NTFS file. No env var needed.
- **Work machine (native Ubuntu):** install at `~/.claude/note/note.py`; DB at `~/.claude/notes.db`. Independent of home.

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

44 tests covering CRUD, parsing, hook-mode I/O, end-to-end subprocess calls, and path resolution.
