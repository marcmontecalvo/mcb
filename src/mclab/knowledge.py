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
) -> Knowledge:
    """Load `knowledge/<pack_id>/`. `sources` overrides the pack's sources.json;
    `transforms` is an optional hand-written transforms file; `foods` an optional
    nutrition table; `mobs` overrides the pack's optional mobs.json (absent: none)."""
    root = Path(path)
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
    )
