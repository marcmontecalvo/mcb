# ruff: noqa: E501
"""Live gather run (companion track, M4 on the real body). Written, not yet run.

Usage (lab server up, idle lab; start = a feet cell next to trees and stone):
    uv run --locked python scripts/run-gather.py --start X Y Z [--iron]

Kits an empty inventory, then targets a stone pickaxe and (with --iron) an iron pickaxe,
through companion_body.CompanionBody + gather_runner.run_gather. Results folder: results/companion/<stamp>/.
"""

import argparse
import json
import sys

from live_common import finish, kit, record_crash, results_folder

from mclab.companion_body import (
    companion_world,
    end_session,
    open_bridge,
    start_session,
)
from mclab.companion_jobs import load_vanilla_pack
from mclab.companion_progress import write_progress
from mclab.gather_options import GatherOptions
from mclab.gather_plan import plan, summarize
from mclab.gather_runner import GatherState, run_gather
from mclab.recorder import exclusive_arena
from mclab.run_config import policies
from mclab.run_config_cli import invocation, parse_run
from mclab.run_launch import launch_record, record_outcome
from mclab.run_launch import record_effective_run as record_run

M = "minecraft:"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", nargs=3, type=int, required=True)
    parser.add_argument(
        "--iron", action="store_true", help="then target an iron pickaxe"
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="run in a fresh copy of this natural seed's world (the lab world is flat)",
    )
    parser.add_argument(
        "--no-climb",
        action="store_true",
        help="disable climbing (ladders/scaffolding) for this run; the seed 20261026 "
        "iron baseline dropped 9/9 to 7/9 when climbing turned on (Nov 3 suspect)",
    )
    parser.add_argument(
        "--tool-choice",
        choices=("lowest_sufficient", "best"),
        default="lowest_sufficient",
    )
    parser.add_argument(
        "--ore-collect", choices=("greedy", "specific"), default="greedy"
    )
    parser.add_argument("--no-ore-vision", action="store_true", help="old ore reach")
    parser.add_argument("--vision-radius", type=int, default=32, help="max 64")
    parser.add_argument("--search-radius", type=int, help="default: all sensed")
    parser.add_argument(
        "--strip-leg", type=int, default=64, help="blocks per strip leg"
    )
    parser.add_argument(
        "--strip-turn", type=int, default=16, help="blocks per connector"
    )
    parser.add_argument("--strip-max-legs", type=int, default=8, help="0: unlimited")
    parser.add_argument("--spare-pickaxes", type=int, default=3, help="carried below")
    parser.add_argument("--carry-logs", type=int, default=16, help="0: no kit")
    parser.add_argument(
        "--raw-iron", type=int, metavar="N", help="mine N raw iron, no smelting"
    )
    args, config = parse_run(parser, "gather")
    with launch_record(config):
        result = run(args, config)
        record_outcome(result)
        return result


def run(args, config):
    opts = GatherOptions(**config["run"]["gather"])
    folder = results_folder()
    folder.mkdir(parents=True, exist_ok=True)
    record_run(folder, config, invocation=invocation())
    write_progress(folder, job="gather", stage="starting")
    knowledge = load_vanilla_pack()
    rows: list[dict] = []
    state = GatherState()
    with exclusive_arena(), companion_world(args.seed):
        bridge = open_bridge(folder)
        memory = None
        try:
            body, memory = start_session(
                bridge,
                folder,
                args.start,
                dig=True,
                **policies(config),
            )
            kit(bridge, inventory=[])
            body.observe()
            targets = [(M + "stone_pickaxe", "stone pickaxe")]
            if args.raw_iron:
                targets.append((M + "raw_iron", "raw iron"))
            elif args.iron:
                targets.append((M + "iron_pickaxe", "iron pickaxe"))
            for i, (item, label) in enumerate(targets):
                write_progress(
                    folder, job="gather", stage=label, done=i, total=len(targets)
                )
                wanted = {item: args.raw_iron if item == M + "raw_iron" else 1}
                first = plan(
                    wanted,
                    body.inventory(),
                    knowledge,
                    state.stations,
                    starter_tools=True,
                    kit=opts,
                )
                print(summarize(first))
                report = run_gather(
                    first, body, knowledge, wanted, state=state, opts=opts
                )
                body.observe()
                (folder / f"gather-{label.replace(' ', '-')}.json").write_text(
                    json.dumps(
                        {
                            "stop": report.stop,
                            "done": report.done,
                            "failures": report.failures,
                            "replans": report.replans,
                            "ate": report.ate,
                            "stations": {k: list(v) for k, v in state.stations.items()},
                            "ops": body.log,
                            "policies": body.policies,
                        },
                        indent=1,
                    ),
                    encoding="utf-8",
                )
                rows.append(
                    {
                        "case": label,
                        "pass": body.inventory().get(item, 0) >= wanted[item],
                        "detail": f"stop={report.stop} tasks={len(report.done)} failures={report.failures[:3]}",
                    }
                )
        except Exception as error:  # noqa: BLE001 - recorded, run ends as failed
            record_crash(folder, rows, error)
        finally:
            try:
                if memory is not None:
                    end_session(bridge, memory)
            finally:
                bridge.close()
    return finish(folder, rows, "run-gather")


if __name__ == "__main__":
    sys.exit(main())
