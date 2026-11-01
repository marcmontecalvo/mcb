# Building from schematics and gathering materials — plan, November 1, 2026

**Status: proposed plan (companion track).** Nothing here is built yet. It
follows the direction document: the model picks tasks only and never sits in the
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

1. **Order inside the companion track.** Gathering (M3–M4) covers the planned
   "simple tasks (mine/gather)" step. Proposal: do M0–M2 right after following a
   player, then M3–M5 as the simple-tasks step.
2. **Builder exactness.** Should the builder clear terrain in the footprint and
   break player-placed blocks that differ, or only fill empty cells? Proposal:
   clear natural terrain and stop on anything a player placed.
3. **Where to start a build.** Proposal: at the speaker's feet, facing the way
   they face, or at given x y z with rotation.
4. **Shared chests.** Should the bot take materials from chests the players
   name? That would make M5 much faster in real play.
