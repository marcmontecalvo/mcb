import http.client
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mclab import dashboard
from mclab.dashboard_events import EventBroker, TooManyListeners


@contextmanager
def local_dashboard():
    value = SimpleNamespace(
        sessions=lambda: [{"id": "session-1", "status": "running"}],
        history=lambda: [{"run": "run-1", "status": "passed"}],
        builds=lambda: [],
        start=lambda request: {"id": "started", "kind": request.get("kind")},
    )
    handler = dashboard.handler(value)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, server, handler
    finally:
        handler.events.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get(base, path, *, headers=None):
    request = Request(base + path, headers=headers or {})
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def post(base, token, path, payload, *, headers=None):
    values = {
        "Content-Type": "application/json",
        "Origin": base,
        "X-UI-Token": token,
    }
    if headers:
        values.update(headers)
    request = Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers=values,
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_root_serves_built_index_with_same_token_and_assets(tmp_path, monkeypatch):
    root = tmp_path / "ui" / "dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(
        (
            '<meta name="mcb-token" content="__TOKEN__">'
            '<script src="/assets/app.js"></script>'
        ),
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log('ready')", encoding="utf-8")
    monkeypatch.setattr(dashboard, "UI_DIST", root)

    with local_dashboard() as (base, _, handler):
        status, headers, html = get(base, "/")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert b"__TOKEN__" not in html
        assert handler.token.encode() in html
        asset_status, asset_headers, body = get(base, "/assets/app.js")
        assert asset_status == 200, body
        assert asset_headers["Content-Type"].startswith("text/javascript")
        assert body == b"console.log('ready')"


def test_missing_build_is_explicit_and_legacy_remains_available(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "UI_DIST", tmp_path / "not-built")
    with local_dashboard() as (base, _, handler):
        status, _, body = get(base, "/")
        assert status == 503
        assert b"npm run build" in body
        assert b"/legacy" in body
        legacy_status, _, legacy = get(base, "/legacy")
        assert legacy_status == 200
        assert b"__TOKEN__" not in legacy
        assert handler.token.encode() in legacy


def test_static_asset_path_rejects_traversal_and_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("index", encoding="utf-8")
    secret = tmp_path / "private.txt"
    secret.write_text("outside", encoding="utf-8")
    try:
        (root / "assets" / "escape.txt").symlink_to(secret)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    monkeypatch.setattr(dashboard, "UI_DIST", root)

    with local_dashboard() as (base, _, _):
        with pytest.raises(HTTPError) as traversal:
            urlopen(base + "/assets/%2e%2e%2fprivate.txt", timeout=3)
        assert traversal.value.code == 404
        with pytest.raises(HTTPError) as symlink:
            urlopen(base + "/assets/escape.txt", timeout=3)
        assert symlink.value.code == 404

        alternate = tmp_path / "alternate"
        alternate.mkdir()
        outside_assets = tmp_path / "outside-assets"
        outside_assets.mkdir()
        (outside_assets / "exposed.js").write_text("outside", encoding="utf-8")
        try:
            (alternate / "assets").symlink_to(outside_assets, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"directory symlinks unavailable: {error}")
        monkeypatch.setattr(dashboard, "UI_DIST", alternate)
        with pytest.raises(HTTPError) as directory_symlink:
            urlopen(base + "/assets/exposed.js", timeout=3)
        assert directory_symlink.value.code == 404


def test_get_rejects_non_loopback_host_header():
    with local_dashboard() as (_, server, _):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/legacy", headers={"Host": "dashboard.local"})
            assert connection.getresponse().status == 403
        finally:
            connection.close()


def test_sse_snapshot_uses_durable_views_and_redacts_secrets(monkeypatch):
    from mclab import server as lab_server

    calls = {"status": 0, "sessions": 0, "history": 0}

    def status():
        calls["status"] += 1
        return {"state": "running", "token": "server-secret"}

    def sessions():
        calls["sessions"] += 1
        return [{"id": "s1", "token": "private-token", "log": "Bearer abc123"}]

    def history():
        calls["history"] += 1
        return [{"status": "passed"}]

    monkeypatch.setattr(lab_server, "status", status)
    from mclab import play_runs

    monkeypatch.setattr(
        play_runs, "server_states", lambda: {"play": "stopped", "arena": "running"}
    )
    value = SimpleNamespace(sessions=sessions, history=history)
    handler = dashboard.handler(value)
    handler.events.interval = 5
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("GET", f"/api/events?token={handler.token}")
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type").startswith("text/event-stream")
        lines = []
        while True:
            line = response.readline()
            if line in {b"\n", b"\r\n"}:
                break
            lines.append(line)
        assert b"event: snapshot\n" in lines
        data = json.loads(
            next(line[6:] for line in lines if line.startswith(b"data: "))
        )
        assert data["status"] == {
            "state": "running",
            "play": "stopped",
            "arena": "running",
        }
        assert data["sessions"][0]["token"] == "[REDACTED]"
        assert "abc123" not in data["sessions"][0]["log"]
        assert data["results"] == [{"status": "passed"}]
        assert calls == {"status": 1, "sessions": 1, "history": 1}
    finally:
        connection.close()
        handler.events.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sse_requires_token_and_enforces_listener_limit():
    with local_dashboard() as (base, server, handler):
        with pytest.raises(HTTPError) as unauthenticated:
            urlopen(base + "/api/events", timeout=3)
        assert unauthenticated.value.code == 403

        streams = []
        try:
            for _ in range(8):
                streams.append(_open_stream(server, handler))
            with pytest.raises(HTTPError) as limited:
                urlopen(
                    Request(
                        base + f"/api/events?token={handler.token}",
                        headers={"Connection": "close"},
                    ),
                    timeout=3,
                )
            assert limited.value.code == 503
        finally:
            for stream in streams:
                stream.close()


def _open_stream(server, handler):
    """An event stream held open, as a tab holds it: the response owns the socket
    (http.client hands it over for a body without a length), so it is kept."""
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    connection.request("GET", f"/api/events?token={handler.token}")
    response = connection.getresponse()
    assert response.status == 200
    return response


def test_a_full_pool_takes_back_the_slot_of_a_closed_tab_at_once():
    # A tab closed or reloaded keeps its slot until the next write fails, up to a
    # 15 s heartbeat on an idle dashboard: a few quick reloads filled the pool, and
    # the new page got 503 on every retry. A full pool now checks its peers first.
    with local_dashboard() as (base, server, handler):
        streams = [_open_stream(server, handler) for _ in range(8)]
        try:
            url = base + f"/api/events?token={handler.token}"
            with pytest.raises(HTTPError) as limited:  # every tab is still open
                urlopen(Request(url, headers={"Connection": "close"}), timeout=3)
            assert limited.value.code == 503
            streams.pop(0).close()  # the tab is gone; no write to it has failed yet
            for _ in range(40):  # the close reaches the server's socket in a moment
                try:
                    streams.append(urlopen(url, timeout=3))
                    break
                except HTTPError as refused:
                    assert refused.code == 503
                    threading.Event().wait(0.05)
            assert len(streams) == 8 and streams[-1].status == 200
        finally:
            for stream in streams:
                stream.close()


def test_a_full_broker_closes_only_listeners_whose_peer_is_gone():
    broker = EventBroker(
        SimpleNamespace(sessions=lambda: [], history=lambda: []),
        server_state=lambda: "stopped",
        max_listeners=2,
    )
    peers = {"a": True, "b": True}
    first = broker.subscribe(alive=lambda: peers["a"])
    second = broker.subscribe(alive=lambda: peers["b"])
    try:
        with pytest.raises(TooManyListeners):  # both still there: the limit holds
            broker.subscribe(alive=lambda: True)
        peers["a"] = False
        third = broker.subscribe(alive=lambda: True)
        assert first.closed and not second.closed and broker.listeners == 2
        assert list(first.stream(heartbeat=0.01)) == []  # its stream ends at once
        third.close()
    finally:
        first.close()
        second.close()
        broker.close()


def test_event_heartbeat_does_not_poll_game_status_again():
    calls = 0

    def server_state():
        nonlocal calls
        calls += 1
        return "stopped"

    broker = EventBroker(
        SimpleNamespace(sessions=lambda: [], history=lambda: []),
        server_state=server_state,
        interval=0.5,
    )
    subscription = broker.subscribe()
    stream = subscription.stream(heartbeat=0.03)
    try:
        assert "event: snapshot" in next(stream)
        assert next(stream) == ": heartbeat\n\n"
        assert calls == 1
    finally:
        subscription.close()
        broker.close()


def test_sse_reconnect_receives_a_fresh_durable_snapshot():
    sessions = [{"id": "before"}]
    broker = EventBroker(
        SimpleNamespace(sessions=lambda: list(sessions), history=lambda: []),
        server_state=lambda: "running",
        interval=5,
    )
    first = broker.subscribe()
    first_stream = first.stream(heartbeat=0.2)
    try:
        assert '"id":"before"' in next(first_stream)
        first.close()
        sessions[0] = {"id": "after"}
        second = broker.subscribe()
        second_stream = second.stream(heartbeat=0.5)
        try:
            assert '"id":"after"' in next(second_stream)
        finally:
            second.close()
    finally:
        first.close()
        broker.close()


def test_suites_resolve_save_list_and_suite_start_persists_selected_entries(
    tmp_path, monkeypatch
):
    from mclab import dashboard_suites

    monkeypatch.setattr(dashboard_suites, "list_suites", lambda: [{"id": "daily"}])
    monkeypatch.setattr(
        dashboard_suites,
        "save_suite",
        lambda request: {"id": "daily", "name": request["name"]},
    )
    jobs = [{"id": "job-1", "kind": "gather", "run_config": {"run": {"seed": 9}}}]
    monkeypatch.setattr(dashboard_suites, "resolve_entries", lambda entries: jobs)
    monkeypatch.setattr(dashboard, "SESSIONS", tmp_path / "results" / "sessions")
    monkeypatch.setattr(dashboard, "worker_busy", lambda: False)
    from mclab import server as lab_server

    monkeypatch.setattr(lab_server, "status", lambda: {"state": "running"})

    class Process:
        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *_a, **_k: Process())

    handler = dashboard.handler(SimpleNamespace())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    entries = [{"test_id": "gather-basic", "preset": "fast", "seeds": [9]}]
    try:
        assert get(base, "/api/suites")[2] == b'[{"id": "daily"}]'
        assert post(base, handler.token, "/api/suites", {"name": "Daily"}) == (
            200,
            {"id": "daily", "name": "Daily"},
        )
        assert post(base, handler.token, "/api/resolve", {"entries": entries}) == (
            200,
            {"jobs": jobs},
        )
        result = dashboard.Dashboard().start(
            {"kind": "suite", "entries": entries, "jobs": [{"kind": "evil"}]}
        )
        session = json.loads(
            (dashboard.SESSIONS / result["id"] / "session.json").read_text()
        )
        assert session["jobs"][0]["id"] == "job-1"
        assert session["suite_entries"] == entries
        assert session["kind"] == "session"
    finally:
        handler.events.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_redact_keeps_token_counts_but_hides_tokens():
    from mclab.dashboard_events import redact

    value = redact({"max_tokens": 64, "prompt_tokens": 9, "api_token": "s"})
    assert value["max_tokens"] == 64 and value["prompt_tokens"] == 9
    assert value["api_token"] != "s"


def test_corrupt_session_record_does_not_hide_others(tmp_path, monkeypatch):
    good, bad = tmp_path / "b", tmp_path / "a"
    good.mkdir()
    bad.mkdir()
    (good / "session.json").write_text(json.dumps({"status": "passed"}))
    (bad / "session.json").write_text("{")
    monkeypatch.setattr(dashboard, "SESSIONS", tmp_path)
    rows = dashboard.Dashboard().sessions()
    statuses = {row.get("id", row.get("status")): row["status"] for row in rows}
    assert statuses["a"] == "unreadable"
    assert "passed" in statuses.values()


def test_session_boots_a_stopped_lab_before_world_runs(tmp_path, monkeypatch):
    from mclab import dashboard_suites
    from mclab import server as lab_server

    monkeypatch.setattr(dashboard, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(dashboard, "worker_busy", lambda: False)
    monkeypatch.setattr(lab_server, "status", lambda: {"state": "stopped"})
    monkeypatch.setattr(
        dashboard.subprocess,
        "Popen",
        lambda *_a, **_k: SimpleNamespace(poll=lambda: None),
    )

    def start(jobs):
        monkeypatch.setattr(dashboard_suites, "resolve_entries", lambda _e: jobs)
        result = dashboard.Dashboard().start({"kind": "suite", "entries": []})
        path = dashboard.SESSIONS / result["id"] / "session.json"
        jobs = json.loads(path.read_text())["jobs"]
        return [j.get("operation") or j["kind"] for j in jobs]

    course = {"id": "job-1", "kind": "courses", "course": "C1"}
    assert start([course]) == ["server-start", "courses"]
    battery = {
        "id": "job-1",
        "kind": "battery",
        "run_config": {"run": {"no_restart": False}},
    }
    assert start([battery]) == ["battery"]  # the battery restarts the lab itself
    assert not dashboard.needs_lab_server({"kind": "setup"})
