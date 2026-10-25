# G4 live integration — October 24, 2026

Status: local Qwen and hosted Jev integration implemented and exercised; G4
remains open for a local SystemOne candidate and broader screening.

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

The CLI limits experiments to one to five attempts and at most 100 requests across
the run. Remote inference additionally requires an explicit configured total
request cap and a conservative cost budget. The total is checked before dividing
requests between episodes. Tokens remain environment references in ignored `.env`.

Results will be recorded under `results/model_execution/` with exact source
snapshots, source/configuration hashes, sanitized requests and responses,
candidate order, decision outcomes, controls, native milestones and termination.
Completion of a smoke batch does not mean its episodes succeeded or G4 passed.

## Initial Qwen results

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

Verification: 137 Python tests, Ruff lint/format and credential scan passed.
Code and Python reviews approved the integration. A final reset restored the
initial C1 state digest after the live runs. No human input or rescue was used.

## OpenRouter Jev

The user supplied `OPENROUTER_API_KEY` and a $1 wallet/key cap. Authentication
confirmed that cap before inference. The pinned route is `typesafe/jev-1.14`
through `https://openrouter.ai/api/v1/systemone`; the served model was
`typesafe/jev-1.14-20261014`.

One readiness call passed in 328 ms. Three calls on one recorded permitted state
with original, reversed and seeded candidate orders returned valid `aim_log`
choices in 266–375 ms. Probability values differed; this does not establish order
invariance, calibration or semantic accuracy.

One live development attempt succeeded in **212 ticks**, using **19 requests**.
All responses validated within the one-second deadline (250–359 ms); one completed
response was discarded on shutdown after success. It used the same tactical
candidate definitions and motor profile as Qwen. This single trial does not prove
an architecture or speed advantage on broader tasks.

The initial plan allowed 44 calls with a conservative $0.02 per-call maximum
($0.88 total). It used **23 calls**, reporting **$0.000637602** in OpenRouter
`usage.cost`. The settled wallet usage exactly matched that total, leaving
$0.999362398 of the $1 cap. Its separate snapshot is retained.
[The Jev proof](G4_JEV_PROOF.json) includes probes, cost accounting and
the live run reference. Cost accounting retains unknown when any response lacks
valid usage; it does not assume a zero charge after failures.

```powershell
rtk proxy uv run --env-file .env --locked mclab models probe --backend openrouter-jev
rtk proxy uv run --env-file .env --locked mclab models live-smoke --backend openrouter-jev --count 1 --request-limit 40
```

These commands make new billable calls; they do not replay stored evidence.

## gpu-docker Qwen3 comparison

The user supplied the unauthenticated Ollama endpoint
`http://10.10.40.20:11434/v1`. Discovery identified `qwen3:14b`, 14.8B parameters,
Q4_K_M quantization and Ollama 0.20.2. `reasoning_effort: none` is explicitly
configured and recorded according to the
[Ollama compatibility contract](https://docs.ollama.com/api/openai-compatibility).
No reasoning content was returned; a server-internal thinking trace is not claimed.

One initial readiness response returned an unknown candidate `1` and was rejected.
Three subsequent original/reversed/seeded choices on the same recorded visible-log
state used for Jev returned valid `aim_log` IDs in 359–500 ms. This small screen
did not reveal the failure on observations where no log is visible.

The live C1 attempt failed after 40 requests and 474 ticks: every response selected
`aim_log` despite an empty visible-log list. The fresh motor guards suppressed
that inapplicable behavior and the body never moved. The declared request budget
ended the attempt; no retries, timestamp changes, rescue or deadline relaxation
were used. Live replies took 468–593 ms (median 485), with no deadline failure.
The recording contains 41 tactical results: 40 wire responses and one local
budget rejection that sent no inference request. Request counts remain 40.

It is faster than Atom's measured responses and slower than Jev's in these small
tests, but unusable under the current tactical prompt. Different models, sizes,
quantization and serving stacks prevent a hardware or general intelligence ranking.
All 44 calls are retained in [gpu-docker evidence](G4_GPU_DOCKER_PROOF.json).
Any representation/prompt/thinking investigation will be a separate configuration.

Next: independent offline action screening and a local SystemOne candidate,
followed by distinct-layout navigation trials after their courses and body
capabilities are qualified. G4 is not marked passed by these C1 smokes.
