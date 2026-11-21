"""One-line answer to "what are you doing / why are you stalled?" from real state."""

STALL_SECONDS = 20
ACTIVE = {"queued", "running"}
HOLD_STAGES = {"staged", "inspection"}  # as run_digest.HOLD_STAGES


def last_failure(events_tail):
    """The newest *_failed / route_stopped reason in the event tail, or None."""
    for event in reversed(events_tail or []):
        kind = event.get("type", "")
        if kind.endswith("_failed") or kind == "route_stopped":
            data = event.get("data") or {}
            reason = data.get("reason") or data.get("state") or "no reason given"
            return f"{kind}: {reason}"
    return None


def explain(progress, session, events_tail, now):
    status = (session or {}).get("status")
    if status not in ACTIVE:
        return f"Finished: {status}." if status else "Idle."
    if not progress:
        return "Starting up."
    job = progress.get("job") or "job"
    stage = progress.get("stage") or "working"
    if stage in HOLD_STAGES:  # the job is over; the world stays up to be looked at
        return f"Finished {job}; holding the world for inspection."
    counts = ""
    if progress.get("total"):
        counts = f" {progress.get('done') or 0:g}/{progress['total']:g}"
    # a successful op (`*_done`, stamped `at` by the reader) is activity too
    seen = [
        e.get("at") or 0
        for e in events_tail or []
        if e.get("type", "").endswith("_done")
    ]
    age = now - max([progress.get("updated_at") or 0, *seen])
    if age <= STALL_SECONDS:
        return f"Running {job}: {stage}{counts}."
    reason = last_failure(events_tail) or "no failure reported"
    return f"Stalled on {job} ({stage}{counts}), no progress for {age:.0f}s; {reason}."
