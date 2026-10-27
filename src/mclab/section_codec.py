"""Lossless palette sections for admitted spatial facts (unknown stays absent)."""

import json
import zlib
from copy import deepcopy

OBSERVATION_FIELDS = frozenset(
    {"observed_tick", "observation_session", "observed_at_ns"}
)


def section_key(position):
    return tuple(v // 16 for v in position)


def encode_section(cells):
    palette, indices, rows = [], {}, []
    for cell in sorted(cells, key=lambda c: c["position"]):
        x, y, z = cell["position"]
        facts = {
            k: v
            for k, v in cell.items()
            if k != "position" and k not in OBSERVATION_FIELDS
        }
        provenance = {k: cell[k] for k in OBSERVATION_FIELDS if k in cell}
        canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
        if canonical not in indices:
            indices[canonical] = len(palette)
            palette.append(facts)
        rows.append(
            [(x % 16) * 256 + (y % 16) * 16 + z % 16, indices[canonical], provenance]
        )
    return zlib.compress(
        json.dumps([palette, rows], separators=(",", ":")).encode(), level=3
    )


def decode_section(key, payload):
    palette, rows = json.loads(zlib.decompress(payload))
    result = []
    for offset, index, provenance in rows:
        # Each decoded record owns its nested facts, even when palette entries repeat.
        facts = deepcopy(palette[index])
        facts.update(provenance)
        facts["position"] = [
            key[0] * 16 + offset // 256,
            key[1] * 16 + (offset // 16) % 16,
            key[2] * 16 + offset % 16,
        ]
        result.append(facts)
    return result
