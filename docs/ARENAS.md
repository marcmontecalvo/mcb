# Combat arenas

Five generated, enclosed arenas for fights with the companion bot (players and the bot
against waves of mobs, or against copies of the bot). A generator produces a **plan**
(a list of block fills plus markers); a separate mod runtime places the blocks, protects
them and spawns the waves. Nothing here needs a Minecraft server: it is Python and data.

```powershell
uv run --locked mcplay arena plan all            # writes arenas/*.json, results/arena-previews/*.png
uv run --locked mcplay arena plan duel --out D   # D/duel.json, D/previews/duel-{top,side}.png
uv run --locked mcplay arena plan hall --no-previews
```

Plans are validated before they are written; an invalid plan is reported and not written
(exit code 1). The committed `arenas/<name>.json` files are checked against the generators
by `tests/test_arena_designs.py`, so regenerate and commit them after changing a design.
Previews go to `results/` (git-ignored) as PNGs written without Pillow.

## The arenas

| Name | Size (x,y,z) | Capacity | What it is |
|---|---|---|---|
| `crash_site` | 89 × 22 × 97 | 2 teams × 4, 12 mobs | PvP: a broken airliner on a round island 83 across under a night sky; the fuselage halves, crater, wings, fin, shacks and giant trees are the cover and high ground |
| `vault` | 41 × 15 × 47 | 1 team × 4, 15 mobs | Raid-vault chamber: dark gilded stone, stepped central dais, six glowing pillars, seven floor portals |
| `duel` | 33 × 13 × 39 | 2 teams × 2, 6 mobs | Sandstone pit: sunken round floor, raised terrace ring with a team on each side, two ramps |
| `hall` | 67 × 15 × 43 | 2 teams × 10, 30 mobs | Long hall: colonnade, side aisles, a dais at each end (or one big co-op group using both) |
| `crossroads` | 63 × 15 × 63 | 4 teams × 10, 24 mobs | Four arms meeting in a plaza: one base per side, a stepped obelisk in the centre |

Teams: `vault` red; `duel`, `hall` and `crash_site` red (west) and blue (east);
`crossroads` red north, blue east, green south, yellow west. `parameters`: `vault(pillars=4|6)`,
`duel(central_dais=True)`, `hall(pillars_per_row=3)`.

### Inspiration

- **`vault`** is in the style of the Vault Hunters modpack's raid/challenge rooms (a Raid
  Room there is a closed chamber of pillager mobs; see the
  [Vault Hunters wiki, Challenge Rooms](https://wiki.vaulthunters.gg/Challenge_Rooms)).
  Our version borrows only the mood: dark polished blackstone, gold trim and glowing
  inlays, a raised altar-like dais in the middle, pillars, mobs arriving from portals in
  the floor. The layout is our own and is not copied from any vault room or map.
- **`duel`** takes the compact, symmetrical pit with a raised centre and ring seating
  common to PvP duel maps, in a Roman sandstone palette
  ([Sportskeeda: 5 best Minecraft PvP arena designs](https://sportskeeda.com/minecraft/5-best-minecraft-pvp-arena-designs),
  [Sportskeeda: 5 best colosseum builds](https://sportskeeda.com/minecraft/5-best-minecraft-colosseum-builds)).
- **`hall`** is a medieval great hall: a colonnade of pillars with side aisles as flanking
  routes and raised end platforms, from the colosseum/medieval arena listings
  ([BuiltByBit: Coliseum](https://builtbybit.com/resources/coliseum.105467/),
  [BuiltByBit: 4 PvP Arenas](https://builtbybit.com/resources/4-pvp-arenas.114545/)).
- **`crossroads`** is the four-quadrant mirrored arena
  ([CurseForge: ArenaPVP](https://www.curseforge.com/minecraft/worlds/arenapvp)) with a
  raised neutral centre platform, as in several maps in the same listings.

- **`crash_site`** follows the "plane crash" PvP map idea the owner shared as screenshots
  (a round island, a broken airliner, a crater, huts and trees). It is our own design, not
  a copy of that map: the fuselage lies north to south across the island, snapped either
  side of a smouldering crater, with walk-through cabins (seats, an aisle, lanterns) and
  walkable roofs. The north half is climbed up its tilted east wing, the south half up a
  spill of cargo on its west side, and the fin up its stepped leading edge (the highest
  ground, 15 above the floor). Cover: the torn-off west wing and its engine, a second
  engine, tailplanes, two shacks with crate stairs to their roofs, fire pits and one-high
  wreckage. The rules shaped it: a sealed box needs a roof, so the sky is a black ceiling
  with sea-lantern stars; leaves, grass and campfires are banned, so the giant trees are
  dark oak trunks under moss canopies that meet the ceiling (an open canopy top would be a
  perch), and the embers are shroomlight (also the flush floor lamps the 16-block ceiling
  makes necessary). Floor: coarse dirt with patches of podzol, mud, packed mud, rooted
  dirt and moss, and a scorched skid behind the tail. Red holds the west rim, blue the
  east, each on glowing pads; four mob portals sit between them; the gallery looks over
  the nose from behind the north cliff.

These pages were seen as search results and listings, not downloaded or copied; the
shapes were designed from scratch for this project's rules.

## Plan format

```json
{"schema": 1, "name": "vault", "size": [sx, sy, sz],
 "fills": [{"from": [x,y,z], "to": [x,y,z], "block": "minecraft:polished_deepslate"}],
 "markers": {"teams": {"red": {"pads": [[x,y,z]], "entry": [x,y,z]}},
             "mob_spawns": [[x,y,z]], "spectator": [x,y,z]},
 "capacity": {"teams": 1, "per_team": 4, "mobs": 15}}
```

Local origin is the minimum corner. Fills apply in order (later wins); the leading run of
`minecraft:air` fills clears the whole bounds (split into pieces when it exceeds the
volume limit). Block strings may carry states. Every fill covers at most 32768 blocks.
The outer shell is the bounds.

### Marker legend (and top-view colours)

| Marker | Meaning | Preview |
|---|---|---|
| `teams.<colour>.pads` | Feet cells of standing spots for that team (2 air over a solid floor) | filled squares in the team colour |
| `teams.<colour>.entry` | Where that team enters the floor | white outline |
| `mob_spawns` | Feet cell at the centre of a solid 3×3 floor with 3×3×3 air; at least 12 blocks (horizontally) from every pad | magenta square on a dark 3×3 |
| `spectator` | Feet cell in a gallery behind a solid wall with a glass window; no walkable path to the floor | cyan square |

Floors are two thick: the floor surface is `y=1` and feet stand at `y=2` (`crash_site` is
three thick, feet at `y=3`, so its crater can sink two below the ground). Top-view
brightness rises with floor height; yellow/orange/white dots are lamps set flush in the floor.

## Rules the validator enforces

`mclab.arena.plan.validate(plan)` returns a list of problems (empty means usable); each
starts with a code.

- Structure: `schema`, `name`, `size` (height ≤ 64), `clear-first`, `fill-bounds`,
  `fill-order`, `fill-volume`, `block-format`, `palette` (≤ 255 distinct blocks).
- `banned`: gravity blocks, water/lava, leaves, crops, fire, doors, trapdoors, buttons (the lobby alone may use wall signs and buttons),
  pressure plates, redstone, TNT, beds, containers, spawners, carpets and things that
  change on their own (ice, grass, vines, sculk and so on). Flagged even if overwritten.
- `shell-open`: every face of the bounds is solid and the top face has no glass.
- `roof-glass` / `roof-open`: above every walkable cell there is a solid non-glass roof and no glass.
- `light`: every walkable cell has block light ≥ 8 (lamps, lanterns, glowstone, froglights...; light
  spreads through air and glass and stops at solid blocks).
- Markers: `marker-bounds`, `team`, `pad-standable`, `entry-standable`,
  `spectator-standable`, `spawn-floor`, `spawn-air`, `spawn-distance`, `capacity`.
- Walking: a player (1×2, steps up 1, drops up to 3) flood-filled from the first pad:
  `marker-unreachable`; `floor-unreachable` (any standable cell the fight floor cannot
  reach, which also catches skeleton perches); `floor-trap` (reachable but cannot be
  walked out of).
- `spectator-path` / `spectator-view`: the gallery cannot reach the floor, and a line of
  sight through glass reaches the floor.
- `ravager`: a 2×2×4 body walking from every mob spawn (steps up 1, may straddle a
  stair edge) reaches within 2 blocks of every team pad. Main fighting floor therefore has
  at least 4 blocks of headroom.

Design rules that keep the checks passing: pillars run floor to ceiling (a short pillar
leaves an unreachable top), two-high cover walls end in one-block steps, trims are flush
with the wall, hanging lanterns are only used under a solid ceiling, and wall `*_wall`
blocks are not used for cover (they are 1.5 high and cannot be jumped).

## Adding an arena

1. Write `def my_arena(**params) -> Plan` in `src/mclab/arena/designs.py` with a `Builder`:
   `shell`, `box`, `disc`, `pillar`, `low_wall`, `stair_ramp`, `band`, `banner`,
   `scatter`, `gallery`/`gallery_north`, `team`, `spawn_pad`, then `hang_lanterns` and
   `ensure_light` (tops up dark floor with flush lamps), and `to_plan(Capacity(...))`.
   `with b.rotated(k, centre):` draws the same arm a quarter turn at a time (see `crossroads`).
2. Register it in `DESIGNS`.
3. Add its capacity to `tests/test_arena_designs.py`, run `uv run --locked mcplay arena plan my_arena`
   and read the preview; fix whatever the validator reports.
4. Commit the regenerated `arenas/my_arena.json`.

## Notes for the runtime

- All markers are feet cells. Pads and entries are only for standing; teleport onto them.
- Mob spawns each have a 3×3 floor marked in a contrasting block; spawn mobs at the
  centre cell, up to 15 (vault) / 6 / 30 / 24 / 12 (crash_site) at once spread over
  `mob_spawns`.
- The runtime must forbid placing and breaking inside the bounds; nothing in the plan
  needs either to be reachable, and nothing changes by itself.
- The gallery glass window is the only glass; it is a wall, not a roof.
