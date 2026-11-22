"""Private, token-authenticated stop/status endpoint; no arbitrary command API."""

import hmac
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mclab.bootstrap import java
from mclab.paths import RUNTIME, SERVER, config, read_json
from mclab.server import STATE, minecraft_status


def forget_state(state_path, launch_id, pause=time.sleep, tries=20):
    """Delete our own state file, never a successor's, and survive transient locks.

    A restart writes the new state while this supervisor is still in teardown
    (the C5 restart of the Nov 7 requalification; run 20261107T030649535487Z):
    unlinking blindly then strands the new supervisor's launch, and Windows readers
    hold the file for a moment. Only delete while the launch_id is still ours.
    """
    for _ in range(tries):
        try:
            if not state_path.exists():
                return False
            if (
                json.loads(state_path.read_text(encoding="utf-8")).get("launch_id")
                != launch_id
            ):
                return False
            state_path.unlink()
            return True
        except (PermissionError, FileNotFoundError, ValueError):
            pause(0.1)
    return False


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
                f"-Dmclab.bridge_token={state['token']}",
                f"-Dmclab.bridge_port={settings['bridge_port']}",
                f"-Dmclab.lab_port={settings['lab_port']}",
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
        # In-game /mcb replies for as long as the server is up (no second process to
        # remember to start): run 20261121T180806599463Z's /mcb status got "Sent."
        # and nothing else.
        from mclab import companion_service

        threading.Thread(
            target=companion_service.run,
            kwargs={"stop": closing},
            name="companion-service",
            daemon=True,
        ).start()

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
                self.respond({"state": "stopping"})

        http = ThreadingHTTPServer(("127.0.0.1", state["port"]), Handler)
        http.timeout = 0.5
        stop_sent = False
        try:
            while process.poll() is None:
                http.handle_request()
                if closing.is_set() and not stop_sent:
                    # Minecraft can lose console input supplied before startup
                    # initializes command processing. Wait for real readiness.
                    try:
                        minecraft_status(settings["game_port"])
                    except (OSError, ValueError):
                        continue
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                    stop_sent = True
        finally:
            http.server_close()
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=60)
            forget_state(STATE, state["launch_id"])


if __name__ == "__main__":
    main()
