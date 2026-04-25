#!/usr/bin/env python3
"""note.py — zero-context-pollution idea capture for Claude Code.

Subcommands: add, list, rm, view, hook.
Storage: SQLite at $NOTE_DB, else ~/.claude/notes.db.
Invoked by a UserPromptSubmit hook in `hook` mode (reads prompt JSON from stdin,
emits {"decision":"block","reason":...} or {} passthrough).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import traceback
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic TEXT NOT NULL,
  idea TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  project_dir TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_topic ON notes(topic);
"""

# Match either /note (raw user input) or __NOTE_HOOK__ (the marker emitted by
# the stub slash command body when Claude Code expands /note <args>).
TRIGGER_RE = re.compile(r"^(?:/note\b|__NOTE_HOOK__\b)\s*(.*)$", re.DOTALL)
SUBCOMMANDS = {"list", "rm", "view", "add"}


def db_path() -> Path:
    override = os.environ.get("NOTE_DB")
    if override:
        return Path(override)
    # Default: sibling of this script's parent dir. When the script lives at
    # <somewhere>/.claude/note/note.py, the DB is <somewhere>/.claude/notes.db.
    # This makes WSL and Windows share the same DB when both invoke the same
    # script under /mnt/c/... = C:\... on the home machine, and keeps the DB
    # next to the Claude config on the work machine.
    return Path(__file__).resolve().parent.parent / "notes.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=2.0)
    conn.execute("PRAGMA busy_timeout=2000")
    conn.executescript(SCHEMA)
    return conn


def add_note(topic: str, idea: str, project_dir: str | None = None) -> int:
    topic = (topic or "").strip()
    idea = (idea or "").strip()
    if not topic:
        raise ValueError("topic is required")
    if not idea:
        raise ValueError("idea is required")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes (topic, idea, project_dir) VALUES (?, ?, ?)",
            (topic, idea, project_dir),
        )
        conn.commit()
        return cur.lastrowid


def list_notes(topic: str | None = None, limit: int | None = 20):
    with connect() as conn:
        if topic:
            total = conn.execute(
                "SELECT COUNT(*) FROM notes WHERE LOWER(topic)=LOWER(?)",
                (topic,),
            ).fetchone()[0]
            q = "SELECT id, created_at, topic, idea FROM notes WHERE LOWER(topic)=LOWER(?) ORDER BY id DESC"
            params: tuple = (topic,)
        else:
            total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            q = "SELECT id, created_at, topic, idea FROM notes ORDER BY id DESC"
            params = ()
        if limit is not None:
            q += " LIMIT ?"
            params = params + (limit,)
        rows = conn.execute(q, params).fetchall()
    return rows, total


def view_note(note_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT id, topic, created_at, project_dir, idea FROM notes WHERE id=?",
            (note_id,),
        ).fetchone()
    return row


def rm_note(note_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        conn.commit()
        return cur.rowcount > 0


def format_list(rows, total: int, shown_limit: int | None) -> str:
    if not rows:
        return "no notes"
    id_w = max(len(str(r[0])) for r in rows)
    topic_w = min(20, max(len(r[2]) for r in rows))
    lines = []
    for id_, created_at, topic, idea in rows:
        snippet = idea.replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        topic_disp = topic if len(topic) <= topic_w else topic[: topic_w - 1] + "…"
        lines.append(f"{str(id_).rjust(id_w)}  {created_at}  {topic_disp.ljust(topic_w)}  {snippet}")
    shown = len(rows)
    if shown_limit is not None and total > shown:
        lines.append(f"{shown} of {total} notes (use --all to see rest)")
    else:
        lines.append(f"{shown} note{'s' if shown != 1 else ''}")
    return "\n".join(lines)


def format_view(row) -> str:
    if not row:
        return ""
    id_, topic, created_at, project_dir, idea = row
    return (
        f"id: {id_}\n"
        f"topic: {topic}\n"
        f"created_at: {created_at}\n"
        f"project_dir: {project_dir or '-'}\n"
        f"\n{idea}"
    )


def parse_note_command(text: str):
    """Parse a raw prompt into (action, kwargs) or return None if not a /note command.

    Returns:
        None if prompt doesn't start with /note.
        ('add', {'topic': str, 'idea': str})
        ('list', {'topic': str | None})
        ('rm', {'id': int})
        ('view', {'id': int})
        ('help', {})
    """
    if text is None:
        return None
    stripped = text.lstrip()
    m = TRIGGER_RE.match(stripped)
    if not m:
        return None
    rest = m.group(1).strip()
    if not rest:
        return ("help", {})

    tokens = rest.split(None, 2)
    first = tokens[0]

    # Explicit add escape hatch: /note add <topic> <idea...>
    if first == "add":
        if len(tokens) < 3:
            return ("error", {"msg": "usage: /note add <topic> <idea>"})
        topic = tokens[1]
        idea = tokens[2]
        return ("add", {"topic": topic, "idea": idea})

    # rm / view require exactly 2 tokens with numeric id; else fall through to add
    if first in ("rm", "view") and len(tokens) == 2 and tokens[1].isdigit():
        return (first, {"id": int(tokens[1])})

    # list: bare, or with single topic token
    if first == "list":
        if len(tokens) == 1:
            return ("list", {"topic": None})
        if len(tokens) == 2:
            return ("list", {"topic": tokens[1]})
        # 3+ tokens → user meant to add with topic="list"

    # Default: first token is topic, rest is idea
    if len(tokens) < 2:
        return ("error", {"msg": "usage: /note <topic> <idea>"})
    topic = first
    # Re-split to preserve original whitespace/newlines in idea
    idea = rest[len(first):].lstrip()
    return ("add", {"topic": topic, "idea": idea})


HELP_TEXT = (
    "usage:\n"
    "  /note <topic> <idea>      add a note\n"
    "  /note add <topic> <idea>  explicit add (escape hatch)\n"
    "  /note list [topic]        list notes (newest first, default 20)\n"
    "  /note view <id>           view full note\n"
    "  /note rm <id>             remove note"
)


def run_action(action: str, kwargs: dict, project_dir: str | None = None) -> str:
    if action == "add":
        note_id = add_note(kwargs["topic"], kwargs["idea"], project_dir=project_dir)
        return f"saved #{note_id} to {kwargs['topic']}"
    if action == "list":
        rows, total = list_notes(topic=kwargs.get("topic"), limit=20)
        return format_list(rows, total, shown_limit=20)
    if action == "view":
        row = view_note(kwargs["id"])
        if not row:
            raise LookupError(f"no note with id {kwargs['id']}")
        return format_view(row)
    if action == "rm":
        ok = rm_note(kwargs["id"])
        if not ok:
            raise LookupError(f"no note with id {kwargs['id']}")
        return f"removed #{kwargs['id']}"
    if action == "help":
        return HELP_TEXT
    if action == "error":
        raise ValueError(kwargs["msg"])
    raise ValueError(f"unknown action: {action}")


def hook_main(stdin_text: str) -> tuple[str, int]:
    """Hook mode entry point. Returns (stdout_json, exit_code).

    Exit code is always 0 — a broken hook must never eat the user's prompt.
    Non-note prompts produce '{}' (passthrough). /note prompts produce
    {"decision":"block","reason":"..."}.
    """
    try:
        try:
            payload = json.loads(stdin_text) if stdin_text.strip() else {}
        except json.JSONDecodeError:
            return ("{}", 0)
        prompt = payload.get("prompt", "")
        cwd = payload.get("cwd")
        parsed = parse_note_command(prompt)
        if parsed is None:
            return ("{}", 0)
        action, kwargs = parsed
        try:
            reason = run_action(action, kwargs, project_dir=cwd)
        except (ValueError, LookupError) as e:
            reason = str(e)
        return (json.dumps({"decision": "block", "reason": reason}), 0)
    except BaseException:
        _log_hook_error(traceback.format_exc())
        return ("{}", 0)


def _log_hook_error(msg: str) -> None:
    try:
        log_dir = Path(os.environ.get("NOTE_LOG_DIR") or os.path.expanduser("~/.claude/note"))
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "hook-errors.log").open("a", encoding="utf-8") as f:
            f.write(msg + "\n---\n")
    except Exception:
        pass


def _cmd_add(args) -> int:
    try:
        note_id = add_note(args.topic, " ".join(args.idea), project_dir=os.environ.get("CLAUDE_PROJECT_DIR"))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"saved #{note_id} to {args.topic}")
    return 0


def _cmd_list(args) -> int:
    limit = None if args.all else args.limit
    rows, total = list_notes(topic=args.topic, limit=limit)
    print(format_list(rows, total, shown_limit=limit))
    return 0


def _cmd_view(args) -> int:
    row = view_note(args.id)
    if not row:
        print(f"no note with id {args.id}", file=sys.stderr)
        return 1
    print(format_view(row))
    return 0


def _cmd_rm(args) -> int:
    if not rm_note(args.id):
        print(f"no note with id {args.id}", file=sys.stderr)
        return 1
    print(f"removed #{args.id}")
    return 0


def _cmd_hook(args) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    stdin_text = sys.stdin.read()
    out, code = hook_main(stdin_text)
    print(out)
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="note", description="Capture ideas into a local SQLite DB.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("add", help="add a note")
    sp.add_argument("topic")
    sp.add_argument("idea", nargs="+")
    sp.set_defaults(func=_cmd_add)

    sp = sub.add_parser("list", help="list notes (newest first)")
    sp.add_argument("topic", nargs="?", default=None)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("view", help="view a note")
    sp.add_argument("id", type=int)
    sp.set_defaults(func=_cmd_view)

    sp = sub.add_parser("rm", help="remove a note")
    sp.add_argument("id", type=int)
    sp.set_defaults(func=_cmd_rm)

    sp = sub.add_parser("hook", help="UserPromptSubmit hook entry (reads stdin JSON)")
    sp.set_defaults(func=_cmd_hook)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
