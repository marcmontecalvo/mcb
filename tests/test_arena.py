"""Arena runtime, Python side: plans, wave presets, the bridge client, the CLI.

See docs/ARENA_RUNTIME.md.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mclab import arena_cli, bridge
from mclab.arena import client as arena

PLAN = {
    "schema": 1,
    "name": "vault",
    "size": [9, 6, 9],
    "fills": [
        {"from": [0, 0, 0], "to": [8, 5, 8], "block": "minecraft:air"},
        {"from": [0, 0, 0], "to": [8, 0, 8], "block": "minecraft:stone"},
    ],
    "markers": {
        "teams": {"red": {"pads": [[1, 1, 1]]}, "blue": {"pads": [[7, 1, 7]]}},
        "mob_spawns": [[4, 1, 4]],
        "spectator": [4, 4, 4],
    },
    "capacity": {"teams": 2, "per_team": 10, "mobs": 15},
}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


# ---- plans and waves ---------------------------------------------------------


def test_load_plan_by_name_and_by_path(tmp_path):
    put(tmp_path / "vault.json", PLAN)
    assert arena.load_plan("vault", root=tmp_path) == PLAN
    other = put(tmp_path / "elsewhere" / "x.json", PLAN)
    assert arena.load_plan(str(other), root=tmp_path) == PLAN


def test_load_plan_says_what_is_missing_or_wrong(tmp_path):
    put(tmp_path / "other.json", PLAN)
    with pytest.raises(arena.ArenaError) as missing:
        arena.load_plan("vault", root=tmp_path)
    assert missing.value.reason == "no_plan" and "other" in missing.value.detail
    (tmp_path / "bad.json").write_text("{nope")
    with pytest.raises(arena.ArenaError) as broken:
        arena.load_plan("bad", root=tmp_path)
    assert broken.value.reason == "bad_plan"
    put(tmp_path / "list.json", [1])
    with pytest.raises(arena.ArenaError) as shape:
        arena.load_plan("list", root=tmp_path)
    assert shape.value.reason == "bad_plan"
    with pytest.raises(arena.ArenaError) as outside:
        arena.load_plan(
            "../etc/passwd", root=tmp_path
        )  # not a name: read as a path, and absent
    assert outside.value.reason == "no_plan"


def test_load_waves_takes_an_object_or_a_bare_list(tmp_path):
    waves = [{"mobs": {"minecraft:zombie": 2}, "delay_ticks": 10}]
    put(tmp_path / "a.json", {"waves": waves})
    put(tmp_path / "b.json", waves)
    assert arena.load_waves("a", root=tmp_path) == waves
    assert arena.load_waves("b", root=tmp_path) == waves
    assert arena.waves_presets(root=tmp_path) == ["a", "b"]
    assert arena.waves_presets(root=tmp_path / "nowhere") == []
    with pytest.raises(arena.ArenaError) as missing:
        arena.load_waves("c", root=tmp_path)
    assert missing.value.reason == "no_waves" and "a, b" in missing.value.detail


@pytest.mark.parametrize(
    "waves, text",
    [
        ([], "at least one"),
        ([{"mobs": {}}], "mobs"),
        ([{"mobs": {"zombie": 1}}], "namespaced"),
        ([{"mobs": {"minecraft:ender_dragon": 1}}], "not allowed"),
        ([{"mobs": {"minecraft:zombie": 0}}], "1..64"),
        ([{"mobs": {"minecraft:zombie": 1.5}}], "1..64"),
        ([{"mobs": {"minecraft:zombie": 65}}], "1..64"),
        ([{"mobs": {"minecraft:zombie": 1}, "delay_ticks": -1}], "delay_ticks"),
        ([{"mobs": {"minecraft:zombie": 1}, "delay_ticks": 72001}], "delay_ticks"),
        ([{"mobs": {"minecraft:zombie": 1}, "extra": 1}], "unknown"),
        (["x"], "object"),
        ([{"mobs": {f"minecraft:m{i}": 64 for i in range(5)}}], "256"),
        ([{"mobs": {"minecraft:zombie": 1}}] * 51, "50"),
    ],
)
def test_check_waves_refuses_what_the_runtime_would(waves, text):
    with pytest.raises(arena.ArenaError) as error:
        arena.check_waves(waves)
    assert error.value.reason == "bad_waves" and text in error.value.detail


def test_shipped_wave_presets_are_valid():
    names = arena.waves_presets()
    assert {"vault-raid", "mixed-10"} <= set(names)
    for name in names:
        waves = arena.load_waves(name)
        assert waves, name
    raid = arena.load_waves("vault-raid")
    kinds = [set(w["mobs"]) for w in raid]
    for mob in ("pillager", "vindicator", "witch", "evoker", "ravager"):
        assert any(f"minecraft:{mob}" in k for k in kinds), mob
    assert (
        max(sum(w["mobs"].values()) for w in raid) <= 15
    )  # the plan example's capacity
    first, last = sum(raid[0]["mobs"].values()), sum(raid[-1]["mobs"].values())
    assert last > first  # escalating
    mixed = arena.load_waves("mixed-10")
    assert sum(sum(w["mobs"].values()) for w in mixed) == 10
    assert len({m for w in mixed for m in w["mobs"]}) >= 4


def test_parse_teams():
    assert arena.parse_teams(["red=Ann,Bo", "blue=Cy"]) == {
        "red": ["Ann", "Bo"],
        "blue": ["Cy"],
    }
    assert arena.parse_teams(None) is None
    for bad in ("red", "=A", "red=", "Red Team=A"):
        with pytest.raises(arena.ArenaError):
            arena.parse_teams([bad])


# ---- the client --------------------------------------------------------------


class FakeTransport:
    """The mod as the client sees it: /events and /admin."""

    def __init__(self, replies=None, latest=5):
        self.events, self.sent, self.latest = [], [], latest
        self.replies = replies or {}
        self.gets = 0

    def get(self, path):
        self.gets += 1
        after = int(path.split("after=")[1])
        return {
            "latest_sequence": self.latest,
            "gap": False,
            "events": [e for e in self.events if e["sequence"] > after],
        }

    def post(self, path, payload):
        assert path == "/admin"
        self.sent.append(payload)
        for kind, data in self.replies.get(payload["op"], [("done", {})]):
            self.latest += 1
            self.events.append(
                {
                    "sequence": self.latest,
                    "type": kind if "_" in kind else f"{payload['op']}_{kind}",
                    "data": {
                        "request_sequence": payload["sequence"],
                        "request_id": payload["request_id"],
                        **data,
                    },
                }
            )
        return {"status": "queued"}


def client(transport, ticks=None):
    clock = iter(range(1000)) if ticks is None else iter(ticks)
    return arena.ArenaClient(
        transport, clock=lambda: next(clock), sleep=lambda _s: None
    )


def test_call_sends_the_envelope_and_returns_the_answer_without_the_echo():
    t = FakeTransport({"arena_list": [("done", {"arenas": []})]})
    assert client(t).call("arena_list") == {"arenas": []}
    sent = t.sent[0]
    assert sent["op"] == "arena_list" and sent["schema_version"] == 1
    assert {"run_id", "episode_id", "body_id", "sequence", "request_id"} <= set(sent)
    assert isinstance(sent["sequence"], int)


def test_sequences_and_request_ids_are_unique_across_calls_and_clients():
    t = FakeTransport()
    c = client(t)
    c.call("arena_list")
    c.call("arena_list")
    other = client(t)
    other.call("arena_list")
    assert len({p["sequence"] for p in t.sent}) == 3
    assert len({p["request_id"] for p in t.sent}) == 3


def test_a_failed_answer_raises_with_the_reason_and_detail():
    t = FakeTransport(
        {
            "arena_start": [
                ("failed", {"reason": "arena_not_ready", "detail": "state is building"})
            ]
        }
    )
    with pytest.raises(arena.ArenaError) as error:
        client(t).call("arena_start", name="vault", mode="waves")
    assert (error.value.reason, error.value.detail) == (
        "arena_not_ready",
        "state is building",
    )
    assert "arena_not_ready" in str(error.value)


def test_events_of_other_requests_are_ignored_and_a_silent_mod_times_out():
    t = FakeTransport({"arena_list": []})
    t.events.append(
        {
            "sequence": 99,
            "type": "arena_list_done",
            "data": {"request_sequence": 1, "request_id": "other"},
        }
    )
    with pytest.raises(arena.ArenaError) as error:
        client(t).call("arena_list", timeout=3)
    assert error.value.reason == "timeout"


def test_only_events_after_the_send_count():
    t = FakeTransport({"arena_list": []}, latest=3)
    t.events.append(
        {
            "sequence": 2,
            "type": "arena_list_done",
            "data": {"request_sequence": 0, "request_id": "stale"},
        }
    )
    with pytest.raises(arena.ArenaError):
        client(t).call("arena_list", timeout=2)


def test_build_start_stop_status_remove_list_send_their_fields():
    t = FakeTransport()
    c = client(t)
    c.build(PLAN, [10, 64, 10], name="v2", dimension="minecraft:the_nether")
    c.build(PLAN, [0, 0, 0])
    c.remove("vault", restore=True)
    c.status("vault")
    c.list()
    c.start(
        "vault",
        "waves",
        waves=[{"mobs": {"minecraft:zombie": 1}}],
        participants=["Ann"],
    )
    c.start("vault", "teams", teams={"red": ["A"], "blue": ["B"]})
    c.stop("vault")
    fields = [{k: v for k, v in p.items() if k not in arena.ENVELOPE} for p in t.sent]
    assert fields[0] == {
        "plan": PLAN,
        "origin": [10, 64, 10],
        "name": "v2",
        "dimension": "minecraft:the_nether",
    }
    assert fields[1] == {"plan": PLAN, "origin": [0, 0, 0]}
    assert fields[2] == {"name": "vault", "restore": True}
    assert fields[3] == fields[7] == {"name": "vault"}
    assert fields[4] == {}
    assert fields[5] == {
        "name": "vault",
        "mode": "waves",
        "waves": [{"mobs": {"minecraft:zombie": 1}}],
        "participants": ["Ann"],
    }
    assert fields[6] == {
        "name": "vault",
        "mode": "teams",
        "teams": {"red": ["A"], "blue": ["B"]},
    }
    assert [p["op"] for p in t.sent] == [
        "arena_build", "arena_build", "arena_remove", "arena_status", "arena_list",
        "arena_start", "arena_start", "arena_stop",
    ]  # fmt: skip


def test_start_checks_waves_before_sending():
    t = FakeTransport()
    with pytest.raises(arena.ArenaError) as error:
        client(t).start("vault", "waves", waves=[{"mobs": {}}])
    assert error.value.reason == "bad_waves" and not t.sent


def test_wait_for_returns_the_arena_event_or_gives_up():
    t = FakeTransport()
    c = client(t)
    c.call("arena_list")
    t.events.append(
        {
            "sequence": 50,
            "type": "arena_build_progress",
            "data": {"name": "vault", "percent": 10},
        }
    )
    t.events.append({"sequence": 51, "type": "arena_built", "data": {"name": "other"}})
    t.events.append(
        {"sequence": 52, "type": "arena_built", "data": {"name": "vault", "placed": 9}}
    )
    seen = []
    event = c.wait_for(
        "vault",
        {"arena_built", "arena_build_aborted"},
        timeout=30,
        on_event=seen.append,
    )
    assert event["type"] == "arena_built" and event["data"]["placed"] == 9
    assert [e["type"] for e in seen] == ["arena_build_progress"]
    with pytest.raises(arena.ArenaError) as error:
        c.wait_for("vault", {"arena_match_won"}, timeout=2)
    assert error.value.reason == "timeout"


def test_wait_for_surfaces_an_aborted_build_as_the_event():
    t = FakeTransport()
    c = client(t)
    c.call("arena_list")
    t.events.append(
        {
            "sequence": 60,
            "type": "arena_build_aborted",
            "data": {"name": "vault", "reason": "chunks_not_loaded"},
        }
    )
    event = c.wait_for("vault", {"arena_built", "arena_build_aborted"}, timeout=5)
    assert event["data"]["reason"] == "chunks_not_loaded"


# ---- the real HTTP transport against a local server ---------------------------


class Handler(BaseHTTPRequestHandler):
    log = []

    def do_GET(self):  # noqa: N802
        self.log.append(("GET", self.path, self.headers.get("Authorization")))
        self.reply(200, {"latest_sequence": 7, "gap": False, "events": []})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.log.append(("POST", self.path, self.headers.get("Authorization"), body))
        self.reply(202 if body.get("op") != "boom" else 413, {"status": "queued"})

    def reply(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


def test_http_transport_sends_the_bearer_token_and_maps_errors():
    Handler.log = []
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        transport = arena.HttpTransport(server.server_port, "s" * 32)
        assert transport.get("/events?after=0")["latest_sequence"] == 7
        assert transport.post("/admin", {"op": "arena_list"}) == {"status": "queued"}
        assert all(entry[2] == "Bearer " + "s" * 32 for entry in Handler.log)
        with pytest.raises(arena.ArenaError) as too_big:
            transport.post("/admin", {"op": "boom"})
        assert too_big.value.reason == "http_413"
    finally:
        server.shutdown()
    with pytest.raises(arena.ArenaError) as down:
        arena.HttpTransport(server.server_port, "s" * 32, timeout=0.2).get(
            "/events?after=0"
        )
    assert down.value.reason == "unreachable"


def test_from_state_reads_the_token_and_the_configured_port(tmp_path):
    state = put(tmp_path / "state.json", {"token": "t" * 32, "launch_id": "L"})
    made = arena.ArenaClient.from_state(
        state_file=state, settings={"bridge_port": 4321}
    )
    assert (made.transport.port, made.transport.token) == (4321, "t" * 32)
    with pytest.raises(arena.ArenaError) as down:
        arena.ArenaClient.from_state(
            state_file=tmp_path / "none.json", settings={"bridge_port": 1}
        )
    assert down.value.reason == "server_not_running"


# ---- the CLI -----------------------------------------------------------------


class Recorder:
    """Stands in for ArenaClient: records calls, answers from ``answers``."""

    def __init__(self, answers=None, fail=None):
        self.calls, self.answers, self.fail = [], answers or {}, fail

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.fail:
                raise arena.ArenaError(*self.fail)
            return self.answers.get(name, {"ok_for": name})

        return call


def run(argv, recorder, tmp_path, capsys):
    code = arena_cli.main(argv, client=recorder, arenas=tmp_path)
    return code, json.loads(capsys.readouterr().out)


def test_cli_build_reads_the_plan_and_the_origin(tmp_path, capsys):
    put(tmp_path / "vault.json", PLAN)
    rec = Recorder()
    code, out = run(["build", "vault", "--at", "10", "64", "-5"], rec, tmp_path, capsys)
    assert code == 0 and out["ok"] is True
    assert rec.calls == [
        ("build", (PLAN, [10, 64, -5]), {"name": None, "dimension": None})
    ]
    run(
        [
            "build",
            "vault",
            "--at",
            "1",
            "2",
            "3",
            "--as",
            "v2",
            "--dimension",
            "minecraft:the_end",
        ],
        rec,
        tmp_path,
        capsys,
    )
    assert rec.calls[-1] == (
        "build",
        (PLAN, [1, 2, 3]),
        {"name": "v2", "dimension": "minecraft:the_end"},
    )
    other = put(tmp_path / "x" / "p.json", PLAN)
    run(
        ["build", "ignored", "--plan", str(other), "--at", "0", "0", "0"],
        rec,
        tmp_path,
        capsys,
    )
    assert rec.calls[-1][1][0] == PLAN


def test_cli_build_wait_follows_the_job_to_the_end(tmp_path, capsys):
    put(tmp_path / "vault.json", PLAN)
    rec = Recorder(
        {"wait_for": {"type": "arena_built", "data": {"name": "vault", "placed": 5}}}
    )
    code, out = run(
        ["build", "vault", "--at", "0", "0", "0", "--wait"], rec, tmp_path, capsys
    )
    assert code == 0 and out["built"]["placed"] == 5
    assert [c[0] for c in rec.calls] == ["build", "wait_for"]
    rec = Recorder(
        {
            "wait_for": {
                "type": "arena_build_aborted",
                "data": {"name": "vault", "reason": "chunks_not_loaded"},
            }
        }
    )
    code, out = run(
        ["build", "vault", "--at", "0", "0", "0", "--wait"], rec, tmp_path, capsys
    )
    assert code == 1 and out["reason"] == "chunks_not_loaded"


def test_cli_start_waves_loads_the_preset_and_players(tmp_path, capsys):
    put(
        tmp_path / "waves" / "tiny.json", {"waves": [{"mobs": {"minecraft:zombie": 1}}]}
    )
    rec = Recorder()
    code, _ = run(
        ["start", "vault", "--waves", "tiny", "--players", "Ann", "Bo"],
        rec,
        tmp_path,
        capsys,
    )
    assert code == 0
    assert rec.calls[0] == (
        "start",
        ("vault", "waves"),
        {
            "waves": [{"mobs": {"minecraft:zombie": 1}}],
            "participants": ["Ann", "Bo"],
            "teams": None,
        },
    )
    code, out = run(
        ["start", "vault"], rec, tmp_path, capsys
    )  # waves mode with no waves named
    assert code == 1 and out["reason"] == "bad_request" and "tiny" in out["detail"]
    code, out = run(
        ["start", "vault", "--waves", "tiny", "--team", "red=A"], rec, tmp_path, capsys
    )
    assert code == 1 and out["reason"] == "bad_request"
    code, out = run(
        ["start", "vault", "--mode", "teams", "--waves", "tiny"], rec, tmp_path, capsys
    )
    assert code == 1 and out["reason"] == "bad_request"


def test_cli_start_teams(tmp_path, capsys):
    rec = Recorder()
    code, _ = run(
        ["start", "vault", "--mode", "teams", "--team", "red=A,B", "--team", "blue=C"],
        rec,
        tmp_path,
        capsys,
    )
    assert code == 0
    assert rec.calls[0] == (
        "start",
        ("vault", "teams"),
        {
            "waves": None,
            "participants": None,
            "teams": {"red": ["A", "B"], "blue": ["C"]},
        },
    )
    run(
        ["start", "vault", "--mode", "teams", "--players", "A", "B"],
        rec,
        tmp_path,
        capsys,
    )
    assert rec.calls[-1][2]["participants"] == ["A", "B"]


def test_cli_simple_ops(tmp_path, capsys):
    rec = Recorder()
    for argv, call in [
        (["stop", "vault"], ("stop", ("vault",), {})),
        (["status", "vault"], ("status", ("vault",), {})),
        (["list"], ("list", (), {})),
        (["remove", "vault"], ("remove", ("vault",), {"restore": False})),
        (["remove", "vault", "--restore"], ("remove", ("vault",), {"restore": True})),
    ]:
        code, out = run(argv, rec, tmp_path, capsys)
        assert code == 0 and out["ok"] is True
        assert rec.calls[-1] == call


def test_cli_reports_failures_as_json_and_exit_one(tmp_path, capsys):
    rec = Recorder(fail=("arena_not_ready", "state is building"))
    code, out = run(["status", "vault"], rec, tmp_path, capsys)
    assert code == 1 and out == {
        "ok": False,
        "reason": "arena_not_ready",
        "detail": "state is building",
    }
    put(tmp_path / "waves" / "bad.json", [{"mobs": {}}])
    code, out = run(["start", "vault", "--waves", "bad"], Recorder(), tmp_path, capsys)
    assert code == 1 and out["reason"] == "bad_waves"
    code, out = run(
        ["build", "missing", "--at", "0", "0", "0"], Recorder(), tmp_path, capsys
    )
    assert code == 1 and out["reason"] == "no_plan"


def test_cli_status_prints_the_answer(tmp_path, capsys):
    rec = Recorder({"status": {"name": "vault", "state": "built"}})
    code, out = run(["status", "vault"], rec, tmp_path, capsys)
    assert code == 0 and out == {"ok": True, "name": "vault", "state": "built"}


def test_cli_rejects_a_bad_arena_name(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        arena_cli.main(["status", "Bad Name"], client=Recorder(), arenas=tmp_path)
    assert error.value.code == 2


def test_cli_builds_the_real_client_when_none_is_given(monkeypatch, tmp_path, capsys):
    made = Recorder()
    monkeypatch.setattr(
        arena.ArenaClient, "from_state", classmethod(lambda cls, **_: made)
    )
    assert arena_cli.main(["list"]) == 0
    assert made.calls == [("list", (), {})]
    capsys.readouterr()

    def down(cls, **_):
        raise arena.ArenaError("server_not_running", "start the server")

    monkeypatch.setattr(arena.ArenaClient, "from_state", classmethod(down))
    assert arena_cli.main(["list"]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "server_not_running"


def test_mclab_cli_routes_arena_to_the_arena_cli(monkeypatch):
    from mclab import cli

    seen = []
    monkeypatch.setattr(arena_cli, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr("sys.argv", ["mcplay", "arena", "list"])
    assert cli.main() == 0
    assert seen == [["list"]]


def test_arena_plan_writes_offline_through_the_same_cli(tmp_path, capsys):
    # no client is made: planning needs no server
    argv = ["plan", "duel", "--out", str(tmp_path), "--no-previews"]
    assert arena_cli.main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["arenas"][0]["problems"] == []
    assert (tmp_path / "duel.json").exists()
    # the written plan is what `arena build` loads
    assert arena.load_plan("duel", root=tmp_path)["name"] == "duel"


# ---- the bridge knows the ops --------------------------------------------------


def test_the_bridge_routes_arena_ops_to_the_body_mod():
    assert set(arena.OPS) <= bridge.BODY_OPS
    assert arena.OPS == (
        "arena_build",
        "arena_remove",
        "arena_list",
        "arena_status",
        "arena_start",
        "arena_stop",
        "arena_spawn",
        "arena_op",
        "arena_kit",
    )


def test_names_match_the_runtime_rule():
    assert re.fullmatch(arena.NAME.pattern, "vault-2_a")
    assert not arena.NAME.fullmatch("Vault")
    assert not arena.NAME.fullmatch("a" * 33)


def test_the_world_ops_and_a_match_with_an_exit_send_their_fields():
    t = FakeTransport()
    c = client(t)
    c.start(
        "hall", "race", waves=[{"mobs": {"minecraft:zombie": 2}}],
        teams={"red": ["Ann"], "blue": ["Bo"]}, exit=(0, 102, 0),
    )  # fmt: skip
    c.start("hall", "teams", teams={"red": ["Ann"], "blue": ["Bo"]}, waves=[])
    c.spawn((0, 102, 0))
    c.op("Ann")
    c.op("Bo", op=False)
    kit = [{"item": "minecraft:iron_sword", "count": 1}]
    c.kit(["Ann", "Bo"], kit)
    fields = [{k: v for k, v in p.items() if k not in arena.ENVELOPE} for p in t.sent]
    assert fields[0] == {
        "name": "hall", "mode": "race", "waves": [{"mobs": {"minecraft:zombie": 2}}],
        "teams": {"red": ["Ann"], "blue": ["Bo"]}, "exit": [0, 102, 0],
    }  # fmt: skip
    assert fields[1] == {
        "name": "hall", "mode": "teams", "teams": {"red": ["Ann"], "blue": ["Bo"]}
    }, "no waves: a team match without mobs"
    assert fields[2] == {"position": [0, 102, 0]}
    assert fields[3] == {"player": "Ann", "operator": True}
    assert fields[4] == {"player": "Bo", "operator": False}
    assert fields[5] == {
        "players": ["Ann", "Bo"],
        "kit": {"give": kit, "clear_inventory": True, "heal": True, "wear": True},
    }
    assert [p["op"] for p in t.sent][2:] == [
        "arena_spawn", "arena_op", "arena_op", "arena_kit",
    ]  # fmt: skip


def test_a_kit_can_keep_what_each_player_carries():
    t = FakeTransport()
    client(t).kit(["Ann"], None)
    (sent,) = t.sent
    assert sent["kit"] == {"heal": True}, "no kit: healed, inventory untouched"
