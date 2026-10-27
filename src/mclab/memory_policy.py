"""Operator controls for map retention, independent of acquisition reach."""

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MemoryPolicy:
    memory_mode: str = "persistent"
    use_historical_geometry: bool = True
    active_map_budget_mb: float = 128
    persistence_queue_budget_mb: float = 128

    def __post_init__(self):
        if type(self.memory_mode) is not str or self.memory_mode not in {
            "current",
            "session",
            "persistent",
        }:
            raise ValueError("Invalid memory mode")
        if type(self.use_historical_geometry) is not bool:
            raise ValueError("Historical access must be a boolean")
        for value in (self.active_map_budget_mb, self.persistence_queue_budget_mb):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= 4096
            ):
                raise ValueError("Invalid memory byte budget")

    def payload(self):
        return asdict(self)

    @classmethod
    def from_payload(cls, value):
        if (
            not isinstance(value, dict)
            or value.keys() != cls.__dataclass_fields__.keys()
        ):
            raise ValueError("Memory profile requires exactly the schema fields")
        return cls(**value)
