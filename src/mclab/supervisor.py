"""Private, token-authenticated stop/status endpoint; no arbitrary command API."""

import hmac
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mclab.bootstrap import java
from mclab.paths import RUNTIME, SERVER, config, read_json
from mclab.server import STATE, minecraft_status


def main() -> None:
    settings = config()
    state = read_json(STATE)
    closing = threading.Event()
    with (RUNTIME / "server-console.log").open("ab") as log:
        process = subprocess.Popen(
            [
                str(java()),
                f"-Xms{settings['heap_gb']}G",
                f"-Xmx{settings['heap_gb']}G",
                f"-Dmclab.launch_id={state['launch_id']}",
                "-jar",
                "fabric-server-launch.jar",
                "nogui",
            ],
            cwd=SERVER,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Do not log Authorization headers.

            def respond(self, value: dict, code: int = 200):
                payload = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def authorized(self):
                return hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + state["token"]
                )

            def do_GET(self):
                if not self.authorized():
                    self.respond({"error": "unauthorized"}, 401)
                    return
                if self.path != "/status":
                    self.respond({"error": "not_found"}, 404)
                    return
                result = {
                    "state": "starting",
                    "pid": process.pid,
                    "game_address": f"127.0.0.1:{settings['game_port']}",
                }
                if process.poll() is not None:
                    result.update(state="exited", exit_code=process.returncode)
                elif closing.is_set():
                    result["state"] = "stopping"
                else:
                    try:
                        result["minecraft"] = minecraft_status(settings["game_port"])
                        result["state"] = "running"
                    except (OSError, ValueError):
                        # Startup is explicitly distinguished from readiness.
                        pass
                self.respond(result)

            def do_POST(self):
                if not self.authorized():
                    self.respond({"error": "unauthorized"}, 401)
                    return
                if self.path != "/stop":
                    self.respond({"error": "not_found"}, 404)
                    return
                if not closing.is_set() and process.poll() is None:
                    closing.set()
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                self.respond({"state": "stopping"})

        http = ThreadingHTTPServer(("127.0.0.1", state["port"]), Handler)
        http.timeout = 0.5
        try:
            while process.poll() is None:
                http.handle_request()
        finally:
            http.server_close()
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=60)
            STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
