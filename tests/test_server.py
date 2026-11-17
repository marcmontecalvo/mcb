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


def test_start_respawns_a_console_killed_supervisor(monkeypatch, tmp_path):
    settings = {
        "game_port": 25565,
        "bridge_port": 25566,
        "lab_port": 25567,
        "supervisor_port": 25568,
    }
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "fabric-server-launch.jar").write_bytes(b"")
    (server_dir / "eula.txt").write_text("eula=true\n")
    (server_dir / "server.properties").write_text(
        f"server-ip=127.0.0.1\nserver-port={settings['game_port']}\n"
        "pause-when-empty-seconds=-1\n"
    )
    monkeypatch.setattr(server, "SERVER", server_dir)
    monkeypatch.setattr(server, "RUNTIME", tmp_path)
    monkeypatch.setattr(server, "STATE", tmp_path / "supervisor.json")
    monkeypatch.setattr(server, "config", lambda: settings)
    monkeypatch.setattr(server, "port_free", lambda *_: True)
    monkeypatch.setattr(server, "java", lambda: None)
    monkeypatch.setattr(server.time, "sleep", lambda *_: None)

    spawned = []

    class FakeProc:
        # The first two supervisors die to a stray console CTRL event (0xC000013A)
        # before either answers or logs; the third one lives.
        def __init__(self, index):
            self.index, self.pid = index, 4000 + index

        def poll(self):
            return None if self.index == 2 else 0xC000013A

    def spawn(_output):
        spawned.append(FakeProc(len(spawned)))
        return spawned[-1]

    monkeypatch.setattr(
        server,
        "status",
        lambda: {"state": "stopped" if len(spawned) < 3 else "running"},
    )
    monkeypatch.setattr(server, "_spawn_detached", spawn)

    assert server._start()["state"] == "running"
    assert len(spawned) == 3
    # The state file carries the last spawn's pid so status() can date the file.
    assert json.loads((tmp_path / "supervisor.json").read_text())["pid"] == 4002


def test_start_reports_a_foreign_supervisor_exit(monkeypatch, tmp_path):
    settings = {
        "game_port": 25565,
        "bridge_port": 25566,
        "lab_port": 25567,
        "supervisor_port": 25568,
    }
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "fabric-server-launch.jar").write_bytes(b"")
    (server_dir / "eula.txt").write_text("eula=true\n")
    (server_dir / "server.properties").write_text(
        f"server-ip=127.0.0.1\nserver-port={settings['game_port']}\n"
        "pause-when-empty-seconds=-1\n"
    )
    monkeypatch.setattr(server, "SERVER", server_dir)
    monkeypatch.setattr(server, "RUNTIME", tmp_path)
    monkeypatch.setattr(server, "STATE", tmp_path / "supervisor.json")
    monkeypatch.setattr(server, "config", lambda: settings)
    monkeypatch.setattr(server, "port_free", lambda *_: True)
    monkeypatch.setattr(server, "java", lambda: None)
    monkeypatch.setattr(server.time, "sleep", lambda *_: None)

    class Dead:
        pid = 4001

        def poll(self):
            return 3  # some other failure: not the console event

    monkeypatch.setattr(server, "_spawn_detached", lambda _output: Dead())
    monkeypatch.setattr(server, "status", lambda: {"state": "stopped"})
    with pytest.raises(RuntimeError, match="Supervisor exited"):
        server._start()


def test_start_waits_for_a_port_a_stopped_server_still_holds(monkeypatch):
    # Run 20261117T184243178083Z: the switch back to the operator world failed
    # with "Port 25575 is in use" seconds after the arena server stopped.
    settings = {"game_port": 1, "bridge_port": 2, "lab_port": 3, "supervisor_port": 4}
    monkeypatch.setattr(server, "assert_stopped", lambda: None)
    monkeypatch.setattr(server, "config", lambda: settings)
    monkeypatch.setattr(server.time, "sleep", lambda *_: None)
    checks = []

    def port_free(port):
        checks.append(port)
        return port != 4 or checks.count(4) > 3  # held for three checks

    class Past(RuntimeError):
        pass

    def java():
        raise Past  # the ports were all free: start went on past them

    monkeypatch.setattr(server, "port_free", port_free)
    monkeypatch.setattr(server, "java", java)
    with pytest.raises(Past):
        server._start()

    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(server.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(server, "port_free", lambda port: port != 4)
    with pytest.raises(RuntimeError, match="Port 4 is in use"):
        server._start()
