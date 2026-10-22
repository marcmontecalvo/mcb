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
| Automated tests | **38 passed**; course, protocol, stale evidence, recording and reference analysis |
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
| G0 | **Passed** | Human joined and confirmed the stone platform and log |
| G1 | **In progress** | Command/API proof and human walk/jump capture implemented; full C0 remains gated |
| G2 | Not run | Reset generations, scoring, recorder, 20 identical authoritative state checks |
| G3 | Not run | At least 9/10 deterministic C1 completions |
| G4 | Not run | Versioned backends, valid real probes, cancellation/deadline/error tests |
| G5–G8 | Not run | Navigation, demonstrations/training, construction and fresh holdouts |

**Scored episodes attempted: 0. Success rate: unavailable.** No model or baseline
performance has been established. C0 is a development fixture, not a completed
mechanics qualification. C2–C4 were generated and contract-tested but not inspected
in a live client. Port 8765 now hosts the qualification-only bridge.

## G0 inspection completed

Marc joined and reported: “I'm on a small stone platform and I see a log”.
The server separately recorded the authenticated human join. G0 is recorded in
`docs/gates.json`; movement fidelity is assessed in G1, not inferred from the quote.

The human reference capture on October 22 recorded 1200 server-tick samples:
three jumps peaked at 1.252203340253729 blocks, matching the fake body, and two
steady 20-tick walking segments measured 4.3169284283 and 4.3170087260 blocks.
The fake's 20-tick standing-start walk was 4.3103033875 blocks. The different start
conditions and unknown client/server alignment are retained in the comparison.

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
source-reviewed starting points. Carpet commands and action-pack integration
have now been exercised through the 39-check development suite.

## G1 development evidence

[G1_BODY_PROOF.json](G1_BODY_PROOF.json) preserves the latest 39/39 passing
server checks, observed values, provisional tolerances, source/jar hashes,
recording paths and the human walk/jump reference. These checks include command
and API spawn, movement/look/jump, mining and interruption, normal pickup,
placement/inventory decrement, collision, slab stepping, sprint, ledge sneak,
fall damage, discrete entity attacks/cooldown, target and body knockback, reach
guarding, death/replacement, expiry/watchdog, and stale/duplicate identity rejection.

G1 remains open for the prepared human interaction reference and final capability
review. Marc deferred this check for later; no interaction preparation or capture
was started. The command prepares the original C1 log, a stationary-physics test cow,
and dirt in an empty hotbar slot:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body reference --player Osadar932 --seconds 90 --interactions
```

The human should use an empty hand to punch the cow once, mine and collect the
oak log, then place one supplied dirt block on the platform. Incoming dig/use/
attack packets and authoritative results are recorded for comparison. Preparation
has been implemented and compiled; this human interaction run is still pending.

All earlier development attempts remain under `results/qualification/`, and
`mclab report` exports their recorded outcomes to `qualification_attempts.csv`.
Failures exposed and repaired these layers:

- Synchronous unknown-name lookup: seed only allocated lab identities in the
  in-memory resolver; actual spawn readiness still waits for registration.
- Empty-server pause: disable it in the lab profile; reject stale queued requests.
- Ledge input gaps: renew five-tick commitments before expiry, and retain sneak
  while stopping residual forward motion.
- Same-boundary mining renewal: apply valid replacements atomically before expiry
  release, preserving continuous mining progress.
- Pickup fixture/controller: bound the approach by 20 movement ticks to handle
  ordinary item scatter; no forced pickup.
- Knockback fixture: replace NoAI cows, which skip travel, with normal-physics
  cows whose own walking speed is zero.
- Build/install ordering: qualification now checks installed source provenance
  and archives exact development inputs before sending any commands.

The source checkout and compiled mod are local development artifacts. No model
ranking, qualified reset claim, scored episode or external baseline result is
inferred from this suite. No paid provider call has run.
Carpet fake bodies have now spawned through commands and the Java API. No Baritone
client has run, no Mineflayer compatibility probe has passed, and no jevtests code
has been copied. Licensing review and
attribution for any future adapter reuse remain required before copying it.
