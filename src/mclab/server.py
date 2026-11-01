"""Local server lifecycle; supervisor and Minecraft each bind loopback."""

import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

from mclab.bootstrap import java
from mclab.paths import ROOT, RUNTIME, SERVER, config, read_json, write_json

STATE = RUNTIME / "supervisor.json"


def port_free(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def control(operation: str, timeout: float = 3) -> dict:
    state = read_json(STATE)
    request = urllib.request.Request(
        f"http://127.0.0.1:{state['port']}/{operation}",
        method="POST" if operation == "stop" else "GET",
        headers={"Authorization": "Bearer " + state["token"]},
        data=b"" if operation == "stop" else None,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
    kernel.CloseHandle(ctypes.c_void_p(handle))
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def status() -> dict:
    if not STATE.exists():
        return {"state": "stopped"}
    try:
        return control("status")
    except (OSError, ValueError, urllib.error.URLError):
        try:
            pid = read_json(STATE).get("pid")
        except (OSError, ValueError):  # the supervisor removed it as we looked
            return {"state": "stopped"}
        if isinstance(pid, int) and not pid_alive(pid):
            return {"state": "stopped", "detail": "supervisor process is gone"}
        return {"state": "unreachable", "detail": "Inspect runtime/server-console.log"}


def _spawn_detached(output) -> subprocess.Popen:
    """Start the supervisor outside the caller's process group and job, so killing
    the script (Ctrl+C, TaskStop, a dashboard Kill) does not take the lab down."""
    command = [sys.executable, "-m", "mclab.supervisor"]
    options = {
        "cwd": ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": output,
        "stderr": output,
    }
    if os.name != "nt":
        return subprocess.Popen(command, start_new_session=True, **options)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:  # leave a kill-on-close job when the job allows breakaway
        return subprocess.Popen(
            command,
            creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
            **options,
        )
    except OSError:
        return subprocess.Popen(command, creationflags=flags, **options)


def assert_stopped() -> None:
    if status()["state"] not in {"stopped", "exited"}:
        raise RuntimeError("Stop the lab server before replacing runtime/course files")


def start(accept_eula: bool = False) -> dict:
    # Exclusive admission prevents concurrent CLI launches orphaning a JVM.
    RUNTIME.mkdir(parents=True, exist_ok=True)
    launch_lock = RUNTIME / "launch.lock"
    try:
        handle = launch_lock.open("x")
    except FileExistsError as error:
        raise RuntimeError(
            "Another launch is in progress; inspect runtime/launch.lock"
        ) from error
    try:
        with handle:
            handle.write(str(os.getpid()))
            return _start(accept_eula)
    finally:
        launch_lock.unlink(missing_ok=True)


def _start(accept_eula: bool = False) -> dict:
    assert_stopped()
    settings = config()
    for key in ("game_port", "bridge_port", "lab_port", "supervisor_port"):
        if not port_free(settings[key]):
            raise RuntimeError(
                f"Port {settings[key]} is in use; no process was stopped"
            )
    java()
    if not (SERVER / "fabric-server-launch.jar").exists():
        raise RuntimeError("Run mclab bootstrap first")
    eula = SERVER / "eula.txt"
    if accept_eula:
        eula.write_text("# Accepted explicitly by the operator.\neula=true\n")
    if not eula.exists() or "eula=true" not in eula.read_text().splitlines():
        raise RuntimeError(
            "Accept Minecraft's EULA, then use server start --accept-eula"
        )
    # Refuse drift rather than overwrite an operator's properties.
    properties = dict(
        line.split("=", 1)
        for line in (SERVER / "server.properties").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    if properties.get("server-ip") != "127.0.0.1" or properties.get(
        "server-port"
    ) != str(settings["game_port"]):
        raise RuntimeError("Server properties host/port differ from config/lab.json")
    if properties.get("pause-when-empty-seconds") != "-1":
        raise RuntimeError("Set pause-when-empty-seconds=-1 in lab server.properties")
    token = secrets.token_urlsafe(32)
    write_json(
        STATE,
        {
            "port": settings["supervisor_port"],
            "token": token,
            "launch_id": secrets.token_hex(16),
        },
    )
    logfile = RUNTIME / "supervisor.log"
    with logfile.open("ab") as output:
        proc = _spawn_detached(output)
    write_json(STATE, {**read_json(STATE), "pid": proc.pid})
    for _ in range(100):
        current = status()
        if current["state"] in {"starting", "running"}:
            return current
        if proc.poll() is not None:
            raise RuntimeError(f"Supervisor exited; inspect {logfile}")
        time.sleep(0.1)
    raise RuntimeError(f"Supervisor did not respond; inspect {logfile}")


def stop() -> dict:
    current = status()
    if current["state"] in {"stopped", "exited"}:
        return current
    return control("stop")


def varint(value: int) -> bytes:
    result = bytearray()
    while True:
        part = value & 0x7F
        value >>= 7
        result.append(part | (0x80 if value else 0))
        if not value:
            return bytes(result)


def receive(sock: socket.socket, count: int) -> bytes:
    output = bytearray()
    while len(output) < count:
        part = sock.recv(count - len(output))
        if not part:
            raise OSError("Connection closed")
        output.extend(part)
    return bytes(output)


def read_varint(sock: socket.socket) -> int:
    value = 0
    for position in range(5):
        part = receive(sock, 1)[0]
        value |= (part & 0x7F) << (7 * position)
        if not part & 0x80:
            return value
    raise ValueError("Oversize protocol VarInt")


def minecraft_status(port: int) -> dict:
    host = b"localhost"
    handshake = b"\x00" + varint(777) + varint(len(host)) + host
    handshake += struct.pack(">H", port) + b"\x01"
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.sendall(varint(len(handshake)) + handshake + b"\x01\x00")
        length = read_varint(sock)
        if not 1 <= length <= 1048576 or read_varint(sock) != 0:
            raise ValueError("Invalid Minecraft status packet")
        size = read_varint(sock)
        if not 0 < size <= length:
            raise ValueError("Invalid Minecraft status payload")
        return json.loads(receive(sock, size))
