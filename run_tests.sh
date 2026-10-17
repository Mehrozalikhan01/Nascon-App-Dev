#!/usr/bin/env bash
set -e

echo "=== Running NaSCon '26 Automated Test Suite ==="

export PYTHONPATH=backend
pytest tests/ -v --html=report.html --self-contained-html

echo "=== All Tests Passed Successfully ==="
