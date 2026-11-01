# Body action ops (companion track, version 1)

Admin ops on `mcb-body` (POST `/admin`, the usual envelope: `schema_version`,
`run_id`, `episode_id`, `body_id`, `sequence`, `op`). Each emits one event
`<op>_done` or `<op>_failed` carrying `request_sequence`. `_failed` has a
`reason`, and both carry the body's `inventory` (`[{slot, item, count}]`).
Plan: [BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md).

All actions are ones a player could do: real reach checks
(`isWithinBlockInteractionRange`), a line of sight from the eye, items only from
the inventory. Python moves the body to a stance first (route planner with
`interact_reach`); these ops never walk.

## Built (wave 1)

| Op | Request fields | `_done` data |
|---|---|---|
| `dump_knowledge` | `dir` | `pack_id`, `path`, `counts` (event `knowledge_dumped` / `knowledge_failed`) |
| `craft` | `recipe`, `times` (1–256), `table` `[x,y,z]` if not 2×2 | `recipe`, `crafted`, `crafted_items`, `stopped` |
| `furnace` | `pos`, `action` `load` (`input`, `count`, `fuel`, `fuel_count`) or `collect` | `input_moved`/`fuel_moved` or `collected`/`collected_item`, `furnace{input,fuel,output,lit,cook_progress}` |
| `equip` | `item` | `item`, `hotbar_slot` |

## Wave 2 (built and live-checked: `scripts/check-placement.py`, `scripts/check-crafting.py`)

Common refusals of all three ops: `body_not_ready`, `route_active` (a route, step, gap or reflex
follower is running), `busy` (a `break_block` is in progress), `invalid_pos`.
Every `_failed` carries `request_sequence`, `reason` and `inventory`.

### `place_block`
Request: `{"pos":[x,y,z], "state":"minecraft:oak_stairs[facing=north,half=bottom,...]", "item":"minecraft:oak_stairs"}`.
`item` defaults to the block's own item (`wall_torch` -> `torch`).

The body stays where it is. It looks for a way to place `item` so that the
placed state matches `state`, ignoring the properties the game recomputes or the
builder ignores: stairs `shape`, connection booleans, `waterlogged`, `powered`,
`power`, `open` (a door or trapdoor is never placed open), and leaves `distance`/`persistent`.
Properties the target omits are free. For each solid neighbour face
of `pos` that is visible from the eye and within reach, and each hit point on it
(face centre, then eight quarter points: up/down/left/right and corners; the
grid picks slab/stair half and door hinge), and the body's yaw snapped to the
needed horizontal facing (pitch aimed at the hit point), it asks the block item's own placement (`BlockItem.getPlacementState`, which calls
`getStateForPlacement` and the survive/obstruction checks; wall torches need this) with a
`BlockPlaceContext` built from that hit. It uses the
first candidate that gives the target state: it selects the item (same as
`equip`), sets the rotation, and performs a normal use on that face. Then it
reads the world.

`_done`: `{"pos", "placed": "<state actually in the world>", "face", "hit":[x,y,z], "yaw", "pitch"}`.
`_failed` reasons:
- `out_of_reach`
- `no_support_face`
- `state_unreachable` (no face, hit or yaw gives the state from this stance)
- `missing_item`
- `occupied` (target not replaceable)
- `placed_wrong` (the world differs after the use; includes `placed`)
- `invalid_state`, `unknown_item`, `not_placeable` (the item is not a block item)

Only faces the eye can see count: the far side of a wall or pillar is `state_unreachable`
from this stance. Clicking an interactive neighbour (door, table) would use it; not handled.

Doors, beds and tall plants: request the lower half or foot state; the game
places the other half.

### `use_block`
Request: `{"pos":[x,y,z]}` plus optional `"item"` to hold. A normal use on the
block's nearest visible face: opens or closes doors, trapdoors and gates, strips
a log when holding an axe, makes a path with a shovel or farmland with a hoe.
`_done`: `{"pos", "before", "after"}` (states). `_failed` also: `nothing_to_use`, `out_of_reach`,
`no_line_of_sight`, `no_effect` (the use was not consumed). A menu the use opens is closed.

### `break_block`
Request: `{"pos":[x,y,z]}` plus optional `"tool"` (otherwise the best tool in
the inventory for the block, chosen like `equip`). The body aims at the block and
holds attack across ticks until it breaks, the target changes, or 400 ticks pass.
It does not move.
`_done` (emitted when the block is gone): `{"pos", "broken": "<state>", "ticks"}`.
The task is cancelled (`_failed`, reason `cancelled:<why>`) by `release`, `cancel_route`, a follow op,
the watchdog or `despawn`.
Drops stay where they fall; collecting them is the walker's job.
`_failed`: `out_of_reach`, `no_line_of_sight`, `timeout`, `wrong_tool` (the block
needs a tool the inventory lacks, so it would drop nothing), `nothing_to_break` (air),
`unbreakable`. With no suitable tool the body holds an empty hotbar slot if it has one.

## Lab support (mcb-lab, not the body)

### `kit` (lab op, acts on the current body by name, like `fixture`)
Request: `{"op":"kit", "player":"<body name>", "inventory":[{"item":"minecraft:oak_log","count":8}, ...],
"place":[{"state":"minecraft:crafting_table","offset":[2,0,0]}, ...], "clear":[dx,dy,dz radius]}`.
Clears the inventory and sets these stacks. Places blocks at offsets from the
body's feet cell. `clear` sets the box feet-cell +-dx, 0..dy, +-dz to air (the body's two cells excepted), puts stone under
it where the floor is air, and removes item entities. Order: clear, place, inventory (each part optional;
`inventory` replaces the whole inventory). `state` is any block-state string (`minecraft:air` works).
`kit_done` data: `cleared`, `placed`, `stacks`; `kit_failed`: `reason`. Refused during a run.
Events `kit_done` / `kit_failed`. Only the lab grants items; the body never
does (D9).
