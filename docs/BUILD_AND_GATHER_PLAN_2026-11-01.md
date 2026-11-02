# Building from schematics and gathering materials — plan, November 1, 2026

**Status: in progress (companion track).** Marc approved M0–M4 on Nov 1 to
run alongside follow-a-player and chat; M5–M6 come later. What is built, and
every known gap with its planned fix, is in §7 and §8. Formats:
[KNOWLEDGE_PACK_FORMAT.md](KNOWLEDGE_PACK_FORMAT.md), body ops:
[BODY_ACTION_OPS.md](BODY_ACTION_OPS.md). The plan follows the direction document: the model picks tasks only and never sits in the
loop (D6). Every step below runs in Python or in `mcb-body` from game data, with no
LLM involved. The direction document's "Later" list names crafting and inventory;
this document plans them.

Background research (Nov 1): no existing bot builds a complex template in survival
starting from an empty inventory.
- **Baritone** builds `.litematic` / `.schem` / `.schematic` files by repeatedly
  comparing the world with the schematic, but stops when blocks are missing.
- **AltoClef** gathers any item from an empty inventory with a tree of item tasks,
  but cannot build.
- **mineflayer-builder** is unfinished.
- **Mindcraft** and **Voyager** leave both jobs to an LLM.

We take Baritone's builder loop and AltoClef's item tree and connect them with a
material list.

## 1. Goal and scope

A player says "build this" with a schematic file and a place. The bot works out
the blocks it needs, gathers and crafts them starting from whatever it holds
(including nothing), and builds the structure in survival using only legal player
actions.

Not in scope at first: block-entity contents (chest items, sign text), entities
(item frames, armour stands), redstone timing, and other people's builds that
need breaking.

## 2. Principles

1. **Game knowledge comes from the running game, not hand-written tables.** A
   one-time dump from the server (§3.1) records recipes, drops, tools and blocks.
   Vanilla 26.3 and any mod pack (Create, etc.) load the same way, and a mod's
   recipes arrive with the mod.
2. **The building goal is "the world matches the template"**, not "run this list
   of placements". The builder compares the world with the template every few
   ticks and fixes what differs (Baritone's model). It survives interruptions,
   players helping, mobs, and blocks that fall.
3. **The gathering goal is an inventory**, re-planned after every task, as
   AltoClef does. If something goes wrong, the plan is rebuilt from the current
   inventory.
4. **Recipe types and placement cases we have no handler for are reported, not
   guessed.** A Create `mixing` recipe is "unavailable: needs handler
   `create:mixing`" until we write one.
5. **Same evidence rules as navigation**: lab fixtures, cohorts kept in a ledger,
   and source-bound requalification after body changes.

## 3. Components

### 3.1 Knowledge pack (body op, run once per game version and mod set)

A new body op `dump_knowledge` writes `knowledge/<pack-id>/`. The pack id is the
game version plus a hash of the mod list and datapacks. The op reads registries
and the live `RecipeManager`, so anything a mod or datapack adds is included.

| File | Contents | Source in game |
|---|---|---|
| `items.json` | stack size, durability, tool tier / mining speed, fuel value | item registry and components |
| `blocks.json` | per block: item it comes from (`asItem`), hardness, `requiresCorrectToolForDrops`, mineable tags, gravity, whether it needs a supporting face (torches, ladders, doors, carpets), state properties | block registry, tags |
| `recipes.json` | every recipe: type id, ingredients (with tag alternatives), result, count, station; recipes of unknown types kept raw | `RecipeManager` with each recipe's codec |
| `drops.json` | what each block drops with each tool tier (hand, wood, stone, iron, diamond, netherite) and with silk touch | simulate the block's loot table with a fake tool |
| `tags.json` | item and block tags (`#minecraft:planks`, `#c:ores/zinc`, …) | tag registries |
| `sources.json` | how a raw item is found in the world: tree, surface, underground at heights, mob drop | hand-written for vanilla to start (about 30 entries); later read from worldgen placed features |

Simulating drops is the key choice. "Mine stone with a wooden pickaxe →
cobblestone" or "mine zinc ore with a stone pickaxe → raw zinc" falls out of the
game's own loot rules, including mod blocks, with no table to maintain.

Each pack also gets a **coverage report**: recipe types seen and how many
recipes each has, which types have a handler, and which items cannot be reached
from the world with current handlers. That report is how we onboard a mod.

### 3.2 Schematic converter (Python, standard library only)

`mclab schematic convert house.litematic → house.build.json`

- Read the gzip NBT (a standard-library NBT reader, about 80 lines; the project
  has no Python dependencies and should keep it that way). Unpack each region's
  `BlockStates` long array: bits per entry = max(2, ceil(log2(palette size))),
  entries may span two longs, index = (y·sizeZ + z)·sizeX + x, and region sizes
  can be negative. Litematica is first; Sponge `.schem` and vanilla structure
  `.nbt` come later and write the same output.
- **The `.litematic` file holds no material list**, only `TotalBlocks` and
  `TotalVolume`. The material list the Litematica mod shows is computed in game.
  We compute ours from the block palette using `blocks.json`, with the same
  special cases Litematica handles:
  - a double slab counts as 2 slabs;
  - only the lower or foot half of doors, beds and tall plants counts;
  - candles, sea pickles and snow layers count by their property;
  - a wall torch is a torch;
  - water and lava need buckets;
  - the waterlogged flag needs no extra block;
  - blocks with no item (fire, portal, piston head, farmland, dirt path) are
    flagged with how to make them, e.g. "hoe on dirt", or as unbuildable.

Output (`build.json`, version 1):

```json
{"schema": 1, "name": "Oak house", "source": "house.litematic", "data_version": "<MinecraftDataVersion from the file>",
 "size": [11, 8, 9],
 "palette": ["minecraft:air", "minecraft:oak_planks", "minecraft:oak_stairs[facing=north,half=bottom,shape=straight]"],
 "blocks": "<base64 of palette index per cell, x fastest, then z, then y>",
 "materials": {"minecraft:oak_planks": 212, "minecraft:oak_stairs": 34},
 "flags": [{"state": "minecraft:farmland", "count": 6, "how": "use hoe on dirt"}]}
```

The command also prints the material list for a quick check against
Litematica's in-game list. Anyone can produce this file, whether a person, a
script or an outside LLM, but the converter makes that unnecessary.

### 3.3 Gathering planner (Python, symbolic, no world access)

Input: target items (the material list, or "stone pickaxe"), the current
inventory, the knowledge pack, and the stations the bot knows about (a crafting
table or furnace it has placed). Output: an ordered task list.

1. **Cheapest way to get each item.** Iterate until nothing changes:
   - cost(item) = min over its sources:
     - gather: estimated ticks to find and mine one, given the tool it needs;
     - recipe: sum of ingredient costs ÷ output count, plus crafting overhead;
     - smelt: the same, plus fuel and time.
   - Repeating until stable handles loops such as ingot ↔ block and tag
     alternatives such as any planks. A tool needed for gathering is a
     prerequisite whose cost is paid once.
2. **Expand** the targets against the inventory (subtract what is held, recurse
   on what is missing, aggregate counts so it crafts 12 planks once rather than 3
   planks 4 times). Tool durability is counted, so a wooden pickaxe has 59 uses
   and 64 cobblestone needs a second one or an upgrade.
3. **Order** prerequisites first: tools before what they mine, and the crafting
   table before 3×3 recipes. Items that need one station are grouped together.
4. **Emit tasks:** `gather(block_or_tag, n, tool)`, `craft(recipe, times,
   station)`, `smelt(input, n, fuel)`, `place_station(kind)`, `equip(item)`.

Re-plan after each task finishes or fails, from the real inventory. Unit tests
use only the planner and a vanilla pack. Empty inventory → stone pickaxe should
give logs → planks → table → sticks → wooden pickaxe → 3 cobblestone → stone
pickaxe. Iron pickaxe adds a furnace, coal or charcoal, and iron ore. A house
material list should collapse to logs, cobblestone and sand.

### 3.4 Task execution (Python decides, body acts)

| Task | How | New body work |
|---|---|---|
| `gather` | find the nearest known block of the type in map memory (explore if none), route with `interact_reach` (roadmap item 14), equip the right tool, mine, pick up drops | equip the best tool from the drop table; walk over item entities to collect them (they are already sensed) |
| `place_station` | choose a nearby standable spot; place; remember where | none (place exists); stations stored in map memory |
| `craft` | 2×2 recipes in the inventory grid; 3×3 at a table within reach | **new op `craft(recipe_id, times)`**: open the menu legally (use on the table) and fill the grid through the same server path as the recipe book, then take the result; refuse if out of reach |
| `smelt` | same reach rule; put in input and fuel; collect output when done (wait or do other tasks) | **new op `furnace(load/collect)`** |
| `equip` | pick the hotbar slot | hotbar selection exists; add moving an item to the hotbar |

Night, hunger and mob defence act as AltoClef-style task chains with priorities.
They already sit in the open night-survival question (roadmap item 13) and are
not part of this plan.

### 3.5 Builder (body executes, Python orders)

The loop runs every N ticks:

1. **Compare** the template with the live world inside the footprint.
   Properties listed as ignorable don't count: leaf `distance`/`persistent`,
   redstone `power`, door `open`, `waterlogged` where configured. That leaves
   wrong cells to break and empty cells to fill.
2. **Choose the next cell** with the lowest-cost rule that is legal now:
   - break wrong blocks first, terrain included;
   - fill from the lowest layer up;
   - a cell can be placed only against an existing solid face;
   - gravity blocks need support below;
   - attached blocks (torches, ladders, doors, buttons) only after their
     support;
   - liquids last.
3. **Find a stance and a placement** in the body:
   - for each candidate standing cell from the planner (`interact_reach` goal on
     the target cell), the body tries each face it could click, hit point and
     yaw;
   - it asks the block's own `getStateForPlacement` what state would result
     (Baritone's method) and keeps the first that gives the template state,
     e.g. a stair facing north with half=bottom, or a log on its x axis;
   - this needs game code, so it lives in `mcb-body` as op
     `place_state(pos, state)`, which returns the stance and the aim or
     "impossible from here".
4. **Reach high cells** with scaffolding: pillar or bridge using throwaway blocks
   (dirt or cobblestone, already priced by the planner's place edges), and
   remove the scaffold at the end. Overhangs get a temporary support block that
   is removed afterwards.
5. **Pause and request materials** when the inventory lacks the block. Under the
   companion flow that calls the gathering planner for the remaining material
   list, then resumes building.

The anchor is set by the command: "build at my feet facing north" or x y z, plus
rotation and mirror. The converter rotates block states with the knowledge
pack's property rules.

## 4. Milestones and acceptance

Each milestone is a lab fixture (flat world plus a known schematic) with a
pass/fail check from the world.

| # | Milestone | Acceptance |
|---|---|---|
| M0 | `dump_knowledge` on vanilla 26.3 | pack loads; drops for stone (hand → nothing, wood → cobblestone) and iron ore (stone → raw iron) are correct; coverage report lists crafting, smelting, blasting, smoking, campfire, stonecutting, smithing |
| M1 | Converter | three schematics (small house, stairs and slabs test, a tower with torches and doors): material list equals Litematica's in-game list; round trip unpacks to the same states as Litematica's own export |
| M2 | Builder with materials given (body inventory filled at the start) | small house, then the stairs/slab test: world diff 0 after the build; no illegal reach; scaffold removed; repeated over 3 reps |
| M3 | `craft` and `smelt` ops | in-lab fixtures: craft planks, sticks, table, pickaxe; smelt iron; no direct item granting anywhere in the body |
| M4 | Gathering planner and executor | empty inventory → stone pickaxe and → iron pickaxe on 3 seeds, 3 reps each; planner unit tests for loops, tags and durability |
| M5 | End to end | empty inventory + house schematic → finished house, day only, 3 seeds; ledger records ticks per phase (gather, craft, build) |
| M6 | First mod pack (Create) | pack dump and coverage report; vanilla recipe types still work alongside Create items; list of Create recipe types and machines we would need to handle, as a separate decision |

M2 needs only M1, so building can be shown before gathering exists. M3 and M4
can be worked on alongside M2.

## 5. Mods such as Create

The dump captures every recipe and recipe type, so onboarding a mod means running
the dump and reading the coverage report. No hand-written tables are needed.
Create raises two further needs, both later:

- **Processing recipes** (crushing, milling, pressing, mixing) run in machines,
  not menus. Each type needs a handler: put items in, wait, collect.
- **Machines are structures.** A press with its shaft, cogwheel and power source
  is a small schematic. The builder (§3.5) builds it, and a handler then uses it.
  Building and gathering meet here: a recipe's station can be "the press
  schematic, built", and its cost enters the planner like a crafting table's.

## 6. Open questions for Marc

1. Answered Nov 1: M0–M4 are built now, in parallel with follow and chat; M5–M6
   later. Questions 2–4 run on the proposals below until Marc says otherwise.
2. **Builder exactness.** Should the builder clear terrain in the footprint and
   break player-placed blocks that differ, or only fill empty cells? Proposal:
   clear natural terrain and stop on anything a player placed.
3. **Where to start a build.** Proposal: at the speaker's feet, facing the way
   they face, or at given x y z with rotation.
4. **Shared chests.** Should the bot take materials from chests the players
   name? That would make M5 much faster in real play.
5. Answered Nov 1 (Marc): **yes.** The bot may shear or kill sheep, cows and
   other animals as needed. It must also eat: kill animals and cook the meat
   (F1).
6. Answered Nov 1 (Marc): **yes.** It may craft and place chests for storage.
   Shulker boxes are supplied by the test harness, not by Marc by hand: either
   in the starting kit, or in a supply chest the lab spawns near the
   construction site. The purpose is to make collecting and storing blocks easy,
   so the tests measure building and gathering.

**Rule for all of this work (Marc, Nov 1):** the goal is real-world use, not
making things hard for the bot. Test runs start somewhere with the resources the
build needs nearby: the right wood types, stone, animals (S1). A run that
shows the bot walking 5,000 blocks to find a tree is pointless. Operator grants of
containers are allowed: the lab places a supply chest with shulker boxes at the
build site, or puts them in the kit. The body itself still never creates items
(D9).

## 7. Implementation status (Nov 1, 2026)

Wave 1 is commit `aa041b2`. Wave 2 is complete, with live checks on the lab
server:
- `scripts/check-placement.py`: 46/46, `results/companion/20261101T172441585144Z`.
  Covers stairs in 4 facings × 2 halves, logs on 3 axes, top and bottom slabs,
  standing and wall torches, doors with both hinges, and trapdoors placed then
  opened with `use_block`. Also `break_block` with an automatically chosen
  pickaxe, plus every refusal reason.
- `scripts/check-crafting.py`: 8/8, `results/companion/20261101T172600197748Z`.
  Covers 2×2 and table crafting, `no_table` and `out_of_reach`, the furnace
  (3 raw iron → 3 ingots), and equip.

Placement simulates through the item's own placement path (`BlockItem`), as
Baritone does. Calling the block directly picked the wrong torch.

| # | Status | Evidence |
|---|---|---|
| M0 | **Done live.** `dump_knowledge` writes the pack in 0.42 s: 1,657 items, 1,286 blocks, 938 tags, 2,042 recipes (361 of types we don't handle), drops for 1,270 blocks, 0 errors. The first dump scrambled tier order (`Map.of`); fixed and re-dumped | `knowledge/26.3-f7dba04d8b5b/` (committed) |
| M1 | **Done** for `.litematic`. The real house converts to 1,227 blocks, matching the file. Pot and plant are counted like Litematica | `tests/test_schematic.py`, `tests/fixtures/schematics/house-154fc7cd.litematic` |
| M2 | **Python done:** diff, ordering, rotation/mirror, scaffolding, open/close by `Use`, potted plants, hanging lanterns, and a build loop with retry/skip. In simulation the real house builds to an empty diff at ground level and raised (with scaffold cleanup). **Body ops done live** (`place_block`, `use_block`, `break_block`). Open: the real adapter (B1) |  `tests/test_build_plan.py`, `tests/test_build_runner.py` |
| M3 | **Done live** (`craft`, `furnace`, `equip`; lab `kit` fixture grants items, never the body) | `scripts/check-crafting.py` |
| M4 | **Planner done** on the real pack (each plan below passes `check_order`):<br>• empty → stone pickaxe: 9 tasks<br>• empty → iron pickaxe: 15 tasks<br>• 200 cobblestone: upgrades to stone pickaxes<br>• house: 76 tasks; only bed and bookshelf unreachable (mob drops)<br>**Done live Nov 2:** empty inventory → iron pickaxe 9/9 (seeds 42424242, 31415926, 20261026 × 3; commit `90050ec`; no damage; 5,165–11,414 ticks). Cohorts and failures: ROADMAP | `tests/test_gather_plan.py`, `tests/test_gather_plan_real.py`, `tests/test_gather_dig.py`, `results/companion/20261102T0539…–063009…` |
| M5 | Not started (§9) | — |
| M6 | Not started (§9) | — |

### Code map

| Piece | Files |
|---|---|
| Game-data dump | `mcb-body/.../KnowledgeDump.java`, `KnowledgeLogic.java` (+ `KnowledgeLogicTest`) |
| Body actions | `mcb-body/.../BodyActions.java`; op cases in `BodyRuntime.java`; routing in `src/mclab/bridge.py` (`BODY_OPS`) |
| Lab kit fixture | `mcb-lab` `kit` op (wave 2) |
| Pack loader | `src/mclab/knowledge.py`: `load_pack(path, sources=, transforms=)` |
| Hand-written data | `knowledge/sources/vanilla-26.3.json` (where raw items are found), `knowledge/transforms/vanilla-26.3.json` (axe stripping; hoe and shovel in place) |
| Converter | `src/mclab/nbt.py`, `src/mclab/schematic.py`; CLI `mclab schematic convert <file> [-o out] [--knowledge pack]` |
| Gathering planner | `src/mclab/gather_plan.py`: `plan(targets, inventory, knowledge, stations, known_blocks=, prefer=)`, `check_order`, `summarize` |
| Builder | `src/mclab/build_plan.py` (`Template`, `diff`, `next_actions`, `cleanup_actions`, `progress`) and `src/mclab/build_runner.py` (`BuildBody` protocol, `run_build`, `FakeBody`) |

## 8. Known gaps and planned fixes

Each gap has an id, so the ledger and the roadmap can refer to it. "Wave" is the
proposed order: wave 3 makes M2–M4 run live, wave 4 is M5, and wave 5 is M6.

### Knowledge pack (M0)

| Id | Gap | Planned fix | Wave |
|---|---|---|---|
| K1 | `needs_support` is guessed from Java class names, so mod blocks get `null` | Probe it in the game: in a scratch area, place each block's default state against one neighbour at a time and record which neighbour `canSurvive` needs. Keep the class table as a cross-check | 5 |
| K2 | Tool `kind`/`tier` come from the item id prefix (`wooden_`, `stone_`); mod tools break this | Derive tier from the tool component's rules (which `incorrect_for_*` tag it denies) and kind from the `mineable/*` tags it mines | 5 |
| K3 | Drop tiers stop at the vanilla list; copper tier missing | Build the tier list from the tool items found in the pack, ordered by what each can mine | 5 |
| K4 | 361 recipes of types we don't handle; `crafting_dye` (6) and `crafting_transmute` (33) matter for vanilla dyeing and recolouring | Parse both in `KnowledgeLogic.recipe` (input + dye → output); keep `brewing` and the special crafting types raw | 3 |
| K5 | 20 rolls give noisy min/max/chance for rare drops (flint, saplings, apples) | 200 rolls for blocks whose first 20 rolls vary; the dump is fast enough | 3 |
| K6 | No coverage report file yet (only counts in the event) | Write `coverage.json`: recipe types with counts and handled flag, and items unreachable with current handlers (run the planner over every item) | 5 |
| K7 | Entity loot tables not dumped (needed for G1) | Add `mobs.json`: per entity, drops when killed by a player (rolled like blocks) and shear/milk results | 3 |

### Converter (M1)

| Id | Gap | Planned fix | Wave |
|---|---|---|---|
| C1 | Only `.litematic` | Add Sponge `.schem` v2/v3 and vanilla structure `.nbt` readers in `schematic.py`, each returning a `Schematic` | 4 |
| C2 | Material list not yet compared with Litematica's in-game list | Marc exports the house's material list from Litematica (it writes a text/CSV file); a test compares them | 3 |
| C3 | Block entities (chest contents, sign text, banner patterns) and entities (item frames) ignored | Keep them in `build.json` as an optional `block_entities` list; the builder ignores them until a handler exists | later |

### Builder (M2)

| Id | Gap | Planned fix | Wave |
|---|---|---|---|
| B1 | **Real adapter missing.** `goto_stance` must use the route planner with `interact_reach`; the ops wrap `place_block` / `use_block` / `break_block` | `src/mclab/build_body.py` implements `BuildBody` over `Bridge`: `goto_stance` = `RouteSearch(goal=target, interact_reach=4.5)` plus route following, excluding `avoid` stances; `world()` from delivered map memory only | 3 |
| B2 | Cells with no item (farmland, dirt path) are skipped silently | Use the in-place transforms: place dirt, then `Use` with a hoe or shovel; count the tool in the material list | 3 |
| B3 | A scaffold cell that is also a template cell is not special-cased | Never scaffold inside the template's non-air cells; pick the nearest column outside, or build up from the template's own lower blocks | 3 |
| B4 | Unknown neighbours defer placement (strict, by project rule) | Before building, walk the footprint perimeter so the awareness sensor delivers every section; report unknown cells in `progress` | 3 |
| B5 | Liquids and waterlogging only roughly handled | Place source blocks with buckets last; skip `waterlogged=true` unless the template has water next to it | 4 |
| B7 | Placing against an interactive neighbour (door, crafting table, chest) would use that block instead of placing | Hold sneak during the `place_block` use, as players do; add a live case placing on a crafting table | 3 |
| B8 | `place_block` never moves, so a far-side face gives `state_unreachable` | Expected: the build loop asks `goto_stance` for another stance (already in `run_build`); live-check it with B1 | 3 |
| B6 | Builds anchored only by explicit origin | Chat command "build X here": origin from the speaker's feet and facing (rotation) | 4 |

### Body actions (M3)

| Id | Gap | Planned fix | Wave |
|---|---|---|---|
| A1 | Done Nov 1: live crafting 8/8 and placement 46/46 (see §7) | — | 2 |
| A5 | Placement skips spawn-protection and `mayInteract` checks | Call the same permission checks the packet path uses before acting; matters on the play server, not the lab | 3 |
| A2 | Body mod changed | Rerun qualification (roadmap item 11 procedure) before any scored navigation run | 3 |
| A3 | Observed inventory has no tool damage, so the planner assumes held tools are new | Add `damage` and `max_damage` per stack to `observe()`; the planner uses remaining uses | 3 |
| A4 | No chest ops (needed for G5 and §6 question 4) | `chest` op: `deposit` / `withdraw` with legal menu clicks, same pattern as `furnace`; also shulker boxes | 3 |

### Gathering (M4)

| Id | Gap | Planned fix | Wave |
|---|---|---|---|
| G0 | **Executor not written** | `src/mclab/gather_runner.py`, same shape as `build_runner` (protocol + fake + real adapter):<br>• `Gather`: find the nearest known block in map memory, else explore by `where`; `goto_stance` and `break_block`; walk over the drops<br>• `Craft` / `Smelt` / `Equip`: the body ops<br>• `PlaceStation`: `place_block` on a free spot near the work, recorded in map memory<br>• `Transform`: place the log, `use_block` with the axe, `break_block`<br>• Re-plan from the observed inventory after every task and on any failure | 3 |
| G1 | Mob drops (leather, wool, string, feather) unreachable, so bed and bookshelf can't be made | Sources of kind `mob` from K7. New tasks `Shear(entity)` (shears on a sheep) and `Hunt(entity, count)` (body op `attack_entity` on passive animals; collect drops). Wool by shearing first. Approved by Marc (§6 question 5) | 3 |
| F1 | No eating; long gather and build jobs will starve the bot | Body op `eat` (hold use on a food item until eaten). A hunger rule in the executors: eat when food ≤ 14 and food is held; if none, add a `Hunt` of the nearest food animal plus `Smelt` of the raw meat to the plan (cooked meat restores more). Food counts as a planner target alongside materials | 3 |
| S1 | A test start may be far from the needed resources | Lab site finder. Given a material list, take the needed raw sources (log types, stone, ores, animals). For each candidate start, use `/locate biome` for the needed biomes, load the area and count logs per wood type, exposed stone and passive animals in a 96-block radius, then pick the best site. Fixed seeds plus chosen sites are recorded in the ledger so runs are reproducible | 4 |
| G2 | Tool tiers are not optimised across block types (the house plan uses 4 wooden pickaxes because the stone pickaxe is "already needed for iron") | A tool-progression pass after expansion: total the mining work per tool kind, choose the tier sequence (wood → stone → iron) with the lowest total cost, and move gathers that a later tier does faster after that upgrade | 3 |
| G3 | Upgrades only for the same tool kind and only from the gathered material | Covered by G2: the tool sequence is chosen globally, from any material in the plan | 3 |
| G4 | Chance-based drops are planned at their expected value | Plan for `ceil(count / chance)` plus one, and re-plan after gathering (the planner is already stateless); `check_order` uses the minimum yield | 3 |
| G5 | No inventory limit; the house needs about 600 logs and 106 iron ore | Inventory model from `items.json` stack sizes (36 slots). Split the plan into trips that fit and deposit into a storage chest at the build site (A4). The bot crafts and places the chests itself. The executor deposits when fewer than 4 slots are free. In tests the lab supplies shulker boxes (kit, or a supply chest at the site); they count as portable storage (a placeable container whose contents travel). Approved by Marc (§6 question 6) | 3 |
| G6 | `check_order` does not simulate tool wear | Simulate durability per tool (one use per block; `max_damage` from the pack, current damage from A3) | 3 |
| G7 | Sources' `y` and `biomes` hints are not used for cost or search | Executor: prefer blocks in map memory; for ores, branch-mine at the best `y` from `sources.json`. Planner: search cost scales with distance to the nearest known block | 4 |
| G8 | Recipe remainders (bucket returned, bottles) not modelled | Read the remainder item from the recipe JSON (K4 parse) and credit it back | 4 |
| G9 | Fortune and silk touch ignored | Not needed for building. Revisit only if a recipe needs a silk-touched block | later |

## 9. Remaining milestones in detail

**M5, end to end** (wave 4): one command does the whole job. It reads the
`build.json`, takes the material list minus what the bot already holds, and
runs the gathering planner and `gather_runner`. Then `run_build` runs, with its
`on_need_materials` hook calling the gatherer for the shortfall. It needs G0,
B1, G5 and A4. Acceptance as in §4: empty inventory to the finished small house,
then the real house once question 5 is answered. The ledger records ticks per
phase.

**M6, first mod pack (Create)** (wave 5): dump the pack with Create installed;
add the K6 coverage report, plus K1–K3 so mod blocks and tools are described
from the game rather than from names. Then list Create's recipe types and
machines and decide with Marc which handlers to build. Each machine is a small
schematic the builder places, plus a handler (put items in, wait, collect),
priced in the planner like a station.
