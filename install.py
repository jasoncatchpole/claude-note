#!/usr/bin/env python3
"""install.py — install the /note hook for Claude Code.

Detects platform, computes paths, merges hook into ~/.claude/settings.json (or
the Windows equivalent) with a backup, seeds the DB schema, and prints
verification commands. Idempotent. --uninstall reverses the changes.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUB_BODY = (
    "---\n"
    "description: Jot a note without polluting Claude's context (handled by UserPromptSubmit hook).\n"
    "argument-hint: <topic> <idea> | list [topic] | view <id> | rm <id>\n"
    "---\n"
    "__NOTE_HOOK__ $ARGUMENTS\n"
)


def detect_platform() -> str:
    s = platform.system()
    if s == "Windows":
        return "windows"
    if s == "Linux":
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    return "wsl"
        except OSError:
            pass
        return "linux"
    return s.lower()


def claude_home() -> Path:
    # On all platforms, Claude Code's per-user dir is <HOME>/.claude.
    return Path(os.path.expanduser("~/.claude"))


def hook_command(plat: str) -> list[str]:
    if plat == "windows":
        return [str(HERE / "hook_win.cmd")]
    return [str(HERE / "hook_wsl.sh")]


def settings_hook_obj(plat: str) -> dict:
    """The inner hook object (type/command/timeout)."""
    cmd = hook_command(plat)[0]
    return {"type": "command", "command": cmd, "timeout": 5}


def settings_matcher_group(plat: str) -> dict:
    """The matcher-group wrapper required by the hooks schema:
    {"matcher": "", "hooks": [{...}]}
    UserPromptSubmit has no per-tool matcher, so matcher is empty string = match all.
    """
    return {"matcher": "", "hooks": [settings_hook_obj(plat)]}


def _hook_command_in_group(group: dict) -> str | None:
    """Extract the command string from a matcher-group or legacy flat hook object."""
    if "hooks" in group:
        inner = group["hooks"]
        if inner and isinstance(inner[0], dict):
            return inner[0].get("command")
    return group.get("command")


def merge_hook(settings: dict, plat: str) -> bool:
    """Merge a UserPromptSubmit matcher group. Returns True if settings changed."""
    cmd = settings_hook_obj(plat)["command"]
    hooks = settings.setdefault("hooks", {})
    ups = hooks.setdefault("UserPromptSubmit", [])

    # Remove stale legacy flat entries (old format without "hooks" array)
    changed = False
    new_ups = []
    for group in ups:
        if not isinstance(group, dict):
            continue
        c = _hook_command_in_group(group)
        if c == cmd and "hooks" not in group:
            # Legacy flat entry — drop it, we'll add the correct one below
            changed = True
            continue
        new_ups.append(group)

    # Check if a valid matcher-group for our command already exists
    for group in new_ups:
        if _hook_command_in_group(group) == cmd and "hooks" in group:
            if not changed:
                return False
            hooks["UserPromptSubmit"] = new_ups
            return True

    new_ups.append(settings_matcher_group(plat))
    hooks["UserPromptSubmit"] = new_ups
    return True


def remove_hook(settings: dict, entry_command: str) -> bool:
    hooks = settings.get("hooks") or {}
    ups = hooks.get("UserPromptSubmit") or []
    before = len(ups)
    hooks["UserPromptSubmit"] = [
        g for g in ups
        if not (isinstance(g, dict) and _hook_command_in_group(g) == entry_command)
    ]
    if not hooks["UserPromptSubmit"]:
        hooks.pop("UserPromptSubmit", None)
    if not hooks:
        settings.pop("hooks", None)
    return len(ups) != before


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: {path} is not valid JSON: {e}")


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


def ensure_stub_command(plat: str) -> Path:
    cmd_path = claude_home() / "commands" / "note.md"
    cmd_path.parent.mkdir(parents=True, exist_ok=True)
    needs_write = True
    if cmd_path.exists():
        existing = cmd_path.read_text(encoding="utf-8")
        # Keep our latest body verbatim. If user has hand-edited, preserve.
        if "__NOTE_HOOK__" in existing:
            needs_write = False
    if needs_write:
        cmd_path.write_text(STUB_BODY, encoding="utf-8")
    return cmd_path


def remove_stub_command() -> Path:
    cmd_path = claude_home() / "commands" / "note.md"
    if cmd_path.exists():
        cmd_path.unlink()
    return cmd_path


def seed_db() -> Path:
    sys.path.insert(0, str(HERE))
    import note  # noqa: E402
    p = note.db_path()
    with note.connect(p):
        pass
    return p


def mark_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except OSError:
        pass


def install(run_tests: bool) -> None:
    plat = detect_platform()
    print(f"platform: {plat}")

    if plat in ("wsl", "linux"):
        mark_executable(HERE / "hook_wsl.sh")

    settings_path = claude_home() / "settings.json"
    settings = load_json(settings_path)
    bak = backup(settings_path)
    if bak:
        print(f"backup: {bak}")

    changed = merge_hook(settings, plat)
    if changed:
        save_json(settings_path, settings)
        print(f"settings: updated {settings_path}")
    else:
        print(f"settings: already installed in {settings_path}")

    stub = ensure_stub_command(plat)
    print(f"stub command: {stub}")

    db = seed_db()
    print(f"db: {db}")

    if run_tests:
        print("running tests...")
        r = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "test_note"],
            cwd=str(HERE),
        )
        if r.returncode != 0:
            raise SystemExit("tests failed")

    _print_verify(plat)


def uninstall() -> None:
    plat = detect_platform()
    settings_path = claude_home() / "settings.json"
    if settings_path.exists():
        settings = load_json(settings_path)
        cmd = hook_command(plat)[0]
        backup(settings_path)
        if remove_hook(settings, cmd):
            save_json(settings_path, settings)
            print(f"settings: removed hook from {settings_path}")
        else:
            print("settings: no hook entry to remove")
    remove_stub_command()
    print("stub command removed")
    print("note: DB left in place at ~/.claude/notes.db (delete manually if desired)")


def _print_verify(plat: str) -> None:
    print()
    print("verify:")
    print("  python3 -m unittest -v test_note   # from this directory")
    if plat == "windows":
        print('  py -3 note.py add test "hello from installer"')
        print("  py -3 note.py list")
    else:
        print('  python3 note.py add test "hello from installer"')
        print("  python3 note.py list")
    print()
    print("then, in a Claude Code session:")
    print("  /note audiobook Test the new Gemini image model")
    print("  /note list")
    print()
    print("Claude should never see these /note messages — the reason line is")
    print("shown only to you. Run `/note list` to confirm it was saved.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--run-tests", action="store_true", help="run the unittest suite after install")
    args = ap.parse_args()
    if args.uninstall:
        uninstall()
    else:
        install(run_tests=args.run_tests)
    return 0


if __name__ == "__main__":
    sys.exit(main())
