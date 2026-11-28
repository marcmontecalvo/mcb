"""TDD suite for Delivery Items 2 & 3: Private In-Game Game Lobby + Authoritative Shared Revision."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mclab.arena import designs, world
from mclab.arena_lobby import (
    ArenaLobby,
    LobbyError,
    LobbyFrozenError,
    PermissionError_,
    StaleRevisionError,
)
from mclab.companion_names import BotNames

ROOT = Path(__file__).resolve().parents[1]


class FakeFleet:
    """In-memory companion fleet stub for deterministic bot provisioning tests."""

    def __init__(self, *, fail_ids: set[str] | None = None, instant: bool = True):
        self.fail_ids = set(fail_ids or ())
        self.instant = instant
        self.running: dict[str, dict] = {}  # bot_id -> {id, name, session}
        self.added: list[str] = []
        self.removed: list[str] = []
        self.restarted: list[str] = []

    def bots(self) -> list[dict]:
        return list(self.running.values())

    def add_bot(self, bot_id: str, name: str) -> dict:
        self.added.append(bot_id)
        if bot_id in self.fail_ids:
            return {"ok": False, "error": f"spawn failed for {bot_id}"}
        session = "ready" if self.instant else "starting"
        self.running[bot_id] = {"id": bot_id, "name": name, "session": session}
        return {"ok": True, "bot": {"id": bot_id, "name": name}}

    def remove_bot(self, bot_id: str) -> dict:
        self.removed.append(bot_id)
        self.running.pop(bot_id, None)
        return {"ok": True}

    def restart_bot(self, bot_id: str, new_name: str) -> dict:
        self.restarted.append(bot_id)
        if bot_id in self.fail_ids:
            return {"ok": False, "error": f"restart failed for {bot_id}"}
        if bot_id in self.running:
            self.running[bot_id]["name"] = new_name
            self.running[bot_id]["session"] = "ready" if self.instant else "starting"
        return {"ok": True, "bot": {"id": bot_id, "name": new_name}}

    def mark_ready(self, bot_id: str) -> None:
        if bot_id in self.running:
            self.running[bot_id]["session"] = "ready"


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def make_lobby(
    tmp_path: Path,
    *,
    fleet: FakeFleet | None = None,
    clock: FakeClock | None = None,
    countdown_seconds: float = 5.0,
    on_launch=None,
) -> tuple[ArenaLobby, FakeFleet, FakeClock]:
    fleet = fleet if fleet is not None else FakeFleet()
    clock = clock if clock is not None else FakeClock()
    lobby = ArenaLobby(
        state_path=tmp_path / "arena-lobby.json",
        runtime_dir=tmp_path,
        fleet=fleet,
        clock=clock,
        countdown_seconds=countdown_seconds,
        on_launch=on_launch,
    )
    return lobby, fleet, clock


# --- 1. Maps, capacities, compatible modes & fresh PvP defaults ----------------


def test_maps_and_capacities_derived_from_plans(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    snap = lobby.snapshot()
    maps = snap["maps"]
    assert "lobby" not in maps
    assert maps["crossroads"]["teams"] == ["red", "blue", "green", "yellow"]
    assert maps["crossroads"]["per_team"] == 10
    assert maps["crossroads"]["compatible_modes"] == ["teams", "waves", "race"]

    assert maps["duel"]["teams"] == ["red", "blue"]
    assert maps["duel"]["per_team"] == 2
    assert maps["hall"]["teams"] == ["red", "blue"]
    assert maps["hall"]["per_team"] == 10
    assert maps["crash_site"]["teams"] == ["red", "blue"]
    assert maps["crash_site"]["per_team"] == 4

    assert maps["vault"]["teams"] == ["red"]
    assert maps["vault"]["per_team"] == 4
    assert maps["vault"]["compatible_modes"] == ["waves"]


def test_fresh_pvp_defaults_mob_free_and_elimination_respawns(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    snap = lobby.snapshot()
    assert snap["map"] == "crossroads"
    assert snap["mode"] == "teams"
    assert snap["waves"] == []
    assert snap["waves_preset"] is None
    assert snap["hazards_enabled"] is False
    assert "no mob" in snap["hazards_summary"].lower()
    assert snap["respawn_mode"] == "elimination"
    assert snap["respawn_count"] == 0
    assert snap["allow_uneven"] is False
    assert snap["revision"] == 1


def test_vault_rejects_pvp_and_switching_to_vault_selects_waves(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.set_map("Alice", "vault")
    snap = lobby.snapshot()
    assert snap["map"] == "vault"
    assert snap["mode"] == "waves"
    assert snap["waves_preset"] is not None or snap["waves"]

    with pytest.raises(LobbyError, match="vault.*waves"):
        lobby.set_mode("Alice", "teams")
    with pytest.raises(LobbyError, match="vault.*waves"):
        lobby.set_mode("Alice", "race")


def test_capacity_overflow_reports_exact_limit_exceeded(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.set_map("Alice", "duel")  # 2 teams x 2 per_team, 6 mobs

    with pytest.raises(LobbyError, match="green.*duel.*red, blue"):
        lobby.join("Bob", "green")

    lobby.join("Bob", "red")  # 2/2 on red
    with pytest.raises(LobbyError, match="red.*2/2|full.*duel.*2"):
        lobby.join("Cara", "red")

    with pytest.raises(LobbyError, match="6.*mobs"):
        lobby.add_mob_group("Alice", "blue", "minecraft:zombie", 7)

    # Switch to vault (1 team x 4) and check total/team capacity
    lobby.set_map("Alice", "vault")
    lobby.join("Cara", "red")
    lobby.join("Dan", "red")
    with pytest.raises(LobbyError, match="4"):
        lobby.join("Eve", "red")


# --- 2. Seats, self-control, host permissions & host-loss recovery ------------


def test_first_human_becomes_host_and_permissions_enforced(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    assert lobby.snapshot()["host"] == "Alice"

    # Bob controls his own seat freely (create green team, switch, spectate)
    lobby.join("Bob", "green")
    assert "Bob" in lobby.snapshot()["teams"]["green"]["humans"]
    lobby.watch("Bob")
    assert "Bob" in lobby.snapshot()["spectators"]
    lobby.join("Bob", "blue")

    # Bob cannot perform host-only actions
    for fn in (
        lambda: lobby.set_map("Bob", "hall"),
        lambda: lobby.set_mode("Bob", "race"),
        lambda: lobby.set_waves("Bob", preset="mixed-10"),
        lambda: lobby.set_kit("Bob", "sword+armor"),
        lambda: lobby.set_respawns("Bob", "player", 2),
        lambda: lobby.set_allow_uneven("Bob", True),
        lambda: lobby.add_bot("Bob", "blue"),
        lambda: lobby.add_mob_group("Bob", "blue", "minecraft:zombie", 2),
        lambda: lobby.transfer_host("Bob", "Bob"),
    ):
        with pytest.raises(PermissionError_, match="host"):
            fn()

    # Host can transfer host to Bob, then Bob can change rules and Alice cannot
    lobby.transfer_host("Alice", "Bob")
    assert lobby.snapshot()["host"] == "Bob"
    lobby.set_map("Bob", "hall")
    assert lobby.snapshot()["map"] == "hall"
    with pytest.raises(PermissionError_, match="host"):
        lobby.set_map("Alice", "duel")


def test_host_loss_promotes_next_human_and_resets_when_empty(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    lobby.watch("Spec")

    lobby.leave("Alice")
    snap = lobby.snapshot()
    assert snap["host"] == "Bob"
    assert "Bob" in snap["last_change_reason"] or "Alice" in snap["last_change_reason"]

    lobby.leave("Bob")
    assert lobby.snapshot()["host"] == "Spec"

    lobby.leave("Spec")
    snap = lobby.snapshot()
    assert snap["host"] is None
    assert snap["phase"] == "open"


# --- 3. Bot provisioning, independent status tracking & failure surfacing ------


def test_bot_provisioning_tracks_requested_vs_connected_and_failures(tmp_path):
    fleet = FakeFleet(fail_ids={"mcbot2"}, instant=False)
    lobby, _, _ = make_lobby(tmp_path, fleet=fleet)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")

    bot1 = lobby.add_bot("Alice", "red")
    assert bot1["bot_id"] == "default"
    assert bot1["name"] == "MCBot"
    assert bot1["status"] == "connecting"

    # Second bot fails provisioning — tracked independently as failed!
    bot2 = lobby.add_bot("Alice", "blue")
    assert bot2["bot_id"] == "mcbot2"
    assert bot2["status"] == "failed"
    assert "spawn failed" in (bot2["error"] or "")

    snap = lobby.snapshot()
    assert snap["bots_ready"] is False
    assert any("MCBot" in r for r in snap["blocking_reasons"])
    assert any(
        "MCBot2" in r and "failed" in r.lower() for r in snap["blocking_reasons"]
    )

    # Both humans ready up, but countdown does NOT start while bots are connecting/failed
    rev = snap["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    assert lobby.snapshot()["phase"] == "open"

    # Fix mcbot2 and retry, mark both connected -> countdown starts once ready
    fleet.fail_ids.clear()
    lobby.retry_bot("Alice", "mcbot2")
    fleet.mark_ready("default")
    fleet.mark_ready("mcbot2")
    lobby.sync_bots()
    snap = lobby.snapshot()
    assert snap["bots_ready"] is True
    assert all(b["status"] == "connected" for b in snap["bots"].values())


def test_bot_rename_move_remove_uses_bot_names_and_invalidates_revision(tmp_path):
    lobby, fleet, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    lobby.add_bot("Alice", "red", name="Tank")
    names = BotNames.load(tmp_path)
    assert names.name_for("default") == "Tank"

    rev1 = lobby.snapshot()["revision"]
    lobby.ready("Alice", seen_revision=rev1)
    assert "alice" in lobby.snapshot()["ready_revisions"]

    # Rename bot -> bumps revision and clears readiness
    lobby.rename_bot("Alice", "Tank", "Shield")
    snap = lobby.snapshot()
    assert snap["revision"] == rev1 + 1
    assert snap["ready_revisions"] == {}
    assert BotNames.load(tmp_path).name_for("default") == "Shield"
    assert "default" in fleet.restarted

    # Move bot -> bumps revision
    rev2 = snap["revision"]
    lobby.move_bot("Alice", "Shield", "blue")
    assert lobby.snapshot()["revision"] == rev2 + 1
    assert lobby.snapshot()["bots"]["default"]["team"] == "blue"

    # Remove bot -> bumps revision
    rev3 = lobby.snapshot()["revision"]
    lobby.remove_bot("Alice", "Shield")
    assert lobby.snapshot()["revision"] == rev3 + 1
    assert "default" not in lobby.snapshot()["bots"]


# --- 4. Revision invariant, spectator isolation & stale readiness --------------


def test_material_edits_invalidate_readiness_with_reason_and_spectators_do_not(
    tmp_path,
):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    rev = lobby.snapshot()["revision"]
    lobby.ready("Alice", seen_revision=rev)

    # Spectator joining and leaving does NOT bump revision or clear readiness
    lobby.watch("Watcher")
    assert lobby.snapshot()["revision"] == rev
    assert lobby.snapshot()["ready_revisions"] == {"alice": rev}
    with pytest.raises(LobbyError, match="[Ss]pectator"):
        lobby.ready("Watcher", seen_revision=rev)
    lobby.leave("Watcher")
    assert lobby.snapshot()["revision"] == rev
    assert lobby.snapshot()["ready_revisions"] == {"alice": rev}

    # Material edit invalidates readiness with a specific reason
    lobby.add_bot("Alice", "blue")
    snap = lobby.snapshot()
    assert snap["revision"] == rev + 1
    assert snap["ready_revisions"] == {}
    assert "MCBot" in snap["last_change_reason"]
    assert "ready" in snap["last_change_reason"].lower()

    # Stale ready click is rejected
    with pytest.raises(StaleRevisionError) as exc:
        lobby.ready("Bob", seen_revision=rev)
    assert (
        str(snap["revision"]) in str(exc.value) or "changed" in str(exc.value).lower()
    )

    # Every other material edit also bumps revision & clears readiness
    for edit in (
        lambda: lobby.set_kit("Alice", "sword+bow+armor"),
        lambda: lobby.set_respawns("Alice", "team", 2),
        lambda: lobby.set_allow_uneven("Alice", True),
        lambda: lobby.set_waves("Alice", preset="mixed-10"),
        lambda: lobby.set_waves("Alice", waves=[]),
        lambda: lobby.set_mode("Alice", "race"),
        lambda: lobby.set_mode("Alice", "teams"),
        lambda: lobby.set_map("Alice", "hall"),
        lambda: lobby.add_mob_group("Alice", "red", "minecraft:skeleton", 2),
    ):
        before = lobby.snapshot()["revision"]
        lobby.ready("Alice", seen_revision=before)
        edit()
        after = lobby.snapshot()
        assert after["revision"] == before + 1
        assert after["ready_revisions"] == {}
        assert after["last_change_reason"]


# --- 5. Balanced default & intentional 10v1 uneven challenge -------------------


def test_balanced_default_and_intentional_10v1_uneven_challenge(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.set_map("Alice", "hall")  # 2 teams x 10
    lobby.join("Bob", "blue")
    lobby.add_bot("Alice", "red")  # 2v1

    snap = lobby.snapshot()
    assert snap["allow_uneven"] is False
    assert any("uneven" in err.lower() for err in snap["validation_errors"])

    # Readying up while uneven does NOT start countdown
    rev = snap["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    assert lobby.snapshot()["phase"] == "open"

    # Host enables uneven challenge and stacks 10v1 on hall (Alice + 9 bots vs Bob)
    lobby.set_allow_uneven("Alice", True)
    for _ in range(8):
        lobby.add_bot("Alice", "red")
    snap = lobby.snapshot()
    assert len(snap["teams"]["red"]["humans"]) + len(snap["teams"]["red"]["bots"]) == 10
    assert len(snap["teams"]["blue"]["humans"]) == 1
    assert snap["validation_errors"] == []

    # 11th on red still hits exact per-team capacity (10)
    with pytest.raises(LobbyError, match="10"):
        lobby.add_bot("Alice", "red")

    rev = snap["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    assert lobby.snapshot()["phase"] == "countdown"


# --- 6. Atomic countdown, freeze, reopen, unready cancel, late arrivals, recap & rematch


def test_atomic_countdown_freeze_reopen_and_unready_cancel(tmp_path):
    launched = []
    lobby, _, clock = make_lobby(tmp_path, on_launch=launched.append)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    rev = lobby.snapshot()["revision"]

    lobby.ready("Alice", seen_revision=rev)
    assert lobby.snapshot()["phase"] == "open"
    lobby.ready("Bob", seen_revision=rev)
    snap = lobby.snapshot()
    assert snap["phase"] == "countdown"
    assert snap["countdown_revision"] == rev

    # All mutations frozen during countdown
    with pytest.raises(LobbyFrozenError):
        lobby.set_map("Alice", "duel")
    with pytest.raises(LobbyFrozenError):
        lobby.add_bot("Alice", "red")
    with pytest.raises(LobbyFrozenError):
        lobby.join("Cara", "green")

    # Late arrival CAN watch and queue for next round without cancelling countdown
    lobby.watch("Late", queue_next=True)
    assert lobby.snapshot()["phase"] == "countdown"
    assert "Late" in lobby.snapshot()["spectators"]
    assert "Late" in lobby.snapshot()["queued_next"]
    assert lobby.snapshot()["revision"] == rev

    # Host reopen cancels countdown, bumps revision, clears readiness
    with pytest.raises(PermissionError_):
        lobby.reopen("Bob")
    lobby.reopen("Alice")
    snap = lobby.snapshot()
    assert snap["phase"] == "open"
    assert snap["revision"] == rev + 1
    assert snap["ready_revisions"] == {}

    # Re-ready -> countdown -> Bob unreadies -> cancels countdown
    rev2 = snap["revision"]
    lobby.ready("Alice", seen_revision=rev2)
    lobby.ready("Bob", seen_revision=rev2)
    assert lobby.snapshot()["phase"] == "countdown"
    lobby.unready("Bob")
    assert lobby.snapshot()["phase"] == "open"

    # Re-ready -> countdown -> tick past countdown -> atomic launch!
    lobby.ready("Bob", seen_revision=rev2)
    assert lobby.snapshot()["phase"] == "countdown"
    clock.advance(4.9)
    assert lobby.tick() is None
    assert lobby.snapshot()["phase"] == "countdown"
    clock.advance(0.2)
    cfg = lobby.tick()
    assert cfg is not None
    assert len(launched) == 1
    assert cfg["map"] == "crossroads"
    assert cfg["mode"] == "teams"
    assert cfg["waves"] == []
    assert cfg["respawn_mode"] == "elimination"
    assert cfg["respawn_count"] == 0
    assert cfg["revision"] == rev2
    assert lobby.snapshot()["phase"] == "running"

    # Another late arrival during running match can watch & queue
    lobby.watch("Late2", queue_next=True)
    assert "Late2" in lobby.snapshot()["queued_next"]

    # Match completes -> recap, queued players folded in, fresh ready check for Play Again
    lobby.complete_match(
        {
            "winner": "red",
            "outcome": "won",
            "duration_s": 42.5,
            "summary": "Red wins!",
        }
    )
    recap_snap = lobby.snapshot()
    assert recap_snap["phase"] == "recap"
    assert recap_snap["last_recap"]["winner"] == "red"
    assert recap_snap["ready_revisions"] == {}
    assert recap_snap["queued_next"] == []
    # Queued late arrivals were folded into active teams
    seated = {h for team in recap_snap["teams"].values() for h in team["humans"]}
    assert {"Alice", "Bob", "Late", "Late2"} <= seated

    # Play Again transitions to open with kept lineup and marks caller ready
    lobby.play_again("Alice")
    again = lobby.snapshot()
    assert again["phase"] == "open"
    assert again["ready_revisions"].get("alice") == again["revision"]
    assert "bob" not in again["ready_revisions"]

    # Change Teams moves back to open without forcing readiness
    lobby.complete_match({"winner": "blue", "outcome": "won"})
    lobby.change_teams("Bob")
    assert lobby.snapshot()["phase"] == "open"
    assert lobby.snapshot()["ready_revisions"] == {}


# --- 7. Persistence, restart recovery & shared revision across processes -------


def test_persistence_restart_recovery_and_shared_state_across_instances(tmp_path):
    path = tmp_path / "arena-lobby.json"
    fleet = FakeFleet()
    clock = FakeClock()

    # Instance A (e.g. in-game companion service)
    lobby_a = ArenaLobby(
        state_path=path, runtime_dir=tmp_path, fleet=fleet, clock=clock
    )
    lobby_a.join("Alice", "red")
    lobby_a.join("Bob", "blue")
    lobby_a.add_bot("Alice", "green")
    rev_a = lobby_a.snapshot()["revision"]

    # Instance B (e.g. dashboard process) reads exact same authoritative revision
    lobby_b = ArenaLobby(
        state_path=path, runtime_dir=tmp_path, fleet=fleet, clock=clock
    )
    assert lobby_b.snapshot()["revision"] == rev_a
    assert lobby_b.snapshot()["host"] == "Alice"

    # Dashboard edits as host -> in-game instance immediately sees new revision on reload
    lobby_b.set_respawns("Alice", "player", 2)
    assert lobby_a.snapshot()["revision"] == rev_a + 1
    assert lobby_a.snapshot()["respawn_mode"] == "player"
    assert lobby_a.snapshot()["respawn_count"] == 2

    # Start countdown, then simulate restart -> recovers cleanly to open with cleared readiness
    lobby_a.remove_bot("Alice", "default")
    rev = lobby_a.snapshot()["revision"]
    lobby_a.ready("Alice", seen_revision=rev)
    lobby_a.ready("Bob", seen_revision=rev)
    assert lobby_a.snapshot()["phase"] == "countdown"

    recovered = ArenaLobby.load(
        state_path=path, runtime_dir=tmp_path, fleet=fleet, clock=clock
    )
    snap = recovered.snapshot()
    assert snap["phase"] == "open"
    assert snap["ready_revisions"] == {}
    assert (
        "recover" in snap["last_change_reason"].lower()
        or "restart" in snap["last_change_reason"].lower()
    )


def test_automated_lab_match_runs_with_zero_humans(tmp_path):
    launched = []
    lobby, _, _ = make_lobby(tmp_path, on_launch=launched.append)
    lobby.add_bot("lab", "red", operator=True)
    lobby.add_bot("lab", "blue", operator=True)
    assert lobby.snapshot()["host"] is None
    assert lobby.snapshot()["validation_errors"] == []

    cfg = lobby.launch_automated()
    assert len(launched) == 1
    assert cfg["teams"]["red"] == ["MCBot"]
    assert cfg["teams"]["blue"] == ["MCBot2"]
    assert lobby.snapshot()["phase"] == "running"


def test_board_rows_fit_sidebar_and_status_text_is_readable(tmp_path):
    lobby, _, _ = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    lobby.join("Cara", "green")
    lobby.add_bot("Alice", "yellow")
    title, rows = lobby.board_rows()
    assert 1 <= len(title) <= 64
    assert 1 <= len(rows) <= 15
    assert all(len(r["text"]) <= 40 for r in rows)
    text = lobby.status_text()
    assert "crossroads" in text.lower()
    assert "Alice" in text and "Bob" in text


# --- 8. Physical lobby design & Java station parity ----------------------------


# The station table and its Java twin are tested in test_arena_lobby_stations.py.


def test_java_mcb_command_exposes_lobby_subcommands_without_op():
    java_path = ROOT / "mcb-body/src/main/java/com/vulpine/mcb/body/McbCommand.java"
    src = java_path.read_text(encoding="utf-8")
    assert 'Commands.literal("lobby")' in src
    for sub in (
        "status",
        "play",
        "watch",
        "rematch",
        "join",
        "spectate",
        "leave",
        "ready",
        "unready",
        "reopen",
        "map",
        "mode",
        "uneven",
        "respawns",
        "host",
        "bot",
    ):
        assert f'"{sub}"' in src
    assert "requires(" not in src  # zero operator rights required


# --- 9. Python ingress, companion_service & dashboard HTTP API -----------------


def test_ingress_handles_lobby_player_commands_with_dashboard_closed(tmp_path):
    from mclab.companion_ingress import Ingress
    from mclab.companion_intents import Actor

    lobby, _, _ = make_lobby(tmp_path)
    tells = []

    class FakeHost:
        def call(self, intent, args, actor, *, key=None, bot=None):
            if intent == "say":
                tells.append((args.get("to"), args.get("text")))
            return {"ok": True, "result": {}}

    class FakeTalker:
        def hear(self, actor, text):
            pass

    ingress = Ingress(
        FakeHost(),
        FakeTalker(),
        lambda after: {"latest_sequence": 0, "events": []},
        lobby=lobby,
    )
    alice = Actor("command", "Alice", "u-alice", None)
    bob = Actor("command", "Bob", "u-bob", None)

    ingress._command(alice, {"command": "lobby", "action": "join", "team": "red"})
    ingress._command(bob, {"command": "lobby", "action": "join", "team": "blue"})
    ingress._command(alice, {"command": "lobby", "action": "bot_add", "team": "green"})
    ingress._command(alice, {"command": "lobby", "action": "status"})

    snap = lobby.snapshot()
    assert snap["host"] == "Alice"
    assert "Alice" in snap["teams"]["red"]["humans"]
    assert "Bob" in snap["teams"]["blue"]["humans"]
    assert len(snap["teams"]["green"]["bots"]) == 1
    assert any("crossroads" in text.lower() for _, text in tells)


def test_companion_service_handles_lobby_command(tmp_path, monkeypatch):
    from mclab import companion_service as svc

    lobby, _, _ = make_lobby(tmp_path)
    config = tmp_path / "companion.json"
    config.write_text(json.dumps({"owners": [], "allowlist": []}))
    monkeypatch.setattr(svc, "CONFIG", config)
    monkeypatch.setattr(svc, "_lobby", lambda: lobby)

    ev = {
        "sequence": 1,
        "type": "player_command",
        "data": {
            "player_uuid": "u-1",
            "player_name": "Alice",
            "command": "lobby",
            "action": "join",
            "team": "red",
        },
    }
    reply = svc.handle(ev)
    assert "red" in reply.lower()
    assert lobby.snapshot()["host"] == "Alice"


def test_dashboard_lobby_api_shares_authoritative_revision(tmp_path, monkeypatch):
    from mclab import arena_lobby
    from mclab import dashboard as ui

    lobby, _, _ = make_lobby(tmp_path)
    monkeypatch.setattr(arena_lobby, "default_lobby", lambda: lobby)

    handler_cls = ui.handler(ui.Dashboard())
    token = handler_cls.token
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_cls) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        conn = HTTPConnection("127.0.0.1", http.server_port)
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{http.server_port}",
            "X-UI-Token": token,
        }
        try:
            # GET /api/lobby
            conn.request("GET", "/api/lobby")
            resp = conn.getresponse()
            assert resp.status == 200
            initial = json.loads(resp.read())
            assert initial["revision"] == 1

            # POST /api/lobby/action: Alice joins red
            body = json.dumps({"action": "join", "actor": "Alice", "team": "red"})
            conn.request("POST", "/api/lobby/action", body=body, headers=headers)
            resp = conn.getresponse()
            assert resp.status == 200
            after_join = json.loads(resp.read())
            assert after_join["lobby"]["revision"] == 2
            assert after_join["lobby"]["host"] == "Alice"

            # POST /api/lobby/action: Bob joins blue
            body = json.dumps({"action": "join", "actor": "Bob", "team": "blue"})
            conn.request("POST", "/api/lobby/action", body=body, headers=headers)
            resp = conn.getresponse()
            assert resp.status == 200
            rev = json.loads(resp.read())["lobby"]["revision"]

            # Non-host Bob tries to change map -> 403/400 refused
            body = json.dumps(
                {
                    "action": "map",
                    "actor": "Bob",
                    "map": "duel",
                    "expected_revision": rev,
                }
            )
            conn.request("POST", "/api/lobby/action", body=body, headers=headers)
            resp = conn.getresponse()
            assert resp.status in (400, 403)
            assert "host" in json.loads(resp.read())["error"].lower()

            # Stale expected_revision -> 409 conflict
            body = json.dumps(
                {"action": "ready", "actor": "Alice", "expected_revision": 1}
            )
            conn.request("POST", "/api/lobby/action", body=body, headers=headers)
            resp = conn.getresponse()
            assert resp.status == 409
            err = json.loads(resp.read())
            assert err["revision"] == rev
        finally:
            conn.close()
            http.shutdown()
            thread.join(timeout=3)


# --- 10. Full action dispatcher, cycling controls, mob groups & edge cases ----


def test_play_and_all_handle_action_verbs_and_cycling(tmp_path):
    lobby, fleet, clock = make_lobby(tmp_path)

    # play opens lobby, seats Alice on red, seats Bob on blue
    r1 = lobby.handle_action("Alice", {"action": "play"})
    assert r1["ok"] and r1["lobby"]["host"] == "Alice"
    assert "Alice" in r1["lobby"]["teams"]["red"]["humans"]
    # Calling play again when already seated is idempotent
    assert lobby.play("Alice")["teams"]["red"]["humans"] == ["Alice"]

    r2 = lobby.handle_action("Bob", {"action": "play"})
    assert "Bob" in r2["lobby"]["teams"]["blue"]["humans"]

    # Cycle map, mode, respawns, uneven toggle
    start_map = lobby.snapshot()["map"]
    r_map = lobby.handle_action("Alice", {"action": "cycle_map"})
    assert r_map["lobby"]["map"] != start_map
    lobby.handle_action("Alice", {"action": "map", "map": "crossroads"})

    r_mode = lobby.handle_action("Alice", {"action": "cycle_mode"})
    assert r_mode["lobby"]["mode"] in ("waves", "race")
    lobby.handle_action("Alice", {"action": "mode", "mode": "teams"})

    r_resp = lobby.handle_action("Alice", {"action": "cycle_respawns"})
    assert r_resp["lobby"]["respawn_mode"] == "player"
    assert r_resp["lobby"]["respawn_count"] == 1
    lobby.handle_action(
        "Alice", {"action": "respawns", "respawn_mode": "team", "respawn_count": 3}
    )
    assert lobby.snapshot()["respawn_mode"] == "team"
    lobby.handle_action(
        "Alice",
        {"action": "respawns", "respawn_mode": "elimination", "respawn_count": 0},
    )

    r_uneven = lobby.handle_action("Alice", {"action": "toggle_uneven"})
    assert r_uneven["lobby"]["allow_uneven"] is True
    lobby.handle_action("Alice", {"action": "uneven", "value": "off"})
    assert lobby.snapshot()["allow_uneven"] is False

    # Waves & kit (including team/slot overrides and materials)
    lobby.handle_action("Alice", {"action": "waves", "preset": "mixed-10"})
    assert lobby.snapshot()["hazards_enabled"] is True
    lobby.handle_action(
        "Alice",
        {
            "action": "waves",
            "waves": [{"mobs": {"minecraft:zombie": 2}, "delay_ticks": 20}],
        },
    )
    assert len(lobby.snapshot()["waves"]) == 1
    lobby.handle_action("Alice", {"action": "waves"})
    assert lobby.snapshot()["hazards_enabled"] is False

    lobby.handle_action(
        "Alice",
        {
            "action": "kit",
            "kit": "sword+shield",
            "weapon_material": "diamond",
            "armor_material": "iron",
        },
    )
    assert lobby.snapshot()["weapon_material"] == "diamond"
    lobby.handle_action("Alice", {"action": "kit", "kit": "bow", "team": "red"})
    assert lobby.snapshot()["kit_overrides"]["team:red"] == "bow"

    # Bots through handle_action: add (auto team), rename, move, retry, remove
    fleet.fail_ids.add("mcbot2")
    lobby.handle_action("Alice", {"action": "bot_add", "team": "red"})
    b2 = lobby.handle_action("Alice", {"action": "bot_add", "team": "blue"})
    assert "Warning" in b2["message"]
    fleet.fail_ids.clear()
    lobby.handle_action("Alice", {"action": "bot_retry", "bot": "mcbot2"})
    lobby.handle_action(
        "Alice", {"action": "bot_rename", "bot": "MCBot", "name": "Alpha"}
    )
    lobby.handle_action(
        "Alice", {"action": "bot_move", "bot": "Alpha", "team": "green"}
    )
    assert lobby.snapshot()["bots"]["default"]["team"] == "green"
    lobby.handle_action("Alice", {"action": "bot_remove", "bot": "Alpha"})
    lobby.handle_action("Alice", {"action": "bot_remove"})  # removes last remaining bot
    assert lobby.snapshot()["bots"] == {}

    # Mob groups through handle_action: add, move, remove
    g = lobby.handle_action(
        "Alice",
        {
            "action": "mob_add",
            "team": "red",
            "mob": "skeleton",
            "count": 2,
            "kit": "bow",
        },
    )
    gid = g["group"]["id"]
    lobby.handle_action(
        "Alice", {"action": "mob_move", "group_id": gid, "team": "blue"}
    )
    assert lobby.snapshot()["mob_groups"][0]["team"] == "blue"
    # Status text & board rows with mob groups and spectators
    lobby.handle_action("Spec", {"action": "watch"})
    assert "skeleton" in lobby.status_text()
    title, rows = lobby.board_rows()
    assert title and len(rows) <= 15
    lobby.handle_action("Alice", {"action": "mob_remove", "group_id": gid})
    assert lobby.snapshot()["mob_groups"] == []

    # Ready toggle, unready, countdown, reopen, host transfer, rematch, change_teams
    r_tog = lobby.handle_action("Alice", {"action": "ready_toggle"})
    assert "alice" in r_tog["lobby"]["ready_revisions"]
    r_untog = lobby.handle_action("Alice", {"action": "ready_toggle"})
    assert "alice" not in r_untog["lobby"]["ready_revisions"]
    lobby.handle_action("Alice", {"action": "ready"})
    lobby.handle_action("Bob", {"action": "ready_toggle"})
    assert lobby.snapshot()["phase"] == "countdown"
    # Board during countdown
    cd_title, _ = lobby.board_rows()
    assert "Starting in" in cd_title
    lobby.handle_action("Alice", {"action": "reopen"})
    assert lobby.snapshot()["phase"] == "open"
    lobby.handle_action("Alice", {"action": "ready"})
    lobby.handle_action("Bob", {"action": "unready"})
    lobby.handle_action("Bob", {"action": "ready"})
    clock.advance(6.0)
    assert lobby.tick() is not None
    assert "Live" in lobby.board_rows()[0]

    # Play during running match auto-watches & queues
    lobby.play("LateJoiner")
    assert "LateJoiner" in lobby.snapshot()["queued_next"]

    # Complete match -> recap board -> play_again / change_teams / host / leave
    lobby.complete_match({"winner": "red", "outcome": "won"})
    assert "Recap" in lobby.board_rows()[0]
    lobby.handle_action("Alice", {"action": "rematch"})
    assert lobby.snapshot()["phase"] == "open"
    lobby.handle_action("Alice", {"action": "change_teams"})
    lobby.handle_action("Alice", {"action": "host", "player": "Bob"})
    assert lobby.snapshot()["host"] == "Bob"
    lobby.handle_action("Alice", {"action": "leave"})
    lobby.handle_disconnect("Bob")
    assert "Bob" not in [h["name"] for h in lobby.snapshot()["teams"]["blue"]["humans"]]


def test_validation_error_branches_and_edge_cases(tmp_path):
    lobby, fleet, clock = make_lobby(tmp_path)

    # Unknown action & invalid inputs
    with pytest.raises(LobbyError, match="Unknown lobby action"):
        lobby.handle_action("Alice", {"action": "dance"})
    with pytest.raises(LobbyError, match="Specify a team"):
        lobby.handle_action("Alice", {"action": "join"})
    with pytest.raises(LobbyError, match="Invalid player name"):
        lobby.join("bad name!", "red")
    with pytest.raises(LobbyError, match="host"):
        lobby.set_map("Alice", "duel")  # no host yet and not operator
    with pytest.raises(LobbyError, match="Join a team"):
        lobby.ready("Ghost")
    with pytest.raises(LobbyError, match="seated"):
        lobby.unready("Ghost")
    with pytest.raises(LobbyError, match="countdown"):
        lobby.reopen("Alice", operator=True)

    lobby.join("Alice", "red")
    with pytest.raises(LobbyError, match="Unknown map"):
        lobby.set_map("Alice", "mars")
    with pytest.raises(LobbyError, match="Unknown mode"):
        lobby.set_mode("Alice", "capture")
    with pytest.raises(LobbyError, match="Unknown wave preset"):
        lobby.set_waves("Alice", preset="nope")
    with pytest.raises(LobbyError, match="not both"):
        lobby.set_waves("Alice", waves=[], preset="mixed-10")
    with pytest.raises(LobbyError, match="Mob type"):
        lobby.add_mob_group("Alice", "red", "", 1)
    with pytest.raises(LobbyError, match="not allowed"):
        lobby.add_mob_group("Alice", "red", "minecraft:ender_dragon", 1)
    with pytest.raises(LobbyError, match="Invalid mob"):
        lobby.add_mob_group("Alice", "red", "bad mob!", 1)
    with pytest.raises(LobbyError, match="positive integer"):
        lobby.add_mob_group("Alice", "red", "zombie", 0)
    with pytest.raises(LobbyError, match="Unknown mob group"):
        lobby.move_mob_group("Alice", "m-999", "blue")
    with pytest.raises(LobbyError, match="Unknown mob group"):
        lobby.remove_mob_group("Alice", "m-999")
    with pytest.raises(LobbyError, match="non-empty"):
        lobby.set_kit("Alice", "")
    with pytest.raises(LobbyError, match="Respawn mode"):
        lobby.set_respawns("Alice", "infinite", 1)
    with pytest.raises(LobbyError, match="0 to 99"):
        lobby.set_respawns("Alice", "player", -1)
    with pytest.raises(LobbyError, match="Elimination"):
        lobby.set_respawns("Alice", "elimination", 2)
    with pytest.raises(LobbyError, match="count >= 1"):
        lobby.set_respawns("Alice", "team", 0)
    with pytest.raises(LobbyError, match="not in the lobby"):
        lobby.transfer_host("Alice", "Nobody")
    with pytest.raises(LobbyError, match="no bots"):
        lobby.remove_bot("Alice")
    with pytest.raises(LobbyError, match="already a human"):
        lobby.add_bot("Alice", "blue", name="Alice")
    with pytest.raises(LobbyError, match="No bot"):
        lobby.move_bot("Alice", "GhostBot", "blue")
    with pytest.raises(LobbyError, match="zero-human"):
        lobby.launch_automated()

    # Waves mode validation: requires waves, checks vault overflow
    lobby.set_mode("Alice", "waves")
    with pytest.raises(LobbyError, match="requires"):
        lobby.set_waves("Alice", waves=[])
    with pytest.raises(LobbyError, match="requires"):
        lobby.set_waves("Alice")

    # Switching from larger map to smaller map surfaces exact overflow in validation_errors
    lobby.set_mode("Alice", "teams")
    lobby.join("Bob", "red")
    lobby.join("Cara", "red")
    lobby.join("Dan", "green")
    lobby.set_map("Alice", "duel")
    errs = lobby.snapshot()["validation_errors"]
    assert any("red" in e.lower() and "2" in e for e in errs)
    assert any("green" in e.lower() and "duel" in e.lower() for e in errs)

    # Corrupted JSON on disk recovers cleanly to blank state
    bad_path = tmp_path / "corrupt.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    recovered = ArenaLobby.load(state_path=bad_path, runtime_dir=tmp_path)
    assert recovered.snapshot()["revision"] == 1

    # Automated launch via handle_action & validation failure
    auto_lobby, _, _ = make_lobby(tmp_path / "auto")
    with pytest.raises(LobbyError, match="at least 2 teams"):
        auto_lobby.handle_action("lab", {"action": "launch_automated"}, operator=True)
    auto_lobby.add_bot("lab", "red", operator=True)
    auto_lobby.add_bot("lab", "blue", operator=True)
    res = auto_lobby.handle_action("lab", {"action": "launch_automated"}, operator=True)
    assert res["ok"] and res["lobby"]["phase"] == "running"


def test_fleet_adapters_and_default_lobby_and_world_stations(tmp_path, monkeypatch):
    from mclab import arena_lobby, companion_api

    # InProcessFleetAdapter
    class MiniFleet:
        def __init__(self):
            self.items = {}

        def bots(self):
            return list(self.items.values())

        def add(self, bot_id):
            if bot_id == "boom":
                raise RuntimeError("nope")
            self.items[bot_id] = {"id": bot_id, "name": "MCBot", "session": "ready"}
            return self.items[bot_id]

        def remove(self, bot_id):
            self.items.pop(bot_id)

        def restart(self, bot_id):
            return self.items[bot_id]

    adapter = arena_lobby.InProcessFleetAdapter(MiniFleet())
    assert adapter.add_bot("default", "MCBot")["ok"] is True
    assert len(adapter.bots()) == 1
    assert adapter.restart_bot("default", "MCBot")["ok"] is True
    assert adapter.remove_bot("default")["ok"] is True
    assert adapter.add_bot("boom", "Boom")["ok"] is False
    assert adapter.remove_bot("missing")["ok"] is False

    # ApiFleetAdapter error handling when host is down
    api_adapter = arena_lobby.ApiFleetAdapter(tmp_path / "no-host")
    assert api_adapter.bots() == []
    assert api_adapter.add_bot("default", "MCBot")["ok"] is False
    assert api_adapter.remove_bot("default")["ok"] is False
    assert api_adapter.restart_bot("default", "MCBot")["ok"] is False

    # default_lobby caching & runtime_dir isolation
    dl1 = arena_lobby.default_lobby(runtime_dir=tmp_path / "rt", fleet=MiniFleet())
    dl2 = arena_lobby.default_lobby(runtime_dir=tmp_path / "rt")
    assert dl1 is dl2

    # world.lobby_stations()
    stations = world.lobby_stations()
    assert len(stations) == len(designs.LOBBY_STATIONS)
    assert {s["id"] for s in stations} >= {"join_red", "ready", "help"}
    assert companion_api.RUNTIME_FILE == "companion-host.json"


def test_ingress_board_updates_and_dashboard_error_codes(tmp_path, monkeypatch):
    from mclab import arena_lobby
    from mclab import dashboard as ui
    from mclab.companion_ingress import Ingress
    from mclab.companion_intents import Actor

    lobby, _, clock = make_lobby(tmp_path)
    boards = []
    tells = []

    class HostStub:
        def call(self, intent, args, actor, *, key=None, bot=None):
            if intent == "say":
                tells.append((args.get("to"), args.get("text")))
            return {"ok": True, "result": {}}

    class TalkerStub:
        def hear(self, actor, text):
            pass

    batches = [
        {"latest_sequence": 1, "events": []},
        {"latest_sequence": 1, "events": []},
    ]
    ingress = Ingress(
        HostStub(),
        TalkerStub(),
        lambda after: batches.pop(0)
        if batches
        else {"latest_sequence": 1, "events": []},
        lobby=lobby,
        board=lambda name, title, rows: boards.append((name, title, rows)),
    )
    alice = Actor("command", "Alice", "u-a", None)
    bob = Actor("command", "Bob", "u-b", None)
    ingress._command(alice, {"command": "lobby", "action": "join", "team": "red"})
    ingress._command(bob, {"command": "lobby", "action": "join", "team": "blue"})
    ingress._command(bob, {"command": "lobby", "action": "map", "map": "duel"})
    assert any("host" in text.lower() for _, text in tells)
    assert boards and boards[-1][0] == "lobby"

    # Tick runs in poll_once
    ingress._command(alice, {"command": "lobby", "action": "ready"})
    ingress._command(bob, {"command": "lobby", "action": "ready"})
    assert lobby.snapshot()["phase"] == "countdown"
    ingress.poll_once()
    clock.advance(6.0)
    ingress.poll_once()
    assert lobby.snapshot()["phase"] == "running"

    # Dashboard 409 frozen and 400 invalid action responses
    lobby.complete_match({"winner": "red", "outcome": "won"})
    lobby.change_teams("Alice")
    rev = lobby.snapshot()["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    assert lobby.snapshot()["phase"] == "countdown"

    monkeypatch.setattr(arena_lobby, "default_lobby", lambda: lobby)
    handler_cls = ui.handler(ui.Dashboard())
    token = handler_cls.token
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_cls) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        conn = HTTPConnection("127.0.0.1", http.server_port)
        headers = {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{http.server_port}",
            "X-UI-Token": token,
        }
        try:
            # 409 frozen during countdown
            conn.request(
                "POST",
                "/api/lobby/action",
                body=json.dumps({"action": "map", "actor": "Alice", "map": "duel"}),
                headers=headers,
            )
            resp = conn.getresponse()
            assert resp.status == 409
            assert json.loads(resp.read())["code"] == "frozen"

            # 400 unknown action
            conn.request(
                "POST",
                "/api/lobby/action",
                body=json.dumps({"action": "bogus", "actor": "Alice"}),
                headers=headers,
            )
            resp = conn.getresponse()
            assert resp.status == 400
        finally:
            conn.close()
            http.shutdown()
            thread.join(timeout=3)


# --- 11. Headless launcher, failure recovery & board integration --------------


def test_fail_match_resets_running_lobby_cleanly(tmp_path):
    lobby, fleet, clock = make_lobby(tmp_path)
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    rev = lobby.snapshot()["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    clock.advance(6.0)
    assert lobby.tick() is not None
    assert lobby.snapshot()["phase"] == "running"

    snap = lobby.fail_match("arena server unreachable")
    assert snap["phase"] == "open"
    assert lobby._state["active_match"] is None
    assert snap["ready_revisions"] == {}
    assert "unreachable" in snap["last_change_reason"]


def test_make_arena_launcher_runs_match_forwards_respawns_and_completes_recap(tmp_path):
    from mclab import arena_lobby

    boards: list[tuple[str, str, list[dict]]] = []
    prepared: list[tuple[str, str]] = []
    launched_series: list[dict] = []
    record_file = tmp_path / "record.json"
    record_file.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "winner": "red",
                        "outcome": "won",
                        "reason": "eliminated",
                        "seconds": 18.4,
                        "players": {"Alice": {"player_kills": 1}},
                        "team_kills": {"red": 1, "blue": 0},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeAdmin:
        def __init__(self):
            self.checked = False

        def require_protocol(self):
            self.checked = True
            return {"protocol": 5}

    admin_stub = FakeAdmin()
    api_stub = object()

    def fake_prepare(admin, arena_name, *, echo):
        prepared.append((arena_name, str(admin.checked)))
        return {"arena": arena_name, "spawn": [0, 102, 0], "built": []}

    def fake_run_series(api, admin, name, waves, **kwargs):
        launched_series.append(
            {"api": api, "admin": admin, "name": name, "waves": waves, **kwargs}
        )
        return record_file

    holder: dict[str, ArenaLobby] = {}
    launcher = arena_lobby.make_arena_launcher(
        runtime_dir=tmp_path,
        lobby_getter=lambda: holder["lobby"],
        board=lambda arena_name, title, rows: boards.append((arena_name, title, rows)),
        prepare_fn=fake_prepare,
        run_series_fn=fake_run_series,
        admin_factory=lambda: admin_stub,
        api_factory=lambda: api_stub,
        async_launch=False,
    )
    lobby, _, clock = make_lobby(tmp_path, on_launch=launcher)
    holder["lobby"] = lobby
    lobby.join("Alice", "red")
    lobby.add_bot("Alice", "blue")
    lobby.set_respawns("Alice", "team", 2)
    lobby.set_kit(
        "Alice", "sword+shield", weapon_material="diamond", armor_material="iron"
    )
    rev = lobby.snapshot()["revision"]
    lobby.ready("Alice", seen_revision=rev)
    clock.advance(6.0)

    cfg = lobby.tick()
    assert cfg is not None
    assert admin_stub.checked is True
    assert prepared == [("crossroads", "True")]
    assert len(launched_series) == 1
    call = launched_series[0]
    assert call["api"] is api_stub
    assert call["name"] == "crossroads"
    assert call["waves"] == []  # mob-free fresh PvP
    assert call["mode"] == "teams"
    assert call["teams"] == {"red": ["Alice"], "blue": ["MCBot"]}
    assert call["exit"] == [0, 102, 0]
    assert call["respawn_mode"] == "team"
    assert call["respawn_count"] == 2
    assert call["bots"] == {"MCBot": "default"}
    assert any(item["item"] == "minecraft:diamond_sword" for item in call["kit"])
    snap = lobby.snapshot()
    assert snap["phase"] == "recap"
    assert snap["last_recap"]["winner"] == "red"
    assert boards and boards[-1][0] == "lobby" and "Recap" in boards[-1][1]


def test_make_arena_launcher_recovers_to_open_on_failure_and_attaches_on_launch(
    tmp_path,
):
    from mclab import arena_lobby

    boards: list[tuple[str, str, list[dict]]] = []
    holder: dict[str, ArenaLobby] = {}

    def failing_prepare(admin, arena_name, *, echo):
        raise RuntimeError("bridge disconnected")

    launcher = arena_lobby.make_arena_launcher(
        runtime_dir=tmp_path,
        lobby_getter=lambda: holder["lobby"],
        board=lambda arena_name, title, rows: boards.append((arena_name, title, rows)),
        prepare_fn=failing_prepare,
        admin_factory=lambda: type("A", (), {"require_protocol": lambda self: {}})(),
        api_factory=lambda: object(),
        async_launch=False,
    )
    lobby, _, clock = make_lobby(tmp_path, on_launch=launcher)
    holder["lobby"] = lobby
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    rev = lobby.snapshot()["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    clock.advance(6.0)
    lobby.tick()

    snap = lobby.snapshot()
    assert snap["phase"] == "open"
    assert lobby._state["active_match"] is None
    assert snap["ready_revisions"] == {}
    assert "bridge disconnected" in snap["last_change_reason"]
    assert boards and boards[-1][0] == "lobby"

    # default_lobby attaches on_launch when an existing instance had None
    rt = tmp_path / "cached_rt"
    first = arena_lobby.default_lobby(runtime_dir=rt)
    assert first._on_launch is None
    second = arena_lobby.default_lobby(runtime_dir=rt, on_launch=launcher)
    assert second is first and second._on_launch is launcher


# --- 12. Kit library modes, random kits, host clone/tweak & anti-scumming -----


def test_lobby_kit_modes_class_picker_and_host_tweak(tmp_path):
    from mclab import arena_kits

    kits_path = tmp_path / "arena-kits.json"
    fleet = FakeFleet()
    lobby = arena_lobby_with_kits(tmp_path, kits_path, fleet=fleet)
    snap = lobby.snapshot()
    assert snap["kit_mode"] == "uniform"
    assert snap["kit_pool"] == ["fighter", "archer", "tank", "skirmisher"]
    assert snap["team_kits"] == {}
    assert snap["player_kits"] == {}
    assert snap["reroll_on_respawn"] is False

    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")

    # Non-host cannot change kit_mode, kit_pool, or reroll_on_respawn
    for fn in (
        lambda: lobby.set_kit_mode("Bob", "class_picker"),
        lambda: lobby.set_kit_pool("Bob", ["fighter", "archer"]),
        lambda: lobby.set_kit_reroll("Bob", True),
        lambda: lobby.clone_tweak_kit("Bob", "fighter", "Sneaky"),
    ):
        with pytest.raises(PermissionError_, match="host"):
            fn()

    # Host switches to class_picker with restricted pool
    snap = lobby.set_kit_mode("Alice", "class_picker")
    assert snap["kit_mode"] == "class_picker"
    lobby.set_kit_pool("Alice", ["fighter", "archer"])
    lobby.set_kit_reroll("Alice", True)
    assert lobby.snapshot()["reroll_on_respawn"] is True

    # Seated player can pick a kit from pool OR 'random'
    lobby.pick_kit("Bob", "random")
    assert lobby.snapshot()["player_kits"]["Bob"] == "random"
    lobby.pick_kit("Alice", "archer")
    assert lobby.snapshot()["player_kits"]["Alice"] == "archer"
    with pytest.raises(LobbyError, match="pool"):
        lobby.pick_kit("Bob", "tank")

    # Host can clone & tweak a kit in the lobby using survival-legal validation
    tweaked = lobby.clone_tweak_kit(
        "Alice",
        "fighter",
        "Boosted Fighter",
        add_items=[{"item": "minecraft:cooked_beef", "count": 4}],
        add_to_pool=True,
    )
    assert tweaked["id"] in lobby.snapshot()["kit_pool"]
    lib = arena_kits.KitLibrary(kits_path)
    assert lib.has_kit(tweaked["id"])

    # Illegal sharpness 100 tweak is rejected cleanly
    with pytest.raises(LobbyError, match="level|invalid"):
        lobby.clone_tweak_kit(
            "Alice",
            "fighter",
            "Cheat Sword",
            enchant_tweaks={"minecraft:iron_sword": {"minecraft:sharpness": 100}},
        )


def arena_lobby_with_kits(
    tmp_path, kits_path, *, fleet=None, clock=None, on_launch=None
):
    return ArenaLobby(
        state_path=tmp_path / "arena-lobby.json",
        runtime_dir=tmp_path,
        kits_path=kits_path,
        fleet=fleet if fleet is not None else FakeFleet(),
        clock=clock if clock is not None else FakeClock(),
        on_launch=on_launch,
    )


def test_random_kits_roll_at_match_start_only_and_forward_team_mobs(tmp_path):
    from mclab import arena_lobby as arena_lobby_module

    launched: list[dict] = []
    record_file = tmp_path / "rec.json"
    record_file.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "winner": "red",
                        "outcome": "won",
                        "seconds": 12.0,
                        "players": {"Alice": {"player_kills": 1}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeAdmin:
        def require_protocol(self):
            return {"protocol": 6}

    def fake_prepare(admin, arena_name, *, echo):
        return {"arena": arena_name, "spawn": [0, 102, 0], "built": []}

    def fake_run_series(api, admin, name, waves, **kwargs):
        launched.append(kwargs)
        # Exercise respawn_kit_roller once if provided
        if kwargs.get("respawn_kit_roller"):
            rolled = kwargs["respawn_kit_roller"]("Bob", 1)
            assert rolled["kit_id"] in {"fighter", "archer", "tank", "skirmisher"}
            assert rolled["compiled"]["give"]
        return record_file

    holder: dict[str, ArenaLobby] = {}
    kits_path = tmp_path / "arena-kits.json"
    launcher = arena_lobby_module.make_arena_launcher(
        runtime_dir=tmp_path,
        lobby_getter=lambda: holder["lobby"],
        prepare_fn=fake_prepare,
        run_series_fn=fake_run_series,
        admin_factory=lambda: FakeAdmin(),
        api_factory=lambda: object(),
        kits_path=kits_path,
        async_launch=False,
    )
    clock = FakeClock()
    lobby = arena_lobby_with_kits(tmp_path, kits_path, clock=clock, on_launch=launcher)
    holder["lobby"] = lobby
    lobby.join("Alice", "red")
    lobby.join("Bob", "blue")
    lobby.add_mob_group("Alice", "red", "minecraft:skeleton", 2)
    lobby.add_mob_group("Alice", "blue", "minecraft:zombie", 2)
    lobby.set_kit_mode("Alice", "class_picker")
    lobby.pick_kit("Alice", "fighter")
    lobby.pick_kit("Bob", "random")
    lobby.set_kit_reroll("Alice", True)

    # Anti-reroll-scumming: random assignment must NOT appear before match start!
    snap = lobby.snapshot()
    assert snap["last_kit_assignment"] is None
    assert snap["player_kits"]["Bob"] == "random"

    rev = snap["revision"]
    lobby.ready("Alice", seen_revision=rev)
    lobby.ready("Bob", seen_revision=rev)
    assert lobby.snapshot()["phase"] == "countdown"
    assert lobby.snapshot()["last_kit_assignment"] is None

    clock.advance(6.0)
    lobby.tick()

    assert len(launched) == 1
    kwargs = launched[0]
    assert kwargs["team_mobs"] == {
        "red": [{"type": "minecraft:skeleton", "count": 2}],
        "blue": [{"type": "minecraft:zombie", "count": 2}],
    }
    assert "Alice" in kwargs["player_kits"] and "Bob" in kwargs["player_kits"]
    meta = kwargs["kit_assignment"]
    assert meta["mode"] == "class_picker" and isinstance(meta["seed"], int)
    assert meta["assignments"]["Alice"]["random_rolled"] is False
    assert meta["assignments"]["Bob"]["random_rolled"] is True
    assert lobby.snapshot()["last_recap"]["kit_assignment"]["seed"] == meta["seed"]


# --- 9. The game menu: settings form, menu action, plain wording ---------------


def _hosted(tmp_path, **kwargs):
    lobby, fleet, clock = make_lobby(tmp_path, **kwargs)
    lobby.join("Ann", "red")
    lobby.join("Bob", "blue")
    return lobby, fleet, clock


def test_apply_settings_changes_everything_in_one_revision(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    lobby.ready("Bob", seen_revision=lobby.snapshot()["revision"])
    before = lobby.snapshot()["revision"]
    snap = lobby.apply_settings(
        "Ann",
        {
            "mode": "race",
            "lives": "team:5",
            "kit_mode": "per_team",
            "reroll": True,
            "uneven": True,
        },
    )
    assert snap["revision"] == before + 1
    assert snap["mode"] == "race"
    assert (snap["respawn_mode"], snap["respawn_count"]) == ("team", 5)
    assert snap["kit_mode"] == "per_team"
    assert snap["reroll_on_respawn"] is True and snap["allow_uneven"] is True
    assert "game settings" in snap["last_change_reason"]
    assert not any(c in snap["last_change_reason"] for c in "#:")


def test_apply_settings_with_no_difference_spends_no_revision(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    before = lobby.snapshot()
    snap = lobby.apply_settings(
        "Ann",
        {
            "mode": before["mode"],
            "kit": "current",
            "reroll": before["reroll_on_respawn"],
        },
    )
    assert snap["revision"] == before["revision"]


@pytest.mark.parametrize(
    "bad,match",
    [
        ({"mode": "chess"}, "Unknown game mode"),
        ({"lives": "forever"}, "lives options"),
        ({"lives": "team:0"}, "lives options"),
        ({"kit_mode": "nope"}, "kit options"),
        ({"kit": "no_such_kit"}, "no kit called"),
    ],
)
def test_apply_settings_refuses_bad_fields_and_changes_nothing(tmp_path, bad, match):
    lobby, _, _ = _hosted(tmp_path)
    before = lobby.snapshot()
    with pytest.raises(LobbyError, match=match):
        lobby.apply_settings("Ann", {"uneven": True, **bad})
    after = lobby.snapshot()
    assert after["revision"] == before["revision"]
    assert after["allow_uneven"] == before["allow_uneven"]


def test_apply_settings_is_host_only_and_checks_the_revision(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    with pytest.raises(LobbyError):
        lobby.apply_settings("Bob", {"mode": "race"})
    with pytest.raises(LobbyError):
        lobby.apply_settings("Ann", {"mode": "race"}, expected_revision=999)


def test_apply_settings_waves_flag_and_mode_rules(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    snap = lobby.apply_settings("Ann", {"waves": True})
    assert snap["waves_preset"] or snap["waves"]
    snap = lobby.apply_settings("Ann", {"waves": False})
    assert not snap["waves_preset"] and not snap["waves"]
    lobby.apply_settings("Ann", {"mode": "waves"})
    with pytest.raises(LobbyError, match="needs mob waves"):
        lobby.apply_settings("Ann", {"waves": False})
    snap = lobby.apply_settings("Ann", {"mode": "teams"})
    assert snap["mode"] == "teams" and not snap["waves_preset"]


def test_apply_settings_rejects_a_mode_the_map_cannot_run(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    lobby.set_map("Ann", "vault")
    with pytest.raises(LobbyError, match="does not work"):
        lobby.apply_settings("Ann", {"mode": "teams"})


def test_apply_settings_reopens_a_recap(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    lobby._state["phase"] = "recap"
    assert lobby.apply_settings("Ann", {"reroll": True})["phase"] == "open"


def test_settings_action_reports_what_changed_in_words(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    reply = lobby.handle_action("Ann", {"action": "settings", "mode": "race"})
    assert reply["ok"] and "game settings" in reply["message"]
    same = lobby.handle_action("Ann", {"action": "settings", "mode": "race"})
    assert same["message"] == "Nothing changed."


def test_menu_action_is_a_quiet_snapshot(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    reply = lobby.handle_action("Ann", {"action": "menu"})
    assert reply["ok"] and reply["message"] == ""
    assert reply["lobby"]["revision"] == lobby.snapshot()["revision"]


def test_bot_add_with_team_auto_and_no_name_picks_a_team_and_a_name(tmp_path):
    lobby, fleet, _ = _hosted(tmp_path)
    reply = lobby.handle_action(
        "Ann", {"action": "bot_add", "team": "auto", "name": ""}
    )
    assert reply["ok"]
    assert len(lobby.snapshot()["bots"]) == 1 and fleet.added


def test_board_rows_and_status_are_plain_english(tmp_path):
    lobby, _, _ = _hosted(tmp_path)
    snap = lobby.snapshot()
    cells = [str(c) for row in lobby.board_rows(snap) for c in row]
    text = " ".join(cells) + " " + lobby.status_text(snap)
    assert "rev #" not in text and "#" not in text
    assert "Elim" not in text and "NoMob" not in text and "P:" not in text
    assert "Team Battle" in text
