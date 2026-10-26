# Experiment queue

Updated October 25, 2026. Queue entries are plans, not qualification evidence.
Current gate results remain in `gates.json`.

## Current: model decisions and actual-game validation

Architecture hypothesis from Marc: a larger LLM might provide planning and Jev
fast tactical decisions. He explicitly corrected the earlier assumption that
this would be the winner. Preserve single-model and tandem comparisons and make
model/role assignment configurable. Optional tandem C1 harness support now has
independent asynchronous lanes, versioned planning context, stale-result rejection
and separate request/provenance accounting. Local fake-provider checks establish
coordination behavior, not live performance or tandem superiority. The first
planner adapter is bounded Choice over a supplied strategy menu. Broader
structured plans, opportunity suspension/resumption and combat remain pending.
See [candidate architecture](HYBRID_ARCHITECTURE.md).

Marc authorized continued work and requested the next five steps. The concrete
sequence and decision boundary are in [Jev navigation plan](JEV_NAVIGATION_PLAN.md):
define the bounded navigation contract; implement/test the shared motor and
selectors; screen actual recorded observations; run bounded native smoke trials;
freeze/audit the paired twenty-layout development comparison. Initial code review
found that the existing navigation controller also chooses goals, so it cannot
serve as an opaque deterministic fallback behind a nominal model selector.

October 25 Jev follow-up passed one new native C1 seed-0 trial on the current
stack: **151 ticks, 13 requests**, no deadline/validation failures, and a complete
raw audit. The arena was reset and the server stopped. See
[current-stack proof](G4_JEV_CURRENT_STACK_PROOF.json). This repeats the initial
task; it does not establish distinct-layout navigation. Next, define a separately
versioned model navigation interface over the qualified C2–C5 motor, screen actual
recorded observations, then run bounded native development trials. The existing
live-smoke runner is intentionally C1-only; do not bypass that admission check.

The [48-state synthetic screen](G4_SCREENING.md) completed 144 option-order cases
for Jev: 141 correct, one wrong choice and two rejected probability sums. It does
not replace broader actual-observation or native sensing tests.

Atom's corrected run stopped after 54 cases (52 correct, one wrong, one timeout).
Marc reports concurrent Hermes agents heavily use Atom, so finish its matrix only
in a quiet window. Preserve these shared-load timings and the earlier runner's
admission failures; do not label them isolated model speed measurements.

C3–C5 generators now each have 25 distinct physical geometries; C2 also has 25
development layouts including facing variation. The old frozen plan remains.
[Navigation implementation](NAVIGATION_IMPLEMENTATION.md) describes the completed
reset/rotation and separately scoped body profile. The v4 baseline completed
C2 20/20, C3 20/20, C4 19/20, C5 16/20 with valid raw audits; C5 missed the
criterion. The V8 candidate adds collection/aiming fixes, between-episode GC and
persistent HTTP. Its matched full matrix passed **80/80**, 20/20 per course, and
all raw audits passed on a uniform frozen stack. G5's deterministic development
criterion is complete. See [current evidence](G5_NAVIGATION_PROOF.json),
[baseline evidence](G5_DEFAULT_GC_BASELINE.json) and the run ledger in
[the autonomous handoff](AUTONOMOUS_RUN.md). A viable local SystemOne candidate and the
dedicated Baritone client bridge remain outstanding. No holdout has been consumed.

October 25 local model follow-up: OpenJev 4B v5 was served on gpu-docker port
8002 and completed the frozen synthetic matrix at **78/144**, with no transport
errors or missed one-second decision deadlines. Mining and obstacle/ledge both
failed 0/24 under the current representation; retain this baseline and hold live
trials. See [OpenJev proof](G4_OPENJEV_PROOF.json). Laya English then completed the
same screen on CUDA: **18/144 accepted and correct**, including 68 probability-sum
rejections. Even disregarding probability validation, its returned choices were
correct only 29/144. No timeouts or reported truncation/fallbacks; response median
78 ms. Hold live trials under this representation. See [Laya proof](G4_LAYA_PROOF.json).
Laya and OpenJev are stopped. Qwen3.5:4b remains available in Ollama; the separate
BF16 SemIf direct scorer is running at port 8003.
These bounded screens do not justify broader trials of either new local model.

Qwen3.5-4B direct scoring is now completed as a bounded screen. The native Ollama
approximation had no accuracy result because its first full action request missed
the one-second deadline. After operator launch, the actual pinned SemIf BF16
forward-pass scorer ran on CUDA and scored **28/144** with the original criterion.
A separate configuration removed only the conflicting JSON-output sentence and
also scored **28/144**. Neither run had errors or missed deadlines. The cleaner
configuration took median 179.5 ms per decision but failed exploration and
obstacle/ledge 0/24 each. Source/score/label audits passed; no native or holdout
trials were run. See [direct-scoring proof](G4_QWEN35_DIRECT_PROOF.json).
All three additional candidate screens are complete. Hold broader local-model
trials under this representation and return to Jev navigation integration.

## Tested: gpu-docker Qwen3 on RTX 5060 Ti

Requested by Marc. The user supplied `http://10.10.40.20:11434/v1/models` and
confirmed no authentication is required. The user's removal of unnecessary
credential entries from `.env` and `.env.example` is preserved.
Discovery identified Ollama 0.20.2, `qwen3:14b`, 14.8B parameters and Q4_K_M
quantization. The loaded context was 4096 tokens and reported VRAM allocation
10,786,471,040 bytes; RTX 5060 Ti identity remains user-reported.

Initial result: three recorded visible-log choice checks passed, but the first
one-option readiness response chose an invalid `1` ID. The live C1 attempt failed:
all 40 choices were `aim_log` while no log was visible, so fresh guards emitted
no movement and the request budget ended the attempt. Live replies were
468–593 ms (median 485), with no deadline failures. This is a decision failure
under the unchanged prompt, not evidence of general intelligence or hardware speed.
See [gpu-docker proof](G4_GPU_DOCKER_PROOF.json).

Any follow-up prompt/representation tuning or thinking-enabled experiment must
have its own configuration and must retain this failed baseline. Do not promote
it to broader live trials based on legal JSON or low latency alone.

Initial comparison plan (completed through the first live smoke):

1. Discover and record the exact model ID and available serving
   metadata. Label the GPU as user-reported unless independently verified.
2. Run bounded readiness and frozen-observation choices with the same candidates
   and instructions used for Qwen on Atom and hosted Jev. Retain candidate order,
   probabilities or synthetic labels, invalid outputs, tokens and latency.
3. Run one initial C1 development smoke using the same body, sensor, course and
   tactical/motor profile. Keep the one-second deadline; separately label any
   slower exploratory profile rather than relaxing the comparison silently.
4. If viable, expand to paired trials and independently reviewed action labels.
   Compare success, decision errors, deadline failures, request counts and
   completion ticks. A single C1 success cannot establish relative intelligence.

Reference evidence: `G4_ATOM_PROBE.json`, `G4_JEV_PROOF.json` and
`G4_LIVE_PROOF.json`. Atom final configuration: 3/3 C1 successes; Jev: 1/1.
These historical samples are small and do not establish an architecture ranking.

## Later: Jev image and Box experiment

Explicitly deferred by Marc until after the next text-model test. No image calls
have been made and the current policy sensor has not been replaced.

- Capture 30–50 labeled Minecraft screenshots covering visible, absent, small and
  partially occluded logs and dropped items, including different view angles.
- Use the explicit `TypeSafe-Danger-Zone-Images: true` header on image/Box requests.
  Verify the supported image payload and header behavior through the configured
  OpenRouter route before a batch; direct TypeSafe credentials are not configured.
- Pair a Noul presence question with a Box localization question. Box always
  returns coordinates, including for absent targets, so presence must gate use.
- Measure false positives/negatives, bounding-box overlap, confidence behavior,
  latency and reported usage cost. Keep small-target and occlusion cases separate.
- Use a separate camera observation profile. A screen-space box supplies no
  authoritative depth or reach; live controls still require fresh state checks.
- Keep the first screen offline. Consider live integration only if the measured
  perception and timing justify it. Image latency is not established by text tests.

Sources reviewed: [Images](https://docs.typesafe.ai/images),
[Box](https://docs.typesafe.ai/primitives/box),
[Jev 1.14 release](https://typesafe.ai/blog/jev-1-14).
TypeSafe labels images and Box experimental; formats and accuracy may change.
