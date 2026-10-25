# Tactical development screen — October 24, 2026

Status: Jev completed the synthetic matrix; Atom remains partial under shared
load. This is additional G4 evidence, not a passed G4 or gameplay gate.

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

Jev's 144 screen requests reported **$0.003844764**. Settled OpenRouter key usage
including its earlier 23 calls is **$0.004482366**, leaving **$0.995517634** of the
verified $1 cap. Each remote batch respected the configured request limit and
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
