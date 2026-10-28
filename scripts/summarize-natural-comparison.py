"""Aggregate frozen natural-run summaries without discarding failed attempts."""

import argparse
import json
from pathlib import Path

from mclab.bootstrap import sha256
from mclab.natural_comparison import summarize
from mclab.paths import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize([read_json(path) for path in args.summaries])
    result["inputs"] = [{"path": str(p), "sha256": sha256(p)} for p in args.summaries]
    write_json(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
