# ruff: noqa: E501
"""Shared helpers for the companion-track live checks (body action ops on the lab server)."""

import json
import math
import sys
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from mclab import run_digest
from mclab.bridge import Bridge
from mclab.paths import ROOT

IGNORED = {
    "shape", "north", "east", "south", "west", "up", "down",
    "waterlogged", "powered", "power", "distance", "persistent", "open",
}  # fmt: skip


_FOLDERS: list[Path] = []  # this process's run folders, for digest_on_exit


def results_folder() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "results/companion" / stamp
    _FOLDERS.append(folder)
    return folder


@contextmanager
def digest_on_exit():
    """Write <run>/digest.json for every run folder this process opened, however
    the run ends (pass, fail, crash, Ctrl-C). Outside launch_record, so the digest
    sees the final launch_status.json. A Ctrl-C skips the event-log pass (it takes
    up to a minute); `mclab runs backfill` fills it in later."""
    interrupted = False
    try:
        yield
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        for folder in _FOLDERS:
            if not folder.is_dir():
                continue
            try:
                value = run_digest.write(folder, deep=not interrupted, ended=True)
                print(
                    f"digest: {folder / run_digest.NAME} ({value['outcome']}, "
                    f"key {value['build_key']})",
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 - a digest must not mask the run's own result
                print(f"digest failed for {folder}: {error!r}", file=sys.stderr, flush=True)


def open_bridge(folder: Path) -> Bridge:
    bridge = Bridge(folder / "events.sqlite3")
    bridge.budget = 12000
    bridge.synchronize()
    if bridge.http("/state").get("body"):
        raise RuntimeError("live checks need an idle lab")
    return bridge


def spawn_ready(bridge: Bridge) -> dict:
    bridge.send("spawn")
    return bridge.wait(lambda s: s["state"] == "ready", timeout=40)


def call(bridge: Bridge, op: str, timeout: float = 15, **fields) -> dict:
    """Sends one op and returns its <op>_done / <op>_failed event data plus a "type" key."""
    payload = bridge.send(op, **fields)
    sequence = payload["sequence"]
    kinds = (f"{op}_done", f"{op}_failed")

    def find(_state=None):
        for event in bridge.events:
            data = event["data"]
            if int(data.get("request_sequence", -1)) != sequence:
                continue
            if event["type"] in kinds:
                return event
            if event["type"] in ("rejected", "rejected_at_execution"):
                raise RuntimeError(f"{op} rejected: {data.get('reason')}")
        return None

    bridge.wait(lambda s: find() is not None, timeout=timeout)
    event = find()
    data = event[
        "data"
    ]  # entity ops carry their own "type" (entity type); the event kind wins here
    return {**data, "entity_type": data.get("type"), "type": event["type"]}


def kit(bridge: Bridge, **fields) -> dict:
    result = call(bridge, "kit", **fields)
    if result["type"] != "kit_done":
        raise RuntimeError(f"kit failed: {result.get('reason')}")
    return result


def feet_cell(state: dict) -> list[int]:
    x, y, z = state["body"]["position"]
    return [math.floor(x), math.floor(y + 1e-6), math.floor(z)]


def at(feet: list[int], dx: int, dy: int, dz: int) -> list[int]:
    return [feet[0] + dx, feet[1] + dy, feet[2] + dz]


def count(inventory: list[dict], item: str) -> int:
    return sum(s["count"] for s in inventory if s["item"] == item)


def parse_state(text: str) -> tuple[str, dict]:
    block, _, rest = text.partition("[")
    props = dict(p.split("=", 1) for p in rest.rstrip("]").split(",") if p)
    return block, props


def state_matches(target: str, placed: str) -> bool:
    block, props = parse_state(target)
    placed_block, placed_props = parse_state(placed)
    return block == placed_block and all(
        placed_props.get(k) == v for k, v in props.items() if k not in IGNORED
    )


def record_crash(folder: Path, rows: list[dict], error: BaseException) -> None:
    """Keep a driver crash: traceback to error.txt and a failing summary row."""
    text = "".join(traceback.format_exception(error))
    (folder / "error.txt").write_text(text, encoding="utf-8")
    print(text, flush=True)
    rows.append({"case": "driver crashed", "pass": False, "detail": f"{type(error).__name__}: {error}"})


def finish(folder: Path, rows: list[dict], name: str) -> int:
    (folder / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    width = max(len(r["case"]) for r in rows)
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['case']:<{width}}  {r['detail']}")
    failed = [r for r in rows if not r["pass"]]
    print(f"\n{name}: {len(rows) - len(failed)}/{len(rows)} passed -> {folder}")
    return 1 if failed else 0
