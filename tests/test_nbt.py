import gzip

import pytest

from mclab import nbt


def test_round_trip_all_tag_types():
    root = {
        "b": (nbt.BYTE, -5),
        "s": (nbt.SHORT, -300),
        "i": (nbt.INT, 70000),
        "l": (nbt.LONG, -(2**62)),
        "f": (nbt.FLOAT, 1.5),
        "d": (nbt.DOUBLE, -2.25),
        "ba": (nbt.BYTE_ARRAY, [1, -2, 3]),
        "str": (nbt.STRING, "héllo"),
        "li": (nbt.LIST, (nbt.INT, [1, 2, 3])),
        "lc": (nbt.LIST, (nbt.COMPOUND, [{"a": (nbt.BYTE, 1)}, {}])),
        "c": (nbt.COMPOUND, {"inner": (nbt.STRING, "x")}),
        "ia": (nbt.INT_ARRAY, [-1, 2]),
        "la": (nbt.LONG_ARRAY, [-1, 2**63 - 1]),
    }
    got = nbt.loads(nbt.dumps(root))
    assert got["b"] == -5 and got["s"] == -300 and got["i"] == 70000
    assert got["l"] == -(2**62) and got["f"] == 1.5 and got["d"] == -2.25
    assert got["ba"] == [1, -2, 3] and got["str"] == "héllo"
    assert got["li"] == [1, 2, 3] and got["lc"] == [{"a": 1}, {}]
    assert got["c"] == {"inner": "x"} and got["ia"] == [-1, 2]
    assert got["la"] == [-1, 2**63 - 1]


def test_uncompressed_and_gzip_both_load():
    raw = nbt.dumps({"a": (nbt.INT, 1)}, compress=False)
    assert nbt.loads(raw) == nbt.loads(gzip.compress(raw)) == {"a": 1}


def test_truncated_data_raises():
    with pytest.raises(ValueError):
        nbt.loads(nbt.dumps({"a": (nbt.STRING, "abc")}, compress=False)[:-2])
