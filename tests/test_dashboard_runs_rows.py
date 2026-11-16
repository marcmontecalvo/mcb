from mclab.paths import ROOT

from mclab.dashboard_runs import result_row


def test_a_summary_listing_check_names_as_strings_does_not_crash_history():
    row = result_row(ROOT / "results/x/summary.json", {"checks": ["wading", "scaffold"]})
    assert row is not None
