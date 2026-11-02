import os
import time

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


def test_companion_logs_are_deleted_not_compressed_and_fresh_files_survive(tmp_path):
    ids = run_ids(5)
    for run in ids:
        make(tmp_path, "companion", run, ["events.jsonl", "build.json"])
    fresh = tmp_path / "results/companion" / ids[0] / "events.jsonl"
    os.utime(fresh)  # touched just now: a run may still be writing it

    prune.sweep(tmp_path)

    companion = tmp_path / "results/companion"
    assert fresh.exists()
    assert not (companion / ids[1] / "events.jsonl").exists()
    assert not (companion / ids[1] / "events.jsonl.gz").exists()
    assert (companion / ids[1] / "build.json").exists()


def test_a_dry_run_changes_nothing(tmp_path):
    for run in run_ids(5):
        make(tmp_path, "navigation", run, ["events.jsonl"])

    result = prune.sweep(tmp_path, run=False)

    assert result["files"] == 2 and not result["applied"]
    assert len(list((tmp_path / "results").rglob("events.jsonl"))) == 5
