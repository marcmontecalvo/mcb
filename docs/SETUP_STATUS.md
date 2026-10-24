# Setup status — October 24, 2026

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
| Automated tests | **56 passed**; course, protocol, stale evidence, recording, report retention and reference analysis |
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
| G1 | **In progress** | Automated proof and human walk/jump/basic interactions recorded; remaining C0 reference and capability review pending |
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

[G1_PROTOCOL_PROOF.json](G1_PROTOCOL_PROOF.json) records a separate **24/24**
passing live protocol suite. It covers malformed control ranges and conflicts,
administrative/policy endpoint restrictions, action timing, duplicate action IDs,
queued-action cancellation with zero displacement, and rejection of commands from
a retired episode after a new body is ready. These supplement the 39 mechanics
checks; they do not replace real-player references or advance a gate.

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body protocol-check
```

The deferred human interaction reference was completed on October 24. The command
prepares the original C1 log, a stationary-physics test cow, and dirt in an empty
hotbar slot:

```powershell
rtk proxy powershell -NoProfile -File scripts/mclab.ps1 body reference --player Osadar932 --seconds 90 --interactions
```

Marc punched the cow with an empty hand, mined and collected the oak log, and
placed one dirt block. [G1_HUMAN_INTERACTION_PROOF.json](G1_HUMAN_INTERACTION_PROOF.json)
preserves the 1,800 contiguous samples' summary and recording hash. Mining took
60 server ticks versus 61–62 in the automated proof; dirt placement consumed one
block, and the in-range punch dealt 1 damage, matching the fake-player results.
The inventory transition confirms collection following the break; a causal
drop-UUID scorer has not been implemented. Client/server timing remains uncertain.

G1 remains open: the handoff also calls for real-player comparison of strafe/stop,
view/sprint, terrain interactions, fall damage, mining interruption, placement
reach, attack cooldown/knockback, and death/replacement where relevant. Those are
covered by automated development checks, but this interaction capture does not
establish their real-player equivalence. Complete that review and freeze the
qualified capability/timing profiles before G2. No reset or scoring work has begun.

The follow-up 120-second mechanics capture recorded 2,400 samples, but did not
contain qualifying pure-strafe/sprint segments, a mining abort or an attack
sequence. [The attempt record](G1_MECHANICS_REFERENCE_ATTEMPT.json) preserves
its reanalysis separately from the original capture summary. Marc disconnected
and requested a fresh attempt. Wait for reconnection and readiness, then guide
one action per recording rather than starting another timed sequence unattended.
The Python analyzer now reports pure strafe/sprint-input windows, residual travel
after released movement inputs, mining aborts and recorded attack intervals.
It rejects movement windows containing mixed inputs, turns or sample gaps.
Sprint input alone does not establish sprint state, and attack damage/intervals
alone do not distinguish cooldown from target invulnerability. G1 stays open.

The fresh, guided pass then recorded four separate captures (3,600 total samples):
left strafe/release, right strafe/release, sprint input/release, and interrupted
mining followed by rapid/spaced attacks. See
[G1_GUIDED_MECHANICS_PROOF.json](G1_GUIDED_MECHANICS_PROOF.json) for all summaries,
raw-recording hashes, provenance and comparison limitations. The mining abort
arrived after 21 ticks; the restart required 60 ticks versus 61 in the fake proof.
Rapid cow attacks included zero-damage results, but knockback made the target
awkward to hit. Marc requested a wall and a repeat rather than accepting that
sequence as cooldown equivalence.

The updated mod builds and adds human velocity, pose, authoritative sprint state,
per-tick attack strength, and attack strength before/after attack packets. These
fields are additive; earlier captures do not contain them. `body reference
--player Osadar932 --seconds 45 --combat` now prepares a five-block-wide,
two-block-high stone backstop behind the cow. Approach from the log side (+Z),
near x=1002.5,z=1006.5, facing north. The wall occupies x=1000..1004,
y=80..81,z=1003 and remains as a visible development fixture after capture;
the tagged cow is removed normally. Preparation refuses to overwrite other block
types or build through a human. This is setup geometry, not a scored reset.
The mod was rebuilt, installed with the server stopped, and the server restarted.
The subsequent 900-sample [wall-backed combat reference](G1_COMBAT_WALL_PROOF.json)
captured all three attacks within reach. The cow's northward motion stopped at
z=1004.45 against the backstop. Damage was 1,0,1 with attack gaps of 6 and 25 ticks.
All three swings began at attack strength 1.0 and reset it to 0.0. Tick samples
then showed strength recovering through 0.2,0.4,0.6,0.8,1.0. The zero-damage second
swing was fully charged, consistent with the target's damage-immunity window,
rather than an uncharged swing. These are server-observed packet/tick phases.
This repeat verifies the wall and direct human cooldown telemetry; earlier
captures still do not establish authoritative sprint-state equivalence. G1 stays
open for the remaining full C0 fidelity and capability/timing review.

After the wall-backed capture, the installed mod passed a fresh **39/39 mechanics**
and **24/24 protocol** rerun. [G1_CURRENT_MOD_REGRESSION.json](G1_CURRENT_MOD_REGRESSION.json)
preserves those attempts and current mod provenance separately from the October 22
proofs. Python tests (47), Ruff lint/format, mod build, installed-source checks and
diff whitespace checks pass. No scored episodes, G2 work or provider calls ran.

## Prepared remaining terrain references

Named human references now support `--mechanic collision`, `step`, `ledge` and
`fall`. Preview instructions with `body reference-plan --mechanic <name>` before
starting a capture. Preparation rebuilds 847 bounded C0 cells, verifies every cell,
restores health/food to 20 and relocates the named player facing south. Inventory
is preserved; armor, effects, nonordinary Survival, unloaded chunks or another
human in the fixture reject preparation. The ledge has a catch floor five blocks
below. The fall check immediately drops the player five blocks. No capture or
human manipulation runs just by previewing a plan.

The pure geometry verifier runs as part of Gradle `check` without a new external
test framework. It checks all layouts, bounds, unique cells, spawn support and
catch floors. `body reference-fixture-check` then exercises the same preparation
path with a fake player on the real server. Its latest **12/12** checks verify
geometry, target blocks, collision, slab stepping, ledge sneaking and fall damage;
see [G1_REFERENCE_FIXTURE_PROOF.json](G1_REFERENCE_FIXTURE_PROOF.json). The first
attempt passed its checks but was marked invalid by a teardown acknowledgement
mismatch. The runner was corrected to wait for the existing receipt and idle
state, and a fresh attempt passed with teardown confirmed. Both raw attempts remain.

The installed mod also passed fresh **39/39** body and **24/24** protocol suites;
the [current regression proof](G1_CURRENT_MOD_REGRESSION.json) binds all three
suites to the installed source/jar. Python now has **56 passing tests**, including
unsupported/conflicting preparation, old-mod acknowledgement rejection, gaps,
grounded step height and first-landing health before regeneration. Ruff lint and
formatting, the mod build/geometry verifier and diff whitespace checks pass.

The four named human terrain runs have **not** been recorded. Their summaries
retain setup acknowledgement, collision/input/height/health measurements and tick
continuity, and always require review. G1 remains in progress, and G2–G8 remain
unstarted. These prepared G1 fixtures do not implement scored episode resets.

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
