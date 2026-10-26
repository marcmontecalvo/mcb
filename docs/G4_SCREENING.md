# Tactical development screen — updated October 25, 2026

Status: Jev, OpenJev, Laya and Qwen3.5-4B direct scoring completed their declared
synthetic matrices; Atom remains partial under shared load. The new local
candidates did not earn live promotion. This is G4 evidence, not a passed G4 or
gameplay gate.

The frozen fixture contains **48 synthetic observations**, eight variants in each
of exploration, aiming error, mining, collection, obstacle/ledge and unknown/stale
terrain. Each appears in original, reversed and seeded candidate order: **144
cases per model**. A separate reviewer checked geometry, heading, reach and useful
next actions before provider calls. The candidate descriptions and instruction
text match the preceding live tactical profile exactly. Labels and construction
facts are never sent to providers.

These are controlled behavior-selection checks. Models receive the same compact
summary as the live selector, including the shared guard's `forward_safe` value.
The screen does not test native sensing, placement, insufficient dirt, positive
stop behavior, learned control, unseen layouts or calibrated probabilities.
Translated variants are not independent Minecraft layouts.

## Results

| Backend/configuration | Attempted cases | Admitted inference requests | Correct | Backend errors | Matrix complete |
| --- | ---: | ---: | ---: | ---: | --- |
| Jev, four batches of 36 | 144 | 144 | 141 | 2 | Yes |
| Atom, initial runner | 36 | 1 | 0 | 36 | No |
| Atom, corrected runner | 54 | 54 | 52 | 1 | No |
| OpenJev 4B v5, NLI adapter, October 25 | 144 | 144 | 78 | 0 | Yes |
| Laya English, unchanged SystemOne adapter, October 25 | 144 | 144 | 18 | 68 | Yes |
| Qwen3.5-4B BF16 SemIf direct, original criterion | 144 | 144 | 28 | 0 | Yes |
| Qwen3.5-4B BF16 SemIf direct, serialization-neutral criterion | 144 | 144 | 28 | 0 | Yes |

The actual SemIf direct-scoring candidate has now run on CUDA with the pinned
Qwen3.5-4B BF16 model and verified unchanged upstream scoring sources. It reads
all seven answer-token logits in one forward pass and generates zero answer
tokens. This is separate from the earlier Ollama Q4 approximation, which stopped
after one full action request exceeded the one-second deadline.

The original frozen criterion includes a JSON-output sentence that conflicts
with SemIf's answer-letter renderer. That first full run remains a distinct
configuration. A second full run removed only that trailing serialization
sentence, preserving task semantics, action descriptions, observations and labels.
Both scored 28/144, with no errors or deadlines missed. The serialization-neutral
run passed exploration 0/24, aiming 9/24, mining 16/24, collection 3/24,
obstacle/ledge 0/24 and unknown/stale 0/24. Its whole-decision median was 179.5 ms,
p95 203 ms and maximum 250 ms. GPU forward-pass median was 115.4 ms. No state kept
the same selected action across all three candidate orders in either run.

The raw audit recomputed every distribution, argmax, label result and candidate
order, and checked matching model/source metadata and input limits. Actual runtime
versions were PyTorch 2.13.0+cu130 and Transformers 5.12.1, differing from SemIf's
original package dependency pins. These two prompt configurations are not pooled;
no gameplay, holdout or general model-quality claim follows. Do not expand this
candidate into live trials under the current representation. See
[direct-scoring proof](G4_QWEN35_DIRECT_PROOF.json).

OpenJev's local SGLang service passed three basic NLI contract checks and completed
the full frozen matrix with the original one-second deadline and no retries.
Exploration, aiming and collection each passed 24/24; mining and obstacle/ledge
each passed 0/24; unknown/stale terrain passed 6/24. Every mining case chose
`aim_log`; every obstacle/ledge case chose `forward`. Median whole-decision latency
was 226.5 ms, p95 265 ms and maximum 657 ms. One decision batches seven option
hypotheses; the matrix therefore scored 1,008 premise/hypothesis pairs.

The experiment uses the released OpenJev typed-decision hypothesis template,
softmaxes each three-class NLI output, and normalizes entailment over options.
These distributions are uncalibrated. All 48 states retained the same selected
action across their three option orders. Raw wire prompts, logits, decisions and
labels were audited; no expected-action labels were sent to the service.
The adapter is retained as an experiment artifact, not installed in the production
backend. This is a failed tactical baseline under the existing representation;
do not promote it into live trials. Historical Jev uses a different adapter and
serving path, so timings do not establish a controlled architecture comparison.
See [OpenJev evidence](G4_OPENJEV_PROOF.json). No native or holdout runs were made.

Laya English passed three basic contract/decision checks on CUDA, then completed
all 144 screen requests with no transport errors or deadline failures. Only
18/144 were accepted and correct: exploration 13/24, aiming 1/24, mining 3/24,
collection 1/24, obstacle/ledge 0/24 and unknown/stale terrain 0/24. Its rounded
probabilities failed the unchanged sum-to-one check on 68 responses; maximum
absolute sum error was 0.0002. These responses were retained and rejected, without
normalization or retries. Separately inspecting every returned choice, including
rejected responses, yielded only 29/144 correct choices. This diagnostic does not
replace the official 18/144 score, and shows that formatting is not the only
problem. Only 15/48 states kept the same returned choice across all three option
orders. The server reported no state truncation and zero CUDA-to-CPU fallbacks.

Laya response latency across all 144 requests was median 78 ms, p95 94 ms and
maximum 156 ms; this excludes local response validation. It remains a failed
tactical baseline under the frozen representation. Hold live trials; Jev remains
stronger on this particular screen. See [Laya evidence](G4_LAYA_PROOF.json).

Jev's three unsuccessful cases were one wrong `mine` choice when a visible log
needed aiming, and two distributions summing to **0.99**. Both distributions were
rejected under the existing validation rule; neither was normalized or dropped
from the denominator. Its valid-response median was about **297 ms**. This
**141/144** result applies only to the independently reviewed synthetic label
policy. Different valid turns can be accepted; it does not establish option-order
invariance or general task accuracy.

Atom's initial request exceeded the one-second deadline. The initial runner then
recorded 35 admission rejections while that call was still outstanding. This batch
is retained as a runner failure, not 36 observed model decisions. The runner now
persists each case before inference and stops the batch at the first deadline or
outstanding-call failure. An interrupted request remains in the denominator.

A separately recorded ten-second diagnostic returned in 922 ms; that diagnostic
did not relax the screening deadline. The corrected Atom run passed its first 36
cases, then stopped on another deadline after 18 further cases. It had one wrong
choice and one timeout, with a valid-response median of **813 ms**. Remaining
batches were not run. Configuration/source hashes keep the two runners separate.

Marc reports several Hermes agents have been heavily using Atom for hours.
Consequently Atom's current timings are **shared-load observations**. Server queue
length was not measured, and no service restart, unload or configuration change
was attempted. Earlier Atom load is not established. One Atom readiness probe
overlapped a Jev batch; these observed timings are not an isolated architecture
or hardware benchmark. Complete the Atom matrix in a quiet window before a full
head-to-head comparison.

## Cost and reproducibility

Jev's 144 screen requests reported **$0.003844764**. At that screen checkpoint,
settled OpenRouter usage including the earlier 23 calls was **$0.004482366**.
The later C1 readiness/gameplay follow-up brought authenticated settled usage to
**$0.004860996**, leaving **$0.995139004** under the unchanged $1 cap; see
[follow-up evidence](G4_JEV_CURRENT_STACK_PROOF.json).
Each remote batch respected the configured request limit and
conservative cost ceiling. Invalid probability responses were still charged and
included. Local infrastructure cost remains unknown.

```powershell
rtk proxy uv run --locked mclab experiments screen-build
rtk proxy uv run --env-file .env --locked mclab experiments screen-run --backend openrouter-jev --offset 0 --limit 36
rtk proxy uv run --locked mclab experiments screen-report
```

The run command makes new billable calls. Batches used offsets 0, 36, 72 and 108.
Fixture definitions cannot be overwritten; review hashes must match before calls.
Reports expose missing/duplicate cases, admitted requests and every failed case.
Exact source snapshots, configuration hashes, candidate orders, sanitized wire
exchanges and reported costs remain under `results/screening/`.

See [independent label review](G4_SCREEN_REVIEW.json),
[screening proof](G4_SCREENING_PROOF.json), and
[navigation preparation](G5_GENERATOR_PREPARATION.json).
