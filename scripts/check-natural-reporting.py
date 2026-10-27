"""Native single-lethal-hit diagnostic; restores the lab after the unscored run."""

from datetime import UTC, datetime
from uuid import uuid4

from mclab.execution import RecordedBridge, terminal_event
from mclab.modbuild import source_hash
from mclab.paths import ROOT, SERVER, read_json, write_json
from mclab.recorder import exclusive_arena


def main():
    if (
        "level-name=world"
        not in (SERVER / "server.properties").read_text().splitlines()
    ):
        raise RuntimeError("Reporting diagnostic requires the flat lab")
    provenance = read_json(ROOT / "runtime/mod-build.json")
    if provenance["source_hash"] != source_hash():
        raise RuntimeError("Install the current server mod first")
    folder = (
        ROOT
        / "results/natural_reporting"
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    summary = {"development_only": True, "mod_provenance": provenance}
    with exclusive_arena():
        client = RecordedBridge(folder / "events.jsonl")
        active = False
        try:
            client.synchronize()
            if client.http("/state")["body"]:
                raise RuntimeError("Reporting diagnostic requires an idle lab")
            request = client.send(
                "natural_start",
                subject="carpet_body",
                player="",
                start=[1280, 80, 1280],
                goal=[1280, 83, 1291],
                yaw=0,
                goal_radius=1,
                tick_limit=400,
                mobs=False,
                development_hill="walk",
                hill_variant=0,
            )
            client.acknowledge(request, "natural_accepted")
            active = True
            client.wait(lambda s: s["state"] == "running", timeout=45)
            # Let ordinary post-spawn damage immunity expire before the one-hit
            # diagnostic; an ignored hit is not evidence about lethal reporting.
            started = client.http("/state")["tick"]
            client.wait(lambda s: s["tick"] >= started + 100, timeout=15)
            health = client.http("/state")["body"]["health"]
            request = client.send("death_proof")
            client.acknowledge(request, "received")
            client.wait(lambda _: terminal_event(client) is not None, timeout=10)
            outcome = terminal_event(client)["data"]
            summary.update(outcome=outcome, health_before=health)
            if outcome["reason"] != "death_or_disconnect":
                raise AssertionError(
                    "Single lethal diagnostic hit did not kill the subject"
                )
            if outcome["status"] != "failed" or outcome["damage_taken"] != health:
                raise AssertionError("Lethal health loss was not fully reported")
            if outcome["kit_blocks_spent_adjusted"] != -1:
                raise AssertionError(
                    "Death inventory loss counted as building stock spend"
                )
            summary["status"] = "passed"
        except Exception as error:
            summary.update(status="error", error=str(error))
            raise
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
                client.wait(lambda s: s["state"] == "idle")
                summary["lab_restored"] = True
            finally:
                client.close()
                write_json(folder / "summary.json", summary)
    print(folder / "summary.json", flush=True)


if __name__ == "__main__":
    main()
