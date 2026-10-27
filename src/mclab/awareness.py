"""Versioned development sensors; legacy scored profiles remain unchanged."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Awareness:
    preset: str = "legacy"
    horizontal_radius_blocks: int = 8
    vertical_radius_blocks: int = 4  # -1 means full dimension height (chunk only)
    entity_radius_blocks: int = 8
    entity_vertical_radius_blocks: int = 4
    entity_visibility: str = "visible"
    update_interval_ticks: int = 5
    extra_scan_cells_per_update: int = 2048
    extra_scan_budget_ms: int = 3
    schema_version: int = 1

    def __post_init__(self):
        if type(self.preset) is not str or self.preset not in {
            "legacy",
            "visible",
            "chunk",
        }:
            raise ValueError("Unknown awareness preset")
        bounds = {
            "horizontal_radius_blocks": (1, 64),
            "vertical_radius_blocks": (-1, 64),
            "entity_radius_blocks": (1, 64),
            "entity_vertical_radius_blocks": (1, 64),
            "update_interval_ticks": (1, 20),
            "extra_scan_cells_per_update": (1, 8192),
            "extra_scan_budget_ms": (1, 10),
            "schema_version": (1, 1),
        }
        for key, (low, high) in bounds.items():
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid awareness {key}")
        if self.vertical_radius_blocks == 0 or (
            self.vertical_radius_blocks == -1 and self.preset != "chunk"
        ):
            raise ValueError("Full height is supported only in chunk mode")
        if type(self.entity_visibility) is not str or self.entity_visibility not in {
            "visible",
            "unoccluded",
        }:
            raise ValueError("Invalid entity visibility")
        if self.preset == "legacy" and asdict(self) != {
            "preset": "legacy",
            "horizontal_radius_blocks": 8,
            "vertical_radius_blocks": 4,
            "entity_radius_blocks": 8,
            "entity_vertical_radius_blocks": 4,
            "entity_visibility": "visible",
            "update_interval_ticks": 5,
            "extra_scan_cells_per_update": 2048,
            "extra_scan_budget_ms": 3,
            "schema_version": 1,
        }:
            raise ValueError("Legacy awareness is frozen; choose visible or chunk")

    @property
    def sensor_profile(self):
        return "local-visibility-v1" if self.preset == "legacy" else "awareness-v1"

    def payload(self):
        return asdict(self)

    @classmethod
    def from_payload(cls, value):
        if (
            not isinstance(value, dict)
            or value.keys() != cls.__dataclass_fields__.keys()
        ):
            raise ValueError("Awareness profile must contain exactly the schema fields")
        return cls(**value)

    @classmethod
    def resolve(cls, preset="legacy", **overrides):
        values = {
            "legacy": {},
            "visible": {"horizontal_radius_blocks": 32, "vertical_radius_blocks": 16},
            "chunk": {"horizontal_radius_blocks": 32, "vertical_radius_blocks": -1},
        }
        if preset not in values:
            raise ValueError("Unknown awareness preset")
        return cls(preset=preset, **(values[preset] | overrides))


def check():
    """Exercise native sensors in a restored development fixture, never a scored run."""
    from datetime import UTC, datetime

    from mclab.bridge import Bridge
    from mclab.modbuild import source_hash
    from mclab.paths import ROOT, read_json, write_json
    from mclab.recorder import exclusive_arena

    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Rebuild and install the current server mod first")
    destination = (
        ROOT / "results/awareness" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    summary = {"development_only": True, "mod_provenance": provenance}
    with exclusive_arena():
        client = Bridge(destination / "events.jsonl")
        spawned = False
        try:
            client.synchronize()
            request = client.send("spawn", via_command=False)
            client.acknowledge(request, "spawn_requested")
            spawned = True
            client.wait(lambda state: state["state"] == "ready", timeout=45)
            request = client.send("awareness_check")
            result = client.acknowledge(request, "awareness_checked", timeout=30)
            summary.update(result["data"]["verification"])
            summary["status"] = "passed" if summary["passed"] else "failed"
        except Exception as error:
            summary.update(status="error", error=str(error))
            raise
        finally:
            try:
                if spawned:
                    client.send("despawn")
                    client.wait(lambda state: state["state"] == "idle")
            except Exception as error:
                summary.update(status="error", cleanup_error=str(error))
                raise
            finally:
                try:
                    client.close()
                except Exception as error:
                    summary.update(status="error", recording_error=str(error))
                    raise
                finally:
                    write_json(destination / "summary.json", summary)
    return {**summary, "artifact": str(destination / "summary.json")}
