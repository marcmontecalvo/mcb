"""Optional live-progress hook for the companion scripts (read by the dashboard).

A script calls write_progress(folder, ...) as it goes; the dashboard polls
<folder>/progress.json. Everything is best effort and never raises.
"""

import json
import time
from pathlib import Path

PROGRESS = "progress.json"
JOB = "job.json"


def _write(path: Path, value: dict) -> None:
    try:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        pass  # progress is advisory; a failed write must not fail the job


def write_progress(
    folder: Path,
    job: str | None = None,
    stage: str | None = None,
    done: float | None = None,
    total: float | None = None,
    detail: str | None = None,
) -> None:
    """Replace progress.json: job, stage label, done/total counts, a detail line."""
    value = {"job": job, "stage": stage, "done": done, "total": total, "detail": detail}
    _write(Path(folder) / PROGRESS, {**value, "updated_at": time.time()})


def write_job(folder: Path, job: str) -> None:
    """Record which job produced a results folder (shown in run history)."""
    _write(Path(folder) / JOB, {"job": job})


def read_json_file(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_progress(folder: Path) -> dict | None:
    value = read_json_file(Path(folder) / PROGRESS)
    return value if isinstance(value, dict) else None


def job_name(folder: Path) -> str | None:
    """job.json, then progress.json's job, then the files the scripts leave behind."""
    for name in (JOB, PROGRESS):
        value = read_json_file(Path(folder) / name)
        if isinstance(value, dict) and isinstance(value.get("job"), str):
            return value["job"]
    folder = Path(folder)
    if (folder / "build.json").exists():
        return "build"
    if any(folder.glob("gather-*.json")):
        return "gather"
    return None
