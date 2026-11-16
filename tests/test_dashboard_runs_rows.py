from mclab.dashboard_runs import result_row
from mclab.paths import ROOT


def test_a_summary_listing_check_names_as_strings_does_not_crash_history():
    value = {"checks": ["wading", "scaffold"]}
    assert result_row(ROOT / "results/x/summary.json", value) is not None
