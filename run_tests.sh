#!/usr/bin/env bash
set -e

echo "=== Running NaSCon '26 Automated Test Suite ==="

export PYTHONPATH=backend
if [ -d "$HOME/.venv/nascon" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.venv/nascon/bin/activate"
fi

if command -v pytest >/dev/null 2>&1; then
    pytest tests/ -v --html=report.html --self-contained-html
elif [ -x "$HOME/.venv/nascon/bin/pytest" ]; then
    "$HOME/.venv/nascon/bin/pytest" tests/ -v --html=report.html --self-contained-html
else
    python3 -m pytest tests/ -v --html=report.html --self-contained-html
fi

echo "=== All Tests Passed Successfully ==="
