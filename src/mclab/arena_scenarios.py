"""Named, versioned arena scenarios, side-swapping, series expansion, and comparison.

Persists arena experiment definitions in ``runtime/arena-scenarios.json``
(schema_version 1) with atomic temp-file replacement and full version history so
every saved configuration, single-factor duplicate, and spawn-side swap preserves
provenance.

Comparison aggregation (``compare_runs`` / ``compare_scenarios``) strictly separates:
- **completed rounds** (``won``, ``lost``, ``draw``): denominator for win rates,
  median/mean duration, kills, and damage dealt/taken (normalized back to logical
  lineups when spawn sides were swapped).
- **failed starts** (``refused``, ``not_started``, ``error``, or top-level series
  failure): counted and listed separately with reason and artifact path.
- **abandoned starts** (``stopped``, ``timeout``, ``interrupted``, ``killed``,
  ``abandoned``): counted and listed separately with reason and artifact path.
"""

from __future__ import annotations

import copy
import json
import os
import re
import statistics
import threading
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mclab.arena.plan import TEAM_NAMES
from mclab.paths import ROOT

SCHEMA_VERSION = 1
DEFAULT_STORE_PATH = ROOT / "runtime/arena-scenarios.json"
DEFAULT_RESULTS_DIR = ROOT / "results/companion/arena"
SAVE_RETRIES = 40
SAVE_RETRY_SLEEP_S = 0.02

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
STAMP_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$")

VALID_GAMES = frozenset({"teams", "waves", "race"})
VALID_RESPAWN_MODES = frozenset({"elimination", "player", "team"})
VALID_KIT_MODES = frozenset(
    {
        "uniform",
        "same",
        "per_team",
        "class_picker",
        "everyone_random",
        "individual_random",
        "random_shared",
        "shared_random",
    }
)

COMPLETED_OUTCOMES = frozenset({"won", "lost", "draw"})
FAILED_START_OUTCOMES = frozenset({"refused", "not_started", "error"})
ABANDONED_START_OUTCOMES = frozenset(
    {"stopped", "timeout", "interrupted", "killed", "abandoned"}
)
KNOWN_TEAM_NAMES = frozenset(TEAM_NAMES)
TEAM_KEYED_FIELDS = ("teams", "team_mobs", "team_kits", "team_mob_kits")

_STORE_LOCK = threading.RLock()


class ScenarioError(ValueError):
    """Domain validation or persistence error for arena scenarios."""


class ScenarioNotFoundError(KeyError):
    """Raised when a requested scenario id or version does not exist."""


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64]
    if not slug or not SLUG_RE.fullmatch(slug):
        raise ScenarioError(f"Could not derive a valid scenario id from {text!r}")
    return slug


def _validate_slug(scenario_id: Any) -> str:
    if not isinstance(scenario_id, str) or not SLUG_RE.fullmatch(scenario_id):
        raise ScenarioError(
            "Scenario id must be 1..64 lowercase letters, digits, hyphens, or "
            f"underscores starting with a letter/digit, got {scenario_id!r}"
        )
    return scenario_id


def _validate_name(name: Any) -> str:
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
        raise ScenarioError("Scenario name must be 1..128 non-empty characters")
    return name.strip()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def normalize_config(raw: Any) -> dict[str, Any]:
    """Validate and normalize an arena scenario config snapshot."""
    if not isinstance(raw, Mapping):
        raise ScenarioError("Scenario config must be a JSON object")
    cfg = copy.deepcopy(dict(raw))

    arena = cfg.get("arena", "crossroads")
    if not isinstance(arena, str) or not arena.strip():
        raise ScenarioError("config.arena must be a non-empty string")
    cfg["arena"] = arena.strip()

    game = cfg.get("game", "teams")
    if not isinstance(game, str) or game not in VALID_GAMES:
        allowed = ", ".join(sorted(VALID_GAMES))
        raise ScenarioError(f"config.game must be one of {allowed}, got {game!r}")
    cfg["game"] = game

    teams = cfg.get("teams")
    if teams is None:
        teams = {}
    if not isinstance(teams, dict):
        raise ScenarioError("config.teams must be an object mapping team -> players")
    norm_teams: dict[str, list[str]] = {}
    for team, members in teams.items():
        if not isinstance(team, str) or not team.strip():
            raise ScenarioError(f"Invalid team key {team!r}")
        if not isinstance(members, list) or any(
            not isinstance(m, str) or not m.strip() for m in members
        ):
            raise ScenarioError(
                f"config.teams[{team!r}] must be a list of player/bot names"
            )
        norm_teams[team.strip()] = [m.strip() for m in members]
    cfg["teams"] = norm_teams

    for dict_field in ("team_mobs", "team_mob_kits", "team_kits", "bot_profiles"):
        val = cfg.get(dict_field)
        if val is None:
            cfg[dict_field] = {}
        elif not isinstance(val, dict):
            raise ScenarioError(f"config.{dict_field} must be an object")

    waves = cfg.get("waves")
    if waves is None:
        cfg["waves"] = []
    elif not isinstance(waves, (list, str)):
        raise ScenarioError("config.waves must be a list of wave specs or preset name")

    respawn_mode = cfg.get("respawn_mode", "elimination")
    if not isinstance(respawn_mode, str) or respawn_mode not in VALID_RESPAWN_MODES:
        allowed = ", ".join(sorted(VALID_RESPAWN_MODES))
        raise ScenarioError(
            f"config.respawn_mode must be one of {allowed}, got {respawn_mode!r}"
        )
    cfg["respawn_mode"] = respawn_mode

    respawn_count = cfg.get("respawn_count", 0)
    if not _is_int(respawn_count) or respawn_count < 0:
        raise ScenarioError("config.respawn_count must be an integer >= 0")
    cfg["respawn_count"] = respawn_count

    if "kit" not in cfg:
        cfg["kit"] = None

    kit_mode = cfg.get("kit_mode", "same")
    if not isinstance(kit_mode, str) or kit_mode not in VALID_KIT_MODES:
        allowed = ", ".join(sorted(VALID_KIT_MODES))
        raise ScenarioError(
            f"config.kit_mode must be one of {allowed}, got {kit_mode!r}"
        )
    cfg["kit_mode"] = kit_mode

    kit_pool = cfg.get("kit_pool")
    if kit_pool is None:
        cfg["kit_pool"] = []
    elif not isinstance(kit_pool, list) or any(
        not isinstance(k, str) for k in kit_pool
    ):
        raise ScenarioError("config.kit_pool must be a list of kit names")

    count = cfg.get("count", 1)
    if not _is_int(count) or count < 1:
        raise ScenarioError("config.count must be an integer >= 1")
    cfg["count"] = count

    timeout = cfg.get("timeout", 600)
    if not _is_number(timeout) or timeout <= 0:
        raise ScenarioError("config.timeout must be a number > 0")
    cfg["timeout"] = timeout

    return cfg


# --- Atomic store I/O --------------------------------------------------------


def _store_path(path: Path | str | None) -> Path:
    return DEFAULT_STORE_PATH if path is None else Path(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    tmp.write_text(encoded, encoding="utf-8")
    for attempt in range(SAVE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == SAVE_RETRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(SAVE_RETRY_SLEEP_S)


def _read_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "scenarios": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ScenarioError(f"Could not read scenario store {path}: {error}") from error
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != SCHEMA_VERSION
        or not isinstance(raw.get("scenarios"), dict)
    ):
        raise ScenarioError(f"Invalid scenario store schema in {path}")
    return raw


# --- CRUD & Versioning -------------------------------------------------------


def list_scenarios(*, path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return all saved scenarios ordered newest-updated first, then by id."""
    store_file = _store_path(path)
    with _STORE_LOCK:
        store = _read_store(store_file)
        items = [copy.deepcopy(v) for v in store["scenarios"].values()]
    items.sort(
        key=lambda s: (s.get("updated_at") or "", s.get("id") or ""),
        reverse=True,
    )
    return items


def get_scenario(scenario_id: str, *, path: Path | str | None = None) -> dict[str, Any]:
    """Return one saved scenario by slug id."""
    slug = _validate_slug(scenario_id)
    store_file = _store_path(path)
    with _STORE_LOCK:
        store = _read_store(store_file)
        entry = store["scenarios"].get(slug)
        if entry is None:
            raise ScenarioNotFoundError(f"Scenario {slug!r} not found")
        return copy.deepcopy(entry)


def get_scenario_version(
    scenario_id: str,
    version: int,
    *,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Return a specific version snapshot (current or historical) of a scenario."""
    if not _is_int(version) or version < 1:
        raise ScenarioError("version must be an integer >= 1")
    scenario = get_scenario(scenario_id, path=path)
    if scenario["version"] == version:
        return {
            "id": scenario["id"],
            "name": scenario["name"],
            "version": scenario["version"],
            "updated_at": scenario["updated_at"],
            "factor_note": scenario.get("factor_note", ""),
            "config": copy.deepcopy(scenario["config"]),
        }
    for snap in scenario.get("history") or ():
        if snap.get("version") == version:
            return {
                "id": scenario["id"],
                "name": scenario["name"],
                "version": snap["version"],
                "updated_at": snap.get("updated_at"),
                "factor_note": snap.get("factor_note", ""),
                "config": copy.deepcopy(snap["config"]),
            }
    raise ScenarioNotFoundError(
        f"Scenario {scenario_id!r} has no version {version}"
    )


def save_scenario(
    payload: Mapping[str, Any], *, path: Path | str | None = None
) -> dict[str, Any]:
    """Create a new v1 scenario or update an existing scenario with version bump."""
    if not isinstance(payload, Mapping):
        raise ScenarioError("Scenario payload must be an object")
    name = _validate_name(payload.get("name"))
    raw_id = payload.get("id")
    slug = _slugify(name) if raw_id is None else _validate_slug(raw_id)
    config = normalize_config(payload.get("config"))
    factor_note = str(payload.get("factor_note") or "").strip()
    parent_id = payload.get("parent_id")
    if parent_id is not None:
        parent_id = _validate_slug(parent_id)
    swapped_from = payload.get("swapped_from")
    if swapped_from is not None:
        swapped_from = _validate_slug(swapped_from)
    changed_fields = payload.get("changed_fields")
    side_mapping = payload.get("side_mapping")

    store_file = _store_path(path)
    with _STORE_LOCK:
        store = _read_store(store_file)
        existing = store["scenarios"].get(slug)
        now = _now_iso()
        if existing is None:
            record: dict[str, Any] = {
                "id": slug,
                "name": name,
                "version": 1,
                "created_at": now,
                "updated_at": now,
                "parent_id": parent_id,
                "factor_note": factor_note,
                "changed_fields": copy.deepcopy(list(changed_fields or [])),
                "swapped_from": swapped_from,
                "config": config,
                "history": [],
            }
            if isinstance(side_mapping, Mapping):
                record["side_mapping"] = dict(side_mapping)
        else:
            history = copy.deepcopy(existing.get("history") or [])
            history.append(
                {
                    "version": existing["version"],
                    "updated_at": existing["updated_at"],
                    "factor_note": existing.get("factor_note", ""),
                    "config": copy.deepcopy(existing["config"]),
                }
            )
            eff_parent = (
                parent_id if parent_id is not None else existing.get("parent_id")
            )
            eff_swapped = (
                swapped_from
                if swapped_from is not None
                else existing.get("swapped_from")
            )
            eff_changes = (
                copy.deepcopy(list(changed_fields))
                if changed_fields is not None
                else diff_configs(existing["config"], config)
            )
            record = {
                "id": slug,
                "name": name,
                "version": int(existing["version"]) + 1,
                "created_at": existing["created_at"],
                "updated_at": now,
                "parent_id": eff_parent,
                "factor_note": factor_note,
                "changed_fields": eff_changes,
                "swapped_from": eff_swapped,
                "config": config,
                "history": history,
            }
            if isinstance(side_mapping, Mapping):
                record["side_mapping"] = dict(side_mapping)
            elif "side_mapping" in existing:
                record["side_mapping"] = copy.deepcopy(existing["side_mapping"])
        store["scenarios"][slug] = record
        _atomic_write_json(store_file, store)
        return copy.deepcopy(record)


def delete_scenario(
    scenario_id: str, *, path: Path | str | None = None
) -> dict[str, Any]:
    """Delete a saved scenario by id."""
    slug = _validate_slug(scenario_id)
    store_file = _store_path(path)
    with _STORE_LOCK:
        store = _read_store(store_file)
        if slug not in store["scenarios"]:
            raise ScenarioNotFoundError(f"Scenario {slug!r} not found")
        del store["scenarios"][slug]
        _atomic_write_json(store_file, store)
    return {"deleted": True, "id": slug}


# --- Diffing & Duplicating (Edit One Factor) ---------------------------------


def diff_configs(
    baseline_config: Mapping[str, Any] | None,
    variant_config: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return sorted list of ``{field, baseline, variant}`` differences."""
    base = dict(baseline_config or {})
    var = dict(variant_config or {})
    diffs: list[dict[str, Any]] = []

    def _walk(prefix: str, left: Any, right: Any) -> None:
        if left == right:
            return
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            keys = sorted(set(left.keys()) | set(right.keys()))
            for key in keys:
                child = f"{prefix}.{key}" if prefix else str(key)
                if key not in left:
                    diffs.append(
                        {
                            "field": child,
                            "baseline": None,
                            "variant": copy.deepcopy(right[key]),
                        }
                    )
                elif key not in right:
                    diffs.append(
                        {
                            "field": child,
                            "baseline": copy.deepcopy(left[key]),
                            "variant": None,
                        }
                    )
                else:
                    _walk(child, left[key], right[key])
            return
        diffs.append(
            {
                "field": prefix,
                "baseline": copy.deepcopy(left),
                "variant": copy.deepcopy(right),
            }
        )

    _walk("", base, var)
    diffs.sort(key=lambda item: item["field"])
    return diffs


def _set_dot_path(target: dict[str, Any], dot_path: str, value: Any) -> None:
    parts = [p for p in dot_path.split(".") if p]
    if not parts:
        raise ScenarioError(f"Invalid edit path {dot_path!r}")
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = copy.deepcopy(value)


def _deep_merge(target: dict[str, Any], patch: Mapping[str, Any]) -> None:
    for key, value in patch.items():
        if not isinstance(key, str) or not key.strip():
            raise ScenarioError(f"Invalid edit key {key!r}")
        if "." in key:
            _set_dot_path(target, key, value)
        elif isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _unique_slug(base_slug: str, existing_ids: set[str]) -> str:
    if base_slug not in existing_ids:
        return base_slug
    index = 2
    while True:
        suffix = f"-{index}"
        candidate = f"{base_slug[: 64 - len(suffix)]}{suffix}"
        if candidate not in existing_ids:
            return candidate
        index += 1


def duplicate_scenario(
    source_id: str,
    *,
    name: str,
    factor_note: str = "",
    edits: Mapping[str, Any] | None = None,
    new_id: str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Deep-copy ``source_id``, apply targeted factor edits, and save as v1."""
    validated_name = _validate_name(name)
    store_file = _store_path(path)
    with _STORE_LOCK:
        source = get_scenario(source_id, path=store_file)
        store = _read_store(store_file)
        existing_ids = set(store["scenarios"].keys())
        if new_id is not None:
            slug = _validate_slug(new_id)
            if slug in existing_ids:
                raise ScenarioError(f"Scenario id {slug!r} already exists")
        else:
            slug = _unique_slug(_slugify(validated_name), existing_ids)

        new_config = copy.deepcopy(source["config"])
        if edits is not None:
            if not isinstance(edits, Mapping):
                raise ScenarioError("edits must be an object")
            _deep_merge(new_config, edits)
        new_config = normalize_config(new_config)
        changed = diff_configs(source["config"], new_config)
        return save_scenario(
            {
                "id": slug,
                "name": validated_name,
                "parent_id": source["id"],
                "factor_note": factor_note,
                "changed_fields": changed,
                "swapped_from": None,
                "config": new_config,
            },
            path=store_file,
        )


# --- Spawn-Side Swapping -----------------------------------------------------


def active_teams(
    config: Mapping[str, Any], *, arena_teams: Iterable[str] | None = None
) -> list[str]:
    """Ordered list of active team keys present in a scenario config."""
    found: list[str] = []

    def _add(team: str) -> None:
        if team not in found:
            found.append(team)

    for field in TEAM_KEYED_FIELDS:
        mapping = config.get(field)
        if isinstance(mapping, Mapping):
            for team in mapping:
                _add(str(team))

    profiles = config.get("bot_profiles")
    if isinstance(profiles, Mapping):
        for key in profiles:
            if key in found or key in KNOWN_TEAM_NAMES:
                _add(str(key))

    if arena_teams is not None:
        ordered_arena = [str(t) for t in arena_teams]
        ordered = [t for t in ordered_arena if t in found]
        extras = [t for t in found if t not in ordered]
        result = ordered + extras
        if len(result) >= 2:
            return result
        if len(ordered_arena) >= 2:
            return ordered_arena
    return found


def side_swap_mapping(
    config: Mapping[str, Any], *, arena_teams: Iterable[str] | None = None
) -> dict[str, str]:
    """Return ``{logical: swapped}`` team map (2-team swap; N-team cyclic shift)."""
    if config.get("game") == "waves":
        raise ScenarioError("Cannot swap sides in non-team waves mode")
    teams = active_teams(config, arena_teams=arena_teams)
    if len(teams) < 2:
        raise ScenarioError("Cannot swap sides with fewer than 2 active teams")
    n = len(teams)
    return {teams[i]: teams[(i + 1) % n] for i in range(n)}


def _rotate_team_dict(
    mapping: Any, team_map: dict[str, str], *, preserve_non_teams: bool = False
) -> Any:
    if not isinstance(mapping, Mapping):
        return copy.deepcopy(mapping)
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if key in team_map:
            out[team_map[key]] = copy.deepcopy(value)
        elif preserve_non_teams:
            out[key] = copy.deepcopy(value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def swap_sides(
    config: Mapping[str, Any], *, arena_teams: Iterable[str] | None = None
) -> dict[str, Any]:
    """Rotate all team-keyed fields across active teams while preserving rules/map."""
    team_map = side_swap_mapping(config, arena_teams=arena_teams)
    swapped = copy.deepcopy(dict(config))
    for field in TEAM_KEYED_FIELDS:
        if field in swapped and isinstance(swapped[field], Mapping):
            swapped[field] = _rotate_team_dict(swapped[field], team_map)
    if isinstance(swapped.get("bot_profiles"), Mapping):
        swapped["bot_profiles"] = _rotate_team_dict(
            swapped["bot_profiles"], team_map, preserve_non_teams=True
        )
    return swapped


def create_swapped_scenario(
    source_id: str,
    *,
    name: str | None = None,
    new_id: str | None = None,
    arena_teams: Iterable[str] | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Save a spawn-side-swapped copy of ``source_id`` linked via ``swapped_from``."""
    store_file = _store_path(path)
    with _STORE_LOCK:
        source = get_scenario(source_id, path=store_file)
        mapping = side_swap_mapping(source["config"], arena_teams=arena_teams)
        swapped_cfg = swap_sides(source["config"], arena_teams=arena_teams)
        target_name = _validate_name(name or f"{source['name']} (Sides Swapped)")
        store = _read_store(store_file)
        existing_ids = set(store["scenarios"].keys())
        if new_id is not None:
            slug = _validate_slug(new_id)
            if slug in existing_ids:
                raise ScenarioError(f"Scenario id {slug!r} already exists")
        else:
            slug = _unique_slug(_slugify(target_name), existing_ids)
        mapping_desc = ", ".join(f"{k}->{v}" for k, v in mapping.items())
        return save_scenario(
            {
                "id": slug,
                "name": target_name,
                "parent_id": source["id"],
                "swapped_from": source["id"],
                "factor_note": f"Swapped spawn sides ({mapping_desc})",
                "changed_fields": diff_configs(source["config"], swapped_cfg),
                "side_mapping": mapping,
                "config": swapped_cfg,
            },
            path=store_file,
        )


# --- Series Expansion --------------------------------------------------------


def expand_comparison_jobs(
    baseline_id: str,
    variant_id: str,
    *,
    repetitions: int = 1,
    include_side_swap: bool = True,
    arena_teams: Iterable[str] | None = None,
    path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Expand baseline vs variant into queued job descriptors (optional side-swap)."""
    if not _is_int(repetitions) or repetitions < 1:
        raise ScenarioError("repetitions must be an integer >= 1")
    baseline = get_scenario(baseline_id, path=path)
    variant = get_scenario(variant_id, path=path)

    jobs: list[dict[str, Any]] = []
    pair_specs = (("baseline", baseline), ("variant", variant))

    for rep in range(1, repetitions + 1):
        # Unswapped pass
        for role, scenario in pair_specs:
            cfg = copy.deepcopy(scenario["config"])
            teams = active_teams(cfg, arena_teams=arena_teams)
            identity = {t: t for t in teams}
            jobs.append(
                {
                    "job_index": len(jobs) + 1,
                    "repetition": rep,
                    "role": role,
                    "swapped": False,
                    "scenario_id": scenario["id"],
                    "scenario_version": scenario["version"],
                    "scenario_name": scenario["name"],
                    "side_mapping": identity,
                    "label": f"{scenario['name']} v{scenario['version']} (rep {rep})",
                    "config": cfg,
                }
            )
        # Swapped pass (only for sided configs with >= 2 active teams)
        if include_side_swap:
            for role, scenario in pair_specs:
                if scenario["config"].get("game") == "waves":
                    continue
                teams = active_teams(scenario["config"], arena_teams=arena_teams)
                if len(teams) < 2:
                    continue
                mapping = side_swap_mapping(scenario["config"], arena_teams=arena_teams)
                swapped_cfg = swap_sides(scenario["config"], arena_teams=arena_teams)
                jobs.append(
                    {
                        "job_index": len(jobs) + 1,
                        "repetition": rep,
                        "role": role,
                        "swapped": True,
                        "scenario_id": scenario["id"],
                        "scenario_version": scenario["version"],
                        "scenario_name": scenario["name"],
                        "side_mapping": mapping,
                        "label": (
                            f"{scenario['name']} v{scenario['version']} "
                            f"(rep {rep}, sides swapped)"
                        ),
                        "config": swapped_cfg,
                    }
                )
    return jobs


# --- Side-by-Side Comparison Aggregator --------------------------------------


def classify_outcome(outcome: Any, *, error: Any = None) -> str:
    """Strictly separate ``completed``, ``failed_start``, and ``abandoned_start``."""
    raw = str(outcome).strip().lower() if outcome is not None else ""
    if raw in COMPLETED_OUTCOMES:
        return "completed"
    if raw in FAILED_START_OUTCOMES:
        return "failed_start"
    if raw in ABANDONED_START_OUTCOMES:
        return "abandoned_start"
    if error:
        return "failed_start"
    return "abandoned_start"


def _stamp_to_iso(stem: str) -> str | None:
    match = STAMP_RE.fullmatch(stem)
    if not match:
        return None
    y, mo, d, h, mi, s = match.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}Z"


def _relative_posix(path: Path, root: Path | None) -> str:
    base = ROOT if root is None else Path(root)
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _infer_side_mapping(
    run: Mapping[str, Any],
    *,
    teams_hint: Mapping[str, Any] | None = None,
    default_teams: Iterable[str] | None = None,
) -> dict[str, str]:
    explicit = run.get("side_mapping")
    if isinstance(explicit, Mapping) and explicit:
        return {str(k): str(v) for k, v in explicit.items()}
    ordered: list[str] = []
    if default_teams:
        ordered = [str(t) for t in default_teams]
    elif isinstance(teams_hint, Mapping) and teams_hint:
        ordered = [str(t) for t in teams_hint.keys()]
    else:
        players = run.get("players")
        if isinstance(players, Mapping):
            for p in players.values():
                if isinstance(p, Mapping) and p.get("team"):
                    t = str(p["team"])
                    if t not in ordered:
                        ordered.append(t)
    if len(ordered) < 2:
        return {}
    n = len(ordered)
    return {ordered[i]: ordered[(i + 1) % n] for i in range(n)}


def _physical_to_logical(
    swapped: bool,
    run: Mapping[str, Any],
    *,
    teams_hint: Mapping[str, Any] | None = None,
    default_teams: Iterable[str] | None = None,
) -> dict[str, str]:
    if not swapped:
        return {}
    mapping = _infer_side_mapping(
        run, teams_hint=teams_hint, default_teams=default_teams
    )
    # mapping is {logical_team: physical_team}, so invert it:
    return {phys: logical for logical, phys in mapping.items()}


def _extract_team_stats(
    run: Mapping[str, Any], phys_to_logical: dict[str, str]
) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    dealt: dict[str, float] = {}
    taken: dict[str, float] = {}
    kills: dict[str, int] = {}

    def _map_team(team: str) -> str:
        return phys_to_logical.get(team, team)

    if isinstance(run.get("damage_dealt_by_team"), Mapping):
        for team, val in run["damage_dealt_by_team"].items():
            if _is_number(val):
                lt = _map_team(str(team))
                dealt[lt] = round(dealt.get(lt, 0.0) + float(val), 2)
    if isinstance(run.get("damage_taken_by_team"), Mapping):
        for team, val in run["damage_taken_by_team"].items():
            if _is_number(val):
                lt = _map_team(str(team))
                taken[lt] = round(taken.get(lt, 0.0) + float(val), 2)
    if isinstance(run.get("kills_by_team"), Mapping):
        for team, val in run["kills_by_team"].items():
            if _is_number(val):
                lt = _map_team(str(team))
                kills[lt] = kills.get(lt, 0) + int(val)

    players = run.get("players")
    if isinstance(players, Mapping) and not (dealt or taken or kills):
        for pdata in players.values():
            if not isinstance(pdata, Mapping):
                continue
            team = pdata.get("team")
            lt = _map_team(str(team)) if team else "coop"
            d = pdata.get("damage_dealt")
            t = pdata.get("damage_taken")
            pk = pdata.get("player_kills") or 0
            mk = pdata.get("mob_kills") or 0
            if _is_number(d):
                dealt[lt] = round(dealt.get(lt, 0.0) + float(d), 2)
            if _is_number(t):
                taken[lt] = round(taken.get(lt, 0.0) + float(t), 2)
            kills[lt] = kills.get(lt, 0) + int(pk) + int(mk)

    if not kills and isinstance(run.get("team_kills"), Mapping):
        for team, val in run["team_kills"].items():
            if _is_number(val):
                lt = _map_team(str(team))
                kills[lt] = kills.get(lt, 0) + int(val)

    return dealt, taken, kills


def _normalize_run_entry(
    raw: Mapping[str, Any],
    *,
    default_teams: Iterable[str] | None = None,
    teams_hint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    outcome = raw.get("outcome")
    error = raw.get("error")
    category = classify_outcome(outcome, error=error)
    swapped = bool(raw.get("swapped", False))
    phys_to_logical = _physical_to_logical(
        swapped, raw, teams_hint=teams_hint, default_teams=default_teams
    )

    winner = raw.get("winner")
    winner_str = str(winner) if isinstance(winner, str) and winner else None
    logical_winner = (
        phys_to_logical.get(winner_str, winner_str) if winner_str is not None else None
    )

    duration_raw = raw.get("duration_s", raw.get("seconds"))
    duration_s = round(float(duration_raw), 2) if _is_number(duration_raw) else None

    reason_val = raw.get("reason") or error
    reason = str(reason_val) if reason_val else None

    dealt, taken, kills = _extract_team_stats(raw, phys_to_logical)

    ver_raw = raw.get("scenario_version")
    scenario_version = int(ver_raw) if _is_int(ver_raw) else None

    return {
        "path": str(raw.get("path") or ""),
        "run_id": str(raw.get("run_id") or ""),
        "when": raw.get("when"),
        "outcome": str(outcome) if outcome is not None else "error",
        "category": category,
        "winner": winner_str,
        "logical_winner": logical_winner,
        "swapped": swapped,
        "duration_s": duration_s,
        "reason": reason,
        "scenario_version": scenario_version,
        "damage_dealt_by_team": dealt,
        "damage_taken_by_team": taken,
        "kills_by_team": kills,
    }


def load_arena_runs(
    results_dir: Path | str | None = None,
    *,
    paths: Iterable[Path | str] | None = None,
    scenario_id: str | None = None,
    scenario_version: int | None = None,
    root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Load arena match rows from ``results/companion/arena/*.json`` artifacts."""
    if paths is not None:
        files = [Path(p) for p in paths]
    else:
        directory = DEFAULT_RESULTS_DIR if results_dir is None else Path(results_dir)
        files = sorted(directory.glob("*.json")) if directory.is_dir() else []

    loaded: list[dict[str, Any]] = []
    root_path = Path(root) if root is not None else ROOT
    for file_path in files:
        if not file_path.is_file():
            continue
        try:
            doc = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue

        doc_scenario = doc.get("scenario_id")
        doc_version = doc.get("scenario_version")
        if scenario_id is not None and doc_scenario != scenario_id:
            # Also check individual runs below if doc-level tag absent
            if doc_scenario is not None:
                continue
        rel_path = _relative_posix(file_path, root_path)
        when = (
            doc.get("when")
            or doc.get("started_at")
            or doc.get("created_at")
            or _stamp_to_iso(file_path.stem)
        )
        doc_swapped = bool(doc.get("swapped", False))
        doc_side_mapping = doc.get("side_mapping")
        doc_teams = doc.get("teams") if isinstance(doc.get("teams"), dict) else None
        raw_runs = doc.get("runs")

        if isinstance(raw_runs, list) and raw_runs:
            for idx, match_row in enumerate(raw_runs, start=1):
                if not isinstance(match_row, dict):
                    continue
                row_scenario = match_row.get("scenario_id", doc_scenario)
                row_version = match_row.get("scenario_version", doc_version)
                if scenario_id is not None and row_scenario != scenario_id:
                    continue
                if (
                    scenario_version is not None
                    and row_version != scenario_version
                ):
                    continue
                merged = dict(match_row)
                merged.setdefault("path", rel_path)
                merged.setdefault("run_id", f"{file_path.stem}#{idx}")
                merged.setdefault("when", when)
                merged.setdefault("swapped", doc_swapped)
                if doc_side_mapping and "side_mapping" not in merged:
                    merged["side_mapping"] = doc_side_mapping
                if row_version is not None:
                    merged["scenario_version"] = row_version
                if doc_teams and "teams" not in merged:
                    merged["_teams_hint"] = doc_teams
                loaded.append(merged)
        else:
            # Top-level failed series with zero runs (e.g. crash before start)
            if scenario_id is not None and doc_scenario != scenario_id:
                continue
            if scenario_version is not None and doc_version != scenario_version:
                continue
            if doc.get("error") or doc.get("outcome"):
                loaded.append(
                    {
                        "path": rel_path,
                        "run_id": f"{file_path.stem}#0",
                        "when": when,
                        "outcome": doc.get("outcome") or "error",
                        "error": doc.get("error"),
                        "reason": doc.get("reason") or doc.get("error"),
                        "swapped": doc_swapped,
                        "side_mapping": doc_side_mapping,
                        "scenario_version": doc_version,
                        "_teams_hint": doc_teams,
                    }
                )
    return loaded


def _aggregate_subset(
    entries: list[dict[str, Any]], all_teams: list[str]
) -> dict[str, Any]:
    completed = [e for e in entries if e["category"] == "completed"]
    failed = [e for e in entries if e["category"] == "failed_start"]
    abandoned = [e for e in entries if e["category"] == "abandoned_start"]

    wins: dict[str, int] = {t: 0 for t in all_teams}
    draws = 0
    coop_wins = 0
    durations: list[float] = []
    dealt: dict[str, float] = {t: 0.0 for t in all_teams}
    taken: dict[str, float] = {t: 0.0 for t in all_teams}
    kills: dict[str, int] = {t: 0 for t in all_teams}

    for entry in completed:
        if entry["duration_s"] is not None:
            durations.append(entry["duration_s"])
        winner = entry["logical_winner"]
        is_draw = entry["outcome"] == "draw" or (
            winner is None and entry["reason"] == "draw"
        )
        if is_draw:
            draws += 1
        elif winner is not None:
            wins[winner] = wins.get(winner, 0) + 1
        elif entry["outcome"] == "won":
            coop_wins += 1

        for team, val in entry["damage_dealt_by_team"].items():
            dealt[team] = round(dealt.get(team, 0.0) + val, 2)
        for team, val in entry["damage_taken_by_team"].items():
            taken[team] = round(taken.get(team, 0.0) + val, 2)
        for team, val in entry["kills_by_team"].items():
            kills[team] = kills.get(team, 0) + val

    denom = len(completed)
    win_rate = (
        {team: round(count / denom, 4) for team, count in wins.items()}
        if denom > 0
        else {}
    )
    mean_dealt = (
        {team: round(val / denom, 2) for team, val in dealt.items()}
        if denom > 0
        else {}
    )
    mean_taken = (
        {team: round(val / denom, 2) for team, val in taken.items()}
        if denom > 0
        else {}
    )
    return {
        "completed_rounds": denom,
        "failed_starts": len(failed),
        "abandoned_starts": len(abandoned),
        "wins_by_team": wins,
        "win_rate_by_team": win_rate,
        "coop_wins": coop_wins,
        "draws": draws,
        "median_duration_s": (
            round(statistics.median(durations), 2) if durations else None
        ),
        "mean_duration_s": round(statistics.mean(durations), 2) if durations else None,
        "damage_dealt_by_team": dealt,
        "damage_taken_by_team": taken,
        "mean_damage_dealt_by_team": mean_dealt,
        "mean_damage_taken_by_team": mean_taken,
        "kills_by_team": kills,
    }


def summarize_runs(
    runs: Iterable[Mapping[str, Any]],
    *,
    default_teams: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Aggregate runs with strict separation of completed, failed, and abandoned."""
    team_order = [str(t) for t in (default_teams or ())]
    entries = [
        _normalize_run_entry(
            run,
            default_teams=team_order or None,
            teams_hint=run.get("_teams_hint")
            if isinstance(run.get("_teams_hint"), Mapping)
            else run.get("teams")
            if isinstance(run.get("teams"), Mapping)
            else None,
        )
        for run in runs
    ]

    discovered_teams: list[str] = list(team_order)
    for entry in entries:
        for team in (
            ([entry["logical_winner"]] if entry["logical_winner"] else [])
            + list(entry["damage_dealt_by_team"].keys())
            + list(entry["damage_taken_by_team"].keys())
            + list(entry["kills_by_team"].keys())
        ):
            if team and team not in discovered_teams:
                discovered_teams.append(team)

    overall = _aggregate_subset(entries, discovered_teams)
    unswapped = _aggregate_subset(
        [e for e in entries if not e["swapped"]], discovered_teams
    )
    swapped = _aggregate_subset(
        [e for e in entries if e["swapped"]], discovered_teams
    )

    completed_runs = [e for e in entries if e["category"] == "completed"]
    failed_starts = [e for e in entries if e["category"] == "failed_start"]
    abandoned_starts = [e for e in entries if e["category"] == "abandoned_start"]

    return {
        "total_runs": len(entries),
        "completed_rounds": overall["completed_rounds"],
        "failed_starts_count": len(failed_starts),
        "abandoned_starts_count": len(abandoned_starts),
        "wins_by_team": overall["wins_by_team"],
        "win_rate_by_team": overall["win_rate_by_team"],
        "coop_wins": overall["coop_wins"],
        "draws": overall["draws"],
        "median_duration_s": overall["median_duration_s"],
        "mean_duration_s": overall["mean_duration_s"],
        "damage_dealt_by_team": overall["damage_dealt_by_team"],
        "damage_taken_by_team": overall["damage_taken_by_team"],
        "mean_damage_dealt_by_team": overall["mean_damage_dealt_by_team"],
        "mean_damage_taken_by_team": overall["mean_damage_taken_by_team"],
        "kills_by_team": overall["kills_by_team"],
        "per_side_breakdown": {
            "unswapped": unswapped,
            "swapped": swapped,
        },
        "completed_runs": completed_runs,
        "failed_starts": failed_starts,
        "abandoned_starts": abandoned_starts,
        "runs": entries,
    }


def _diff_number(
    left: float | None, right: float | None, ndigits: int = 2
) -> float | None:
    if left is None or right is None:
        return None
    return round(right - left, ndigits)


def _diff_team_metric(
    left: Mapping[str, float | int],
    right: Mapping[str, float | int],
    *,
    ndigits: int | None = 2,
) -> dict[str, float | int]:
    teams = sorted(set(left.keys()) | set(right.keys()))
    out: dict[str, float | int] = {}
    for team in teams:
        lv = left.get(team, 0)
        rv = right.get(team, 0)
        out[team] = int(rv - lv) if ndigits is None else round(float(rv - lv), ndigits)
    return out


def compare_runs(
    baseline_runs: Iterable[Mapping[str, Any]],
    variant_runs: Iterable[Mapping[str, Any]],
    *,
    baseline_meta: Mapping[str, Any] | None = None,
    variant_meta: Mapping[str, Any] | None = None,
    baseline_config: Mapping[str, Any] | None = None,
    variant_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare two sets of runs side-by-side with deltas and config diff."""
    base_teams = active_teams(baseline_config) if baseline_config else None
    var_teams = active_teams(variant_config) if variant_config else None

    b_summary = summarize_runs(baseline_runs, default_teams=base_teams)
    v_summary = summarize_runs(variant_runs, default_teams=var_teams)

    if baseline_meta is not None:
        b_summary["scenario"] = dict(baseline_meta)
    if variant_meta is not None:
        v_summary["scenario"] = dict(variant_meta)

    changed = (
        diff_configs(baseline_config, variant_config)
        if baseline_config is not None and variant_config is not None
        else []
    )

    deltas = {
        "completed_rounds": (
            v_summary["completed_rounds"] - b_summary["completed_rounds"]
        ),
        "failed_starts": (
            v_summary["failed_starts_count"] - b_summary["failed_starts_count"]
        ),
        "abandoned_starts": (
            v_summary["abandoned_starts_count"] - b_summary["abandoned_starts_count"]
        ),
        "median_duration_s": _diff_number(
            b_summary["median_duration_s"], v_summary["median_duration_s"]
        ),
        "mean_duration_s": _diff_number(
            b_summary["mean_duration_s"], v_summary["mean_duration_s"]
        ),
        "win_rate_by_team": _diff_team_metric(
            b_summary["win_rate_by_team"], v_summary["win_rate_by_team"], ndigits=4
        ),
        "damage_dealt_by_team": _diff_team_metric(
            b_summary["damage_dealt_by_team"],
            v_summary["damage_dealt_by_team"],
            ndigits=2,
        ),
        "damage_taken_by_team": _diff_team_metric(
            b_summary["damage_taken_by_team"],
            v_summary["damage_taken_by_team"],
            ndigits=2,
        ),
        "kills_by_team": _diff_team_metric(
            b_summary["kills_by_team"], v_summary["kills_by_team"], ndigits=None
        ),
    }

    return {
        "baseline": b_summary,
        "variant": v_summary,
        "changed_fields": changed,
        "deltas": deltas,
    }


def compare_scenarios(
    baseline_id: str,
    variant_id: str,
    *,
    results_dir: Path | str | None = None,
    baseline_paths: Iterable[Path | str] | None = None,
    variant_paths: Iterable[Path | str] | None = None,
    baseline_version: int | None = None,
    variant_version: int | None = None,
    root: Path | str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Load two saved scenarios (or specific versions) and compare their arena runs."""
    base_full = get_scenario(baseline_id, path=path)
    var_full = get_scenario(variant_id, path=path)

    base_snap = (
        get_scenario_version(baseline_id, baseline_version, path=path)
        if baseline_version is not None
        else base_full
    )
    var_snap = (
        get_scenario_version(variant_id, variant_version, path=path)
        if variant_version is not None
        else var_full
    )

    b_runs = load_arena_runs(
        results_dir=results_dir,
        paths=baseline_paths,
        scenario_id=None if baseline_paths is not None else baseline_id,
        scenario_version=baseline_version,
        root=root,
    )
    v_runs = load_arena_runs(
        results_dir=results_dir,
        paths=variant_paths,
        scenario_id=None if variant_paths is not None else variant_id,
        scenario_version=variant_version,
        root=root,
    )

    base_meta = {
        "id": base_full["id"],
        "name": base_full["name"],
        "version": base_snap["version"],
        "factor_note": base_snap.get("factor_note", ""),
        "parent_id": base_full.get("parent_id"),
        "swapped_from": base_full.get("swapped_from"),
    }
    var_meta = {
        "id": var_full["id"],
        "name": var_full["name"],
        "version": var_snap["version"],
        "factor_note": var_snap.get("factor_note", ""),
        "parent_id": var_full.get("parent_id"),
        "swapped_from": var_full.get("swapped_from"),
    }

    return compare_runs(
        b_runs,
        v_runs,
        baseline_meta=base_meta,
        variant_meta=var_meta,
        baseline_config=base_snap["config"],
        variant_config=var_snap["config"],
    )
