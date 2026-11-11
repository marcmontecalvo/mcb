"""Build-run stats: one streamed pass over the event log, cached beside the run."""

import gzip
import json
import os
import time

import pytest

from mclab import build_stats as bs
from mclab import dashboard_runs as runs
from mclab.paths import write_json

SECOND = 10**9


def line(at, kind, payload):
    """One log line in the recorder's field order (the scan reads only the head)."""
    head = f'{{"schema_version":1,"sequence":1,"monotonic_ns":{at * SECOND},'
    return (
        head
        + f'"type":"{kind}","payload":{json.dumps(payload, separators=(",", ":"))}}}'
    )


def request(at, op):
    return line(at, "request", {"sequence": 1, "op": op})


def event(at, kind, **data):
    payload = {"sequence": 1, "tick": at * 20, "phase": "fabric_end"}
    return line(at, "server_event", {**payload, "type": kind, "data": data})


def placed(at, block):
    return event(at, "place_block_done", pos=[0, 64, 0], placed=block)


LOG = [
    request(0, "spawn"),
    event(5, "policy_sample", terrain="x" * 5000),  # skipped unparsed
    event(10, "craft_done"),  # gathering ends with the last craft
    placed(12, "minecraft:crafting_table"),
    event(40, "block_broken", position=[1, 65, 1], block="minecraft:oak_log"),
    event(41, "break_block_done", pos=[1, 65, 1]),
    placed(70, "minecraft:dirt"),  # site fill
    placed(100, "minecraft:oak_planks[axis=y]"),  # building starts
    event(110, "place_block_failed", reason="state_unreachable"),
    event(111, "place_block_failed", reason="state_unreachable"),
    event(112, "break_block_failed", reason="out_of_reach"),
    placed(120, "minecraft:scaffolding"),  # not a house block
    placed(130, "minecraft:glass_pane"),
    event(140, "plan_finished", plan_id="p1", status="reached", expanded=12),
    event(141, "plan_finished", plan_id="p2", status="incomplete", expanded=4000),
    request(400, "place_block"),  # last work: 270 s after the last edit
    request(900, "heartbeat"),  # --inspect keeps the heartbeat going: not work
    request(901, "stop"),
    '{"schema_version":1,"monotonic_ns":95',  # cut short by a kill
]


@pytest.fixture
def run(tmp_path):
    folder = tmp_path / "20261110T000000000000Z"
    folder.mkdir()
    (folder / "events.jsonl").write_text("\n".join(LOG), encoding="utf-8")
    return folder


def test_scan_times_phases_counts_and_gap(run):
    found = bs.scan(run)
    assert found["total_s"] == 400
    assert found["phases_s"] == {"gather": 10, "trees": 30, "site": 60, "building": 300}
    assert found["placed"] == 2 and found["per_minute"] == 0.4
    assert found["places_ok"] == 5 and found["breaks_ok"] == 1
    assert found["place_failed"] == {"state_unreachable": 2}
    assert found["break_failed"] == {"out_of_reach": 1}
    assert (found["plans"], found["plans_incomplete"]) == (2, 1)
    assert found["plan_expanded_max"] == 4000
    assert (found["longest_gap_s"], found["longest_gap_at_s"]) == (270, 130)


def test_gzipped_log_reads_the_same(run):
    plain = bs.scan(run)
    raw = (run / "events.jsonl").read_bytes()
    (run / "events.jsonl").unlink()
    with gzip.open(run / "events.jsonl.gz", "wb") as file:
        file.write(raw)
    assert [p.name for p in bs.event_logs(run)] == ["events.jsonl.gz"]
    assert bs.scan(run) == plain


def test_cache_is_reused_survives_pruning_and_follows_the_log(run, monkeypatch):
    old = time.time() - 3600
    os.utime(run / "events.jsonl", (old, old))
    assert bs.stats(run)["state"] == "pending"
    bs.compute(run)
    assert bs.stats(run)["state"] == "ready"
    monkeypatch.setattr(bs, "scan", lambda folder: pytest.fail("scanned again"))
    assert bs.stats(run)["placed"] == 2
    with open(run / "events.jsonl", "a", encoding="utf-8") as file:
        file.write("\n" + placed(401, "minecraft:stone"))
    os.utime(run / "events.jsonl", (old, old))
    assert bs.cached(run) is None  # the log changed: stale
    monkeypatch.undo()
    bs.compute(run)
    (run / "events.jsonl").unlink()  # pruned: the cache is all that is left
    assert bs.stats(run)["placed"] == 3
    assert bs.stats(run)["state"] == "ready"


def test_a_log_still_being_written_is_not_scanned(run):
    assert bs.is_live(run)
    assert bs.stats(run)["state"] == "running"
    assert bs.stats(run)["result"] == "running"


def test_obsolete_cache_with_pruned_log_is_unavailable(run):
    write_json(run / bs.CACHE, {"version": bs.VERSION - 1, "events": True})
    (run / "events.jsonl").unlink()
    assert bs.stats(run)["state"] == "no_log"
    assert "events" not in bs.stats(run)


def test_report_reads_build_json(run):
    write_json(
        run / "build.json",
        {
            "stop": "need_materials",
            "job_stop": "not_built",
            "counts": {"correct": 280, "wrong": 0, "missing": 947, "unknown": 0},
            "site": {"stop": "ready", "cleared": 4, "filled": 251, "blocked": {}},
            "stalls": [{"recovery": "escape_hole"}, {"recovery": "escape_hole"}],
            "failures": [[[1, 2, 3], "no_stance", 5, ""]],
            "refills": ["done"],
            "shortfall": {"minecraft:lantern": 24},
        },
    )
    found = bs.report(run)
    assert found["result"] == "need_materials" and found["finished"]
    assert found["cells"]["missing"] == 947
    assert found["site"] == {
        "stop": "ready",
        "cleared": 4,
        "filled": 251,
        "blocked": 0,
        "trees": None,
    }
    assert (found["stalls"], found["recoveries"]) == (2, {"escape_hole": 2})
    assert found["failures"] == {"no_stance": 1}
    assert (found["refills"], found["shortfall"]) == (1, {"minecraft:lantern": 24})


def test_killed_run_falls_back_to_progress_and_marker_file(run):
    from mclab import companion_progress as cp

    detail = "site=ready cleared=0 filled=260 steps=2192 stalls=201 stop="
    cp.write_progress(run, job="build", stage="building", done=835, detail=detail)
    (run / "KILLED_run23_stuck_at_835.txt").write_text("")
    found = bs.report(run)
    assert found["result"] == "killed" and not found["finished"]
    assert found["note"] == "KILLED_run23_stuck_at_835"
    assert (found["stalls"], found["site"]["filled"]) == (201, 260)
    assert found["placed_progress"] == 835
    assert bs.is_build(run)


def test_previous_run_is_the_one_before_it_of_the_same_build(tmp_path):
    for name, build in (("1", "a"), ("2", "b"), ("3", "a"), ("4", None), ("5", "a")):
        folder = tmp_path / name
        (folder / "checkpoints").mkdir(parents=True)
        if build:
            head = {"version": 1, "seed": 7, "start": [1, 64, 2], "build": build}
            write_json(folder / "checkpoints/0005.json", {**head, "cells": []})
    (tmp_path / "not-a-build").mkdir()
    rows = {r["run"]: r for r in bs.runs(tmp_path)}
    assert list(rows) == ["5", "4", "3", "2", "1"]
    assert {k: r["previous"] for k, r in rows.items()} == {
        "5": "3",
        "4": None,  # unknown identities must not be paired automatically
        "3": "1",
        "2": None,
        "1": None,
    }
    assert (rows["5"]["seed"], rows["5"]["start"]) == (7, [1, 64, 2])


def test_completion_extends_end_and_site_materials_do_not_inflate_rate(run):
    with (run / "events.jsonl").open("a", encoding="utf-8") as file:
        file.write("\n" + placed(410, "minecraft:dirt"))
        file.write("\n" + placed(420, "minecraft:oak_planks"))
    found = bs.scan(run)
    assert found["total_s"] == 420
    assert found["placed"] == 3
    assert found["per_minute"] == 0.56


def test_no_house_start_has_no_house_placements_or_rate(tmp_path):
    (tmp_path / "events.jsonl").write_text(
        "\n".join([request(1, "spawn"), placed(60, "minecraft:dirt")])
    )
    found = bs.scan(tmp_path)
    assert found["placed"] == 0
    assert found["per_minute"] is None


def test_scanner_computes_in_the_background(run):
    old = time.time() - 3600
    os.utime(run / "events.jsonl", (old, old))
    scanner = bs.Scanner()
    scanner.want(run)
    scanner.want(run)  # asked twice, queued once
    deadline = time.time() + 10
    while not bs.cached(run) and time.time() < deadline:
        time.sleep(0.02)
    assert bs.stats(run)["placed"] == 2


# ---- the dashboard's build form ----


@pytest.fixture
def build_request(tmp_path, monkeypatch):
    from mclab import natural

    workspace = tmp_path
    monkeypatch.setattr(runs, "ROOT", tmp_path)

    snapshots = workspace / "runtime/natural"
    (snapshots / "natural-31415926").mkdir(parents=True)
    (snapshots / "natural-31415926/level.dat").touch()
    (snapshots / "natural-broken").mkdir()
    monkeypatch.setattr(natural, "SNAPSHOTS", snapshots)
    (workspace / "scripts").mkdir(exist_ok=True)
    (workspace / "scripts/run-build.py").write_text("")
    (workspace / "builds").mkdir(exist_ok=True)
    (workspace / "builds/b.build.json").write_text("{}")
    return {"kind": "companion", "job": "build", "start": [-28, 64, 56]} | {
        "build": "builds/b.build.json"
    }


def test_build_form_passes_every_run_build_option(build_request):
    options = {"seed": 31415926, "margin": 2, "supply": True, "coal": "ore"}
    [job] = runs.plan({**build_request, **options, "inspect": True})
    assert job["args"] == [
        *("--start", "-28", "64", "56"),
        *("--build", "builds/b.build.json", "--dx", "3", "--dz", "2"),
        *("--rotation", "0", "--seed", "31415926", "--margin", "2"),
        *("--supply", "--coal", "ore", "--inspect"),
    ]
    assert runs.natural_seeds() == [31415926]
    [plain] = runs.plan(build_request)  # flat lab world, defaults
    assert plain["args"][-2:] == ["--margin", "1"]
    assert "--seed" not in plain["args"] and "--supply" not in plain["args"]


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": 5},  # no saved world
        {"seed": "31415926"},
        {"margin": -1},
        {"margin": 1.5},
        {"supply": "yes"},
        {"inspect": 1},
        {"coal": "charcoal", "supply": True},
        {"coal": "ore"},  # run-build.py: --coal requires --supply
    ],
)
def test_build_form_refuses_what_run_build_would(build_request, changes):
    with pytest.raises(ValueError):
        runs.plan({**build_request, **changes})


def test_dashboard_arguments_parse_in_run_build(build_request):
    """The args the form produces are ones scripts/run-build.py's parser accepts."""
    import re
    from pathlib import Path

    script = Path(__file__).parents[1] / "scripts/run-build.py"
    source = script.read_text(encoding="utf-8")
    known = set(re.findall(r'"(--[a-z-]+)"', source))
    [job] = runs.plan({**build_request, "seed": 31415926, "supply": True})
    assert {a for a in job["args"] if a.startswith("--")} <= known


def test_launch_is_recorded_beside_the_run(build_request, tmp_path):
    workspace = tmp_path
    folder = workspace / "results/companion/20261110T000000000000Z"
    folder.mkdir(parents=True)
    [job] = runs.plan({**build_request, "seed": 31415926})
    assert runs.record_launch({**job, "before": []}) == folder
    found = bs.identity(folder)
    assert (found["build"], found["seed"]) == ("builds/b.build.json", 31415926)
    assert found["start"] == [-28, 64, 56]
