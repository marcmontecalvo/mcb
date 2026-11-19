"""One-off: delete launch records that pytest wrote into results/launches before
tests/conftest.py isolated them. A record is a test's when its invocation is pytest
arguments or the test fixture's `--start 1 70 3`. Dry run unless --apply."""

import json
import shutil
import sys
from pathlib import Path

PYTEST = {"-q", "-x", "-p", "--deselect", "--tb=short", "-rf", "--no-header", "-k"}
FIXTURE = ["--start", "1", "70", "3"]


def is_test_record(folder: Path) -> bool:
    try:
        invocation = json.loads((folder / "provenance.json").read_text("utf-8")).get(
            "invocation"
        )
    except (OSError, ValueError):
        return False
    if not isinstance(invocation, list):
        return False
    return invocation == FIXTURE or any(
        a in PYTEST or str(a).startswith("tests/") for a in invocation
    )


def main(root: Path, apply: bool) -> None:
    launches = sorted((root / "results/launches").iterdir())
    found = [d for d in launches if is_test_record(d)]
    print(f"{len(found)} test-written launch records")
    if apply:
        for folder in found:
            shutil.rmtree(folder)
        print("deleted")


if __name__ == "__main__":
    main(Path(sys.argv[1]), "--apply" in sys.argv)
