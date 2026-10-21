#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ -f .venv/bin/python ] && .venv/bin/python -c "import PySide6" 2>/dev/null; then
    exec .venv/bin/python launcher.py "$@"
else
    exec python3 launcher.py "$@"
fi
