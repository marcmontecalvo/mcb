# Java migration handoff — November 3, 2026

The Terra agents hit their usage limit during implementation; Marc reset the
limits and both resumed. This checkpoint records preserved work, not a stop request.
The work is unfinished, unreviewed and not installed. Do not label the migration complete or
remove production selection until the missing parity/integration is implemented.

## Isolation and source

- Original lab checkout: `D:\mcb`, main `1fcbf68`, with the preserved debugging
  and common-body changes from the prior task still uncommitted there.
- Migration checkout: `D:\mcb-java-migration`, branch `java-planner-migration`.
- Frozen copy of the original dirty working tree: commit `b994dea` on this branch.
  All later migration changes are currently uncommitted.
- Installed baseline mod source: `ea9738034a63995e5f0dd3d1cfa3bbc70a7711b3cd561ffd320c5bb218678992`.
  The agents did not install migration jars or change the original code.

## Implemented provisionally

See `JAVA_MIGRATION_INVENTORY.md` for path-by-path status. The provisional changes
include Java interaction goals, visibility/range checks, survival costs, some
policy ablations, explicit climbing permission, per-route door/vine permissions,
immutable palette/section planning maps and bounded map-delta transport.

`TerrainPlanner.java` ports the separate legacy terrain beam with staircase and
pillar macros, branch overlays, stock, protections and exclusions. Its dedicated
verification and the reproducible `scripts/analysis/terrain_java_parity.py`
comparison passed 30 generated cases on status, expansion count, route and
placement order. The isolated JDK 25 Gradle build compiled the provisional classes.
These are focused offline checks, not full migration qualification.

## Required remaining work

1. Finish all policy/shape/search/cost ablation parity and the new ordinary
   RoutePlanner differential harness (second agent stopped during this task).
2. Integrate immutable map snapshots with current/session/persistent memory and
   historical access settings; finish generation acknowledgement, cancellation,
   failure recovery, resource bounds and backpressure tests.
3. Integrate the legacy terrain planner protocol and preserve every supported
   macro/stock/budget behavior, including planner_edits disabled.
4. Complete request validation, route-local policy transitions, precise stance
   arrival and all production caller paths.
5. Only then remove production Python RouteSearch/TerrainRouteSearch fallback and
   the associated runtime selection. Keep any offline oracle explicitly separate.
   Do not remove supported features or turn disabled flags on to avoid porting.
6. Run full Python, Java/Gradle and meaningful differential tests. Obtain separate
   independent Java and Python/code reviews; fix findings before installation.
7. Integrate without losing the frozen original changes, install final reviewed
   source only when the baseline lab is idle, requalify and repeat paired inputs.

Do not commit `.local-planner-classes/` or machine-local bootstrap/verification
scripts as product source. Review new/untracked files and build registrations.

## Baseline operation (owned by root)

Exact frozen inputs, hashes and commands are in the original checkout's
`runtime/java-migration-baseline-manifest.json`. Durable collected outputs are in
`results/java_migration/20261103T125214Z-baseline/`.

- Action/navigation interaction: 14/14, companion run `20261103T125214536980Z`.
- Iron gathering seed 20261026: 0/2, run `20261103T125223144627Z`; no_stance at
  the known hole/crafting-table problem; preserved as pre-existing failure.
- Supplied-material small house: 4/4, run `20261103T130200247759Z`; all 84 blocks
  correct, empty final diff, 86 steps and 14,014 build ticks.
- First qualification attempt `20261103T131427864439Z-regression`: awareness
  passed, initial resets invalid because the action fixture left a chest at
  `[995,80,990]`. The exact chest was removed with a recorded lab kit operation,
  and the failed logs were archived as `baseline-more-attempt1*.log`.
- Qualification retry and matched calm/night navigation were relaunched in root;
  retry awareness 15/15, initial resets 20/20, adversarial 29/29 and C2/C3 reset
  cohorts (20 each) passed; C4 is running as of the latest handoff update.
  Queue PID 89528; collector PID 101488. The collector refreshes the durable
  baseline aggregate automatically.
  inspect `runtime/java-migration-baseline-more*.log` and actual process state.
  Never run another live job concurrently or install new jars during that queue.

The comparison matrix is source-bound development evidence. No qualification
ledger has been promoted and no post-migration baselines have been run.
