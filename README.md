# Minecraft AI laboratory

This repository implements the bootstrap stage of
[the technical handoff](Minecraft_AI_Technical_Handoff.md). It runs an isolated,
local Minecraft Java 26.3 server and generates development courses C0–C4.
The bootstrap Fabric mod verifies arena geometry against the generated specification.

**G0 passed human inspection. G1 is in progress; G2–G8 have not run.**
The Carpet adapter and a bounded qualification bridge are implemented. There is
no scored controller, evaluator, model integration, or learned policy yet.
See [setup status](docs/SETUP_STATUS.md) and
[implementation decisions](docs/DECISIONS.md).

## Local operation

Run from this directory in PowerShell with Git and uv installed:

```powershell
rtk proxy powershell -NoProfile -File scripts/setup.ps1
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server start
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server status
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course verify-bootstrap
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 report
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server stop
```

The initial start requires accepting Minecraft's EULA. After reading
[the EULA](https://aka.ms/MinecraftEULA), an accepting operator can use
`server start --accept-eula`. Acceptance persists only in ignored local runtime files.
Marc authorized acceptance for the current local installation on October 22, 2026.

Join **127.0.0.1:25575** from an authenticated Java 26.3 client on this desktop.
The game interface binds loopback, so this address is not accessible from another
computer. The initial spawn is near **1000,80,1000**. C1 has an oak log at
**1000,80,1012**. Face south (+Z; yaw 0). Inspect the platform, empty hands,
Survival movement, and log. The client needs no mods for this check.
Do not start scored comparisons until body qualification and subsequent gates pass.

Empty-server pausing is disabled so queued setup and reset requests keep running.
Bootstrap verification starts
after 160 simulation ticks and checks 2048 cells per tick. Allow approximately
11 seconds of simulation after startup. `server start` acknowledges supervisor
startup; `server status` reports `running` only after a Minecraft protocol response.

## Configuration and generated courses

[config/lab.json](config/lab.json) controls origin, memory, ports, and launch settings.
Settings initialize `server.properties` on first bootstrap. Existing properties are
preserved; startup rejects host/port drift. To change those fields, edit both files
with the lab stopped. No existing public server configuration is touched.

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server stop
# Wait for server status to say stopped before replacing files.
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course build --course C3 --seed 4
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course verify-generated
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server start
```

All C0–C4 functions are generated. Only the selected course occupies the active
arena. Building changes files while stopped. The datapack compares its specification
hash at load and reconstructs a newly selected specification after warming chunks.
Rebuilding the same specification does **not** reset a modified world. Episode reset,
scoped entity cleanup, and body state digest checks belong to G2 and are pending.

Courses clear a bounded 49×41×49 volume, build a four-block-deep isolated platform,
and use absolute coordinates. Vanilla fill limits are respected. The source
specifications are development fixtures: C0 is an empty mechanics arena with a log,
not a completed qualification suite. C3 has a two-block-tall wall; C4 has a
full-width one-block rise and descent. Restriction enforcement and success/drop
provenance are pending. Do not regard these as scored arenas yet.

## Builds and verification

```powershell
rtk proxy powershell -NoProfile -File scripts/build-mod.ps1
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 mod-install
rtk proxy uv run --locked pytest -q
rtk proxy uv run --locked ruff check src tests scripts
rtk proxy uv run --locked ruff format --check src tests scripts
```

Install the built mod only with the server stopped. The build script sets JDK and
Gradle cache paths for its own process; Java 21 remains installed and unchanged.
Dependency versions/hashes are in `config/dependencies.lock.json`, `uv.lock`,
`mod/gradle.lockfile`, `mod/buildscript-gradle.lockfile`, and Gradle verification
metadata. The wrapper pins and verifies Gradle 9.6.0. `-UpdateLocks` is an explicit
maintainer operation, not the normal build. Keep generated wrapper files in version
control. The repository initially had no commits.

`scripts/discover.ps1` and `scripts/resolve-dependencies.py` are dependency-update
tools. Normal setup never resolves latest versions. Updating the lock needs review
and complete requalification.

## Artifacts and boundaries

- `runtime/server/`: disposable lab world, dependencies, properties and logs.
- `runtime/server/mclab-bootstrap-evidence.json`: server-thread geometry snapshot,
  serialized off-thread; includes launch ID, specification hash and mismatch counts.
- `results/generated/`: reproduction specifications and functions.
- `results/doctor.json`: local environment checks.
- `results/reports/initial.{md,csv,json}`: explicit zero-episode initial report.

Runtime and results are ignored. Keep the handoff intact. The supervisor exposes
only token-authenticated status/stop on loopback port 8766; port 8765 hosts the
qualification bridge. No model service or paid request runs during setup.
The future evaluator and filtered sensor must follow the handoff's server-thread,
queue, deadline, observation and stale-episode constraints.

## Body qualification and human reference

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body qualify
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body protocol-check
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body reference --player Osadar932 --seconds 60
```

These are bounded development checks, not scored task episodes. Qualification
uses a disposable mechanics fixture near 990,80,988.5 and rejects setup/cleanup
when a human is inside it. Keep human players near the platform center.
The suite first proves Carpet commands and then uses the Java action pack.
It preserves all attempts, checks, actual apply ticks, observations and errors
in `results/qualification/`. Human reference captures go in `results/references/`.
Do not run these commands concurrently.

The supplementary protocol check has 24 live admission/cancellation checks,
reported separately from the 39 mechanics checks. G1 remains open while the
human interaction reference is deferred; later gates have not started.

The reference recorder reads the actual latest client button state received by
the server, plus view and body state. It records client/server timing uncertainty;
these are not training-ready, precisely aligned client-timestamped demonstrations.
See [bridge protocol](docs/BRIDGE_PROTOCOL.md) for the qualification-only interface.
