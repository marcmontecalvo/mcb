"""Server instances: lab unchanged, a named instance (play) fully separate."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mclab import bootstrap, modbuild, paths, recorder, server, supervisor
from mclab.paths import ROOT, instance_paths

# ---------------------------------------------------------------- resolution


@pytest.mark.parametrize("name", [None, "", "lab"])
def test_the_lab_keeps_its_historical_locations(name, tmp_path):
    p = instance_paths(name, tmp_path)
    runtime = tmp_path / "runtime"
    assert p.name == "lab"
    assert p.config_file == tmp_path / "config/lab.json"
    assert p.server == runtime / "server"
    assert p.state == runtime / "supervisor.json"
    assert p.launch_lock == runtime / "launch.lock"
    assert p.supervisor_log == runtime / "supervisor.log"
    assert p.console_log == runtime / "server-console.log"
    assert p.mod_build == runtime / "mod-build.json"
    assert p.arena_lock_name == ".arena.lock"


def test_a_named_instance_keeps_runtime_files_apart(tmp_path):
    p = instance_paths("play", tmp_path)
    runtime = tmp_path / "runtime/instances/play"
    assert p.name == "play"
    assert p.config_file == tmp_path / "config/instances/play.json"
    assert p.server == runtime / "server"
    assert p.state == runtime / "supervisor.json"
    assert p.launch_lock == runtime / "launch.lock"
    assert p.supervisor_log == runtime / "supervisor.log"
    assert p.console_log == runtime / "server-console.log"
    assert p.mod_build == runtime / "mod-build.json"
    assert p.companion_log == runtime / "companion-host.log"
    assert p.arena_lock_name == ".arena-play.lock"


@pytest.mark.parametrize(
    "name", ["Play", "1play", "a_b", "../x", "play/x", "a" * 17, "play x"]
)
def test_bad_instance_names_are_refused(name, tmp_path):
    with pytest.raises(ValueError, match="MCLAB_INSTANCE"):
        instance_paths(name, tmp_path)


def test_the_longest_and_hyphenated_names_are_accepted(tmp_path):
    assert instance_paths("a" * 16, tmp_path).name == "a" * 16
    assert instance_paths("my-play-2", tmp_path).name == "my-play-2"


def test_module_constants_follow_the_resolved_instance():
    assert paths.SERVER == paths.INSTANCE.server
    assert paths.INSTANCE_RUNTIME == paths.INSTANCE.runtime
    assert paths.RUNTIME == ROOT / "runtime"
    assert server.STATE == paths.INSTANCE.state


# -------------------------------------------------------------------- config


def _write_config(root, name, **overrides):
    base = {
        "schema_version": 1,
        "minecraft": "26.3",
        "server_host": "127.0.0.1",
        "game_port": 25565,
        "bridge_port": 8775,
        "supervisor_port": 8776,
        "heap_gb": 4,
        "online_mode": True,
    }
    base.update(overrides)
    base = {k: v for k, v in base.items() if v is not None}
    p = instance_paths(name, root)
    p.config_file.parent.mkdir(parents=True, exist_ok=True)
    p.config_file.write_text(json.dumps(base))
    return p


def _config(monkeypatch, root, name, **overrides):
    monkeypatch.setattr(paths, "INSTANCE", _write_config(root, name, **overrides))
    return paths.config()


def test_the_lab_mod_defaults_to_true_and_needs_a_lab_port(monkeypatch, tmp_path):
    value = _config(monkeypatch, tmp_path, "lab", lab_port=8768)
    assert value["lab_mod"] is True
    with pytest.raises(KeyError):
        _config(monkeypatch, tmp_path, "lab")


def test_no_lab_port_is_needed_without_the_lab_mod(monkeypatch, tmp_path):
    value = _config(monkeypatch, tmp_path, "play", lab_mod=False)
    assert value["lab_mod"] is False


def test_a_lab_port_clash_only_counts_with_the_lab_mod(monkeypatch, tmp_path):
    _config(monkeypatch, tmp_path, "play", lab_mod=False, lab_port=25565)
    with pytest.raises(ValueError, match="distinct"):
        _config(monkeypatch, tmp_path, "play", lab_mod=True, lab_port=25565)


def test_the_companion_port_must_be_distinct_and_unprivileged(monkeypatch, tmp_path):
    _config(monkeypatch, tmp_path, "play", lab_mod=False, companion_port=8777)
    with pytest.raises(ValueError, match="distinct"):
        _config(monkeypatch, tmp_path, "play", lab_mod=False, companion_port=8775)
    with pytest.raises(ValueError, match="unprivileged"):
        _config(monkeypatch, tmp_path, "play", lab_mod=False, companion_port=80)


def test_loopback_and_heap_are_still_required(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        _config(monkeypatch, tmp_path, "play", lab_mod=False, server_host="0.0.0.0")
    with pytest.raises(ValueError, match="heap"):
        _config(monkeypatch, tmp_path, "play", lab_mod=False, heap_gb=64)


def test_lan_opens_only_the_game_port_and_must_be_a_yes_or_no(monkeypatch, tmp_path):
    value = _config(monkeypatch, tmp_path, "play", lab_mod=False, lan=True)
    assert value["server_host"] == "127.0.0.1"  # every other port stays loopback
    assert paths.game_bind(value) == ""  # Minecraft: every interface
    assert paths.game_bind(_config(monkeypatch, tmp_path, "play", lab_mod=False)) == (
        "127.0.0.1"
    )
    assert paths.game_bind({}) == "127.0.0.1"
    with pytest.raises(ValueError, match="lan"):
        _config(monkeypatch, tmp_path, "play", lab_mod=False, lan="yes")


def test_lan_ip_is_the_address_other_pcs_reach_this_one_at():
    class Probe:
        def __init__(self, address):
            self.address = address

        def connect(self, _target):  # UDP: picks the route, sends nothing
            if self.address is None:
                raise OSError("no network")

        def getsockname(self):
            return (self.address, 50000)

        def close(self):
            pass

    assert paths.lan_ip(probe=lambda: Probe("192.168.1.20")) == "192.168.1.20"
    assert paths.lan_ip(probe=lambda: Probe("127.0.0.1")) is None
    assert paths.lan_ip(probe=lambda: Probe(None)) is None


def test_the_shipped_play_server_is_open_to_the_lan(monkeypatch):
    monkeypatch.setattr(paths, "INSTANCE", instance_paths("play"))
    assert paths.config()["lan"] is True
    assert paths.game_bind(json.loads((ROOT / "config/lab.json").read_text())) == (
        "127.0.0.1"
    )


def test_config_ports_lists_what_the_instance_needs():
    assert paths.config_ports({}) == [
        "game_port",
        "bridge_port",
        "supervisor_port",
        "lab_port",
    ]
    assert paths.config_ports({"lab_mod": False, "companion_port": 1}) == [
        "game_port",
        "bridge_port",
        "supervisor_port",
        "companion_port",
    ]


def test_the_shipped_play_config_is_valid_and_independent_of_the_lab(monkeypatch):
    monkeypatch.setattr(paths, "INSTANCE", instance_paths("play"))
    play = paths.config()
    lab = json.loads((ROOT / "config/lab.json").read_text())
    assert play["lab_mod"] is False
    assert play["companion"] == {"bot": "default"}
    assert play["world"]["level_type"] == "minecraft:normal"
    assert not {play["game_port"], play["bridge_port"], play["supervisor_port"]} & {
        lab["game_port"],
        lab["bridge_port"],
        lab["supervisor_port"],
        lab["lab_port"],
    }
    assert play["companion_port"] not in lab.values()


def test_the_shipped_arena_config_is_a_void_world_apart_from_lab_and_play(
    monkeypatch,
):
    monkeypatch.setattr(paths, "INSTANCE", instance_paths("arena"))
    arena = paths.config()
    lab = json.loads((ROOT / "config/lab.json").read_text())
    play = json.loads((ROOT / "config/instances/play.json").read_text())
    assert arena["lab_mod"] is False and arena["lan"] is True
    # the bot comes in at the lobby, built first: under the world spawn is only void
    assert arena["companion"] == {"bot": "default", "spawn": "lobby"}
    world = arena["world"]
    assert world["level_type"] == "minecraft:flat"
    generator = json.loads(world["generator_settings"])
    assert generator["layers"] == [{"block": "minecraft:air", "height": 1}]
    assert generator["biome"] == "minecraft:the_void"
    assert world["generate_structures"] is False
    assert world["max_players"] >= 17  # 8v8 and the bot
    ports = {arena[key] for key in paths.config_ports(arena)}
    others = {lab[k] for k in paths.config_ports(lab)}
    others |= {play[k] for k in paths.config_ports(play)}
    assert not ports & others


# ----------------------------------------------------------------- bootstrap

ARTIFACTS = {
    "minecraft_server": {"name": "server-x.jar"},
    "fabric_launcher": {"name": "fabric-x.jar"},
    "carpet": {"name": "carpet-x.jar"},
    "fabric_api": {"name": "fabric-api-x.jar"},
}
PLAY_SETTINGS = {
    "server_host": "127.0.0.1",
    "game_port": 25565,
    "online_mode": True,
    "lab_mod": False,
    "world": {
        "seed": 20261123,
        "level_type": "minecraft:normal",
        "difficulty": "normal",
        "view_distance": 10,
        "simulation_distance": 8,
        "motd": "MCB companion",
    },
}
LAB_SETTINGS = {
    "server_host": "127.0.0.1",
    "game_port": 25575,
    "online_mode": True,
    "lab_mod": True,
    "world_seed": 734193,
}


def _bootstrap(monkeypatch, tmp_path, name, settings):
    inst = instance_paths(name, tmp_path)
    downloads = tmp_path / "dl"
    downloads.mkdir(exist_ok=True)

    def fake_download(artifact):
        path = downloads / artifact["name"]
        path.write_bytes(b"jar")
        return path

    monkeypatch.setattr(bootstrap, "INSTANCE", inst)
    monkeypatch.setattr(bootstrap, "SERVER", inst.server)
    monkeypatch.setattr(bootstrap, "config", lambda: settings)
    monkeypatch.setattr(bootstrap, "lock", lambda: {"artifacts": ARTIFACTS})
    monkeypatch.setattr(bootstrap, "download", fake_download)
    monkeypatch.setattr(bootstrap, "java", lambda: Path("java.exe"))
    result = bootstrap.bootstrap()
    props = dict(
        line.split("=", 1)
        for line in (inst.server / "server.properties").read_text().splitlines()
    )
    return inst, props, result


def test_play_bootstrap_writes_a_normal_world(monkeypatch, tmp_path):
    inst, props, result = _bootstrap(monkeypatch, tmp_path, "play", PLAY_SETTINGS)
    assert props["server-ip"] == "127.0.0.1"
    assert props["server-port"] == "25565"
    assert props["level-type"] == "minecraft:normal"
    assert props["level-seed"] == "20261123"
    assert props["view-distance"] == "10"
    assert props["simulation-distance"] == "8"
    assert props["spawn-protection"] == "0"
    assert props["pause-when-empty-seconds"] == "-1"
    assert props["max-players"] == "8"
    assert props["enable-rcon"] == "false"
    assert props["white-list"] == "false"
    assert props["motd"] == "MCB companion"
    assert "generator-settings" not in props
    assert "generate-structures" not in props
    assert (inst.server / "eula.txt").read_text().endswith("eula=false\n")
    assert (inst.server / "mods/carpet-x.jar").exists()
    assert (inst.server / "mods/fabric-api-x.jar").exists()
    assert (inst.server / "server.jar").exists()
    assert result["server"] == str(inst.server)


def test_a_lan_bootstrap_binds_the_game_port_on_every_interface(monkeypatch, tmp_path):
    _, props, _ = _bootstrap(
        monkeypatch, tmp_path, "play", {**PLAY_SETTINGS, "lan": True}
    )
    assert props["server-ip"] == ""
    (tmp_path / "lab").mkdir()
    _, lab, _ = _bootstrap(
        monkeypatch, tmp_path / "lab", "lab", {**LAB_SETTINGS, "lan": True}
    )
    assert lab["server-ip"] == ""


def test_arena_bootstrap_writes_an_empty_void_world(monkeypatch, tmp_path):
    void = (
        '{"layers":[{"block":"minecraft:air","height":1}],"biome":"minecraft:the_void"}'
    )
    world = {
        **PLAY_SETTINGS["world"],
        "level_type": "minecraft:flat",
        "generator_settings": void,
        "generate_structures": False,
        "max_players": 20,
        "motd": "MCB arena",
    }
    settings = {**PLAY_SETTINGS, "game_port": 25566, "world": world}
    _, props, _ = _bootstrap(monkeypatch, tmp_path, "arena", settings)
    assert props["level-type"] == "minecraft:flat"
    assert props["generator-settings"] == void
    assert props["generate-structures"] == "false"
    assert props["max-players"] == "20"
    assert props["motd"] == "MCB arena"
    assert props["spawn-protection"] == "0"


def test_lab_bootstrap_still_writes_the_flat_lab_world(monkeypatch, tmp_path):
    _, props, _ = _bootstrap(monkeypatch, tmp_path, "lab", LAB_SETTINGS)
    assert props["level-type"] == "minecraft:flat"
    assert props["level-seed"] == "734193"
    assert props["max-players"] == "4"
    assert "generator-settings" in props


def test_bootstrap_keeps_an_existing_server_properties(monkeypatch, tmp_path):
    inst = instance_paths("play", tmp_path)
    inst.server.mkdir(parents=True)
    (inst.server / "server.properties").write_text("motd=kept\n")
    (inst.server / "eula.txt").write_text("eula=true\n")
    _, props, _ = _bootstrap(monkeypatch, tmp_path, "play", PLAY_SETTINGS)
    assert props == {"motd": "kept"}
    assert (inst.server / "eula.txt").read_text() == "eula=true\n"


# ------------------------------------------------------------------ modbuild


def _mod_tree(root):
    for mod in paths.MODS:
        (root / mod / "src").mkdir(parents=True)
        (root / mod / "src/A.java").write_text(mod)
        (root / mod / "build.gradle").write_text("")
        libs = root / mod / "build/libs"
        libs.mkdir(parents=True)
        (libs / modbuild.jar_name(mod)).write_bytes(mod.encode())
    (root / "config").mkdir()
    (root / "config/dependencies.lock.json").write_text("{}")
    past = 1_000_000_000
    for path in root.rglob("*"):
        if (
            path.is_file()
            and "build" not in path.parts[-3:-1]
            and path.suffix != ".jar"
        ):
            os.utime(path, (past, past))


def _modbuild(monkeypatch, tmp_path, name, lab_mod):
    _mod_tree(tmp_path)
    inst = instance_paths(name, tmp_path)
    monkeypatch.setattr(modbuild, "ROOT", tmp_path)
    monkeypatch.setattr(modbuild, "INSTANCE", inst)
    monkeypatch.setattr(modbuild, "SERVER", inst.server)
    monkeypatch.setattr(modbuild, "config", lambda: {"lab_mod": lab_mod})
    monkeypatch.setattr(modbuild.server, "assert_stopped", lambda: None)
    return inst


def test_the_play_instance_installs_only_the_body(monkeypatch, tmp_path):
    inst = _modbuild(monkeypatch, tmp_path, "play", lab_mod=False)
    mods = inst.server / "mods"
    mods.mkdir(parents=True)
    (mods / "mcb-lab-0.1.0.jar").write_bytes(b"stale")
    record = modbuild.install()
    assert sorted(p.name for p in mods.iterdir()) == ["mcb-body-0.1.0.jar"]
    assert list(record["jars"]) == ["mcb-body"]
    assert json.loads(inst.mod_build.read_text())["jars"].keys() == {"mcb-body"}
    assert not (tmp_path / "runtime/mod-build.json").exists()
    assert modbuild.installed() is True


def test_the_lab_instance_installs_both_mods_in_its_own_record(monkeypatch, tmp_path):
    inst = _modbuild(monkeypatch, tmp_path, "lab", lab_mod=True)
    record = modbuild.install()
    assert set(record["jars"]) == {"mcb-body", "mcb-lab"}
    assert inst.mod_build == tmp_path / "runtime/mod-build.json"
    assert inst.mod_build.exists()
    assert modbuild.installed() is True


def test_installed_notices_a_changed_or_missing_jar(monkeypatch, tmp_path):
    inst = _modbuild(monkeypatch, tmp_path, "play", lab_mod=False)
    assert modbuild.installed() is False  # nothing recorded yet
    modbuild.install()
    jar = inst.server / "mods/mcb-body-0.1.0.jar"
    jar.write_bytes(b"tampered")
    assert modbuild.installed() is False
    jar.unlink()
    assert modbuild.installed() is False


def test_installed_is_false_after_a_source_change_or_legacy_jar(monkeypatch, tmp_path):
    inst = _modbuild(monkeypatch, tmp_path, "play", lab_mod=False)
    modbuild.install()
    (inst.server / "mods" / modbuild.LEGACY_JAR).write_bytes(b"old")
    assert modbuild.installed() is False
    (inst.server / "mods" / modbuild.LEGACY_JAR).unlink()
    (tmp_path / "mcb-body/src/A.java").write_text("changed")
    assert modbuild.installed() is False


def test_a_lab_record_without_the_lab_jar_is_not_installed_for_the_lab(
    monkeypatch, tmp_path
):
    inst = _modbuild(monkeypatch, tmp_path, "play", lab_mod=False)
    modbuild.install()
    monkeypatch.setattr(modbuild, "config", lambda: {"lab_mod": True})
    assert modbuild.installed() is False
    assert inst.mod_build.exists()


def test_a_stale_source_refuses_installation(monkeypatch, tmp_path):
    _modbuild(monkeypatch, tmp_path, "play", lab_mod=False)
    os.utime(tmp_path / "mcb-body/src/A.java")  # newer than the jar
    jar = tmp_path / "mcb-body/build/libs/mcb-body-0.1.0.jar"
    os.utime(jar, (1_000_000_000, 1_000_000_000))
    with pytest.raises(RuntimeError, match="rebuild"):
        modbuild.install()


def test_a_missing_jar_asks_for_a_build(monkeypatch, tmp_path):
    _modbuild(monkeypatch, tmp_path, "play", lab_mod=False)
    (tmp_path / "mcb-body/build/libs/mcb-body-0.1.0.jar").unlink()
    with pytest.raises(RuntimeError, match="build-mod"):
        modbuild.install()


# ------------------------------------------------------------ server helpers


def test_only_non_lab_instances_get_a_jvm_instance_flag():
    assert server.instance_flag("lab") == []
    assert server.instance_flag("play") == ["-Dmclab.instance=play"]


def test_orphan_matching_never_crosses_instances(monkeypatch, tmp_path):
    lab_jvm = "java -Dmclab.launch_id=1 -jar fabric-server-launch.jar"
    play_jvm = "java -Dmclab.launch_id=2 -Dmclab.instance=play -jar x.jar"
    play2_jvm = "java -Dmclab.launch_id=3 -Dmclab.instance=play2 -jar x.jar"
    monkeypatch.setattr(server, "INSTANCE", instance_paths("lab", tmp_path))
    assert server._is_ours(lab_jvm)
    assert not server._is_ours(play_jvm)
    monkeypatch.setattr(server, "INSTANCE", instance_paths("play", tmp_path))
    assert server._is_ours(play_jvm)
    assert not server._is_ours(lab_jvm)
    assert not server._is_ours(play2_jvm)


REAL_ORPHANS = server.orphans  # bound at import, before the autouse stub


def test_orphans_lists_only_this_instances_jvms(monkeypatch, tmp_path):
    sep = "\t" if os.name == "nt" else " "
    out = "\n".join(
        [
            f"11{sep}java -Dmclab.launch_id=a -jar f.jar",
            f"12{sep}java -Dmclab.launch_id=b -Dmclab.instance=play -jar f.jar",
        ]
    )

    class Out:
        stdout = out

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: Out())
    monkeypatch.setattr(server, "INSTANCE", instance_paths("play", tmp_path))
    assert REAL_ORPHANS() == [12]
    monkeypatch.setattr(server, "INSTANCE", instance_paths("lab", tmp_path))
    assert REAL_ORPHANS() == [11]


def test_the_arena_lock_is_per_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(recorder, "INSTANCE", instance_paths("play", tmp_path))
    with recorder.exclusive_arena():
        assert (tmp_path / "results/.arena-play.lock").exists()
        assert not (tmp_path / "results/.arena.lock").exists()
        with pytest.raises(RuntimeError, match="active"):
            with recorder.exclusive_arena():
                pass
    monkeypatch.setattr(recorder, "INSTANCE", instance_paths("lab", tmp_path))
    with recorder.exclusive_arena():
        assert (tmp_path / "results/.arena.lock").exists()


# ---------------------------------------------------------------- supervisor


class FakeProc:
    _pid = 100

    def __init__(self):
        FakeProc._pid += 1
        self.pid = FakeProc._pid
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.hang = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.hang:
            self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("host", timeout)
        return self.returncode


def _unreachable():
    raise OSError("no control API")


class Host:
    def __init__(self, tmp_path, ask_to_stop=_unreachable):
        self.started = []
        self.now = 0.0
        self.messages = []
        self.runner = supervisor.HostRunner(
            tmp_path / "logs/companion-host.log",
            popen=self.popen,
            clock=lambda: self.now,
            say=self.messages.append,
            ask_to_stop=ask_to_stop,
        )

    def popen(self, command, **options):
        proc = FakeProc()
        self.started.append((command, options, proc))
        return proc

    @property
    def proc(self):
        return self.started[-1][2]


def test_the_host_command_is_the_companion_host_module(tmp_path):
    host = Host(tmp_path)
    host.runner.tick(True)
    command, options, _ = host.started[0]
    assert command == [sys.executable, "-u", "-m", "mclab.companion_host"]
    assert options["cwd"] == ROOT
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] is options["stderr"]
    if os.name == "nt":
        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert (tmp_path / "logs/companion-host.log").exists()


def test_the_host_waits_for_a_running_server(tmp_path):
    host = Host(tmp_path)
    host.runner.tick(False)
    assert host.started == []
    host.runner.tick(True)
    host.runner.tick(True)
    assert len(host.started) == 1


def test_an_exited_host_restarts_with_backoff(tmp_path):
    host = Host(tmp_path)
    host.runner.tick(True)
    host.proc.returncode = 1
    host.runner.tick(True)  # notices the exit, schedules a restart
    assert len(host.started) == 1
    host.now += 0.5
    host.runner.tick(True)
    assert len(host.started) == 1  # still backing off
    host.now += 1.0
    host.runner.tick(True)
    assert len(host.started) == 2
    host.proc.returncode = 1
    host.runner.tick(True)
    host.now += 1.5  # second exit backs off longer than the first
    host.runner.tick(True)
    assert len(host.started) == 2
    host.now += 1.0
    host.runner.tick(True)
    assert len(host.started) == 3


def test_a_host_that_keeps_exiting_is_given_up_on(tmp_path):
    host = Host(tmp_path)
    for _ in range(supervisor.HOST_MAX_EXITS):
        host.now += 30
        host.runner.tick(True)
        host.proc.returncode = 3
        host.runner.tick(True)
    assert host.runner.gave_up
    count = len(host.started)
    host.now += 100
    host.runner.tick(True)
    assert len(host.started) == count
    assert any("giving up" in m and "3" in m for m in host.messages)


def test_exits_spread_over_time_do_not_add_up(tmp_path):
    host = Host(tmp_path)
    for _ in range(supervisor.HOST_MAX_EXITS * 2):
        host.now += supervisor.HOST_WINDOW_S
        host.runner.tick(True)
        host.proc.returncode = 1
        host.runner.tick(True)
    assert not host.runner.gave_up


def test_stop_terminates_and_never_restarts(tmp_path):
    host = Host(tmp_path)
    host.runner.tick(True)
    proc = host.proc
    host.runner.stop()
    assert proc.terminated and not proc.killed
    host.now += 1000
    host.runner.tick(True)
    assert len(host.started) == 1
    host.runner.stop()  # idempotent


def test_stop_kills_a_host_that_ignores_terminate(tmp_path):
    host = Host(tmp_path)
    host.runner.tick(True)
    host.proc.hang = True
    host.runner.stop()
    assert host.proc.terminated and host.proc.killed


def test_stop_asks_the_host_to_shut_down_before_terminating(tmp_path):
    # Windows terminate() is a hard kill: the host would never halt and despawn
    # (save) the body. Ask over the control API first.
    asked = []

    def ask():
        asked.append(1)
        host.proc.returncode = 0  # the host exits by itself

    host = Host(tmp_path, ask_to_stop=ask)
    host.runner.tick(True)
    host.runner.stop()
    assert asked == [1]
    assert not host.proc.terminated and not host.proc.killed


def test_a_host_that_stays_up_after_asking_is_terminated(tmp_path):
    host = Host(tmp_path, ask_to_stop=lambda: None)
    host.runner.tick(True)
    host.runner.stop()
    assert host.proc.terminated
    assert any("did not exit" in m for m in host.messages)


def test_the_default_ask_reads_the_instance_runtime_file(tmp_path, monkeypatch):
    seen = []

    class Client:
        @classmethod
        def from_runtime(cls, path):
            seen.append(path)
            return cls()

        def shutdown(self):
            seen.append("shutdown")

    from mclab import companion_api

    monkeypatch.setattr(companion_api, "ApiClient", Client)
    supervisor.ask_host_to_stop(tmp_path / "companion-host.json")
    assert seen == [tmp_path / "companion-host.json", "shutdown"]


def test_stop_without_a_host_or_after_it_exited_is_harmless(tmp_path):
    host = Host(tmp_path)
    host.runner.stop()
    host = Host(tmp_path)
    host.runner.tick(True)
    host.proc.returncode = 0
    host.runner.stop()
    assert not host.proc.terminated


class FakeHttp:
    def __init__(self, *_):
        self.timeout = 0

    def handle_request(self):
        pass

    def server_close(self):
        pass


def _run_main(monkeypatch, tmp_path, settings):
    """Run supervisor.main against fakes; return (jvm command, host runner events)."""
    state = tmp_path / "supervisor.json"
    state.write_text(json.dumps({"launch_id": "L", "token": "T", "port": 1}))
    commands = []
    events = []

    class Jvm(FakeProc):
        def __init__(self, command):
            super().__init__()
            self.polls = iter([None, None, 0])
            self.stdin = type("S", (), {"write": lambda *_: 0, "flush": lambda *_: 0})()

        def poll(self):
            return next(self.polls, 0)

    def popen(command, **_):
        commands.append(command)
        return Jvm(command)

    class Runner:
        def __init__(self, log):
            events.append(("log", log))

        def tick(self, running):
            events.append(("tick", running))

        def stop(self):
            events.append(("stop",))

    monkeypatch.setattr(supervisor, "config", lambda: settings)
    monkeypatch.setattr(supervisor, "STATE", state)
    monkeypatch.setattr(supervisor, "RUNTIME", tmp_path)
    monkeypatch.setattr(supervisor, "SERVER", tmp_path)
    monkeypatch.setattr(supervisor, "java", lambda: Path("java"))
    monkeypatch.setattr(supervisor, "ThreadingHTTPServer", FakeHttp)
    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    monkeypatch.setattr(supervisor, "HostRunner", Runner)
    monkeypatch.setattr(supervisor, "_minecraft_up", lambda port: True)
    monkeypatch.setattr(supervisor, "HOST_PROBE_S", 0.0)
    monkeypatch.setattr(supervisor, "minecraft_status", lambda port: {})
    monkeypatch.setattr(supervisor, "forget_state", lambda *a: None)
    from mclab import companion_service

    monkeypatch.setattr(
        companion_service, "run", lambda stop: events.append(("service",))
    )
    supervisor.main()
    return commands[0], events


BASE = {"heap_gb": 2, "bridge_port": 8775, "game_port": 25565, "lab_mod": False}


def test_a_play_supervisor_runs_the_host_not_the_companion_service(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(supervisor, "INSTANCE", instance_paths("play", tmp_path))
    command, events = _run_main(monkeypatch, tmp_path, {**BASE, "companion": {}})
    assert not any(a.startswith("-Dmclab.lab_port") for a in command)
    assert "-Dmclab.instance=play" in command
    assert ("tick", True) in events
    assert events[-1] == ("stop",)
    assert ("service",) not in events


def test_the_lab_supervisor_is_unchanged(monkeypatch, tmp_path):
    import time

    monkeypatch.setattr(supervisor, "INSTANCE", instance_paths("lab", tmp_path))
    command, events = _run_main(
        monkeypatch, tmp_path, {**BASE, "lab_mod": True, "lab_port": 8768}
    )
    time.sleep(0.2)  # the service thread is a daemon
    assert "-Dmclab.lab_port=8768" in command
    assert not any(a.startswith("-Dmclab.instance") for a in command)
    assert not any(e[0] in {"tick", "stop"} for e in events)


# ------------------------------------------------------------------ play_cli


def test_mcplay_selects_the_instance_before_mclab_is_imported():
    env = {k: v for k, v in os.environ.items() if k != "MCLAB_INSTANCE"}
    script = (
        "import sys\n"
        "from mclab import play_cli\n"
        "assert 'mclab.paths' not in sys.modules, 'paths imported too early'\n"
        "sys.argv = ['mcplay', 'server', 'status']\n"
        "code = play_cli.main()\n"
        "from mclab import paths\n"
        "print('INSTANCE', paths.INSTANCE.name, paths.SERVER)\n"
        "sys.exit(code)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "INSTANCE play" in result.stdout
    assert str(Path("runtime/instances/play/server")) in result.stdout
