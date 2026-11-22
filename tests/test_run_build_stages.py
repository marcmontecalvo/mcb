"""Stage labs in scripts/run-build.py: --from a library stage, --until a milestone."""

import hashlib
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from mclab import build_checkpoint as ck

ROOT = Path(__file__).parents[1]
HOUSE = ROOT / "tests/fixtures/schematics/house-large.build.json"
START, SEED = [-24, 64, 56], 31415926
KEY = "build:house-large@-24,64,56#31415926"


@pytest.fixture(scope="module")
def program():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        return runpy.run_path(str(ROOT / "scripts/run-build.py"))
    finally:
        sys.path.remove(str(ROOT / "scripts"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_run_from_a_stage_finds_it_by_the_whole_builds_key(program, tmp_path):
    saved = {"version": 2, "stage": "site_ready", "build_sha256": _sha(HOUSE)}
    ck.save(ck.stage_path(KEY, "site_ready", tmp_path), saved)

    path, data = program["stage_start"](HOUSE, START, SEED, "site_ready", tmp_path)

    assert path == ck.stage_path(KEY, "site_ready", tmp_path)
    assert data["stage"] == "site_ready"


def test_a_stage_of_an_edited_build_file_is_refused(program, tmp_path):
    saved = {"version": 2, "stage": "site_ready", "build_sha256": "0" * 64}
    ck.save(ck.stage_path(KEY, "site_ready", tmp_path), saved)

    with pytest.raises(ValueError, match="another version"):
        program["stage_start"](HOUSE, START, SEED, "site_ready", tmp_path)


def _report(stop):
    return SimpleNamespace(stop=stop, steps=3, counts={}, unknown=[])


CLEAN = SimpleNamespace(breaks=[], places=[], unknown=[], uses=[])
LEFT = SimpleNamespace(breaks=[], places=[(0, 0, 0)] * 40, unknown=[], uses=[])


def test_a_whole_build_passes_when_done_with_an_empty_diff(program):
    rows = program["build_rows"](_report("done"), CLEAN, ticks=9)
    assert [r["case"] for r in rows] == ["build stop", "no unknown cells", "empty diff"]
    assert all(r["pass"] for r in rows)


def test_a_stage_lab_passes_at_its_milestone_with_the_stage_saved(program):
    # The rest of the house is not built on purpose: no empty-diff row.
    rows = program["build_rows"](
        _report("until_site"), LEFT, ticks=9, until="site", saved=True
    )
    cases = {r["case"]: r["pass"] for r in rows}
    assert cases == {"build stop": True, "no unknown cells": True, "stage saved": True}


@pytest.mark.parametrize(
    ("stop", "saved"), [("until_site", False), ("stalled", True), ("done", True)]
)
def test_a_stage_lab_fails_short_of_its_milestone_or_without_its_stage(
    program, stop, saved
):
    rows = program["build_rows"](
        _report(stop), LEFT, ticks=9, until="site", saved=saved
    )
    assert not all(r["pass"] for r in rows)
