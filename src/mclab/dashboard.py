"""Dependency-free desktop run launcher: python -m mclab.dashboard."""

import _thread
import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from contextlib import ExitStack
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from mclab import dashboard_runs as runs
from mclab.paths import ROOT, read_json, write_json
from mclab.recorder import exclusive_run

SESSIONS = ROOT / "results/sessions"
WORKER_LOCK = ROOT / "runtime/dashboard-worker.lock"
ACTIVE = {"queued", "running"}


def reopen_dashboard(port, no_browser):
    """A second launcher opens the running UI, including a nondefault port."""
    state_path = ROOT / "runtime/dashboard-ui.json"
    for _ in range(15):
        try:
            state = read_json(state_path) if state_path.exists() else {}
            active_port = state.get("port", port)
            if type(active_port) is not int or not 1 <= active_port <= 65535:
                raise ValueError("Invalid dashboard port")
            connection = HTTPConnection("127.0.0.1", active_port, timeout=0.5)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                html = response.read(4096)
                if response.status == 200 and b"<title>MCB Lab</title>" in html:
                    url = f"http://127.0.0.1:{active_port}/"
                    print(f"MCB Lab already running: {url}", flush=True)
                    if not no_browser:
                        webbrowser.open(url)
                    return True
            finally:
                connection.close()
        except (OSError, ValueError, HTTPException):
            pass  # The first launcher may still be binding its HTTP socket.
        time.sleep(0.1)
    return False


def save(path, value):
    temporary = path.with_suffix(".tmp")
    write_json(temporary, value)
    temporary.replace(path)


def tail(path, size=100_000):
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(max(0, length - size))
            data = stream.read()
        return data.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def live_events(directory):
    """Read a bounded tail; no extra policy requests or heartbeat interference."""
    paths = list(directory.glob("events.jsonl")) + list(
        directory.glob("*/events.jsonl")
    )
    if not paths:
        return {}
    path = max(paths, key=lambda p: p.stat().st_mtime_ns)
    latest = {}
    for line in tail(path, 160_000).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # truncated first line or an in-progress append
        if event.get("type") != "observation":
            continue
        payload = event["payload"]
        course = (
            payload.get("episode")
            or payload.get("course_episode")
            or payload.get("natural")
            or {}
        )
        # Older and split bridge observations use 'course' for evaluator state.
        course = course or payload.get("course", {})
        latest = {
            "phase": payload.get("state", "working"),
            "episode": event.get("episode_id"),
            "sample_age_s": max(
                0,
                (time.monotonic_ns() - event.get("monotonic_ns", time.monotonic_ns()))
                / 1e9,
            ),
        }
        if isinstance(course, dict) and course.get("status") == "running":
            tick, deadline = payload.get("tick"), course.get("deadline_tick")
            if type(tick) is int and type(deadline) is int:
                latest["remaining_ticks"] = max(0, deadline - tick)
                latest["game_seconds_left"] = latest["remaining_ticks"] / 20
    return latest


def run_directories(job):
    return sorted(
        p for p in (ROOT / "results" / runs.folder(job)).glob("*") if p.is_dir()
    )


def new_directories(job):
    before = set(job.get("before", []))
    return [
        p for p in run_directories(job) if p.relative_to(ROOT).as_posix() not in before
    ]


def new_artifacts(job):
    return [
        p / "summary.json"
        for p in new_directories(job)
        if (p / "summary.json").is_file()
    ]


KILL_GRACE_S = 180  # natural_world cleanup stops the server: up to ~120 s


def watch_kill(path, done):
    """Kill-now: raise KeyboardInterrupt in the runner so its finally blocks
    restore the lab world; hard-exit only if cleanup itself hangs."""
    flag = path.with_name("kill-now")
    while not flag.exists():
        if done.wait(0.5):
            return
    print("Kill requested; interrupting the runner and restoring the lab", flush=True)
    _thread.interrupt_main()
    if not done.wait(KILL_GRACE_S):
        print(f"Cleanup exceeded {KILL_GRACE_S}s; forcing exit", flush=True)
        os._exit(3)


def job_outputs(job):
    """Result directories a job wrote (recorded at job end; older sessions fall
    back to their artifacts, killed workers to directory creation time)."""
    if "outputs" in job:
        return job["outputs"]
    found = {Path(a).parent.as_posix() for a in job.get("artifacts", [])}
    if "before" in job and job.get("started_at"):
        end = job.get("finished_at") or time.time()
        found.update(
            p.relative_to(ROOT).as_posix()
            for p in new_directories(job)
            if job["started_at"] - 1 <= p.stat().st_ctime <= end + 1
        )
    return sorted(found)


def worker(path):
    session = read_json(path)
    done = threading.Event()
    threading.Thread(target=watch_kill, args=(path, done), daemon=True).start()
    try:
        with exclusive_run(WORKER_LOCK):
            session.update(status="running", started_at=time.time())
            save(path, session)
            for job in session["jobs"]:
                if path.with_name("stop-after-batch").exists():
                    session["status"] = "stopped"
                    break
                job.update(
                    status="running",
                    started_at=time.time(),
                    before=[
                        p.relative_to(ROOT).as_posix() for p in run_directories(job)
                    ],
                )
                save(path, session)
                print(
                    f"Starting {job['entrant']} / {job.get('course', job['kind'])}",
                    flush=True,
                )
                try:
                    result = runs.execute(job)
                    if job["kind"] == "setup":
                        print(json.dumps(result), flush=True)
                    found = []
                    for artifact in new_artifacts(job):
                        value = read_json(artifact)
                        if (
                            result.get("run_id")
                            and value.get("run_id") == result["run_id"]
                        ) or value == result:
                            found.append(artifact.relative_to(ROOT).as_posix())
                    job.update(
                        artifacts=found, result_status=result.get("status", "returned")
                    )
                    # Task failures are results; runner/cleanup errors stop the queue.
                    broken = result.get("status") in {
                        "invalid",
                        "interrupted",
                        "running",
                    } or any(
                        result.get(k)
                        for k in (
                            "error",
                            "cleanup_error",
                            "stop_error",
                            "recording_error",
                        )
                    )
                    if broken:
                        raise RuntimeError(
                            result.get("error")
                            or result.get("cleanup_error")
                            or f"Runner returned {result.get('status')}"
                        )
                    job["status"] = "completed"
                except KeyboardInterrupt:
                    job.update(status="killed", error="Killed from the dashboard")
                    session["status"] = "killed"
                except Exception as error:
                    job.update(status="error", error=str(error))
                    traceback.print_exc()
                    session["status"] = "error"
                finally:
                    job["finished_at"] = time.time()
                    job["outputs"] = [
                        p.relative_to(ROOT).as_posix() for p in new_directories(job)
                    ]
                    save(path, session)
                if session["status"] in {"error", "killed"}:
                    break
            else:
                session["status"] = "completed"
    except KeyboardInterrupt:
        session["status"] = "killed"
    except Exception as error:
        session.update(status="error", error=str(error))
        traceback.print_exc()
    finally:
        for job in session["jobs"]:
            if job["status"] == "queued":
                job["status"] = "not run"
        session["finished_at"] = time.time()
        save(path, session)
        done.set()


def worker_busy():
    try:
        with exclusive_run(WORKER_LOCK):
            return False
    except RuntimeError:
        return True


def session_path(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(
        r"[0-9T]{15}-[0-9a-f]{8}", session_id
    ):
        raise ValueError("Unknown session")
    path = SESSIONS / session_id / "session.json"
    if not path.is_file():
        raise ValueError("Unknown session")
    return path


def request_kill():
    """Kill the active session's current run now (the runner still restores the lab)."""
    for path in SESSIONS.glob("*/session.json"):
        if read_json(path)["status"] in ACTIVE:
            path.with_name("stop-after-batch").touch()
            path.with_name("kill-now").touch()
            return {"status": f"Kill requested for {path.parent.name}"}
    return {"status": "No active session"}


def cited_in_docs():
    texts = [
        p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "docs").rglob("*")
        if p.suffix in {".md", ".json"} and p.is_file()
    ]
    return lambda name: any(name in text for text in texts)


def delete_session(session_id):
    """Remove a finished session and the result directories its jobs wrote.

    Run directories cited in docs/ are evidence and are kept.
    """
    path = session_path(session_id)
    session = read_json(path)
    if session["status"] in ACTIVE:
        raise ValueError("Kill or finish the session before deleting it")
    results = (ROOT / "results").resolve()
    cited = cited_in_docs()
    deleted, kept = [], []
    for job in session["jobs"]:
        for relative in job_outputs(job):
            run = (ROOT / relative).resolve()
            # Only results/<suite>/<run> directories, never anything wider.
            if run.parent.parent != results or not run.is_dir():
                continue
            if cited(run.name):
                kept.append(relative)
                continue
            shutil.rmtree(run)
            deleted.append(relative)
    shutil.rmtree(path.parent)
    return {"deleted": deleted, "kept_cited_in_docs": kept}


class Dashboard:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.process_path = None
        self.cache = {}

    def sessions(self):
        with self.lock:
            values = []
            for path in sorted(SESSIONS.glob("*/session.json"), reverse=True):
                value = read_json(path)
                if value["status"] in ACTIVE:
                    own_alive = (
                        self.process_path == path
                        and self.process is not None
                        and self.process.poll() is None
                    )
                    if not own_alive and not worker_busy():
                        value.update(
                            status="interrupted",
                            finished_at=time.time(),
                            error=(
                                "Worker exited before completion; "
                                "inspect the log and lab state"
                            ),
                        )
                        for job in value["jobs"]:
                            if job["status"] in ACTIVE:
                                job["status"] = (
                                    "interrupted"
                                    if job["status"] == "running"
                                    else "not run"
                                )
                        save(path, value)
                    for job in value["jobs"]:
                        if job["status"] == "running":
                            paths = new_directories(job)
                            if paths:
                                latest = paths[-1] / "summary.json"
                                try:
                                    summary = read_json(latest)
                                    job["progress"] = len(runs.outcomes(summary))
                                    job["live_artifact"] = latest.relative_to(
                                        ROOT
                                    ).as_posix()
                                except (OSError, ValueError):
                                    # Existing runners write summaries non-atomically.
                                    pass
                                job["live"] = live_events(latest.parent)
                value["log"] = tail(path.with_name("console.log"), 12_000)
                value["stop_requested"] = path.with_name("stop-after-batch").exists()
                value["kill_requested"] = path.with_name("kill-now").exists()
                values.append(value)
            return values

    def start(self, request):
        jobs = runs.plan(request)
        # sessions() also reconciles workers that died unexpectedly.
        if any(s["status"] in ACTIVE for s in self.sessions()):
            raise ValueError("A session is already active")
        with self.lock:
            if (
                self.process is not None and self.process.poll() is None
            ) or worker_busy():
                raise ValueError("A session is already active")
            session_id = (
                time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid4().hex[:8]
            )
            path = SESSIONS / session_id / "session.json"
            value = {
                "id": session_id,
                "status": "queued",
                "created_at": time.time(),
                "jobs": [{**j, "status": "queued"} for j in jobs],
            }
            save(path, value)
            try:
                with path.with_name("console.log").open("ab") as output:
                    self.process = subprocess.Popen(
                        [
                            sys.executable,
                            "-u",
                            "-m",
                            "mclab.dashboard",
                            "--worker",
                            str(path),
                        ],
                        cwd=ROOT,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=output,
                        creationflags=subprocess.CREATE_NO_WINDOW
                        if os.name == "nt"
                        else 0,
                    )
                self.process_path = path
            except OSError as error:
                value.update(status="error", error=str(error), finished_at=time.time())
                save(path, value)
                raise
            return {"id": session_id}

    def history(self):
        values = []
        with self.lock:
            for path in runs.summary_paths():
                try:
                    stamp = path.stat().st_mtime_ns
                    cached = self.cache.get(path)
                    if cached is None or cached[0] != stamp:
                        cached = (stamp, runs.result_rows(path, read_json(path)))
                        self.cache[path] = cached
                    values.extend(cached[1])
                except (OSError, ValueError, TypeError, KeyError) as error:
                    values.append(
                        {
                            "path": path.relative_to(ROOT).as_posix(),
                            "date": path.parent.name,
                            "status": "unreadable",
                            "error": str(error),
                            "body": "unknown",
                            "stage": "unknown",
                            "suite": path.parent.parent.name,
                            "statuses": {},
                        }
                    )
        return sorted(values, key=lambda row: row["date"], reverse=True)


def handler(dashboard):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, value, content_type="application/json"):
            body = (
                value
                if isinstance(value, bytes)
                else json.dumps(value, allow_nan=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.trusted_host():
                return self.reply(403, {"error": "Use the printed loopback URL"})
            parsed = urlsplit(self.path)
            try:
                if parsed.path == "/":
                    html = (
                        Path(__file__)
                        .with_name("dashboard.html")
                        .read_text(encoding="utf-8")
                    )
                    return self.reply(
                        200,
                        html.replace("__TOKEN__", token).encode(),
                        "text/html; charset=utf-8",
                    )
                if parsed.path == "/api/catalog":
                    return self.reply(200, runs.catalog())
                if parsed.path == "/api/status":
                    from mclab import server

                    return self.reply(200, {"state": server.status()["state"]})
                if parsed.path == "/api/sessions":
                    return self.reply(200, dashboard.sessions())
                if parsed.path == "/api/results":
                    return self.reply(200, dashboard.history())
                if parsed.path == "/api/detail":
                    relative = parse_qs(parsed.query).get("path", [""])[0]
                    path = runs.project_file(relative)
                    if path not in runs.summary_paths():
                        raise ValueError("Select a run summary")
                    return self.reply(200, read_json(path))
                return self.reply(404, {"error": "Not found"})
            except (OSError, ValueError, TypeError, KeyError) as error:
                return self.reply(400, {"error": str(error)})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                not self.trusted_host()
                or self.headers.get("Origin") != origin
                or not secrets.compare_digest(self.headers.get("X-UI-Token", ""), token)
            ):
                return self.reply(403, {"error": "Reload the local dashboard"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(length))
                if self.path == "/api/start":
                    return self.reply(200, dashboard.start(request))
                if self.path == "/api/shutdown":
                    self.reply(
                        200,
                        {"status": "Dashboard stopped; active batches keep running"},
                    )
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if self.path == "/api/stop":
                    active = next(
                        (s for s in dashboard.sessions() if s["status"] in ACTIVE), None
                    )
                    if active:
                        (SESSIONS / active["id"] / "stop-after-batch").touch()
                    return self.reply(
                        200, {"status": "Stop requested after current batch"}
                    )
                if self.path == "/api/kill":
                    return self.reply(200, request_kill())
                if self.path == "/api/delete":
                    with dashboard.lock:
                        return self.reply(200, delete_session(request.get("id")))
                return self.reply(404, {"error": "Not found"})
            except (OSError, ValueError, TypeError, KeyError) as error:
                return self.reply(400, {"error": str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--sessions", action="store_true", help="List sessions and exit"
    )
    parser.add_argument(
        "--kill", action="store_true", help="Kill the active run now and exit"
    )
    parser.add_argument(
        "--delete", metavar="SESSION_ID", help="Delete a session and its results"
    )
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    if args.sessions:
        for path in sorted(SESSIONS.glob("*/session.json")):
            value = read_json(path)
            jobs = ", ".join(f"{j['entrant']}:{j['status']}" for j in value["jobs"])
            print(f"{value['id']}  {value['status']:<11} {jobs}")
        return
    if args.kill:
        print(request_kill()["status"])
        return
    if args.delete:
        print(json.dumps(delete_session(args.delete), indent=2))
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(exclusive_run(ROOT / "runtime/dashboard.lock"))
        except RuntimeError:
            if reopen_dashboard(args.port, args.no_browser):
                return
            parser.exit(
                1,
                "MCB Lab is already starting or is not responding. "
                "Inspect runtime/dashboard.log and runtime/dashboard-error.log.\n",
            )
        with ThreadingHTTPServer(
            ("127.0.0.1", args.port), handler(Dashboard())
        ) as http:
            save(ROOT / "runtime/dashboard-ui.json", {"port": http.server_port})
            url = f"http://127.0.0.1:{http.server_port}/"
            print(
                f"MCB Lab: {url} (Ctrl+C closes the UI; active batches keep running)",
                flush=True,
            )
            if not args.no_browser:
                webbrowser.open(url)
            try:
                http.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
