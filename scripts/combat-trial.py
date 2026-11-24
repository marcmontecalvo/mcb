"""Combat trials on the PLAY server: each weapon against each mob, measured.

Needs the play server and its companion host running
(`uv run --env-file .env --locked mcplay server start`). It talks to the host's
control API only (the scene goes through `POST /trial`, an operator route that is
never an MCP tool), so the mod's request sequence stays the host's.

    uv run --locked python scripts/combat-trial.py --repeat 10
    uv run --locked python scripts/combat-trial.py --weapons sword,bow \
        --mobs zombie,creeper,skeleton*2,zombie*2+skeleton --repeat 5 \
        --baseline results/companion/trials/<earlier>.json
    uv run --locked python scripts/combat-trial.py --weapons spear+sword \
        --arena=-281,77,-37

Give --arena with "=" when a coordinate is negative (argparse reads "-281" as a flag).
A weapon cell may join several (``spear+sword``: the charge hands over to the sword).
A mob cell is a mob or a group (``zombie*3``, ``zombie*2+skeleton``); a group trial
is a kill once every mob of it is dead. The repeats go round the whole matrix.
Writes results/companion/trials/<UTC stamp>.json: every trial, the per-cell
``summary`` (kill rate, time-to-kill spread, health left, hits, crit rate, flees,
ends, failures) and the ``regressions`` against --baseline. Prints the summary
table and `TRIALS <path>` at the end. Docs:
docs/COMPANION_COMBAT_PLAN_2026-11-23.md section 5.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from mclab import combat_trial as ct
from mclab.companion_api import ApiClient, ApiUnavailable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEAPONS = "sword,axe,spear,bow"
DEFAULT_MOBS = "zombie,skeleton,creeper,pig"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weapons", default=DEFAULT_WEAPONS)
    parser.add_argument("--mobs", default=DEFAULT_MOBS)
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds per trial")
    parser.add_argument("--repeat", type=int, default=10, help="times round the matrix")
    parser.add_argument(
        "--baseline", help="an earlier trials file to flag regressions against"
    )
    parser.add_argument("--out", default=str(ROOT / "results" / "companion" / "trials"))
    parser.add_argument(
        "--arena",
        type=ct.parse_arena,
        help="X,Y,Z every trial is fought at (default: where the bot stands at the "
        "start); a bot that drifted more than a few blocks walks back first",
    )
    parser.add_argument(
        "--tier",
        default=ct.DEFAULT_TIER,
        help=f"sword, axe and spear material ({', '.join(ct.WEAPON_TIERS)})",
    )
    parser.add_argument(
        "--armor",
        default=ct.DEFAULT_TIER,
        help=f"armor material ({', '.join(ct.ARMOR_TIERS)})",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("MCLAB_INSTANCE", "play")  # never the lab
    weapons = [w for w in args.weapons.split(",") if w]
    mobs = [m for m in args.mobs.split(",") if m]
    gear = {"tier": args.tier, "armor": args.armor}
    for weapon in weapons:
        try:  # refuse an unknown weapon or material before anything runs
            ct.kit(weapon, **gear)
        except ValueError as error:
            parser.error(str(error))
    for mob in mobs:
        ct.scene("fist", mob)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    baseline = None
    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        baseline = base.get("summary") or ct.summary(base.get("trials", []))
    try:
        client = ApiClient.from_runtime()
        client.events(0, wait=0)
    except ApiUnavailable as error:
        print(f"combat-trial: {error}", file=sys.stderr)
        return 2
    arena = args.arena or ct.locate(client)
    if arena is None:
        print("combat-trial: the bot's position is unknown; give --arena X,Y,Z",
              file=sys.stderr)  # fmt: skip
        return 2
    print(f"ARENA {arena[0]} {arena[1]} {arena[2]}")
    path = ct.run_matrix(
        client,
        weapons,
        mobs,
        out_dir=args.out,
        timeout=args.timeout,
        echo=print,
        arena=arena,
        repeat=args.repeat,
        baseline=baseline,
        gear=gear,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    print()
    print(ct.summary_table(record["summary"]))
    for r in record["regressions"]:
        print(f"REGRESSION {r['cell']} {r['metric']}: {r['was']} -> {r['now']}")
    print(f"TRIALS {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
