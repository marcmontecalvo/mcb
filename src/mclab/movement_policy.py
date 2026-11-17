"""Development movement permissions; frozen legacy navigation stays unchanged."""

from dataclasses import asdict, dataclass

# Longest drop a route may plan (Java RouteMotor.HURT_DROP_MAX). Past 3 blocks a drop
# hurts; the companion asks for more only as a last way out (companion_body.hurt_drop).
HURT_DROP_MAX = 12


@dataclass(frozen=True)
class MovementPolicy:
    allow_diagonals: bool = False
    allow_sprint: bool = False
    max_drop: int = 1
    allow_shapes: bool = False
    max_gap: int = 0
    allow_swim: bool = False
    allow_climb: bool = False

    def __post_init__(self):
        if (
            type(self.allow_climb) is not bool
            or type(self.allow_diagonals) is not bool
            or type(self.allow_sprint) is not bool
            or type(self.allow_shapes) is not bool
            or type(self.allow_swim) is not bool
        ):
            raise ValueError("Movement permissions must be booleans")
        if type(self.max_drop) is not int or not 0 <= self.max_drop <= HURT_DROP_MAX:
            raise ValueError("Solid-ground drop must be between zero and twelve blocks")
        if type(self.max_gap) is not int or not 0 <= self.max_gap <= 2:
            raise ValueError("Gap jumps must be between zero and two blocks")

    def payload(self):
        value = asdict(self)
        if not self.allow_climb:  # Old requests stay byte-identical.
            del value["allow_climb"]
        return value

    @classmethod
    def from_payload(cls, value):
        if (
            not isinstance(value, dict)
            or not value.keys() <= cls.__dataclass_fields__.keys()
        ):
            raise ValueError("Invalid movement profile fields")
        return cls(**value)
