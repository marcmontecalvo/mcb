# Qualification bridge v1

This interface supports C0 development. It is **not qualified for scoring** and
its privileged fixture observations must never be sent to a decision backend.
The 17×9×17 occlusion-filtered map and evaluator restrictions remain future work.

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
