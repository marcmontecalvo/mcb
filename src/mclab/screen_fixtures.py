"""Synthetic offline tactical cases with geometry-authored progress labels.

These are controlled fixture observations, not native sensor recordings, unseen
Minecraft layouts, training demonstrations or evidence of gameplay qualification.
Labels are authored from the construction below, independently of motor/safety
implementation. They are never part of the model-visible observation.
"""

import math
from uuid import UUID

from mclab.tactical import CANDIDATES, PROFILE

DEFAULT_INSTRUCTIONS = (
    "Collect one oak log using only visible information. "
    "When no log or drop is visible, explore: move forward "
    "when forward_safe is true, otherwise turn to seek safe "
    "visible space. An unseen goal is not a reason to stop. "
    "When a log is visible, aim at it, approach safely until "
    "the ray hits it, then keep choosing mine until it breaks. "
    "Collect a visible log drop after breaking. Stop only "
    "when movement is unsafe and no useful turn or mining "
    "action is available, or collection is complete. "
    "Choose one bounded behavior. Return only compact JSON "
    '{"choice":"ID"} with no whitespace or explanation.'
)

CATEGORIES = (
    "exploration",
    "aiming_error",
    "mining",
    "collection",
    "obstacle_or_ledge",
    "unknown_or_stale",
)


def _cell(position, block="minecraft:air", observed_tick=100):
    return {
        "position": list(position),
        "block": block,
        "properties": {},
        "collision_boxes": []
        if block == "minecraft:air"
        else [[0.0, 0.0, 0.0, 1.0, 1.0, 1.0]],
        "fluid": "minecraft:empty",
        "replaceable": block == "minecraft:air",
        "observed_tick": observed_tick,
    }


def _observation(index):
    # Translation changes coordinates, not claimed layout diversity. Heading and
    # target/hazard variations provide the controlled differences within groups.
    x, z = index * 16, -index * 11
    return {
        "schema_version": 1,
        "sensor_profile": "local-visibility-v1",
        "tick": 100,
        "body": {
            "position": [x + 0.5, 80.0, z + 0.5],
            "yaw": 0.0,
            "pitch": 0.0,
            "ray": {},
        },
        "visible_blocks": [
            _cell(
                (x + dx, y, z + dz), "minecraft:stone" if y == 79 else "minecraft:air"
            )
            for dx in range(-6, 7)
            for dz in range(-6, 7)
            for y in (79, 80, 81)
        ],
        "visible_entities": [],
    }


def _replace(observation, cell):
    position = cell["position"]
    observation["visible_blocks"] = [
        old for old in observation["visible_blocks"] if old["position"] != position
    ] + [cell]


def _ahead_cell(observation, distance=1):
    x, y, z = observation["body"]["position"]
    yaw = math.radians(observation["body"]["yaw"])
    return [
        math.floor(x - math.sin(yaw) * distance),
        int(y),
        math.floor(z + math.cos(yaw) * distance),
    ]


def _bearing(dx, dz):
    return math.degrees(math.atan2(-dx, dz))


def _turn_for_offset(offset):
    return "turn_left" if offset > 0 else "turn_right"


def _make_case(category, variant, index):
    observation = _observation(index)
    body = observation["body"]
    bx, _, bz = body["position"]
    facts = {"forward_safe": True, "goal_visible": False}
    if category == "exploration":
        body["yaw"] = [-135, -90, -45, 0, 45, 90, 135, 180][variant]
        body["pitch"] = [-25, -15, -5, 0, 5, 15, 25, 35][variant]
        actions = ["forward"]
        rationale = (
            "No goal is visible and the complete forward footprint has fresh "
            "flat support and headroom. The frozen instructions require forward "
            "exploration when safe; stopping or repeated empty-target aiming "
            "does not make progress."
        )
    elif category == "aiming_error":
        dx, dz = [(0, 4), (4, 4), (4, 0), (4, -4), (0, -4), (-4, -4), (-4, 0), (-4, 4)][
            variant
        ]
        offset = [-110, -80, -50, 50, 80, 110, -140, 140][variant]
        body["yaw"] = (_bearing(dx, dz) + offset + 180) % 360 - 180
        body["pitch"] = -20 + variant * 5
        target = [int(bx - 0.5) + dx, 80, int(bz - 0.5) + dz]
        _replace(observation, _cell(target, "minecraft:oak_log"))
        # A visible wall lies in the current (wrong) heading, not on the
        # eye-to-target line. It independently rules out forward movement.
        wall = _ahead_cell(observation, 1)
        for y in (80, 81):
            _replace(observation, _cell([wall[0], y, wall[2]], "minecraft:stone"))
        actions = ["aim_log", _turn_for_offset(offset)]
        facts.update(
            forward_safe=False,
            goal_visible=True,
            target=target,
            yaw_error_degrees=-offset,
            wall=wall,
        )
        rationale = (
            "A visible log is off the crosshair; the current forward heading "
            "meets a wall. Aim directly or turn in the direction reducing the "
            "target yaw error. Opposite rotation and mining do not progress."
        )
    elif category == "mining":
        dx, dz = [(0, 2), (2, 2), (2, 0), (2, -2), (0, -2), (-2, -2), (-2, 0), (-2, 2)][
            variant
        ]
        body["yaw"] = _bearing(dx, dz)
        body["pitch"] = math.degrees(math.atan2(1.12, math.hypot(dx, dz)))
        target = [int(bx - 0.5) + dx, 80, int(bz - 0.5) + dz]
        _replace(observation, _cell(target, "minecraft:oak_log"))
        body["ray"] = {"block": "minecraft:oak_log", "position": list(target)}
        actions = ["mine"]
        facts.update(
            goal_visible=True,
            target=target,
            ray_hits_target=True,
            eye_to_center_distance=math.sqrt(dx * dx + dz * dz + 1.12**2),
        )
        # The diagonal target may be beyond the conservative 2-block guard, so
        # forward safety is not used to author this label. Walking is unnecessary:
        # the native ray already identifies the reachable target.
        facts.pop("forward_safe")
        rationale = (
            "The crosshair ray hits the visible log within ordinary reach. "
            "Mining advances the objective; aiming is already satisfied and "
            "walking or turning interrupts the ready mining alignment."
        )
    elif category == "collection":
        dx, dz = [(0, 3), (3, 3), (3, 0), (3, -3), (0, -3), (-3, -3), (-3, 0), (-3, 3)][
            variant
        ]
        # Every current heading points more than 90 degrees away, so walking
        # forward increases drop distance rather than offering another valid
        # progress action. The two labels remain collect or turn toward it.
        offset = [-100, -110, -120, 100, 110, 120, -130, 130][variant]
        body["yaw"] = (_bearing(dx, dz) + offset + 180) % 360 - 180
        target = [bx + dx, 80.2, bz + dz]
        observation["visible_entities"] = [
            {
                "uuid": str(UUID(int=index + 1)),
                "type": "minecraft:item",
                "item": "minecraft:oak_log",
                "count": 1,
                "position": target,
            }
        ]
        actions = ["collect", _turn_for_offset(offset)]
        facts.update(goal_visible=True, target=target, yaw_error_degrees=-offset)
        rationale = (
            "A visible oak-log drop is away from the current heading on flat "
            "open ground. Collect aims toward that drop; turning toward it also "
            "makes progress. No block log exists to aim at or mine."
        )
    elif category == "obstacle_or_ledge":
        body["yaw"] = [0, 90, 180, -90][variant % 4]
        hazard = _ahead_cell(observation, 1)
        if variant < 4:
            for y in (80, 81):
                _replace(
                    observation, _cell([hazard[0], y, hazard[2]], "minecraft:stone")
                )
            hazard_kind = "wall"
        else:
            _replace(observation, _cell([hazard[0], 79, hazard[2]]))
            hazard_kind = "ledge"
        actions = ["turn_left", "turn_right"]
        facts.update(forward_safe=False, hazard=hazard, hazard_kind=hazard_kind)
        rationale = (
            "No goal is visible and forward meets a wall or missing support. "
            "Either turn seeks another heading; walking is unsafe and target "
            "behaviors have no visible target."
        )
    else:
        body["yaw"] = [0, 90, 180, -90][variant % 4]
        hazard = _ahead_cell(observation, 1)
        position = [hazard[0], 79, hazard[2]]
        if variant < 4:
            observation["visible_blocks"] = [
                cell
                for cell in observation["visible_blocks"]
                if cell["position"] != position
            ]
            hazard_kind = "unknown_support"
        else:
            _replace(observation, _cell(position, "minecraft:stone", observed_tick=94))
            hazard_kind = "stale_support"
        actions = ["turn_left", "turn_right"]
        facts.update(forward_safe=False, hazard=hazard, hazard_kind=hazard_kind)
        rationale = (
            "The forward footprint includes unknown support or support observed "
            "six ticks ago, beyond the five-tick freshness bound. Either turn "
            "seeks another heading. Missing or stale geometry is not clear air."
        )
    return {
        "state_id": f"synthetic-{category}-{variant}",
        "synthetic": True,
        "category": category,
        "variant": variant,
        "observation": observation,
        "valid_actions": actions,
        "review_status": "pending_independent_geometry_review",
        "rationale": rationale,
        "geometry_facts": facts,
    }


def build_fixture(instructions=None):
    """Return a fresh deterministic definition; do not write runtime artifacts."""
    if instructions is not None and (
        not isinstance(instructions, str) or not instructions
    ):
        raise ValueError("Instructions must be a nonempty string")
    return {
        "schema_version": 1,
        "profile": PROFILE,
        "fixture_version": "synthetic-tactical48-v1",
        "synthetic": True,
        "scored": False,
        "training_ready": False,
        "gameplay_qualified": False,
        "candidates": dict(CANDIDATES),
        "instructions": instructions or DEFAULT_INSTRUCTIONS,
        "label_policy": (
            "Useful immediate progress under the frozen instructions, not all "
            "physically safe controls. When a visible target needs yaw correction, "
            "both its specialized behavior and the turn reducing yaw error count. "
            "Redundant aiming, empty-target behaviors, stopping while progress is "
            "possible, unsafe walking and opposite turns do not count."
        ),
        "limitations": [
            "Synthetic permitted-state fixtures, not native visibility proofs.",
            "Eight variants per category are not eight unseen physical layouts.",
            "No placement candidate; construction and insufficient-dirt skill "
            "untested.",
            "Labels require independent geometry review before scored screening.",
        ],
        "states": [
            _make_case(category, variant, category_index * 8 + variant)
            for category_index, category in enumerate(CATEGORIES)
            for variant in range(8)
        ],
    }
