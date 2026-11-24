"""``mcplay arena``: write arena plans, then build, start, stop, inspect and remove
arenas on the server.

``plan`` works offline (docs/ARENAS.md). Every server command prints one JSON object
(``ok`` first) and exits 0, or prints the mod's reason and exits 1.
docs/ARENA_RUNTIME.md has the rules and the events.
"""

import argparse
import json
import sys
from pathlib import Path

from mclab.arena import client as arena
from mclab.arena import designs, preview
from mclab.arena import plan as ap
from mclab.paths import ROOT


def plan(name: str, out: Path | None = None, previews: bool = True) -> dict:
    """Build, validate and write one arena or ``all``; invalid plans are not written."""
    if name != "all" and name not in designs.DESIGNS:
        raise ValueError(
            f"unknown arena {name!r}; choose from {', '.join(designs.NAMES)} or all"
        )
    plans_dir = out if out is not None else ROOT / "arenas"
    previews_dir = (
        out / "previews" if out is not None else ROOT / "results" / "arena-previews"
    )
    names = designs.NAMES if name == "all" else (name,)
    report = []
    for arena_name in names:
        built = designs.build(arena_name)
        problems = ap.validate(built)
        entry = {
            "name": arena_name,
            "size": list(built.size),
            "capacity": {
                "teams": built.capacity.teams,
                "per_team": built.capacity.per_team,
                "mobs": built.capacity.mobs,
            },
            "fills": len(built.fills),
            "problems": problems,
        }
        if not problems:
            plans_dir.mkdir(parents=True, exist_ok=True)
            path = plans_dir / f"{arena_name}.json"
            path.write_text(ap.to_json(built), encoding="utf-8", newline="\n")
            entry["plan"] = str(path)
            if previews:
                top, side = preview.render(built, previews_dir)
                entry["previews"] = [str(top), str(side)]
        report.append(entry)
    return {"arenas": report}


def _name(value: str) -> str:
    if not arena.NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{value!r}: use lowercase letters, digits, - and _ (at most 32)"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcplay arena", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    planner = sub.add_parser(
        "plan", help="write arenas/<name>.json and previews (offline)"
    )
    planner.add_argument("name", help=f"{', '.join(designs.NAMES)} or all")
    planner.add_argument(
        "--out", type=Path, help="write plans here and previews to OUT/previews"
    )
    planner.add_argument("--no-previews", action="store_true")
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
    start.add_argument(
        "--ready-timeout",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="first wait up to this long for each --players name to be online and "
        "each human to type /mcb ready (0, the default: start at once)",
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


def _echo(line: str) -> None:
    print(line, file=sys.stderr, flush=True)  # stdout ends with the JSON answer


def _wait_ready(args, client, names: list[str]) -> None:
    if args.ready_timeout > 0 and names:
        client.wait_ready(
            names, timeout=args.ready_timeout, arena=args.name, echo=_echo
        )


def _start(args, client, arenas: Path) -> dict:
    teams = arena.parse_teams(args.team)
    if args.mode == "teams":
        if args.waves:
            raise arena.ArenaError("bad_request", "the teams mode takes no waves")
        members = [p for team in (teams or {}).values() for p in team]
        _wait_ready(args, client, (args.players or []) + members)
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
    _wait_ready(args, client, args.players or [])
    return client.start(
        args.name, "waves", waves=waves, participants=args.players, teams=None
    )


def main(
    argv: list[str] | None = None, *, client=None, arenas: Path = arena.ARENAS
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":  # offline: no server needed
        try:
            result = plan(args.name, args.out, previews=not args.no_previews)
        except ValueError as error:
            print(
                json.dumps(
                    {"ok": False, "reason": "unknown_arena", "detail": str(error)}
                )
            )
            return 1
        print(json.dumps(result, indent=2))
        return 1 if any(a["problems"] for a in result["arenas"]) else 0
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
