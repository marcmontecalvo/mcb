"""Validate and expand development palette samples; absence is never air."""

import math

NATIVE_SAMPLE_LIMIT = 8192 + 17 * 9 * 17
GEOMETRY_FIELDS = {"block", "fluid", "replaceable", "properties", "collision_boxes"}


def expand_terrain(observation):
    if "terrain_sections" not in observation:
        return observation
    batch = observation["terrain_sections"]
    if (
        observation.get("sensor_profile") != "awareness-v1"
        or "visible_blocks" in observation
        or not isinstance(batch, dict)
        or batch.keys()
        != {
            "schema_version",
            "encoding",
            "unknown_is_air",
            "sample_tick",
            "known_cells",
            "palette",
            "sections",
            "encoding_ms",
        }
        or type(batch["schema_version"]) is not int
        or batch["schema_version"] != 1
        or batch["encoding"] != "sparse_section_palette"
        or batch["unknown_is_air"] is not False
    ):
        raise ValueError("Invalid compact terrain schema")
    tick, count = batch["sample_tick"], batch["known_cells"]
    if (
        type(tick) is not int
        or tick < 0
        or tick
        != observation.get("awareness", {}).get("coverage", {}).get("sample_tick")
        or type(count) is not int
        or not 0 <= count <= NATIVE_SAMPLE_LIMIT
        or type(batch["encoding_ms"]) not in (int, float)
        or not math.isfinite(batch["encoding_ms"])
        or batch["encoding_ms"] < 0
    ):
        raise ValueError("Invalid compact terrain count or epoch")
    palette, sections = batch["palette"], batch["sections"]
    if (
        not isinstance(palette, list)
        or not isinstance(sections, list)
        or len(palette) > count
        or len(sections) > count
    ):
        raise ValueError("Invalid compact terrain palette bounds")
    for state in palette:
        if (
            not isinstance(state, dict)
            or state.keys() != GEOMETRY_FIELDS
            or type(state["block"]) is not str
            or type(state["fluid"]) is not str
            or type(state["replaceable"]) is not bool
            or not isinstance(state["properties"], dict)
            or not all(
                type(k) is str and type(v) is str
                for k, v in state["properties"].items()
            )
            or not isinstance(state["collision_boxes"], list)
        ):
            raise ValueError("Invalid compact terrain state")
        for box in state["collision_boxes"]:
            if (
                not isinstance(box, list)
                or len(box) != 6
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            ):
                raise ValueError("Invalid compact terrain collision shape")
    cells, seen = [], set()
    for section in sections:
        if not isinstance(section, dict) or section.keys() != {"section", "cells"}:
            raise ValueError("Invalid compact terrain section")
        key, rows = section["section"], section["cells"]
        if (
            not isinstance(key, list)
            or len(key) != 3
            or any(type(v) is not int or abs(v) > 2**27 for v in key)
            or not isinstance(rows, list)
            or len(rows) > 4096
        ):
            raise ValueError("Invalid compact terrain section bounds")
        for row in rows:
            if (
                not isinstance(row, list)
                or len(row) != 3
                or any(type(v) is not int for v in row)
            ):
                raise ValueError("Invalid compact terrain row")
            offset, state, age = row
            if (
                not 0 <= offset < 4096
                or not 0 <= state < len(palette)
                or not 0 <= age <= tick
            ):
                raise ValueError("Invalid compact terrain row bounds")
            p = (
                key[0] * 16 + offset // 256,
                key[1] * 16 + offset // 16 % 16,
                key[2] * 16 + offset % 16,
            )
            if p in seen or len(cells) >= count:
                raise ValueError("Duplicate or excess compact terrain cell")
            seen.add(p)
            cells.append({**palette[state], "position": list(p), "observed_tick": age})
    if len(cells) != count:
        raise ValueError("Incomplete compact terrain sample")
    return {
        **{k: v for k, v in observation.items() if k != "terrain_sections"},
        "visible_blocks": cells,
    }
