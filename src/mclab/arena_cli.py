"""``mcplay arena``: build, start, stop, inspect and remove arenas on the server.

Every command prints one JSON object (``ok`` first) and exits 0, or prints the mod's
reason and exits 1. docs/ARENA_RUNTIME.md has the rules and the events.
"""

import argparse
import json
import sys
from pathlib import Path

from mclab import arena


def _name(value: str) -> str:
    if not arena.NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{value!r}: use lowercase letters, digits, - and _ (at most 32)"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcplay arena", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="place arenas/<name>.json at --at x y z")
    build.add_argument("name", type=_name, help="the plan file arenas/<name>.json")
    build.add_argument(
        "--at",
        nargs=3,
        type=int,
        required=True,
        metavar=("X", "Y", "Z"),
        help="the plan's min corner",
    )
    build.add_argument("--plan", help="a plan file path instead of arenas/<name>.json")
    build.add_argument(
        "--as",
        dest="registered",
        type=_name,
        help="register under this name, not the plan's",
    )
    build.add_argument("--dimension", help="e.g. minecraft:overworld (the default)")
    build.add_argument(
        "--wait", action="store_true", help="stay until the arena is built"
    )
    build.add_argument(
        "--timeout", type=float, default=600.0, help="seconds --wait may take"
    )
    start = sub.add_parser("start", help="start a match")
    start.add_argument("name", type=_name)
    start.add_argument("--mode", choices=["waves", "teams"], default="waves")
    start.add_argument(
        "--waves",
        help="a preset in arenas/waves/ (vault-raid, mixed-10) or a file path",
    )
    start.add_argument(
        "--players",
        nargs="+",
        metavar="NAME",
        help="participants (default: everyone inside the arena)",
    )
    start.add_argument(
        "--team",
        action="append",
        metavar="TEAM=A,B",
        help="teams mode: a team and its players",
    )
    for command, text in [
        ("stop", "end the match and discard its mobs"),
        ("status", "state, wave, mobs, participants"),
    ]:
        sub.add_parser(command, help=text).add_argument("name", type=_name)
    sub.add_parser("list", help="every registered arena")
    remove = sub.add_parser(
        "remove", help="unregister an arena (--restore clears it to air)"
    )
    remove.add_argument("name", type=_name)
    remove.add_argument("--restore", action="store_true")
    return parser


def _build(args, client, arenas: Path) -> dict:
    plan = arena.load_plan(args.plan or args.name, root=arenas)
    ack = client.build(
        plan, list(args.at), name=args.registered, dimension=args.dimension
    )
    if not args.wait:
        return ack
    name = ack.get("name") or args.registered or plan.get("name", args.name)

    def progress(event):
        print(f"{event['type']}: {event['data'].get('percent', '')}%", file=sys.stderr)

    event = client.wait_for(
        name,
        {"arena_built", "arena_build_aborted"},
        timeout=args.timeout,
        on_event=progress,
    )
    if event["type"] == "arena_build_aborted":
        raise arena.ArenaError(
            str(event["data"].get("reason", "aborted")), f"building {name} stopped"
        )
    return {**ack, "built": event["data"]}


def _start(args, client, arenas: Path) -> dict:
    teams = arena.parse_teams(args.team)
    if args.mode == "teams":
        if args.waves:
            raise arena.ArenaError("bad_request", "the teams mode takes no waves")
        return client.start(
            args.name, "teams", waves=None, participants=args.players, teams=teams
        )
    if teams:
        raise arena.ArenaError("bad_request", "--team belongs to --mode teams")
    if not args.waves:
        raise arena.ArenaError(
            "bad_request",
            "waves mode needs --waves (presets: "
            + ", ".join(arena.waves_presets(arenas / "waves"))
            + ")",
        )
    waves = arena.load_waves(args.waves, root=arenas / "waves")
    return client.start(
        args.name, "waves", waves=waves, participants=args.players, teams=None
    )


def main(
    argv: list[str] | None = None, *, client=None, arenas: Path = arena.ARENAS
) -> int:
    args = _parser().parse_args(argv)
    try:
        client = client or arena.ArenaClient.from_state()
        match args.command:
            case "build":
                answer = _build(args, client, arenas)
            case "start":
                answer = _start(args, client, arenas)
            case "stop":
                answer = client.stop(args.name)
            case "status":
                answer = client.status(args.name)
            case "list":
                answer = client.list()
            case "remove":
                answer = client.remove(args.name, restore=args.restore)
    except arena.ArenaError as error:
        print(json.dumps({"ok": False, "reason": error.reason, "detail": error.detail}))
        return 1
    print(json.dumps({"ok": True, **answer}))
    return 0
