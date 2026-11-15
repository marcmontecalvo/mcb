# MCB — start here

1. Read [docs/ROADMAP.md](docs/ROADMAP.md) for upcoming work.
   [docs/IMPLEMENTED.md](docs/IMPLEMENTED.md) summarizes verified behavior and its
   limits; [docs/DECISION_MATRIX.md](docs/DECISION_MATRIX.md) separates settled
   choices from open decisions; [docs/INDEX.md](docs/INDEX.md) routes to plans,
   proof, contracts, and history.
2. [docs/NAVIGATION_DIRECTION_2026-10-28.md](docs/NAVIGATION_DIRECTION_2026-10-28.md)
   remains authoritative for navigation decisions and phases. Check the linked
   gate records and source hashes before treating dated results as current.
3. To operate the lab, read the README and [docs/LAB_UI.md](docs/LAB_UI.md). Recheck
   live server status rather than trusting recorded PIDs. Attempt analysis scripts
   are in `scripts/analysis/`.

## Document precedence

- The direction document above overrides older docs where they conflict.
  Docs with a "Partly superseded (Oct 28, 2026)" or "History (Oct 31, 2026)" banner
  contain legacy sections. `docs/INDEX.md` lists each document's role.
- When work finishes an item or finds a new one, update `docs/ROADMAP.md`; keep
  implementation/proof claims in `docs/IMPLEMENTED.md` and choices in
  `docs/DECISIONS.md` / `docs/DECISION_MATRIX.md`.
- When you edit an older doc for other reasons and find a conflict, update it to
  match the direction document or mark the section legacy. Do not leave two answers.
- `Minecraft_AI_Technical_Handoff.md` is the original specification: do not edit it.
- `docs/SPRINT_ACCEPTANCE.json` remains the acceptance tracker.

## Working rules carried forward

- Commands run from the repo root in PowerShell, using the `rtk proxy uv run --locked ...`
  forms shown in the docs.
- Unknown cells are never treated as support or air by the **motor**. (The planner may
  plan through unknown space at a penalty; see the direction document, D3.)
- Git is the record of how the code was. `results/` is working data, not an archive:
  `src/mclab/prune.py` keeps it small (it runs when an arena session ends, or
  `python -m mclab.prune [--apply]`). The three newest runs of each family stay raw;
  a run whose id appears in `docs/` keeps its databases and has `events.jsonl`
  gzipped; any other old run loses its raw logs and databases. Small results
  (`summary.json`, `build.json`, `checkpoints/`) always stay. To keep a run, cite its
  id in `docs/IMPLEMENTED.md` or the relevant proof document. `MCLAB_NO_PRUNE=1` turns the sweep off for a session.
- Do not pool earlier configurations into a new denominator when comparing results.
  Native changes require source-bound requalification before legacy scored admission.
- To test a build from a known point, do not rerun gathering: each build run writes
  `results/companion/<run>/checkpoints/NNNN.json` and `final.json`; restore one with
  `run-build.py --resume <file>` (same seed, template cells and inventory, build only).
- Swimming is implemented in the planner and body; status per item in §5 of the
  direction document.
- Never poll `/policy` and discard the result: a served terrain batch counts as
  delivered and is not resent. Use `/state` to wait for a run state.
- To judge what the planner knew, replay only the terrain batches Python read
  through the Java planner (`GOAL="[x,y,z]" ... scripts/analysis/planner_parity.py <events.jsonl> <tick> <start>`,
  which exports the delivered map and runs the `routePlannerParity` gradle task), not
  the body's `policy_sample` log.
  Exact only for plans whose `plan_accepted` says `terrain: uploaded`; `live` plans read
  the world within their `terrain_radius`, so delivered batches are a lower bound.
