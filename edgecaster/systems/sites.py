# edgecaster/systems/sites.py
"""
Site Registry - Runtime management of placed sites.

The SiteRegistry holds all procedurally placed sites for a game run,
providing efficient lookup by coordinate and managing visibility state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, TYPE_CHECKING

import yaml

from edgecaster.climate import Biome
from edgecaster.state.sites import SiteSpec, SiteTypeConfig, SiteVisibility

if TYPE_CHECKING:
    from edgecaster.game import Game


# Cache for site type configs
_site_types_cache: Optional[Dict[str, SiteTypeConfig]] = None


def load_site_types() -> Dict[str, SiteTypeConfig]:
    """Load site type configurations from site_types.yaml."""
    global _site_types_cache
    if _site_types_cache is not None:
        return _site_types_cache

    content_dir = Path(__file__).resolve().parent.parent / "content"
    yaml_path = content_dir / "site_types.yaml"

    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}

    configs: Dict[str, SiteTypeConfig] = {}

    for kind, cfg in data.items():
        if not isinstance(cfg, dict):
            continue

        # Parse biome lists
        primary = []
        for b in cfg.get("primary_biomes", []):
            try:
                primary.append(Biome[b] if isinstance(b, str) else Biome(b))
            except (KeyError, ValueError):
                pass

        secondary = []
        for b in cfg.get("secondary_biomes", []):
            try:
                secondary.append(Biome[b] if isinstance(b, str) else Biome(b))
            except (KeyError, ValueError):
                pass

        # Parse color
        color_raw = cfg.get("map_color", [255, 255, 255])
        if isinstance(color_raw, list) and len(color_raw) >= 3:
            color = (int(color_raw[0]), int(color_raw[1]), int(color_raw[2]))
        else:
            color = (255, 255, 255)

        configs[kind] = SiteTypeConfig(
            kind=kind,
            name=cfg.get("name", kind.replace("_", " ").title()),
            primary_biomes=primary,
            secondary_biomes=secondary,
            elevation_min=float(cfg.get("elevation_min", 0.0)),
            elevation_max=float(cfg.get("elevation_max", 1.0)),
            temperature_min=float(cfg.get("temperature_min", 0.0)),
            temperature_max=float(cfg.get("temperature_max", 1.0)),
            moisture_min=float(cfg.get("moisture_min", 0.0)),
            moisture_max=float(cfg.get("moisture_max", 1.0)),
            corruption_max=float(cfg.get("corruption_max", 1.0)),
            water_proximity=cfg.get("water_proximity"),
            spacing=int(cfg.get("spacing", 10)),
            rarity=float(cfg.get("rarity", 1.0)),
            max_count=cfg.get("max_count"),
            structures=cfg.get("structures", []),
            npc_pool=cfg.get("npc_pool", []),
            enemy_pool=cfg.get("enemy_pool", []),
            map_glyph=cfg.get("map_glyph", "?"),
            map_color=color,
        )

    _site_types_cache = configs
    return configs


def clear_site_types_cache() -> None:
    """Clear the cached site types (for testing)."""
    global _site_types_cache
    _site_types_cache = None


class SiteRegistry:
    """
    Runtime registry of all placed sites.

    Provides efficient lookup by ID or coordinate, and manages
    discovery/visibility state changes.
    """

    def __init__(self) -> None:
        self._sites: Dict[str, SiteSpec] = {}
        self._by_coord: Dict[Tuple[int, int, int], SiteSpec] = {}
        self._by_zone: Dict[Tuple[int, int], List[SiteSpec]] = {}

    def add(self, spec: SiteSpec) -> None:
        """Add a site to the registry."""
        self._sites[spec.id] = spec
        self._by_coord[spec.coord] = spec

        zone = spec.zone_coord
        if zone not in self._by_zone:
            self._by_zone[zone] = []
        self._by_zone[zone].append(spec)

    def get(self, site_id: str) -> Optional[SiteSpec]:
        """Get a site by its ID."""
        return self._sites.get(site_id)

    def get_at(self, coord: Tuple[int, int, int]) -> Optional[SiteSpec]:
        """Get a site at a specific coordinate (zx, zy, depth)."""
        return self._by_coord.get(coord)

    def get_at_zone(self, zx: int, zy: int) -> List[SiteSpec]:
        """Get all sites at a zone coordinate (any depth)."""
        return self._by_zone.get((zx, zy), [])

    def get_visible(self) -> List[SiteSpec]:
        """Get all sites that should be visible on the world map."""
        return [s for s in self._sites.values() if s.is_visible_on_map]

    def get_by_kind(self, kind: str) -> List[SiteSpec]:
        """Get all sites of a specific type."""
        return [s for s in self._sites.values() if s.kind == kind]

    def mark_discovered(self, site_id: str) -> bool:
        """Mark a site as discovered. Returns True if state changed."""
        spec = self._sites.get(site_id)
        if spec and spec.visibility != SiteVisibility.DISCOVERED:
            spec.mark_discovered()
            return True
        return False

    def mark_rumored(self, site_id: str) -> bool:
        """Mark a site as rumored. Returns True if state changed."""
        spec = self._sites.get(site_id)
        if spec and spec.visibility == SiteVisibility.HIDDEN:
            spec.mark_rumored()
            return True
        return False

    def discover_at_coord(self, coord: Tuple[int, int, int]) -> Optional[SiteSpec]:
        """Mark any site at this coord as discovered. Returns the site if found."""
        spec = self.get_at(coord)
        if spec:
            spec.mark_discovered()
        return spec

    def __iter__(self) -> Iterator[SiteSpec]:
        """Iterate over all sites."""
        return iter(self._sites.values())

    def __len__(self) -> int:
        """Number of registered sites."""
        return len(self._sites)

    def all_coords(self) -> List[Tuple[int, int, int]]:
        """Get all coordinates with sites."""
        return list(self._by_coord.keys())

    def to_save_data(self) -> List[Dict[str, Any]]:
        """Serialize registry to save data."""
        result = []
        for spec in self._sites.values():
            result.append({
                "id": spec.id,
                "kind": spec.kind,
                "coord": list(spec.coord),
                "seed": spec.seed,
                "biome": int(spec.biome),
                "tags": spec.tags,
                "visibility": spec.visibility.value,
                "cleared": spec.cleared,
                "visited": spec.visited,
            })
        return result

    @classmethod
    def from_save_data(cls, data: List[Dict[str, Any]]) -> "SiteRegistry":
        """Deserialize registry from save data."""
        registry = cls()
        for entry in data:
            try:
                spec = SiteSpec(
                    id=entry["id"],
                    kind=entry["kind"],
                    coord=tuple(entry["coord"]),
                    seed=entry["seed"],
                    biome=Biome(entry["biome"]),
                    tags=entry.get("tags", {}),
                    visibility=SiteVisibility(entry.get("visibility", "hidden")),
                    cleared=entry.get("cleared", False),
                    visited=entry.get("visited", False),
                )
                registry.add(spec)
            except (KeyError, ValueError, TypeError):
                continue
        return registry
