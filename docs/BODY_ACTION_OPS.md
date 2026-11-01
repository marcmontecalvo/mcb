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

## Wave 3 (built and live-checked: `scripts/check-animals.py` 16/16, `scripts/check-containers.py` 18/18, `scripts/check-natural-ops.py` 14/14)

Common refusals as in wave 2. Entity ops take `"entity": "<uuid>"` from the
entities the body already senses (`PolicySensor.entities`, with `uuid` added to
each entry).

### `place_block` change (B7)
The use is performed while sneaking, so placing against a crafting table, chest,
door or furnace places the block instead of opening the neighbour. The same
permission checks as the packet path apply: spawn protection and `mayInteract`
(A5). Built: `place_block` runs with sneak on (restored afterwards); `place_block`,
`use_block`, `use_entity` and `container` fail with `protected` when
`MinecraftServer.isUnderSpawnProtection`, `ServerLevel.mayInteract` or the build limits refuse the position.
Observations: `observe()` inventory entries gain `damage` and `max_damage` for damageable stacks (A3);
`PolicySensor.entities` entries carry `uuid`, `sheared` (sheep) and `baby` (animals).

Notes on built behaviour (differences from the first contract text):
- Entity-op `_done` events carry the entity type in `type`, as in the contract; the live scripts'
  `call()` helper moves it to `entity_type` and keeps `type` for the event kind.
- `use_entity` reports `no_effect` when nothing changed (entity state and inventory identical), because
  vanilla answers CONSUME to shears on an already sheared sheep.
- `eat` and `attack_entity` are per-tick tasks like `break_block`: `busy` while one runs, cancelled
  by `release`, `cancel_route`, follow ops and despawn (`<op>_failed`, `cancelled:<why>`).
  `eat` uses `ServerPlayerGameMode.useItem` (about 32 ticks); `_done` also has `ticks`.
- `attack_entity` calls `Player.attack` when the attack strength is at least 0.95, then swings and resets
  the cooldown, as a client does. Weapon choice: best sword, else axe, by durability.
- A finished route no longer leaves the ops blocked: `route_active` means a route, step, gap or reflex
  follower is running now, not that native mode was once entered. A threat reflex still cancels a running
  break, attack or eat.
- `container` deposits/withdraws by quick-move for whole stacks and by pick up, one-by-one place and put back
  for partial counts. Without `items`, `deposit` moves every inventory stack that is not damageable, food or a
  tool, and not in the first three inventory slots. Failures leave moved items moved.
  Shulker boxes keep their contents when broken (checked: the dropped item's `container` component lists them).
- The ops work inside a running `natural_start` session (check-natural-ops.py), including after a
  finished `follow_route`. Companion sessions (`subject` `carpet_body`) may use `tick_limit` up to
  1,200,000 and a goal of any distance; other subjects keep the old limits (24,000 ticks, 384 blocks).

### `attack_entity`
Request: `{"entity":"<uuid>", "weapon":"<item>"?}`. Targets passive animals only
(`Animal`, not tamed, not a player, not a villager); anything else is refused
with `not_allowed`. The body equips the weapon, or the best sword/axe held (else
an empty hand). It aims at the entity's box centre each tick and attacks whenever
the attack cooldown is full and the entity is within entity interaction range
with a line of sight. It does not move: if the animal walks out of reach the op
fails with `out_of_reach`, and Python re-approaches. It ends when the entity dies
or after 200 ticks.
`_done`: `{"entity", "type", "killed": true, "ticks", "hits"}`. `_failed`:
`out_of_reach`, `no_line_of_sight`, `not_allowed`, `not_found`, `timeout`,
`cancelled:<why>`.
Drops stay where they fall.

### `use_entity`
Request: `{"entity":"<uuid>", "item":"<item>"?}`. A single normal use on the
entity (`interactOn`) with the item held, e.g. shears on a sheep, or a bucket on a
cow. Reach and line of sight as above.
`_done`: `{"entity", "type", "result": "<InteractionResult>", "before", "after"}`
(for sheep, `sheared` before and after). `_failed`: `out_of_reach`,
`no_line_of_sight`, `no_effect`, `not_found`.

### `eat`
Request: `{"item":"<food item>"?}` (default: the held food that restores the most).
The body holds use until the item is consumed (about 32 ticks), and is not allowed
to move meanwhile.
`_done`: `{"item", "food_before", "food_after", "saturation_after"}`. `_failed`:
`no_food`, `not_hungry` (food is full and the item can't always be eaten),
`interrupted`, `cancelled:<why>`.

### `container`
Chests, barrels and shulker boxes. Request:
`{"pos":[x,y,z], "action":"deposit"|"withdraw"|"peek", "items":[{"item":"minecraft:oak_log","count":64}, ...]}`.
For `deposit` with `items` omitted, it deposits everything except tools, food and
the hotbar's first 3 slots. It opens the container as a use would (reach check;
the clicked face must be visible), moves stacks with legal menu clicks (quick
move), and closes the menu.
`_done`: `{"pos", "moved":[{item,count}], "contents":[{slot,item,count}]}`.
`_failed`: `not_a_container`, `out_of_reach`, `cannot_open` (e.g. a chest under a
solid block), `container_full`, `inventory_full`, `missing_item`.
A shulker box keeps its contents when broken with `break_block` (the game does
this). Python records where each container is and what it holds.

## Lab support (mcb-lab, not the body)

### `kit` (lab op, acts on the current body by name, like `fixture`)
Request: `{"op":"kit", "player":"<body name>", "inventory":[{"item":"minecraft:oak_log","count":8}, ...],
"place":[{"state":"minecraft:crafting_table","offset":[2,0,0]}, ...], "clear":[dx,dy,dz radius]}`.
Clears the inventory and sets these stacks. Places blocks at offsets from the
body's feet cell. `clear` sets the box feet-cell +-dx, 0..dy, +-dz to air (the body's two cells excepted), puts stone under
it where the floor is air, and removes item entities. Order: clear, place, inventory (each part optional;
`inventory` replaces the whole inventory). `state` is any block-state string (`minecraft:air` works).
Extra parts, applied after `inventory`: `"food": 0..20` sets the food level (saturation 0);
`"summon":[{"entity":"minecraft:sheep","offset":[3,0,0],"baby":false}]` spawns entities at offsets from
the body's feet cell (max 16). **Fixture simplification:** summoned mobs get `setPersistenceRequired()` and
`setNoAi(true)`, so they stay where put and do not despawn (a wandering animal would leave reach during a check);
a `clear` also removes mobs in its box. A `place` entry may carry `"contents":[{"item":"minecraft:shulker_box","count":4}]`
(chest, barrel or shulker box; stacks split at their maximum size into the first free slots), e.g. a supply
chest holding shulker boxes: `{"state":"minecraft:chest[facing=north]","offset":[3,0,0],"contents":[...]}`.
`"nearby": <radius up to 32>` adds a list of the entities around the body to `kit_done` (a lab-side debugging
view): `type`, `uuid`, `position`, and for items `item`, `count`, `container` (stacks inside a shulker
box item), for sheep `sheared`.
`kit_done` data: `cleared`, `placed`, `stacks`, `summoned`, `summoned_entities` (`[{entity, uuid}]`), `nearby`;
`kit_failed`: `reason`. Refused during course episodes and other runs; **allowed inside a running natural
session** (`natural_start`), where the usual run/episode/body identity must match.
Events `kit_done` / `kit_failed`. Only the lab grants items; the body never
does (D9).
