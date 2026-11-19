import json
import os
import time

import pytest
from run_fixtures import make_run

from mclab import prune

OLD = time.time() - 3 * 86400
BIG = b"x" * 1_100_000


def make(root, family, run, files):
    folder = root / "results" / family / run
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(BIG if name != "summary.json" else b"{}")
        os.utime(folder / name, (OLD, OLD))


def run_ids(n):
    return [f"2026110{i}T000000000000Z" for i in range(1, n + 1)]


def test_old_raw_logs_go_small_results_stay_and_the_newest_runs_are_left_alone(
    tmp_path,
):
    ids = run_ids(5)  # ...01 to ...05
    for run in ids:
        make(tmp_path, "natural", run, ["events.jsonl", "map.sqlite3", "summary.json"])
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ROADMAP.md").write_text(f"see {ids[0]}", encoding="utf-8")

    result = prune.sweep(tmp_path)

    natural = tmp_path / "results/natural"
    # newest three untouched
    for run in ids[2:]:
        assert (natural / run / "events.jsonl").exists()
        assert (natural / run / "map.sqlite3").exists()
    # cited: events gzipped, database kept
    assert (natural / ids[0] / "events.jsonl.gz").exists()
    assert not (natural / ids[0] / "events.jsonl").exists()
    assert (natural / ids[0] / "map.sqlite3").exists()
    # uncited old: events gzipped, database deleted; summary always stays
    assert (natural / ids[1] / "events.jsonl.gz").exists()
    assert not (natural / ids[1] / "map.sqlite3").exists()
    assert (natural / ids[1] / "summary.json").exists()
    assert result["gzip"] == 2 and result["delete"] == 1


def test_a_dry_run_changes_nothing(tmp_path):
    for run in run_ids(5):
        make(tmp_path, "navigation", run, ["events.jsonl"])

    result = prune.sweep(tmp_path, run=False)

    assert result["files"] == 2 and not result["applied"]
    assert len(list((tmp_path / "results").rglob("events.jsonl"))) == 5


# ---- build/gather runs: per build key -----------------------------------------


def stamp(day, hour=10):
    return f"202611{day:02}T{hour:02}0000000000Z"


def verdicts(root, **options):
    return {v.run.name: v.verdict for v in prune.plan_superseded(root, **options)}


def test_the_per_family_sweep_leaves_build_and_gather_runs_to_the_build_key_rule(
    tmp_path,
):
    for day in range(1, 6):
        make_run(tmp_path, stamp(day))

    assert prune.sweep(tmp_path)["files"] == 0
    assert len(list(tmp_path.rglob("events.jsonl"))) == 5


def test_older_failed_attempts_are_superseded_latest_and_passing_runs_stay_whole(
    tmp_path,
):
    make_run(tmp_path, stamp(1), result="fail")
    make_run(tmp_path, stamp(2), result="pass")
    make_run(tmp_path, stamp(3), result="crash")
    make_run(tmp_path, stamp(4), result="fail")
    make_run(tmp_path, stamp(5), result="fail", start=[0, 64, 0])  # another build

    assert verdicts(tmp_path) == {
        stamp(1): "superseded",
        stamp(2): "pass",
        stamp(3): "superseded",
        stamp(4): "latest",
        stamp(5): "latest",
    }
    assert verdicts(tmp_path, keep=2)[stamp(3)] == "latest"


def test_a_superseded_run_loses_logs_map_and_intermediate_checkpoints_only(tmp_path):
    old = make_run(tmp_path, stamp(1), result="fail")
    make_run(tmp_path, stamp(2), result="fail")
    before = sorted(p.name for p in old.rglob("*") if p.is_file())

    plan = prune.plan_superseded(tmp_path)
    summary = prune.summarize_superseded(plan)
    assert summary["need_digest"] == 1 and summary["files"] == 5
    assert sorted(p.name for p in old.rglob("*") if p.is_file()) == before  # dry run

    freed, kept = prune.apply_superseded(plan)

    left = sorted(p.relative_to(old).as_posix() for p in old.rglob("*") if p.is_file())
    assert kept == [] and freed > 0
    assert left == [
        "build.json",
        "checkpoints/final.json",
        "digest.json",
        "launch_status.json",
        "progress.json",
        "provenance.json",
        "run_config.json",
        "summary.json",
        "walkthrough.md",
    ]
    digest = json.loads((old / "digest.json").read_text())
    assert digest["deep"] and digest["stalls"]["standstills"]["count"] == 1
    assert (tmp_path / "results/companion" / stamp(2) / "events.jsonl").exists()


def test_without_final_json_the_highest_numbered_checkpoint_stays(tmp_path):
    run = make_run(tmp_path, stamp(1), result="killed", checkpoints=12)
    assert prune.final_checkpoint(run).name == "0012.json"
    assert run / "checkpoints/0012.json" not in prune.heavy_files(run)


def test_runs_that_may_still_be_going_are_never_touched_or_counted_as_latest(
    tmp_path,
):
    make_run(tmp_path, stamp(1), result="fail")
    make_run(tmp_path, stamp(2), result="fail")
    # newer attempts that may be live: no status and fresh; no status and quiet
    # for less than a day; ended but its store's -wal written a minute ago
    make_run(tmp_path, stamp(3), result="running", age_s=None)
    make_run(tmp_path, stamp(4), result="killed", age_s=6 * 3600)
    ended = make_run(tmp_path, stamp(5), result="fail")
    (ended / "events.sqlite3-wal").write_bytes(b"")

    found = verdicts(tmp_path)

    assert found[stamp(3)] == found[stamp(4)] == found[stamp(5)] == "active"
    assert found[stamp(2)] == "latest" and found[stamp(1)] == "superseded"


def test_a_killed_run_quiet_for_a_day_is_an_ordinary_superseded_attempt(tmp_path):
    make_run(tmp_path, stamp(1), result="killed", age_s=2 * 86400)
    make_run(tmp_path, stamp(2), result="fail")

    assert verdicts(tmp_path)[stamp(1)] == "superseded"


def test_runs_without_a_build_key_or_cited_in_the_docs_are_left_alone(tmp_path):
    keyless = make_run(tmp_path, stamp(1), result="fail")
    (keyless / "run_config.json").unlink()
    (keyless / "build.json").unlink()  # no argv either
    make_run(tmp_path, stamp(2), result="fail")
    make_run(tmp_path, stamp(3), result="fail")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "IMPLEMENTED.md").write_text(
        f"see {stamp(2)}", encoding="utf-8"
    )
    make_run(tmp_path, stamp(4), result="fail")

    found = verdicts(tmp_path)

    assert found[stamp(1)] == "unknown" and found[stamp(2)] == "cited"
    assert found[stamp(3)] == "superseded" and found[stamp(4)] == "latest"


def test_a_run_whose_digest_cannot_be_made_keeps_its_files(tmp_path, monkeypatch):
    old = make_run(tmp_path, stamp(1), result="fail")
    make_run(tmp_path, stamp(2), result="fail")
    plan = prune.plan_superseded(tmp_path)

    def broken(run):
        raise OSError("disk full")

    monkeypatch.setattr(prune.run_digest, "ensure", broken)
    freed, kept = prune.apply_superseded(plan)

    assert freed == 0 and kept == [(stamp(1), "digest failed: OSError('disk full')")]
    assert (old / "events.jsonl").exists() and (old / "checkpoints/0001.json").exists()


def test_keep_must_be_at_least_one(tmp_path):
    with pytest.raises(ValueError):
        prune.plan_superseded(tmp_path, keep=0)
