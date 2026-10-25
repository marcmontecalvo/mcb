# Experiment queue

Updated October 24, 2026. Queue entries are plans, not qualification evidence.
Current gate results remain in `gates.json`.

## Next: gpu-docker Qwen3 on RTX 5060 Ti

Requested by Marc. Pending the endpoint URL and local credential entry.
`GPU_DOCKER_API_KEY` is prepared in ignored `.env` and documented in `.env.example`.
No endpoint address, port, model size, quantization or serving engine is assumed.

1. Authenticate discovery and record the exact model ID and available serving
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
