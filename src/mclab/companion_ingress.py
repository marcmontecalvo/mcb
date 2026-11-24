"""The play instance's chat and /mcb input: body-bridge events -> host calls.

``Ingress`` polls the body bridge's ``/events`` with its own cursor (never
replaying commands from before it started) and turns each ``player_command`` into
a host call, replying to the commanding player with a short tell, and each
``player_chat`` into ``talker.hear``. ``/mcb ask`` also goes to the talker.
Replies and the talker's speech go out as the operator-level ``api`` actor; the
command itself runs as the player (``command`` actor) so the host applies
authority. Docs: COMPANION_LLM_PLAN_2026-11-23.md sections 4 and 6.
"""

import json
import threading
import urllib.error
import urllib.request

from mclab.companion_intents import SAY_MAX, Actor

HEAD = 2**62  # past any sequence: the bridge answers with its latest
MAX_BATCH_BYTES = 8_388_608
TELL_ACTOR = Actor("api")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OSError("bridge redirect rejected")


def bridge_events_source(*, port=None, token=None, path="/events", timeout=4.0):
    """``events_source(after)`` over the body bridge's HTTP API. ``port`` and
    ``token`` are callables, read on every call so a restarted server (new token)
    is followed; by default the instance's config and supervisor state."""

    def default_port():
        from mclab import paths

        return paths.config()["bridge_port"]

    def default_token():
        from mclab import server
        from mclab.paths import read_json

        return read_json(server.STATE)["token"]

    port, token = port or default_port, token or default_token

    def source(after):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port()}{path}?after={after}",
            headers={"Authorization": "Bearer " + token()},
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(request, timeout=timeout) as response:
                data = response.read(MAX_BATCH_BYTES + 1)
        except urllib.error.URLError as error:
            raise OSError(f"bridge unreachable: {type(error).__name__}") from None
        if len(data) > MAX_BATCH_BYTES:
            raise OSError("bridge response exceeded byte limit")
        return json.loads(data)

    return source


def bridge_players_source(**kwargs):
    """``players()`` -> the online player names from the body's ``/players``, or None
    when the bridge cannot say. Takes ``bridge_events_source``'s arguments."""
    read = bridge_events_source(path="/players", **kwargs)

    def players():
        try:
            roster = read(0).get("players")
            return [p["name"] for p in roster if isinstance(p.get("name"), str)]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    return players


def _position(data):
    value = data.get("position")
    try:
        if isinstance(value, dict):
            value = [value["x"], value["y"], value["z"]]
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(float(v) for v in value)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _int(value):
    return value if type(value) is int else None


def _describe(intent, args):
    if intent == "goto":
        return f"goto {args['x']} {args['y']} {args['z']}"
    if intent == "gather":
        return f"gather {args['count']} {args['item']}"
    if intent == "attack":
        arena = args.get("arena")
        who = (
            args.get("player")
            or args.get("mob")
            or ("the nearest hostile" if args.get("nearest_hostile") else None)
            or ("every wave mob in the arena" if arena is True else None)
            or (f"every wave mob in {arena}" if arena else "?")
        )
        with_ = f" with {args['weapon']}" if args.get("weapon") else ""
        return f"attack {who}{with_}"
    return f"build {args['template']}"


HOSTILE_WORDS = frozenset({"hostile", "hostiles", "nearest_hostile", "nearest-hostile"})


def _defense_text(settings):
    """The defence settings, as one line a player reads in chat."""
    hearts = settings.get("floor_hearts", settings.get("floor_health", 0) / 2)
    return (
        f"Defence: fall back below {hearts:g} hearts, threats within "
        f"{settings.get('threat_radius')} blocks, allies within "
        f"{settings.get('ally_radius')}, base within {settings.get('base_radius')}."
    )


class Ingress:
    def __init__(self, host, talker, events_source, *, bot, poll_s=1.0, players=None):
        self.host, self.talker, self.source = host, talker, events_source
        self.bot, self.poll_s = bot, poll_s
        self.players = players  # () -> online player names, or None when unknown
        self.cursor: int | None = None
        self.launch_id = None
        self.gaps = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="companion-ingress"
        )

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(5.0)

    def _run(self):
        last_error = None
        while not self._stop.is_set():
            try:
                self.poll_once()
                last_error = None
            except (OSError, ValueError, KeyError) as error:
                text = f"companion-ingress: {type(error).__name__}: {error}"
                if text != last_error:
                    print(text, flush=True)
                last_error = text
            self._stop.wait(self.poll_s)

    # -- polling --------------------------------------------------------------

    def poll_once(self):
        if self.cursor is None:  # start at the head: old commands are not replayed
            head = self.source(HEAD)
            self.cursor = head["latest_sequence"]
            self.launch_id = head.get("launch_id")
            return
        batch = self.source(self.cursor)
        launch = batch.get("launch_id")
        if launch != self.launch_id and None not in (launch, self.launch_id):
            # the server restarted: sequences restart with it
            self.launch_id, self.cursor = launch, 0
            batch = self.source(0)
        if batch.get("gap"):
            self.gaps += 1
            print(
                "companion-ingress: event ring gap, some events were lost", flush=True
            )
        for event in batch.get("events") or []:
            self.cursor = max(self.cursor, event.get("sequence", 0))
            try:
                self.handle(event)
            except Exception as error:  # one bad event must not drop the batch
                print(f"companion-ingress: {type(error).__name__}", flush=True)
        self.cursor = max(self.cursor, batch.get("latest_sequence", 0))

    # -- events ---------------------------------------------------------------

    def handle(self, event):
        data = event.get("data")
        if not isinstance(data, dict):
            return
        kind = event.get("type")
        if kind not in ("player_command", "player_chat"):
            return
        name = data.get("player_name")
        if not isinstance(name, str) or not name:
            return
        source = "command" if kind == "player_command" else "chat"
        actor = Actor(source, name, data.get("player_uuid"), _position(data))
        if kind == "player_chat":
            text = data.get("text")
            if isinstance(text, str) and text:
                self.talker.hear(actor, text)
            return
        self._command(actor, data)

    def _tell(self, actor, text):
        try:
            self.host.call(
                "say",
                {"text": " ".join(text.split())[:SAY_MAX], "to": actor.name},
                TELL_ACTOR,
            )
        except Exception:
            pass

    def _command(self, actor, data):
        sub = data.get("command")
        if sub == "ask":
            text = data.get("text")
            if isinstance(text, str) and text:
                self.talker.hear(actor, text)
            return
        if sub == "status":
            return self._run_intent(actor, "status", {}, self._explain)
        if sub == "stop":
            return self._run_intent(actor, "stop", {}, lambda r, a, i: "Stopped.")
        if sub == "goto":
            coords = [_int(data.get(axis)) for axis in "xyz"]
            if None in coords:
                return self._tell(actor, "Can't do that: goto needs x, y and z.")
            args = dict(zip("xyz", coords, strict=True))
            return self._run_intent(actor, "goto", args, self._started)
        if sub == "gather":
            item, count = data.get("item"), _int(data.get("count"))
            if not isinstance(item, str) or count is None:
                return self._tell(
                    actor, "Can't do that: gather needs an item and a count."
                )
            args = {"item": item, "count": count}
            return self._run_intent(actor, "gather", args, self._started)
        if sub == "build":
            template = data.get("name")
            if not isinstance(template, str):
                return self._tell(actor, "Can't do that: build needs a name.")
            return self._run_intent(
                actor, "build", {"template": template}, self._started
            )
        if sub == "attack":
            return self._attack(actor, data)
        if sub == "ready":  # a match's wait reads it (ArenaClient.wait_ready)
            return None
        if sub == "defense":
            return self._defense(actor, data)
        self._tell(actor, "Unknown command.")

    def _attack(self, actor, data):
        """``/mcb attack <word> [weapon]``: the word is an online player's name first,
        else hostile (the nearest one), else arena (every wave mob of the match the
        bot stands in), else a mob type. The host checks who may."""
        word, weapon = data.get("target"), data.get("weapon")
        if not isinstance(word, str) or not word.strip():
            return self._tell(actor, "Can't do that: attack needs a target.")
        word = word.strip()
        online = self.online_player(word)
        if online is not None:
            args = {"player": online}
        elif word.lower() in HOSTILE_WORDS:
            args = {"nearest_hostile": True}
        elif word.lower() == "arena":  # every wave mob of the match the bot is in
            args = {"arena": True}
        else:
            args = {"mob": word}
        if isinstance(weapon, str) and weapon:
            args["weapon"] = weapon
        return self._run_intent(actor, "attack", args, self._started)

    def online_player(self, word):
        """The online player ``word`` names (case-insensitive), else None."""
        try:
            names = self.players() if self.players is not None else None
        except Exception:
            names = None
        return next((n for n in names or () if n.lower() == word.lower()), None)

    def _defense(self, actor, data):
        args = {}
        for key in ("floor_hearts", "threat_radius"):
            value = data.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return self._tell(actor, f"Can't do that: {key} must be a number.")
            args[key] = value
        return self._run_intent(actor, "defense", args, self._defense_reply)

    @staticmethod
    def _defense_reply(result, args, intent):
        return _defense_text(result.get("defense") or {})

    def _run_intent(self, actor, intent, args, describe):
        try:
            result = self.host.call(intent, args, actor)
        except Exception:
            return self._tell(actor, "Something went wrong.")
        if result.get("ok"):
            self._tell(actor, describe(result.get("result") or {}, args, intent))
        else:
            self._tell(actor, f"Can't do that: {result.get('error') or 'failed'}")

    @staticmethod
    def _explain(result, args, intent):
        text = result.get("explanation")
        return text if isinstance(text, str) and text else "Status is not available."

    @staticmethod
    def _started(result, args, intent):
        shown = _describe(intent, args)
        job = result.get("job")
        job_id = job.get("id") if isinstance(job, dict) else None
        return f"Started job {job_id}: {shown}" if job_id else f"Started: {shown}"
