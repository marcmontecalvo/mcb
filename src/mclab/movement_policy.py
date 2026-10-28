"""Development movement permissions; frozen legacy navigation stays unchanged."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MovementPolicy:
    allow_diagonals: bool = False
    allow_sprint: bool = False
    max_drop: int = 1
    allow_shapes: bool = False
    max_gap: int = 0
    allow_swim: bool = False

    def __post_init__(self):
        if (
            type(self.allow_diagonals) is not bool
            or type(self.allow_sprint) is not bool
            or type(self.allow_shapes) is not bool
            or type(self.allow_swim) is not bool
        ):
            raise ValueError("Movement permissions must be booleans")
        if type(self.max_drop) is not int or not 0 <= self.max_drop <= 3:
            raise ValueError("Solid-ground drop must be between zero and three blocks")
        if type(self.max_gap) is not int or not 0 <= self.max_gap <= 2:
            raise ValueError("Gap jumps must be between zero and two blocks")

    def payload(self):
        return asdict(self)

    @classmethod
    def from_payload(cls, value):
        if (
            not isinstance(value, dict)
            or not value.keys() <= cls.__dataclass_fields__.keys()
        ):
            raise ValueError("Invalid movement profile fields")
        return cls(**value)
