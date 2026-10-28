# Development hill ascent

> **Partly superseded (Oct 28, 2026):** "Known walking first, terrain forecast only for unresolved obstacles" is legacy as the route chooser. The executors, confirmations and fixtures below remain valid. See [NAVIGATION_DIRECTION_2026-10-28.md](NAVIGATION_DIRECTION_2026-10-28.md).

The segmented controller executes cut-step and placed-foothold plans using normal
Survival inputs. The original single-step chooser has been replaced by
[rolling four-move forecasts](ROLLING_TERRAIN_PLANNING.md), including downstream
edits and resource needs. Known walking search runs first; unresolved obstacles
trigger bounded terrain forecasts. Each actual step checks fresh support,
headroom, tools and materials. This is not a global search over arbitrary edits.

The [current proof](AWARENESS_MOVEMENT_PROOF.json) records ten successful live
layouts for each family and the source-bound legacy regression. See the
[results](AWARENESS_MOVEMENT_RESULTS.md) for timings and limits.

`StepExecutor` aims at the planned block, validates the live ray and reach,
selects the hotbar tool/material, and mines or pulses use. It waits for freshly
observed geometry after every operation. Placement also requires the selected
stack to decrease by exactly one. Missing observations do not establish success.
No movement onto predicted footholds occurs before confirmation. Failure has a
tick deadline and excludes the failed edge from subsequent local edit choices.

The allowed cuts are currently stone, cobblestone, dirt and grass blocks, with
the corresponding iron tool in the hotbar. Unknown adjacent geometry, fluids,
sand and gravel reject an excavation. Placement currently uses dirt or
cobblestone on the upper face of a known full ground block. This is an initial
hill-footing capability, not general building, bridging or arbitrary placement.

## Controls

`natural run --entrant segmented_goal` uses these defaults. Pass
`--terrain-profile <file.json>` to resolve a different development policy:

```json
{
  "allow_break": true,
  "allow_place": true,
  "break_budget": 8,
  "place_budget": 8,
  "reserve_blocks": 0,
  "interaction_tick_limit": 160
}
```

Edit budgets are reserved when a step plan is admitted. Cancellation and failure
retain that reservation, since an issued interaction can finish before its
confirmation arrives. Confirmed edit counts are reported separately. Material
reserves apply to permitted hotbar building stacks. Terrain overrides are rejected for
the frozen V8/Baritone comparison entrypoints. The resolved policy is recorded
in the natural-run manifest. Changing modes or budgets requires a fresh run.

The direct Python adapter can omit `terrain_policy` to retain walking-only
behavior. Cancel releases intent by returning neutral controls; the driver must
send those controls. An interrupted step is discarded, and newly observed
geometry is required before resuming movement.

## Live fixtures

With the current mod installed, the flat lab world active and no subject running:

```powershell
rtk proxy uv run --locked python scripts/check-hill-navigation.py --variants 10
```

Run this cohort on a fresh server: it uses 30 development bodies and one final
C1 reset, within the existing 32-body lifecycle limit. `--kind walk|cut|place`
and `--variants 1..10` select smaller diagnostic cohorts. Run the new mixed family
separately with `--kind mixed --variants 10` on a fresh server.

The explicitly requested fixture occupies a bounded region near (1280,80,1280),
outside the qualified course. It snapshots original block states before setup,
rejects overlapping players and block entities, restores original states after
each attempt and verifies restoration. The runner then resets C1 and despawns.
Successful hill scoring requires the actual summit height, an on-ground subject
and horizontal arrival within one block. General natural-run scoring is unchanged.

The three families are a three-block walking staircase, a three-block face that
requires cuts, and a two-block face that requires a placed foothold. Width and
slope position vary across ten distinct layouts per family. Walking forbids both
edit types; cutting forbids placement; placement forbids cutting. The server
records changed blocks and remaining kit stock separately from controller claims.

Raw events, admitted map archives, source hashes, per-case outcomes and restoration
status are retained under `results/hills/`. Diagnostic failures remain there too.
These fixtures establish the stated mechanics; they do not establish broad
natural-terrain reliability or Baritone parity.

## Timing and remaining work

Planning reads a copied index of immutable owned cell records. Ingestion replaces
records, so a planning revision survives later updates without JSON-copying the
whole map during action renewal. Search itself remains sliced by node/time
budgets. The live hill runner uses the existing motor GC policy. Control age,
duration and expiry limits are unchanged.

Background search/prefetch, palette-compressed map sections, coarse connections,
globally costed edit routes, mixed excavation/placement hills, bridging/pillaring,
fluids, ladders and gap jumps remain later sprint work. The initial awareness
and spatial proofs retain their historical source hashes; new evidence must bind
to the installed source after this fixture addition.
