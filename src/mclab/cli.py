"""The same command surface is used in PowerShell and VS Code tasks."""

import argparse
import json
import subprocess
import sys

from mclab import (
    bootstrap,
    courses,
    evidence,
    gates,
    modbuild,
    protocolproof,
    qualify,
    reference,
    referenceproof,
    resetcheck,
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
    build.add_argument("--course", choices=["C0", "C1", "C2", "C3", "C4"])
    build.add_argument("--seed", type=int)
    course.add_parser("verify-generated")
    course.add_parser("verify-bootstrap")
    resets = course.add_parser("reset-check")
    resets.add_argument("--count", type=int, default=20)
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
                else:
                    result = evidence.verify_bootstrap()
        print(json.dumps(result, indent=2))
        if args.command == "doctor":
            return 0 if all(c.get("ok", True) for c in result["checks"]) else 1
        if args.command == "body" and args.operation != "reference-plan":
            return (
                0
                if result["status"] in {"passed_development_subset", "recorded"}
                else 1
            )
        if args.command == "course" and args.operation == "reset-check":
            return 0 if result["status"] == "passed_development_subset" else 1
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
