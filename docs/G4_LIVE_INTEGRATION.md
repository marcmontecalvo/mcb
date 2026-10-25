# G4 live integration — October 24, 2026

Status: live local integration implemented and exercised; G4 remains open for
the other backend routes and broader screening.

The implemented experiment is a bounded local Atom/Qwen C1 smoke run. It uses the
existing authenticated bridge, frozen G2 course and causal evaluator. Attempts
are explicitly unscored development experiments in the common episode ledger.
The qualified deterministic G3 implementation is unchanged.

The selector runs inference asynchronously, at most one request per body and
two admissions per second, with the existing one-second provider deadline.
The motor loop continues while inference runs. Every motor command is computed
from fresh filtered policy state, scheduled three ticks later, lasts five ticks,
and retains the five-tick observation-age limit. Stale motor commands are
discarded, released and regenerated, at most three times per attempt.

This is a bounded visible-target tactical profile. Aiming at a visible log and
approaching a visible drop are explicit short behaviors, not learned motor skill.
It does not use the deterministic C1 controller as a fallback. Candidate
generation and observation summarization are published in `src/mclab/tactical.py`.
Hidden course coordinates, evaluator labels and manifest data do not enter model
requests. Provider failures terminate the attempt as failed; reset or recording
corruption invalidates it. No automatic retries or additional time are granted.

Reproduction:

```powershell
rtk proxy uv run --env-file .env --locked mclab models live-smoke --backend atom-qwen-chat --count 1 --request-limit 40
```

The CLI limits this initial experiment to local inference, one to five attempts,
and at most 100 requests across the run. It never enables a paid route. The token
remains an environment reference loaded from the ignored `.env`.

Results will be recorded under `results/model_execution/` with exact source
snapshots, source/configuration hashes, sanitized requests and responses,
candidate order, decision outcomes, controls, native milestones and termination.
Completion of a smoke batch does not mean its episodes succeeded or G4 passed.

## Measured results

Five development attempts ran across three selector configurations. The first
mostly chose stop and failed on a provider deadline after 14 requests. The second
used an explicit exploration instruction and moved toward the log, but failed on
a deadline after six requests. Both failures remain recorded.

The final configuration requests compact JSON instead of pretty printing. It
completed three consecutive attempts in **255, 386 and 230 ticks**, each through
the native causal break/drop/pickup evaluator. It made 51 requests: 48 returned
valid decisions and three pending requests were cancelled after native success.
Those terminal cancellations were discarded and never applied. Valid final
configuration responses took **719–890 ms**. The one-second provider deadline,
600-tick course limit and motor timing limits were unchanged.

Across all configurations: **3 successes / 5 attempts**, **71 live requests**.
This is repeated initial C1 seed0 development evidence, not five different smoke
layouts, a holdout score or a learned-policy result. Actual inference cost is
unknown. The earlier eight provider probe requests remain separately counted;
the combined request total is 79.

[The machine-readable proof](G4_LIVE_PROOF.json) binds summaries and raw recordings
to hashes. All retained source snapshots match their recorded hashes; every
running policy tick is present and all applied controls used observations aged
three ticks. The first two deadline failures retain their original diagnostics;
the second and later configurations additionally retain sanitized error reasons.
The committed runner additionally classifies provider deadline errors as timing
failures; historical records keep their original broad decision category.
Run `rtk proxy uv run --locked python scripts/audit-model-smokes.py` to regenerate
the compact audit from local raw evidence. `mclab report` includes live request
counts and keeps readiness probes distinct from gameplay calls.

Verification: 131 Python tests, Ruff lint/format and credential scan passed.
Code and Python reviews approved the integration. A final reset restored the
initial C1 state digest after the live runs. No human input or rescue was used.

Next: independent offline action screening and the missing local SystemOne/Jev
routes, followed by distinct-layout navigation trials after their courses and
body capabilities are qualified. G4 is not marked passed by this C1 smoke.
