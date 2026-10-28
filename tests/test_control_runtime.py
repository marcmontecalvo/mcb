import sys

import pytest

from mclab.control_runtime import thread_switch_interval


def test_switch_interval_is_scoped_and_restored_after_failure():
    previous = sys.getswitchinterval()
    with pytest.raises(RuntimeError, match="interrupted"):
        with thread_switch_interval(1):
            assert sys.getswitchinterval() == pytest.approx(0.001)
            with thread_switch_interval(0.5):
                assert sys.getswitchinterval() == pytest.approx(0.0005)
            assert sys.getswitchinterval() == pytest.approx(0.001)
            raise RuntimeError("interrupted")
    assert sys.getswitchinterval() == previous
    with thread_switch_interval():
        assert sys.getswitchinterval() == previous


@pytest.mark.parametrize("value", [True, 0, 0.01, 6, float("nan"), float("inf"), "1"])
def test_invalid_switch_interval_does_not_change_runtime(value):
    previous = sys.getswitchinterval()
    with pytest.raises(ValueError):
        with thread_switch_interval(value):
            pytest.fail("invalid setting reached body")
    assert sys.getswitchinterval() == previous
