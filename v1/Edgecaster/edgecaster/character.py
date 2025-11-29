from dataclasses import dataclass
from typing import Dict


@dataclass
class Character:
    name: str
    generator: str  # "koch", "branch", "zigzag"
    illuminator: str  # "radius" or "neighbors"
    stats: Dict[str, int]  # con, agi, int, res
    point_pool: int = 0


def default_character() -> Character:
    stats = {"con": 3, "agi": 2, "int": 2, "res": 3}
    return Character(
        name="Pandaemonium",
        generator="koch",
        illuminator="radius",
        stats=stats,
        point_pool=4,
    )
