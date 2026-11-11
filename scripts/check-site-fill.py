"""Development-only natural-pond fill regression; restores the lab afterward."""

from datetime import UTC, datetime
from types import SimpleNamespace

from live_common import kit

from mclab import build_plan as bp
from mclab import site_prep as sp
from mclab.build_runner import BuildReport
from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.paths import ROOT, write_json
from mclab.recorder import exclusive_arena


def main():
    folder = ROOT / "results/site-fill" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True)
    summary = {"development_only": True, "passed": False, "seed": 31415926}
    try:
        with exclusive_arena(), companion_world(31415926):
            bridge = open_bridge(folder)
            memory = None
            try:
                body, memory = start_session(bridge, folder, (-16, 62, 70))
                kit(bridge, inventory=[{"item": "minecraft:dirt", "count": 64}] * 4)
                body.observe()
                # Isolate the fill phase: production site prep clears this volume
                # before filling. Keep the natural pond floor and water intact.
                clear = [
                    (x, y, z)
                    for x in range(-22, -14)
                    for z in range(59, 67)
                    for y in range(64, 73)
                    if body.world(x, y, z) not in (None, "minecraft:air")
                ]
                summary["fixture_clear_above_ground"] = clear
                for i in range(0, len(clear), 64):
                    kit(
                        bridge,
                        place=[
                            {
                                "offset": [x + 16, y - 62, z - 70],
                                "state": "minecraft:air",
                            }
                            for x, y, z in clear[i : i + 64]
                        ],
                    )
                body.wait_ticks(10)
                body.observe()
                fills = []
                for x in range(-21, -15):
                    for z in range(60, 66):
                        column = []
                        for y in range(63, 55, -1):
                            state = body.world(x, y, z)
                            if state is None:
                                raise RuntimeError("Unknown regression floor")
                            if (
                                bp.parse_state(state)[0]
                                not in bp.AIR | bp.FLUIDS | sp.FLOWERS
                            ):
                                break
                            column.append((x, y, z))
                        fills.extend(reversed(column))
                if not fills or len(fills) > 256:
                    raise RuntimeError(f"Invalid regression fill count: {len(fills)}")
                summary["fills"] = fills
                report = BuildReport()
                runner = SimpleNamespace(report=report, max_steps=1000)
                n = sp._fill_sweep(body, runner, fills, 256, 63)
                summary.update(placed=n, expected=len(fills), after=body.position())
                # A move only counts when it enabled an edit. This is the actual
                # sweep used by site prep, starting below the future ground level.
                body.wait_ticks(10)
                body.observe()
                summary["final_states"] = [
                    {"pos": p, "state": body.world(*p)} for p in fills
                ]
                # Exposed dirt can legitimately grow grass during the sweep.
                # Lower cells must remain dirt; neither water nor an empty hole
                # counts as filled, even if the placement counter is complete.
                summary["mismatches"] = [
                    row
                    for row in summary["final_states"]
                    if bp.parse_state(row["state"] or "")[0]
                    not in (
                        {"minecraft:dirt", "minecraft:grass_block"}
                        if row["pos"][1] == 63
                        else {"minecraft:dirt"}
                    )
                ]
                summary["passed"] = n == len(fills) and not summary["mismatches"]
                print(
                    {
                        k: v
                        for k, v in summary.items()
                        if k
                        not in ("fills", "fixture_clear_above_ground", "final_states")
                    },
                    flush=True,
                )
                if not summary["passed"]:
                    raise RuntimeError("Incomplete natural-pond fill regression")
            finally:
                try:
                    if memory is not None:
                        summary["ops"] = body.log
                        end_session(bridge, memory)
                finally:
                    bridge.close()
    except Exception as exc:
        summary["error"] = repr(exc)
        raise
    finally:
        write_json(folder / "summary.json", summary)
        print(folder / "summary.json", flush=True)


if __name__ == "__main__":
    main()
