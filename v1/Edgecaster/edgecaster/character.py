from dataclasses import dataclass
from typing import Dict
from edgecaster import config


@dataclass
class Character:
    name: str
    generator: str  # "koch", "branch", "zigzag"
    illuminator: str  # "radius" or "neighbors"
    stats: Dict[str, int]  # con, agi, int, res
    point_pool: int = 0
    custom_pattern: list | None = None  # optional list of points defining a custom generator
    player_class: str | None = None
    seed: int | None = None
    use_random_seed: bool = False


def default_character() -> Character:
    stats = {"con": 3, "agi": 2, "int": 2, "res": 3}
    return Character(
        name="Pandaemonium",
        generator="custom",
        illuminator="radius",
        stats=stats,
        point_pool=4,
        custom_pattern=None,
        player_class=None,
        seed=config.default_seed,
        use_random_seed=False,
    )
