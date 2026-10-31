# Development awareness modes

Implemented October 26, 2026 as the first awareness sprint increment. These modes
are available to the deterministic natural-terrain controller. Existing scored
C1/C2-C5 profiles still require the frozen legacy sensor.

[Verification evidence](AWARENESS_MODES_PROOF.json) records 15 native awareness
checks, the original sensor checks, one short flat-route integration run per mode,
and source-bound legacy reset/evaluator requalification. This is development
capability evidence, not a new natural-terrain performance result.

| Preset | Terrain access | Default reach |
| --- | --- | --- |
| `legacy` | 360-degree, eye-to-surface occlusion | 8 blocks horizontally, 4 vertically |
| `visible` | Same occlusion rules, configurable larger box | 32 horizontally, 16 vertically |
| `chunk` | All block geometry in the requested box, including behind walls | 32 horizontally, full dimension height |

All reach values are axis-aligned radii measured from the body's block position.
Chunk mode reads only full chunks already available on the server. It does not
load/generate remote chunks or grant whole-world access. This initial mode does
not reproduce a client's chunk subscription: `client_equivalent` is false.

Entity sensing is independent. All presets default to occluded entities within
the existing 8-horizontal/4-vertical bounding-box expansion. Larger entity range
and `unoccluded` entity access must be explicitly selected. Terrain access does
not change block interaction reach, movement physics, or control expiry.

## Configure and inspect

Commands run from the repository in PowerShell:

```powershell
rtk proxy uv run --locked mclab awareness show --preset legacy
rtk proxy uv run --locked mclab awareness show --preset visible --horizontal-radius-blocks 24 --vertical-radius-blocks 12 --output runtime/visible-24.json
rtk proxy uv run --locked mclab awareness show --preset chunk --output runtime/chunk-32.json
rtk proxy uv run --locked mclab awareness status
rtk proxy uv run --locked mclab awareness check
```

`show` resolves and validates configuration without changing the server. An
exported file contains the complete versioned configuration. `status` reads the
server's current configuration, episode revision, and acquisition progress.
`check` spawns a development body, runs native sensor fixtures, restores the
fixture, despawns, and saves evidence in `results/awareness/`. It requires the
current mod to be built/installed and the lab server running. It is not scored
qualification or a navigation benchmark.

| Flag | Accepted values |
| --- | --- |
| `--horizontal-radius-blocks` | 1-64 |
| `--vertical-radius-blocks` | 1-64; `-1` means full height in chunk mode only |
| `--entity-radius-blocks` | 1-64 |
| `--entity-vertical-radius-blocks` | 1-64 |
| `--entity-visibility` | `visible` or `unoccluded` |
| `--update-interval-ticks` | 1-20; default 5 |
| `--extra-scan-cells-per-update` | 1-8192; default 2048 |
| `--extra-scan-budget-ms` | 1-10; default 3 |

The legacy preset rejects any changed setting. Choose visible/chunk to adjust
knobs. The server independently validates schema, types, and ranges; malformed
or unknown fields are rejected before admitting a natural run.

## Use in a natural-terrain run

```powershell
rtk proxy uv run --locked mclab natural run --seed 1337 --routes results/natural/routes-1337.json --entrant v8_goal --condition calm --awareness-profile runtime/chunk-32.json
```

This command restores the selected existing natural-world snapshot between
attempts, restarts the server, and restores the original lab properties on exit.
The server is left stopped by the existing natural-world harness. Routes must
already exist; this command does not select or generate routes/world snapshots.
Without `--awareness-profile`, MCB uses legacy awareness. `--entrant both` includes
Baritone; `--condition both` includes night. MCB settings do not alter Baritone's
native perception/cache. The run manifest records the selected MCB settings.

Changing profiles requires a new episode and controller. Configuration cannot
be hot-swapped during a run. Sensor buffers, scheduled actions, and replay map
memory are cleared across awareness revisions; the goal controller rejects
unexpected settings or revisions. Frozen qualification spawn/course interfaces
reject awareness overrides. Switching back to legacy uses the original profile
and does not inherit geometry acquired by the broader run.

## Acquisition and remaining work

The new modes refresh the immediate neighborhood (up to 8 horizontal / 4 vertical
blocks) on each update. Larger regions are scanned outward from the body in
nearest-first shells, incrementally with cell and
time limits on the extra work. These are additional-work limits, not a maximum
for the entire sensor tick. Each publication contains the local refresh plus a
batch of additional observed cells; it is not a full replacement map. Air cells
are explicit. Missing cells remain unknown.

Coverage reports include cursor/volume, completed sweeps, unavailable cells,
returned cells, sample tick/time, and anchor. A completed sweep does not guarantee
full current knowledge: chunks can be unavailable, visibility can hide cells,
and the body/world can move during acquisition. `full_coverage_claimed` therefore
remains false. Large full-height requests can take many updates; status exposes
that cost instead of presenting the radius as immediately known.

The current V8 goal mover still trims its working memory to radius 20/height 12.
Broader acquisition alone does not remove that limit or fix its routing. Durable
map memory, compact chunk transport, client-subscription-equivalent access, wider
route search, and performance tuning remain sprint work. These modes do not yet
establish Baritone-equivalent awareness or improved route success.

## `sections` preset (Oct 30, 2026)

Whole-section scanner for matching Baritone's loaded-chunk knowledge
(`config/stepc/awareness-sections-64.json`: radius 64, vertical +-48, 32768
cost/update, 10 ms, every 5 ticks). Requires `terrain_encoding=sections-v1`.
Every loaded 16x16x16 section in range is sent once (nearest first; a uniform
section is one palette entry, cost 64; a mixed section is 4096 palette indices,
cost 4096), then 24 sent sections per update are re-read (stale and near first)
and re-sent only when their content changed. Unloaded chunks are never sent;
moving the body only reorders the queue. Wire keys `bulk_cells`,
`dense_sections`, `uniform_sections` are optional additions to
`sparse_section_palette`. Python ingests them as section slice assignments.
