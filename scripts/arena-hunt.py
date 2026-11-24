"""Arena hunts on the PLAY server: the bot clears a waves match, measured.

Needs the play server, its companion host and a built arena
(`mcplay arena build vault --at X Y Z --wait`). Each run heals and dresses the
bot, starts the match with it on a pad, sends `attack {arena}` and samples the
match once a second until it ends.

    uv run --env-file .env --locked python scripts/arena-hunt.py --arena vault \
        --waves vault-raid --repeat 3
    uv run --env-file .env --locked python scripts/arena-hunt.py --arena vault \
        --wave pillager*2 --wave pillager+vindicator --repeat 5 --label pairs

`--waves` takes a preset in arenas/waves/ or a file; `--wave` builds one wave per
flag from `type*count+type` (bare names are minecraft:), each after --delay ticks.
Writes results/companion/arena/<UTC stamp>.json and prints `HUNTS <path>`.
Docs: docs/ARENA_RUNTIME.md "The bot's hunt".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mclab import arena_hunt as ah
from mclab import combat_trial as ct
from mclab.arena import client as arena
from mclab.companion_api import ApiClient, ApiUnavailable

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arena", required=True, help="a built arena's name")
    parser.add_argument("--waves", help="a preset in arenas/waves/ or a file")
    parser.add_argument("--wave", action="append", help="one wave: type*count+type")
    parser.add_argument(
        "--delay", type=int, default=60, help="ticks before each --wave"
    )
    parser.add_argument("--players", nargs="+", default=["MCBot"])
    parser.add_argument(
        "--kit",
        default="all+armor+shield+trident",
        help="the trial kit handed out each run (combat-trial weapons joined by +), "
        "or 'keep' for what the bot carries",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="seconds per match"
    )
    parser.add_argument("--label", help="a name for the series in its file")
    parser.add_argument("--out", default=str(ROOT / "results" / "companion" / "arena"))
    args = parser.parse_args(argv)
    os.environ.setdefault("MCLAB_INSTANCE", "play")  # never the lab
    if bool(args.waves) == bool(args.wave):
        parser.error("give --waves or --wave (one of them)")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        waves = (
            arena.load_waves(args.waves)
            if args.waves
            else [ah.parse_wave(w, args.delay) for w in args.wave]
        )
        arena.check_waves(waves)
        kit = None if args.kit == "keep" else ct.kit(args.kit)
    except (ValueError, arena.ArenaError) as error:
        parser.error(str(error))
    try:
        api = ApiClient.from_runtime()
        api.events(0, wait=0)
        admin = arena.ArenaClient.from_state()
    except (ApiUnavailable, arena.ArenaError) as error:
        print(f"arena-hunt: {error}", file=sys.stderr)
        return 2
    path = ah.run_series(
        api,
        admin,
        args.arena,
        waves,
        repeat=args.repeat,
        participants=args.players,
        timeout=args.timeout,
        out_dir=args.out,
        label=args.label,
        kit=kit,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(record["summary"]))
    print(f"HUNTS {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
