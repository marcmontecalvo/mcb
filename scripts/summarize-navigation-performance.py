"""Summarize a completed hill cohort, retaining its source and result provenance."""

import argparse
import json
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.navigation_metrics import summarize
from mclab.paths import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    events_path = args.summary.with_name("events.jsonl")
    with events_path.open(encoding="utf-8") as stream:
        result = summarize(
            read_json(args.summary), (json.loads(line) for line in stream)
        )
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
