import json
import os
import time
from datetime import UTC, datetime

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
    # cited: events gzipped and kept, the derived map deleted
    assert (natural / ids[0] / "events.jsonl.gz").exists()
    assert not (natural / ids[0] / "events.jsonl").exists()
    assert not (natural / ids[0] / "map.sqlite3").exists()
    # uncited old: raw logs and databases deleted; summary always stays
    assert not (natural / ids[1] / "events.jsonl").exists()
    assert not (natural / ids[1] / "events.jsonl.gz").exists()
    assert not (natural / ids[1] / "map.sqlite3").exists()
    assert (natural / ids[1] / "summary.json").exists()
    assert result["gzip"] == 1 and result["delete"] == 3


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


# ---- aggressive retention: nested logs, keyless runs, ledgers, auto -----------


def test_nested_and_compressed_logs_of_old_uncited_runs_are_deleted(tmp_path):
    ids = run_ids(5)
    for run in ids:
        make(tmp_path, "natural", run, ["summary.json"])
        route = tmp_path / "results/natural" / run / "R1-calm-v8"
        route.mkdir()
        for name in ("events.jsonl", "events.jsonl.gz", "events.sqlite3"):
            (route / name).write_bytes(BIG)
            os.utime(route / name, (OLD, OLD))

    prune.sweep(tmp_path)

    oldest = tmp_path / "results/natural" / ids[0]
    assert list(oldest.rglob("events.*")) == []
    assert (oldest / "summary.json").exists()
    assert len(list((tmp_path / "results/natural" / ids[4]).rglob("events.*"))) == 3


def test_a_cited_run_keeps_its_logs_and_ledger(tmp_path):
    ids = run_ids(5)
    for run in ids:
        make(tmp_path, "navigation", run, ["events.jsonl.gz", "episodes.sqlite3"])
    (tmp_path / "README.md").write_text(f"see {ids[0]}", encoding="utf-8")

    prune.sweep(tmp_path)

    cited = tmp_path / "results/navigation" / ids[0]
    assert (cited / "events.jsonl.gz").exists() and (
        cited / "episodes.sqlite3"
    ).exists()
    assert not (tmp_path / "results/navigation" / ids[1] / "episodes.sqlite3").exists()


def keyless(root, day, **options):
    run = make_run(root, stamp(day), result="fail", **options)
    (run / "run_config.json").unlink()
    (run / "build.json").unlink()
    if options.get("age_s", 3 * 86400) is not None:
        prune_age = time.time() - options.get("age_s", 3 * 86400)
        for path in [run, *run.rglob("*")]:
            os.utime(path, (prune_age, prune_age))
    return run


def test_old_keyless_runs_lose_their_heavy_files_without_a_digest(tmp_path):
    old = keyless(tmp_path, 1)
    plan = prune.plan_superseded(tmp_path)
    [verdict] = plan
    assert verdict.verdict == "unknown" and verdict.actions

    freed, kept = prune.apply_superseded(plan)

    assert freed > 0 and kept == []
    assert not (old / "events.jsonl").exists()
    assert (old / "summary.json").exists() and not (old / "digest.json").exists()


def test_cited_keyless_runs_are_kept(tmp_path):
    keyless(tmp_path, 1)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/NOTES.md").write_text(stamp(1), encoding="utf-8")

    [verdict] = prune.plan_superseded(tmp_path)

    assert verdict.verdict == "cited" and not verdict.actions


def big_payload():
    return json.dumps({"type": "policy_sample", "data": "y" * 4000})


def ledger(path, runs):
    from mclab.recorder import EpisodeRecorder

    db = EpisodeRecorder(path)
    for run_id, started in runs:
        db.begin_run(run_id, {})
        db.db.execute("UPDATE runs SET started_at=? WHERE run_id=?", (started, run_id))
        db.begin(run_id, 1, f"{run_id}-e", "body", "x")
        for seq in range(1, 51):  # raw: as written before the ledger elided payloads
            db.db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, seq, f"{run_id}-e", "server_event", seq, big_payload()),
            )
        db.finish_run(run_id, "finished")
    db.close()


def big_rows(path):
    import sqlite3

    with sqlite3.connect(path) as db:
        rows = dict(
            db.execute(
                "SELECT run_id, sum(length(payload) > ?) FROM events GROUP BY run_id",
                (prune.recorder.LEDGER_PAYLOAD_MAX,),
            )
        )
        total = db.execute("SELECT count(*) FROM events").fetchone()[0]
        episodes = db.execute("SELECT count(*) FROM episodes").fetchone()[0]
    return rows, total, episodes


def test_old_ledger_payloads_are_elided_rows_and_episodes_stay(tmp_path):
    path = tmp_path / "results/episodes.sqlite3"
    ledger(
        path,
        [
            ("old-run", "2026-10-01T00:00:00+00:00"),
            ("new-run", "2026-11-18T00:00:00+00:00"),
        ],
    )
    nav = tmp_path / "results/navigation/r1/episodes.sqlite3"
    ledger(nav, [("nav-old", "2026-10-02T00:00:00+00:00")])
    now = datetime(2026, 11, 19, tzinfo=UTC).timestamp()
    before = path.stat().st_size

    dry = prune.trim_ledgers(tmp_path, days=7, now=now, run=False)
    assert dry["rows"] == 100 and big_rows(path)[0]["old-run"] == 50

    result = prune.trim_ledgers(tmp_path, days=7, now=now)

    # every row stays (navigationproof counts them); old payloads are stubs
    assert big_rows(path) == ({"old-run": 0, "new-run": 50}, 100, 2)
    assert big_rows(nav) == ({"nav-old": 0}, 50, 1)
    assert result["rows"] == 100 and result["databases"] == 2
    assert path.stat().st_size < before
    assert prune.trim_ledgers(tmp_path, days=7, now=now)["rows"] == 0


def test_a_locked_ledger_is_skipped_not_fatal(tmp_path, monkeypatch):
    path = tmp_path / "results/episodes.sqlite3"
    ledger(path, [("old-run", "2026-10-01T00:00:00+00:00")])

    def locked(*args, **kwargs):
        raise prune.sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(prune, "_trim_one", locked)
    result = prune.trim_ledgers(tmp_path, days=7)

    assert result["rows"] == 0 and result["skipped"] == [str(path)]


def test_auto_runs_every_rule_and_caps_the_digest_work(tmp_path):
    for day in range(1, 5):
        make_run(tmp_path, stamp(day), result="fail")
    keyless(tmp_path, 9)
    ledger(tmp_path / "results/episodes.sqlite3", [("r", "2026-01-01T00:00:00+00:00")])

    result = prune.auto(tmp_path, max_runs=2)

    gone = [
        d.name
        for d in sorted((tmp_path / "results/companion").iterdir())
        if not (d / "events.jsonl").exists()
    ]
    # two oldest superseded runs (capped) plus the keyless one
    assert gone == [stamp(1), stamp(2), stamp(9)]
    assert result["superseded_runs"] == 2 and result["ledger"]["rows"] == 50
    assert result["deferred"] == 1


def test_auto_is_off_under_the_opt_out(tmp_path, monkeypatch):
    make_run(tmp_path, stamp(1), result="fail")
    make_run(tmp_path, stamp(2), result="fail")
    monkeypatch.setenv("MCLAB_NO_PRUNE", "1")

    assert prune.auto(tmp_path) == {"skipped": "MCLAB_NO_PRUNE"}
    assert (tmp_path / "results/companion" / stamp(1) / "events.jsonl").exists()


def test_main_dry_run_and_apply(tmp_path, capsys):
    make_run(tmp_path, stamp(1), result="fail")
    make_run(tmp_path, stamp(2), result="fail")
    ledger(tmp_path / "results/episodes.sqlite3", [("r", "2026-01-01T00:00:00+00:00")])

    assert prune.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "ledgers" in out
    assert (tmp_path / "results/companion" / stamp(1) / "events.jsonl").exists()

    assert prune.main(["--root", str(tmp_path), "--apply", "--ledger-days", "3"]) == 0
    assert "freed" in capsys.readouterr().out
    assert not (tmp_path / "results/companion" / stamp(1) / "events.jsonl").exists()
