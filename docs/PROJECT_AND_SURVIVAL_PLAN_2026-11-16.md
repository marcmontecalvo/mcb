# Project layer (B4) and night shelter / death recovery (B5) — plan, Nov 16, 2026

Status: plan. Open questions at the end need a decision before B4 step 6 and B5 steps 3–6.

## What exists
- Per-world terrain archive and `PlaceGraph` (`companion_body.py` ~1576–1650, `place_graph.py` 74–90); `place_block`
  records crafting_table/furnace/chest/barrel places (`RECORDED_BLOCKS`), chests without contents.
- `GatherState.containers {pos: {item: n}}` (`gather_runner.py` 122–136) is in memory only; `gather_storage.py`
  uses one container and does not model capacity; `MAX_REFILLS = 40` counts refill calls.
- Build checkpoints restore world+inventory for tests; they do not record a project, phase or chests.
- Nightfall protocol decided Nov 7 (`ROADMAP_HISTORY_2026-11-15.md` ~695–702): dig/seal a 1×2 cell and wait for dawn;
  no combat, no reactive flight. Missing mechanic: an in-place seal edge (break y+2 above the stance).
- No death/respawn/time-of-day handling in `src/mclab`; the mod exposes `health` and `food`.

## B4 steps (one commit each, offline test each)
1. `containers` table beside `places`; hydrate/flush `GatherState.containers`. Test: round trip across reopen.
2. Flush on peek/deposit/withdraw. Test: a new state after a fake gather knows the same chests.
3. Capacity (27 slots) and first-fit across a base chest cluster near `home`; place a new chest when full.
4. `sightings(item,pos,seen_ns,count)` written by the gather scan, read before exploring.
5. `projects` table: site, template, needed, phase (gather|build|done), progress; phases move forward only.
6. `build_job` creates/resumes the project; resume skips finished phases and re-plans from inventory+chests.
7. `/mcb status|build` report the active project.
Live check: kept world, `/mcb build`, kill mid-gather, restart, same project resumes with chests remembered.

## B5 steps
1. World time in the observation; `is_night` (12542–23460).
2. Shelter trigger between tasks in gather and build loops; resume at dawn.
3. In-place seal edge in the planner/StepMotor (offline route probe, cave and open ground).
4. Bed: record `bed` places; sleep via `use_block`; else the sealed cell.
5. Death detection, respawn, death position + inventory on the project record.
6. Return, pick up drops, resume the stored phase (needs B4 step 6).
Live check: start near dusk, shelter undamaged to dawn, resume; second run with an operator `/kill` mid-job.

## Open questions
- Project survives a world reset? Proposed: no, keyed by world namespace.
- Capacity: 27 slots per chest; do double chests/barrels count?
- One active project per world?
- Bed if one exists, else sealed cell?
- Does the mod respawn the Carpet body or must Python respawn it?
- Death recovery without a retry cap (per "no artificial limits")?
- Assume keepInventory off (drops on the ground)?
