"""Tests for versioned arena scenarios, side-swapping, series expansion, and comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mclab import arena_scenarios as sc


def _base_config(**overrides) -> dict:
    cfg = {
        "arena": "crossroads",
        "game": "teams",
        "teams": {"red": ["MCBot"], "blue": ["MCBot2"]},
        "team_mobs": {
            "red": {"minecraft:skeleton": 2},
            "blue": {"minecraft:zombie": 3},
        },
        "team_mob_kits": {
            "red": {"minecraft:skeleton": "archer"},
            "blue": {"minecraft:zombie": "fighter"},
        },
        "waves": [],
        "respawn_mode": "elimination",
        "respawn_count": 0,
        "kit": "fighter",
        "team_kits": {"red": "fighter", "blue": "tank"},
        "kit_mode": "per_team",
        "kit_pool": ["fighter", "archer", "tank", "skirmisher"],
        "bot_profiles": {"red": "aggressive", "blue": "defensive"},
        "count": 3,
        "timeout": 300,
    }
    cfg.update(overrides)
    return cfg


def _match_row(
    *,
    outcome: str = "won",
    winner: str | None = "red",
    reason: str | None = "last_team_standing",
    seconds: float | None = 42.0,
    players: dict | None = None,
    error: str | None = None,
    swapped: bool | None = None,
    side_mapping: dict[str, str] | None = None,
    mode: str = "teams",
) -> dict:
    row: dict = {
        "mode": mode,
        "outcome": outcome,
        "reason": reason,
        "seconds": seconds,
    }
    if winner is not None:
        row["winner"] = winner
    if players is not None:
        row["players"] = players
    if error is not None:
        row["error"] = error
    if swapped is not None:
        row["swapped"] = swapped
    if side_mapping is not None:
        row["side_mapping"] = side_mapping
    return row


def _write_series(
    folder: Path,
    filename: str,
    *,
    scenario_id: str,
    scenario_version: int = 1,
    swapped: bool = False,
    side_mapping: dict[str, str] | None = None,
    teams: dict[str, list[str]] | None = None,
    runs: list[dict] | None = None,
    error: str | None = None,
    mode: str = "teams",
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "arena": "crossroads",
        "label": f"{scenario_id}-v{scenario_version}",
        "mode": mode,
        "scenario_id": scenario_id,
        "scenario_version": scenario_version,
        "swapped": swapped,
        "teams": teams or {"red": ["MCBot"], "blue": ["MCBot2"]},
        "runs": runs if runs is not None else [],
    }
    if side_mapping is not None:
        record["side_mapping"] = side_mapping
    if error is not None:
        record["error"] = error
    path = folder / filename
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


# --- Persistence, schema & versioning ----------------------------------------


class TestScenarioPersistenceAndVersioning:
    def test_empty_store_returns_empty_list(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        assert sc.list_scenarios(path=store_path) == []

    def test_save_creates_v1_persists_schema_and_reloads(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        saved = sc.save_scenario(
            {
                "name": "Crossroads Baseline",
                "factor_note": "Initial balanced 1v1 + allied mobs",
                "config": _base_config(),
            },
            path=store_path,
        )
        assert saved["id"] == "crossroads-baseline"
        assert saved["name"] == "Crossroads Baseline"
        assert saved["version"] == 1
        assert saved["parent_id"] is None
        assert saved["swapped_from"] is None
        assert saved["factor_note"] == "Initial balanced 1v1 + allied mobs"
        assert saved["history"] == []
        assert saved["created_at"] == saved["updated_at"]
        assert saved["config"]["arena"] == "crossroads"
        assert saved["config"]["team_mobs"]["red"] == {"minecraft:skeleton": 2}
        assert saved["config"]["bot_profiles"] == {
            "red": "aggressive",
            "blue": "defensive",
        }

        raw = json.loads(store_path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 1
        assert "crossroads-baseline" in raw["scenarios"]

        loaded = sc.get_scenario("crossroads-baseline", path=store_path)
        assert loaded == saved
        listed = sc.list_scenarios(path=store_path)
        assert len(listed) == 1
        assert listed[0]["id"] == "crossroads-baseline"

    def test_update_increments_version_and_preserves_history(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        v1 = sc.save_scenario(
            {
                "id": "duel-core",
                "name": "Duel Core",
                "factor_note": "v1 fighter kit",
                "config": _base_config(kit="fighter"),
            },
            path=store_path,
        )
        v2 = sc.save_scenario(
            {
                "id": "duel-core",
                "name": "Duel Core",
                "factor_note": "v2 switched to archer kit",
                "config": _base_config(kit="archer"),
            },
            path=store_path,
        )
        v3 = sc.save_scenario(
            {
                "id": "duel-core",
                "name": "Duel Core Revised",
                "factor_note": "v3 added 2 respawns",
                "config": _base_config(
                    kit="archer", respawn_mode="player", respawn_count=2
                ),
            },
            path=store_path,
        )

        assert v1["version"] == 1
        assert v2["version"] == 2
        assert v3["version"] == 3
        assert v3["created_at"] == v1["created_at"]
        assert len(v3["history"]) == 2
        assert v3["history"][0]["version"] == 1
        assert v3["history"][0]["config"]["kit"] == "fighter"
        assert v3["history"][0]["factor_note"] == "v1 fighter kit"
        assert v3["history"][1]["version"] == 2
        assert v3["history"][1]["config"]["kit"] == "archer"
        assert v3["history"][1]["factor_note"] == "v2 switched to archer kit"

        snap1 = sc.get_scenario_version("duel-core", 1, path=store_path)
        assert snap1["version"] == 1
        assert snap1["config"]["kit"] == "fighter"
        snap3 = sc.get_scenario_version("duel-core", 3, path=store_path)
        assert snap3["version"] == 3
        assert snap3["config"]["respawn_count"] == 2

        with pytest.raises(sc.ScenarioNotFoundError):
            sc.get_scenario_version("duel-core", 99, path=store_path)

    def test_delete_scenario_removes_entry_and_missing_raises(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        sc.save_scenario(
            {"id": "temp-a", "name": "Temp A", "config": _base_config()},
            path=store_path,
        )
        res = sc.delete_scenario("temp-a", path=store_path)
        assert res == {"deleted": True, "id": "temp-a"}
        assert sc.list_scenarios(path=store_path) == []
        with pytest.raises(sc.ScenarioNotFoundError):
            sc.get_scenario("temp-a", path=store_path)
        with pytest.raises(sc.ScenarioNotFoundError):
            sc.delete_scenario("temp-a", path=store_path)

    @pytest.mark.parametrize(
        "bad_id",
        ["", "UPPER", "has space", "../escape", "-leading-dash", "a" * 65],
    )
    def test_invalid_slug_rejected(self, tmp_path: Path, bad_id: str):
        store_path = tmp_path / "arena-scenarios.json"
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario(
                {"id": bad_id, "name": "Valid Name", "config": _base_config()},
                path=store_path,
            )

    def test_invalid_payload_and_config_rejected(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario({"name": "   ", "config": _base_config()}, path=store_path)
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario({"name": "No Config"}, path=store_path)
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario(
                {"name": "Bad Respawn", "config": _base_config(respawn_mode="infinite")},
                path=store_path,
            )
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario(
                {"name": "Negative Respawn", "config": _base_config(respawn_count=-1)},
                path=store_path,
            )
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario(
                {"name": "Zero Count", "config": _base_config(count=0)},
                path=store_path,
            )
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario(
                {"name": "Zero Timeout", "config": _base_config(timeout=0)},
                path=store_path,
            )
        with pytest.raises(sc.ScenarioError):
            sc.save_scenario(
                {"name": "Bad Game", "config": _base_config(game="capture_flag")},
                path=store_path,
            )

    def test_atomic_save_retries_permission_error_on_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        store_path = tmp_path / "arena-scenarios.json"
        real_replace = sc.os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked by reader")
            return real_replace(src, dst)

        monkeypatch.setattr(sc.os, "replace", flaky_replace)
        monkeypatch.setattr(sc.time, "sleep", lambda _s: None)

        saved = sc.save_scenario(
            {"id": "retry-test", "name": "Retry Test", "config": _base_config()},
            path=store_path,
        )
        assert saved["id"] == "retry-test"
        assert calls["n"] == 3
        assert store_path.is_file()

    def test_corrupt_store_raises_scenario_error(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        store_path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(sc.ScenarioError):
            sc.list_scenarios(path=store_path)


# --- Duplicate, edit one factor & diff_configs -------------------------------


class TestDuplicateAndDiff:
    def test_duplicate_applies_scalar_and_dot_path_edits_and_records_diff(
        self, tmp_path: Path
    ):
        store_path = tmp_path / "arena-scenarios.json"
        base = sc.save_scenario(
            {"id": "base-1", "name": "Base 1", "config": _base_config()},
            path=store_path,
        )
        variant = sc.duplicate_scenario(
            "base-1",
            name="Base 1 Archer Red",
            factor_note="Give red team archer kit",
            edits={"team_kits.red": "archer", "respawn_count": 2},
            path=store_path,
        )
        assert variant["id"] == "base-1-archer-red"
        assert variant["version"] == 1
        assert variant["parent_id"] == "base-1"
        assert variant["factor_note"] == "Give red team archer kit"
        assert variant["config"]["team_kits"]["red"] == "archer"
        assert variant["config"]["team_kits"]["blue"] == "tank"
        assert variant["config"]["respawn_count"] == 2
        # Parent must not be mutated
        reloaded_base = sc.get_scenario("base-1", path=store_path)
        assert reloaded_base["config"]["team_kits"]["red"] == "fighter"
        assert reloaded_base["config"]["respawn_count"] == 0
        assert reloaded_base == base

        fields = {d["field"]: d for d in variant["changed_fields"]}
        assert set(fields) == {"team_kits.red", "respawn_count"}
        assert fields["team_kits.red"] == {
            "field": "team_kits.red",
            "baseline": "fighter",
            "variant": "archer",
        }
        assert fields["respawn_count"] == {
            "field": "respawn_count",
            "baseline": 0,
            "variant": 2,
        }

    def test_duplicate_supports_nested_dict_edits_and_slug_deduplication(
        self, tmp_path: Path
    ):
        store_path = tmp_path / "arena-scenarios.json"
        sc.save_scenario(
            {"id": "base", "name": "Base", "config": _base_config()},
            path=store_path,
        )
        dup1 = sc.duplicate_scenario(
            "base",
            name="Variant",
            edits={"team_mobs": {"red": {"minecraft:skeleton": 4}}},
            path=store_path,
        )
        dup2 = sc.duplicate_scenario(
            "base",
            name="Variant",
            edits={"kit": "tank"},
            path=store_path,
        )
        assert dup1["id"] == "variant"
        assert dup2["id"] == "variant-2"
        assert dup1["config"]["team_mobs"]["red"]["minecraft:skeleton"] == 4
        assert dup1["config"]["team_mobs"]["blue"]["minecraft:zombie"] == 3

    def test_diff_configs_detects_added_removed_and_changed_fields(self):
        a = _base_config(kit="fighter", waves=[])
        b = _base_config(
            kit="archer",
            waves=[{"mobs": {"minecraft:zombie": 3}, "delay_ticks": 60}],
            team_kits={"red": "fighter", "blue": "skirmisher", "green": "tank"},
        )
        diffs = sc.diff_configs(a, b)
        by_field = {d["field"]: d for d in diffs}
        assert set(by_field) == {"kit", "waves", "team_kits.blue", "team_kits.green"}
        assert by_field["team_kits.green"] == {
            "field": "team_kits.green",
            "baseline": None,
            "variant": "tank",
        }
        assert sc.diff_configs(a, a) == []


# --- Side swapping -----------------------------------------------------------


class TestSideSwapping:
    def test_swap_sides_two_teams_rotates_all_team_keyed_fields_preserves_rules(self):
        cfg = _base_config()
        swapped = sc.swap_sides(cfg)

        assert swapped["teams"] == {"red": ["MCBot2"], "blue": ["MCBot"]}
        assert swapped["team_mobs"] == {
            "red": {"minecraft:zombie": 3},
            "blue": {"minecraft:skeleton": 2},
        }
        assert swapped["team_mob_kits"] == {
            "red": {"minecraft:zombie": "fighter"},
            "blue": {"minecraft:skeleton": "archer"},
        }
        assert swapped["team_kits"] == {"red": "tank", "blue": "fighter"}
        assert swapped["bot_profiles"] == {"red": "defensive", "blue": "aggressive"}

        # Global rules preserved unchanged
        assert swapped["arena"] == cfg["arena"]
        assert swapped["game"] == cfg["game"]
        assert swapped["waves"] == cfg["waves"]
        assert swapped["respawn_mode"] == cfg["respawn_mode"]
        assert swapped["respawn_count"] == cfg["respawn_count"]
        assert swapped["kit"] == cfg["kit"]
        assert swapped["kit_mode"] == cfg["kit_mode"]
        assert swapped["kit_pool"] == cfg["kit_pool"]
        assert swapped["count"] == cfg["count"]
        assert swapped["timeout"] == cfg["timeout"]

        # Swapping twice returns original config
        assert sc.swap_sides(swapped) == cfg

    def test_swap_sides_four_teams_cyclic_shift(self):
        cfg = _base_config(
            teams={
                "red": ["A"],
                "blue": ["B"],
                "green": ["C"],
                "yellow": ["D"],
            },
            team_kits={"red": "k1", "blue": "k2", "green": "k3", "yellow": "k4"},
            team_mobs={
                "red": {"minecraft:zombie": 1},
                "blue": {"minecraft:skeleton": 2},
                "green": {"minecraft:pillager": 3},
                "yellow": {"minecraft:witch": 1},
            },
            team_mob_kits={},
            bot_profiles={"A": "aggro", "B": "cautious"},  # keyed by bot name!
        )
        mapping = sc.side_swap_mapping(
            cfg, arena_teams=["red", "blue", "green", "yellow"]
        )
        assert mapping == {
            "red": "blue",
            "blue": "green",
            "green": "yellow",
            "yellow": "red",
        }
        swapped = sc.swap_sides(cfg, arena_teams=["red", "blue", "green", "yellow"])
        assert swapped["teams"] == {
            "blue": ["A"],
            "green": ["B"],
            "yellow": ["C"],
            "red": ["D"],
        }
        assert swapped["team_kits"] == {
            "blue": "k1",
            "green": "k2",
            "yellow": "k3",
            "red": "k4",
        }
        # Bot-keyed profiles must stay attached to their bot names
        assert swapped["bot_profiles"] == {"A": "aggro", "B": "cautious"}

    def test_swap_sides_rejects_fewer_than_two_teams(self):
        pve = _base_config(
            game="waves",
            teams={},
            team_mobs={},
            team_mob_kits={},
            team_kits={},
            bot_profiles={},
        )
        with pytest.raises(sc.ScenarioError):
            sc.swap_sides(pve)
        one_team = _base_config(
            game="teams",
            teams={"red": ["MCBot"]},
            team_mobs={},
            team_mob_kits={},
            team_kits={},
            bot_profiles={},
        )
        with pytest.raises(sc.ScenarioError):
            sc.swap_sides(one_team)

    def test_create_swapped_scenario_links_provenance(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        sc.save_scenario(
            {"id": "xroads", "name": "Crossroads", "config": _base_config()},
            path=store_path,
        )
        swapped = sc.create_swapped_scenario("xroads", path=store_path)
        assert swapped["swapped_from"] == "xroads"
        assert swapped["parent_id"] == "xroads"
        assert swapped["version"] == 1
        assert swapped["config"]["teams"] == {"red": ["MCBot2"], "blue": ["MCBot"]}
        assert swapped["side_mapping"] == {"red": "blue", "blue": "red"}
        assert len(swapped["changed_fields"]) > 0


# --- Series expansion --------------------------------------------------------


class TestExpandComparisonJobs:
    def test_expand_comparison_jobs_with_repetitions_and_side_swap(
        self, tmp_path: Path
    ):
        store_path = tmp_path / "arena-scenarios.json"
        sc.save_scenario(
            {"id": "base", "name": "Base", "config": _base_config(kit="fighter")},
            path=store_path,
        )
        sc.duplicate_scenario(
            "base",
            name="Var",
            new_id="var",
            edits={"kit": "archer"},
            path=store_path,
        )
        jobs = sc.expand_comparison_jobs(
            "base", "var", repetitions=2, include_side_swap=True, path=store_path
        )
        assert len(jobs) == 8  # 2 reps * (base, var, base-swapped, var-swapped)
        assert [
            (j["repetition"], j["role"], j["swapped"], j["scenario_id"]) for j in jobs
        ] == [
            (1, "baseline", False, "base"),
            (1, "variant", False, "var"),
            (1, "baseline", True, "base"),
            (1, "variant", True, "var"),
            (2, "baseline", False, "base"),
            (2, "variant", False, "var"),
            (2, "baseline", True, "base"),
            (2, "variant", True, "var"),
        ]
        # Verify swapped jobs actually rotated teams and recorded side_mapping
        assert jobs[0]["config"]["teams"] == {"red": ["MCBot"], "blue": ["MCBot2"]}
        assert jobs[2]["config"]["teams"] == {"red": ["MCBot2"], "blue": ["MCBot"]}
        assert jobs[2]["side_mapping"] == {"red": "blue", "blue": "red"}
        assert jobs[1]["config"]["kit"] == "archer"
        assert jobs[3]["config"]["kit"] == "archer"
        assert jobs[3]["scenario_version"] == 1

    def test_expand_comparison_jobs_without_side_swap_and_validation(
        self, tmp_path: Path
    ):
        store_path = tmp_path / "arena-scenarios.json"
        sc.save_scenario(
            {"id": "base", "name": "Base", "config": _base_config()},
            path=store_path,
        )
        sc.save_scenario(
            {"id": "var", "name": "Var", "config": _base_config(kit="archer")},
            path=store_path,
        )
        jobs = sc.expand_comparison_jobs(
            "base", "var", repetitions=3, include_side_swap=False, path=store_path
        )
        assert len(jobs) == 6
        assert all(not j["swapped"] for j in jobs)

        with pytest.raises(sc.ScenarioError):
            sc.expand_comparison_jobs("base", "var", repetitions=0, path=store_path)


# --- Side-by-side comparison aggregator --------------------------------------


class TestComparisonAggregator:
    def test_compare_runs_separates_completed_failed_and_abandoned_and_normalizes_swaps(
        self,
    ):
        baseline_runs = [
            # Unswapped: red wins
            {
                "path": "results/companion/arena/20261127T100000Z.json",
                "run_id": "base-1",
                "when": "2026-11-27T10:00:00Z",
                "swapped": False,
                "scenario_version": 1,
                **_match_row(
                    outcome="won",
                    winner="red",
                    seconds=40.0,
                    players={
                        "MCBot": {
                            "team": "red",
                            "player_kills": 1,
                            "mob_kills": 2,
                            "damage_dealt": 50.0,
                            "damage_taken": 20.0,
                        },
                        "MCBot2": {
                            "team": "blue",
                            "player_kills": 0,
                            "mob_kills": 1,
                            "damage_dealt": 20.0,
                            "damage_taken": 50.0,
                        },
                    },
                ),
            },
            # Swapped: physical blue (= logical red) wins!
            {
                "path": "results/companion/arena/20261127T100500Z.json",
                "run_id": "base-2",
                "when": "2026-11-27T10:05:00Z",
                "swapped": True,
                "side_mapping": {"red": "blue", "blue": "red"},
                "scenario_version": 1,
                **_match_row(
                    outcome="won",
                    winner="blue",
                    seconds=60.0,
                    players={
                        "MCBot": {
                            "team": "blue",
                            "player_kills": 1,
                            "mob_kills": 0,
                            "damage_dealt": 44.0,
                            "damage_taken": 16.0,
                        },
                        "MCBot2": {
                            "team": "red",
                            "player_kills": 0,
                            "mob_kills": 0,
                            "damage_dealt": 16.0,
                            "damage_taken": 44.0,
                        },
                    },
                ),
            },
            # Failed start (refused) - MUST NOT pollute win_rate or duration
            {
                "path": "results/companion/arena/20261127T101000Z.json",
                "run_id": "base-3",
                "when": "2026-11-27T10:10:00Z",
                "swapped": False,
                "scenario_version": 1,
                **_match_row(
                    outcome="refused",
                    winner=None,
                    reason=None,
                    seconds=0.2,
                    error="bot refused attack job",
                ),
            },
            # Abandoned start (timeout) - MUST NOT pollute win_rate or duration
            {
                "path": "results/companion/arena/20261127T101500Z.json",
                "run_id": "base-4",
                "when": "2026-11-27T10:15:00Z",
                "swapped": False,
                "scenario_version": 1,
                **_match_row(
                    outcome="timeout",
                    winner=None,
                    reason="time_limit",
                    seconds=300.0,
                ),
            },
        ]

        variant_runs = [
            # Unswapped: blue wins
            {
                "path": "results/companion/arena/20261127T110000Z.json",
                "run_id": "var-1",
                "when": "2026-11-27T11:00:00Z",
                "swapped": False,
                "scenario_version": 1,
                **_match_row(
                    outcome="won",
                    winner="blue",
                    seconds=30.0,
                    players={
                        "MCBot": {
                            "team": "red",
                            "player_kills": 0,
                            "mob_kills": 0,
                            "damage_dealt": 15.0,
                            "damage_taken": 60.0,
                        },
                        "MCBot2": {
                            "team": "blue",
                            "player_kills": 1,
                            "mob_kills": 1,
                            "damage_dealt": 60.0,
                            "damage_taken": 15.0,
                        },
                    },
                ),
            },
            # Draw round (outcome=lost, reason=draw)
            {
                "path": "results/companion/arena/20261127T110500Z.json",
                "run_id": "var-2",
                "when": "2026-11-27T11:05:00Z",
                "swapped": True,
                "side_mapping": {"red": "blue", "blue": "red"},
                "scenario_version": 1,
                **_match_row(
                    outcome="lost",
                    winner=None,
                    reason="draw",
                    seconds=50.0,
                    players={
                        "MCBot": {
                            "team": "blue",
                            "player_kills": 1,
                            "mob_kills": 0,
                            "damage_dealt": 30.0,
                            "damage_taken": 30.0,
                        },
                        "MCBot2": {
                            "team": "red",
                            "player_kills": 1,
                            "mob_kills": 0,
                            "damage_dealt": 30.0,
                            "damage_taken": 30.0,
                        },
                    },
                ),
            },
            # Failed start (not_started)
            {
                "path": "results/companion/arena/20261127T111000Z.json",
                "run_id": "var-3",
                "when": "2026-11-27T11:10:00Z",
                "swapped": False,
                "scenario_version": 1,
                **_match_row(
                    outcome="not_started",
                    winner=None,
                    reason=None,
                    seconds=None,
                    error="kit missing",
                ),
            },
            # Abandoned start (stopped)
            {
                "path": "results/companion/arena/20261127T111500Z.json",
                "run_id": "var-4",
                "when": "2026-11-27T11:15:00Z",
                "swapped": True,
                "scenario_version": 1,
                **_match_row(
                    outcome="stopped",
                    winner=None,
                    reason="operator_stop",
                    seconds=12.0,
                ),
            },
        ]

        cmp = sc.compare_runs(
            baseline_runs,
            variant_runs,
            baseline_config=_base_config(kit="fighter"),
            variant_config=_base_config(kit="archer"),
        )

        # Changed fields
        assert [d["field"] for d in cmp["changed_fields"]] == ["kit"]

        # Baseline metrics: 2 completed, 1 failed, 1 abandoned
        b = cmp["baseline"]
        assert b["completed_rounds"] == 2
        assert b["failed_starts_count"] == 1
        assert b["abandoned_starts_count"] == 1
        assert b["total_runs"] == 4
        assert b["wins_by_team"] == {"red": 2, "blue": 0}
        assert b["win_rate_by_team"] == {"red": 1.0, "blue": 0.0}
        assert b["draws"] == 0
        assert b["median_duration_s"] == 50.0
        assert b["mean_duration_s"] == 50.0
        # Damage normalized back to logical team:
        # red = 50 (unswapped red) + 44 (swapped physical blue) = 94
        # blue = 20 (unswapped blue) + 16 (swapped physical red) = 36
        assert b["damage_dealt_by_team"] == {"red": 94.0, "blue": 36.0}
        assert b["damage_taken_by_team"] == {"red": 36.0, "blue": 94.0}
        assert b["kills_by_team"] == {"red": 4, "blue": 1}

        # Per-side breakdown
        assert b["per_side_breakdown"]["unswapped"]["completed_rounds"] == 1
        assert b["per_side_breakdown"]["unswapped"]["wins_by_team"]["red"] == 1
        assert b["per_side_breakdown"]["swapped"]["completed_rounds"] == 1
        assert b["per_side_breakdown"]["swapped"]["wins_by_team"]["red"] == 1

        # Failed and abandoned details preserved with path and reason
        assert len(b["failed_starts"]) == 1
        assert b["failed_starts"][0]["outcome"] == "refused"
        assert b["failed_starts"][0]["reason"] == "bot refused attack job"
        assert b["failed_starts"][0]["path"] == "results/companion/arena/20261127T101000Z.json"
        assert len(b["abandoned_starts"]) == 1
        assert b["abandoned_starts"][0]["outcome"] == "timeout"
        assert b["abandoned_starts"][0]["reason"] == "time_limit"

        # Variant metrics: 2 completed (1 blue win, 1 draw), 1 failed, 1 abandoned
        v = cmp["variant"]
        assert v["completed_rounds"] == 2
        assert v["draws"] == 1
        assert v["wins_by_team"] == {"blue": 1, "red": 0}
        assert v["win_rate_by_team"] == {"blue": 0.5, "red": 0.0}
        assert v["failed_starts_count"] == 1
        assert v["abandoned_starts_count"] == 1
        assert v["median_duration_s"] == 40.0
        assert v["damage_dealt_by_team"] == {"red": 45.0, "blue": 90.0}

        # Deltas (variant - baseline)
        assert cmp["deltas"]["median_duration_s"] == -10.0
        assert cmp["deltas"]["win_rate_by_team"]["red"] == -1.0
        assert cmp["deltas"]["win_rate_by_team"]["blue"] == 0.5
        assert cmp["deltas"]["damage_dealt_by_team"]["blue"] == 54.0

    def test_compare_scenarios_reads_disk_artifacts_and_handles_top_level_errors(
        self, tmp_path: Path
    ):
        store_path = tmp_path / "runtime" / "arena-scenarios.json"
        results_dir = tmp_path / "results" / "companion" / "arena"

        sc.save_scenario(
            {"id": "base-s", "name": "Base S", "config": _base_config(kit="fighter")},
            path=store_path,
        )
        sc.duplicate_scenario(
            "base-s",
            name="Var S",
            new_id="var-s",
            edits={"kit": "archer"},
            path=store_path,
        )

        # Series file for baseline with 2 matches (1 completed, 1 interrupted)
        _write_series(
            results_dir,
            "20261127T120000Z.json",
            scenario_id="base-s",
            scenario_version=1,
            swapped=False,
            runs=[
                _match_row(
                    outcome="won",
                    winner="red",
                    seconds=25.0,
                    players={
                        "MCBot": {"team": "red", "damage_dealt": 40.0, "damage_taken": 10.0, "player_kills": 1},
                        "MCBot2": {"team": "blue", "damage_dealt": 10.0, "damage_taken": 40.0, "player_kills": 0},
                    },
                ),
                _match_row(
                    outcome="interrupted",
                    winner=None,
                    reason="killed_by_user",
                    seconds=5.0,
                ),
            ],
        )
        # Top-level failed series file (zero runs, top-level error)
        _write_series(
            results_dir,
            "20261127T120500Z.json",
            scenario_id="base-s",
            scenario_version=1,
            runs=[],
            error="arena server crashed before match start",
        )
        # Series file for variant (swapped, without explicit side_mapping -> inferred!)
        _write_series(
            results_dir,
            "20261127T121000Z.json",
            scenario_id="var-s",
            scenario_version=1,
            swapped=True,
            teams={"red": ["MCBot2"], "blue": ["MCBot"]},
            runs=[
                _match_row(
                    outcome="won",
                    winner="red",  # physical red = logical blue when swapped
                    seconds=35.0,
                    players={
                        "MCBot2": {"team": "red", "damage_dealt": 55.0, "damage_taken": 12.0, "player_kills": 1},
                        "MCBot": {"team": "blue", "damage_dealt": 12.0, "damage_taken": 55.0, "player_kills": 0},
                    },
                ),
            ],
        )

        cmp = sc.compare_scenarios(
            "base-s",
            "var-s",
            results_dir=results_dir,
            root=tmp_path,
            path=store_path,
        )
        assert cmp["baseline"]["scenario"]["id"] == "base-s"
        assert cmp["variant"]["scenario"]["id"] == "var-s"
        assert cmp["baseline"]["completed_rounds"] == 1
        assert cmp["baseline"]["failed_starts_count"] == 1
        assert cmp["baseline"]["abandoned_starts_count"] == 1
        assert cmp["baseline"]["failed_starts"][0]["path"] == (
            "results/companion/arena/20261127T120500Z.json"
        )
        assert "crashed" in cmp["baseline"]["failed_starts"][0]["reason"]

        assert cmp["variant"]["completed_rounds"] == 1
        assert cmp["variant"]["wins_by_team"] == {"blue": 1, "red": 0}
        assert cmp["variant"]["completed_runs"][0]["winner"] == "red"
        assert cmp["variant"]["completed_runs"][0]["logical_winner"] == "blue"
        assert cmp["variant"]["completed_runs"][0]["swapped"] is True

    def test_compare_scenarios_filters_by_version_and_explicit_paths(
        self, tmp_path: Path
    ):
        store_path = tmp_path / "runtime" / "arena-scenarios.json"
        results_dir = tmp_path / "results" / "companion" / "arena"

        sc.save_scenario(
            {"id": "s1", "name": "S1", "config": _base_config(kit="fighter")},
            path=store_path,
        )
        sc.save_scenario(
            {"id": "s1", "name": "S1", "config": _base_config(kit="tank")},
            path=store_path,
        )
        p1 = _write_series(
            results_dir,
            "20261127T130000Z.json",
            scenario_id="s1",
            scenario_version=1,
            runs=[_match_row(outcome="won", winner="red", seconds=20.0)],
        )
        p2 = _write_series(
            results_dir,
            "20261127T130500Z.json",
            scenario_id="s1",
            scenario_version=2,
            runs=[_match_row(outcome="won", winner="blue", seconds=80.0)],
        )

        # Version filtering: compare s1 v1 vs s1 v2!
        cmp_ver = sc.compare_scenarios(
            "s1",
            "s1",
            baseline_version=1,
            variant_version=2,
            results_dir=results_dir,
            root=tmp_path,
            path=store_path,
        )
        assert cmp_ver["baseline"]["completed_rounds"] == 1
        assert cmp_ver["baseline"]["wins_by_team"]["red"] == 1
        assert cmp_ver["variant"]["completed_rounds"] == 1
        assert cmp_ver["variant"]["wins_by_team"]["blue"] == 1
        assert [d["field"] for d in cmp_ver["changed_fields"]] == ["kit"]

        # Explicit file paths override
        cmp_paths = sc.compare_scenarios(
            "s1",
            "s1",
            baseline_paths=[p1],
            variant_paths=[p2],
            root=tmp_path,
            path=store_path,
        )
        assert cmp_paths["baseline"]["median_duration_s"] == 20.0
        assert cmp_paths["variant"]["median_duration_s"] == 80.0

    def test_edge_cases_coop_runs_empty_comparison_and_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        store_path = tmp_path / "arena-scenarios.json"
        # PvE co-op scenario & job expansion skips swap gracefully
        pve_cfg = _base_config(
            arena="vault",
            game="waves",
            teams={},
            team_mobs={},
            team_mob_kits={},
            team_kits={},
            bot_profiles={},
            waves=[{"mobs": {"minecraft:zombie": 3}, "delay_ticks": 60}],
        )
        sc.save_scenario(
            {"id": "pve-base", "name": "PvE Base", "config": pve_cfg},
            path=store_path,
        )
        sc.duplicate_scenario(
            "pve-base",
            name="PvE Fast",
            new_id="pve-fast",
            edits={"timeout": 120},
            path=store_path,
        )
        jobs = sc.expand_comparison_jobs(
            "pve-base", "pve-fast", repetitions=1, include_side_swap=True, path=store_path
        )
        assert len(jobs) == 2
        assert all(not j["swapped"] for j in jobs)

        # Duplicate with colliding explicit new_id fails
        with pytest.raises(sc.ScenarioError):
            sc.duplicate_scenario(
                "pve-base", name="Dup", new_id="pve-fast", path=store_path
            )

        # Co-op summary + empty comparison deltas + team_kills fallback
        coop_runs = [
            {
                "path": "results/companion/arena/coop1.json",
                "run_id": "coop-1",
                "outcome": "won",
                "seconds": 30.0,
                "players": {"MCBot": {"mob_kills": 5, "damage_dealt": 80.0, "damage_taken": 12.0}},
            },
            {
                "path": "results/companion/arena/coop2.json",
                "run_id": "coop-2",
                "outcome": "lost",
                "reason": "participants_down",
                "seconds": 20.0,
                "team_kills": {"coop": 2},
            },
            {
                "path": "results/companion/arena/coop3.json",
                "run_id": "coop-3",
                "outcome": None,
                "error": "spawn failed",
            },
            {
                "path": "results/companion/arena/coop4.json",
                "run_id": "coop-4",
                "outcome": "unknown_status",
            },
        ]
        cmp_empty = sc.compare_runs(coop_runs, [])
        assert cmp_empty["baseline"]["coop_wins"] == 1
        assert cmp_empty["baseline"]["completed_rounds"] == 2
        assert cmp_empty["baseline"]["failed_starts_count"] == 1
        assert cmp_empty["baseline"]["abandoned_starts_count"] == 1
        assert cmp_empty["variant"]["completed_rounds"] == 0
        assert cmp_empty["deltas"]["median_duration_s"] is None

        # Exhausted PermissionError retries propagate and clean up temp file
        def always_locked(_src, _dst):
            raise PermissionError("permanently locked")

        monkeypatch.setattr(sc.os, "replace", always_locked)
        monkeypatch.setattr(sc.time, "sleep", lambda _s: None)
        with pytest.raises(PermissionError):
            sc.save_scenario(
                {"id": "locked-test", "name": "Locked", "config": pve_cfg},
                path=store_path,
            )
        assert list(tmp_path.glob("*.tmp")) == []

    def test_diff_removed_keys_and_additional_validation_paths(self, tmp_path: Path):
        store_path = tmp_path / "arena-scenarios.json"
        # Removed nested key in diff_configs
        diffs = sc.diff_configs(
            {"team_kits": {"red": "fighter", "blue": "tank"}},
            {"team_kits": {"red": "fighter"}},
        )
        assert diffs == [{"field": "team_kits.blue", "baseline": "tank", "variant": None}]

        # Invalid store schema
        store_path.write_text(json.dumps({"schema_version": 99, "scenarios": {}}), encoding="utf-8")
        with pytest.raises(sc.ScenarioError):
            sc.list_scenarios(path=store_path)

        store_path.unlink()
        # Additional config validation errors
        for bad_cfg in [
            "not-a-dict",
            _base_config(arena=""),
            _base_config(teams="bad"),
            _base_config(teams={"": ["MCBot"]}),
            _base_config(teams={"red": [123]}),
            _base_config(team_mobs="bad"),
            _base_config(waves=123),
            _base_config(kit_mode="invalid_mode"),
            _base_config(kit_pool="not-a-list"),
        ]:
            with pytest.raises(sc.ScenarioError):
                sc.normalize_config(bad_cfg)

        # Invalid version query
        sc.save_scenario(
            {"id": "vcheck", "name": "VCheck", "config": _base_config()},
            path=store_path,
        )
        with pytest.raises(sc.ScenarioError):
            sc.get_scenario_version("vcheck", 0, path=store_path)

        # create_swapped_scenario with explicit colliding new_id fails
        sc.create_swapped_scenario("vcheck", new_id="vcheck-swap", path=store_path)
        with pytest.raises(sc.ScenarioError):
            sc.create_swapped_scenario("vcheck", new_id="vcheck-swap", path=store_path)

