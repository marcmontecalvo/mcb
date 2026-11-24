"""Isolated UI contract fixture: real HTTP/SSE/static assets, no Minecraft workers."""

import argparse
import copy
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from mclab import dashboard, dashboard_runs, dashboard_suites, play_runs, server
from mclab.paths import read_json
from mclab.run_config import resolve_config
from mclab.run_launch import validate_launch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    original_root = dashboard.ROOT
    # The real arena mission (its fields and defaults) from the real catalog.
    arena = next(t for t in dashboard_suites.test_catalog() if t["id"] == "arena:hunt")
    with TemporaryDirectory(prefix="mcb-ui-e2e-") as temporary:
        root = Path(temporary)
        dashboard.ROOT = dashboard_runs.ROOT = dashboard_suites.ROOT = root
        dashboard.SESSIONS = root / "results/sessions"
        preset = root / "config/presets/careful.json"
        preset.parent.mkdir(parents=True)
        dashboard.save(preset, {"planner": {"movement": {"max_gap": 0}}})
        tests = [
            {
                "id": "gather",
                "label": "Gather",
                "family": "gather",
                "section": "Big jobs",
                "basic_fields": ["seeds", "repetitions"],
                "description": "Isolated gather contract fixture",
                "locked_fields": ["run.test"],
                "default_config": resolve_config(
                    "gather",
                    overrides={"run": {"start": [0, 70, 0]}},
                    root=original_root,
                ),
            },
            {
                "id": "companion:crafting",
                "label": "Crafting",
                "family": "companion",
                "section": "Skills",
                "description": "Isolated companion contract fixture",
                "locked_fields": ["run.test", "run.check"],
                "default_config": resolve_config(
                    "companion",
                    overrides={"run": {"check": "crafting"}},
                    root=original_root,
                ),
            },
            arena,
        ]
        dashboard_suites.test_catalog = lambda: copy.deepcopy(tests)

        def plan(request):
            config = request["run_config"]
            validate_launch(config)
            return [{"kind": config["run"]["test"], "run_config": config}]

        dashboard_runs.plan = plan
        dashboard_runs.catalog = lambda: {
            "tests": tests,
            "presets": dashboard_suites.presets(),
            "suites": dashboard_suites.list_suites(),
            "sections": dashboard_suites.sections(),
            "companion": {},
        }
        dashboard_runs.natural_seeds = lambda: []
        server.status = lambda: {"state": "fixture idle"}
        play_runs.play_state = lambda **_: "stopped"
        play_runs.where = lambda **_: {
            "servers": {
                "lab": {"address": "127.0.0.1:25575", "label": "Lab server"},
                "play": {"address": "127.0.0.1:25565", "label": "Play server"},
            },
            "arenas": {
                name: {
                    "built": name == "vault",
                    "teleport": "/tp @s -325 82 -56" if name == "vault" else None,
                    "match": None,
                    "corner": None,
                    "spectator": None,
                }
                for name in play_runs.arena_names()
            },
        }

        class Fixture:
            lock = threading.RLock()

            def sessions(self):
                with self.lock:
                    return [
                        read_json(path)
                        for path in sorted(dashboard.SESSIONS.glob("*/session.json"))
                    ]

            def history(self):
                return []

            def builds(self):
                return []

            def start(self, request):
                jobs = dashboard_suites.resolve_entries(request["entries"])
                with self.lock:
                    identity = f"fixture-{len(self.sessions()) + 1}"
                    value = {
                        "id": identity,
                        "kind": "suite",
                        "status": "running",
                        "jobs": [dict(job, status="queued") for job in jobs],
                        "entries": request["entries"],
                    }
                    path = dashboard.SESSIONS / identity / "session.json"
                    path.parent.mkdir(parents=True)
                    dashboard.save(path, value)
                    return value

        application = Fixture()
        dashboard.request_kill = lambda: {"status": "Fixture cancellation requested"}
        handler = dashboard.handler(application)
        with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as http:
            try:
                http.serve_forever()
            finally:
                handler.events.close()


if __name__ == "__main__":
    main()
