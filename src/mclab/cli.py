"""The same command surface is used in PowerShell and VS Code tasks."""

import argparse
import json
import subprocess
import sys

from mclab import bootstrap, courses, evidence, modbuild, server
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
    result = {
        "schema_version": 1,
        "checks": checks,
        "server": server.status(),
        "gates": {"G0": "pending_human_join_and_course_inspection", "G1-G8": "not_run"},
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
                else:
                    result = evidence.verify_bootstrap()
        print(json.dumps(result, indent=2))
        if args.command == "doctor":
            return 0 if all(c.get("ok", True) for c in result["checks"]) else 1
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
