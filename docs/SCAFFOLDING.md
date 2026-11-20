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
  body holds it and the cell's distance would be 6 or less; an edit that would need a
  non-pillar step-up from a scaffolding floor onto scaffolding-over-scaffolding is rejected.
  The rest are solid, priced with `SOLID_PLACE = 200` so dirt/stone stays a last resort rather
  than creating floating dirt floors or `[dirt, scaffolding]` stacks. The edit carries
  `scaffold: [x,y,z,distance]` for the motor.
- **Step motor:** places scaffolding for those cells, takes a scaffolding floor as support,
  extends horizontal bridges off scaffolding with aligned plain clicks (`redirectAim`), stacks
  pillars on scaffolding with sneaking clicks, and never sneaks while on scaffolding.
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
64 scaffolding, 64 dirt, and three tools, with `material_allowlist=("minecraft:dirt",)` so dirt is
permitted as solid fallback, while `foreign(placed)` still fails on any placed block that is not
`minecraft:scaffolding`. Every placed cell's block id is read back; the course fails on any
dirt or other non-scaffolding block (and leg 7 checks both scaffolding and dirt remain at 64).
The towers stand on the rims 12 apart, so every span cell is within distance 6 of one tower.
Each leg has its own pass/fail in `summary.json`; a failed leg skips the rest, but the take-down
and restore still run.

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

## Scaffold challenges (bot or a person)

`scripts/check-scaffold-challenges.py` (battery check `scaffold-challenges`, slow, skipped by
`--fast`) lays one course at a time at `--origin` (a flat, empty lab spot; default the lab
fixture, 990 80 988), runs it, scores it and clears the spot again. `--list` prints them;
pick by name or `--level basic|advanced|all`.

| Level | Name | What it asks |
|---|---|---|
| basic | `climb` | onto a stone ledge five up |
| basic | `descend` | to the floor of a shaft ten deep, no damage (drop scaffolding down, stack it up, climb down) |
| basic | `reach-out` | stand two up and four out over a pit (tower, then extend sideways) |
| advanced | `bridge` | straight bridge (root + 5, 8 scaffolding) to an island in a fenced, roofed pit 10 deep, no falling in |
| advanced | `bridge-l` | the island is off every line from the pad: one turn (root + 4, 7 scaffolding) |
| advanced | `bridge-u` | a glass wall stands in front of the island: two turns round it (root + 5, 8 scaffolding) |
| advanced | `stairs` | upstairs in a two-storey house with a solid ceiling: take the stairs; any scaffolding fails it |

The geometry is pure (`src/mclab/scaffold_challenges.py`). Tests check:
- each course needs scaffolding (walking alone cannot reach the goal; the stairs course is
  walkable);
- the bridges need 0, 1 and 2 turns within scaffold distance 6;
- every pit is walled and floored in laid stone;
- the bridge courses are closed under a glass roof at y=4 on fences three high (no tower
  onto a fence to walk along it), and their pit is deeper than the scaffolding handed out
  (no column down to the floor and up again at the island);
- the roof leaves the step from the pad onto the bridge root its jump room (launch cell
  clear to feet+3, as `RoutePlanner.planEdit` and StepMotor need). A roof at y=3 kept the
  bot on the pad in the first live run (20261120T173256582204Z).

`descend` asks for what the planner cannot do yet: scaffolding placed over a shaft falls and
stacks up from the floor, and the bot has no edit for that (it hangs a bridge out and stops;
first live run: 3 placed, search cap toward the floor). It stays a course: it is the test for
that work (ROADMAP B2a).

The kit is scaffolding alone (it breaks by hand; a pickaxe would dig out of a pit or quarry
stone to pillar with). Any changed course cell fails an attempt. A bridge course also needs
the bridge left standing: the open cells are read back and scaffolding must join, face to
face, a root on the pad to the island's side (`bridge_stands`). A sprint jump from the pad's
corner reaches the bridge-l island with no scaffolding, and bridge-u's one-turn L leaves a
gap a person can jump (review of e029b74); a jump leaves no bridge, or one with a gap. The lab kit reads the cells
back (`probe`), so a person's own scaffolding, placed or broken, does not count. A pit
13 wide crossed by dropping scaffolding onto its floor to stack a middle tower is not a
course yet: the walk down and up again cannot be ruled out, and the planner does not drop
columns (ROADMAP).

- **Bot:** `--subject bot` (default). The bot spawns at the start with the scaffolding and
  navigates to the goal with placing allowed (every course cell is protected). Add
  `--show-markers` to see its red planned path, green goal and yellow block; create or
  delete `<run folder>/<challenge>/MARKERS` to flip them while it runs.
- **Person:** join the lab server with a normal client, then
  `--subject human --player <name> [--minutes 10]`. You are moved to the start with only
  the scaffolding; the green ring and beam mark the goal and the attempt ends when you stand
  on it. It is stopped and the spot restored even if the script fails part way. Results:
  `results/scaffolding/<stamp>/summary.json` (reached, damage, scaffolding used, course
  cells changed).
