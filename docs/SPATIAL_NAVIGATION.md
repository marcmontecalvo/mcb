# Development spatial memory and segmented navigation

The first integration adds a durable SQLite map, a bounded active map, resumable
A* search and a separate motor adapter. It is an unqualified development entrant.
Existing V8 controllers and scored profiles retain their original behavior.

Run the flat-lab integration with the installed awareness mod and an idle server:

```powershell
rtk proxy uv run --locked python scripts/check-spatial-navigation.py
```

The probe uses ordinary Survival movement in the lab, writes raw events and a
map under `results/spatial/<timestamp>/`, reopens the archive to check recall,
then restores initial C1 and despawns. Its route is flat; it does not demonstrate
natural terrain, block use or Baritone parity. Failed attempts remain on disk.

The natural-world runner accepts `--entrant segmented_goal`, now with the
[hill terrain policy](HILL_NAVIGATION.md). Supply the
existing frozen routes JSON and optionally an exported awareness profile, as
described in [awareness modes](AWARENESS_MODES.md). Each restored attempt receives
an isolated map generation and `map.sqlite3` in its result folder. Cross-attempt
learning is deliberately excluded from that benchmark. The default entrant and
the meaning of `--entrant both` remain V8 and the existing V8/Baritone pair.

## Storage and recall contract

`MapIdentity` requires an explicit world/save identity, reset generation,
dimension and exact awareness configuration. Seed equality is insufficient.
Callers must allocate a new generation after restoring/resetting a save and a
unique observation session after restarting a controller process. Partitions
cannot recall one another's facts, including broad-to-restricted profiles.

`SpatialMemory.ingest()` accepts admitted geometry, owns its nested data, updates
a bounded LRU working map and queues writes without disk access. The defaults
are 20,000 active cells and 16 pending batches, with an additional 128 MiB
accounted cell-data budget and 128 MiB persistence-data budget. The worker coalesces repeated
cell updates and commits whole batches in WAL transactions. Explicit air replaces
old blocks; absent cells remain unknown. Each cell retains acquisition tick,
wall-clock timestamp and observation session. Entities are not persisted.

The background controller sends the driver's immutable parsed observations to a
separate admission worker (two waiting samples maximum, plus the active sample).
It owns/copies facts, accounts their size, updates the hot map and queues durable
writes. Distinct samples cannot be silently dropped: admission overflow fails
closed. Published planning views are versioned and owned; later admission replaces
records rather than mutating published ones. The driver must not mutate a
submitted observation. `SpatialMemory.ingest()` itself retains its synchronous
ownership contract for other callers.

Motor safety uses the current native sample with its original observation ticks,
separate from the admission worker's historical map. Missing support remains
unknown. The sensor-sample buffer is additionally bounded by the native scan
limit; it is separate from accounted hot-map and persistence budgets. The motor
does not wait for an unfinished map admission or a disk transaction. Worker
errors surface before further decisions; shutdown drains admission before the
spatial writer is flushed. Live performance still requires cohort acceptance.

`flush()`/`close()` establish durability with a deadline. An unflushed queue is
not durable. Queue overflow or writer failure rejects further policy access;
flushing/closing marks that namespace untrusted. Recovery requires a new map
generation and rescan. A crash cannot partially commit a SQLite batch; uncommitted
observations are lost. The archive makes no complete-coverage claim.

`recall(center, radius, height)` is an explicit bounded disk operation outside the
motor renewal loop. It restores nearby admitted historical facts into active
memory, without making them fresh. `snapshot()` supplies an independent planning
view. `fresh(tick)` only returns cells from the current observation session and
the permitted age window. Durable history alone cannot authorize movement.

The archive now stores sparse 16-cube palette sections compressed with zlib.
Per-cell observation provenance survives compression. Existing schema-one cell
archives migrate transactionally to schema two, across all namespaces; coverage
gaps survive migration. Partial updates merge within a transaction, including
explicit air, without deleting untouched neighbors. Independent writers serialize
the read/merge/write transaction. Failed migration preserves the old cell table.
Recall decodes one section at a time and retains only the requested nearest cells.
Compression runs on the persistence worker, outside motor renewal.

Development natural/hill runs can now use `sections-v1` transport: a sparse set
of section-local cell indices, a geometry palette and per-cell observation ticks.
The server captures immutable facts on its tick thread; palette construction is
lazy and cached on the HTTP serialization thread. Historical event batches retain
compact rows after serialization instead of expanded cell maps. The Python driver
validates bounds, sample epoch, duplicates, collision facts and declared cell count
before exposing expanded facts. Missing cells remain unknown. It records the
compact wire representation and measures decoding separately from motor decisions.
Malformed or incomplete samples stop admission rather than fabricating coverage.

`scripts/check-hill-navigation.py` defaults to this format, with
`--terrain-encoding cells` available for comparison. Natural `segmented_goal` runs
use it when the selected awareness profile is non-legacy. Other entrants and
legacy observations retain the cell-list format. These compact samples still
represent incremental observations, not complete sections or transport deltas.
Byte-budgeted deltas and coarse region/connection graphs remain pending.
Cell data is charged using Python object sizes, including nested facts and an
index-entry allowance. Repeated native geometry uses cached charges; positions
and observation provenance are charged separately. Shared facts are charged more
than once, so this is conservative cell-data accounting, not a process-RSS limit.
The persistence charge includes batches already taken by the writer, until their
transaction finishes. Byte overflow fails closed just like queue overflow.
SQLite pages, allocator arenas and other controller data require separate process
measurements. Recall latency and database size still need larger-world evidence.

The optional `--memory-profile` JSON on `natural run` (segmented entrant only)
and `scripts/check-hill-navigation.py` has these exact fields:

```json
{
  "memory_mode": "persistent",
  "use_historical_geometry": true,
  "active_map_budget_mb": 128,
  "persistence_queue_budget_mb": 128
}
```

`current` keeps policy-visible geometry within the fresh five-tick window and
current configured reach. `session` retains this controller's admitted geometry
in the bounded hot map. Both modes skip durable observation writes and reject
archive recall. `persistent` writes admitted facts and permits explicit historical
recall. Turning `use_historical_geometry` off restricts both planning views to
fresh, in-range facts and rejects historical recall, including after displacement.
Every mode still requires fresh observations for movement. Mode/access changes
require a fresh memory/controller instance; there is no mutable in-place switch
that could retain an obsolete route. Automatic background recall remains pending.

## Planner and motor boundary

`RouteSearch` searches an independent observed-map snapshot. Each `advance()`
has a node and time budget and returns at most one bounded route segment plus
its map revision, expansion count and explicit status. It checks support and
clearance as edges are expanded instead of rebuilding all standing nodes first.
Unknown support/clearance cannot form an edge. Search can take detours that
initially move away from the destination.

`SegmentedController` supplies cancel/resume/replace-goal/status operations,
retains route segments and feeds waypoints to the existing fresh-geometry motor.
It checks local geometry again before controls, drops obstructed segments and
resumes search. It has no unconditional direct-goal digging recovery. Cancel
returns neutral controls; the driver remains responsible for sending them and
retains the existing action expiry/watchdog contract.

This version slices planning in the driver thread. Next-segment prefetch,
background search and a global place/connection graph are pending. Traversal
covers ordinary full-cube ground and single steps; the hill adapter adds bounded
confirmed cuts and footholds.
The live smoke establishes integration; focused tests establish restart recall,
detour search, partition isolation, bounded memory, overflow behavior and stale
history rejection. These are separate evidence scopes.
