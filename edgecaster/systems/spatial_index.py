"""Unified spatial and realization index.

This module provides the SpatialIndex, which replaces the disparate
AttentionCellStore, WorldEntityIndex, and POIRegistry. It tracks entities
in absolute space alongside their realization state (proxy, staged, realized),
and provides fast spatial and semantic queries.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from edgecaster.state.pois import ABSRect, POISpec


@dataclass
class SpatialIndexEntry:
    """A single record in the unified Spatial Index."""
    entity_id: str
    obj: Any
    rect: Tuple[float, float, float, float]
    zz: int
    realization_state: str  # "proxy", "staged", "realized"
    semantic_id: Optional[str] = None
    kind: str = "generic"
    source: Optional[str] = None


class SpatialIndex:
    """
    A scale-aware spatial hash for tracking entities at all realization levels.
    """
    _REALIZATION_PRIORITY = {
        "proxy": 10,
        "collapsed": 10,
        "staged": 20,
        "realized": 30,
        "live": 30,
    }

    def __init__(self, bin_size: int = 32) -> None:
        self.bin_size = max(1, int(bin_size))
        self.entries: Dict[str, SpatialIndexEntry] = {}

        # Spatial buckets: (bin_size_level, bx, by, zz) -> Set[entity_id]
        self.spatial_bins: Dict[Tuple[int, int, int, int], Set[str]] = {}
        # entity_id -> Set[spatial_bin_keys]
        self.entity_bins: Dict[str, Set[Tuple[int, int, int, int]]] = {}

        # Semantic index (e.g. for quest lookups or named POIs)
        self.semantic_index: Dict[str, Set[str]] = {}
        # Kind index (e.g. for finding all "legendary_lair" markers)
        self.kind_index: Dict[str, Set[str]] = {}

    @staticmethod
    def _get_entity_id(obj: Any) -> str:
        try:
            return str(getattr(obj, "entity_id", "") or getattr(obj, "id", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _matches_filter(value: Any, expected: object | None) -> bool:
        if expected is None:
            return True
        if isinstance(expected, (set, frozenset, tuple, list)):
            expected_values = {str(item) for item in expected}
            return str(value) in expected_values
        return str(value) == str(expected)

    @classmethod
    def _entry_matches(
        cls,
        entry: SpatialIndexEntry,
        *,
        source: object | None = None,
        realization_state: object | None = None,
        kind: object | None = None,
    ) -> bool:
        return (
            cls._matches_filter(entry.source, source)
            and cls._matches_filter(entry.realization_state, realization_state)
            and cls._matches_filter(entry.kind, kind)
        )

    def _calculate_bins(self, rect: Tuple[float, float, float, float], zz: int) -> List[Tuple[int, int, int, int]]:
        x0, y0, x1, y1 = rect
        max_dim = max(1.0, x1 - x0, y1 - y0)
        level = 0
        cell_size = float(self.bin_size)
        # Find the power-of-two bin level that best fits the entity's dimensions
        while cell_size < max_dim and level < 16:
            cell_size *= 2.0
            level += 1

        bx0 = int(math.floor(x0 / cell_size))
        by0 = int(math.floor(y0 / cell_size))
        bx1 = int(math.floor((x1 - 1e-6) / cell_size))
        by1 = int(math.floor((y1 - 1e-6) / cell_size))

        out = []
        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                out.append((level, bx, by, zz))
        return out

    def add_or_update(
        self,
        obj: Any,
        rect: Tuple[float, float, float, float],
        zz: int,
        realization_state: str,
        *,
        semantic_id: Optional[str] = None,
        kind: Optional[str] = None,
        source: Optional[str] = None,
    ) -> str:
        entity_id = self._get_entity_id(obj)
        if not entity_id:
            raise ValueError("Object must have a valid id or entity_id")

        existing = self.entries.get(entity_id)
        if existing is not None and source is not None and existing.source != source:
            old_priority = self._REALIZATION_PRIORITY.get(str(existing.realization_state), 0)
            new_priority = self._REALIZATION_PRIORITY.get(str(realization_state), 0)
            if old_priority > new_priority:
                return entity_id

        x0, y0, x1, y1 = rect
        if x1 < x0: x0, x1 = x1, x0
        if y1 < y0: y0, y1 = y1, y0
        normalized_rect = (float(x0), float(y0), float(x1), float(y1))

        if not kind:
            kind = str(getattr(obj, "kind", "generic") or "generic")

        if not semantic_id:
            semantic_id = str(getattr(obj, "semantic_id", "") or "") or None

        # Clean up old locations and indices before updating
        self.remove(entity_id)

        entry = SpatialIndexEntry(
            entity_id=entity_id,
            obj=obj,
            rect=normalized_rect,
            zz=int(zz),
            realization_state=str(realization_state),
            semantic_id=semantic_id,
            kind=kind,
            source=str(source) if source else None,
        )

        self.entries[entity_id] = entry

        bins = self._calculate_bins(normalized_rect, int(zz))
        self.entity_bins[entity_id] = set(bins)
        for b in bins:
            self.spatial_bins.setdefault(b, set()).add(entity_id)

        if semantic_id:
            self.semantic_index.setdefault(semantic_id, set()).add(entity_id)

        self.kind_index.setdefault(kind, set()).add(entity_id)

        return entity_id

    def remove(self, entity_id: str, *, source: Optional[str] = None) -> bool:
        eid = str(entity_id)
        entry = self.entries.get(eid)
        if not entry:
            return False
        if source is not None and entry.source != str(source):
            return False
        self.entries.pop(eid, None)

        for b in self.entity_bins.pop(eid, set()):
            s = self.spatial_bins.get(b)
            if s:
                s.discard(eid)
                if not s:
                    del self.spatial_bins[b]

        if entry.semantic_id:
            s = self.semantic_index.get(entry.semantic_id)
            if s:
                s.discard(eid)
                if not s:
                    del self.semantic_index[entry.semantic_id]

        s = self.kind_index.get(entry.kind)
        if s:
            s.discard(eid)
            if not s:
                del self.kind_index[entry.kind]

        return True

    def clear_source(self, source: str) -> None:
        """Remove all entries mirrored by a specific legacy/source index."""
        src = str(source)
        for entity_id, entry in list(self.entries.items()):
            if entry.source == src:
                self.remove(entity_id, source=src)

    def get(self, entity_id: str) -> Optional[SpatialIndexEntry]:
        return self.entries.get(str(entity_id))

    def query_rect(
        self,
        rect: Tuple[float, float, float, float],
        zz: int,
        *,
        source: object | None = None,
        realization_state: object | None = None,
        kind: object | None = None,
    ) -> List[SpatialIndexEntry]:
        """Find all indexed entities whose footprint overlaps the ABS query rect."""
        x0, y0, x1, y1 = rect
        if x1 < x0: x0, x1 = x1, x0
        if y1 < y0: y0, y1 = y1, y0
        if x1 == x0 or y1 == y0:
            return []

        seen: Set[str] = set()
        out: List[SpatialIndexEntry] = []

        for level in range(17):
            cell_size = float(self.bin_size) * (2 ** level)
            bx0 = int(math.floor(x0 / cell_size))
            by0 = int(math.floor(y0 / cell_size))
            bx1 = int(math.floor((x1 - 1e-6) / cell_size))
            by1 = int(math.floor((y1 - 1e-6) / cell_size))

            for by in range(by0, by1 + 1):
                for bx in range(bx0, bx1 + 1):
                    b = (level, bx, by, zz)
                    ids = self.spatial_bins.get(b)
                    if not ids:
                        continue
                    for eid in ids:
                        if eid in seen:
                            continue
                        seen.add(eid)
                        entry = self.entries.get(eid)
                        if not entry:
                            continue
                        if not self._entry_matches(
                            entry,
                            source=source,
                            realization_state=realization_state,
                            kind=kind,
                        ):
                            continue
                        ex0, ey0, ex1, ey1 = entry.rect
                        if ex1 <= x0 or ex0 >= x1 or ey1 <= y0 or ey0 >= y1:
                            continue
                        out.append(entry)
        return out

    def query_semantic_id(self, semantic_id: str) -> List[SpatialIndexEntry]:
        ids = self.semantic_index.get(str(semantic_id), set())
        return [self.entries[eid] for eid in ids if eid in self.entries]

    def query_kind(self, kind: str) -> List[SpatialIndexEntry]:
        ids = self.kind_index.get(str(kind), set())
        return [self.entries[eid] for eid in ids if eid in self.entries]

    def query_tag(
        self,
        tag: str,
        value: object | None = None,
        *,
        source: object | None = None,
        realization_state: object | None = None,
        kind: object | None = None,
    ) -> List[SpatialIndexEntry]:
        """Return entries whose object tags contain tag, optionally matching value."""
        tag_key = str(tag)
        out: List[SpatialIndexEntry] = []
        for entry in self.entries.values():
            if not self._entry_matches(
                entry,
                source=source,
                realization_state=realization_state,
                kind=kind,
            ):
                continue
            tags = getattr(entry.obj, "tags", None)
            if not isinstance(tags, dict) or tag_key not in tags:
                continue
            if value is not None:
                found = tags.get(tag_key)
                if found != value and str(found) != str(value):
                    continue
            out.append(entry)
        return out


    def iter_all(
        self,
        *,
        source: object | None = None,
        realization_state: object | None = None,
        kind: object | None = None,
    ) -> List[SpatialIndexEntry]:
        """Return all entries matching the given filters without a spatial constraint."""
        return [
            entry for entry in self.entries.values()
            if self._entry_matches(entry, source=source, realization_state=realization_state, kind=kind)
        ]


def get_game_spatial_index(game: Any) -> Optional[SpatialIndex]:
    """Return the shared game spatial index when this save/runtime has one."""
    return getattr(game, "spatial_index", None)


def query_game_spatial_rect(
    game: Any,
    rect: Tuple[float, float, float, float],
    *,
    zz: int,
    source: object | None = None,
    realization_state: object | None = None,
    kind: object | None = None,
) -> List[SpatialIndexEntry]:
    """Query the shared spatial index, returning an empty list if absent."""
    idx = get_game_spatial_index(game)
    if idx is None:
        return []
    return idx.query_rect(
        rect,
        int(zz),
        source=source,
        realization_state=realization_state,
        kind=kind,
    )


def entry_anchor(entry: SpatialIndexEntry) -> Tuple[float, float]:
    """Return the best ABS anchor for an indexed entry."""
    obj = entry.obj
    ap = getattr(obj, "abs_pos", None)
    if isinstance(ap, (tuple, list)) and len(ap) >= 2:
        try:
            return (float(ap[0]), float(ap[1]))
        except Exception:
            pass
    x0, y0, x1, y1 = entry.rect
    return ((float(x0) + float(x1)) * 0.5, (float(y0) + float(y1)) * 0.5)


def _resolve_poi_spec(
    entry: SpatialIndexEntry,
    *,
    poi_getter: Any = None,
) -> Optional[POISpec]:
    obj = entry.obj
    if isinstance(obj, POISpec):
        return obj
    if callable(poi_getter):
        try:
            poi_id = str(getattr(obj, "id", "") or entry.entity_id or "").strip()
        except Exception:
            poi_id = str(getattr(entry, "entity_id", "") or "").strip()
        if poi_id:
            try:
                spec = poi_getter(poi_id)
                if isinstance(spec, POISpec):
                    return spec
            except Exception:
                pass
    return None


def query_spatial_poi_specs_in_rect(
    spatial_index: Optional[SpatialIndex],
    rect: ABSRect,
    *,
    depth: int,
    poi_getter: Any = None,
) -> List[POISpec]:
    """Return POI specs overlapping *rect* from a shared SpatialIndex."""
    if spatial_index is None:
        return []
    try:
        entries = spatial_index.query_rect(
            (
                float(rect.x0),
                float(rect.y0),
                float(rect.x1),
                float(rect.y1),
            ),
            int(depth),
            source="poi_registry",
        )
    except Exception:
        return []

    out: List[POISpec] = []
    seen: Set[str] = set()
    for entry in entries:
        poi = _resolve_poi_spec(entry, poi_getter=poi_getter)
        if poi is None:
            continue
        try:
            if int(getattr(poi, "depth", 0) or 0) != int(depth):
                continue
        except Exception:
            continue
        try:
            footprint = getattr(poi, "footprint", None)
            if not isinstance(footprint, ABSRect) or not footprint.overlaps(rect):
                continue
        except Exception:
            continue
        poi_id = str(getattr(poi, "id", "") or getattr(entry, "entity_id", "") or "").strip()
        if not poi_id or poi_id in seen:
            continue
        seen.add(poi_id)
        out.append(poi)
    return out


def query_spatial_poi_specs_at_abs_point(
    spatial_index: Optional[SpatialIndex],
    x: int,
    y: int,
    *,
    depth: int,
    poi_getter: Any = None,
) -> List[POISpec]:
    """Return POI specs whose footprints contain an ABS point."""
    rect = ABSRect(x0=int(x), y0=int(y), x1=int(x) + 1, y1=int(y) + 1)
    hits = query_spatial_poi_specs_in_rect(
        spatial_index,
        rect,
        depth=int(depth),
        poi_getter=poi_getter,
    )
    out: List[POISpec] = []
    seen: Set[str] = set()
    for poi in hits:
        try:
            if not poi.footprint.contains_point(int(x), int(y)):
                continue
        except Exception:
            continue
        poi_id = str(getattr(poi, "id", "") or "").strip()
        if not poi_id or poi_id in seen:
            continue
        seen.add(poi_id)
        out.append(poi)
    return out


def _scan_registry_poi_specs(
    poi_registry: Any,
    *,
    rect: Optional[ABSRect] = None,
    point: Optional[Tuple[int, int]] = None,
    depth: int,
) -> List[POISpec]:
    """Fallback POI scan when no shared SpatialIndex is available."""
    if poi_registry is None:
        return []
    try:
        pois = list(iter(poi_registry))
    except Exception:
        return []

    out: List[POISpec] = []
    seen: Set[str] = set()
    for poi in pois:
        if not isinstance(poi, POISpec):
            continue
        try:
            if int(getattr(poi, "depth", 0) or 0) != int(depth):
                continue
        except Exception:
            continue
        try:
            footprint = getattr(poi, "footprint", None)
            if not isinstance(footprint, ABSRect):
                continue
            if rect is not None and not footprint.overlaps(rect):
                continue
            if point is not None and not footprint.contains_point(int(point[0]), int(point[1])):
                continue
        except Exception:
            continue
        poi_id = str(getattr(poi, "id", "") or "").strip()
        if not poi_id or poi_id in seen:
            continue
        seen.add(poi_id)
        out.append(poi)
    return out


def query_game_poi_specs_in_rect(
    game: Any,
    rect: ABSRect,
    *,
    depth: int,
) -> List[POISpec]:
    """Return POI specs overlapping an ABS rect from the shared spatial layer."""
    poi_registry = getattr(game, "poi_registry", None)
    hits = query_spatial_poi_specs_in_rect(
        get_game_spatial_index(game),
        rect,
        depth=int(depth),
        poi_getter=getattr(poi_registry, "get", None),
    )
    if hits:
        return hits
    return _scan_registry_poi_specs(poi_registry, rect=rect, depth=int(depth))


def query_game_poi_specs_at_abs_point(
    game: Any,
    x: int,
    y: int,
    *,
    depth: int,
) -> List[POISpec]:
    """Return POI specs whose footprints contain an ABS point."""
    poi_registry = getattr(game, "poi_registry", None)
    hits = query_spatial_poi_specs_at_abs_point(
        get_game_spatial_index(game),
        int(x),
        int(y),
        depth=int(depth),
        poi_getter=getattr(poi_registry, "get", None),
    )
    if hits:
        return hits
    return _scan_registry_poi_specs(poi_registry, point=(int(x), int(y)), depth=int(depth))


def query_game_poi_specs_at_zone(
    game: Any,
    zx: int,
    zy: int,
    *,
    depth: int,
    zone_w: int,
    zone_h: int,
) -> List[POISpec]:
    """Return POI specs whose footprints overlap a zone coordinate."""
    rect = ABSRect.from_zone_coord(int(zx), int(zy), int(zone_w), int(zone_h))
    return query_game_poi_specs_in_rect(game, rect, depth=int(depth))

def coerce_rect(raw: object) -> Optional[Tuple[float, float, float, float]]:
    """Extract a normalized (x0, y0, x1, y1) float tuple from an object, dict, or list."""
    if isinstance(raw, dict):
        if all(k in raw for k in ("x0", "y0", "x1", "y1")):
            try:
                x0, y0, x1, y1 = float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"])
                if x1 < x0: x0, x1 = x1, x0
                if y1 < y0: y0, y1 = y1, y0
                return (x0, y0, x1, y1)
            except Exception:
                return None
        return None
    if isinstance(raw, (tuple, list)) and len(raw) >= 4:
        try:
            x0, y0, x1, y1 = float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
            if x1 < x0: x0, x1 = x1, x0
            if y1 < y0: y0, y1 = y1, y0
            return (x0, y0, x1, y1)
        except Exception:
            return None
    return None

def rect_for_obj(
    obj: Any,
    *,
    abs_x: float,
    abs_y: float,
) -> Tuple[float, float, float, float]:
    """Derive a normalized ABS bounding box for an entity based on its properties."""
    rect = coerce_rect(getattr(obj, "footprint_abs", None))
    if rect is None:
        tags = getattr(obj, "tags", None)
        if isinstance(tags, dict):
            rect = coerce_rect(tags.get("footprint_abs"))

    if rect is None:
        size_val = None
        for raw in (
            getattr(obj, "abs_size", None),
            getattr(obj, "base_size", None),
        ):
            if isinstance(raw, (int, float)) and float(raw) > 1.0:
                size_val = float(raw)
                break
        if size_val is None:
            tags = getattr(obj, "tags", None)
            if isinstance(tags, dict):
                raw = tags.get("abs_size")
                if isinstance(raw, (int, float)) and float(raw) > 1.0:
                    size_val = float(raw)
        if size_val is not None:
            half = 0.5 * float(size_val)
            rect = (float(abs_x) - half, float(abs_y) - half, float(abs_x) + half, float(abs_y) + half)
        else:
            rect = (float(abs_x), float(abs_y), float(abs_x) + 1.0, float(abs_y) + 1.0)
    return rect
