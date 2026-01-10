# edgecaster/state/sites.py
"""
Site data structures for procedurally placed POIs.

A Site represents a meaningful location on the overmap (city, village, shrine, etc.)
that is placed based on biome suitability and realized when the player enters the zone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from edgecaster.climate import Biome


class SiteVisibility(Enum):
    """Visibility state for sites on the world map."""
    HIDDEN = "hidden"       # Not shown on map, player doesn't know it exists
    RUMORED = "rumored"     # Marker shown, but details unknown
    DISCOVERED = "discovered"  # Fully revealed


@dataclass
class SiteSpec:
    """
    Specification for a placed site.

    This is the stable placement record created during world generation.
    It contains all the information needed to realize the site when entered.
    """
    id: str                           # Unique stable ID (e.g., "bird_aerie_42")
    kind: str                         # Site type (e.g., "bird_aerie", "fishing_village")
    coord: Tuple[int, int, int]       # (zone_x, zone_y, depth)
    seed: int                         # Derived from world seed + coord + kind
    biome: Biome                      # Primary biome at this location
    tags: Dict[str, Any] = field(default_factory=dict)  # Faction, size, etc.
    visibility: SiteVisibility = SiteVisibility.HIDDEN

    # Runtime state (not part of placement, updated during play)
    cleared: bool = False             # Has the site been "cleared" (if applicable)
    visited: bool = False             # Has the player entered this zone

    @property
    def is_visible_on_map(self) -> bool:
        """Whether this site should show a marker on the world map."""
        return self.visibility != SiteVisibility.HIDDEN

    @property
    def zone_coord(self) -> Tuple[int, int]:
        """Get just the (x, y) zone coordinates."""
        return (self.coord[0], self.coord[1])

    def mark_discovered(self) -> None:
        """Mark the site as discovered (fully revealed)."""
        self.visibility = SiteVisibility.DISCOVERED
        self.visited = True

    def mark_rumored(self) -> None:
        """Mark the site as rumored (known to exist but not visited)."""
        if self.visibility == SiteVisibility.HIDDEN:
            self.visibility = SiteVisibility.RUMORED


@dataclass
class SiteTypeConfig:
    """
    Configuration for a type of site (loaded from site_types.yaml).

    Defines the suitability criteria and content for a site type.
    """
    kind: str                         # Unique identifier
    name: str                         # Display name

    # Biome affinity
    primary_biomes: List[Biome] = field(default_factory=list)
    secondary_biomes: List[Biome] = field(default_factory=list)

    # Suitability thresholds
    elevation_min: float = 0.0
    elevation_max: float = 1.0
    temperature_min: float = 0.0
    temperature_max: float = 1.0
    moisture_min: float = 0.0
    moisture_max: float = 1.0
    corruption_max: float = 1.0
    water_proximity: Optional[int] = None  # Must be within N tiles of water

    # Placement constraints
    spacing: int = 10                 # Minimum tiles between same-type sites
    rarity: float = 1.0               # Probability multiplier
    max_count: Optional[int] = None   # Maximum number to place (None = unlimited)

    # Content
    structures: List[str] = field(default_factory=list)
    npc_pool: List[str] = field(default_factory=list)
    enemy_pool: List[str] = field(default_factory=list)

    # Display
    map_glyph: str = "?"
    map_color: Tuple[int, int, int] = (255, 255, 255)
