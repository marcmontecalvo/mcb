# Knowledge pack and build file formats (version 1)

Contract between the body's `dump_knowledge` op (producer), the schematic
converter, the gathering planner and the builder (consumers). Plan:
[BUILD_AND_GATHER_PLAN_2026-11-01.md](BUILD_AND_GATHER_PLAN_2026-11-01.md).
Change this file first when a format changes, and bump `schema`.

All ids are namespaced strings (`minecraft:oak_planks`). Tags are written with a
leading `#` (`#minecraft:planks`). Every file is UTF-8 JSON with a top-level
`"schema": 1`.

## Pack layout

`knowledge/<pack_id>/` where `pack_id` = `<game version>-<first 12 hex of sha256
over sorted "modid@version" lines and datapack names>`, e.g. `26.3-3f9a0c1b2d4e`.

### `pack.json`
```json
{"schema": 1, "pack_id": "26.3-3f9a0c1b2d4e", "game_version": "26.3",
 "data_version": 4671, "mods": ["fabricloader@0.19.5", "minecraft@26.3", "..."],
 "datapacks": ["vanilla"], "created": "2026-11-01T20:00:00Z"}
```

### `items.json`
```json
{"schema": 1, "items": {
  "minecraft:wooden_pickaxe": {"stack": 1, "durability": 59,
     "tool": {"kind": "pickaxe", "tier": "wood", "speed": 2.0}, "fuel_ticks": 200},
  "minecraft:oak_planks": {"stack": 64, "fuel_ticks": 300},
  "minecraft:stick": {"stack": 64, "fuel_ticks": 100}}}
```
`durability` only for damageable items; `tool` only for mining tools (`kind` one of
`pickaxe`, `axe`, `shovel`, `hoe`, `sword`, `shears`; `tier` one of `wood`,
`stone`, `copper`, `iron`, `gold`, `diamond`, `netherite`, or the mod's own tier id);
`fuel_ticks` only for furnace fuels.

### `blocks.json`
```json
{"schema": 1, "blocks": {
  "minecraft:stone": {"item": "minecraft:stone", "hardness": 1.5,
     "needs_correct_tool": true, "mineable": ["pickaxe"], "gravity": false,
     "needs_support": null, "properties": {}},
  "minecraft:oak_stairs": {"item": "minecraft:oak_stairs", "hardness": 2.0,
     "needs_correct_tool": false, "mineable": ["axe"], "gravity": false,
     "needs_support": null,
     "properties": {"facing": ["north","south","west","east"], "half": ["top","bottom"],
                    "shape": ["straight","inner_left","inner_right","outer_left","outer_right"],
                    "waterlogged": ["true","false"]}},
  "minecraft:wall_torch": {"item": "minecraft:torch", "hardness": 0.0,
     "needs_correct_tool": false, "mineable": [], "gravity": false,
     "needs_support": "facing_opposite", "properties": {"facing": ["north","south","west","east"]}},
  "minecraft:fire": {"item": null, "...": "..."}}}
```
- `item`: `Block.asItem()` id, or `null` when the block has no item.
- `needs_support`: `null`, `"below"` (torch, carpet, door, sapling), `"facing_opposite"`
  (wall torch, ladder, wall sign), `"any_face"` (button, lever per `face` property),
  `"above"` (hanging lantern etc.).
- `mineable`: tool kinds from the `minecraft:mineable/*` tags.

### `tags.json`
```json
{"schema": 1, "items": {"#minecraft:planks": ["minecraft:oak_planks", "..."]},
 "blocks": {"#minecraft:logs": ["minecraft:oak_log", "..."]}}
```
Fully expanded (nested tags resolved).

### `recipes.json`
```json
{"schema": 1, "recipes": [
  {"id": "minecraft:oak_planks", "type": "minecraft:crafting_shapeless",
   "station": "inventory",
   "ingredients": [{"options": ["#minecraft:oak_logs"], "count": 1}],
   "result": {"item": "minecraft:oak_planks", "count": 4}},
  {"id": "minecraft:wooden_pickaxe", "type": "minecraft:crafting_shaped",
   "station": "crafting_table", "width": 3, "height": 3,
   "pattern": ["XXX", " # ", " # "], "key": {"X": ["#minecraft:planks"], "#": ["minecraft:stick"]},
   "ingredients": [{"options": ["#minecraft:planks"], "count": 3},
                   {"options": ["minecraft:stick"], "count": 2}],
   "result": {"item": "minecraft:wooden_pickaxe", "count": 1}},
  {"id": "minecraft:iron_ingot_from_smelting_raw_iron", "type": "minecraft:smelting",
   "station": "furnace", "ingredients": [{"options": ["minecraft:raw_iron"], "count": 1}],
   "result": {"item": "minecraft:iron_ingot", "count": 1}, "cook_ticks": 200},
  {"id": "create:crushing/zinc_ore", "type": "create:crushing", "station": null,
   "raw": {"...": "codec output, unparsed"}}]}
```
- `ingredients` is the aggregated list (each distinct slot ingredient with its
  count); `options` lists item ids and/or tags, any one of which satisfies it.
- `station`: `inventory` (fits 2×2), `crafting_table`, `furnace`, `blast_furnace`,
  `smoker`, `campfire`, `stonecutter`, `smithing_table`, or `null` for types we do
  not handle (then `raw` holds the codec JSON and `ingredients`/`result` may be absent).
- Special (dynamic) crafting recipes (map cloning, firework, etc.) are listed with
  `station: null`.

### `drops.json`
```json
{"schema": 1, "tools": ["hand", "wood", "stone", "iron", "diamond", "netherite", "silk_touch"],
 "drops": {
  "minecraft:stone": {"hand": [], "wood": [{"item": "minecraft:cobblestone", "min": 1, "max": 1}],
                      "silk_touch": [{"item": "minecraft:stone", "min": 1, "max": 1}]},
  "minecraft:iron_ore": {"hand": [], "wood": [], "stone": [{"item": "minecraft:raw_iron", "min": 1, "max": 1}]}}}
```
Tool keys mean "the correct tool kind for this block at that tier" (`hand` = empty
hand). A missing key means "same as the next lower tier". `min`/`max` come from
simulating the loot table many times (no fortune); a drop seen in fewer than 100%
of rolls has `"chance": p`. Only blocks with an item or a non-empty drop appear.

### `mobs.json` (produced by the dump; added Nov 1, still schema 1)
```json
{"schema": 1, "mobs": {
  "minecraft:cow": {"category": "creature", "passive": true, "health": 10.0,
     "killed_by_player": [{"item": "minecraft:beef", "min": 1, "max": 3},
                          {"item": "minecraft:leather", "min": 0, "max": 2, "chance": 0.6}],
     "interactions": [{"item": "minecraft:bucket", "gives": "minecraft:milk_bucket"}]},
  "minecraft:sheep": {"category": "creature", "passive": true, "health": 8.0,
     "killed_by_player": [{"item": "minecraft:mutton", "min": 1, "max": 2},
                          {"item": "minecraft:white_wool", "min": 1, "max": 1}],
     "interactions": [{"item": "minecraft:shears", "gives": "minecraft:white_wool",
                       "min": 1, "max": 3, "per_color": true}]}}}
```
- `killed_by_player`: the entity's loot table rolled with a player killer and an
  empty hand, no looting (200 rolls; `chance` when not every roll dropped it).
- `interactions`: hand-checked list for vanilla (shearing sheep and mooshrooms,
  milking, bowls on mooshrooms); `per_color` means the wool matches the sheep's
  colour.
- Only `category` `creature` / `water_creature` / `ambient` entities with
  `passive: true` are hunting targets (Marc, Nov 1: animals are allowed; hostile
  mobs are a separate question).

### `foods-26.3.json` (hand-written, `knowledge/sources/`)
`{"schema": 1, "foods": {"minecraft:cooked_beef": {"nutrition": 8, "saturation": 12.8}, ...}}`,
covering 16 vanilla foods. Loaded with `load_pack(..., foods=path)`; `mobs.json`
is read from the pack root when present, and `mobs=` overrides it.

### `sources.json` (hand-written for vanilla; not produced by the dump)
Mobs are listed here too, with `"where": "mob"`, e.g.
`"minecraft:cow": {"where": "mob", "biomes": "grassland"}`.
```json
{"schema": 1, "sources": {
  "minecraft:oak_log": {"where": "tree", "biomes": "temperate"},
  "minecraft:stone": {"where": "underground", "y": [-64, 256]},
  "minecraft:iron_ore": {"where": "underground", "y": [-64, 72]},
  "minecraft:sand": {"where": "surface", "near": "water"}}}
```
Blocks to look for in the world. The gathering planner treats any block listed
here (or present in the bot's map memory) as gatherable.

## Build file (`*.build.json`, schema 1)

Produced by `mclab schematic convert`; consumed by the builder.

```json
{"schema": 1, "name": "Oak house", "source": "house.litematic",
 "data_version": 4671, "size": [11, 8, 9],
 "palette": ["minecraft:air", "minecraft:oak_planks",
             "minecraft:oak_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"],
 "blocks": "<base64 of little-endian uint16 palette indices>",
 "materials": {"minecraft:oak_planks": 212, "minecraft:oak_stairs": 34},
 "flags": [{"state": "minecraft:farmland[moisture=0]", "count": 6, "reason": "no_item", "how": "use hoe on dirt"}]}
```
- Cell order: index = (y · size_z + z) · size_x + x, origin at the minimum corner.
- Palette entry 0 is always `minecraft:air`. Air cells mean "must be air" unless
  the builder is told to keep existing blocks.
- States are written as `id[prop=value,...]` with properties sorted by name.
- Multiple Litematica regions are merged into one box.
