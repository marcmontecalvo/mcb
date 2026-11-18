# Scaffolding: how the body uses it

The user's direction (2026-11-17): scaffolding is the default for every temporary block —
towers, pillars, step-ups and bridges. It breaks instantly, the whole column drops when its
base is broken, it is cheap, and a body passes through it. Solid temporaries (dirt,
cobblestone, spare plain blocks; see `temporary_blocks.py`) are the fallback, never refused.

## Vanilla rules the code relies on

- **Distance.** Each scaffolding block stores `distance` 0–7. On a sturdy block, or on
  scaffolding directly below, it takes the distance of what is under it (0 on ground). Otherwise
  it is the least horizontal neighbour distance + 1. At 7 it falls. So one column reaches 6
  blocks out in every direction: one tower spans a 13-block bridge (6 + column + 6), and two
  towers carry anything longer.
- **Placing on scaffolding redirects** (`ScaffoldingBlockItem`). Clicking the top face extends
  it horizontally the way the player faces; clicking a side face goes up to the top of that
  column. Sneaking places it on the clicked face like a normal block.
- **Standing and descending.** A body above scaffolding stands on its top plate. Holding sneak
  sinks through it; jumping inside climbs it. A body beside or inside a column is not blocked
  by it. The sensor reports the shape for a body above (plate and legs), which the planner and
  motor treat as a floor, not as a wall.
- **Taking it down.** Breaking the bottom block drops the column and everything held by it.

## What the code does (commit `3954dac`)

- **Planner** (`RoutePlanner.scaffoldCells`): an edit's placed cells are scaffolding when the
  body holds it, the cell's distance would be 6 or less, and it is not clicked onto scaffolding
  (the redirect would put it elsewhere). The rest are solid. The edit carries
  `scaffold: [x,y,z,distance]` for the motor.
- **Step motor:** places scaffolding for those cells, takes a scaffolding floor as support, and
  never sneaks while on scaffolding.
- **Route motor:** holds sneak to go down inside a scaffolding column.
- **Build runner:** towers are scaffolding first (`_tower`), and the standing towers are taken
  down and re-used for the next one (`_reclaim`).

## Known gaps (watch for these live)

- A pillar jumped up on scaffolding is only scaffolding at its first block: the next block
  would be clicked onto scaffolding, and the redirect sends it sideways. Use the side-face
  click (goes up the column) or a sneak click to keep the whole pillar scaffolding.
- Sneak descent, landing on a top plate, and the bridge redirect are covered by unit tests
  only, not yet seen in a live run.
- The planner does not yet choose a tower base to reach a set of cells, as a person does for a
  roof or a row of windows ("one tower, work all around it").

## The live obstacle course

`scripts/check-companion-scaffold-course.py [--origin X Y Z]` (battery check `scaffold-course`).
A hole 11 wide (x), 3 long (z), 4 deep is cut 3 cells east of a flat spawn spot (default
990,80,988); its air cells are `protected_positions`, so the bot can neither pillar in it nor
climb out. A stone stair on the hole's z+1 row lets it walk down to pick up drops. The bot holds
64 scaffolding and three tools, no dirt or cobblestone and `material_allowlist=()`, so any solid
temporary block is a failure. Every placed cell's block id is read back; the course fails on
any that is not `minecraft:scaffolding`. The towers stand on the rims 12 apart, so every span
cell is within distance 6 of one tower. Each leg has its own pass/fail in `summary.json`; a
failed leg skips the rest, but the take-down and restore still run.

| Leg | Goal | Asserted |
|---|---|---|
| 0 walk through | level through 3 ground scaffolding cells between stone walls | no `CROUCHING` pose in any sampled state; at least 3 samples inside the cells |
| 1 up | stance 5 above the ground at tower A | on ground there; all 5 tower cells scaffolding |
| 2 out | stance 4 out over the hole at the top | standing on a scaffolding cell over the hole |
| 3 up again | stance 2 higher at the bridge end | the 3 cells above the bridge are scaffolding (inherits distance) |
| 4 second tower | top of tower B on the east rim | bot reached the ground (sneak-descended: a `CROUCHING` sample), all 5 B cells scaffolding |
| 5 bridge across | stances over the hole from B until the span closes | all 11 span cells scaffolding; rim distance 12 |
| 6 walk the bridge | B top to A top | the sampled y never drops below stance-1 (it did not go round on the ground) |
| 7 take down | descend, `build_runner._clean_up` with an empty template, collect | no scaffolding left among the placed cells, inventory back to 64 exactly, at most 6 breaks (roots only, the rest falls with them) |

Pose and height are sampled from `/state` (the body observation exposes `pose`), not per tick;
the motor's own controls are not recorded. The inventory count has no tolerance: the bot can
reach every drop, so a shortfall is a real failure. The pure parts (geometry, verdict, pose and
height checks) are `src/mclab/scaffold_course.py`, tested in `tests/test_scaffold_course.py`;
`RoutePlannerTest` mirrors legs 1-5 and the sneak-down move on synthetic maps. Not yet seen
live as of this writing.

### Pass bar: no delays, caps or loops

After the legs the script closes the bridge and analyses `events.sqlite3`; `summary.json`
holds the results.

- **Search caps:** `mclab.search_caps.cap_hits`/`verdict`; any near hit fails the course.
- **Standstills:** `scripts/analysis/idle_gaps.py --min-seconds 3`; an unexplained stand over
  about 3 s fails the leg whose tick range it overlaps (a stall in no leg fails the course;
  a recording that cannot prove it, exit 2, also fails).
- **Timing:** each leg records `wall_s`, `ticks`, `plans` and `max_expanded`.
- **Ground-layer loop:** each leg counts planner placements and breaks
  (`CompanionBody.edits_placed`/`edits_broken`, every placement, including a cell placed again).
  A leg fails if it places more than twice its minimum (0, 5, 4, 2, 5, 7, 0, 0 for legs 0-7)
  or breaks anything before leg 7.
