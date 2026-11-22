import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from run_fixtures import HOUSE, SEED, START, age, make_run

from mclab import event_log, run_digest, run_history

STAMP = "20261119T133618892664Z"
KEY = "build:house-large@-24,64,56#31415926"


def test_a_failed_build_digest_has_phases_stalls_progress_reason_and_commit(tmp_path):
    run = make_run(tmp_path, STAMP, result="fail")

    d = run_digest.build(run)

    assert d["build_key"] == KEY and d["job"] == "build"
    assert d["outcome"] == "fail" and d["status"] == "failed"
    # phases inferred from the log (build_stats.scan): craft at 7.5 s, first house
    # block at 18 s, last work at 19 s
    assert d["phase_source"] == "events"
    assert d["phases_s"] == {"gather": 7.5, "trees": 0.0, "site": 10.5, "building": 1.0}
    stalls = d["stalls"]
    assert stalls["watchdog"] == {"count": 2, "total_s": 14.5, "max_s": 8.5}
    assert stalls["standstills"]["count"] == 1
    assert 9.5 <= stalls["standstills"]["total_s"] <= 10.5
    assert stalls["silences"]["count"] == 1 and stalls["silences"]["max_s"] == 2.0
    assert stalls["motion"]["stopped"] == 0.5 and stalls["motion"]["walk"] == 0.5
    assert d["progress"]["placed"] == 40 and d["progress"]["total"] == 50
    assert d["progress"]["fraction"] == 0.8 and d["progress"]["stop"] == "stalled"
    assert d["failure"]["reason"] == "trapped: 4 stalls in a row"
    assert d["failure"]["failures_by_reason"] == {"no_stance": 1}
    assert d["git"] == {"commit": "a" * 40, "dirty": True, "config_sha256": "c" * 64}
    assert d["config"]["supply"] is True and d["config"]["awareness"] == "sections"
    assert d["deep"] is True and d["analysis_error"] is None
    assert len(run_digest.dumps(d)) < run_digest.MAX_BYTES


def test_the_sqlite_event_store_gives_the_same_digest_as_the_jsonl_log(tmp_path):
    run = make_run(tmp_path, STAMP, result="fail")
    from_jsonl = run_digest.build(run)
    event_log.convert(run / "events.jsonl", delete=True)

    from_store = run_digest.build(run)

    assert from_store["phases_s"] == from_jsonl["phases_s"]
    assert from_store["stalls"] == from_jsonl["stalls"]


def test_runner_phase_timings_win_and_a_bloated_run_still_fits_in_5_kb(tmp_path):
    runner = {
        "phases_s": {"setup_arena": 35.09, "building_structure": 1986.63},
        "stages_s": {f"stage {n}": float(n) for n in range(40)},
        "job_duration_s": 2854.59,
    }
    run = make_run(tmp_path, STAMP, result="fail", build_extra=runner)
    rows = [
        {"case": f"case {n} " + "x" * 300, "pass": False, "detail": "y" * 900}
        for n in range(30)
    ]
    (run / "summary.json").write_text(json.dumps(rows), encoding="utf-8")

    d = run_digest.build(run, deep=False)

    assert d["phase_source"] == "runner"
    assert d["phases_s"] == {"setup_arena": 35.1, "building_structure": 1986.6}
    assert d["job_s"] == 2854.6
    assert len(run_digest.dumps(d)) <= run_digest.MAX_BYTES
    assert d["build_key"] == KEY and d["failure"]["reason"]  # the essentials stay


@pytest.mark.parametrize(
    ("result", "outcome"),
    [("pass", "pass"), ("fail", "fail"), ("crash", "crashed"), ("killed", "aborted")],
)
def test_outcome_of_each_way_a_run_ends(tmp_path, result, outcome):
    run = make_run(tmp_path, STAMP, result=result, with_events=False)

    d = run_digest.build(run, deep=False)

    assert d["outcome"] == outcome
    assert (d["failure"] is None) == (outcome == "pass")
    if result == "crash":
        assert d["failure"]["reason"] == "RuntimeError: kit failed"
    if result == "killed":
        assert d["failure"]["reason"].startswith("no result; last stage 'placing'")


def test_a_fresh_run_without_terminal_status_is_running_and_its_exit_hook_ends_it(
    tmp_path,
):
    run = make_run(tmp_path, STAMP, result="running", with_events=False, age_s=None)

    assert run_digest.build(run, deep=False)["outcome"] == "running"
    assert run_digest.may_be_running(run)
    assert run_digest.build(run, deep=False, ended=True)["outcome"] == "aborted"


def test_a_staged_run_holds_however_quiet_until_a_week(tmp_path):
    run = make_run(tmp_path, STAMP, result="running", with_events=False)
    progress = json.loads((run / "progress.json").read_text())
    (run / "progress.json").write_text(json.dumps({**progress, "stage": "staged"}))
    age(run, 3 * 86400)  # staged three days ago, nothing written since

    assert run_digest.activity(run) == "running"
    assert run_digest.activity(run, time.time() + 5 * 86400) == "abandoned"


def test_build_key_comes_from_run_config_else_argv_and_names_the_gather_target(
    tmp_path,
):
    run = make_run(tmp_path, STAMP, result="fail", with_events=False)
    assert run_digest.build_key(run_digest.identity(run)) == KEY

    (run / "run_config.json").unlink()  # an older run: build.json's argv says it
    ident = run_digest.identity(run)
    assert ident["source"] == "argv"
    assert run_digest.build_key(ident) == "build:house-large@-24,64,56"  # no seed

    gather = {"job": "gather", "target": "iron", "start": [68, 71, -64], "seed": 4}
    assert run_digest.build_key(gather) == "gather:iron@68,71,-64#4"
    assert run_digest.build_key({"job": "gather", "start": None}) is None


def test_a_stage_lab_is_its_own_build_key_and_the_library_key_is_the_whole_build(
    tmp_path,
):
    # A site-only or structure-only lab is not comparable with a whole build: its
    # key carries the segment. The stage library is shared by all of them.
    run = make_run(tmp_path, STAMP, result="fail", with_events=False)
    config = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    config["run"] |= {"start_from": "site_ready", "until": "structure"}
    (run / "run_config.json").write_text(json.dumps(config), encoding="utf-8")

    ident = run_digest.identity(run)

    assert ident["mode"] == "from" and ident["start_from"] == "site_ready"
    assert run_digest.build_key(ident) == KEY + "+from=site_ready+until=structure"
    assert run_digest.build_key(ident, segment=False) == KEY
    only_site = {**ident, "start_from": None, "until": "site"}
    assert run_digest.build_key(only_site) == KEY + "+until=site"


def test_write_then_load_round_trips_and_ensure_keeps_a_deep_digest(tmp_path):
    run = make_run(tmp_path, STAMP, result="fail")
    written = run_digest.write(run)
    stamp = (run / run_digest.NAME).stat().st_mtime_ns

    assert run_digest.load(run) == written
    assert run_digest.ensure(run) == written
    assert (run / run_digest.NAME).stat().st_mtime_ns == stamp  # not rewritten


def test_runs_table_numbers_attempts_per_key_oldest_first(tmp_path):
    make_run(tmp_path, "20261118T100000000000Z", result="fail", with_events=False)
    make_run(tmp_path, "20261119T100000000000Z", result="pass", with_events=False)
    make_run(
        tmp_path,
        "20261119T110000000000Z",
        result="fail",
        start=[0, 64, 0],
        with_events=False,
    )

    values = run_history.digests(tmp_path / run_history.FAMILY)
    lines = run_history.listing(values, "house-large@-24", last=10, unknown=False)

    assert lines[0] == f"{KEY}  (2 runs)"
    assert lines[1].split()[:3] == ["#", "started", "outcome"]
    assert lines[2].split()[:4] == ["1", "11-18", "10:00", "fail"]
    assert lines[3].split()[:4] == ["2", "11-19", "10:00", "pass"]
    assert "40/50 80% stalled" in lines[2] and "aaaaaaa*" in lines[2]
    assert "2/0:14" in lines[2]  # the watchdog's stalls: count/time


def test_backfill_skips_running_runs_and_existing_deep_digests(tmp_path, capsys):
    done = make_run(tmp_path, "20261118T100000000000Z", result="fail")
    live = make_run(tmp_path, "20261119T100000000000Z", result="running", age_s=None)

    assert run_history.backfill([done, live], deep=True, force=False) == 1
    assert run_history.backfill([done, live], deep=True, force=False) == 0
    assert (done / run_digest.NAME).is_file()
    assert not (live / run_digest.NAME).exists()
    assert "may still be running" in capsys.readouterr().out


def _live_common():
    spec = importlib.util.spec_from_file_location(
        "live_common", Path(__file__).resolve().parents[1] / "scripts/live_common.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_exit_hook_writes_a_digest_when_the_run_crashes(tmp_path, monkeypatch):
    live = _live_common()
    monkeypatch.setattr(live, "ROOT", tmp_path)

    with pytest.raises(RuntimeError), live.digest_on_exit():
        folder = live.results_folder()
        folder.mkdir(parents=True)
        (folder / "launch_status.json").write_text('{"status": "failed"}')
        raise RuntimeError("bridge went away")

    digest = run_digest.load(folder)
    assert digest["outcome"] == "crashed" and digest["deep"]


def test_identity_fields_match_the_fixture(tmp_path):
    run = make_run(tmp_path, STAMP, result="fail", with_events=False)
    ident = run_digest.identity(run)
    assert (ident["build"], ident["start"], ident["seed"], ident["mode"]) == (
        HOUSE,
        START,
        SEED,
        "supply",
    )
    assert (
        run_digest.started_at(run)
        == datetime(2026, 11, 19, 13, 36, 18, 892664, tzinfo=UTC).timestamp()
    )


def test_a_build_in_another_order_is_its_own_build_key(tmp_path):
    # Two orders of one build are an A/B, not attempts at the same thing; the zone
    # order (the default) keeps the keys runs already have. The stage library is
    # shared: a stage is the same whatever order reached it.
    run = make_run(tmp_path, STAMP, result="fail", with_events=False)
    config = json.loads((run / "run_config.json").read_text(encoding="utf-8"))
    config["run"] |= {"start_from": "site_ready", "order": "storeys"}
    (run / "run_config.json").write_text(json.dumps(config), encoding="utf-8")

    ident = run_digest.identity(run)

    assert run_digest.build_key(ident) == KEY + "+from=site_ready+order=storeys"
    assert run_digest.build_key(ident, segment=False) == KEY
    assert run_digest.build_key({**ident, "order": "zones"}) == KEY + "+from=site_ready"
