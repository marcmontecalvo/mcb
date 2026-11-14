# ruff: noqa: E501
"""Read-only view of a knowledge pack (docs/KNOWLEDGE_PACK_FORMAT.md, schema 1).

Pure data loading: no world access, no HTTP. Everything returned is immutable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCHEMA = 1


class KnowledgePackError(ValueError):
    """The pack is missing a file or has an unsupported schema."""


@dataclass(frozen=True)
class Ingredient:
    options: tuple[str, ...]  # item ids and/or "#tag" ids; any one satisfies it
    count: int


@dataclass(frozen=True)
class Recipe:
    id: str
    type: str
    station: str | None  # None: a type the pack does not parse
    ingredients: tuple[Ingredient, ...]
    result_item: str | None
    result_count: int
    cook_ticks: int | None  # only for smelting-like recipes


@dataclass(frozen=True)
class Drop:
    item: str
    min: int
    max: int
    chance: float = 1.0

    @property
    def expected(self) -> float:
        return self.chance * (self.min + self.max) / 2


@dataclass(frozen=True)
class Transform:
    """An item made by using a tool on a placed block (strip a log with an axe).
    `in_place` transforms change a block in the world and yield no item."""

    id: str
    input: str
    tool: str  # tool kind: axe, hoe, shovel
    output: str
    count: int = 1
    in_place: bool = False


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _read(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise KnowledgePackError(f"knowledge pack file missing: {path}")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        found = data.get("schema") if isinstance(data, dict) else type(data).__name__
        raise KnowledgePackError(
            f"{path}: unsupported schema {found!r}, this build reads schema {SCHEMA}"
        )
    return data


def _parse_recipe(raw: Mapping[str, Any]) -> Recipe:
    result = raw.get("result") or {}
    return Recipe(
        id=raw["id"],
        type=raw.get("type", ""),
        station=raw.get("station"),
        ingredients=tuple(
            Ingredient(tuple(i["options"]), int(i.get("count", 1)))
            for i in raw.get("ingredients") or ()
        ),
        result_item=result.get("item"),
        result_count=int(result.get("count", 1)),
        cook_ticks=raw.get("cook_ticks"),
    )


@dataclass(frozen=True)
class Knowledge:
    pack: Mapping[str, Any]
    items: Mapping[str, Mapping[str, Any]]
    blocks: Mapping[str, Mapping[str, Any]]
    item_tags: Mapping[str, tuple[str, ...]]
    block_tags: Mapping[str, tuple[str, ...]]
    recipes: tuple[Recipe, ...]
    drop_tiers: tuple[str, ...]
    drops: Mapping[str, Mapping[str, tuple[Drop, ...]]]
    sources: Mapping[str, Mapping[str, Any]]
    _by_result: Mapping[str, tuple[Recipe, ...]]
    transforms: tuple[Transform, ...] = ()
    mobs: Mapping[str, Mapping[str, Any]] = MappingProxyType({})
    foods: Mapping[str, Mapping[str, float]] = MappingProxyType({})
    ores: Mapping[str, Mapping[str, Any]] = MappingProxyType(
        {}
    )  # ores.json placed features
    ore_bounds: tuple[int, int] = (-64, 319)  # overworld min/max build Y
    ore_overrides: Mapping[str, int] = MappingProxyType({})

    def mob_drops(self, entity: str) -> tuple[Drop, ...]:
        """Drops of a mob killed by a player (mobs.json `killed_by_player`)."""
        return tuple(
            Drop(d["item"], d.get("min", 1), d.get("max", 1), d.get("chance", 1.0))
            for d in (self.mobs.get(entity) or {}).get("killed_by_player", ())
        )

    def recipes_for(self, item_id: str) -> tuple[Recipe, ...]:
        return self._by_result.get(item_id, ())

    def expand(self, option: str) -> tuple[str, ...]:
        """Item ids an ingredient option stands for (a tag is expanded)."""
        if option.startswith("#"):
            return self.item_tags.get(option, ())
        return (option,)

    def matches(self, option: str, item_id: str) -> bool:
        return item_id in self.expand(option)

    def drops_for(self, block_id: str, tier: str) -> tuple[Drop, ...]:
        """Drops of a block with the correct tool at `tier`; a missing tier key
        means the same as the next lower tier."""
        table = self.drops.get(block_id)
        if not table or tier not in self.drop_tiers:
            return ()
        for name in reversed(self.drop_tiers[: self.drop_tiers.index(tier) + 1]):
            if name in table:
                return table[name]
        return ()


def _parse_transforms(path: str | Path | None) -> tuple[Transform, ...]:
    if path is None:
        return ()
    return tuple(
        Transform(
            t["id"],
            t["input"],
            t["tool"],
            t["output"],
            int(t.get("count", 1)),
            bool(t.get("in_place", False)),
        )
        for t in _read(Path(path))["transforms"]
    )


def load_pack(
    path: str | Path,
    sources: str | Path | None = None,
    transforms: str | Path | None = None,
    foods: str | Path | None = None,
    mobs: str | Path | None = None,
    ore_overrides: str | Path | None = None,
) -> Knowledge:
    """Load `knowledge/<pack_id>/`. `sources` overrides the pack's sources.json;
    `transforms` is an optional hand-written transforms file; `foods` an optional
    nutrition table; `mobs` overrides the pack's optional mobs.json (absent: none);
    `ore_overrides` defaults to the optional `<knowledge>/sources/ores-overrides.json`."""
    root = Path(path)
    ore_data = _read(root / "ores.json", required=False)
    overrides_path = (
        Path(ore_overrides)
        if ore_overrides
        else root.parent / "sources" / "ores-overrides.json"
    )
    overrides = (
        {k: int(v) for k, v in _read(overrides_path)["overrides"].items()}
        if overrides_path.is_file()
        else {}
    )
    pack = _read(root / "pack.json")
    items = _read(root / "items.json")["items"]
    blocks = _read(root / "blocks.json")["blocks"]
    tags = _read(root / "tags.json")
    recipes = tuple(_parse_recipe(r) for r in _read(root / "recipes.json")["recipes"])
    drops = _read(root / "drops.json")
    src = _read(
        Path(sources) if sources else root / "sources.json",
        required=sources is not None,
    )

    by_result: dict[str, list[Recipe]] = {}
    for r in recipes:
        if r.result_item is not None:
            by_result.setdefault(r.result_item, []).append(r)

    return Knowledge(
        pack=_freeze(pack),
        items=_freeze(items),
        blocks=_freeze(blocks),
        item_tags=_freeze({k: list(v) for k, v in tags.get("items", {}).items()}),
        block_tags=_freeze({k: list(v) for k, v in tags.get("blocks", {}).items()}),
        recipes=recipes,
        drop_tiers=tuple(drops.get("tools", ())),
        drops=_freeze(
            {
                block: {
                    tier: tuple(
                        Drop(
                            d["item"],
                            d.get("min", 1),
                            d.get("max", 1),
                            d.get("chance", 1.0),
                        )
                        for d in entries
                    )
                    for tier, entries in table.items()
                }
                for block, table in drops.get("drops", {}).items()
            }
        ),
        sources=_freeze(src.get("sources", {})),
        _by_result=MappingProxyType({k: tuple(v) for k, v in by_result.items()}),
        transforms=_parse_transforms(transforms),
        mobs=_freeze(
            _read(
                Path(mobs) if mobs else root / "mobs.json", required=mobs is not None
            ).get("mobs", {})
        ),
        foods=_freeze(_read(Path(foods)).get("foods", {}) if foods else {}),
        ores=_freeze(ore_data.get("ores", {})),
        ore_bounds=(ore_data.get("min_y", -64), ore_data.get("max_y", 319)),
        ore_overrides=MappingProxyType(overrides),
    )


# ---- ores: where a block generates, from the game's own placed features (ores.json)


def _anchor(a: Mapping[str, Any], lo: int, hi: int) -> int:
    if "absolute" in a:
        return int(a["absolute"])
    if "above_bottom" in a:
        return lo + int(a["above_bottom"])
    return hi - int(a["below_top"])


def _mean(p: Any) -> float:
    """Expected value of an int provider (plain int, or {type, min_inclusive, max_inclusive, value})."""
    if isinstance(p, (int, float)):
        return float(p)
    if "value" in p:
        return float(p["value"])
    if "min_inclusive" in p:
        return (p["min_inclusive"] + p["max_inclusive"]) / 2
    return 1.0  # ponytail: weighted_list/clamped providers read as 1, upgrade if an ore uses one


def _pmf(h: Mapping[str, Any], lo: int, hi: int) -> dict[int, float]:
    """Probability of each Y for a height provider (mirrors the game's sample())."""
    if "absolute" in h or "above_bottom" in h or "below_top" in h:
        return {_anchor(h, lo, hi): 1.0}
    kind = str(h.get("type", "")).removeprefix("minecraft:")
    a, b = _anchor(h["min_inclusive"], lo, hi), _anchor(h["max_inclusive"], lo, hi)
    inner = int(h.get("inner", 1))
    out: dict[int, float] = {}

    def add(y: int, w: float) -> None:
        out[y] = out.get(y, 0.0) + w

    if b < a:
        return {a: 1.0}
    rng = b - a
    if kind == "trapezoid" and int(h.get("plateau", 0)) < rng:
        start = (rng - int(h.get("plateau", 0))) // 2
        end = rng - start
        for x in range(end + 1):
            for y in range(start + 1):
                add(a + x + y, 1 / ((end + 1) * (start + 1)))
    elif kind == "biased_to_bottom" and rng - inner + 1 > 0:
        n = rng - inner + 1
        for limit in range(n):
            for y in range(limit + inner):
                add(a + y, 1 / (n * (limit + inner)))
    elif kind == "very_biased_to_bottom" and rng - inner + 1 > 0:
        uppers = range(a + inner, b + 1)
        for u in uppers:
            for bu in range(a, u):
                top = bu - 1 + inner
                for y in range(a, top + 1):
                    add(y, 1 / (len(uppers) * (u - a) * (top - a + 1)))
    else:  # uniform, constant, and any unknown type read as a flat band
        for y in range(a, b + 1):
            add(y, 1 / (rng + 1))
    return out


@dataclass(frozen=True)
class OreFeature:
    id: str
    blocks: tuple[str, ...]  # ore block ids this feature places
    size: int
    per_chunk: float  # expected veins per chunk (count * 1/rarity)
    density: Mapping[int, float]  # Y -> probability a vein is centred there
    biomes: tuple[str, ...]


def _base(block: str) -> str:
    return block.replace("deepslate_", "", 1)


def ore_features(k: Knowledge, block: str) -> tuple[OreFeature, ...]:
    """Placed features generating `block` or its deepslate variant (all biomes listed)."""
    lo, hi = k.ore_bounds
    want, out = _base(block), []
    for fid, f in k.ores.items():
        feat = f.get("feature")
        if not feat:
            continue
        blocks = tuple(
            t["state"]["Name"] if isinstance(t["state"], dict) else t["state"]
            for t in feat.get("targets", ())
            if "state" in t
        )
        if not any(_base(b) == want for b in blocks):
            continue
        per, height = 1.0, {}
        for m in f.get("placement", ()):
            t = str(m.get("type", "")).removeprefix("minecraft:")
            if t == "count":
                per *= _mean(m["count"])
            elif t == "rarity_filter":
                per /= m["chance"]
            elif t == "height_range":
                height = _pmf(m["height"], lo, hi)
        if height:
            out.append(
                OreFeature(
                    fid,
                    blocks,
                    int(feat["size"]),
                    per,
                    MappingProxyType(height),
                    tuple(f.get("biomes", ())),
                )
            )
    return tuple(out)


def best_y(k: Knowledge, block: str, y_max: int = 63) -> int | None:
    """Y with the most expected `block` ore per chunk layer, summed over every placed
    feature that makes it (iron_ore and deepslate_iron_ore both count). Each feature is
    weighted by the share of ore-bearing biomes it appears in, so a mountain-only band
    does not beat a worldwide one. `knowledge/sources/ores-overrides.json`
    (`{"schema":1,"overrides":{"minecraft:iron_ore":16}}`) replaces the result per block.
    Heights above `y_max` (default sea level) are ignored: mostly sky, unreachable by digging.
    None: no feature makes it."""
    if block in k.ore_overrides:
        return k.ore_overrides[block]
    feats = ore_features(k, block)
    if not feats:
        return None
    every = {b for f in k.ores.values() for b in f.get("biomes", ())} or {""}
    score: dict[int, float] = {}
    for f in feats:
        w = f.per_chunk * f.size * (len(f.biomes) / len(every) if f.biomes else 1.0)
        for y, p in f.density.items():
            if y > y_max:
                continue
            score[y] = score.get(y, 0.0) + w * p
    if not score:
        return None
    top = max(score.values())
    ys = sorted(y for y, v in score.items() if v >= top - 1e-12)
    return ys[len(ys) // 2]
