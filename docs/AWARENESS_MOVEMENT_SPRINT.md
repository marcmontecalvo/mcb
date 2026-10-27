# Awareness, movement, and block-use sprint

Planned October 26, 2026. Status: implementation plan; no new capability or
qualification is claimed by this document. Budget: ten development days, with
an integrated checkpoint after day five. Day allocations are estimates, not a
promise of Baritone parity. This is the next development priority from Marc's
October 26 discussion; existing evidence remains in its original ledgers.

Implementation has started. The [first awareness increment](AWARENESS_MODES.md)
adds development sensor modes and controls. The [initial spatial integration](SPATIAL_NAVIGATION.md)
now adds compressed durable sections, bounded hot memory, restart recall,
background segmented search and prefetch, and a separate development motor
adapter. Development transport now has sparse section palettes; byte-budgeted
deltas and coarse routing remain pending. The [hill adapter](HILL_NAVIGATION.md) now implements
bounded cut steps and confirmed foothold placement; broader movement/block-use
stages remain pending. These increments do not
mean this sprint's acceptance criteria have passed.

## Outcome

MCB retains its server-side Carpet body and gains configurable world awareness,
durable spatial memory, bounded long-distance navigation, and terrain modification
as part of route planning. A single controller can choose to walk around a hill,
cut steps, place footholds, or combine those actions. It can also execute basic
safe drops, swimming, ladders, and gap jumps. Baritone remains the reference.

The sprint delivers working, measured behavior and operator controls. It does
not claim complete Baritone parity, learned navigation, or combat competence.

## Baseline constraints at sprint start

The following describes the pre-sprint baseline. Current increments and pending
acceptance are tracked in [SPRINT_ACCEPTANCE.json](SPRINT_ACCEPTANCE.json).

- `PolicySensor.java` samples a 17 by 9 by 17 block neighborhood, applies
  eye-to-surface occlusion, and separately samples visible entities.
- `BridgeRuntime.java` refreshes terrain every five ticks and hard-codes
  `local-visibility-v1`. Existing policy consumers and qualification admission
  explicitly validate that profile.
- `navigation.py` routes over cardinal full-cube standing positions with
  one-block ascents/descents. It combines exploration and motor behavior.
- `navigation_goal.py` trims map memory to horizontal radius 20 and vertical
  radius 12. After insufficient straight-line progress, it digs toward the goal;
  this can defeat a necessary detour.
- The body protocol already includes hotbar selection, use pulses/holds,
  mining, sprint, sneak, jump, and bounded rotations. Existing initial/navigation
  profiles deliberately exclude construction. Interface availability does not
  establish placement or traversal qualification.
- The natural-world harness supplies authoritative start/goal scoring, world
  snapshot restoration, and a Baritone comparison. Its reporting misses damage
  from a single lethal hit; fix that before new survival comparisons.

## 1. Awareness profiles and operator knobs

Introduce a versioned awareness configuration shared by the server, map store,
controller, and recorder. Supply named presets plus CLI/config overrides; show
the resolved configuration in status and every run manifest. Reject invalid or
unsupported combinations before starting a run.

| Knob | Supported behavior |
| --- | --- |
| `geometry_mode` | `visible`: occlusion-tested geometry; `chunk`: geometry through obstructions in admitted chunks |
| `horizontal_radius_blocks` | Positive explicit block radius for bounded profiles |
| `vertical_radius_blocks` | Positive explicit radius, or full dimension height in chunk mode |
| `chunk_scope` | Bounded radius, or a client-equivalent chunk envelope |
| `client_view_distance_chunks` | Explicit envelope limit; record actual available chunks and effective range |
| `terrain_update_interval_ticks` | Refresh cadence independent of control renewal; default five ticks |
| `max_scan_cells_per_tick` | Bound snapshot construction work; report partial coverage/backlog |
| `max_delta_bytes_per_update` | Bound transport work; never silently drop geometry changes |
| `entity_radius_blocks` / `entity_visibility` | Separate entity reach and occlusion settings; default existing visible-entity behavior |
| `memory_mode` | Current surroundings, session, or persistent |
| `use_historical_geometry` | Whether navigation may consult older admitted geometry beyond current reach |
| `active_map_budget_mb` | Bound hot memory without deleting persistent knowledge |
| `search_budget_ms` / `search_node_limit` | Bounded route search, separate from motor timing |

Presets:

1. **Legacy visibility:** reproduce the existing radius, occlusion, cadence, and
   legacy observation contract for regressions.
2. **Extended visibility:** configurable radius with occlusion; begin trials at
   32 horizontal / 16 vertical blocks, subject to scan-budget measurements.
3. **Chunk awareness:** configurable non-occluded geometry; begin development
   at a two-chunk radius with full height, then measure wider envelopes.
4. **Client-comparable awareness:** geometry admitted through an explicitly
   tracked client-style chunk envelope, with optional historical cache. Compare
   actual coverage with the pinned Baritone client before calling access equal.

Never substitute all server-loaded chunks for the client envelope: other players
and chunk loaders can expand server knowledge. Initial profiles do not generate
or force-load remote chunks. Missing/unavailable geometry remains unknown.

Mode/range changes release controls, invalidate pending observations/plans, and
start a new awareness revision. Changes during an experiment start a new run.
Tag facts with acquisition mode, coverage, and revision. Restricted profiles
cannot read facts learned only under broader access. Clear or partition derived
routes and place summaries too. Shrinking reach with historical memory enabled
retains permitted old knowledge explicitly; disable historical access for a
strict current-range limit. Switching to strict mode removes broader facts from
all policy-visible caches before resuming. Entity settings remain independent.

Acceptance: fixtures prove walls hide/reveal geometry according to mode, range
boundaries and vertical limits hold, unavailable chunks remain unknown, and a
broad-to-restricted switch cannot leak geometry or routes. Legacy sensing keeps
its original behavior. Status reports the effective limits, not just requests.

## 2. Persistent spatial memory

Separate acquisition, durable storage, active working map, and search. Use
compact chunk sections/block palettes rather than repeatedly transmitting large
lists of per-cell JSON objects. Capture bounded immutable data on the server
thread; serialize, persist, and search off-thread. Do not access mutable Minecraft
world objects from background workers.

Use SQLite-backed chunk-section records and a bounded in-memory working set.
Key records by world/save identity, reset generation, dimension, chunk/section,
and awareness provenance. A restored snapshot creates a new world generation;
memory is isolated unless an explicit transfer-memory experiment allows reuse.
Do not key worlds by seed alone.

Track known/unknown cells, block state, collision/affordance data, observation
tick and persistent observation time/session, geometry revision, and pending
modifications. Session tick counters alone cannot establish age after restart.
Snapshots declare their covered region; deltas include additions, changes, and
removals. Gaps invalidate coverage until resynchronization. An absent delta is
not air. Our own successful interactions immediately invalidate affected routes
and request fresh geometry; an issued interaction is not a confirmed edit.

Persist asynchronously with bounded queues and explicit flush/checkpoint status.
Overflow marks coverage stale and requests resynchronization; it cannot silently
produce a trusted old map. Revalidate remembered geometry immediately before
movement or interaction. Keep disk persistence out of the action-renewal loop.

Add a small places/connections schema: place ID/name, world/dimension/location,
discovery provenance, last verification, connection type and preconditions.
Represent a future Waystone link as a teleport edge with activation/access/cost
requirements. This sprint tests storage and graph representation only; it does
not install Waystones or execute teleports. Embeddings remain optional future
semantic retrieval, outside geometric storage and path search.

Acceptance: remember a discovered detour after leaving the active-map window;
restore it after process restart; invalidate a changed block; isolate worlds,
dimensions, resets, and awareness profiles; preserve unknown coverage after an
interrupted update. Demonstrate bounded active memory on a growing map.

## 3. Shared navigation and movement

Expose one navigation contract: start/replace goal, status/progress, cancel,
interrupt, resume, and failure reason. Planner/tactical layers use this contract
without depending on a particular motor. No new model integration is required
for this sprint; exercise interruption with deterministic scripted events.

Split route planning from execution. Search immutable map revisions with
goal-directed bounded A*, reuse route segments, and precompute a next segment
while executing the current one. Maintain a coarse region/connection graph for
long-distance routing. A search timeout returns a validated partial route or an
explicit incomplete result, never permission to cross unknown space.

Progress means route advancement, useful exploration, or completed planned
terrain work. Moving farther from the destination is allowed. Detect repeated
states/failed edges and change the route; remove unconditional goal-directed
digging as the stuck response. Revalidate near-term support, clearance, hazards,
inventory, and geometry revisions before each bounded control commitment.

Movement scope:

| Capability | Sprint acceptance boundary |
| --- | --- |
| Walk, strafe, turn, sprint | Continuous route following; safe diagonals; no corner cutting |
| Steps and slopes | One-block ascents/descents, collision-shape-aware slabs/stairs and headroom |
| Falls | Configurable maximum solid-ground drop; landing/support/hazard checks; default no-damage policy |
| Fluids | Basic surface swimming, entry/exit and recovery in water; air/suffocation telemetry; reject lava routes |
| Ladders | Approach, ascend, descend, dismount from ordinary supported ladders |
| Parkour | Ordinary one- and two-block horizontal gaps with verified takeoff, clearance and landing; configurable disable |
| Recovery | Obstruction, stale geometry, knockback/displacement, interrupted interaction, cancellation and safe resume |

Expose enable flags for sprint, gap jumps, ladders, and swimming; maximum drop
and jump gap; and time/risk budgets. A disabled action cannot appear as a route
edge or execute through a recovery fallback. When no permitted route exists,
report why. Keep existing action-age, duration, expiry, and watchdog guarantees.
If timing changes are needed, use a separately measured profile rather than
relaxing the legacy one.

Acceptance: each movement family passes ten generated development cases before
mixed-route testing. Include unsafe alternatives, changed landing geometry,
blocked dismounts, and settings that deliberately prohibit the action. Track
capability-specific failures; do not average an unimplemented family into a pass.

## 4. Terrain modification, with hill climbing as the main task

October 27 clarification: keep a rolling plan for the next three or four moves,
including future cuts, footholds and their resources. A shorter plan is sufficient
when the goal is closer. Revalidate each move against actual terrain; unexpected
changes trigger replanning. Do not substitute isolated single-step edits for this
lookahead requirement.

Make walking, breaking, placing, and combined sequences compete in the route
cost model. Include estimated execution time with the actual tool, durability,
material consumption, risk, and resource reserves. Use bounded modification
macros with explicit preconditions and predicted effects; do not enumerate an
unbounded tree of hypothetical world edits. Confirm effects before using them.

Required actions:

- Select the appropriate available hotbar tool or permitted building material.
- Break an obstruction or headroom block to create a safe ascending step.
- Place a foothold, build a short staircase, and pillar when an exit is known.
- Sneak-place from a supported edge and construct a short bridge.
- Combine excavation and placement to ascend an irregular hill.
- Prefer a cheaper safe detour when modification is unnecessary or resources
  are reserved. When insufficient supplies prevent a route, stop/replan honestly.

Expose `allow_break`, `allow_place`, per-plan break/place budgets, material
allowlist, protected-block denylist, reserve count, tool-wear penalty, placement
penalty, and risk budget. Initial supply selection is hotbar-based; arbitrary
inventory rearrangement/crafting is outside this sprint.

Use normal Survival interactions. Verify reach, target/face, replaceability,
support, player collision, and inventory count. Observe the actual block change
and inventory/tool result. Guard against removing support under the player,
falling sand/gravel, and exposing fluids; require enough knowledge for the safety
check. Cancellation releases controls and invalidates the unfinished macro.
Retries have a cap and an explicit failure reason.

Acceptance: ten generated cases each for cut steps, placed steps, mixed hill
ascent, and short bridging/pillaring. Include low headroom, bad placement faces,
wrong/exhausted material, failed interactions, interruption, protected terrain,
and a safe detour that should win. Assert authoritative block changes and stock
consumption as well as arrival. Success with a forbidden edit is a failure.

## Delivery order and checkpoints

| Estimate | Deliverable | Checkpoint |
| --- | --- | --- |
| Days 1-2 | Versioned awareness controls, bounded chunk acquisition, status/manifests, initial memory store | Sensor boundaries and profile isolation; same-controller awareness comparison |
| Days 3-4 | Persistent memory integration, segmented search, shared navigation/execution boundary | Restart/route recall, long detours, bounded planning without stale-control failures |
| Day 5 | Basic cut/place hill ascent end to end, inventory and geometry verification | Demonstrate walking, cutting, and placing choices on different hills; record unresolved limits |
| Days 6-7 | Mixed hill work, staircases, bridging/pillaring, recovery and resource policies | Complete terrain-modification cases and policy knob checks |
| Days 8-9 | Drops, collision-shape traversal, water, ladders, basic gap jumps, interruption | Capability cases and mixed routes; no family silently omitted |
| Day 10 | Freeze candidate, run fresh terrain comparisons, regressions, publish evidence | Acceptance report with denominators, failures, costs, and recommendation |

These are dependency stages, not independent silos. Build a minimal complete
acquire/store/plan/execute path first. At day five, if the integration is behind,
record the unfinished work and revise the estimate explicitly. Do not call the
sprint complete by quietly narrowing movement scope or loosening acceptance.

## Evaluation and definition of done

1. **Awareness contribution:** on the five existing calm routes, compare the old
   movement policy under legacy, extended-visible, and chunk awareness. Only add
   the profile/map adapter required to accept the data; hold movement, control
   timing, and session-memory policy fixed. Report effective map coverage and
   controller adapter changes. Treat this as an acquisition comparison; measure
   persistence separately through restart/recall cases. Old routes are development
   evidence, not fresh holdouts. Failure to process a wider map is also a result.
2. **Feature checks:** all knob boundaries, memory isolation, persistence, movement
   families, terrain macros, and interruption cases above pass. Use focused unit
   tests for contracts/algorithms and authoritative live fixtures for mechanics.
   Human-reference-dependent capabilities stay explicitly unqualified until their
   references are obtained; developer fixtures can progress independently.
3. **Fresh natural terrain:** freeze ten routes across at least three new world
   seeds before running the candidate. Run three calm attempts per route for
   MCB and Baritone, restoring identical world snapshots and controlling kits,
   settings, cache state, and awareness envelope as closely as the integrations
   allow. Record any remaining information/timing differences. Target MCB at
   least 27/30 completions and median per-route time ratio no greater than 2x
   Baritone on paired successful attempts. Publish completion counts and timeout
   outcomes alongside speed so excluding failures cannot imply parity.
4. **Night diagnostic:** three selected routes, three attempts per entrant, nine
   each. Record damage, deaths, exposure time, and observed threats. This evaluates
   the movement system under disturbance; combat and deliberate mob avoidance
   are not sprint acceptance claims. Preserve random-spawn limitations.
5. **Performance:** record snapshot/search p50/p95/p99, server MSPT/TPS, transport
   volume, scan backlog, active memory, and persistence queue. Initial target:
   added acquisition work p95 at most 5 ms/server tick and motor decision work
   p95 at most 20 ms, with no invalid attempts from planning/storage starving
   renewals in the acceptance cohort. Report startup acquisition/warm-up costs
   separately and equally account for each entrant's preparation. If a wider
   profile exceeds its budget, throttle transparently or reject that profile;
   do not claim the requested radius is fully known while scanning is partial.
6. **Regressions:** build both affected mod projects as applicable; run Python
   tests, Ruff checks/format verification, and profile/sensor adversarial tests.
   Server-mod changes require the existing source-bound reset/admission checks
   before claiming new scored C1/C2-C5 results. Keep new construction/traversal
   qualification separate from the frozen legacy profiles. Retain lab-world
   restoration and reset verification after natural-world runs.

Publish `docs/AWARENESS_MOVEMENT_RESULTS.md` with resolved profiles, code/config
hashes, raw-result locations, per-capability denominators, failures and invalid
attempts, awareness comparison, terrain comparison, performance, and remaining
qualification limits. Completion requires the capability checks and natural
terrain target; missed criteria produce an incomplete sprint report and an
explicit next decision, not a success label.

## Implementation map

- Java acquisition and telemetry: `PolicySensor.java`, `BridgeRuntime.java`,
  `CarpetBodyAdapter.java`, `SensorVerification.java`, and small new dedicated
  awareness/snapshot classes. Extend `NaturalRun.java` for accurate lethal damage
  and any required action auditing.
- Python configuration and memory: new focused awareness/map-storage modules;
  integrate with policy recording and run provenance. Add new profiles instead
  of changing the meaning of `local-visibility-v1`.
- Navigation: split map/search/traversal/macros/execution responsibilities out
  of `navigation.py` and `navigation_goal.py`; adapt drivers without breaking
  frozen legacy controllers. Keep search results bound to goal/map revisions.
- Experiments: extend `natural.py`, CLI/config resolution, and result summaries;
  add separate development fixtures without renumbering existing C6-C8 contracts
  from the technical handoff. Add tests beside existing sensor/navigation/natural
  coverage and update current-operation documentation.

Existing benchmark snapshots and issued qualification evidence remain intact.
The original technical handoff is unchanged. No paid inference, shared model
reconfiguration, deployment, or Baritone replacement is necessary for this sprint.

## Outside this sprint

Live Waystone integration and dimension travel; embedding-based semantic search;
combat and a trained threat policy; learned motor training; crafting/general
inventory management; advanced parkour, jumping with midair placement, vines,
underwater cave navigation, lava travel, and emergency bucket saves. These are
later extensions, not implied by basic capability acceptance.
