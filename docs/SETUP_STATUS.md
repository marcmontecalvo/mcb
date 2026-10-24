# Setup status — October 24, 2026

**G0–G3 passed for the frozen initial C1 scope.** The final deterministic
perception-goal configuration completed **10/10 scored attempts**, each with a
verified causal break/drop/pickup chain and complete per-tick policy recording.
Completion took 128–141 ticks (median 132). Three stale controls were discarded
and regenerated from fresh observations within the declared recovery limit.
Earlier failed/invalid scored configurations remain in the all-attempt report.
G4 is in progress; G5–G8 are unqualified. Provider requests: **0**.
The original `Minecraft_AI_Technical_Handoff.md` remains unchanged.

## Runtime and qualification

| Item | Current evidence |
| --- | --- |
| Python | Project-local CPython 3.12.13 and locked uv environment |
| Java | Project-local Temurin 25.0.4.1+1; system Java 21 retained |
| Build | Gradle 9.6.0, Loom 1.17.21; locked dependencies and verification metadata |
| Minecraft | Java 26.3, protocol 777, authenticated mode, Survival |
| Mods | Fabric Loader 0.19.5, API 0.164.0+26.3, pinned Carpet v26.3 |
| Network | Loopback game 127.0.0.1:25575; authenticated bridge 8765 and supervisor 8766 |
| Python checks | 114 tests pass; Ruff lint and formatting pass |
| Java checks | Mod build and pure geometry verifier pass |
| G1 live suites | 39 body checks, 24 protocol checks, 12 reference-fixture checks |
| G2 reset foundation | 20 consecutive identical state digests; teardown confirmed |

The server stays active while empty (`pause-when-empty-seconds=-1`). A start
acknowledgement is not readiness: require a real protocol response from
`mclab server status`. Marc authorized this local installation's EULA acceptance
on October 22; no global Java/PATH or public server configuration was changed.

## G0

Marc joined and confirmed the stone platform and log. The initial C1 seed-0
server scan checked 98,441 cells with zero mismatches. Reviewed spec hash:
`8632e6132b76947bc406ac6c5bbee311d1fe569c9b07cd1fdaa8afae93ea9bdf`.
This historical bootstrap scan does not substitute for later reset verification.
Human reference fixtures intentionally modify the development arena.

## G1: qualified initial profile

[The capability review](G1_CAPABILITY_REVIEW.json) binds qualification to
[config/initial_body_profile.json](../config/initial_body_profile.json).
The scope is C1 ground locomotion, aiming, mining and pickup, with the tested
jump/sneak/sprint and terrain behaviors. The profile freezes runtime pins,
acceptance bounds and timing limits. `gates.require_initial_profile` rejects an
open gate, edited profile, changed physics pins, other courses or excluded
capabilities. This is scoped qualification, not full-game fidelity.

Entity attacks, combat knockback, construction and dimension travel remain
outside the initial policy profile. Their supplementary interaction checks remain
development evidence. Death/replacement qualification is the intentional Carpet
lifecycle: terminal disconnect and a fresh registered Survival body, rather than
imitation of a human respawn screen.

Completed human references:

- [Walk/jump and body proof](G1_BODY_PROOF.json): three jump peaks at
  1.252203340253729 blocks; steady 20-tick walking distances about 4.317 blocks.
- [Basic interactions](G1_HUMAN_INTERACTION_PROOF.json): mining 60 ticks versus
  fake 61–62, normal log collection, one dirt consumed per placement and a
  one-damage in-range punch. Causal drop-UUID scoring is still G2 work.
- [Guided mechanics](G1_GUIDED_MECHANICS_PROOF.json): strafe/release and a mining
  abort after 21 ticks followed by a full 60-tick restart.
- [Terrain](G1_HUMAN_TERRAIN_PROOF.json): 1,800 reviewed samples. Collision stopped
  at z=989.6999999880791, step rise was 0.5 without jumping, ledge position settled
  at z=990.2975091220263, and a five-block fall first landed with health 18. These
  match the corresponding fake-player results. Human collision review uses
  position and received input; the human horizontal-collision flag stayed false.
- [Sprint](G1_HUMAN_SPRINT_PROOF.json): complete, flat, grounded 20-tick windows
  with native sprint state and matching inputs, within existing development
  bounds. Same-duration fake observations are included. Overlapping windows are
  not independent trials; starting conditions still require careful comparison.
- [View](G1_HUMAN_VIEW_PROOF.json): stationary body, native degree yaw/pitch and
  pitch extremes -90..90. Native rotation and guard APIs supplement the recorded
  comparisons; a matched mouse-input trajectory is not claimed.
- [Wall-backed combat](G1_COMBAT_WALL_PROOF.json): all three attacks reached the
  cow, damage 1/0/1, attack strength reset to zero and recovered to one. The
  six-tick second hit was fully charged; zero damage is consistent with target
  damage immunity. This is supplementary evidence outside the initial C1 policy.

Incomplete/operator-mistake human captures were removed at Marc's request.
Actual implementation failures remain in development qualification history.
Recordings use server-observed packets/ticks; synchronized client timestamps
and training-ready demonstration alignment are not claimed.

## G2: reset foundation

`mclab course reset-check --count 20` is a bounded development check gated by the
initial C1 profile. It closes policy admission, clears pending controls, retires
the old body, creates new episode/body identities and increments a generation.
Retired asynchronous spawns remain watched and are removed if they arrive late.

The server removes arena-scoped drops/projectiles, normalizes the native 26.3
Overworld clock and weather, and reconstructs the complete 49×41×49 envelope.
Building and each verification pass are limited to 2,048 cells per tick. After
the first terrain verification it spawns a fresh body, overwrites initial state,
waits for registration/settling, warms 20 ticks and verifies terrain again.
The physical fingerprint must remain stable through that second scan.

The fingerprint covers full block states, position/velocity, view, Survival mode,
health/food, saturation/exhaustion/hunger timer, inventory/equipment, effects,
abilities, pose, air/fire/freeze, reach, attack strength and environment state.
Ephemeral names/UUIDs/episode IDs/generations are excluded from the physical
digest and recorded separately. Twenty consecutive resets matched. This is
authoritative state checking, not bit-exact simulation replay.

Safety and scope:

- Humans in the arena, unloaded chunks and unexpected living entities reject the
  reset. A human entering during reset aborts it.
- The foundation supports ordinary generated C1 and a fluid/block-entity-free
  arena. Unsupported state invalidates the attempt; arbitrary scheduled-effect
  or block-entity restoration is not claimed.
- Policy controls stay closed after `reset_ready` until explicit `course_begin`
  revalidates terrain/body state; reset-check itself cannot report scored success.
- Requests/events remain launch/run/episode/body scoped. Runs declare a
  1,500-request reset-check budget; motor age/duration limits are unchanged.
- JSONL and summaries live in `results/qualification/`. SQLite schema v1 in
  `results/reset_checks.sqlite3` records pending/finalized attempts. Pending work
  is never treated as passed or resumed inside a modified world.

G2 passed for initial C1: [29 adversarial and sensor checks](G2_EPISODE_PROOF.json)
verify pre-begin admission, forbidden controls, illegal floor breaking, planted
items, altered target, cancellation, the 600-tick deadline, controller watchdog,
out-of-bounds termination and native visibility fixtures. Those intentionally
failed/invalid attempts remain unscored and visible in the episode ledger.

The server scores native target break -> newly created item UUID -> normal
inventory transfer. `Inventory.add` is observed before `Player.take`, because
Minecraft restores a discarded stack count before `playerTouch` returns. A live
smoke exposed the original incorrect RETURN hook; its failed attempt was retained.
A second integration race between HTTP snapshots and terminal events was fixed
and covered by a regression test, with its invalid attempt also retained.

The recorder uses SQLite schema v1 plus JSONL, complete versioned event payloads,
per-tick body/policy events and periodic observed-map snapshots/deltas. A bounded
64-record writer queue keeps serialization/disk I/O off the motor loop and
invalidates missing critical recordings before finalization. Pending
attempts after a crash become interrupted. One OS arena lock covers execution,
reset, reference and mechanics runners. Body profile, native mod source, sensor and course-specification drift block scored runs.

## G3: deterministic C1

[The execution proof](G3_EXECUTION_PROOF.json) binds the final ten-attempt run to
its controller hash, source commit, native mod, course specification, sensor and
`c1-three-tick-bounded-resense-v4` timing profile. Every applied action used a
three-tick-old observation, within the existing five-tick bound. All ten item
UUID chains and every running policy tick were audited against JSONL and SQLite.
This is repeatability on one development C1 layout, not unseen-course navigation,
learned control or model skill.

The implementation history includes a 3-success/1-invalid aborted scored batch,
a 4/10 synchronous-recorder batch and an 8/10 asynchronous batch that terminated
on isolated stale-control rejections. Each remains recorded under its exact
configuration/source hash. The final profile discards stale commands, releases
inputs and makes a fresh observation/decision, at most three times per episode.
No timestamp is rewritten and no physics, action-age/duration or course budget
was relaxed. Recording failures invalidate attempts before finalization.

## Later work prepared

Choice/SystemOne and chat backend adapters have deadline, single-outstanding-call,
zero-retry, output-validation, redaction and budget tests. No configured endpoint
or credential was found; standard loopback ports 11434/1234/8000/8080 were closed.
`models probe` explicitly skipped. No provider readiness or model skill is claimed.
Upstream source references/hashes are pinned without vendoring unlicensed code.

Baritone v1.20.0 Fabric API artifact SHA-256 was verified against its release
metadata, and supported goal/mine/cancel signatures reviewed. The dedicated client
bridge, login and live scored baseline remain pending. C2 development planning
found 25 distinct variants, C3 only three and C4 one; C5 is absent. Later courses
need generator expansion and qualification. Thirty-five actual permitted C1
observations were exported with original/reversed/seeded candidate orders and
pending independent labels. No synchronized training demonstrations, trained
checkpoint or fresh holdout result is claimed.
See [decisions](DECISIONS.md), [bridge protocol](BRIDGE_PROTOCOL.md) and
[the gate ledger](gates.json) for extension boundaries.
