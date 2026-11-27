"""Integration tests for /api/kits* and /api/scenarios* routes."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mclab import arena_kits, arena_scenarios, dashboard, dashboard_runs


@pytest.fixture()
def server_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard_runs, "ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "SESSIONS", tmp_path / "results/sessions")
    monkeypatch.setattr(
        arena_kits, "DEFAULT_KITS_PATH", tmp_path / "runtime/arena-kits.json"
    )
    monkeypatch.setattr(
        arena_scenarios,
        "DEFAULT_STORE_PATH",
        tmp_path / "runtime/arena-scenarios.json",
    )
    monkeypatch.setattr(
        arena_scenarios,
        "DEFAULT_RESULTS_DIR",
        tmp_path / "results/companion/arena",
    )
    handler_cls = dashboard.handler(dashboard.Dashboard())
    token = handler_cls.token
    http = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{http.server_port}"
    headers = {
        "Content-Type": "application/json",
        "Origin": origin,
        "X-UI-Token": token,
    }
    try:
        yield tmp_path, http.server_port, headers
    finally:
        http.shutdown()
        thread.join(timeout=3)


def _req(
    port: int,
    method: str,
    path: str,
    body: dict | None = None,
    headers: dict | None = None,
):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = json.dumps(body) if body is not None else None
        req_headers = dict(headers or {})
        if payload is not None and "Content-Length" not in req_headers:
            req_headers["Content-Length"] = str(len(payload.encode("utf-8")))
        conn.request(method, path, body=payload, headers=req_headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw) if raw else None
    finally:
        conn.close()


def test_dashboard_kits_crud_validation_and_builtin_protection(server_env):
    _root, port, headers = server_env

    status, data = _req(port, "GET", "/api/kits")
    assert status == 200
    assert {k["id"] for k in data["kits"]} >= {
        "fighter",
        "archer",
        "tank",
        "skirmisher",
    }
    assert "enchantments" in data["catalog"]

    # Auth guard blocks POST/PUT/DELETE without X-UI-Token
    st_unauth, _ = _req(
        port,
        "POST",
        "/api/kits",
        {
            "name": "Unauth",
            "items": [{"item": "minecraft:iron_sword", "count": 1}],
        },
    )
    assert st_unauth == 403

    # POST /api/kits rejects illegal cheat enchantment & non-catalog item
    st_bad, err_bad = _req(
        port,
        "POST",
        "/api/kits",
        {
            "name": "OP Sword",
            "items": [
                {
                    "item": "minecraft:iron_sword",
                    "count": 1,
                    "enchantments": {"minecraft:sharpness": 100},
                },
                {"item": "minecraft:enchanted_golden_apple", "count": 1},
            ],
        },
        headers,
    )
    assert st_bad == 400
    assert err_bad["errors"]

    # POST /api/kits/validate checks draft and computes tier
    st_val, val = _req(
        port,
        "POST",
        "/api/kits/validate",
        {
            "name": "Duelist",
            "items": [
                {
                    "item": "minecraft:diamond_sword",
                    "count": 1,
                    "enchantments": {"minecraft:sharpness": 3},
                },
                {"item": "minecraft:cooked_beef", "count": 16},
            ],
        },
        headers,
    )
    assert st_val == 200
    assert val["power"]["score"] > 0

    # POST /api/kits creates custom kit
    st_create, created = _req(
        port,
        "POST",
        "/api/kits",
        {
            "name": "Custom Duelist",
            "description": "Balanced sword build",
            "items": [
                {
                    "item": "minecraft:iron_sword",
                    "count": 1,
                    "enchantments": {"minecraft:sharpness": 2},
                },
                {"item": "minecraft:cooked_beef", "count": 16},
            ],
        },
        headers,
    )
    assert st_create == 200
    cid = created["id"]

    # PUT /api/kits/<id> updates custom kit; built-in -> 403; unknown -> 404
    st_upd, updated = _req(
        port,
        "PUT",
        f"/api/kits/{cid}",
        {
            "name": "Custom Duelist Plus",
            "items": [
                {
                    "item": "minecraft:diamond_sword",
                    "count": 1,
                    "enchantments": {"minecraft:sharpness": 2},
                },
                {"item": "minecraft:cooked_beef", "count": 16},
            ],
        },
        headers,
    )
    assert st_upd == 200 and updated["name"] == "Custom Duelist Plus"
    st_bi, _ = _req(
        port, "PUT", "/api/kits/fighter", {"name": "Hacked", "items": []}, headers
    )
    assert st_bi == 403
    st_miss, _ = _req(
        port, "PUT", "/api/kits/missing", {"name": "Nope", "items": []}, headers
    )
    assert st_miss == 404

    # POST /api/kits/<id>/duplicate duplicates starter class
    st_dup, dup = _req(
        port, "POST", "/api/kits/fighter/duplicate", {"name": "My Fighter"}, headers
    )
    assert st_dup == 200
    assert dup["builtin"] is False and dup["name"] == "My Fighter"

    # DELETE built-in 403, missing 404, custom 200
    assert _req(port, "DELETE", "/api/kits/fighter", headers=headers)[0] == 403
    assert _req(port, "DELETE", "/api/kits/missing", headers=headers)[0] == 404
    st_del, del_res = _req(port, "DELETE", f"/api/kits/{cid}", headers=headers)
    assert st_del == 200 and del_res["deleted"] == cid


def test_dashboard_scenarios_and_arena_detail_routes(server_env):
    root, port, headers = server_env

    # Realistic arena series artifact for comparison + /api/detail inspection
    series_rel = "results/companion/arena/20261127T120000Z.json"
    series_path = root / series_rel
    series_path.parent.mkdir(parents=True, exist_ok=True)
    series_path.write_text(
        json.dumps(
            {
                "arena": "crossroads",
                "mode": "teams",
                "runs": [
                    {
                        "scenario_id": "base-duel",
                        "scenario_version": 1,
                        "outcome": "won",
                        "winner": "red",
                        "seconds": 25.0,
                        "players": {
                            "MCBot": {
                                "team": "red",
                                "mob_kills": 0,
                                "player_kills": 1,
                                "damage_dealt": 24.0,
                                "damage_taken": 8.0,
                            },
                            "MCBot2": {
                                "team": "blue",
                                "mob_kills": 0,
                                "player_kills": 0,
                                "damage_dealt": 8.0,
                                "damage_taken": 24.0,
                            },
                        },
                    },
                    {
                        "scenario_id": "base-duel",
                        "scenario_version": 1,
                        "outcome": "not_started",
                        "error": "over_capacity",
                    },
                    {
                        "scenario_id": "base-duel-var",
                        "scenario_version": 1,
                        "outcome": "won",
                        "winner": "red",
                        "swapped": True,
                        "side_mapping": {"red": "blue", "blue": "red"},
                        "seconds": 19.0,
                        "players": {
                            "MCBot2": {
                                "team": "red",
                                "mob_kills": 0,
                                "player_kills": 1,
                                "damage_dealt": 22.0,
                                "damage_taken": 6.0,
                            },
                            "MCBot": {
                                "team": "blue",
                                "mob_kills": 0,
                                "player_kills": 0,
                                "damage_dealt": 6.0,
                                "damage_taken": 22.0,
                            },
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # POST /api/scenarios creates v1
    st1, s1 = _req(
        port,
        "POST",
        "/api/scenarios",
        {
            "id": "base-duel",
            "name": "Base Duel",
            "config": {
                "arena": "crossroads",
                "game": "teams",
                "teams": {"red": ["MCBot"], "blue": ["MCBot2"]},
                "kit": "fighter",
            },
        },
        headers,
    )
    assert st1 == 200 and s1["version"] == 1

    st_list, listed = _req(port, "GET", "/api/scenarios")
    assert st_list == 200 and len(listed) == 1

    # Duplicate & edit one factor
    st_dup, var = _req(
        port,
        "POST",
        "/api/scenarios/base-duel/duplicate",
        {
            "id": "base-duel-var",
            "name": "Base Duel Archer",
            "factor_note": "Switch kit to archer",
            "edits": {"kit": "archer"},
        },
        headers,
    )
    assert st_dup == 200 and var["parent_id"] == "base-duel" and var["changed_fields"]

    # Side swap
    st_swap, swapped = _req(port, "POST", "/api/scenarios/base-duel/swap", {}, headers)
    assert st_swap == 200 and swapped["swapped_from"] == "base-duel"

    # Compare baseline vs variant + check /api/detail works on arena run JSON
    st_cmp, cmp_data = _req(
        port,
        "POST",
        "/api/scenarios/compare",
        {"baseline_id": "base-duel", "variant_id": "base-duel-var"},
        headers,
    )
    assert st_cmp == 200
    assert cmp_data["baseline"]["completed_rounds"] == 1
    assert cmp_data["baseline"]["failed_starts_count"] == 1
    assert cmp_data["variant"]["completed_rounds"] == 1

    st_det, detail = _req(port, "GET", f"/api/detail?path={series_rel}")
    assert st_det == 200 and detail["arena"] == "crossroads"

    # Unknown scenario returns 404, DELETE works, DELETE auth fails without token
    assert _req(port, "GET", "/api/scenarios/missing")[0] == 404
    assert _req(port, "DELETE", "/api/scenarios/base-duel")[0] == 403
    st_sdel, _ = _req(
        port, "DELETE", f"/api/scenarios/{swapped['id']}", headers=headers
    )
    assert st_sdel == 200
