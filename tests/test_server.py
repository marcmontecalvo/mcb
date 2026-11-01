import json
import socket
import threading

import pytest

from mclab import server


@pytest.mark.parametrize("value", [0, 1, 127, 128, 777, 32768, 1048576])
def test_protocol_varint_roundtrip(value):
    # socketpair also checks fragmented reads rather than assuming one recv is enough.
    sender, receiver = socket.socketpair()
    with sender, receiver:
        for byte in server.varint(value):
            sender.sendall(bytes([byte]))
        assert server.read_varint(receiver) == value


def test_oversized_varint_is_rejected():
    sender, receiver = socket.socketpair()
    with sender, receiver:
        sender.sendall(b"\x80" * 5)
        with pytest.raises(ValueError, match="Oversize"):
            server.read_varint(receiver)


def test_status_payload_has_a_size_limit(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]

    def peer():
        with listener:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(server.varint(1048577))

    thread = threading.Thread(target=peer)
    thread.start()
    try:
        with pytest.raises(ValueError, match="Invalid Minecraft status packet"):
            server.minecraft_status(port)
    finally:
        thread.join(timeout=3)


def test_running_server_prevents_rebuilding(monkeypatch):
    monkeypatch.setattr(server, "status", lambda: {"state": "running"})
    with pytest.raises(RuntimeError, match="Stop the lab"):
        server.assert_stopped()


def _stale_state(monkeypatch, tmp_path, pid):
    state = tmp_path / "supervisor.json"
    state.write_text(json.dumps({"port": 1, "token": "t", "pid": pid}))
    monkeypatch.setattr(server, "STATE", state)
    return state


def test_status_is_stopped_when_the_recorded_supervisor_process_is_gone(
    monkeypatch, tmp_path
):
    _stale_state(monkeypatch, tmp_path, 4_000_000)  # no such process
    monkeypatch.setattr(server, "control", lambda *_: (_ for _ in ()).throw(OSError))
    assert server.status()["state"] == "stopped"
    server.assert_stopped()


def test_status_stays_unreachable_while_the_supervisor_process_lives(
    monkeypatch, tmp_path
):
    import os

    _stale_state(monkeypatch, tmp_path, os.getpid())
    monkeypatch.setattr(server, "control", lambda *_: (_ for _ in ()).throw(OSError))
    assert server.status()["state"] == "unreachable"


def test_pid_alive_knows_this_process_and_a_dead_one():
    import os

    assert server.pid_alive(os.getpid())
    assert not server.pid_alive(4_000_000)


def test_status_is_stopped_when_the_state_file_vanishes_mid_check(
    monkeypatch, tmp_path
):
    state = _stale_state(monkeypatch, tmp_path, 1)

    def gone(*_):
        state.unlink()
        raise OSError

    monkeypatch.setattr(server, "control", gone)
    assert server.status()["state"] == "stopped"
