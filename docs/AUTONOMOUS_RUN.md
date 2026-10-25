# Autonomous implementation and test run

Marc requested continued implementation/testing in this session, independent
course trials wherever possible, and a final evidence-backed report. Keep this
file and the gate ledger current across context compaction. Do not request another
manual testing pass for work that the native harness can verify itself.

## Current phase

Implementing the separately scoped C2–C5 navigation reset/body/motor extension.
Native code builds; Java review and live regression remain before installation
and qualification. The last installed server still uses the preceding native mod.
The original C1 profile and specification remain frozen.

New components under development:

- Native `navigation-c2-c5-survival-v1` admin reset profile with validated generated
  specifications and initial headings; legacy C1 reset rejects overrides.
- `config/navigation_body_profile.json` inherits the qualified C1 physics and
  timing bounds. It does not add construction, attacks or dimension travel.
- A permitted-observation deterministic navigation motor with map memory,
  exploration, obstacle handling and one-block jumps.
- Recorded reset and execution runners; navigation scoring requires separate
  source/profile-bound course qualification and 20 matching reset checks.

## Execution order

1. Review/build new native and Python changes; fix any findings.
2. Stop the loopback server gracefully, install the reviewed mod and wait for
   actual Minecraft protocol readiness after restart.
3. Rerun C1 mechanics/protocol/reference, reset, adversarial/sensor and deterministic
   execution regressions. Refresh current-source proof only after audit.
4. Qualify navigation headings/reset state under fresh identities, restarting
   between batches before the retained-body limit of 32 is reached.
5. Run distinct navigation smoke layouts, diagnose failures, then freeze viable
   controller configurations for paired 20-layout development trials toward G5.
6. Continue compatible baseline, demonstration/training, construction and holdout
   work in handoff order as prerequisites unlock. Preserve failed configurations.

## Standing constraints

- G0–G3 evidence is historical and scoped; source/profile drift blocks scoring.
  Do not widen the initial profile or mark a later gate passed by preparation.
- Atom is heavily shared with Hermes agents per Marc. Do not benchmark it as an
  isolated service, retry an overloaded batch repeatedly, unload its model or
  change its service. Finish the partial screen in a quiet window later.
- OpenRouter has a verified $1 key/wallet cap. Last settled usage: $0.004482366.
  Keep explicit provider/request/cost limits and record every charged request.
- gpu-docker Qwen3 failed its existing tactical profile; preserve that baseline.
  Jev image/Box work remains a later, separate camera experiment.
- The original handoff is unchanged. Local commits are authorized; no deployment
  or publishing is part of this run. Preserve user environment/configuration edits.

The final report must distinguish passed, failed, incomplete and externally
blocked work. An authenticated baseline client session or synchronized human
demonstrations may require user input if no usable existing source is available;
do all independent work before returning such a blocker. Never substitute
synthetic/agent data for required human demonstrations without saying so.
