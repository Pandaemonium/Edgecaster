# edgecaster/state/entities.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

Pos = Tuple[int, int]
ZoneCoord = Tuple[int, int, int]


@dataclass
class Entity:
    """Generic thing that exists in the world.

    Canonical truth:
    - abs_pos is the authoritative world position in absolute tile coordinates.
    - pos is a *cached* local position (typically relative to a loaded zone/LevelState).

    During the ongoing migration away from zone-metaphysics, some legacy code may
    still construct entities with only `pos`. In those cases, abs_pos can be
    backfilled deterministically given a zone coordinate and zone dimensions.
    """
    id: str
    name: str
    pos: Pos

    # Canonical world position (absolute tiles). Optional during migration.
    abs_pos: Optional[Pos] = None

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
        return int(self.pos[0])

    @property
    def y(self) -> int:
        return int(self.pos[1])

    def ensure_abs_pos(self, *, zone_coord: ZoneCoord, zone_w: int, zone_h: int) -> Pos:
        """Ensure abs_pos is populated, deriving from (zone_coord, pos) if needed."""
        if self.abs_pos is None:
            zx, zy, _z = zone_coord
            self.abs_pos = (
                int(zx) * int(zone_w) + int(self.pos[0]),
                int(zy) * int(zone_h) + int(self.pos[1]),
            )
        return (int(self.abs_pos[0]), int(self.abs_pos[1]))

    def set_abs_pos(self, abs_pos: Pos) -> None:
        """Set canonical ABS position."""
        self.abs_pos = (int(abs_pos[0]), int(abs_pos[1]))
