from dataclasses import dataclass, field
from typing import Dict
from edgecaster import config


@dataclass
class Character:
    name: str
    generator: str  # "koch", "branch", "zigzag"
    illuminator: str  # "radius" or "neighbors"
    stats: Dict[str, int]  # con, agi, int, res
    point_pool: int = 0
    # Runtime progression currency (earned at level-up, spent on stat growth).
    # Keep on Character so it naturally persists with character state.
    advancement_points: int = 0
    custom_pattern: list | None = None  # optional list of points defining a custom generator
    player_class: str | None = None
    seed: int | None = None
    use_random_seed: bool = False
    # Optional character species (used for anatomy schema / templates).
    species: str | None = None
    # Optional actor template override for the player (e.g., species base body).
    template_id: str | None = None
    # Optional chakra initialization payload (dict from ChakraState.to_dict()).
    chakra_init: dict | None = None
    # Per-character ability bar layout (ordering + grouping); built lazily by AbilityBarState.
    ability_layout: dict | None = None
    # Per-character reputation with each faction (faction_id -> score).
    reputation: Dict[str, int] = field(default_factory=dict)


def default_character() -> Character:
    stats = {"con": 3, "agi": 2, "int": 2, "res": 3}
    return Character(
        name="Pandaemonium",
        generator="custom",
        illuminator="radius",
        stats=stats,
        point_pool=4,
        advancement_points=0,
        custom_pattern=None,
        player_class=None,
        seed=config.default_seed,
        use_random_seed=False,
        species="human",
        template_id="human_base",
    )
