# Setup status — October 22, 2026

The repository started with only `Minecraft_AI_Technical_Handoff.md` and an empty
Git history. The original handoff has not been edited.

## Runtime-tested

| Item | Result |
| --- | --- |
| Python | Project environment: CPython 3.12.13; `uv.lock` generated and installed |
| JDK | Project-local Temurin 25.0.4.1+1; system Corretto 21 retained |
| Gradle/Loom | Wrapper 9.6.0 with distribution checksum; fixed Loom 1.17.21 builds |
| Server | Minecraft Java 26.3, protocol 777, authenticated mode, Survival |
| Fabric | Loader 0.19.5 and API 0.164.0+26.3 load successfully |
| Carpet | Pinned v26.3 asset loads; SHA-256 matches handoff |
| Process lifecycle | Hidden supervisor start/status, Minecraft status handshake, graceful stop, restart |
| Binding | Game 127.0.0.1:25575; private supervisor 127.0.0.1:8766 |
| Heap | Configured 4 GB; no performance tuning or inference timing claim |
| Datapack | Format 121.0 loaded without function parse errors |
| Generated files | C0–C4; C1 seed 0 selected; absolute coordinates and bounded fills |
| C1 world geometry | All **98,441** cells checked on the server thread; **0 mismatches** |
| Automated tests | **28 passed**; course reproducibility/detours/fill bounds, tamper checks, protocol bounds, stale evidence rejection |
| Python quality | Ruff lint and formatting pass |
| Provider traffic | No requests; no paid batches, downloads of model registries, or training |

C1 specification hash:
`8632e6132b76947bc406ac6c5bbee311d1fe569c9b07cd1fdaa8afae93ea9bdf`.
Geometry snapshot completed at simulation tick 208. Verification uses a launch ID
to reject evidence left by a prior JVM. This is an initial block-identity check,
not a repeated reset/body-state digest or a new live scan on every CLI invocation.

The EULA was accepted only after Marc's explicit approval in this session. The
server is left running for local human inspection. Do not infer readiness solely
from the `starting` acknowledgement: check `server status` for the actual protocol
response. Runtime logs include a third-party JOML Unsafe deprecation warning; the
project's Java source builds without deprecation warnings.

## Gate ledger

| Gate | Status | Evidence still required |
| --- | --- | --- |
| G0 | **Pending human check** | Join with a Java 26.3 client and inspect C1 |
| G1 | Not run | Command proof, body adapter, real-player C0 comparisons and cancellation |
| G2 | Not run | Reset generations, scoring, recorder, 20 identical authoritative state checks |
| G3 | Not run | At least 9/10 deterministic C1 completions |
| G4 | Not run | Versioned backends, valid real probes, cancellation/deadline/error tests |
| G5–G8 | Not run | Navigation, demonstrations/training, construction and fresh holdouts |

**Scored episodes attempted: 0. Success rate: unavailable.** No model or baseline
performance has been established. C0 is a development fixture, not a completed
mechanics qualification. C2–C4 were generated and contract-tested but not inspected
in a live client. There is no gameplay bridge listening on reserved port 8765.

## Complete G0

Join **127.0.0.1:25575** on this desktop with an authenticated Java 26.3 client.
The generated spawn is near **1000,80,1000**, with an oak log at
**1000,80,1012**. Face south (+Z). Confirm the platform and log render correctly,
that Survival movement works, and report any issue. New human presence appears in
the server-side snapshot; presence alone does not certify inspection.

The handoff explicitly requires the human check before advancing to body work.
Once confirmed, continue with the Carpet command proof and G1 rather than claiming
any later stage from the bootstrap evidence.

## Reproduction

See [README](../README.md) for full setup and start/stop commands. On this prepared
desktop:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 doctor
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 server status
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course verify-generated
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 course verify-bootstrap
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 report
```

Raw evidence and reports live in ignored `runtime/` and `results/`. The stable
summary is this file. No credentials/auth tokens are included in committed files.
When testing a changed course, stop the server first; a new spec hash triggers a
rebuild. Resetting a modified arena with the same spec is still G2 work.

## Source-reviewed, not runtime-proven

The handoff's Carpet, Baritone, Jev, Mineflayer and mc_aiplayer findings remain
source-reviewed starting points. Only Carpet loading has been exercised here.
No fake body has spawned, no Baritone client has run, no Mineflayer compatibility
probe has passed, and no jevtests code has been copied. Licensing review and
attribution for any future adapter reuse remain required before copying it.
