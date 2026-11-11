# Dashboard build metrics review

The house-build view uses `build.json` for outcome, ticks, cell counts, steps,
site results, stalls and recoveries, failures, refills, shortfall, gather stop,
and supply state. In its absence it uses progress and operator marker files.
`launch.json` or the first checkpoint supplies build identity, seed and start;
an unknown identity is never automatically paired. The streamed event scan
provides elapsed time, estimated phase boundaries, estimated house placements
and rate, all successful placements and breaks, failure reasons, plan statuses,
maximum expansions and the longest interval without a successful edit. The
classification is heuristic: dirt and stations in a template are excluded, and
the first other placement can be mistaken for a house block. A log can remain
active during inspection. Neither log freshness nor a completed result proves
that inspection is waiting for STOP. Explicit producer phase and inspection
markers remain open work. Incomplete routes can mean unknown terrain as well as
a search cap. Old cache versions with pruned source logs cannot be recomputed.

The separate navigation comparison derives these keys from summary attempts:
`ticks`, `wall_seconds`, `damage`, `broken`, `placed`, `confirmed_places`,
`confirmed_breaks`, `blocks_spent`, `kit_estimate`, `died`, `disconnected`,
`minimum_air`, `water_ticks`, `hostile_ticks`, `peak_hostiles`, and
`goal_distance`. Aggregates include totals, damage mean and worst, median
successful ticks and wall time, minimum air, peak hostiles and minimum goal
distance. It also presents outcomes, expected attempts, failure reasons,
settings and per-attempt values. Missing values remain missing. Companion-only
comparisons suppress navigation metric rows with no samples; house-build
comparisons use the dedicated view.

Event log names are centralized in `build_stats.LOG_NAMES`; add actual split
log names only when their producer schema exists. The scanner reads plain or
gzip logs sequentially and caches stable signatures. It does not scan logs
written in the preceding 60 seconds; this is a freshness heuristic, not process
liveness. The dashboard refreshes build rows during normal polling and while
scans are pending. It cannot recover a metric after its log was pruned before
a compatible cache was made.
