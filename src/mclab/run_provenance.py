"""Immutable, pre-mutation identity for one lab run.

The record deliberately stores hashes for source/config inputs rather than their
contents. It describes the bytes available at launch time; it does not claim that
an installed mod was built from the current checkout unless the evidence agrees.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = 1
_SOURCE_ROOTS = ("src", "scripts", "mcb-body/src", "mcb-lab/src", "client-bridge/src")
_CONFIG_ROOTS = ("config", "courses", "mcb-body", "mcb-lab", "client-bridge")
_SOURCE_SUFFIXES = {".py", ".java", ".kt", ".kts", ".gradle", ".toml", ".yaml", ".yml"}
_CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml", ".properties", ".gradle"}
_SENSITIVE = re.compile(
    r"(?:secret|token|password|credential|api[_-]?key|access[_-]?key|authorization|auth[_-]?header|cookie)",
    re.I,
)
_CREDENTIAL_VALUE = re.compile(r"(?i)(bearer\s+|(?:api[_-]?key|token|password)=)[^\s]+")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sensitive_input_path(path: Path) -> bool:
    names = [part.lower() for part in path.parts]
    return any(
        _SENSITIVE.search(name) or name == ".env" or "backend" in name for name in names
    )


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _excluded(path: str) -> bool:
    parts = Path(path).parts
    lower = [part.lower() for part in parts]
    if any(
        part
        in {
            "results",
            "build",
            "dist",
            "target",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
        }
        for part in lower
    ):
        return True
    if any(_SENSITIVE.search(part) for part in lower):
        return True
    name = lower[-1] if lower else ""
    return name.startswith(".") and name not in {".gitignore"}


def _is_input(path: str) -> bool:
    p = Path(path)
    if _excluded(path):
        return False
    under_source = any(
        path == base or path.startswith(base + "/") for base in _SOURCE_ROOTS
    )
    under_config = any(
        path == base or path.startswith(base + "/") for base in _CONFIG_ROOTS
    )
    if path in {"pyproject.toml", "uv.lock"}:
        return True
    if under_source:
        return p.suffix.lower() in _SOURCE_SUFFIXES or p.name in {
            "Dockerfile",
            "Makefile",
        }
    if under_config:
        # Runtime auth/supervisor/backend files are explicitly outside this allowlist.
        return p.suffix.lower() in _CONFIG_SUFFIXES and not any(
            word in p.name.lower()
            for word in ("backend", "credential", "secret", "token", "auth")
        )
    return False


def _input_inventory(root: Path) -> dict:
    tracked_raw = _git(root, "ls-files", "-z")
    tracked = set(tracked_raw.split("\0")) if tracked_raw else set()
    all_paths = set(tracked)
    # Walk only the allowlisted roots so untracked source/config files participate.
    for base in (*_SOURCE_ROOTS, *_CONFIG_ROOTS):
        directory = root / base
        if directory.is_dir():
            for item in directory.rglob("*"):
                if item.is_file():
                    all_paths.add(item.relative_to(root).as_posix())
    all_paths.update(p for p in ("pyproject.toml", "uv.lock") if (root / p).is_file())
    files = {}
    for rel in sorted(path for path in all_paths if path and _is_input(path)):
        path = root / Path(rel)
        if path.is_file():
            files[rel] = _sha(path.read_bytes())
        elif rel in tracked:
            files[rel] = "deleted"
    digest = _sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    return {"sha256": digest, "files": files}


def _sanitize(value: Any, key: str = "") -> Any:
    if _SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(k): _sanitize(v, str(k))
            for k, v in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        value = _CREDENTIAL_VALUE.sub(r"\1[REDACTED]", value)
        # URLs can carry credentials in userinfo or query parameters.
        if value.startswith(("http://", "https://")):
            try:
                parts = urlsplit(value)
                host = parts.hostname or ""
                if parts.port:
                    host += f":{parts.port}"
                userinfo = "[REDACTED]@" if parts.username or parts.password else ""
                value = urlunsplit((parts.scheme, userinfo + host, parts.path, "", ""))
            except ValueError:
                return "[REDACTED_URL]"
        return value
    if value is None or type(value) in {bool, int, float}:
        return value
    return str(value)


def _invocation(args: list[str] | None) -> list[str] | None:
    if args is None:
        return None
    result = []
    mask_next = False
    for raw in args:
        arg = str(raw)
        if mask_next:
            result.append("[REDACTED]")
            mask_next = False
            continue
        if _SENSITIVE.search(arg.split("=", 1)[0]):
            if "=" in arg:
                result.append(arg.split("=", 1)[0] + "=[REDACTED]")
            else:
                result.append(arg)
                mask_next = True
            continue
        result.append(_CREDENTIAL_VALUE.sub(r"\1[REDACTED]", arg))
    return result


def _installed_mods(root: Path) -> dict:
    # paths.SERVER is runtime/server in the current lab contract. lab.json does
    # not define a server-directory override; if this changes, update this path
    # lookup together with mclab.paths rather than assuming another install.
    mod_dir = root / "runtime" / "server" / "mods"
    names = ("mcb-body-0.1.0.jar", "mcb-lab-0.1.0.jar")
    build_path = root / "runtime" / "mod-build.json"
    try:
        build = json.loads(build_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        build = None
    current_source = _source_hash(root)
    result = {}
    for name in names:
        jar = mod_dir / name
        if not jar.is_file():
            result[name.removesuffix("-0.1.0.jar")] = {
                "status": "unknown",
                "reason": "installed_jar_missing",
            }
            continue
        jar_hash = _sha(jar.read_bytes())
        mod = name.removesuffix("-0.1.0.jar")
        declared = build.get("jars", {}).get(mod, {}) if isinstance(build, dict) else {}
        declared_hash = (
            declared.get("jar_sha256") if isinstance(declared, dict) else None
        )
        result[mod] = {
            "status": "hashed",
            "installed_jar_sha256": jar_hash,
            "build_record_jar_matches": jar_hash == declared_hash
            if declared_hash
            else None,
            "recorded_build_source_sha256": build.get("source_hash")
            if isinstance(build, dict)
            else None,
            "current_source_sha256": current_source,
            "recorded_build_matches_current_source": (
                build.get("source_hash") == current_source
                if isinstance(build, dict) and isinstance(build.get("source_hash"), str)
                else None
            ),
        }
    return result


def _source_hash(root: Path) -> str:
    hasher = hashlib.sha256()
    for mod in ("mcb-body", "mcb-lab"):
        directory = root / mod / "src"
        if directory.is_dir():
            for item in sorted(p for p in directory.rglob("*") if p.is_file()):
                hasher.update(item.relative_to(root).as_posix().encode())
                hasher.update(item.read_bytes())
    return hasher.hexdigest()


def _prompt_identity(config: dict) -> dict:
    found = {}

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                here = f"{path}.{key}" if path else str(key)
                low = str(key).lower()
                if low in {"prompt_identity", "planner_prompt_identity"}:
                    if isinstance(child, (str, int, float)) and child:
                        found[here] = {"identity": _sanitize(child, key)}
                elif "prompt_version" in low and isinstance(child, (str, int, float)):
                    found[here] = {"version": child}
                elif "prompt" in low or "instruction" in low or low == "candidates":
                    if isinstance(child, (str, bytes)):
                        found[here] = {
                            "version": None,
                            "sha256": _sha(
                                child.encode() if isinstance(child, str) else child
                            ),
                        }
                    elif isinstance(child, dict) and any(
                        k in child for k in ("version", "sha256", "hash", "identity")
                    ):
                        found[here] = _sanitize(child)
                    elif isinstance(child, dict):
                        encoded = json.dumps(
                            _sanitize(child), sort_keys=True, separators=(",", ":")
                        ).encode()
                        found[here] = {"sha256": _sha(encoded)}
                    elif isinstance(child, list):
                        encoded = json.dumps(
                            _sanitize(child), sort_keys=True, separators=(",", ":")
                        ).encode()
                        found[here] = {"sha256": _sha(encoded)}
                walk(child, here)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")

    walk(config)
    from mclab.tactical import CANDIDATES, INSTRUCTIONS, PROFILE

    built_in = {
        "profile_id": PROFILE,
        "instructions_sha256": _sha(INSTRUCTIONS.encode("utf-8")),
        "candidates_sha256": _sha(
            json.dumps(CANDIDATES, sort_keys=True, separators=(",", ":")).encode()
        ),
    }
    built_in["combined_sha256"] = _sha(
        json.dumps(built_in, sort_keys=True, separators=(",", ":")).encode()
    )
    selected = any(
        value.get("identity") == PROFILE
        for value in found.values()
        if isinstance(value, dict)
    )
    return {
        # The tactical prompt implementation is always identifiable from its
        # in-checkout profile, even when this run did not select it.
        "status": "identified",
        "selection_status": "identified" if selected else "unknown",
        "selected": PROFILE if selected else None,
        "entries": found,
        "available_profiles": {"tactical": built_in},
    }


def _input_references(
    root: Path,
    config: dict,
    invocation: list[str] | None,
    input_references: dict[str, str] | None = None,
) -> dict:
    """Hash launch-selected presets, run configs, builds and checkpoints by bytes."""
    refs, hashes = {}, {}
    paths: dict[str, Path] = {}

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                here = f"{path}.{key}" if path else str(key)
                low = str(key).lower()
                if low in {
                    "preset_ref",
                    "preset_refs",
                    "preset_id",
                    "preset_ids",
                    "input_refs",
                    "input_ref",
                    "profile_ref",
                    "profile_refs",
                    "selected_profiles",
                }:
                    refs[here] = _sanitize(child)
                elif low.endswith("_profile") and isinstance(child, str):
                    refs[here] = Path(child).name
                    if Path(child).suffix.lower() == ".json":
                        paths[here] = _resolve_input_path(root, Path(child))
                elif (
                    low in {"preset", "preset_name"}
                    and (path in {"", "run"})
                    and isinstance(child, str)
                ):
                    preset_id = Path(child).stem
                    refs[here] = preset_id
                    paths[f"preset:{preset_id}"] = (
                        root / "config" / "presets" / f"{preset_id}.json"
                    )
                elif low in {
                    "input_hash",
                    "input_hashes",
                    "preset_hash",
                    "preset_hashes",
                    "sha256",
                }:
                    hashes[here] = _sanitize(child)
                walk(child, here)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")

    walk(config)
    run = config.get("run") if isinstance(config.get("run"), dict) else {}
    for field in ("build", "resume"):
        candidate = run.get(field)
        if isinstance(candidate, str) and candidate:
            path = Path(candidate)
            paths[f"run.{field}"] = path if path.is_absolute() else root / path
    args = [str(value) for value in (invocation or [])]
    for name, value in (input_references or {}).items():
        if not isinstance(value, str) or not value:
            continue
        refs[f"selected.{name}"] = Path(value).name
        paths[f"selected.{name}"] = _resolve_input_path(root, Path(value))
    i = 0
    while i < len(args):
        arg = args[i]
        flag, sep, inline = arg.partition("=")
        if flag in {
            "--preset",
            "--run-config",
            "--routes",
            "--route-profile",
            "--awareness-profile",
            "--terrain-profile",
            "--movement-profile",
            "--memory-profile",
        }:
            value = inline if sep else (args[i + 1] if i + 1 < len(args) else "")
            if not sep:
                i += 1
            if value:
                if flag == "--preset":
                    preset_id = Path(value).stem
                    refs[f"invocation.{flag}"] = preset_id
                    paths[f"preset:{preset_id}"] = (
                        root / "config" / "presets" / f"{preset_id}.json"
                    )
                elif flag == "--run-config":
                    path = Path(value)
                    paths["invocation.--run-config"] = (
                        path if path.is_absolute() else root / path
                    )
                    refs["invocation.--run-config"] = Path(value).name
                else:
                    key = "invocation." + flag
                    refs[key] = Path(value).name
                    paths[key] = _resolve_input_path(root, Path(value))
        i += 1
    for label, path in paths.items():
        if _sensitive_input_path(path):
            hashes[label] = {
                "reference": path.name,
                "status": "excluded_sensitive_input",
                "sha256": None,
            }
            continue
        try:
            content_hash = _sha(path.read_bytes())
            state = "hashed"
        except OSError:
            content_hash = None
            state = "missing_or_unreadable"
        hashes[label] = {
            "reference": path.name,
            "status": state,
            "sha256": content_hash,
        }
    return {
        "preset_references": refs,
        "input_hashes": hashes,
        "status": "identified" if refs or hashes else "unknown",
    }


def _resolve_input_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _run_identity(root: Path, config: dict) -> dict:
    run = config.get("run") if isinstance(config.get("run"), dict) else {}
    fields = (
        "test",
        "start",
        "seed",
        "build",
        "rotation",
        "origin",
        "resume",
        "routes",
        "entrant",
        "seeds",
        "route_ids",
        "route_data",
        "entrants",
        "conditions",
        "repetitions",
        "cost_profile",
        "course",
        "operation",
    )
    identity = {key: _sanitize(run[key], key) for key in fields if key in run}
    resume = run.get("resume")
    if isinstance(resume, str) and resume:
        path = Path(resume)
        if not path.is_absolute():
            path = root / path
        try:
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            identity["checkpoint"] = {
                key: _sanitize(checkpoint[key], key)
                for key in ("seed", "start", "origin", "rotation", "build")
                if key in checkpoint
            }
            identity["checkpoint_status"] = "identified"
            if "origin" in checkpoint:
                identity["effective_origin"] = {
                    "status": "identified",
                    "value": _sanitize(checkpoint["origin"], "origin"),
                    "source": "resume_checkpoint",
                }
        except (OSError, ValueError, TypeError):
            identity["checkpoint_status"] = "unknown"
    else:
        identity["checkpoint_status"] = "not_resumed"
    if "effective_origin" not in identity:
        origin = run.get("origin")
        if origin is not None:
            identity["effective_origin"] = {
                "status": "identified",
                "value": _sanitize(origin),
                "source": "resolved_config",
            }
        elif (
            isinstance(run.get("start"), (list, tuple))
            and len(run["start"]) == 3
            and type(run.get("dx")) is int
            and type(run.get("dz")) is int
        ):
            start = run["start"]
            identity["effective_origin"] = {
                "status": "identified",
                "value": [start[0] + run["dx"], start[1], start[2] + run["dz"]],
                "source": "start_plus_build_offset",
            }
        elif "build" in run:
            identity["effective_origin"] = {"status": "unknown"}
    return identity


def record_run(
    folder: Path,
    config: dict,
    *,
    invocation: list[str] | None = None,
    root: Path | None = None,
    input_references: dict[str, str] | None = None,
) -> dict:
    """Write immutable ``run_config.json`` and ``provenance.json`` before mutation.

    Call this before opening a world or issuing any body/lab operation. Repeating
    an identical call is idempotent; a conflicting or partial existing record is
    refused rather than rewritten.
    """
    folder = Path(folder)
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    if not isinstance(config, dict):
        raise TypeError("config must be a dictionary")
    prompt_identity = _prompt_identity(config)
    references = _input_references(root, config, invocation, input_references)
    clean_config = _sanitize(config)
    config_bytes = (
        json.dumps(clean_config, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    source_inputs = _input_inventory(root)
    git_dirty = status is None or bool(status)
    cfg_path = folder / "run_config.json"
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "git": {
            "commit": commit,
            "dirty": git_dirty,
            "relevant_inputs_sha256": source_inputs["sha256"],
            "relevant_inputs": source_inputs["files"],
        },
        "invocation": _invocation(invocation),
        "referenced_inputs": references,
        "run_identity": _run_identity(root, config),
        "config_sha256": _sha(config_bytes),
        "installed_mods": _installed_mods(root),
        "model": {
            "identifiers": _extract_model_ids(clean_config),
            "prompt_identity": prompt_identity,
        },
    }
    prov_bytes = (
        json.dumps(provenance, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    prov_path = folder / "provenance.json"
    existing = (cfg_path.exists(), prov_path.exists())
    if any(existing):
        if not all(existing):
            raise FileExistsError(
                "Partial run provenance exists; refusing to overwrite"
            )
        if (
            cfg_path.read_bytes() != config_bytes
            or prov_path.read_bytes() != prov_bytes
        ):
            raise FileExistsError(
                "Run provenance already exists with different identity"
            )
        return {"run_config": clean_config, "provenance": provenance}
    folder.mkdir(parents=True, exist_ok=True)
    # Exclusive creation closes the race between checking and writing. If either
    # create fails, the partial pair remains visible and future calls fail closed.
    with cfg_path.open("xb") as stream:
        stream.write(config_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    with prov_path.open("xb") as stream:
        stream.write(prov_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    return {"run_config": clean_config, "provenance": provenance}


def _extract_model_ids(config: dict) -> dict:
    model = config.get("model")
    if not isinstance(model, dict):
        return {"status": "unknown"}
    keys = {
        "name",
        "kind",
        "model",
        "requested_model",
        "backend",
        "backend_id",
        "role",
        "prompt_identity",
        "inference_scope",
        "network_scope",
        "request_limit",
        "deadline_s",
        "planner_backend",
        "planner_model",
        "planner_request_limit",
        "planner_prompt_identity",
    }
    ids = {k: _sanitize(v, k) for k, v in model.items() if k in keys}
    profile = model.get("planner_profile")
    if isinstance(profile, dict):
        ids["planner_profile"] = {
            key: _sanitize(profile[key], key)
            for key in ("planner_interval_s", "plan_ttl_ticks", "planner_failure")
            if key in profile
        }
        for key in ("instructions", "candidates"):
            if key in profile:
                value = profile[key]
                encoded = (
                    value.encode("utf-8")
                    if isinstance(value, str)
                    else json.dumps(
                        value, sort_keys=True, separators=(",", ":")
                    ).encode()
                )
                ids["planner_profile"][key + "_sha256"] = _sha(encoded)
    return {"status": "identified" if ids else "unknown", "values": ids}
