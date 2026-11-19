"""Small fake build-run folders (results/companion/<stamp>/) for digest/prune tests."""

import json
import os
import time
from pathlib import Path

T0 = 1000.0
OLD = time.time() - 3 * 86400
HOUSE = "tests/fixtures/schematics/house-large.build.json"
START = [-24, 64, 56]
SEED = 31415926


def _at(tick: int) -> int:
    return int((T0 + tick / 20) * 1e9)


def server(kind: str, tick: int, data: dict | None = None) -> dict:
    return {
        "body_id": "b",
        "monotonic_ns": _at(tick),
        "type": "server_event",
        "payload": {"type": kind, "tick": tick, "data": data or {}},
    }


def request(op: str, tick: int, **fields) -> dict:
    return {
        "body_id": "b",
        "monotonic_ns": _at(tick),
        "type": "request",
        "payload": {"op": op, **fields},
    }


def python(kind: str, tick: int, **payload) -> dict:
    return {"body_id": "b", "monotonic_ns": _at(tick), "type": kind, "payload": payload}


def events() -> list[dict]:
    """20 s of a build: walks 5 s, stands 10 s with nothing to show for it (one
    standstill), walks 5 s; a craft at 7.5 s, dirt at 16 s (site), the first house
    block at 18 s, last work at 19 s; one 2 s Python silence."""
    out = [server("body_state", 0, {"state": "ready"}), request("kit", 0)]
    x = 0.0
    for tick in range(401):
        if tick < 100 or tick >= 300:
            x += 0.2  # walk: 0.19 <= v < 0.245 blocks/tick
        out.append(
            server("policy_sample", tick, {"body": {"position": [x, 64.0, 0.0]}})
        )
    out += [
        server("craft_done", 150, {"request_sequence": 9}),
        python("python_silence", 240, idle_s=1.5, stack=["wait", "run_job"]),
        python("python_silence_end", 250, duration_s=2.0),
        request("place_block", 310),
        server("place_block_done", 320, {"placed": "minecraft:dirt"}),
        server("place_block_done", 360, {"placed": "minecraft:oak_planks"}),
        request("place_block", 380),
        server("stopped", 400),
    ]
    out.sort(key=lambda e: e["monotonic_ns"])
    for number, record in enumerate(out, 1):
        record["sequence"] = number
    return out


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_run(
    root: Path,
    stamp: str,
    *,
    result: str = "fail",  # pass | fail | crash | killed | running
    start=START,
    build: str = HOUSE,
    test: str = "build",
    with_events: bool = True,
    checkpoints: int = 3,
    age_s: float | None = 3 * 86400,
    build_extra: dict | None = None,
) -> Path:
    """A run folder; ``age_s`` back-dates every file (None: leave them fresh)."""
    run = root / "results" / "companion" / stamp
    run.mkdir(parents=True)
    write_json(
        run / "run_config.json",
        {
            "run": {
                "test": test,
                "build": build,
                "start": start,
                "seed": SEED,
                "supply": True,
                "iron": False,
                "raw_iron": None,
            },
            "body": {
                "control_profile": "lockstep-three-tick-v1",
                "awareness": {"preset": "sections"},
            },
        },
    )
    write_json(
        run / "provenance.json",
        {"config_sha256": "c" * 64, "git": {"commit": "a" * 40, "dirty": True}},
    )
    passed = result == "pass"
    updated = time.time() - (age_s or 0)
    write_json(
        run / "progress.json",
        {
            "job": test,
            "stage": "done" if result in ("pass", "fail") else "placing",
            "done": 40,
            "total": 50,
            "detail": "stalls=2",
            "updated_at": updated,
        },
    )
    if result in ("pass", "fail", "crash"):
        rows = [{"case": "gathered", "pass": True, "detail": "done"}]
        if result == "crash":
            rows.append(
                {
                    "case": "driver crashed",
                    "pass": False,
                    "detail": "RuntimeError: kit failed",
                }
            )
            (run / "error.txt").write_text(
                "Traceback (most recent call last):\nRuntimeError: kit failed\n",
                encoding="utf-8",
            )
        else:
            rows.append(
                {
                    "case": "build stop",
                    "pass": passed,
                    "detail": "done" if passed else "stalled steps=12",
                }
            )
        write_json(run / "summary.json", rows)
        write_json(
            run / "launch_status.json", {"status": "finished" if passed else "failed"}
        )
    elif result in ("killed", "running"):
        write_json(run / "launch_status.json", {"status": "starting"})
    if result in ("pass", "fail"):
        write_json(
            run / "build.json",
            {
                "argv": ["--start", *map(str, start), "--build", build],
                "stop": "done" if passed else "stalled",
                "stall_reason": None if passed else "trapped: 4 stalls in a row",
                "counts": {
                    "correct": 50 if passed else 40,
                    "wrong": 0,
                    "missing": 0 if passed else 10,
                    "unknown": 0,
                },
                "stalls": [
                    {"op": "goto_stance", "idle_s": 8.5},
                    {"op": "goto_stance", "idle_s": 6.0},
                ],
                "failures": [[[0, 64, 0], "no_stance", [0, 64, 1], {}]],
                "ticks": {"gather": 100, "build": 300},
                **(build_extra or {}),
            },
        )
        (run / "walkthrough.md").write_text("# walkthrough\n", encoding="utf-8")
    if with_events:
        with (run / "events.jsonl").open("w", encoding="utf-8") as stream:
            for record in events():
                stream.write(json.dumps(record) + "\n")
        (run / "map.sqlite3").write_bytes(b"m" * 1000)
    for n in range(1, checkpoints + 1):
        write_json(run / "checkpoints" / f"{n:04}.json", {"version": 1, "n": n})
    if checkpoints and result in ("pass", "fail"):
        write_json(run / "checkpoints" / "final.json", {"version": 1, "final": True})
    if age_s is not None:
        age(run, age_s)
    return run


def age(run: Path, seconds: float) -> None:
    then = time.time() - seconds
    for path in [run, *run.rglob("*")]:
        os.utime(path, (then, then))
