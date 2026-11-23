"""Install the built project mods (mcb-body, mcb-lab) only with a stopped lab server."""

import hashlib
import shutil

from mclab import server
from mclab.bootstrap import sha256
from mclab.paths import INSTANCE, MODS, ROOT, SERVER, config, read_json, write_json

# Retired single-mod jar; it must never load beside the split mods.
LEGACY_JAR = "mclab-bootstrap-0.1.0.jar"


def jar_name(mod: str) -> str:
    return f"{mod}-0.1.0.jar"


def instance_mods() -> tuple[str, ...]:
    """Mods this instance runs: the body always, the lab only with lab_mod."""
    return MODS if config()["lab_mod"] else tuple(m for m in MODS if m != "mcb-lab")


def source_hash(mods: tuple[str, ...] | None = None) -> str:
    """One hash over the instance's mods' sources, so evidence binds what ran."""
    hasher = hashlib.sha256()
    for mod in mods or instance_mods():
        for path in sorted((ROOT / mod / "src").rglob("*")):
            if path.is_file():
                hasher.update(path.relative_to(ROOT).as_posix().encode())
                hasher.update(path.read_bytes())
    return hasher.hexdigest()


def install() -> dict:
    server.assert_stopped()
    selected = instance_mods()
    jars = {}
    for mod in selected:
        source = ROOT / mod / "build/libs" / jar_name(mod)
        if not source.exists():
            raise RuntimeError("Run scripts/build-mod.ps1 first")
        inputs = [p for p in (ROOT / mod / "src").rglob("*") if p.is_file()]
        inputs += [ROOT / mod / "build.gradle", ROOT / "config/dependencies.lock.json"]
        if any(p.stat().st_mtime > source.stat().st_mtime for p in inputs):
            raise RuntimeError(
                f"{mod} source/configuration changed; rebuild before installing"
            )
        jars[mod] = source
    mods = SERVER / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    (mods / LEGACY_JAR).unlink(missing_ok=True)
    for mod in MODS:
        if mod not in selected:  # a stale jar from a different instance setup
            (mods / jar_name(mod)).unlink(missing_ok=True)
    value = {"schema_version": 2, "source_hash": source_hash(selected), "jars": {}}
    for mod, source in jars.items():
        destination = mods / jar_name(mod)
        shutil.copyfile(source, destination)
        value["jars"][mod] = {"jar_sha256": sha256(source), "jar": str(destination)}
    write_json(INSTANCE.mod_build, value)
    return value


def installed() -> bool:
    """The instance's jars present, matching the recorded build and the source."""
    record_path = INSTANCE.mod_build
    if not record_path.exists() or (SERVER / "mods" / LEGACY_JAR).exists():
        return False
    selected = instance_mods()
    record = read_json(record_path)
    if record.get("schema_version") != 2 or record["source_hash"] != source_hash(
        selected
    ):
        return False
    for mod in selected:
        jar = SERVER / "mods" / jar_name(mod)
        if (
            not jar.exists()
            or mod not in record["jars"]
            or sha256(jar) != record["jars"][mod]["jar_sha256"]
        ):
            return False
    return True
