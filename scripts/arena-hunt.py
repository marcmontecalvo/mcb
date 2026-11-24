"""Arena matches on the PLAY or ARENA server: the bot and humans fight, measured.

Needs the server, a built arena (or `--world` on the arena server, which builds the
lobby and the arena itself) and, when the bot plays, its companion host. Each run
dresses everyone (the bot and the humans get the same kit), starts the match, sends
the bot `attack {arena}` when it is in the roster and samples the match once a
second until it ends.

    uv run --env-file .env --locked python scripts/arena-hunt.py --arena vault \\
        --waves vault-raid --repeat 3
    uv run --env-file .env --locked python scripts/arena-hunt.py --arena vault \\
        --wave pillager*2 --wave pillager+vindicator --repeat 5 --label pairs
    uv run --env-file .env --locked python scripts/arena-hunt.py --arena duel \\
        --mode teams --team red=MCBot,Ann --team blue=Bob,Cy --op Ann --world

Modes: `waves` (co-op: everyone against the mob waves), `teams` (team against
team; mob waves optional) and `race` (teams race to kill the most wave mobs).
`--waves` takes a preset in arenas/waves/ or a file; `--wave` builds one wave per
flag from `type*count+type` (bare names are minecraft:), each after --delay ticks.
Writes results/companion/arena/<UTC stamp>.json and prints `HUNTS <path>`.
Docs: docs/ARENA_RUNTIME.md "The bot's hunt" and "The arena server".
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
from mclab.arena import world
from mclab.companion_api import ApiClient, ApiUnavailable

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arena", required=True, help="the arena's name")
    parser.add_argument("--mode", choices=["waves", "teams", "race"], default="waves")
    parser.add_argument("--waves", help="a preset in arenas/waves/ or a file")
    parser.add_argument("--wave", action="append", help="one wave: type*count+type")
    parser.add_argument(
        "--delay", type=int, default=60, help="ticks before each --wave"
    )
    parser.add_argument(
        "--players", nargs="+", help="waves mode: the participants (default MCBot)"
    )
    parser.add_argument(
        "--team",
        action="append",
        metavar="TEAM=A,B",
        help="teams and race modes: a team and its players, one flag per team",
    )
    parser.add_argument("--bot", default=ah.BOT, help="the bot's player name")
    parser.add_argument(
        "--op",
        action="append",
        default=[],
        metavar="NAME",
        help="make NAME an operator",
    )
    parser.add_argument(
        "--deop", action="append", default=[], metavar="NAME", help="take it away"
    )
    parser.add_argument(
        "--world",
        action="store_true",
        help="the arena server: build the lobby and this arena when missing, make "
        "the lobby the spawn, and send players back to it after each match",
    )
    parser.add_argument(
        "--exit",
        nargs=3,
        type=int,
        metavar=("X", "Y", "Z"),
        help="where players still in the arena go when a match ends",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=900.0,
        help="seconds to wait, before the first match, for every player to be "
        "online and each human to type /mcb ready",
    )
    parser.add_argument(
        "--kit",
        default="all+armor+shield+trident+food+potions",
        help="the kit everyone gets each run (combat-trial weapons joined by +), "
        "or 'keep' for what each carries",
    )
    parser.add_argument(
        "--tier",
        default=ct.DEFAULT_TIER,
        help=f"the kit's sword, axe and spear material ({', '.join(ct.WEAPON_TIERS)})",
    )
    parser.add_argument(
        "--armor",
        default=ct.DEFAULT_TIER,
        help=f"the kit's armor material ({', '.join(ct.ARMOR_TIERS)})",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--timeout", type=float, default=600.0, help="seconds per match"
    )
    parser.add_argument("--label", help="a name for the series in its file")
    parser.add_argument("--out", default=str(ROOT / "results" / "companion" / "arena"))
    return parser


def _roster(parser, args) -> tuple[list[str] | None, dict | None]:
    teams = arena.parse_teams(args.team)
    if args.mode == "waves":
        if teams:
            parser.error("--team belongs to --mode teams or race")
        return args.players or [args.bot], None
    if args.players or not teams:
        parser.error(f"--mode {args.mode} takes its players by --team, one per team")
    return None, teams


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    os.environ.setdefault("MCLAB_INSTANCE", "play")  # never the lab
    if args.waves and args.wave:
        parser.error("give --waves or --wave, not both")
    if args.mode != "teams" and not (args.waves or args.wave):
        parser.error(f"--mode {args.mode} needs --waves or --wave")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        participants, teams = _roster(parser, args)
        waves = (
            arena.load_waves(args.waves)
            if args.waves
            else [ah.parse_wave(w, args.delay) for w in args.wave or ()]
        )
        if waves:
            arena.check_waves(waves)
        kit = (
            None
            if args.kit == "keep"
            else ct.kit(args.kit, tier=args.tier, armor=args.armor)
        )
    except (ValueError, arena.ArenaError) as error:
        parser.error(str(error))
    people = ah.roster(participants, teams)
    ops = {name: True for name in args.op} | {name: False for name in args.deop}
    exit_cell = tuple(args.exit) if args.exit else None
    echo = lambda line: print(line, flush=True)  # noqa: E731
    try:
        api = None
        if any(p.lower() == args.bot.lower() for p in people):
            api = ApiClient.from_runtime()
            api.events(0, wait=0)
        admin = arena.ArenaClient.from_state()
        if args.world:
            ready = world.prepare(admin, args.arena, echo=echo)
            exit_cell = exit_cell or tuple(ready["spawn"])
        admin.wait_ready(
            people, timeout=args.ready_timeout, arena=args.arena, echo=echo
        )
    except (ApiUnavailable, arena.ArenaError) as error:
        print(f"arena-hunt: {error}", file=sys.stderr)
        return 2
    path = ah.run_series(
        api,
        admin,
        args.arena,
        waves,
        repeat=args.repeat,
        participants=participants,
        timeout=args.timeout,
        out_dir=args.out,
        mode=args.mode,
        teams=teams,
        exit=exit_cell,
        ops=ops,
        bot=args.bot,
        echo=echo,
        label=args.label,
        kit=kit,
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(record["summary"]))
    print(f"HUNTS {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
