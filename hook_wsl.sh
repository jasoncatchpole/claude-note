#!/bin/bash
# UserPromptSubmit hook wrapper (WSL / Linux).
# Claude Code pipes the hook payload JSON on stdin.
exec python3 "$(dirname "$0")/note.py" hook
