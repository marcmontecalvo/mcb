"""Unscored flat-lab smoke: segmented motor plus durable map reopened from disk."""

from datetime import UTC, datetime
from uuid import uuid4

from mclab.awareness import Awareness
from mclab.bootstrap import sha256
from mclab.execution import RecordedBridge
from mclab.modbuild import source_hash
from mclab.model_execution import drive
from mclab.navigation_segmented import SegmentedController
from mclab.paths import ROOT, SERVER, read_json, write_json
from mclab.recorder import exclusive_arena
from mclab.spatial_memory import MapArchive, MapIdentity, SpatialMemory


def main():
    if (
        "level-name=world"
        not in (SERVER / "server.properties").read_text().splitlines()
    ):
        raise RuntimeError("This probe requires the flat lab world")
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Rebuild/install the current server mod first")
    destination = (
        ROOT / "results/spatial" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    summary = {"development_only": True, "mod_provenance": provenance}
    summary["source_sha256"] = {
        name: sha256(ROOT / "src/mclab" / name)
        for name in ("navigation_segmented.py", "route_search.py", "spatial_memory.py")
    }
    with exclusive_arena():
        client = RecordedBridge(destination / "events.jsonl")
        client.budget = 1000
        active = False
        memory = None
        try:
            client.synchronize()
            if client.http("/state")["body"]:
                raise RuntimeError("Probe requires an idle lab")
            selected = Awareness.resolve("visible", vertical_radius_blocks=4)
            client.episode_id = "spatial-" + uuid4().hex[:12]
            client.body_id = "body-" + uuid4().hex[:12]
            request = client.send(
                "natural_start",
                subject="carpet_body",
                player="",
                start=[1000, 80, 1000],
                goal=[1000, 80, 1016],
                yaw=0,
                goal_radius=3,
                tick_limit=600,
                mobs=False,
                awareness=selected.payload(),
            )
            client.acknowledge(request, "natural_accepted")
            active = True
            client.wait(lambda state: state["state"] == "running", timeout=30)
            identity = MapIdentity(
                str((SERVER / "world").resolve()),
                client.episode_id,
                "minecraft:overworld",
                selected,
            )
            archive = MapArchive(destination / "map.sqlite3", identity)
            memory = SpatialMemory(archive, client.episode_id)
            controller = SegmentedController([1000, 80, 1016], memory)
            terminal = drive(client, controller, 50)
            summary["outcome"] = terminal["data"]
            summary["controller"] = controller.status()
            summary["persisted_before_close"] = len(memory.cells)
            memory.close()
            memory = None
            recalled = MapArchive(archive.path, identity).recall(
                (1000, 80, 1008),
                64,
                64,
                limit=20000,
            )
            summary["recalled_cells_after_reopen"] = len(recalled)
            if not recalled or terminal["data"]["status"] != "success":
                raise RuntimeError("Spatial integration failed; evidence retained")
            summary["status"] = "passed"
        except Exception as error:
            summary.update(status="error", error=str(error))
            raise
        finally:
            try:
                if memory is not None:
                    memory.close()
            finally:
                try:
                    if active:
                        request = client.send("natural_stop")
                        client.acknowledge(request, "received")
                    client.episode_id = "restore-" + uuid4().hex[:12]
                    client.body_id = "body-" + uuid4().hex[:12]
                    request = client.send(
                        "course_reset", profile_id="initial-c1-survival-v1"
                    )
                    client.acknowledge(request, "course_reset_complete", timeout=90)
                    client.send("despawn")
                    client.wait(lambda state: state["state"] == "idle")
                    summary["lab_restored"] = True
                finally:
                    client.close()
                    write_json(destination / "summary.json", summary)
    print(destination / "summary.json")


if __name__ == "__main__":
    main()
