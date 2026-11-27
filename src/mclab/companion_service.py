"""In-game /mcb commands -> the dashboard's companion job path.

The body mod records each /mcb use as a `player_command` event (stop also cancels
the body in the mod, with no Python or model involved). This loop reads those
events, applies the owner/allowlist from
config/companion.json (empty lists let every player in), validates arguments and
dispatches.
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
RUNS = ROOT / "results/companion"
POLL_SECONDS = 1.0
ITEM = re.compile(r"[a-z0-9_]{1,64}")
BUILD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")


def allowed(uuid, name="", *, listed=False):
    """Who may command the bot. Nobody listed (or no config file): every player in
    the game. Otherwise only the listed owners and allowlist, each a player UUID or
    a name (case-insensitive). A config that cannot be read refuses everyone.
    ``listed``: only someone the config names (an empty list lets nobody in)."""
    if not CONFIG.is_file():
        return not listed
    try:
        value = read_json(CONFIG)
        people = {
            str(u).lower()
            for key in ("owners", "allowlist")
            for u in value.get(key) or []
        }
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    if not people:
        return not listed
    return bool({str(uuid).lower(), str(name).lower()} & people - {""})


def parse(data):
    """The validated (command, args) of a player_command, or ValueError with a
    player-safe message."""
    command = data.get("command")
    if command in {"status", "stop"}:
        return command, {}
    if command == "goto":
        coords = [data.get(axis) for axis in "xyz"]
        if any(type(c) is not int or abs(c) > 30_000_000 for c in coords):
            raise ValueError("Bad coordinates.")
        return command, dict(zip("xyz", coords, strict=True))
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
    if command == "lobby":
        return command, dict(data)
    raise ValueError("Unknown command.")


def _lobby():
    from mclab import arena_lobby

    return arena_lobby.default_lobby()


def _start(request):
    from mclab import dashboard

    return dashboard.Dashboard().start(request)


def _kill():
    from mclab import dashboard

    return dashboard.request_kill()


def _status(tail, now=None):
    """The dashboard's session when one is active; otherwise the newest run, live
    when it still writes or holds for inspection (run-build.py has no session)."""
    from mclab import dashboard, run_digest

    now = time.time() if now is None else now
    sessions = sorted(dashboard.SESSIONS.glob("*/session.json"))
    session = read_json(sessions[-1]) if sessions else None
    folders = sorted(RUNS.glob("*/progress.json"), key=lambda p: p.stat().st_mtime)
    run = folders[-1].parent if folders else None
    progress = companion_progress.read_progress(run) if run else None
    if (session or {}).get("status") not in companion_status.ACTIVE:
        live = run is not None and run_digest.activity(run, now) == "running"
        session = {"status": "running"} if live else None
    return companion_status.explain(progress, session, list(tail), now)


def handle(event, tail=()):
    """The reply for one player_command event. Never raises."""
    data = event.get("data") or {}
    if not allowed(data.get("player_uuid", ""), data.get("player_name", "")):
        return "Not allowed."
    try:
        command, args = parse(data)
        if command == "stop":
            return (
                "Stopped."
                if "Kill requested" in _kill()["status"]
                else "Nothing to stop."
            )
        if command == "status":
            return _status(tail)
        if command == "lobby":
            player = str(data.get("player_name") or "")
            result = _lobby().handle_action(player, args)
            return str(result.get("message") or "Lobby updated.")[:200]
        if command == "goto":
            return "goto not supported yet."
        if command == "gather":
            # run-gather has no item/count option yet: only the iron-pickaxe target
            # is selectable.
            iron = args["item"] in {"iron", "iron_pickaxe"}
            _start({"kind": "companion", "job": "gather", "iron": iron})
            return "Gathering started (item and count are not used yet)."
        build = f"builds/{args['name']}.build.json"
        _start({"kind": "companion", "job": "build", "build": build})
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
        headers={
            "Authorization": "Bearer " + state["token"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.loads(response.read())


def notable(kind):
    """Kept in the status tail: work, failures and commands. Samples and receipts
    (three quarters of the body's events) would push recent work out of it."""
    return (
        kind == "player_command"
        or companion_status.is_work(kind)
        or companion_status.is_failure(kind)
    )


def poll(state, fetch=_call, tell=None, now=time.time):
    """One poll: advance state's cursor, reply to each player_command.
    A ring gap skips forward. Each kept event is stamped ``at`` when read (the
    bridge gives ticks, not times) so the stall answer counts recent work."""
    batch = fetch(f"/events?after={state['cursor']}")
    if batch["launch_id"] != state.get("launch_id"):
        # server restarted: sequences restart
        state.update(launch_id=batch["launch_id"], cursor=0)
        if batch["events"]:
            batch = fetch("/events?after=0")
    if batch.get("gap"):
        print("companion-service: event ring gap, some events were lost", flush=True)
    for event in batch["events"]:
        state["cursor"] = event["sequence"]
        if not notable(event["type"]):
            continue
        event.setdefault("at", now())
        state["tail"].append(event)
        if event["type"] == "player_command":
            reply = handle(event, state["tail"])
            data = event["data"]
            send = tell or (lambda u, t: fetch("/tell", {"uuid": u, "text": t}))
            send(data["player_uuid"], reply)
    state["cursor"] = max(state["cursor"], batch["latest_sequence"])


def run(stop=None, call=None, pause=None):
    """Reply to /mcb until ``stop`` is set. The lab supervisor runs this for the
    server's whole life; `mclab companion-service` runs it alone. While the bridge
    is not up (the server starting) each poll fails; a failure is logged once."""
    call = call or _call
    wait = pause or (stop.wait if stop is not None else time.sleep)
    state = {"cursor": 2**62, "launch_id": None, "tail": deque(maxlen=50)}
    first, last_error = True, None
    while stop is None or not stop.is_set():
        try:
            if first:  # start at the head: old commands are not replayed
                head = call(f"/events?after={2**62}")
                state.update(
                    cursor=head["latest_sequence"], launch_id=head["launch_id"]
                )
                first = False
            poll(state, fetch=call)
            last_error = None
        except (OSError, ValueError, KeyError) as error:
            text = f"companion-service: {type(error).__name__}: {error}"
            if text != last_error:
                print(text, flush=True)
            last_error = text
        wait(POLL_SECONDS)
