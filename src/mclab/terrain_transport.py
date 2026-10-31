"""Validate and expand development palette samples; absence is never air."""

import math
from types import MappingProxyType

# Broad near refresh plus a 5x5 vertical spine's eight extra layers.
NATIVE_SAMPLE_LIMIT = 8192 + 17 * 9 * 17 + 5 * 8 * 5
# "sections" preset: <=65536 cost/update, uniform sections cost 64, dense 4096.
SECTION_CELLS = 4096
MAX_BULK_SECTIONS = 1024 + 16
BULK_CELL_LIMIT = MAX_BULK_SECTIONS * SECTION_CELLS
BASE_KEYS = {
    "schema_version",
    "encoding",
    "unknown_is_air",
    "sample_tick",
    "known_cells",
    "palette",
    "sections",
    "encoding_ms",
}
BULK_KEYS = {"bulk_cells", "dense_sections", "uniform_sections"}
GEOMETRY_FIELDS = {"block", "fluid", "replaceable", "properties", "collision_boxes"}


class ExpandedTerrain(dict):
    """Decoded facts plus their validated position index, borrowed immutably.

    The index is an internal attribute, not a wire field. Records are the same
    objects as visible_blocks; consumers must not mutate the borrowed observation.
    Freshness is still evaluated against the current control tick at every use.
    """

    def __init__(self, values, cell_index, bulk=(), palette=()):
        super().__init__(values)
        self.cell_index = MappingProxyType(cell_index)
        # Whole sections as (section key, palette index | list of 4096, tick).
        # Never expanded to per-cell dicts; `palette` indexes into geometry facts.
        self.bulk = tuple(bulk)
        self.palette = tuple(palette)


def expand_terrain(observation):
    if "terrain_sections" not in observation:
        return observation
    batch = observation["terrain_sections"]
    if (
        observation.get("sensor_profile") != "awareness-v1"
        or "visible_blocks" in observation
        or not isinstance(batch, dict)
        or not (batch.keys() == BASE_KEYS or batch.keys() == BASE_KEYS | BULK_KEYS)
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
    bulk_cells = batch.get("bulk_cells", 0)
    if (
        type(bulk_cells) is not int
        or not 0 <= bulk_cells <= BULK_CELL_LIMIT
        or not isinstance(palette, list)
        or not isinstance(sections, list)
        or len(palette) > count + bulk_cells
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
    cells, index = [], {}
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
            if p in index or len(cells) >= count:
                raise ValueError("Duplicate or excess compact terrain cell")
            cell = {**palette[state], "position": list(p), "observed_tick": age}
            index[p] = cell
            cells.append(cell)
    if len(cells) != count:
        raise ValueError("Incomplete compact terrain sample")
    bulk = _bulk_sections(batch, palette, tick, bulk_cells)
    return ExpandedTerrain(
        {
            **{k: v for k, v in observation.items() if k != "terrain_sections"},
            "visible_blocks": cells,
        },
        index,
        bulk,
        palette,
    )


def _bulk_sections(batch, palette, tick, bulk_cells):
    """Validate dense/uniform whole sections; absence in them is still unknown."""
    if "bulk_cells" not in batch:
        return ()
    dense, uniform = batch["dense_sections"], batch["uniform_sections"]
    if (
        not isinstance(dense, list)
        or not isinstance(uniform, list)
        or len(dense) + len(uniform) > MAX_BULK_SECTIONS
        or bulk_cells != (len(dense) + len(uniform)) * SECTION_CELLS
    ):
        raise ValueError("Invalid compact terrain bulk sections")
    result, seen = [], set()
    for kind, rows in (("dense", dense), ("uniform", uniform)):
        payload = "states" if kind == "dense" else "state"
        for row in rows:
            if not isinstance(row, dict) or row.keys() != {"section", "tick", payload}:
                raise ValueError("Invalid compact terrain bulk section")
            key, age, value = row["section"], row["tick"], row[payload]
            if (
                not isinstance(key, list)
                or len(key) != 3
                or any(type(v) is not int or abs(v) > 2**27 for v in key)
                or type(age) is not int
                or not 0 <= age <= tick
                or tuple(key) in seen
            ):
                raise ValueError("Invalid compact terrain bulk section key")
            seen.add(tuple(key))
            if kind == "dense":
                if (
                    not isinstance(value, list)
                    or len(value) != SECTION_CELLS
                    or set(map(type, value)) != {int}
                    or min(value) < 0
                    or max(value) >= len(palette)
                ):
                    raise ValueError("Invalid compact terrain dense states")
            elif type(value) is not int or not 0 <= value < len(palette):
                raise ValueError("Invalid compact terrain uniform state")
            result.append((tuple(key), value, age))
    return result
