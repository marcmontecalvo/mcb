# Inspecting a build

`scripts/run-build.py` now supports a reference copy and a failure walkthrough.
These are operator aids in the development lab; the companion cannot kit blocks.

For a new run, append `--preview --inspect` to the existing build command.
After construction ends, the lab places the reference copy beside the attempt
and keeps the session open. Join `127.0.0.1:25575` to inspect it. Create a file
named `STOP` in the printed results folder to finish and restore the lab world.
Interrupting the script also unwinds its normal cleanup.

For an existing failed run, use its saved checkpoint to skip gathering:

```powershell
rtk proxy uv run --locked python scripts/run-build.py --resume results/companion/<run>/checkpoints/final.json --stage --preview
```

`--stage` restores the attempt and waits for `STOP` without running construction.
Omit `--stage` to retry construction; add `--inspect` to hold the result afterward.

The default reference displacement is the rotated template's X width plus five
blocks. `--preview-offset X Y Z` supplies a different displacement relative to
the attempt's origin. Overlap with template cells or the body is rejected.
Every template cell, including explicit air, is copied in batches of 64.
The preview uses exact states without normal placement checks or neighbour
updates, like checkpoint restore: it is a target reference, not proof that the
builder can construct it. It may replace terrain at the reference location.
Normal runs place it after the job so gathering cannot mine its materials.
`preview.json` records the offset and labels it diagnostic-only.

After a build returns a report, its results folder also contains:

- `walkthrough.md`: every failed attempt's target, desired block state, reason,
  actual stance, observer `/tp @s` command, and full available placement diagnosis.
- `walkthrough.json`: the same records for analysis tools. Repeated attempts stay
  separate; a recorded stance may now be occupied by the finished structure, so
  use spectator mode when following the walkthrough.
- `build.json`: existing failures and operations, plus effective movement and
  terrain policies. Navigation entries reference `policies` by `policy_id` so a
  large protected build footprint is stored once per distinct configuration.

Without `--inspect`, a seeded run restores the original lab world when it exits;
use checkpoint staging to inspect that attempt later. Existing crash recording
still writes `error.txt` and a failing summary row. A crash before a build report
exists does not fabricate a failure walkthrough.
