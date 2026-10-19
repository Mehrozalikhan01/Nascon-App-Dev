"""Update COVERAGE_DAY2.json from a real Coverage.py JSON report."""

import json
import sys
from pathlib import Path


MODULES = {
    "day2_routes": "day2.py",
    "streaming": "streaming.py",
    "costream": "costream.py",
    "qr_guest": "qr_guest.py",
    "moderation_dlq": "moderation_dlq.py",
    "observability": "observability.py",
}
MINIMUM = 85.0


def main(report_path: Path, output_path: Path) -> int:
    report = json.loads(report_path.read_text())
    files = report.get("files", {})
    measurements = {}
    for name, filename in MODULES.items():
        suffix = f"/services/{filename}"
        match = next((item for path, item in files.items() if path.endswith(suffix)), None)
        if match is None:
            print(f"Missing coverage data for {filename}", file=sys.stderr)
            return 2
        measurements[name] = round(float(match["summary"]["percent_covered"]), 2)

    failing = {name: value for name, value in measurements.items() if value < MINIMUM}
    if failing:
        for name, value in failing.items():
            print(f"FAIL: {name} coverage is {value:.2f}% (required >= {MINIMUM:.1f}%)", file=sys.stderr)
        return 1

    output_path.write_text(json.dumps(measurements, indent=2) + "\n")
    for name, value in measurements.items():
        print(f"{name}: {value:.2f}%")
    print(f"Updated {output_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: verify_day2_coverage.py COVERAGE_JSON COVERAGE_DAY2.json", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
