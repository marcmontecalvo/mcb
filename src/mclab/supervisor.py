"""Private, token-authenticated stop/status endpoint; no arbitrary command API."""

import hmac
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mclab.bootstrap import java
from mclab.paths import (
    INSTANCE,
    ROOT,
    SERVER,
    config,
    read_json,
)
from mclab.paths import (
    INSTANCE_RUNTIME as RUNTIME,
)
from mclab.server import STATE, instance_flag, minecraft_status


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


HOST_PROBE_S = 2.0


def _minecraft_up(port: int) -> bool:
    try:
        minecraft_status(port)
    except (OSError, ValueError):
        return False
    return True


HOST_COMMAND = [sys.executable, "-u", "-m", "mclab.companion_host"]
HOST_MAX_EXITS = 5  # exits within HOST_WINDOW_S before the supervisor gives up
HOST_WINDOW_S = 600.0
HOST_BACKOFF_S = (1.0, 2.0, 5.0, 10.0)
HOST_KILL_AFTER_S = 10.0
# Asked to stop, the host cancels its job, halts and despawns the body (a logout
# that saves) and exits. A hung job unwinds within the host's own 30 s join.
HOST_GRACE_S = 35.0


def ask_host_to_stop(runtime_file=None) -> None:
    """``POST /shutdown`` on the instance's control API; raises if unreachable."""
    from mclab import companion_api

    path = runtime_file or INSTANCE.runtime / companion_api.RUNTIME_FILE
    companion_api.ApiClient.from_runtime(path).shutdown()


class HostRunner:
    """Keeps the companion host child alive while Minecraft runs (play instance)."""

    def __init__(
        self,
        log_path,
        popen=subprocess.Popen,
        clock=time.monotonic,
        command=None,
        cwd=ROOT,
        say=print,
        ask_to_stop=ask_host_to_stop,
    ):
        self.log_path = log_path
        self.popen = popen
        self.clock = clock
        self.command = command or HOST_COMMAND
        self.cwd = cwd
        self.say = say
        self.ask_to_stop = ask_to_stop
        self.process = None
        self.exits: list[float] = []
        self.next_start = 0.0
        self.gave_up = False

    def _start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as output:
            self.process = self.popen(
                self.command,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        self.say(f"companion host started (pid {self.process.pid})")

    def tick(self, server_running: bool) -> None:
        """Call regularly; starts the host once the server runs, restarts on exit."""
        now = self.clock()
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            self.process = None
            self.exits = [t for t in self.exits if now - t < HOST_WINDOW_S] + [now]
            if len(self.exits) >= HOST_MAX_EXITS:
                self.gave_up = True
                self.say(
                    f"companion host exited {len(self.exits)} times within "
                    f"{HOST_WINDOW_S:.0f}s (last code {code}); giving up, see "
                    f"{self.log_path}"
                )
            else:
                delay = HOST_BACKOFF_S[min(len(self.exits), len(HOST_BACKOFF_S)) - 1]
                self.next_start = now + delay
                self.say(f"companion host exited ({code}); restarting in {delay}s")
        if (
            self.process is None
            and server_running
            and not self.gave_up
            and now >= self.next_start
        ):
            self._start()

    def stop(self) -> None:
        process, self.process = self.process, None
        self.gave_up = True  # never restart after a stop
        if process is None or process.poll() is not None:
            return
        # Politely first: on Windows terminate() is TerminateProcess, which skips
        # the host's cleanup (halt and despawn the body).
        try:
            self.ask_to_stop()
        except Exception as error:  # noqa: BLE001 - fall back to terminate
            self.say(f"companion host not asked to stop ({error}); terminating")
        else:
            try:
                process.wait(timeout=HOST_GRACE_S)
                return
            except subprocess.TimeoutExpired:
                self.say(
                    f"companion host did not exit {HOST_GRACE_S:.0f}s after being "
                    "asked; terminating"
                )
        process.terminate()
        try:
            process.wait(timeout=HOST_KILL_AFTER_S)
        except subprocess.TimeoutExpired:
            self.say("companion host ignored terminate; killing it")
            process.kill()
            process.wait()


def main() -> None:
    settings = config()
    state = read_json(STATE)
    closing = threading.Event()
    with (RUNTIME / "server-console.log").open("ab") as log:
        lab_flags = (
            [f"-Dmclab.lab_port={settings['lab_port']}"] if settings["lab_mod"] else []
        )
        process = subprocess.Popen(
            [
                str(java()),
                f"-Xms{settings['heap_gb']}G",
                f"-Xmx{settings['heap_gb']}G",
                f"-Dmclab.launch_id={state['launch_id']}",
                f"-Dmclab.bridge_token={state['token']}",
                f"-Dmclab.bridge_port={settings['bridge_port']}",
                *lab_flags,
                *instance_flag(INSTANCE.name),
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
        host = None
        if "companion" in settings:
            # The companion host is a child process of this supervisor.
            host = HostRunner(INSTANCE.companion_log)
        else:
            # In-game /mcb replies for as long as the server is up (no second
            # process to remember to start): run 20261121T180806599463Z's
            # /mcb status got "Sent." and nothing else.
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
        last_probe = 0.0
        try:
            while process.poll() is None:
                http.handle_request()
                if host is not None:
                    if closing.is_set():
                        host.stop()  # before Minecraft goes down
                    elif time.monotonic() - last_probe >= HOST_PROBE_S:
                        last_probe = time.monotonic()
                        host.tick(_minecraft_up(settings["game_port"]))
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
            if host is not None:
                host.stop()
            http.server_close()
            if process.poll() is None:
                process.stdin.write("stop\n")
                process.stdin.flush()
                process.wait(timeout=60)
            forget_state(STATE, state["launch_id"])


if __name__ == "__main__":
    main()
