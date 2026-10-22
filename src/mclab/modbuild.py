"""Install a built project mod only with a stopped lab server."""

import hashlib
import shutil

from mclab import server
from mclab.bootstrap import sha256
from mclab.paths import ROOT, SERVER, write_json


def source_hash() -> str:
    hasher = hashlib.sha256()
    for path in sorted((ROOT / "mod/src").rglob("*")):
        if path.is_file():
            hasher.update(path.relative_to(ROOT).as_posix().encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def install() -> dict:
    server.assert_stopped()
    source = ROOT / "mod/build/libs/mclab-bootstrap-0.1.0.jar"
    if not source.exists():
        raise RuntimeError("Run scripts/build-mod.ps1 first")
    source_inputs = [p for p in (ROOT / "mod/src").rglob("*") if p.is_file()]
    source_inputs.extend(
        [ROOT / "mod/build.gradle", ROOT / "config/dependencies.lock.json"]
    )
    if any(p.stat().st_mtime > source.stat().st_mtime for p in source_inputs):
        raise RuntimeError(
            "Mod source/configuration changed; rebuild before installing"
        )
    destination = SERVER / "mods/mclab-bootstrap-0.1.0.jar"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    value = {
        "schema_version": 1,
        "jar_sha256": sha256(source),
        "source_hash": source_hash(),
        "jar": str(destination),
    }
    write_json(ROOT / "runtime/mod-build.json", value)
    return value
