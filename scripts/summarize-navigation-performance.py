"""Summarize a completed hill cohort, retaining its source and result provenance."""

import argparse
import json
from pathlib import Path

from mclab import event_log
from mclab.bootstrap import sha256
from mclab.navigation_metrics import summarize
from mclab.paths import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    events_path = args.summary.with_name("events.sqlite3")
    result = summarize(read_json(args.summary), event_log.records(events_path))
    result.update(
        summary_sha256=sha256(args.summary),
        events_sha256=sha256(events_path),
        summarizer_sha256=sha256(Path(__file__)),
        metrics_source_sha256=sha256(
            Path(__file__).resolve().parents[1] / "src/mclab/navigation_metrics.py"
        ),
    )
    destination = args.summary.with_name("performance.json")
    write_json(destination, result)
    print(json.dumps({"artifact": str(destination), **result}, indent=2))


if __name__ == "__main__":
    main()
