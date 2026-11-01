"""Minimal big-endian Java NBT reader and writer (gzip), standard library only.

Reading returns plain Python values: compounds are dicts, lists are lists,
byte/short/int/long arrays are lists of ints, numbers are int/float.

Writing needs tag types, so values are typed pairs ``(tag_id, payload)``:
a compound payload is ``{name: typed}``; a list payload is
``(element_tag_id, [payload, ...])``; arrays are lists of ints.
"""

import gzip
import struct

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE = range(7)
BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(7, 13)

_NUMBER = {
    BYTE: ">b",
    SHORT: ">h",
    INT: ">i",
    LONG: ">q",
    FLOAT: ">f",
    DOUBLE: ">d",
}
_ARRAY = {BYTE_ARRAY: ("b", 1), INT_ARRAY: ("i", 4), LONG_ARRAY: ("q", 8)}


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("NBT data ends unexpectedly")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        return self.take(self.unpack(">H")).decode("utf-8", "surrogatepass")

    def payload(self, tag: int):
        if tag in _NUMBER:
            return self.unpack(_NUMBER[tag])
        if tag in _ARRAY:
            fmt, width = _ARRAY[tag]
            count = self.unpack(">i")
            return list(struct.unpack(f">{count}{fmt}", self.take(count * width)))
        if tag == STRING:
            return self.string()
        if tag == LIST:
            element = self.unpack("B")
            return [self.payload(element) for _ in range(self.unpack(">i"))]
        if tag == COMPOUND:
            result = {}
            while (child := self.unpack("B")) != END:
                name = self.string()
                result[name] = self.payload(child)
            return result
        raise ValueError(f"unknown NBT tag type {tag}")


def loads(data: bytes) -> dict:
    """Parse (optionally gzip'd) NBT bytes and return the root compound."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    reader = _Reader(data)
    if reader.unpack("B") != COMPOUND:
        raise ValueError("NBT root is not a compound")
    reader.string()
    return reader.payload(COMPOUND)


def load(path) -> dict:
    with open(path, "rb") as handle:
        return loads(handle.read())


def _string(text: str) -> bytes:
    raw = text.encode("utf-8", "surrogatepass")
    return struct.pack(">H", len(raw)) + raw


def _payload(tag: int, value) -> bytes:
    if tag in _NUMBER:
        return struct.pack(_NUMBER[tag], value)
    if tag in _ARRAY:
        fmt, _ = _ARRAY[tag]
        return struct.pack(">i", len(value)) + struct.pack(
            f">{len(value)}{fmt}", *value
        )
    if tag == STRING:
        return _string(value)
    if tag == LIST:
        element, items = value
        body = b"".join(_payload(element, item) for item in items)
        return struct.pack(">Bi", element, len(items)) + body
    if tag == COMPOUND:
        body = b"".join(
            struct.pack(">B", child) + _string(name) + _payload(child, item)
            for name, (child, item) in value.items()
        )
        return body + b"\x00"
    raise ValueError(f"unknown NBT tag type {tag}")


def dumps(root: dict, name: str = "", compress: bool = True) -> bytes:
    """Serialize ``{name: (tag_id, payload)}`` as a root compound."""
    raw = struct.pack(">B", COMPOUND) + _string(name) + _payload(COMPOUND, root)
    return gzip.compress(raw) if compress else raw
