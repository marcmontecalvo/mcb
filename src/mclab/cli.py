"""The same command surface is used in PowerShell and VS Code tasks."""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mclab import (
    backends,
    bootstrap,
    courses,
    episodeproof,
    evidence,
    execution,
    experiments,
    gates,
    modbuild,
    model_execution,
    protocolproof,
    qualify,
    reference,
    referenceproof,
    resetcheck,
    screening,
    server,
)
from mclab.paths import ROOT, SERVER, config, lock, read_json, write_json


def doctor() -> dict:
    settings = config()
    checks = []
    try:
        result = subprocess.run(
            [str(bootstrap.java()), "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        checks.append(
            {
                "check": "project_jdk",
                "ok": 'version "25.' in result.stderr,
                "observed": result.stderr.splitlines()[0],
            }
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        checks.append({"check": "project_jdk", "ok": False, "error": str(error)})
    for name, artifact in lock()["artifacts"].items():
        path = ROOT / "runtime/downloads" / artifact["name"]
        checks.append(
            {
                "check": "checksum:" + name,
                "ok": path.exists() and bootstrap.sha256(path) == artifact["sha256"],
            }
        )
    for key in ("game_port", "bridge_port", "supervisor_port"):
        checks.append(
            {
                "check": key,
                "available": server.port_free(settings[key]),
                "port": settings[key],
            }
        )
    for name, relative in {
        "minecraft_server": "server.jar",
        "fabric_launcher": "fabric-server-launch.jar",
        "carpet": "mods/" + lock()["artifacts"]["carpet"]["name"],
        "fabric_api": "mods/" + lock()["artifacts"]["fabric_api"]["name"],
    }.items():
        path = SERVER / relative
        checks.append(
            {
                "check": "installed:" + name,
                "ok": path.exists()
                and bootstrap.sha256(path) == lock()["artifacts"][name]["sha256"],
            }
        )
    mod_record = ROOT / "runtime/mod-build.json"
    mod_jar = SERVER / "mods/mclab-bootstrap-0.1.0.jar"
    checks.append(
        {
            "check": "installed:project_mod",
            "ok": mod_record.exists()
            and mod_jar.exists()
            and bootstrap.sha256(mod_jar) == read_json(mod_record)["jar_sha256"]
            and modbuild.source_hash() == read_json(mod_record)["source_hash"],
        }
    )
    checks.append(
        {
            "check": "eula",
            "ok": (SERVER / "eula.txt").exists()
            and "eula=true" in (SERVER / "eula.txt").read_text().splitlines(),
        }
    )
    checks.append(
        {
            "check": "generated_course",
            "ok": (SERVER / "world/datapacks/mclab/pack.mcmeta").exists(),
        }
    )
    properties = SERVER / "server.properties"
    checks.append(
        {
            "check": "continuous_ticks_when_empty",
            "ok": properties.exists()
            and "pause-when-empty-seconds=-1" in properties.read_text().splitlines(),
        }
    )
    result = {
        "schema_version": 1,
        "checks": checks,
        "server": server.status(),
        "gates": gates.statuses(),
    }
    write_json(ROOT / "results/doctor.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("doctor")
    sub.add_parser("report")
    sub.add_parser("mod-install")
    experiment = sub.add_parser("experiments").add_subparsers(
        dest="operation", required=True
    )
    experiment.add_parser("plan").add_argument("--output", type=Path)
    experiment.add_parser("screen-build")
    experiment.add_parser("screen-report")
    screen_run = experiment.add_parser("screen-run")
    screen_run.add_argument("--backend", required=True)
    screen_run.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "results/experiments/tactical_screen_v1.json",
    )
    screen_run.add_argument("--offset", type=int, default=0)
    screen_run.add_argument("--limit", type=int, default=36)
    screen = experiment.add_parser("export-screening")
    screen.add_argument("--recording", type=Path, required=True)
    screen.add_argument("--output", type=Path, required=True)
    screen.add_argument("--limit", type=int, default=60)
    models = sub.add_parser("models").add_subparsers(dest="operation", required=True)
    models.add_parser("probe").add_argument("--backend")
    live = models.add_parser("live-smoke")
    live.add_argument("--backend", required=True)
    live.add_argument("--count", type=int, default=1)
    live.add_argument("--request-limit", type=int, default=40)
    live.add_argument("--planner-backend")
    live.add_argument("--planner-profile", type=Path)
    live.add_argument("--planner-request-limit", type=int)
    body = sub.add_parser("body").add_subparsers(dest="operation", required=True)
    body.add_parser("qualify")
    body.add_parser("protocol-check")
    body.add_parser("reference-fixture-check")
    plan = body.add_parser("reference-plan")
    plan.add_argument("--mechanic", required=True, choices=reference.MECHANICS)
    capture = body.add_parser("reference")
    capture.add_argument("--player", required=True)
    capture.add_argument("--seconds", type=int, default=45)
    capture.add_argument("--interactions", action="store_true")
    capture.add_argument(
        "--combat",
        action="store_true",
        help="Prepare interaction fixtures and a stone backstop behind the test cow",
    )
    capture.add_argument("--mechanic", choices=reference.MECHANICS, default="none")
    srv = sub.add_parser("server").add_subparsers(dest="operation", required=True)
    srv.add_parser("start").add_argument("--accept-eula", action="store_true")
    srv.add_parser("stop")
    srv.add_parser("status")
    course = sub.add_parser("course").add_subparsers(dest="operation", required=True)
    build = course.add_parser("build")
    build.add_argument("--course", choices=["C0", "C1", "C2", "C3", "C4", "C5"])
    build.add_argument("--seed", type=int)
    course.add_parser("verify-generated")
    course.add_parser("verify-bootstrap")
    course.add_parser("episode-check")
    resets = course.add_parser("reset-check")
    resets.add_argument("--count", type=int, default=20)
    execute = course.add_parser("execution-check")
    execute.add_argument("--count", type=int, default=10)
    execute.add_argument("--development", action="store_true")
    args = parser.parse_args()
    try:
        match args.command:
            case "bootstrap":
                server.assert_stopped()
                result = bootstrap.bootstrap()
            case "doctor":
                result = doctor()
            case "report":
                result = evidence.report()
            case "mod-install":
                result = modbuild.install()
            case "experiments":
                if args.operation == "plan":
                    result = experiments.development_plan(args.output)
                elif args.operation == "screen-build":
                    fixture = screening.build()
                    result = {
                        "states": len(fixture["states"]),
                        "status": "frozen_synthetic_fixture",
                    }
                elif args.operation == "screen-run":
                    value = screening.run(
                        args.backend, args.fixture, offset=args.offset, limit=args.limit
                    )
                    result = {
                        k: value[k]
                        for k in (
                            "artifact",
                            "status",
                            "attempted",
                            "correct",
                            "errors",
                        )
                    }
                elif args.operation == "screen-report":
                    result = screening.report()
                else:
                    result = experiments.export_screening(
                        args.recording, args.output, args.limit
                    )
            case "models":
                if args.operation == "live-smoke":
                    if args.planner_backend:
                        if args.planner_profile is None:
                            raise ValueError("Tandem mode requires --planner-profile")
                        result = model_execution.run_tandem(
                            args.backend,
                            args.planner_backend,
                            planner_profile=read_json(args.planner_profile),
                            count=args.count,
                            request_limit=args.request_limit,
                            planner_request_limit=4
                            if args.planner_request_limit is None
                            else args.planner_request_limit,
                        )
                    else:
                        if (
                            args.planner_profile is not None
                            or args.planner_request_limit is not None
                        ):
                            raise ValueError(
                                "Planner options require --planner-backend"
                            )
                        result = model_execution.run(
                            args.backend,
                            count=args.count,
                            request_limit=args.request_limit,
                        )
                    print(json.dumps(result, indent=2))
                    return 0 if result["status"] == "completed_development_smoke" else 1
                specs = read_json(ROOT / "config/backends.json")["backends"]
                if args.backend:
                    specs = [s for s in specs if s["name"] == args.backend]
                    if len(specs) != 1:
                        raise ValueError("Select exactly one configured backend")
                outcomes = backends.probe_config(specs)
                result = {
                    "schema_version": 1,
                    "run_id": "probe-" + uuid4().hex[:12],
                    "status": "not_ready"
                    if any(o["status"] == "not_ready" for o in outcomes)
                    else "complete"
                    if outcomes
                    else "skipped_no_configured_backends",
                    "backends": outcomes,
                }
                write_json(ROOT / "results/provider_probes/latest.json", result)
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                write_json(ROOT / "results/provider_probes" / (stamp + ".json"), result)
            case "body":
                if args.operation == "qualify":
                    result = qualify.qualify_commands()
                elif args.operation == "protocol-check":
                    result = protocolproof.qualify_protocol()
                elif args.operation == "reference-plan":
                    result = reference.reference_plan(args.mechanic)
                elif args.operation == "reference-fixture-check":
                    result = referenceproof.qualify_reference_fixtures()
                else:
                    result = reference.record_reference(
                        args.player,
                        args.seconds,
                        args.interactions,
                        args.combat,
                        args.mechanic,
                    )
            case "server":
                if args.operation == "start":
                    result = server.start(args.accept_eula)
                else:
                    result = (
                        server.stop() if args.operation == "stop" else server.status()
                    )
            case "course":
                if args.operation == "build":
                    server.assert_stopped()
                    settings = config()
                    selected = args.course or settings["active_course"]
                    seed = (
                        args.seed if args.seed is not None else settings["course_seed"]
                    )
                    result = courses.build(selected, seed)
                elif args.operation == "verify-generated":
                    result = courses.verify_generated()
                elif args.operation == "reset-check":
                    result = resetcheck.qualify_resets(args.count)
                elif args.operation == "episode-check":
                    result = episodeproof.qualify_episodes()
                elif args.operation == "execution-check":
                    result = execution.qualify_execution(
                        args.count, development=args.development
                    )
                else:
                    result = evidence.verify_bootstrap()
        print(json.dumps(result, indent=2))
        if args.command == "doctor":
            return 0 if all(c.get("ok", True) for c in result["checks"]) else 1
        if args.command == "models":
            return 1 if result["status"] == "not_ready" else 0
        if args.command == "experiments" and args.operation == "screen-run":
            return 0 if result["status"] == "completed_development_screen" else 1
        if args.command == "body" and args.operation != "reference-plan":
            return (
                0
                if result["status"] in {"passed_development_subset", "recorded"}
                else 1
            )
        if args.command == "course" and args.operation in {
            "reset-check",
            "episode-check",
        }:
            return 0 if result["status"] == "passed_development_subset" else 1
        if args.command == "course" and args.operation == "execution-check":
            return 0 if result["status"] == "passed" else 1
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
