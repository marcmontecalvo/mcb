---
name: mcb-log-query
description: Use when answering any question about what an MCB run did — why it failed, stalled, stood still, replanned, hit a search cap or took so long — from results/ data (digest.json, events.sqlite3, events.jsonl[.gz], episode ledgers) without reading gigabyte logs whole.
---

# Querying MCB run data

Event logs run to gigabytes. **Never `cat`, `Read` or `head` an `events.*` file.** Go from
cheap to expensive and stop as soon as the question is answered.

## Where things are

| Path | Holds |
|---|---|
| `results/companion/<run>/digest.json` | outcome, duration, phase timings, stalls, failure reasons, build key |
| `results/companion/<run>/summary.json`, `walkthrough.md` | end-of-run summary, readable story |
| `results/companion/<run>/progress.json` | live stage, `done/total`, `updated_at` |
| `results/companion/<run>/events.sqlite3` | full compressed event log (current format) |
| `.../events.jsonl[.gz]` | older runs and navigation/natural sessions |
| `results/navigation/<run>/`, `results/natural/<run>/` | navigation / natural sessions |
| `results/battery/<b>/<round>/SUMMARY.md` | battery results |
| `results/episodes.sqlite3` | shared episode ledger: runs and episodes tables; event payloads over 2 KB are stubbed |
| `results/launches/<stamp>/` | one launch record per dashboard/CLI launch |

Older runs keep only digest, summary, walkthrough and the final checkpoint
(docs/DATA_RETENTION.md). If `events.*` is gone, answer from the digest, or say the raw
log was pruned.

## Ladder

1. **Digests across attempts**:
   `uv run --locked mclab runs <key part> --last 5` (add `--json` for fields,
   `mclab runs show <run>` for one). Use `mclab runs --unknown` for runs without a key.
2. **Event counts by type**:
   `uv run --locked python -m mclab.event_log stats <run>/events.sqlite3`
3. **Filtered events**:
   `python -m mclab.event_log show <src> --type <type> [--kind K] [--plan P] [--limit N | --tail N]`.
   Always pass `--limit` or `--tail`.
4. **Purpose-built analyses** (`scripts/analysis/`, run with `uv run --locked python`):
   - `idle_gaps.py RUN_DIR [--min-seconds 5]`: the bot stood still. The stall rule
     allows it only while breaking, placing or crafting.
   - `silence_gaps.py RUN_DIR`: Python sent the body nothing.
   - `watch_digest.py RUN_DIR [--seconds 120]`: what happened in the last window of a
     live run.
   - `planner_trace.py <attempt>/events.sqlite3`: planner mode/status/token changes per
     tick (superseded searches).
   - `time_budget.py <attempt>/events.sqlite3`: speed class × controller state.
   - `timeline.py <events.jsonl> <out.txt> <positions.pkl>`: requests and routes per tick.
   - `planner_parity.py`: replay one decision through Java (see skill `mcb-planner-ab`).
5. **Search caps**: `uv run --locked python -m mclab.search_caps <run>`. A cap hit near
   the goal is a bug.
6. **Ad hoc SQL** (last resort) on `events.sqlite3`: open it read-only
   (`sqlite3 "file:<path>?mode=ro" -cmd ".mode line"`). Select narrow columns with a
   `LIMIT`. Payloads may be compressed, so prefer `mclab.event_log`, which decodes them.

## Answer shape

- Say which run and which source (digest or event log), and quote the numbers.
- Separate what the data shows from your guess about the cause.
- For a "why did it fail" question: name the first failing op or phase, the failure
  reason as recorded, and the events just before it.
- Never conclude a cell was air or support because it was unknown: unknown is neither.
