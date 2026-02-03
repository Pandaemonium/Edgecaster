"""Yoga-compliant POI data structures.

POIs (Points of Interest) exist in absolute space, independent of zone loading.
This module provides data structures that support:
- Multi-zone POIs (spanning multiple zones)
- Multiple POIs per zone
- Nested POIs (city -> shops -> shelves)
- Stable identity across observation changes

Yoga Principles (from the_yoga.txt):
- Absolute space is truth; zones are caches
- Macro entities have stable identity and footprint in ABS space
- Camera attention != world generation (POIs exist independently)
- Observation selects fidelity, not existence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any


@dataclass(frozen=True)
class ABSRect:
    """Axis-aligned bounding box in absolute tile coordinates.

    Used to define POI footprints in world space. Zones are derived from
    this footprint, not the other way around.

    Coordinates follow the convention:
    - x0, y0: minimum corner (inclusive)
    - x1, y1: maximum corner (exclusive)
    """

    x0: int  # min x (inclusive)
    y0: int  # min y (inclusive)
    x1: int  # max x (exclusive)
    y1: int  # max y (exclusive)

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> Tuple[int, int]:
        return ((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)

    def contains_point(self, x: int, y: int) -> bool:
        """Check if a point is within this rectangle."""
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1

    def overlaps(self, other: "ABSRect") -> bool:
        """Check if two rectangles overlap."""
        return not (
            self.x1 <= other.x0
            or self.x0 >= other.x1
            or self.y1 <= other.y0
            or self.y0 >= other.y1
        )

    def contains_rect(self, other: "ABSRect") -> bool:
        """Check if this rectangle fully contains another."""
        return (
            self.x0 <= other.x0
            and self.y0 <= other.y0
            and self.x1 >= other.x1
            and self.y1 >= other.y1
        )

    def zone_coords(self, zone_w: int, zone_h: int) -> Set[Tuple[int, int]]:
        """Return all (zx, zy) zone coordinates this rect overlaps.

        Args:
            zone_w: Width of a zone in tiles
            zone_h: Height of a zone in tiles

        Returns:
            Set of (zx, zy) tuples for all zones that overlap this rect
        """
        if zone_w <= 0 or zone_h <= 0:
            return set()

        zx0 = self.x0 // zone_w
        zy0 = self.y0 // zone_h
        # x1, y1 are exclusive, so subtract 1 before dividing
        zx1 = (self.x1 - 1) // zone_w if self.x1 > self.x0 else zx0
        zy1 = (self.y1 - 1) // zone_h if self.y1 > self.y0 else zy0

        return {(zx, zy) for zx in range(zx0, zx1 + 1) for zy in range(zy0, zy1 + 1)}

    @classmethod
    def from_zone_coord(
        cls, zx: int, zy: int, zone_w: int, zone_h: int
    ) -> "ABSRect":
        """Create a rect covering a single zone."""
        return cls(
            x0=zx * zone_w,
            y0=zy * zone_h,
            x1=(zx + 1) * zone_w,
            y1=(zy + 1) * zone_h,
        )

    @classmethod
    def from_center_size(
        cls, cx: int, cy: int, width: int, height: int
    ) -> "ABSRect":
        """Create a rect centered at a point with given dimensions."""
        hw = width // 2
        hh = height // 2
        return cls(
            x0=cx - hw,
            y0=cy - hh,
            x1=cx - hw + width,
            y1=cy - hh + height,
        )


@dataclass
class NPCSpawnSpecV2:
    """NPC spawn specification using absolute coordinates.

    Supports both new ABS positions and legacy zone-local offsets for
    backwards compatibility.
    """

    npc_id: str
    name: Optional[str] = None
    glyph: Optional[str] = None
    color: Optional[Tuple[int, int, int]] = None
    description: Optional[str] = None

    # ABS positions - preferred (list allows multiple spawn candidates)
    abs_positions: List[Tuple[int, int]] = field(default_factory=list)

    # Legacy: zone-local offsets from POI anchor (still supported)
    offsets: List[Tuple[int, int]] = field(default_factory=list)

    # Additional tags for customization
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StructureSpec:
    """Structure placement within a POI."""

    kind: str  # Structure type (e.g., "item_depot", "shop_building")

    # Position - one of these should be set
    abs_rect: Optional[ABSRect] = None  # Explicit ABS footprint
    relative_offset: Optional[Tuple[int, int]] = None  # Offset from POI anchor

    # Configuration
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntitySpawnSpec:
    """Entity spawn specification within a POI."""

    proto_id: str  # Entity prototype ID

    # ABS positions for spawning
    abs_positions: List[Tuple[int, int]] = field(default_factory=list)

    # Or relative to POI anchor
    offsets: List[Tuple[int, int]] = field(default_factory=list)

    # Spawn parameters
    count: int = 1
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class POISpec:
    """Yoga-compliant POI specification.

    POIs exist in absolute space with:
    - Stable identity (id never changes)
    - Stable footprint (ABSRect in world coordinates)
    - Optional hierarchy (parent/child relationships)
    """

    # Identity
    id: str  # Unique stable ID
    kind: str  # POI type (e.g., "city", "shop", "dungeon", "lair")
    name: str  # Display name

    # Spatial - ABS space is truth
    footprint: ABSRect  # Bounding box in absolute tiles
    depth: int = 0  # Z-level (0 = surface)
    anchor_abs: Tuple[int, int] = (0, 0)  # Reference point for content placement

    # Hierarchy
    parent_id: Optional[str] = None  # Containing POI (for nesting)
    child_ids: List[str] = field(default_factory=list)  # Contained POIs

    # Content specifications (templates, not instances)
    npc_specs: List[NPCSpawnSpecV2] = field(default_factory=list)
    structure_specs: List[StructureSpec] = field(default_factory=list)
    entity_specs: List[EntitySpawnSpec] = field(default_factory=list)

    # Generation
    seed: int = 0  # For deterministic content generation
    tags: Dict[str, Any] = field(default_factory=dict)

    # Realization tracking (runtime, not serialized in definition)
    realized_zones: Set[Tuple[int, int]] = field(default_factory=set)

    def get_zone_coords(self, zone_w: int, zone_h: int) -> Set[Tuple[int, int]]:
        """Get all zone coordinates this POI overlaps."""
        return self.footprint.zone_coords(zone_w, zone_h)

    def is_multi_zone(self, zone_w: int, zone_h: int) -> bool:
        """Check if this POI spans multiple zones."""
        return len(self.get_zone_coords(zone_w, zone_h)) > 1


@dataclass
class POIContentState:
    """Persistent state for POI contents.

    Tracks what has happened to a POI's contents independent of zone loading.
    This ensures killed NPCs stay dead, removed items stay gone, etc.
    """

    poi_id: str

    # Spawned entity tracking (by stable key within POI)
    spawned_npcs: Dict[str, str] = field(default_factory=dict)  # spec_key -> entity_id
    spawned_structures: Dict[str, str] = field(default_factory=dict)
    spawned_entities: Dict[str, str] = field(default_factory=dict)

    # Content modification tracking
    killed_npcs: Set[str] = field(default_factory=set)  # entity_ids that died
    removed_items: Set[str] = field(default_factory=set)  # items taken/destroyed
    modified_structures: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Completion/clear state
    cleared: bool = False
    visited: bool = False
    first_visited_tick: Optional[int] = None
