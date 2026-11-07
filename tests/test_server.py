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


def test_forget_state_only_removes_its_own_launch(tmp_path):
    from mclab.supervisor import forget_state

    path = tmp_path / "supervisor.json"
    path.write_text(json.dumps({"launch_id": "old"}))
    # our launch: removed
    assert forget_state(path, "old") is True
    assert not path.exists()
    # a successor already wrote its own launch: never touched
    path.write_text(json.dumps({"launch_id": "new"}))
    assert forget_state(path, "old") is False
    assert path.exists()


def test_forget_state_retries_a_transiently_locked_file(tmp_path):
    from mclab.supervisor import forget_state

    real = tmp_path / "supervisor.json"
    real.write_text(json.dumps({"launch_id": "me"}))

    class LockedOnce:
        def __init__(self, path):
            self.path, self.locked = path, True

        def exists(self):
            return self.path.exists()

        def read_text(self, encoding=None):
            return self.path.read_text(encoding=encoding)

        def unlink(self):
            if self.locked:
                self.locked = False
                raise PermissionError  # Windows reader still holds the file
            self.path.unlink()

    stub = LockedOnce(real)
    assert forget_state(stub, "me", pause=lambda *_: None) is True
    assert not real.exists()
