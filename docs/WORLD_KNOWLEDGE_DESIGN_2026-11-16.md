# World knowledge for the companion — design proposal, Nov 16, 2026

Status: proposal. Builds on [PROJECT_AND_SURVIVAL_PLAN_2026-11-16.md](PROJECT_AND_SURVIVAL_PLAN_2026-11-16.md) (B4).

## The problem
The planner sees every block but not what they mean. In run 20261116T234547601261Z it searched 10,000
nodes beside the house it was building because nothing told it "this is our house, that is its door".
What the body learns today lives in short caches that forget: `_failed_stance_probes` (10 s),
`_dead_ends` (one map revision), `GatherState.containers` (one job), `PlaceGraph` (tables, chests,
furnaces, never checked again).

## The idea: the same loop as an assistant's memory, done by rules
| Assistant memory | Companion equivalent |
|---|---|
| Capture: what the user says is saved automatically | Capture: rules on events the body already emits save facts |
| Recall: relevant memories put into context | Recall: `knowledge.context(task, position)` hands each decision the facts that apply |
| Curation: merge, supersede, expire | Curation: the sensor is the ground truth; a fact is re-checked when its cells are seen again |

### Fact
One table `facts` in the per-world archive (beside `places`, `containers`, `sightings`):
`kind, subject (unique key), region (box), value (json), source (event + run id), seen_ns, check`.
`check` names the rule that verifies it against terrain (e.g. "door at P is a door").

### Kinds (first set)
- `structure`: a finished or in-progress build: footprint box, template id, protected cells, phase.
- `entrance`: a doorway of a structure: the door cell, the outside and inside stance cells.
- `container`: chest contents (moves the B4 containers table under the same rules).
- `resource`: sightings (already written by the gather scan).
- `hazard`: a plugged leak, lava, a drop: cells to avoid or re-check.
- `route_note`: "goal G is not reachable from region R" keyed by map revision of the cells involved
  (replaces the 10 s probe cache and `_dead_ends`).
- later B5: `shelter`, `bed`, `death`.

### Capture rules (deterministic, one function per event)
- build started -> `structure` (footprint, template, phase=build); build done -> phase=done.
- `place_block_done` of a door/gate inside a structure -> `entrance` with its inside/outside cells.
- chest peek/deposit/withdraw -> `container`.
- leak plugged -> `hazard`.
- plan unreachable / dead end -> `route_note`.

### Curation rules
- Same subject: newest wins (supersede, keep the source of both in the event log).
- Terrain ingest for a section re-runs `check` for facts in it: a door that is no longer a door
  drops the `entrance`; an empty chest updates its `container`; a `route_note` expires when any
  of its cells change.
- Nothing expires by age alone except `resource` sightings far from any project.

### Recall into the planner
- `plan_route` gains optional `hints`: entrances of structures whose footprint holds the start or goal.
  The planner tries start -> entrance outside cell -> inside cell -> goal first (a few hundred nodes);
  only if an entrance is gone or blocked does it fall back to the full search, where break-and-restore
  applies (break through the wall, put the block back, then fix what blocked the door).
- Stance choice reads `route_note`s instead of its own caches.
- Jobs read `structure` phase, `container`s and `resource`s (B4 steps 5-7).

## Build order (one commit and offline test each)
1. `facts` table + `Knowledge` (put/get/near/supersede) in `place_graph.py`'s archive.
2. `structure` + `entrance` capture from the build runner; `check` on terrain ingest.
3. `hints` in `plan_route` (Java: entrance waypoint pass before the full search). Test: the roofed
   doorway house from run 20261116T234547601261Z, goal inside, door open: < 100 nodes.
4. `route_note` replaces `_failed_stance_probes` / `_dead_ends`.
5. `container`/`resource` move under the same rules; then B4 steps 5-7 (project resume).
