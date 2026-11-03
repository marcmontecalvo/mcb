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
| Source-bound regression | All seven stages passed | Background run pending |
| Empty-inventory gathering | 0/2, existing `no_stance` failure | Background run pending |
| Supplied-material house | 4/4, 84 correct cells | Background run pending |
| Calm routes | 10/10, zero damage, 7,188 ticks | Background run pending |
| Safe night routes | 5/5, four damage, 3,960 ticks; Python fallback | Background run pending |

The smoke test includes a `use_entity` response of `not_found`; this does not
qualify successful interaction with every entity. Baselines are development
evidence, not broad survival or giant-construction qualification. Wall time is
not an idle-machine benchmark. The invalid original fixture attempt is retained
separately from the successful qualification retry.

## Resume and evidence

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

At handoff, the remaining live results have **not** been reviewed or promoted
to qualification ledgers. After the queue finishes, compare its exact summaries,
record failures, and update this table. Do not rerun a second queue concurrently.
