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
