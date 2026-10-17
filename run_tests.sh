#!/usr/bin/env bash
set -e

echo "=== Running NaSCon '26 Automated Test Suite ==="

# Execute pytest with coverage and html report
pytest tests/ -v --cov=backend/app --cov-report=term-missing --cov-report=json:coverage_report.json --html=report.html --self-contained-html

echo "=== All Tests Passed Successfully ==="
