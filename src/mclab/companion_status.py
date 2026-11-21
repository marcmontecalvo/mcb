"""One-line answer to "what are you doing / why are you stalled?" from real state."""

STALL_SECONDS = 20
ACTIVE = {"queued", "running"}
HOLD_STAGES = {"staged", "inspection"}  # as run_digest.HOLD_STAGES
# Body events that are work even though no op finished: walking between cells,
# a block broken by a step edit, the water plug.
WORK = {"route_progress", "block_broken", "leak_plugged"}


def is_work(kind):
    """A successful op (`*_done`) or other movement / world edit."""
    return kind.endswith("_done") or kind in WORK


def is_failure(kind):
    return kind.endswith("_failed") or kind == "route_stopped"


def last_failure(events_tail):
    """The newest *_failed / route_stopped reason in the event tail, or None."""
    for event in reversed(events_tail or []):
        kind = event.get("type", "")
        if is_failure(kind):
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
    # body work (stamped `at` by the reader) is activity too
    seen = [e.get("at") or 0 for e in events_tail or [] if is_work(e.get("type", ""))]
    age = now - max([progress.get("updated_at") or 0, *seen])
    if age <= STALL_SECONDS:
        return f"Running {job}: {stage}{counts}."
    reason = last_failure(events_tail) or "no failure reported"
    return f"Stalled on {job} ({stage}{counts}), no progress for {age:.0f}s; {reason}."
