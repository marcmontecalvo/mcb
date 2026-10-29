# Qualification bridge v1

## Split into mcb-body and mcb-lab (October 28, 2026)

Section 0, step A of [the navigation direction](NAVIGATION_DIRECTION_2026-10-28.md)
split the single `mclab` mod into two independent mods (decision D9). **This
section overrides the rest of this document where they conflict**; the sections
below describe the single-mod bridge and remain accurate for everything not listed here.

| Mod | Port (`config/lab.json`) | Endpoints | Owns |
|---|---|---|---|
| `mcb-body` (`mcb-body/`) | `bridge_port` 8765 | `/admin`, `/controls`, `/state`, `/policy`, `/events` | Carpet body spawn/despawn, controls, sensing and terrain stream, reach guard, body interaction events, sensor self-checks, Carpet command proofs |
| `mcb-lab` (`mcb-lab/`) | `lab_port` 8768 | `/admin`, `/state`, `/events` | G0 arena verification, course reset, episodes and causal scoring, natural runs, mechanics fixtures, death/knockback proofs, human reference recording |

Both use the same bearer token and launch ID. Each has its own event ring and
sequence numbers. Neither imports the other; `mcb-lab` does not depend on Carpet.
Either mod loads and serves alone. The lab scores any **named player**: a Carpet
body, a human or a Baritone client, all through the same code path.

Body ops: `spawn` (`name`, `position`, `yaw`, `pitch`, `replace`, `stream`,
`limits`: `none`|`course`, `extended_observation`, `awareness`, `terrain_encoding`,
`via_command`), `despawn`, `release`, `heartbeat`, `sensor_check`,
`awareness_check`, `command_proof`, `trace`, and `controls` on `/controls`.
Lab ops: `course_reset` (`player`, `profile_id`, `course_spec`, `watchdog`),
`course_begin`, `course_end`, `course_test`, `natural_probe`, `natural_start`
(`player`, `subject` label; optional `show_markers` and `stall_ticks`, 0 = off,
else 40..2400), `natural_path` (`points`: up to 64 cells, drawn only while markers
are on), `stop` (`reason`), `fixture`/`death_proof`/
`knockback_proof` (`player`), `reference_start`, `reference_stop`, `heartbeat`.
The lab checks its op allowlist before identity. A natural run ends `failed` /
`stalled` when the subject stays within one block of a point for `stall_ticks`;
it always ends `success` / `reached_goal` inside the goal radius.

Behaviour that changed with the split:

- **Subjects join by name.** `course_reset` and `natural_start` name the player and
  may arrive before it exists. The lab waits up to 600 ticks for the player to be
  present for 5 consecutive ticks (state `awaiting_subject`), else fails with
  `subject_not_present`. The C1 Carpet path and the Baritone path are now one reset:
  park in spectator above the arena, rebuild, place, settle 20 ticks, fingerprint,
  re-verify terrain, compare. The old `human_course_*` ops are gone.
- **Early motion fails begin instead of being refused.** The body cannot know about
  episodes, so it accepts controls before `course_begin`. `course_begin` then fails
  with `body_changed_since_reset` because the subject fingerprint changed. The
  episode check asserts this as `motion_before_begin_cannot_score`.
- **Course control limits live on the body.** A body spawned with `limits: course`
  rejects attack, use and non-zero hotbar slots (`course_forbidden_control`).
- **Controller watchdog is split.** The body releases controls after 2 s without
  contact (as before). The lab fails a running course episode with
  `controller_watchdog` only when `course_reset` set `watchdog: true` (bridge-driven
  Carpet bodies); Baritone and human runs are not heartbeated and have no lab watchdog.
- **Sensing starts when the body is ready, not at episode start.** A streaming body
  senses and emits `policy_sample` whenever it is ready in survival, including the
  settle period at the course spawn before `course_begin`.
- **The body re-settles when moved.** Leaving survival (e.g. parked as spectator) or
  moving more than 4 blocks in one tick clears `ready` until the body settles again.
- `sensor_check` is a body op and no longer invalidates a lab reset; it restores
  the terrain it edits.

The Python client (`src/mclab/bridge.py`) keeps the single-bridge interface. It
routes each op to its mod, merges both event streams, and overlays the lab's run
state, episode, fixture view and pickup ledger on the body's `/state` and
`/policy`. Its compound ops are: `course_reset` without `player` (lab reset naming a
fresh body, then body `spawn`), `natural_start` with `subject: carpet_body`,
`despawn` (body despawn + lab `stop`) and `natural_stop` (lab `stop` + body despawn).

This interface supports C0 development. It is **not qualified for scoring** and
its privileged fixture observations must never be sent to a decision backend.
The 17×9×17 occlusion-filtered map and evaluator restrictions remain future work.

## Development native route execution (Step B)

`dev-native-route-v1` is an opt-in development control profile. Planning and edit
policy stay in Python; the body executes movement and actions at START_SERVER_TICK
using live position/velocity and fresh local sensor facts. It does not import the
lab or score itself. Legacy `/controls` timing, permission and qualification
contracts are unchanged.

New authenticated body `/admin` operations use the existing identity and monotonic
request sequence envelope:

| Operation | Fields | Acknowledgement |
| --- | --- | --- |
| `follow_route` | `route_id`, `route` (2–32 `[x,y,z]` standing nodes), full `movement_policy` | `route_accepted` |
| `follow_step` | `step_id`, `plan` (`source`, `target`, explicit `breaks`/`places`, `kind`, optional `exit`), full `terrain_policy` | `step_accepted` |
| `follow_gap` | `gap_id`, gap `plan` (`source`, `target`), `movement_policy` | `gap_accepted` |
| `cancel_route` | no extra fields; cancels route, edits, gap and reflex inputs | `route_cancelled` |

Native objectives require a ready body spawned with `extended_observation: true`
and without `limits: course`. Ordinary routes enforce adjacency, standing-height,
diagonal and drop permissions. Gap objectives separately enforce known runway,
level gap width, landing/headroom and sprint/food permission. Edits use real
hotbar/aim/mine/use inputs; no world writes, fabricated item counts or teleport
fallback exist. Body admission independently enforces edit shape, budgets,
material allowlist and protected positions/blocks.

State and extended-policy snapshots expose `route_follower`, `step_follower`,
`gap_follower` and `reflex_follower`. They carry the accepted objective ID, progress,
state and failure reason. A normal route/gap finishes with `done`; an edit finishes
with `edits_ready`, **not** a committed move. Python requires freshly observed edit
geometry plus the matching inventory debit adjusted for recorded pickups, then
hands over the next movement step. Failure becomes `needs_replan`; missing facts
never provide support or clearance. Progress/stop events stay in the body stream.

Native per-tick sensing uses the configured awareness bounds and occlusion rules,
never force-loads a chunk, and does not use lab fixture observations. A local
threat reflex can choose a checked retreat from nearby observed hostiles, avoiding
unknown terrain and deferring takeover while airborne. This is not a combat
controller or the later survival cost model.

An objective replacement releases old inputs and clears scheduled controls. Raw
`/controls` cannot be mixed into an active native route/edit/gap/reflex. Explicit
release, despawn, body loss and the existing two-second contact watchdog stop
native execution as well. Heartbeats keep the external objective owner alive;
Python/HTTP is outside the per-tick movement/action loop.

## Transport and thread boundary

The bridge binds `127.0.0.1:<bridge_port>` (default 8765). Each request requires the
private bearer token from ignored `runtime/supervisor.json`. Never copy that token
into documentation or result artifacts. The supervisor passes launch ID and
configuration through process-local JVM properties.

Transport is bounded HTTP JSON commands plus cursor-based event batches, an
equivalent bounded stream rather than a WebSocket dependency. Clients poll batches
and persist every event. The server ring retains 2048 events and marks gaps;
missing events invalidate qualification evidence. New runs synchronize their cursor
before sending the first request, so evicted events from older runs do not create
a false gap. Commands themselves are never retried.

- `GET /state`: immutable latest END_SERVER_TICK body snapshot.
- `GET /events?after=<sequence>`: ordered events, latest sequence, gap flag, launch ID.
- `POST /controls`: only raw motor controls, no administrative vocabulary.
- `POST /admin`: fixed qualification operations; no arbitrary command strings.

HTTP admission returns 202, not action success. Network handlers accept up to
16 KiB, enqueue in a 64-entry inbox, and never access a player/world. Up to eight
requests are processed on each START_SERVER_TICK. Future motor actions use a
16-entry queue. HTTP worker queues and event storage are bounded. Serialization
and recording happen off the simulation thread. The Python recorder persists
JSONL; disk/network latency never pauses game ticks.

## Request identity and controls

Each command includes `schema_version=1`, `run_id`, `episode_id`, `body_id`,
positive monotonic `sequence`, and `op`. The server checks identity before sequence
admission. A new spawn or human-reference session establishes a new identity.
Duplicate/out-of-order sequence values and repeated action IDs are rejected.

`POST /controls` requires `op=controls` and a `controls` object:

| Field | Semantics |
| --- | --- |
| action_id | Unique ID for this body session |
| observation_tick | Tick of the snapshot on which this decision is based |
| intended_apply_tick | Future server tick, at most five ticks ahead at admission |
| expires_tick | Exactly intended_apply_tick + duration |
| duration | 1–5 ticks |
| forward / strafe | [-1,1]; positive forward follows yaw, positive strafe is left |
| yaw_delta / pitch_delta | Finite degrees, each bounded to ±45 per commitment |
| jump | One jump pulse on application, not a teleport |
| sneak / sprint | Held booleans; mutually exclusive |
| mine | Continuous ATTACK for block mining; renewal preserves break progress |
| attack_pulse | Discrete entity attack; mutually exclusive with mine |
| use_hold / use_pulse | Held use or one discrete use; mutually exclusive |
| hotbar_slot | 0–8; translated to Carpet's 1–9 interface internally |

Minecraft axes: +X east, +Y up, +Z south. Yaw 0 faces south, +90 west, -90 east;
positive pitch looks down. Ordinary collision, gravity, inertia, mining and item
use apply. Input release never zeros physical velocity.

At execution the server rechecks identity, readiness, exact intended tick, expiry,
and observation age (strictly earlier than apply tick, at most five ticks old).
Only one action may target a particular apply tick. A valid replacement at the
expiry boundary renews holds atomically; otherwise expiry releases before Carpet's
action update. This avoids erasing mining progress between contiguous commitments.
A two-second monotonic heartbeat
watchdog releases held inputs. Explicit release cancels queued controls too.

The measured pinned callback order is:
`Fabric START -> Carpet action-pack HEAD -> Carpet action-pack TAIL -> Fabric END observation`.
A trace mixin records this ordering for the owned body; no cross-version ordering
is assumed. Accepted receipt, actual application tick, rejection and release are
separate events.

Carpet's fixed 4.5-block entity trace can exceed ordinary entity reach. The adapter
rechecks the target every pre-tick using the player's actual block/entity range
predicates and cancels ATTACK/USE on an illegal target. Guard events are recorded.

## Administrative qualification operations

`spawn` supports `via_command=true/false` and always requests Survival. A unique
lab-only offline identity is added to the 26.3 in-memory name resolver before
Carpet spawn, avoiding its synchronous unknown-name lookup. Human authentication
stays enabled. A true createFake/command return is only an acknowledgement.
Readiness requires registered fake-player state, a complete explicit state
overwrite, Survival assertion, and three stable grounded ticks.

`fixture`, `command_proof`, `trace`, `release`, `heartbeat`, `death_proof`, and
`despawn` are setup/qualification operations, not policy actions. The fixture has
bounded variants for collision, slab step, ledge sneaking, fall and entity reach.
Cleanup is region-scoped and refuses to overwrite a human's current fixture area.
Late asynchronous spawns remain watched after cancellation and are removed.
Death is terminal for the current body; the next test uses a fresh identity.

`reference_start` accepts a connected human player's name and 5–120 seconds;
`reference_stop` ends capture. The resulting server-tick events contain actual
`getLastClientInput()` buttons, position, yaw/pitch and grounding. Client timestamp
and network-delay uncertainty are explicitly unknown. Capture does not manipulate
the human player or infer button labels from displacement. Optional interaction
preparation restores the C1 log, spawns a tagged test cow and supplies 16 dirt in
an empty human hotbar slot. It preserves occupied slots. Passive server packet
and game-mode hooks record dig starts/aborts, successful block breaks, placement
faces/inventory changes, and attack results; these are development instrumentation,
not the future causal scoring evaluator. The real player's reach attributes are
captured separately from geometric interaction evidence.

Reference analysis reports contiguous 20-tick walk/strafe/sprint-input windows
with steady yaw and unmixed buttons, plus horizontal path distance following
movement release. These are measurements for review, not automatic fidelity
passes; collisions, other forces and unknown client timing still matter. Sprint
input does not establish authoritative sprint state. Dig aborts preserve their
start/abort ticks; subsequent mining duration starts at the next dig start.
Attack summaries include intervals between recorded, matched attacks, but damage
and timing alone cannot distinguish cooldown from target invulnerability. A
missing attack target is excluded from matched results without invalidating the
rest of the capture.

New captures also include authoritative `sprinting`, velocity, pose and per-tick
`attack_strength`. Attack packet/result pairs include `attack_strength` before
and after execution. Older recordings omit those fields; the analyzer keeps
input-only sprint windows separate from windows confirmed by authoritative state.
Recorded strength samples retain their ticks for reviewing reset and recovery.

`prepare_combat: true` alongside `prepare_interactions: true` builds a stone
backstop behind the reference cow. CLI `body reference --combat` sets both flags.
For the default origin the wall is x=1000..1004,y=80..81,z=1003; approach the cow
from +Z, preferably x=1002.5,z=1006.5, facing north. It is a persistent development
fixture, unlike the tagged cow, which is cleaned up when capture stops. Setup
requires wall cells to be air or stone and refuses an overlapping human. It
does not pin the cow or disable normal knockback/physics.

## Named G1 terrain references

`reference_start` optionally accepts `reference_mechanic` (`collision`, `step`,
`ledge`, `fall`, or default `none`). Named mechanics cannot be combined with
interaction/combat preparation. The server validates the mechanic before editing
the world and returns `setup` in `reference_started`, including layout version,
origin/spawn, edited/verified cell counts and the player's pre-setup position,
health and food. The recorder invalidates a named capture if that acknowledgement
is absent or names the wrong mechanic; an older mod cannot silently produce a
flat capture labeled as a terrain test.

Only explicit named setup relocates the human and normalizes health/food. It
preserves inventory, rejects armor/effects/nonordinary Survival, preflights loaded
chunks and refuses another human in the modified region. The geometry planner is
pure Java and bounded to 847 cells. The default footprint is x=987..993,
y=75..85,z=986..996. A solid catch floor remains below the ledge. World edits,
verification, snapshots and relocation all run on the server thread. No human
input is synthesized. The development geometry remains after recording.

`body reference-plan --mechanic <name>` describes the setup and instructions
without connecting or mutating the world. `body reference --mechanic <name>`
starts it only when the operator requests a capture. Summaries report positions,
grounded height, horizontal collisions, jump/sneak input, health and first landing,
with sample gaps flagged for review; no automatic equivalence or gate pass follows.

The existing administrative `fixture` operation also accepts `reference_collision`,
`reference_step`, `reference_ledge` and `reference_fall` for fake-body-only probes.
These use the same preparation and verification path, require an empty initial
inventory and retain setup metadata in `body.reference_setup`. The bounded
`body reference-fixture-check` suite records these under `reference_fixture_v1`.
It verifies 12 geometry/target/movement checks and confirms body teardown. It is
supplementary development evidence, not human reference evidence or G2 reset proof.

## C1 reset foundation

Administrative `course_reset` requires `profile_id: initial-c1-survival-v1` and
fresh episode/body IDs. The Python runner checks the frozen G1 profile, dependency
pins and qualified course before connecting. The server supports only the ordinary
C1 manifest and the bounded 49×41×49 reset envelope. It retires the old body and
clears queued controls before terrain work. A generation increments on each
accepted reset. Retired asynchronous spawns remain watched.

`course_reset_started`, `course_reset_complete` and `course_reset_failed` carry
the request sequence and generation. Reset failures propagate immediately to the
runner rather than being hidden behind its completion timeout. Snapshots include
`course_reset` progress. Terrain construction/verification is limited to 2,048
cells per tick. A fresh body must register, settle and warm 20 ticks before the
second terrain verification; its physical fingerprint must remain unchanged.
The completion event includes terrain/body/combined digests and the physical
snapshot. Ephemeral IDs and generation values are tracked separately from the
physical digest so consecutive attempts can compare equal.

Humans entering the arena abort a reset. Unexpected living entities, unloaded
chunks, fluids or block entities invalidate it. Arena-scoped drops/projectiles
are removed. Clock/weather normalization uses the native 26.3 clock and weather
APIs; pausing the daylight clock does not pause simulation ticks.

Policy admission remains closed until explicit episode begin. `heartbeat`, `release` and `despawn` remain
administrative lifecycle operations. Fresh reset requests can replace a prepared
body; old controls cannot survive queue clearing or the identity change.

`course reset-check --count 20` records the bounded run in JSONL and a versioned
SQLite reset-attempt table. It uses a declared 1,500-request orchestration budget,
without relaxing motor action age/duration limits. Pending/interrupted work is
not a completed reset and a new run always starts a new attempt and full reset.
Twenty matching checks establish reset-foundation evidence. The episode suite
separately verifies restrictions/evaluation and recording integration.

## C1 episodes and permitted observation

`course_begin` requires reset_ready, matching body state and no residual entities
or humans. It performs another incremental terrain scan before `course_started`
acknowledges its request sequence, start tick and fixed 600-tick deadline.
State progresses through begin_verifying, running and episode_terminal.
`course_begin_failed` propagates verification failure without waiting for timeout.

Only ordinary movement/look/jump/sneak/sprint/mining controls are admitted during
C1. Placement, entity attacks and hotbar switching are rejected and logged; the
native destroyBlock guard permits only the designated log. Arena mutations
outside that native break invalidate the episode. Development setup operations
cannot run during an episode. `course_end`, reset and despawn terminate running
work, release controls and preserve the terminal outcome.

The evaluator hooks successful native block destruction, actual addFreshEntity
creation and Inventory.add transfer inside playerTouch. Item UUID, owner, item
type/count, proximity and ordering must match. The pickup hook precedes Player.take
because native playerTouch restores a discarded stack's count before RETURN.
Generic inventory contents never establish success. `course_episode_complete`
emits success/failed/invalid, reason, ticks and evaluator-only causal milestones.
Death/disconnect, bounds/pit, tick limit and controller watchdog cannot score success.

Authenticated GET `/policy` exposes versioned `local-visibility-v1`: filtered self
state, a 17x9x17 local map and visible entities. Native eye-to-surface OUTLINE rays
with fluids included test occlusion in all directions. This is augmented geometric
visibility, not camera vision. Geometry refreshes every five ticks and retains
observed_tick; missing cells are unknown. Known geometry includes block properties,
collision boxes, replaceability and fluid identity. No manifest, target coordinates,
safe route, expected answer or evaluator drop identity enters this endpoint.
The contestant discovers a log by its visible ordinary block identity.

`policy_sample` records self state each running server tick, native MSPT, and full
visible maps when refreshed (`map_update`). Python records model-visible snapshots,
periodic map-memory full snapshots/deltas, actions and apply times, errors and
resources in JSONL and SQLite schema v1. A bounded 2304-record queue moves JSON
serialization and event database writes off the motor loop. The writer owns its
SQLite connection; identifiers, sequence and monotonic capture time are assigned
before enqueue. Critical queue overflow, disk failure or flush/shutdown failure
invalidates the attempt/run. Recording failure cannot promote a native success.
The queue holds one complete 2048-event native poll plus local/in-flight records.
The HTTP transport consumes responses fully and reuses its authenticated loopback
connection, preventing polling from exhausting Windows temporary TCP ports.
There is no automatic retry of a command whose admission may be ambiguous.
HTTP failures retain their status code; launch identity is checked per response.
The writer commits at most 128 already queued records in one transaction; flush
acknowledges that batch only after its commit. New databases use WAL. Navigation
runs have separate `results/navigation/<run>/episodes.sqlite3` ledgers; reports
aggregate them with the historical/shared `results/episodes.sqlite3` ledger.
Flush completes before an episode is finalized. OS locking prevents concurrent runner
recovery. Pending attempts after a crash become interrupted; no half-modified
episode resumes. Replay is event/map inspection, not bit-exact simulation.

The deterministic C1 execution profile is `c1-three-tick-bounded-resense-v4`:
controls target observation_tick+3, last at most five ticks and keep the existing
five-tick maximum observation age. A stale admission/execution rejection releases
controls and discards that command permanently. The next loop senses and decides
again with a fresh action ID; no timestamp is rewritten. At most three such
recoveries are permitted per episode, with every rejection recorded. Other
rejections and controller deadlines fail the attempt. The original 600-tick and
90-second wall limits continue throughout recovery.

Development-only `course_test` requires reset_ready before begin. Named kinds
planted_item and target_missing exercise rejection. sensor_check temporarily builds
bounded native occlusion fixtures, restores their cells synchronously and requires
a fresh reset before begin. It cannot be used to perform a scored task.

## Optional tandem recording

Optional tandem orchestration is a Python-side C1 experiment and does not change
the native bridge schema or body qualification. It adds `planner_request`,
`planner_response`, context acceptance/invalidation/discard events,
`planner_degraded` and `planner_backend_provenance` to the local episode recording.
Existing tactical events carry context revision IDs and sanitized per-request
exchanges. Summaries distinguish single/tandem mode, model roles, per-role caps
and request counts, and degraded outcomes. Snapshot hashes bind `tandem.py` in
addition to the shared runner, tactical motor and backend files. See
[candidate coordination contracts](HYBRID_ARCHITECTURE.md) and
[harness verification](TANDEM_HARNESS_PROOF.json). These recording additions are
not native combat or live tandem qualification.

## Supplementary automated protocol proof

`mclab body protocol-check` runs a separate, bounded development suite against
the installed mod. It checks control ranges/conflicts, schema and action IDs,
endpoint restrictions, administrative allowlisting, past/distant actions,
duplicate action IDs, pending-action cancellation and retired-session rejection.
Cancellation must show that the movement request was not rejected, never applied,
and produced less than 0.001 blocks of displacement. Each attempt preserves
JSONL events, source inputs, provenance and expected/observed checks under
`results/qualification/`. Cleanup failure invalidates the attempt.

This suite does not replace human mechanics references or advance G1. Reports
label it `supplementary_protocol_v1` separately from body mechanics attempts.
