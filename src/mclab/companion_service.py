"""In-game /mcb commands -> the dashboard's companion job path.

The body mod records each /mcb use as a `player_command` event (stop also cancels the body in the mod, with
no Python or model involved). This loop reads those events, applies the owner/allowlist from
config/companion.json (empty lists refuse everyone), validates arguments and dispatches.
"""

import json
import re
import time
import urllib.request
from collections import deque

from mclab import companion_progress, companion_status
from mclab.paths import ROOT, config, read_json
from mclab.server import STATE

CONFIG = ROOT / "config/companion.json"
POLL_SECONDS = 1.0
ITEM = re.compile(r"[a-z0-9_]{1,64}")
BUILD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def allowed(uuid):
    try:
        value = read_json(CONFIG)
    except (OSError, ValueError):
        return False
    people = {str(u).lower() for key in ("owners", "allowlist") for u in value.get(key, [])}
    return str(uuid).lower() in people


def parse(data):
    """The validated (command, args) of a player_command, or ValueError with a player-safe message."""
    command = data.get("command")
    if command in {"status", "stop"}:
        return command, {}
    if command == "goto":
        coords = [data.get(axis) for axis in "xyz"]
        if any(type(c) is not int or abs(c) > 30_000_000 for c in coords):
            raise ValueError("Bad coordinates.")
        return command, dict(zip("xyz", coords))
    if command == "gather":
        count = data.get("count")
        if not isinstance(data.get("item"), str) or not ITEM.fullmatch(data["item"]):
            raise ValueError("Bad item name.")
        if type(count) is not int or not 1 <= count <= 2304:
            raise ValueError("Count must be 1 to 2304.")
        return command, {"item": data["item"], "count": count}
    if command == "build":
        if not isinstance(data.get("name"), str) or not BUILD.fullmatch(data["name"]):
            raise ValueError("Bad build name.")
        return command, {"name": data["name"]}
    raise ValueError("Unknown command.")


def _start(request):
    from mclab import dashboard

    return dashboard.Dashboard().start(request)


def _kill():
    from mclab import dashboard

    return dashboard.request_kill()


def _status(tail):
    from mclab import dashboard

    sessions = sorted(dashboard.SESSIONS.glob("*/session.json"))
    session = read_json(sessions[-1]) if sessions else None
    folders = sorted(
        (ROOT / "results/companion").glob("*/progress.json"), key=lambda p: p.stat().st_mtime
    )
    progress = companion_progress.read_progress(folders[-1].parent) if folders else None
    return companion_status.explain(progress, session, list(tail), time.time())


def handle(event, tail=()):
    """The reply for one player_command event. Never raises."""
    data = event.get("data") or {}
    if not allowed(data.get("player_uuid", "")):
        return "Not allowed."
    try:
        command, args = parse(data)
        if command == "stop":
            return "Stopped." if "Kill requested" in _kill()["status"] else "Nothing to stop."
        if command == "status":
            return _status(tail)
        if command == "goto":
            return "goto not supported yet."
        if command == "gather":
            # run-gather has no item/count option yet: only the iron-pickaxe target is selectable.
            _start({"kind": "companion", "job": "gather", "iron": args["item"] in {"iron", "iron_pickaxe"}})
            return "Gathering started (item and count are not used yet)."
        _start({"kind": "companion", "job": "build", "build": f"builds/{args['name']}.build.json"})
        return "Build started."
    except ValueError as error:
        return str(error)[:200]
    except Exception as error:  # a bad command must not kill the service
        return f"Failed: {type(error).__name__}."


def _call(path, payload=None):
    state = read_json(STATE)
    request = urllib.request.Request(
        f"http://127.0.0.1:{config()['bridge_port']}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + state["token"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.loads(response.read())


def poll(state, fetch=_call, tell=None):
    """One poll: advance state's cursor, reply to each player_command. A ring gap skips forward."""
    batch = fetch(f"/events?after={state['cursor']}")
    if batch["launch_id"] != state.get("launch_id"):
        state.update(launch_id=batch["launch_id"], cursor=0)  # server restarted: sequences restart
        if batch["events"]:
            batch = fetch("/events?after=0")
    if batch.get("gap"):
        print("companion-service: event ring gap, some events were lost", flush=True)
    for event in batch["events"]:
        state["cursor"] = event["sequence"]
        state["tail"].append(event)
        if event["type"] == "player_command":
            reply = handle(event, state["tail"])
            data = event["data"]
            (tell or (lambda u, t: fetch("/tell", {"uuid": u, "text": t})))(data["player_uuid"], reply)
    state["cursor"] = max(state["cursor"], batch["latest_sequence"])


def run():
    if not CONFIG.is_file():
        CONFIG.write_text(json.dumps({"owners": [], "allowlist": []}, indent=2) + "\n", encoding="utf-8")
    state = {"cursor": 2**62, "launch_id": None, "tail": deque(maxlen=50)}
    first = True
    while True:
        try:
            if first:  # start at the head: old commands are not replayed
                head = _call(f"/events?after={2**62}")
                state.update(cursor=head["latest_sequence"], launch_id=head["launch_id"])
                first = False
            poll(state)
        except (OSError, ValueError, KeyError) as error:
            print(f"companion-service: {type(error).__name__}: {error}", flush=True)
        time.sleep(POLL_SECONDS)
