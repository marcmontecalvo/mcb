"""progress.json: what a watcher of a live job reads."""

from mclab import companion_progress as cp


def test_a_stage_change_keeps_the_counts_last_written(tmp_path):
    """Run 20261122T013145553434Z: each stage change wrote done/total as null, so a
    watcher saw ``done=0/0`` for the whole placing stage."""
    progress = cp.Progress(tmp_path, job="build")
    progress.write(stage="placing", done=40, total=1227, detail="steps=40")
    progress.write(stage="refilling materials")
    got = cp.read_progress(tmp_path)
    assert got["job"] == "build" and got["stage"] == "refilling materials"
    assert (got["done"], got["total"], got["detail"]) == (40, 1227, "steps=40")
    progress.write(stage="placing", done=41)
    got = cp.read_progress(tmp_path)
    assert (got["stage"], got["done"], got["total"]) == ("placing", 41, 1227)


def test_the_stage_is_kept_when_only_the_counts_change(tmp_path):
    progress = cp.Progress(tmp_path, job="build")
    progress.write(stage="cleanup")
    progress.write(done=7, total=9)
    got = cp.read_progress(tmp_path)
    assert (got["stage"], got["done"], got["total"]) == ("cleanup", 7, 9)
    assert got["updated_at"] > 0
