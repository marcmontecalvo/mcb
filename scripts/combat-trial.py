"""Combat trials on the PLAY server: each weapon against each mob, measured.

Needs the play server and its companion host running
(`uv run --env-file .env --locked mcplay server start`). It talks to the host's
control API only (the scene goes through `POST /trial`, an operator route that is
never an MCP tool), so the mod's request sequence stays the host's.

    uv run --locked python scripts/combat-trial.py
    uv run --locked python scripts/combat-trial.py --weapons sword,bow \
        --mobs zombie,creeper

Writes results/companion/trials/<UTC stamp>.json. Docs:
docs/COMPANION_COMBAT_PLAN_2026-11-23.md section 5.
"""

import argparse
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
    parser.add_argument("--out", default=str(ROOT / "results" / "companion" / "trials"))
    parser.add_argument(
        "--arena",
        type=ct.parse_arena,
        help="X,Y,Z every trial is fought at (default: where the bot stands at the "
        "start); a bot that drifted more than a few blocks walks back first",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("MCLAB_INSTANCE", "play")  # never the lab
    weapons = [w for w in args.weapons.split(",") if w]
    mobs = [m for m in args.mobs.split(",") if m]
    for weapon in weapons:
        ct.kit(weapon)  # refuse an unknown weapon before anything runs
    for mob in mobs:
        ct.scene("fist", mob)
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
    )
    print(f"TRIALS {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
