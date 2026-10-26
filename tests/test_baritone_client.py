import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mclab import baritone_client


def _serve(seen: list):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            seen.append((self.command, self.path, self.headers["Authorization"], body))
            ok = self.headers["Authorization"] == "Bearer " + "t" * 64
            status, value = (
                (200, {"accepted": True}) if ok else (401, {"error": "unauthorized"})
            )
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _reply

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _client(tmp_path, monkeypatch, port, token):
    state = tmp_path / "client-bridge.json"
    state.write_text(json.dumps({"token": token, "port": port}))
    monkeypatch.setattr(baritone_client, "STATE", state)
    return baritone_client.BaritoneClient()


def test_client_sends_bearer_token_and_commands(tmp_path, monkeypatch):
    seen = []
    server = _serve(seen)
    try:
        client = _client(tmp_path, monkeypatch, server.server_port, "t" * 64)
        client.status()
        client.goto(1, 80, -3)
        client.mine("minecraft:oak_log", 1)
        client.cancel()
    finally:
        server.shutdown()
    assert [(m, p) for m, p, _, _ in seen] == [
        ("GET", "/status"),
        ("POST", "/goto"),
        ("POST", "/mine"),
        ("POST", "/cancel"),
    ]
    assert all(auth == "Bearer " + "t" * 64 for _, _, auth, _ in seen)
    assert seen[1][3] == {"x": 1, "y": 80, "z": -3}


def test_client_raises_on_rejected_command(tmp_path, monkeypatch):
    server = _serve([])
    try:
        client = _client(tmp_path, monkeypatch, server.server_port, "wrong" * 8)
        with pytest.raises(RuntimeError, match="401"):
            client.status()
    finally:
        server.shutdown()


def test_prepare_rejects_tampered_baritone_jar(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "baritone-api-fabric-1.20.0.jar").write_bytes(b"not the pinned jar")
    monkeypatch.setattr(baritone_client, "RUNTIME", tmp_path)
    with pytest.raises(RuntimeError, match="SHA-256"):
        baritone_client.prepare()
