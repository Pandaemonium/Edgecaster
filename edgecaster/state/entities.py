# edgecaster/state/entities.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from edgecaster.state.chakra_component import ChakraComponent

Pos = Tuple[int, int]
ZoneCoord = Tuple[int, int, int]
RectF = Tuple[float, float, float, float]


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

    # Phase 1 substrate fields (backward-compatible defaults).
    # Unification note: these are the right per-entity identity hooks, but
    # relationship authority still needs to move into the shared graph store so
    # reparenting, provenance, and geometry invalidation do not rely on ad hoc
    # attribute patching alone.
    entity_id: Optional[str] = None
    semantic_id: Optional[str] = None
    proto_id: Optional[str] = None
    lod_band: str = "micro"
    parent_entity_id: Optional[str] = None
    socket_id: Optional[str] = None
    chakra_component: Optional["ChakraComponent"] = None

    # Canonical world position (absolute tiles). Optional during migration.
    abs_pos: Optional[Pos] = None

    # Visuals
    glyph: str = "?"
    color: Tuple[int, int, int] = (255, 255, 255)
    render_layer: int = 1    # 0=terrain overlay, 1=items, 2=actors, etc.
    kind: str = "generic"

    # Collision
    blocks_movement: bool = False

    # Canonical runtime footprint fields (Option A).
    # footprint_* values are in tile coordinates (float rectangles).
    footprint_local: Optional[RectF] = None
    footprint_abs: Optional[RectF] = None
    footprint_follows_pos: bool = True
    _footprint_anchor_local: Optional[Pos] = None
    _footprint_anchor_abs: Optional[Pos] = None

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
        old = self.abs_pos
        new_abs = (int(abs_pos[0]), int(abs_pos[1]))
        self.abs_pos = new_abs

        if self.footprint_follows_pos and self.footprint_abs is not None and old is not None:
            dx = int(new_abs[0]) - int(old[0])
            dy = int(new_abs[1]) - int(old[1])
            x0, y0, x1, y1 = self.footprint_abs
            self.footprint_abs = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            self._footprint_anchor_abs = new_abs
        elif self._footprint_anchor_abs is None:
            self._footprint_anchor_abs = new_abs

    def set_pos(self, pos: Pos) -> None:
        """Set local position and keep local footprint in sync when anchored."""
        old = self.pos
        new_pos = (int(pos[0]), int(pos[1]))
        self.pos = new_pos

        if self.footprint_follows_pos and self.footprint_local is not None:
            dx = int(new_pos[0]) - int(old[0])
            dy = int(new_pos[1]) - int(old[1])
            x0, y0, x1, y1 = self.footprint_local
            self.footprint_local = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            self._footprint_anchor_local = new_pos
        elif self._footprint_anchor_local is None:
            self._footprint_anchor_local = new_pos
