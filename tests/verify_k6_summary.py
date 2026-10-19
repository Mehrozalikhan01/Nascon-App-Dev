"""Fail when a saved k6 summary contradicts the Day 2 load thresholds."""

import json
import sys
from pathlib import Path
from typing import Any


def metric_value(metric: dict[str, Any], name: str) -> float:
    candidates = (metric, metric.get("values", {}))
    for candidate in candidates:
        value = candidate.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    raise ValueError(f"Missing numeric metric value: {name}")


def threshold_passed(metric: dict[str, Any], name: str) -> bool:
    state = metric.get("thresholds", {}).get(name)
    if isinstance(state, dict):
        state = state.get("ok")
    elif isinstance(state, bool):
        # k6 0.50 exports a boolean that is true when the threshold was
        # breached. Newer k6 versions export {"ok": bool} instead.
        state = not state
    if not isinstance(state, bool):
        raise ValueError(f"Missing boolean threshold result: {name}")
    return state


def main(path: Path) -> int:
    try:
        summary = json.loads(path.read_text())
        metrics = summary["metrics"]
        duration = metrics["http_req_duration"]
        failures = metrics["http_req_failed"]
        p95_ms = metric_value(duration, "p(95)")
        failure_rate = metric_value(failures, "value") if "value" in failures else metric_value(failures, "rate")
        duration_ok = p95_ms < 800
        failure_ok = failure_rate == 0
        recorded_duration_ok = threshold_passed(duration, "p(95)<800")
        recorded_failure_ok = threshold_passed(failures, "rate==0.00")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"Invalid k6 summary {path}: {error}", file=sys.stderr)
        return 2

    print(f"k6 p95: {p95_ms:.2f} ms (required < 800 ms)")
    print(f"k6 HTTP failure rate: {failure_rate:.6%} (required 0%)")
    errors = []
    if not duration_ok:
        errors.append("p95 latency exceeds 800 ms")
    if not failure_ok:
        errors.append("HTTP failure rate is not zero")
    if recorded_duration_ok != duration_ok:
        errors.append("saved p95 threshold flag contradicts the measured p95")
    if recorded_failure_ok != failure_ok:
        errors.append("saved HTTP failure threshold flag contradicts the measured rate")
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("All k6 threshold values and saved threshold flags agree.")
    return 0


if __name__ == "__main__":
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/reports/k6-summary.json")
    raise SystemExit(main(report_path))
