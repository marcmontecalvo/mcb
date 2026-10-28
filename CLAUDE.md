# MCB — start here

1. Read [docs/NAVIGATION_DIRECTION_2026-10-28.md](docs/NAVIGATION_DIRECTION_2026-10-28.md).
   It is the authoritative plan for navigation, route planning, model roles and
   the Baritone comparison. Begin with **Phase 0** unless Marc says otherwise.
2. For live runtime state, source hashes and how to operate the lab, read
   [docs/PAUSE_HANDOFF_2026-10-27.md](docs/PAUSE_HANDOFF_2026-10-27.md) and the README.
   Recheck live server status rather than trusting recorded PIDs.

## Document precedence

- The direction document above overrides older docs where they conflict.
  Docs with a "Partly superseded (Oct 28, 2026)" banner contain legacy sections.
- When you edit an older doc for other reasons and find a conflict, update it to
  match the direction document or mark the section legacy. Do not leave two answers.
- `Minecraft_AI_Technical_Handoff.md` is the original specification: do not edit it.
- `docs/SPRINT_ACCEPTANCE.json` remains the acceptance tracker.

## Working rules carried forward

- Commands run from the repo root in PowerShell, using the `rtk proxy uv run --locked ...`
  forms shown in the docs.
- Unknown cells are never treated as support or air by the **motor**. (The planner may
  plan through unknown space at a penalty; see the direction document, D3.)
- Keep every failed cohort in the evidence ledger; never pool earlier configurations
  into a new denominator. Native changes require source-bound requalification before
  legacy scored admission.
- Swimming is being developed in parallel; see §5 of the direction document.
