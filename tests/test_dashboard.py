"""UI orchestration tests use fake runners; no Minecraft world or provider calls."""

import json
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from mclab import dashboard as ui
from mclab import dashboard_runs as runs
from mclab.paths import read_json, write_json


def test_dashboard_exposes_all_runner_control_profiles_with_native_default():
    from html.parser import HTMLParser
    from pathlib import Path

    from mclab.control_loop import NATIVE, PROFILES

    class ControlOptions(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.values = []
            self.selected = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "select":
                self.active = attrs.get("id") == "control-profile"
            if tag == "option" and self.active:
                self.values.append(attrs["value"])
                if "selected" in attrs:
                    self.selected.append(attrs["value"])

        def handle_endtag(self, tag):
            if tag == "select":
                self.active = False

    parser = ControlOptions()
    parser.feed(Path(ui.__file__).with_name("dashboard.html").read_text("utf-8"))
    assert set(parser.values) == set(PROFILES)
    assert parser.selected == [NATIVE]


def test_native_dashboard_selection_reaches_natural_runner(workspace, monkeypatch):
    from mclab import natural
    from mclab.control_loop import NATIVE

    routes = [{"route_id": "R1", "start": [0, 70, 0], "goal": [100, 70, 0], "yaw": 0}]
    write_json(workspace / "routes.json", routes)
    job = runs.plan(
        {
            "kind": "natural",
            "entrants": ["segmented_goal"],
            "seed": 1337,
            "routes": "routes.json",
            "control_profile": NATIVE,
        }
    )[0]
    calls = []
    monkeypatch.setattr(natural, "run", lambda *a, **kw: calls.append((a, kw)))
    runs.execute(job)
    assert calls[0][1]["control_profile"] == NATIVE


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "ROOT", tmp_path)
    monkeypatch.setattr(ui, "ROOT", tmp_path)
    monkeypatch.setattr(ui, "SESSIONS", tmp_path / "results/sessions")
    monkeypatch.setattr(ui, "WORKER_LOCK", tmp_path / "runtime/worker.lock")
    return tmp_path


def test_matched_course_matrix_and_unsupported_selections(workspace):
    request = {
        "kind": "courses",
        "entrants": ["v8_goal", "baritone"],
        "courses": ["C2", "C5"],
        "seeds": [101, 102],
    }
    jobs = runs.plan(request)
    assert [(j["course"], j["entrant"]) for j in jobs] == [
        ("C2", "v8_goal"),
        ("C2", "baritone"),
        ("C5", "v8_goal"),
        ("C5", "baritone"),
    ]
    assert all(j["seeds"] == [101, 102] for j in jobs)
    for changes in (
        {"seeds": [1, 1]},
        {"seeds": [True]},
        {"entrants": ["mineflayer"]},
        {"entrants": [{}]},
        {"courses": ["C1"]},
    ):
        with pytest.raises(ValueError):
            runs.plan({**request, **changes})


def test_catalog_discovers_evidence_profiles_without_scanning_archives(workspace):
    from mclab.awareness import Awareness
    from mclab.movement_policy import MovementPolicy

    write_json(workspace / "config/backends.json", {"backends": []})
    write_json(workspace / "courses/profiles.json", {"courses": []})
    directory = "docs/evidence/2026-10-28-phase0"
    awareness = f"{directory}/awareness-chunk-64.json"
    movement = f"{directory}/movement-passing-families.json"
    terrain = f"{directory}/terrain-passing-families.json"
    write_json(workspace / awareness, Awareness.resolve("chunk").payload())
    write_json(workspace / movement, MovementPolicy(allow_sprint=True).payload())
    write_json(workspace / terrain, {"allow_bridge": True, "max_support_stack": 3})
    write_json(workspace / directory / "invalid.json", {"allow_sprint": "yes"})
    write_json(
        workspace / directory / "20261028-summary.json",
        {"routes": [{"route_id": "R1"}]},
    )
    write_json(
        workspace / directory / "sources/archived-movement.json",
        MovementPolicy().payload(),
    )
    profiles = runs.catalog()["profiles"]
    assert profiles["awareness"] == [awareness]
    assert profiles["movement"] == [movement]
    assert profiles["terrain"] == [terrain]
    assert profiles["memory"] == []
    assert profiles["routes"] == []


def test_original_backend_is_reread_at_batch_start(workspace, monkeypatch):
    from mclab import model_execution

    path = workspace / "config/backends.json"
    write_json(
        path, {"backends": [{"name": "local", "enabled": True, "request_limit": 4}]}
    )
    job = runs.plan({"kind": "models", "backends": ["local"]})[0]
    write_json(
        path, {"backends": [{"name": "local", "enabled": True, "request_limit": 7}]}
    )
    calls = []
    monkeypatch.setattr(model_execution, "run", lambda *a, **kw: calls.append((a, kw)))
    runs.execute(job)
    assert calls == [(("local",), {"count": 1, "request_limit": 7})]
    assert "configuration" not in job


def test_natural_profiles_are_paths_and_objects_are_reread(workspace, monkeypatch):
    from mclab import natural
    from mclab.awareness import Awareness

    routes = [{"route_id": "R1", "start": [0, 70, 0], "goal": [100, 70, 0], "yaw": 0}]
    write_json(workspace / "routes.json", routes)
    write_json(workspace / "awareness.json", Awareness.resolve("chunk").payload())
    job = runs.plan(
        {
            "kind": "natural",
            "entrants": ["segmented_goal"],
            "seed": 1337,
            "routes": "routes.json",
            "awareness_profile": "awareness.json",
        }
    )[0]
    assert job["awareness_profile"] == "awareness.json"
    changed = Awareness.resolve("visible")
    write_json(workspace / "awareness.json", changed.payload())
    calls = []
    monkeypatch.setattr(natural, "run", lambda *a, **kw: calls.append((a, kw)))
    runs.execute(job)
    assert calls[0][1]["awareness"].payload() == changed.payload()
    assert calls[0][0] == (1337, routes)


def test_reject_outside_and_archived_inputs(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "input.json"
    write_json(outside, {})
    write_json(workspace / "sources/profile.json", {})
    for name in (str(outside), "sources/profile.json", ".env", "missing.json"):
        with pytest.raises(ValueError):
            runs.project_file(name)


def test_history_separates_entrants_and_keeps_invalids(workspace):
    path = workspace / "results/natural/run/summary.json"
    value = {
        "routes": [{"route_id": "R1"}],
        "attempts": [
            {"entrant": "baritone", "status": "success", "ticks": 100},
            {"entrant": "v8_goal", "status": "invalid", "ticks": 1},
            {
                "entrant": "v8_goal",
                "status": "success",
                "start_tick": 10,
                "end_tick": 210,
            },
        ],
    }
    write_json(path, value)
    write_json(workspace / "results/navigation/broken/summary.json", {})
    (workspace / "results/navigation/broken/summary.json").write_text("")
    result = ui.Dashboard().history()
    rows = {r["body"]: r for r in result}
    assert rows["baritone"]["median_ticks"] == 100
    assert rows["v8_goal"]["median_ticks"] == 200
    assert rows["v8_goal"]["statuses"] == {"invalid": 1, "success": 1}
    assert rows["v8_goal"]["id"] != rows["baritone"]["id"]
    assert any(r["status"] == "unreadable" for r in result)
    assert read_json(path) == value


def test_progress_before_first_summary_and_recorded_timeout(workspace, monkeypatch):
    path = workspace / "results/sessions/test/session.json"
    job = {
        "kind": "courses",
        "course": "C2",
        "entrant": "v8_goal",
        "before": [],
        "status": "running",
        "expected": 3,
    }
    write_json(path, {"id": "test", "status": "running", "jobs": [job]})
    events = workspace / "results/navigation/new/events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps(
            {
                "type": "observation",
                "monotonic_ns": time.monotonic_ns(),
                "payload": {
                    "tick": 120,
                    "state": "running",
                    "episode": {"status": "running", "deadline_tick": 400},
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr(ui, "worker_busy", lambda: True)
    live = ui.Dashboard().sessions()[0]["jobs"][0]["live"]
    assert live["game_seconds_left"] == 14
    assert live["phase"] == "running"
    assert live["sample_age_s"] < 2


def test_worker_keeps_error_and_does_not_launch_remaining_jobs(workspace, monkeypatch):
    path = workspace / "results/sessions/test/session.json"
    jobs = [
        {"kind": "courses", "course": "C2", "entrant": name, "status": "queued"}
        for name in ("v8_goal", "baritone")
    ]
    write_json(path, {"id": "test", "status": "queued", "jobs": jobs})
    calls = []

    def fail(job):
        calls.append(job["entrant"])
        raise RuntimeError("current source is not installed")

    monkeypatch.setattr(runs, "execute", fail)
    ui.worker(path)
    session = read_json(path)
    assert calls == ["v8_goal"]
    assert session["status"] == "error"
    assert session["jobs"][0]["error"] == "current source is not installed"
    assert session["jobs"][1]["status"] == "not run"


def test_stop_allows_current_batch_to_finish_cleanup(workspace, monkeypatch):
    path = workspace / "results/sessions/test/session.json"
    jobs = [
        {"kind": "courses", "course": "C2", "entrant": name, "status": "queued"}
        for name in ("v8_goal", "baritone")
    ]
    write_json(path, {"id": "test", "status": "queued", "jobs": jobs})
    calls = []

    def finish(job):
        calls.append(job["entrant"])
        path.with_name("stop-after-batch").touch()
        return {"status": "completed_navigation_execution"}

    monkeypatch.setattr(runs, "execute", finish)
    ui.worker(path)
    session = read_json(path)
    assert calls == ["v8_goal"]
    assert session["status"] == "stopped"
    assert [j["status"] for j in session["jobs"]] == ["completed", "not run"]


def test_dead_worker_is_visible_as_interrupted(workspace):
    path = workspace / "results/sessions/test/session.json"
    write_json(
        path,
        {
            "id": "test",
            "status": "running",
            "jobs": [{"status": "running"}, {"status": "queued"}],
        },
    )
    session = ui.Dashboard().sessions()[0]
    assert session["status"] == "interrupted"
    assert [j["status"] for j in session["jobs"]] == ["interrupted", "not run"]


@pytest.mark.parametrize("record_port", [False, True])
def test_second_launcher_reopens_existing_ui(workspace, monkeypatch, record_port):
    opened = []
    monkeypatch.setattr(ui.webbrowser, "open", opened.append)
    with ThreadingHTTPServer(("127.0.0.1", 0), ui.handler(ui.Dashboard())) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            if record_port:
                write_json(
                    workspace / "runtime/dashboard-ui.json", {"port": http.server_port}
                )
            port_argument = 8770 if record_port else http.server_port
            assert ui.reopen_dashboard(port_argument, False)
            assert opened == [f"http://127.0.0.1:{http.server_port}/"]
            assert ui.reopen_dashboard(port_argument, True)
            assert len(opened) == 1
        finally:
            http.shutdown()
            thread.join(timeout=3)


def test_locked_ui_main_reuses_server_without_starting_another(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(ui.sys, "argv", ["dashboard", "--no-browser"])
    monkeypatch.setattr(
        ui, "reopen_dashboard", lambda *args: calls.append(args) or True
    )
    with ui.exclusive_run(workspace / "runtime/dashboard.lock"):
        ui.main()
    assert calls == [(8770, True)]


def test_unresponsive_locked_ui_has_friendly_error(workspace, monkeypatch, capsys):
    monkeypatch.setattr(ui.sys, "argv", ["dashboard", "--no-browser"])
    monkeypatch.setattr(ui, "reopen_dashboard", lambda *_args: False)
    with ui.exclusive_run(workspace / "runtime/dashboard.lock"):
        with pytest.raises(SystemExit) as error:
            ui.main()
    assert error.value.code == 1
    assert "already starting or is not responding" in capsys.readouterr().err


def test_launch_uses_existing_python_and_blocks_second_session(workspace, monkeypatch):
    launched = []

    class Process:
        def __init__(self, args, **kwargs):
            launched.append((args, kwargs))

        def poll(self):
            return None

    monkeypatch.setattr(ui.subprocess, "Popen", Process)
    dashboard = ui.Dashboard()
    request = {
        "kind": "courses",
        "entrants": ["v8_goal"],
        "courses": ["C2"],
        "seeds": [101],
    }
    session_id = dashboard.start(request)["id"]
    assert launched[0][0][:4] == [ui.sys.executable, "-u", "-m", "mclab.dashboard"]
    assert not launched[0][1].get("shell")
    with pytest.raises(ValueError, match="already active"):
        dashboard.start(request)
    assert len(launched) == 1
    value = read_json(workspace / "results/sessions" / session_id / "session.json")
    assert value["jobs"][0]["seeds"] == [101]


def test_worker_links_original_summary_and_preserves_task_failure(
    workspace, monkeypatch
):
    path = workspace / "results/sessions/test/session.json"
    job = {"kind": "courses", "course": "C2", "entrant": "v8_goal", "status": "queued"}
    write_json(path, {"id": "test", "status": "queued", "jobs": [job]})
    artifact = workspace / "results/navigation/new/summary.json"
    original = {
        "run_id": "run-1",
        "status": "completed_navigation_execution",
        "episodes": [{"status": "failed", "reason": "tick_limit"}],
    }

    def finish(_job):
        write_json(artifact, original)
        return original

    monkeypatch.setattr(runs, "execute", finish)
    ui.worker(path)
    session = read_json(path)
    assert session["status"] == "completed"
    assert session["jobs"][0]["artifacts"] == ["results/navigation/new/summary.json"]
    assert read_json(artifact) == original


def test_http_rejects_foreign_origins_hosts_and_arbitrary_files(workspace, monkeypatch):
    dashboard = ui.Dashboard()
    calls = []
    monkeypatch.setattr(
        dashboard, "start", lambda request: calls.append(request) or {"id": "test"}
    )
    with ThreadingHTTPServer(("127.0.0.1", 0), ui.handler(dashboard)) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", http.server_port)

        def request(method, path, body=None, headers=None):
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()

        try:
            # Page assets use the installed package, not the test workspace.
            status, html = request("GET", "/")
            assert status == 200
            token = html.decode().split("const token='")[1].split("'")[0]
            assert "__TOKEN__" not in html.decode()
            assert request("POST", "/api/start", "{}", {"X-UI-Token": token})[0] == 403
            headers = {
                "X-UI-Token": token,
                "Origin": f"http://127.0.0.1:{http.server_port}",
            }
            assert (
                request(
                    "POST",
                    "/api/start",
                    "{}",
                    {**headers, "Origin": "https://evil.example"},
                )[0]
                == 403
            )
            assert request("POST", "/api/start", "{}", headers)[0] == 200
            assert calls == [{}]
            assert (
                request("GET", "/api/results", headers={"Host": "evil.example"})[0]
                == 403
            )
            write_json(workspace / "runtime/client-bridge.json", {"token": "private"})
            status, body = request("GET", "/api/detail?path=runtime/client-bridge.json")
            assert status == 400
            assert b"private" not in body
        finally:
            connection.close()
            http.shutdown()
            thread.join(timeout=3)


def test_shutdown_requires_local_token_and_stops_http_server(workspace):
    with ThreadingHTTPServer(("127.0.0.1", 0), ui.handler(ui.Dashboard())) as http:
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", http.server_port, timeout=3)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            token = response.read().decode().split("const token='")[1].split("'")[0]
            origin = f"http://127.0.0.1:{http.server_port}"
            for headers in (
                {"Origin": origin},
                {"Origin": "https://example.com", "X-UI-Token": token},
            ):
                connection.request("POST", "/api/shutdown", "{}", headers)
                response = connection.getresponse()
                assert response.status == 403
                response.read()
                assert thread.is_alive()
            connection.request(
                "POST",
                "/api/shutdown",
                "{}",
                {"Origin": origin, "X-UI-Token": token},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert (
                "active batches keep running" in json.loads(response.read())["status"]
            )
            thread.join(timeout=3)
            assert not thread.is_alive()
        finally:
            connection.close()
            http.shutdown()
            thread.join(timeout=3)


def test_kill_now_interrupts_current_run_and_skips_the_rest(workspace, monkeypatch):
    path = workspace / "results/sessions/20261029T000000-0000abcd/session.json"
    jobs = [
        {"kind": "courses", "course": "C2", "entrant": name, "status": "queued"}
        for name in ("v8_goal", "baritone")
    ]
    write_json(path, {"id": path.parent.name, "status": "running", "jobs": jobs})
    calls = []

    def hang(job):
        calls.append(job["entrant"])
        ui.request_kill()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:  # the watcher interrupts this loop
            time.sleep(0.05)
        raise AssertionError("kill did not interrupt the runner")

    monkeypatch.setattr(runs, "execute", hang)
    ui.worker(path)
    session = read_json(path)
    assert calls == ["v8_goal"]
    assert session["status"] == "killed"
    assert [j["status"] for j in session["jobs"]] == ["killed", "not run"]


def test_delete_session_removes_its_results_but_keeps_cited_evidence(workspace):
    sid = "20261029T000000-0000abcd"
    path = workspace / f"results/sessions/{sid}/session.json"
    own = workspace / "results/natural/run-own"
    cited = workspace / "results/natural/run-cited"
    other = workspace / "results/natural/run-other"
    for d in (own, cited, other):
        (d / "summary.json").parent.mkdir(parents=True)
        (d / "summary.json").write_text("{}")
    (workspace / "docs").mkdir()
    (workspace / "docs/ledger.md").write_text("see results/natural/run-cited")
    jobs = [
        {
            "kind": "natural",
            "entrant": "segmented_goal",
            "status": "killed",
            "outputs": [
                "results/natural/run-own",
                "results/natural/run-cited",
                "results",  # never anything wider than results/<suite>/<run>
            ],
        }
    ]
    write_json(path, {"id": sid, "status": "killed", "jobs": jobs})
    result = ui.delete_session(sid)
    assert result == {
        "deleted": ["results/natural/run-own"],
        "kept_cited_in_docs": ["results/natural/run-cited"],
    }
    assert not own.exists() and cited.exists() and other.exists()
    assert not path.parent.exists()


def test_delete_refuses_active_or_malformed_sessions(workspace):
    sid = "20261029T000000-0000abcd"
    path = workspace / f"results/sessions/{sid}/session.json"
    write_json(path, {"id": sid, "status": "running", "jobs": []})
    with pytest.raises(ValueError, match="Kill or finish"):
        ui.delete_session(sid)
    with pytest.raises(ValueError, match="Unknown session"):
        ui.delete_session("../../config")


def test_natural_route_selection_markers_and_stall_reach_runner(workspace, monkeypatch):
    from mclab import natural

    routes = [
        {"route_id": rid, "start": [0, 70, 0], "goal": [100, 70, 0], "yaw": 0}
        for rid in ("R1", "R2", "R3")
    ]
    write_json(workspace / "routes.json", routes)
    request = {
        "kind": "natural",
        "entrants": ["segmented_goal"],
        "seed": 1337,
        "routes": "routes.json",
        "route_ids": ["R3", "R1"],
        "show_markers": True,
        "stall_seconds": 5,
    }
    job = runs.plan(request)[0]
    assert job["route_ids"] == ["R1", "R3"]  # file order
    assert job["expected"] == 2
    calls = []
    monkeypatch.setattr(natural, "run", lambda *a, **kw: calls.append((a, kw)))
    runs.execute(job)
    assert [r["route_id"] for r in calls[0][0][1]] == ["R1", "R3"]
    assert calls[0][1]["show_markers"] is True
    assert calls[0][1]["stall_seconds"] == 5
    for bad in (
        {"route_ids": []},
        {"route_ids": ["R9"]},
        {"stall_seconds": 2},
        {"show_markers": "yes"},
    ):
        with pytest.raises(ValueError):
            runs.plan({**request, **bad})
    # Without a selection every route in the file runs, as before.
    del request["route_ids"]
    assert runs.plan(request)[0]["expected"] == 3


def test_catalog_lists_route_ids_per_route_file(workspace):
    write_json(
        workspace / "config/routes.json",
        {"routes": [{"route_id": "A"}, {"route_id": "B"}]},
    )
    write_json(workspace / "config/backends.json", {"backends": []})
    write_json(workspace / "courses/profiles.json", {"courses": {}})
    assert runs.catalog()["route_ids"] == {"config/routes.json": ["A", "B"]}


def test_companion_check_list_is_a_readable_run():
    path = runs.ROOT / "results/companion/20261101T000000000000Z/summary.json"
    cases = [
        {"case": "craft planks", "pass": True, "detail": "16"},
        {"case": "furnace", "pass": False, "detail": "0 ingots"},
    ]

    [row] = runs.result_rows(path, cases)

    assert row["suite"] == "companion"
    assert row["status"] == "failed"
    assert row["statuses"] == {"passed": 1, "failed": 1}


@pytest.fixture
def companion(workspace):
    (workspace / "scripts").mkdir(exist_ok=True)
    for name in ("check-crafting", "run-gather", "run-build"):
        (workspace / f"scripts/{name}.py").write_text("")
    for relative in ("tests/fixtures/schematics/a.build.json", "builds/b.build.json"):
        (workspace / relative).parent.mkdir(parents=True, exist_ok=True)
        (workspace / relative).write_text("{}")
    return workspace


def test_companion_plan_validates_every_input(companion):
    base = {"kind": "companion", "job": "build", "start": [1, 64, -3]}
    base["build"] = "builds/b.build.json"
    [job] = runs.plan({**base, "dx": 4, "rotation": 90})
    assert job["args"] == [
        *("--start", "1", "64", "-3"),
        *("--build", "builds/b.build.json", "--dx", "4", "--dz", "2"),
        *("--rotation", "90"),
    ]
    [gather] = runs.plan(
        {"kind": "companion", "job": "gather", "start": [0, 0, 0], "iron": True}
    )
    assert "--iron" in gather["args"]
    assert gather["args"][-2:] == ["--seed", "1337"]  # lab world is flat
    assert runs.plan({"kind": "companion", "job": "crafting"})[0]["args"] == []
    bad = [
        {"job": "animals"},  # script missing
        {"job": "nope"},
        {**base, "start": [30_000_001, 0, 0]},
        {**base, "start": [1, 2]},
        {**base, "start": [1.5, 2, 3]},
        {**base, "start": [True, 2, 3]},
        {**base, "rotation": 45},
        {**base, "build": "../etc/x.build.json"},
        {**base, "build": "builds/../scripts/run-build.py"},
        {**base, "build": "builds/missing.build.json"},
        {"job": "gather", "start": [0, 0, 0], "iron": "yes"},
    ]
    for changes in bad:
        with pytest.raises(ValueError):
            runs.plan({"kind": "companion", **changes})


def test_companion_catalog_lists_builds_and_schematics(companion):
    (companion / "results/companion/2026").mkdir(parents=True)
    (companion / "results/companion/2026/c.build.json").write_text("{}")
    (companion / "tests/fixtures/schematics/h.litematic").write_text("")
    found = runs.companion_catalog()
    assert found["checks"] == ["crafting"]
    assert found["builds"] == [
        "builds/b.build.json",
        "results/companion/2026/c.build.json",
        "tests/fixtures/schematics/a.build.json",
    ]
    assert found["schematics"] == ["tests/fixtures/schematics/h.litematic"]


def test_convert_schematic_stays_inside_allowed_folders(companion, monkeypatch):
    from mclab import schematic

    (companion / "tests/fixtures/schematics/h.litematic").write_text("")
    monkeypatch.setattr(
        schematic,
        "convert",
        lambda src, out: {
            "size": [1, 1, 1],
            "materials": {"minecraft:dirt": 1},
            "flags": [],
        },
    )
    done = runs.convert_schematic("tests/fixtures/schematics/h.litematic")
    assert done["output"] == "builds/h.build.json"
    for relative in ("../h.litematic", "scripts/x.litematic", 5, "builds/b.build.json"):
        with pytest.raises(ValueError):
            runs.convert_schematic(relative)


def test_companion_job_is_refused_while_lab_server_is_down(companion, monkeypatch):
    from mclab import server

    monkeypatch.setattr(server, "status", lambda: {"state": "stopped"})
    with pytest.raises(ValueError, match="not running"):
        ui.Dashboard().start({"kind": "companion", "job": "crafting"})
    with pytest.raises(RuntimeError, match="not running"):
        runs.execute(runs.plan({"kind": "companion", "job": "crafting"})[0])


def test_run_companion_launches_script_and_labels_results(companion, monkeypatch):
    from mclab import server

    monkeypatch.setattr(server, "status", lambda: {"state": "running"})
    launched = []

    class Process:
        def __init__(self, args, **kwargs):
            launched.append((args, kwargs))
            folder = companion / "results/companion/20261101T000000000000Z"
            folder.mkdir(parents=True)
            write_json(
                folder / "summary.json", [{"case": "x", "pass": True, "detail": ""}]
            )

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(runs.subprocess, "Popen", Process)
    job = {**runs.plan({"kind": "companion", "job": "crafting"})[0], "before": []}
    assert runs.execute(job)["status"] == "completed_companion"
    args, kwargs = launched[0]
    assert args[:3] == [runs.sys.executable, "-u", "-c"]
    assert args[4].endswith("check-crafting.py")
    assert kwargs["stdin"] is runs.subprocess.DEVNULL and "stdout" not in kwargs
    summary = companion / "results/companion/20261101T000000000000Z/summary.json"
    [row] = runs.result_rows(summary, read_json(summary))
    assert row["stage"] == "crafting" and row["statuses"] == {"passed": 1}


def test_run_companion_without_summary_is_an_error(companion, monkeypatch):
    from mclab import server

    monkeypatch.setattr(server, "status", lambda: {"state": "running"})

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout=None):
            return 3

    monkeypatch.setattr(runs.subprocess, "Popen", Process)
    job = {**runs.plan({"kind": "companion", "job": "crafting"})[0], "before": []}
    assert "code 3" in runs.execute(job)["error"]


def test_companion_live_reads_progress_summary_and_op_events(companion):
    from mclab import companion_progress as cp

    folder = companion / "results/companion/20261101T000000000000Z"
    folder.mkdir(parents=True)
    cp.write_progress(folder, job="build", stage="placing", done=3, total=10)
    write_json(
        folder / "build.json", {"stop": "done", "steps": 7, "counts": {"correct": 2}}
    )
    lines = [
        {"type": "observation", "payload": {}},
        {"type": "server_event", "payload": {"type": "place_done", "tick": 5}},
        {
            "type": "server_event",
            "payload": {"type": "break_failed", "data": {"reason": "far"}},
        },
        {
            "type": "server_event",
            "payload": {"type": "rejected", "data": {"op": "use", "reason": "x"}},
        },
    ]
    (folder / "events.jsonl").write_text(
        "garbage\n" + "\n".join(json.dumps(x) for x in lines) + "\n{trunc"
    )
    live = runs.companion_live(folder)
    assert live["progress"]["done"] == 3
    assert live["report"]["steps"] == 7
    assert [(e["op"], e["result"], e["reason"]) for e in live["events"]] == [
        ("place", "done", None),
        ("break", "failed", "far"),
        ("use", "rejected", "x"),
    ]
    assert cp.job_name(folder) == "build"


def test_progress_helper_never_raises(tmp_path):
    from mclab import companion_progress as cp

    cp.write_progress(tmp_path / "missing", job="x")  # folder absent: ignored
    assert cp.read_progress(tmp_path) is None
    cp.write_job(tmp_path, "check-crafting")
    assert cp.job_name(tmp_path) == "check-crafting"


def test_sessions_show_live_companion_progress(companion):
    from mclab import companion_progress as cp

    job = {**runs.plan({"kind": "companion", "job": "crafting"})[0]}
    job.update(status="running", before=[], started_at=time.time())
    folder = companion / "results/companion/20261101T000000000000Z"
    folder.mkdir(parents=True)
    cp.write_progress(folder, stage="crafting", done=1, total=4)
    path = companion / "results/sessions/20261101T000000-aaaaaaaa/session.json"
    write_json(path, {"id": path.parent.name, "status": "running", "jobs": [job]})
    dashboard = ui.Dashboard()
    dashboard.process_path = path
    dashboard.process = type("P", (), {"poll": lambda self: None})()
    [session] = dashboard.sessions()
    assert session["jobs"][0]["live"]["progress"]["total"] == 4
