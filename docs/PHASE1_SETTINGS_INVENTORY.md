# Phase 1 settings and input inventory

Status: current-code inventory, November 15, 2026. This describes what the Python
launch contract and native bridge currently accept; it is not a promise that
every field is exposed by a UI or consumed by every test. Primary references:
[`run_config.py`](../src/mclab/run_config.py), the policy dataclasses under
`src/mclab/`, [`BRIDGE_PROTOCOL.md`](BRIDGE_PROTOCOL.md), and
[`BODY_ACTION_OPS.md`](BODY_ACTION_OPS.md).

Run artifacts pair the sanitized resolved configuration with an immutable
provenance record before world or body mutation. Provenance hashes tracked,
untracked, and deleted relevant source/config inputs, selected preset/run-config/
build/checkpoint/profile files, and installed mod jars independently. It retains
natural route data inline, records selected file references with hashes, and
reports effective checkpoint/build origin. Sensitive backend files and
credential-like input paths are excluded; prompt text remains in runnable config
while hashes and readable prompt IDs appear in provenance.

## Shared resolved configuration

`resolve_config(test, preset, base, overrides)` starts with defaults, merges a
named `config/presets/<name>.json`, then a base `--run-config` object, then
explicit CLI overrides. Omitted CLI arguments are suppressed, so parser defaults
do not overwrite presets. It rejects unknown keys, wrong types, invalid policy
values, unsupported test-specific options and invalid combinations before the
runner opens the arena. `--print-config` resolves and prints without launching.
The current schema version is `1`; the top-level sections are `schema_version`,
`run`, `body.awareness`, `planner.terrain`, `planner.movement`,
`planner.memory`, and `model`.

The actual common `run` fields and defaults are:

| Field | Default | Current use |
| --- | --- | --- |
| `test` | selected runner | `gather` or `build`; must match the runner |
| `start` | `null` | 3 integer coordinates; required except build resume |
| `seed` | `null` | signed 64-bit natural-world seed |
| `build` | `tests/fixtures/schematics/small-house.build.json` | Build template input |
| `resume` | `null` | Build checkpoint path; sets up the recorded seed/world and builds only |
| `dx`, `dz` | `3`, `2` | Build origin offset when not resuming |
| `rotation` | `0` | Build rotation: 0, 90, 180, or 270 degrees |
| `margin` | `1` | Prepared level ground around build footprint; nonnegative blocks |
| `inspect`, `preview` | `false`, `false` | Build-only diagnostics / hold-for-inspection mode |
| `preview_offset` | `null` | Optional 3-coordinate diagnostic copy offset; requires preview |
| `empty`, `supply` | `false`, `false` | Build material sourcing modes; mutually exclusive |
| `coal` | `item` | Build supply form: `item`, `block`, or `ore`; non-item requires supply |
| `stage` | `false` | Build resume staging; requires resume |
| `scaffolding` | `null` | Optional nonnegative build kit count |
| `iron`, `raw_iron` | `false`, `null` | Gather-only goal selectors; raw iron count must be positive |
| `gather` | `GatherOptions` defaults | Gather and build/refill settings below |
| `course`, `entrant`, `seeds`, `routes`, `route_ids`, `route_data` | `null` | Natural-family course, route-file reference, selected route IDs, or resolved inline route objects |
| `condition`, `repetitions`, `entrants`, `conditions` | `calm`, `1`, `null`, `null` | Natural-family environment and repeated entrant/condition matrix |
| `cost_profile`, `baritone_parkour`, `count`, `checks`, `output`, `timeout`, `check`, `operation`, `arguments` | family-specific | Natural, fixed-check, and other runner-specific identity/options |
| `origin` | `null` | Effective world/build origin when resolved; build checkpoints may provide it |

The `model` object has explicit backend/model, request limit, deadline, prompt
identity, and planner backend/model/profile fields. `planner_profile` carries
the public instructions, candidate descriptions, interval, TTL, and failure
mode used by the planner. Provenance keeps these runnable settings in
`run_config.json` and separately records prompt identities and content hashes.
Tactical built-in instructions and candidates are identified by `PROFILE` and
content hashes, with selection status reported separately. The separate
experiment backend configuration is `config/backends.json`; it contains
provider references and is not copied into run artifacts. Credentials are
looked up by environment-variable name in `HttpBackend`; credential values are
not part of run identity.

## Policy fields and actual consumers

Defaults below are the dataclass defaults, before profiles/presets and runner
overrides. Config validation uses the same Python dataclass validators. A new
field is not effective merely because it appears in JSON: the call site must
pass it to the operation that consumes it.

### MovementPolicy — `planner.movement`

| Field | Default / accepted values | Actual use |
| --- | --- | --- |
| `allow_diagonals` | `false` | Python route search and native route motor where included in full movement payload |
| `allow_sprint` | `false` | Gap and route sprint permission |
| `max_drop` | `1`, integer 0–3 blocks | Route search/motor grounded-drop bound |
| `allow_shapes` | `false` | Collision-shape movement permission |
| `max_gap` | `0`, integer 0–2 blocks | Gap planner and `follow_gap` |
| `allow_swim` | `false` | Swimming edges and body execution; planner swimming also needs terrain `planner_swim` |
| `allow_climb` | `false` | Ladder/climb movement. Omitted in the legacy payload when false to keep old requests byte-identical |

Validation requires booleans for permissions and exact integers for the two
bounds. The fields are launch/profile values, but their effect is objective
local: a route receives a policy at `follow_route`; a gap receives one at
`follow_gap`. Updating a config file does not alter an already accepted route.

### TerrainPolicy — `planner.terrain`

| Fields | Defaults | Constraints / consumer |
| --- | --- | --- |
| `allow_break`, `allow_place` | `true`, `true` | Booleans; gate edit planning/execution |
| `break_budget`, `place_budget`, `reserve_blocks` | `8`, `8`, `0` | Integers 0–64; per-step edits and reserved hotbar building supply |
| `interaction_tick_limit` | `160` | Integer 20–400 ticks; step execution deadline |
| `material_allowlist` | `minecraft:cobblestone`, `minecraft:dirt` | List of namespaced block IDs; only those two are presently supported |
| `protected_blocks`, `protected_positions` | empty tuples | Namespaced IDs and integer `[x,y,z]` locations; block/position edit guard |
| `break_penalty`, `placement_penalty`, `tool_wear_penalty` | `3.0`, `4.0`, `0.0` | Finite 0–1000 planner cost terms |
| `allow_bridge`, `allow_pillar` | `false`, `false` | Booleans; placement plans |
| `max_support_stack`, `max_pillar_height` | `1`, `3` | Integers 1–3 blocks |
| `planner_tick_costs`, `planner_unknown`, `planner_edits` | all `false` | Independent Step C planner ablations |
| `unknown_penalty` | `2.0` | Numeric 1–10; unknown terrain planning penalty |
| `baritone_search`, `baritone_diagonals`, `baritone_moves` | all `false` | Search/diagonal/move-model switches; do not independently enable motor permissions |
| `planner_swim` | `false` | Planner water-edge pricing; requires `MovementPolicy.allow_swim` for movement |
| `heuristic_weight` | `0.0` | 0 (legacy) or 1–10 |
| `mob_coefficient` | `1.0` | Numeric 0.1–10; visible-hostile cost multiplier |
| `mob_radius` | `8` | Integer 1–32 blocks |
| `hazard_penalty` | `0.0` | Finite 0–1000 ticks for adjacent hazard |
| `allow_falling_neighbours` | `true` | Boolean; falling-block cut admission; effective only with `baritone_moves` |
| `use_doors`, `climb_vines` | `false`, `true` | Local route permissions; door/vine behavior is sent on each native route/gap operation |

All listed permission switches are strict booleans. `follow_step` carries a full
`terrain_policy`; `follow_route` and `follow_gap` carry full `movement_policy`
plus route-local `use_doors` and `climb_vines`. `route_policy` latches only the
raw-control permissions when no native objective is active. It does not replace
the policy captured by a running route. Java-side edit shape, budgets, material
allowlist and protected-region checks remain authoritative and must be supported
and consumed on both sides before a new UI control is considered live.

### MemoryPolicy — `planner.memory`

| Field | Default / accepted values | Current use |
| --- | --- | --- |
| `memory_mode` | `persistent`; `current`, `session`, `persistent` | Python memory retention mode |
| `use_historical_geometry` | `true` | Whether admitted historical geometry can enter a planning snapshot |
| `active_map_budget_mb` | `128`, number `>0` through 4096 | Active in-memory map cap |
| `persistence_queue_budget_mb` | `128`, number `>0` through 4096 | Persistence queue cap |

All four fields validate in `MemoryPolicy` and resolve through
`run_config.policies`. Companion startup passes the policy into `SpatialMemory`;
`current` limits planning to live-session geometry, `session` retains a run-local
archive, and `persistent` enables the world archive and optional historical
geometry. Restored fresh-world sessions remain isolated from prior world
geometry. Active-map and persistence-queue budgets are enforced in Python; these
fields are not Java movement payloads.

### Awareness — `body.awareness`

| Field | Default | Accepted values / notes |
| --- | --- | --- |
| `preset` | `legacy` | `legacy`, `visible`, `chunk`, `sections`; companion resolver currently permits only `sections` |
| `horizontal_radius_blocks` | `8` | Integer 1–64; section-mode preset resolves to 64 |
| `vertical_radius_blocks` | `4` | Integer -1–64 excluding 0; -1 supported only by chunk/sections |
| `entity_radius_blocks`, `entity_vertical_radius_blocks` | `8`, `4` | Integer 1–64 each |
| `entity_visibility` | `visible` | `visible` or `unoccluded` |
| `update_interval_ticks` | `5` | Integer 1–20 ticks |
| `extra_scan_cells_per_update` | `2048` | Integer 1–8192 except sections allows 65536; sections are 4096 cells |
| `extra_scan_budget_ms` | `3` | Integer 1–10 ms |
| `schema_version` | `1` | Fixed at 1 |

Preset resolution: `visible` sets horizontal/vertical 32/16; `chunk` sets 32/-1;
`sections` sets 64/48, 32768 cells and 10 ms. `legacy` is frozen to its exact
default object. Awareness is sent at body spawn and affects sensing bounds and
refresh; a live body needs a new spawn to adopt a different awareness profile.
It is separate from Minecraft server view distance. The current bridge has no
operator setting in these dataclasses that changes the server's view distance.

### GatherOptions — `run.gather`

| Field | Default / accepted values | Consumer |
| --- | --- | --- |
| `tool_choice` | `lowest_sufficient`; `best` | Gather tool selection |
| `ore_collect` | `greedy`; `specific` | Whether to gather other harvestable ores or only plan-required ores |
| `ore_vision` | `true` | Whether visible buried ore is eligible |
| `vision_radius` | `32`, integer 1–64 | Ore scan radius; effective radius is capped by sensor radius 64 and `search_radius` |
| `search_radius` | `null` | Optional positive radius; null means all sensed |
| `strip_leg`, `strip_turn` | `64`, `16` | Positive blocks along strip leg and connector |
| `strip_max_legs` | `8` | Positive count; `null` is unlimited (CLI `--strip-max-legs 0` means null) |
| `spare_pickaxes`, `carry_logs` | `3`, `16` | Nonnegative kit quantities |

`GatherOptions` itself is a frozen dataclass without a `__post_init__`; the shared
resolver validates enum spellings, radius and positive/nonnegative numeric
constraints. The options flow through initial gathering, build material planning,
refills, withdrawals, and execution via gather planner/runner calls. They are not
native body bridge fields.

## CLI, config and bridge field mapping

Gather and build launch scripts use `run_config_cli.parse_run`. Shared flags are
`--run-config PATH`, `--preset NAME`, and `--print-config`. Gather flags are
`--start X Y Z`, `--iron`, `--seed`, `--no-climb`, `--tool-choice`,
`--ore-collect`, `--no-ore-vision`, `--vision-radius`, `--search-radius`,
`--strip-leg`, `--strip-turn`, `--strip-max-legs`, `--spare-pickaxes`,
`--carry-logs`, and `--raw-iron`. Build flags are `--start`, `--resume`,
`--build`, `--dx`, `--dz`, `--rotation`, `--margin`, `--inspect`, `--preview`,
`--preview-offset`, `--empty`, `--supply`, `--coal`, `--stage`,
`--scaffolding`, and `--seed`. Each launch updates effective start/seed/build data
before the provenance call; build resume also restores start/seed/rotation/template
from its checkpoint before it records the run.

`Bridge` keeps one Python client but splits operations to the body and lab mods.
The body provides `/admin`, `/controls`, `/state`, `/policy`, `/events`; the lab
provides `/admin`, `/state`, `/events`. The Python client merges their events and
state. Requests use `{schema_version, run_id, episode_id, body_id, sequence, op}`;
the HTTP response is admission (202), and the matching event is action completion
or failure. Never consume `/policy` merely to wait; served terrain is marked
delivered. Wait with `/state`.

Operation/config boundaries in the current protocol:

| Operation | Fields relevant to settings | What actually consumes them |
| --- | --- | --- |
| body `spawn` | awareness, terrain encoding, observation/limits and start details | Spawn-time sensor profile and body permissions |
| `follow_route` | route nodes, full movement policy, `use_doors`, `climb_vines`; optional `center_only`, `arrival_radius` | Captured route movement and local door/vine actions |
| `follow_gap` | source/target plan, movement policy, `use_doors`, `climb_vines` | Captured gap jump and door/vine behavior |
| `follow_step` | source/target/break/place/kind/exit plan, full terrain policy | Confirmed, bounded edit step |
| `route_policy` | `use_doors`, `climb_vines` | Raw controls only when no native objective is active |
| planner map/plan | versioned staged map and map generation, goal/position mode, policy | Python sends admitted map; Java plans against committed generation |
| lab `course_reset`, `course_begin`, `course_test` | player/profile/spec/watchdog or case kind | Lab reset and evaluator; course test capabilities remain test-specific |

The companion ops in `BODY_ACTION_OPS.md` are real body actions: `craft`
(`recipe`, `times`, optional `table`), `furnace` (`pos`, `action`, load input/fuel
and counts), `equip` (`item`), `place_block` (`pos`, `state`, optional `item`),
`use_block` (`pos`, optional `item`), `break_block` (`pos`, optional `tool`),
`attack_entity` (`entity`, optional `weapon`), `use_entity` (`entity`, optional
`item`), `eat` (optional `item`), and `container` (`pos`, `action`, optional item
counts). `dump_knowledge` accepts `dir`. These run through lab-admin envelopes
and return `_done`/`_failed` events with request sequence; they do not override
the movement/terrain dataclasses. The lab's `kit` is development setup, not a
bot ability.

## Model prompt inventory and open gaps

CodeGraph exploration for model backend/request paths was supplemented by a
source search for `prompt`, `ChoiceRequest`, and `instructions`. Current
construction is in `src/mclab/tactical.py`: the literal `INSTRUCTIONS` constant
is passed as the `ChoiceRequest.instructions` value with fixed `CANDIDATES`;
provenance records the tactical `PROFILE` plus hashes of both constants and
reports whether the resolved run selected that profile.
`src/mclab/backends.py` defines the fallback instruction string and serializes it
into either a SystemOne question or the JSON chat message; it accepts the
observation, candidate IDs/descriptions, and instructions. Optional planning in
`src/mclab/tandem.py` takes `planner_instructions` and candidates from its caller
and wraps tactical context without a separate prompt file. Screening freezes the
tactical instruction/candidate values into its fixture and checks them against
the current constants. No versioned prompt files exist yet; tactical profile ID
and hashes provide source-bound identity. Planner instructions/candidates are
inline resolved configuration and have explicit prompt identifiers when set.
Provenance labels the built-in tactical profile as available even when the
selected prompt cannot be established; `selection_status` carries that
distinction.

Known Phase 1 contract gaps from current code:

- Backend configs and credentials remain separate. Do not copy their raw
  contents into run artifacts.
- Awareness is fixed to the `sections` preset for companion runs; capability
  metadata should disable other awareness presets there.
- Native operation-local fields (`use_doors`, `climb_vines`, action choices) need
  to remain bound to their specific operation. Raw-control `route_policy` does
  not mutate accepted route policy.
- Server view distance has no operator path in this contract and must not be
  presented as awareness radius.
- A run provenance record binds current source/config inputs and hashes installed
  mod jars. Current source identity and installed artifact identity are separate;
  matching source-build claims require the installed `runtime/mod-build.json`
  hashes to match the actual jar bytes and its source hash to match the exact
  build routine's current source digest.
