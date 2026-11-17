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
