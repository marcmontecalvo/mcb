"""Live development proof of named reference fixtures; no human or scored run."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from mclab import gates
from mclab.bootstrap import sha256
from mclab.bridge import Bridge
from mclab.modbuild import source_hash
from mclab.paths import MOD_SOURCES, ROOT, read_json, write_json
from mclab.protocolproof import controls
from mclab.qualify import QualificationCheckFailure
from mclab.recorder import arena_guard
from mclab.reference import MECHANICS


@arena_guard
def qualify_reference_fixtures() -> dict:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = ROOT / "results/qualification" / stamp
    client = Bridge(destination / "events.jsonl")
    summary = {
        "schema_version": 1,
        "suite": "reference_fixture_v1",
        "scored": False,
        "run_id": client.run_id,
        "launch_id": client.launch_id,
        "G1": gates.statuses()["G1"],
        "G1_scope": "Gate status refers to the frozen initial C1 profile",
        "checks": [],
        "qualifier_source_sha256": sha256(Path(__file__)),
        "mod_provenance": read_json(ROOT / "runtime/mod-build.json"),
    }

    def check(name, expected, observed, passed):
        result = dict(
            name=name, expected=expected, observed=observed, passed=bool(passed)
        )
        summary["checks"].append(result)
        client.record("check", result)
        if not passed:
            raise QualificationCheckFailure(f"Reference fixture check failed: {name}")

    def motor(index, forward):
        tick = client.state()["tick"]
        request = client.send(
            "controls",
            motor=True,
            controls=controls(
                tick,
                f"ledge-{index}",
                forward=forward,
                sneak=True,
                duration=5,
                expires_tick=tick + 8,
            ),
        )
        client.acknowledge(request, "applied")

    try:
        if summary["mod_provenance"]["source_hash"] != source_hash():
            raise RuntimeError(
                "Installed mod differs from source; rebuild and install first"
            )
        for relative in (
            "src/mclab",
            *MOD_SOURCES,
            "config/qualification.json",
        ):
            source, target = ROOT / relative, destination / "sources" / relative
            if source.is_dir():
                shutil.copytree(
                    source, target, ignore=shutil.ignore_patterns("__pycache__")
                )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        client.synchronize()
        request = client.send("spawn", via_command=False)
        client.acknowledge(request, "spawn_requested")
        client.wait(lambda state: state["state"] == "ready", timeout=40)
        client.wait(lambda state: state["tick"] >= 230, timeout=20)
        for mechanic in MECHANICS:
            first_tick = client.state()["tick"]
            request = client.send(
                "fixture", log=False, dirt=0, mechanic="reference_" + mechanic
            )
            client.acknowledge(request, "received")
            state = client.wait(
                lambda s, start=first_tick: s["tick"] > start + 5
                and s["state"] == "ready"
            )
            setup = state["body"]["reference_setup"]
            check(
                f"{mechanic}_geometry",
                847,
                setup.get("verified_cells"),
                setup.get("verified_cells") == 847
                and setup.get("mechanic") == mechanic,
            )
            x, y, z = setup["origin"]
            target = state["body"]["qualification_target_block"]
            expected = {
                "collision": "minecraft:stone",
                "step": "minecraft:stone_slab",
                "ledge": "minecraft:air",
                "fall": "minecraft:air",
            }[mechanic]
            check(f"{mechanic}_target", expected, target, target == expected)
            if mechanic in {"collision", "step"}:
                first_tick = state["tick"]
                request = client.send("command_proof", command="forward", duration=25)
                applied = client.acknowledge(request, "command_acknowledged")["tick"]
                end = client.wait(lambda s, start=applied: s["tick"] >= start + 30)
                if mechanic == "collision":
                    value = end["body"]["position"][2]
                    check(
                        "collision_blocks_forward",
                        [z + 1.69, z + 1.701],
                        value,
                        z + 1.69 <= value <= z + 1.701,
                    )
                else:
                    peak = (
                        max(
                            s["body"]["position"][1]
                            for s in client.samples
                            if first_tick <= s["tick"] <= end["tick"]
                        )
                        - y
                    )
                    check(
                        "step_walk_without_jump",
                        [0.49, 0.51],
                        peak,
                        0.49 <= peak <= 0.51,
                    )
            elif mechanic == "ledge":
                for index in range(13):
                    motor(index, 1)
                for index in range(3):
                    motor(f"stop-{index}", 0)
                value = client.state()["body"]["position"]
                check(
                    "ledge_sneak_holds_edge",
                    f"y={y}, {z + 2.2} <= z <= {z + 2.301}",
                    value,
                    abs(value[1] - y) < 0.01 and z + 2.2 <= value[2] <= z + 2.301,
                )
            else:
                landings = [
                    s["body"]
                    for s in client.samples
                    if s["tick"] > first_tick
                    and s["body"].get("on_ground")
                    and s["body"]["position"][1] == y
                ]
                health = min(s["health"] for s in landings)
                check("fall_lands_with_damage", [17, 19], health, 17 <= health <= 19)
        summary["status"] = "passed_development_subset"
    except QualificationCheckFailure as error:
        summary.update(status="failed", error=str(error))
    except Exception as error:
        summary.update(status="invalid", error=str(error))
    finally:
        try:
            request = client.send("despawn")
            client.acknowledge(request, "received")
            client.wait(lambda state: state["state"] == "idle")
        except Exception as error:
            summary["cleanup_error"] = str(error)
            summary["status"] = "invalid"
        summary["recording"] = str(destination / "events.jsonl")
        summary["requests"] = client.submitted
        client.close()
        write_json(destination / "summary.json", summary)
    return summary
