# Inspecting a build

`scripts/run-build.py` now supports a reference copy and a failure walkthrough.
These are operator aids in the development lab; the companion cannot kit blocks.

For a new run, append `--preview --inspect` to the existing build command.
After construction ends, the lab places the reference copy beside the attempt
and keeps the session open. Join `127.0.0.1:25575` to inspect it. Create a file
named `STOP` in the printed results folder to finish and restore the lab world.
Interrupting the script also unwinds its normal cleanup.

For an existing failed run, use its saved checkpoint to skip gathering:

```powershell
rtk proxy uv run --locked python scripts/run-build.py --resume results/companion/<run>/checkpoints/final.json --stage --preview
```

`--stage` restores the attempt and waits for `STOP` without running construction.
Omit `--stage` to retry construction; add `--inspect` to hold the result afterward.

The default reference displacement is the rotated template's X width plus five
blocks. `--preview-offset X Y Z` supplies a different displacement relative to
the attempt's origin. Overlap with template cells or the body is rejected.
Every template cell, including explicit air, is copied in batches of 64.
The preview uses exact states without normal placement checks or neighbour
updates, like checkpoint restore: it is a target reference, not proof that the
builder can construct it. It may replace terrain at the reference location.
Normal runs place it after the job so gathering cannot mine its materials.
`preview.json` records the offset and labels it diagnostic-only.

After a build returns a report, its results folder also contains:

- `walkthrough.md`: every failed attempt's target, desired block state, reason,
  actual stance, observer `/tp @s` command, and full available placement diagnosis.
- `walkthrough.json`: the same records for analysis tools. Repeated attempts stay
  separate; a recorded stance may now be occupied by the finished structure, so
  use spectator mode when following the walkthrough.
- `build.json`: existing failures and operations, plus effective movement and
  terrain policies. Navigation entries reference `policies` by `policy_id` so a
  large protected build footprint is stored once per distinct configuration.

Without `--inspect`, a seeded run restores the original lab world when it exits;
use checkpoint staging to inspect that attempt later. Existing crash recording
still writes `error.txt` and a failing summary row. A crash before a build report
exists does not fabricate a failure walkthrough.

## Site preparation (`--margin`)

Before any template cell is built, `run_build` prepares the ground
(`src/mclab/site_prep.py`): the transformed template's footprint (after rotation and
mirroring) plus `--margin` blocks on every horizontal side. Default 1; 0 prepares the
footprint only; any non-negative integer is accepted (negative input is rejected).
Finished ground is `template_min_y - 1`, the block row under the schematic's lowest
layer. Trees (`clear_trees_in_footprint`) and everything above that level are cleared,
except cells the template wants filled and protected cells (supply containers);
low spots, holes and water at or below it are filled with dirt until a solid column
supports it (at most 8 deep). Edits go through the normal body actions.

```
rtk proxy uv run --locked python scripts/run-build.py --build <build.json> --margin 2 ...
```

`build_job(..., site_margin=N)` takes the same value (`None` skips preparation).
The result is `report.site` in `build.json` (stop, area, cleared, filled, blocked,
unknown, needed). The build stops with `stop="site_not_ready"` and an actionable
`stall_reason` when the site is `blocked`, `unknown`, `need_materials`, `max_steps` or
`max_rounds`; it never counts those as success.

## Progress watchdog

Recovery events distinguish `not_tried`, `failed`, and `succeeded`. A successful
escape resets the consecutive-stall counters; failed walks at the same position
remain bounded even when their targets differ. Native route rejection events
(`native_route_edge_rejected`) include the edge, reason, position, and tick. A
target rejected as unknown or changed is excluded from the next search rather
than immediately retried with the old preferred route.

Shallow water with solid support and dry headroom uses the actual feet height
and does not enable swimming. Deeper water may use swimming and a surface start.
Run `rtk proxy uv run --locked python scripts/check-companion-wading.py` on the
idle lab for the unscored natural-pond wading and bank-exit regression. Its
evidence goes under `results/wading`; normal cleanup restores the lab world.
The separate `check-hill-navigation.py --kind water --variants 2 --swim
--collision-shapes --control-profile dev-native-route-v1` checks deeper water and
low-air recovery in disposable fixtures. These checks are development evidence,
not qualification results.

Add `--trapped` to `check-companion-wading.py` to walk from the shallow shelf at
`(-17, 62, 71)` through the tall-seagrass column that trapped the large-house run
`20261111T113743970757Z`, then return through shallow water to the bank. This is
movement through the natural pond, without teleporting into unsupported water.
The deep-water leg explicitly enables swimming; dry-to-wet permission recovery
also has a controller regression in `tests/test_companion_body.py`.
The motor and planner both admit known, collision-free source water containing
seagrass; flowing water, solid waterlogged blocks and hazards remain excluded.
A companion walk that enters unsupported water enables swimming for recovery,
even when it began on dry land. Supported shallow wading keeps swimming disabled.
Final swimming waypoints must reach within 0.25 blocks of the requested height;
the one-block bobbing allowance applies only to intermediate waypoints. Stopping
jump a block early left the body sinking below an already completed route. A
verified surface arrival can finish afloat with eyes out of water, without ground
contact, while preserving the ordinary goal position and height checks.
Parked probes normally use a 2.5-second soft deadline. If the native planner map
is still uploading, the probe lets that generation finish within the shared
five-second inactivity limit. Cancelling a nearly complete upload forces the
following walk to resend it; a committed generation can instead be reused.

The navigation idle clock is shared across successive planner probes and walks;
starting another probe cannot buy another five seconds of standing still. Actual
block edits reset it, and an alert gives recovery one new bounded opportunity.

Companion navigation checks wall time inside the live observation loop. Five
seconds without half a block of displacement emits `STALL ... navigation idle`
with the position, planner mode and native route report, and ends that navigation
attempt for bounded recovery. An issued, following native edit resets the timer;
a queued step alone does not. This catches an idle motor while `walk_to` is still
blocked, rather than waiting for its older simulation-tick timeout to return.

Site preparation clears vines and audits the delivered area without its skip
filter before reporting `ready`. Remaining edits, protected obstructions, or
unknown cells keep the readiness gate closed.

During shallow-water travel, the native motor advances waypoints when feet match
the verified supported height, even if buoyancy has unset `onGround`. Requiring
that flag caused lookahead to pass a waypoint and repeatedly turn back to it.
Site filling first reaches the working surface and then disables drops while
walking its rim. Failed placement stances remain refused until an edit changes
the world, and four approaches without a placement stop the sweep. Newly filled
ground is included in stand-height selection and refreshed before route planning.
Fill columns are strictly bottom-up: a refused lower cell cannot be capped by
a successful placement above it. Otherwise the lower hole becomes inaccessible.
Run `rtk proxy uv run --locked python scripts/check-site-fill.py` on the idle lab
to exercise this sweep in the original natural pond. Evidence is retained under
`results/site-fill`, including failed attempts; cleanup restores the lab. This
development fixture clears the volume above ground before filling, to isolate
the production fill phase after site clearance; its pond floor remains natural.

`src/mclab/progress_watch.py` wraps the body during `run_build`. Working ops (stance,
walk, place, break, use) that make no progress (no success, feet moved < 0.75) for
more than 5 s log `STALL ...`, try `escape_hole` (which first equips fill material
onto the hotbar: the planner prices pillar edits from hotbar stock only), and after
3 consecutive stalls stop with `stop="stalled"`. Events are in `report.stalls`.

Drop collection checks the snapped walking height as well as the item's height.
A falling log near the body must not become a walking destination on the canopy
four or more blocks above it. Both gather pickup paths use `walk_to_drop`; rises
or height snaps over two blocks are skipped until a reachable drop is available.

Companion navigation also ends a bounded search that returns `incomplete` with
no usable move. Previously every incoming map revision could launch another
4,000-node search at the same feet position. The job now tries another stance;
incomplete routes that contain usable movement remain valid partial routes.

Site preparation clears natural foliage in future template cells. Low leaves above fill columns are removed before filling, since a leaf can roof a hole and occlude every useful placement face. One bounded retry revisits early no-stance failures after the surrounding ground is complete. The no-drop fill policy applies at the working height; a body returning from a higher dirt-borrowing hill must first be allowed to descend.

An open trapdoor can leave a clear stance centre while blocking the approach with its thin vertical panel. The native planner now checks the motor swept-hitbox rule for land edges involving partial collision geometry. The small-house regression reproduced repeated planner-approved ascents that the motor correctly refused; the planner must choose an unobstructed approach instead.

Scaffold climbing has an isolated live regression:
`uv run --locked python scripts/check-companion-scaffold.py`. It uses `allow_climb`
with optional extra moves disabled, places a three-block column, requires arrival
standing on the top platform, and restores every changed fixture cell. Climbing
permission is independent of optional edit/water-landing moves. Both geometry
layers admit the scaffold top collision surface; building stances are above the
column rather than suspended inside it. The native planner includes the vertical
exit onto that platform, and the final vertical climb centres to the ordinary
land-arrival radius.

The same live check exercises an off-centre body just outside a two-block goal
radius, while its rounded cell centre is already inside it. Companion position
plans reserve the motor's 0.2-block arrival tolerance so that a stationary
`reached` plan cannot prevent the body from moving inside the actual goal radius.
Interaction goals and generic navigation controllers retain their existing rules.

Scaffold cleanup can reopen a finished house's door. After cleanup, the builder
repairs remaining toggle states with automatic door opening disabled, then audits
the delivered world again. A failed repair changes `stop` from `done` to `blocked`;
success requires an empty final diff after all cleanup movement.
