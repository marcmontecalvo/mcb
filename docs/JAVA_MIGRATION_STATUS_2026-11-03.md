# Java migration status — November 3, 2026

Production migration is implemented, independently reviewed, committed, rebuilt
and installed. Main commit: `3e09a07`. Its tree matches the reviewed worktree
candidate `0aee342d408415890ea66a88aad74c121b92000c`.
Both installed mods are bound to source hash
`c414e408b6b06a0cb52090d3f7953fa3a260ff475d00ec9e0b96c54056e80c82`.

Production route and terrain planning uses Java. Python search remains only as
an explicit offline reference and test oracle. Movement permissions are task
settings on the shared body. See [migration inventory](JAVA_MIGRATION_INVENTORY.md)
and [ability inventory](COMPANION_ABILITIES.md).

Validation completed: 1,043 Python tests, Ruff lint/format, both mod Gradle
builds/checks, 90 ordinary differential cases and 30 terrain differential cases.
Independent Java and Python reviews found no actionable runtime defects. The
general reviewer requested production controller integration coverage; eight
new cases passed independently and resolved that finding.

## Live comparison

| Check | Original build | Migrated build at handoff |
| --- | --- | --- |
| Natural action smoke | 14/14 | 14/14, `results/companion/20261103T190029116584Z` |
| Source-bound regression | All seven stages passed | All seven stages passed |
| Empty-inventory gathering | 0/2, existing `no_stance` failure | 2/2 passed |
| Supplied-material house | 4/4, 84 correct cells | 4/4, 84 correct cells; 14,362 build ticks versus 14,014 original |
| Calm routes | 10/10, zero damage, 7,188 ticks | 10/10, zero damage, 7,726 ticks (7.5% more) |
| Safe night routes | 5/5, four damage, 3,960 ticks; Python fallback | 4 successes, R3 invalid after creeper death; stage exit 1 |

The queue completed at 14:57 Eastern. Night R3's server log confirms death by
creeper. Subsequent planner and cleanup requests were rejected because the body
was gone (`native_plan_requires_development_body`, then
`route_policy_requires_development_body`), masking the terminal outcome as a
generic RuntimeError/invalid attempt. Preserve the recorded invalid attempt;
death-aware planning/cleanup and outcome classification need correction before
retesting. R2 took 22.282 HP and R4 took 8 HP; successful-route damage alone was
30.282 HP versus four HP in the original full cohort. This run does not establish
survival parity.

Migrated artifacts: gathering `results/companion/20261103T192408837819Z`, house
`results/companion/20261103T192954992449Z`, calm
`results/natural/20261103T194235139763Z`, night
`results/natural/20261103T195252574413Z`.

The smoke test includes a `use_entity` response of `not_found`; this does not
qualify successful interaction with every entity. Baselines are development
evidence, not broad survival or giant-construction qualification. Wall time is
not an idle-machine benchmark. The invalid original fixture attempt is retained
separately from the successful qualification retry.

## Resume and evidence

The MCB Lab dashboard at `http://127.0.0.1:8770` now has a **Baseline checks**
panel above Sessions. It refreshes every three seconds from the running batch's
logs, showing stage completion, elapsed time, process status and console output.
This is a read-only view; dashboard session controls do not control this batch.

Background runner PID at launch: **90496**. It runs serially and stops on a
failed smoke, cleanup, qualification, or house check. Gathering retains its
known failure without preventing the navigation comparisons. Inspect outcome
summaries as well as process exits; natural runs can exit normally with failed
attempts.

- Progress: `runtime/java-migration-post.log`
- Errors: `runtime/java-migration-post-error.log`
- Queue: `runtime/java-migration-post-baseline.ps1`
- Candidate provenance: `runtime/java-migration-candidate-manifest.json`
- Original evidence: `results/java_migration/20261103T125214Z-baseline/manifest.json`
- Original audit: `runtime/java-migration-prebaseline-audit.json`
- Rollback jars and provenance: `runtime/java-migration-backups/ea973803/`

The completed summaries have now been inspected. No qualification ledgers have
been promoted. Migration implementation is complete, but the night outcome and
calm timing regression remain open; final live parity is not claimed.
