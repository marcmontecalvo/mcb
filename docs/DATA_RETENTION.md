# Data retention

`results/` holds working data, not an archive. Git records how the code was. Digests,
summaries and checkpoints record what a run did. Raw event logs are only for the runs
being worked on now. `src/mclab/prune.py` implements this policy, and its module
docstring is the precise contract.

## What is kept

| Data | Kept whole | Reduced to small files | Never touched |
|---|---|---|---|
| Build/gather runs (`results/companion/`) | Per build key: the newest ended run (`--keep N`) and every passing run | Superseded runs keep `digest.json`, `summary.json`, `walkthrough.md`, the final checkpoint and other small files. The digest is written from the log first; a run whose digest fails keeps everything | Runs that may still be going; runs whose id is cited in `docs/`, README or CLAUDE.md |
| Build/gather runs without a build key | — | All heavy files deleted, no digest | Cited runs, active runs |
| Other families (navigation, natural, hills, …) | The newest 3 runs, and anything younger than 24 h | Uncited runs lose every `events.*` and `*.sqlite3` file (plus WAL/SHM). Cited runs keep their event log (gzipped) and ledger; their map sqlite is deleted | Small result files |
| Episode ledgers (`results/episodes.sqlite3`, `results/navigation/<run>/episodes.sqlite3`) | Every run, episode and event row | Event payloads over 2 KB of runs older than 7 days become `{"type", "elided_bytes"}` stubs, then the file is vacuumed | — |

**To keep a run's raw logs, cite its run id in a doc.**

## At the source

The ledger used to store every per-tick `policy_sample` (~60 KB each) uncompressed, which
duplicated the run's own event log. That came to about 2 GB per navigation run.
`recorder._ledger_payload` now stores payloads over `LEDGER_PAYLOAD_MAX` (2 KB) as stubs
when they are written. No code reads ledger payloads back: proofs count rows, and
evidence reads the runs and episodes tables. `MCLAB_LEDGER_FULL=1` keeps full payloads
for a debugging session.

## Automatic prune

`recorder.exclusive_arena` calls `prune.auto()` when every arena session ends: run-build,
run-gather, navigation and natural. `auto` does the following:

1. sweeps the other families;
2. prunes the oldest **3** superseded build/gather runs (each may need a digest pass over
   its log) and every keyless run. The rest is reported as `deferred` and handled at the
   next session;
3. trims and vacuums the episode ledgers (7-day cutoff).

Opt-outs and manual control:

- `MCLAB_NO_PRUNE=1`: no automatic prune. Tests never prune (`PYTEST_CURRENT_TEST`).
- `uv run --locked python -m mclab.prune`: dry run of the full plan.
- `... --apply [--keep N] [--ledger-days D] [--root DIR]`: full pass, uncapped.
- `uv run --locked mclab runs backfill`: write missing digests ahead of a prune.

## Cleanup of 2026-11-19

The first full `--apply` pass freed 13.13 GB from the other families (1,056 files),
6.2 GB from superseded and keyless build runs, and 46.3 GB from the ledgers. The ledgers
went from 47.67 GB to 1.34 GB: 30 databases, 161,993 payloads stubbed. Separately,
2,707 launch records that pytest had written into `results/launches` were deleted
(docs/LAB_OPERATIONS.md, "Launch records").

| Category (MB) | Before | After |
|---|---:|---:|
| navigation | 36,414 | 7,246 |
| natural | 17,243 | 9,390 |
| companion | 15,702 | 9,785 |
| hills | 4,376 | 881 |
| qualification | 374 | 270 |
| scaffolding | 308 | 74 |
| launches | 118 | 9 |
| execution | 98 | 52 |
| results/episodes.sqlite3 | 16,155 | 559 |
| **results/ total** | **~91,000** | **29,513** |

What remains is mostly the newest navigation runs (one ~6 GB `events.jsonl`), and the
natural and companion runs that the docs cite.
