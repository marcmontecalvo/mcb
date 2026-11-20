# One command: every live qualification check, then a pass/fail table.
#   scripts\qualify.ps1 [--fast] [--only NAME ...] [--keep-all]
# Failed checks keep their logs and run folders; clean passes are deleted.
Set-Location (Split-Path -Parent $PSScriptRoot)
uv run --locked mclab qual @args
exit $LASTEXITCODE
