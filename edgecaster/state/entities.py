# edgecaster/state/entities.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple, Any

Pos = Tuple[int, int]


@dataclass
class Entity:
    """Generic thing that exists on a map tile.

    Actors, items, features, spell effects, etc. will all be Entities.
    """
    id: str
    name: str
    pos: Pos

    # Visuals
    glyph: str = "?"
    color: Tuple[int, int, int] = (255, 255, 255)
    render_layer: int = 1    # 0=terrain overlay, 1=items, 2=actors, etc.
    kind: str = "generic"

    # Collision
    blocks_movement: bool = False

    # Metadata
    tags: Dict[str, Any] = field(default_factory=dict)
    statuses: Dict[str, int] = field(default_factory=dict)
    cooldowns: Dict[str, int] = field(default_factory=dict)

    @property
    def x(self) -> int:
        return self.pos[0]

    @property
    def y(self) -> int:
        return self.pos[1]
