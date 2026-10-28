# Control latency follow-up: October 27, 2026

Resumed at Marc's request from [the pause handoff](PAUSE_HANDOFF_2026-10-27.md).
Subsequent movement and comparison work is tracked in
[the movement follow-up](MOVEMENT_ENGINE_FOLLOWUP_2026-10-27.md).
The full [awareness/movement sprint](AWARENESS_MOVEMENT_SPRINT.md) remains
unfinished. This increment addresses processing latency on existing development
fixtures. It introduces no new traversal family or natural-terrain comparison.

## Changes

- Archive updates merge section palette indices and observation provenance
  directly, avoiding expansion and deep copying of unchanged stored geometry.
  Each write removes unused palette entries. Schema two, transactional migration,
  namespace isolation, explicit air/unknown handling and independent public
  decode semantics remain in place.
- The compact decoder retains its validated position index as an internal
  read-only attribute. The background motor borrows this immutable index instead
  of repeating position validation and indexing. Persistent admission still owns
  its copies. Every motor use checks cell age against the current tick; replacing
  a sample replaces the index. Plain cell-list observations retain validation.
- Optional `--trace-processing` events measure the full observation path,
  decision plus selector recording, control submission and acknowledgement wait.
  Motor events also record diagnostic thread CPU samples.
- `--thread-switch-ms` explicitly scopes a scheduling experiment and restores
  the caller's interpreter setting even on failure. The default preserves the
  existing setting. The 1 ms trial did not improve latency. No process worker
  was implemented and no control age, duration, expiry or watchdog was relaxed.

## Measurements and retained failures

All folders below are under `results/hills/`; each contains `summary.json`,
`events.jsonl` and a derived `performance.json`. Tracked summary/performance
receipts live in [the evidence directory](evidence/2026-10-27-awareness-navigation).
Raw logs, archives and runtime files remain ignored locally. Receipts retain
source/configuration identities and hashes of the summary and raw events.

| Folder suffix after `20261027T` | Scope | Result | Motor p95 ms | Decode + motor p95 ms |
| --- | --- | --- | --- | --- |
| `162317922824Z` | Unmodified resume baseline | Failed first layout; restored | 4.18 | 12.27 |
| `162537422269Z` | Full-path timing added | 1/1; restored | 21.87 | 27.66 |
| `162616002624Z` | Invasive cProfile diagnosis | Failed; restored | Diagnostic only | Diagnostic only |
| `162721329805Z` | 1 ms thread-switch interval | 1/1; restored | 23.42 | 30.68 |
| `163001750631Z` | Direct archive merge | Nine arrivals; tenth admission and final reset rejected by body cap | 22.70 | 30.17 |
| `163447710293Z` | Direct merge, fresh server | 10/10; restored | 23.30 | 31.26 |
| `163820551143Z` | Decoder index reuse smoke | 1/1; restored | 2.69 | 10.10 |
| `163902433041Z` | Decoder index reuse, frozen mixed cohort | 10/10; restored | 3.94 | 11.79 |
| `164419677066Z` | Same source, frozen long prefetch cohort | 10/10; restored | 2.28 | 6.93 |

The first failed baseline recorded `stale_queued_request`; the server log also
reported a 2,215 ms / 44-tick overload warning at 12:23:28 Eastern. Its short
motor sample is not a passing latency baseline. The cProfile invocation was
`python -m cProfile -o runtime/control-profile.pstats scripts/check-hill-navigation.py
--kind mixed --variants 1 --background-planning --trace-processing`; its overhead
and failure exclude it from acceptance timing.

The nine-arrival cohort retains `status=error` and no final restoration proof.
Its active-body state was checked idle before restarting the server. A fresh
ten-layout cohort, rather than an appended tenth case, established restoration.
Later restarts were performed only after completed cohorts and idle checks.

The codec replay benchmark alternated algorithm order over twelve distinct
recorded observations and three repetitions. Median total codec work fell from
1,397.45 ms for the prior rewrite to 399.83 ms for direct merge, with equal final
decoded facts each time. This excludes disk I/O and is not a live-control result.
The receipt is `section-merge-benchmark.json` in the evidence directory;
`scripts/benchmark-section-merge.py` reproduces the comparison.

The final mixed cohort recorded 873 motor decisions. Per-layout motor p95 ranged
from 2.18 to 6.99 ms. There were no discarded stale actions, controller errors or
invalid attempts; every arrival passed authoritative edit/stock checks and the
lab was restored. Aggregate motor p99 was 19.00 ms and maximum was 40.00 ms;
decode-plus-motor p99 was 25.85 ms and maximum was 44.20 ms. Passing p95 does not
eliminate tail spikes.

Native terrain sampling p95 was 1.39 ms; this excludes other snapshot work.
Rolling server average MSPT p95 was 0.97 ms, not a per-tick or TPS measurement.
Actual policy response-body p95 was 66,989 bytes. Full observation-to-before-send
p95 was 35.72 ms and includes transport/event/recording work; it is a different
metric from the 11.79 ms decode-plus-motor result. Windows thread CPU samples
advanced in 15.625 ms steps, so they cannot prove the cause of individual waits.

## Verification and remaining work

The current Python suite passed **418 tests**, with Ruff lint, formatting and
`git diff --check` passing. The test receipt is
`results/background/resume-python-tests.xml`. Focused cases cover sparse merges,
palette pruning, migration rollback, concurrent writers, immutable index reuse,
malformed/forged indexes, sample replacement and stale motor geometry rejection.

The ten longer routes passed on the same Python source and runner hashes as the
final mixed cohort. All ten adopted one prefetched segment, with zero discarded
stale actions, controller errors or invalid attempts. Every per-route motor p95
was below 2.41 ms. The cohort restored the lab. These two frozen cohorts and all
retained trials are bound in [CONTROL_LATENCY_PROOF.json](CONTROL_LATENCY_PROOF.json).
The fixture processing target is met at this checkpoint; full performance and
sprint acceptance remain open. No new Baritone or legacy scored result is claimed.

Next priorities remain byte-budgeted deltas with backlog/gap resynchronization,
client-envelope admission and Baritone coverage calibration; automatic bounded
historical recall and places/connections/teleport-edge storage; missing traversal
and construction families with adverse cases; then the frozen natural comparison,
night diagnostics and source-bound legacy requalification. The acceptance tracker
retains these separate unfinished requirements.

The installed native source/jar remain those in the pause handoff. That source
is not legacy-qualified: scored admission stays closed until requalification.
Do not repeat the old 10/10 Baritone versus 0/10 MCB natural comparison as current
candidate evidence, or promote these fixtures to natural-terrain parity.

At this checkpoint the local server is running on the restored `world` lab,
Minecraft 26.3/protocol 777, game port 25575, with zero connected players. The
latest checked PID is `59464`; recheck actual status before using it. No cohort,
build or test is left running. Restart between bounded cohorts when needed for
the 32-retired-body limit. A body-cap rejection remains terminal; inspect actual
state before restarting, and wait for protocol readiness after starting.
