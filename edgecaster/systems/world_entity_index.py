"""
WorldEntityIndex

A lightweight spatial index for *world-level* entities that should be renderable
at macro scales without instantiating gameplay zones (LevelState).

Invariant:
- Querying this index must have no gameplay side effects.
- These entities are not owned by a LevelState; they exist "on the overmap".

[LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
WorldEntityIndex remains a macro-scale parallel cache. Once attention/render
queries read from a shared realization/index path over the entity graph, this
module should go away.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple


class WorldEntityIndex:
    """
    Write-through adapter that mirrors world-level entities into the shared SpatialIndex.

    All spatial reads now go through SpatialIndex directly. This class exists only to
    keep add() call sites intact while they are migrated to register directly into
    the shared SpatialIndex.

    [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
    WorldEntityIndex is a write-through shim. Once all add() call sites register
    directly into SpatialIndex (or the entity-graph spawn pipeline does it), this
    class can be deleted.
    """

    def __init__(self, *, zone_w: int, zone_h: int, spatial_index: object | None = None) -> None:
        self.zone_w = max(1, int(zone_w))
        self.zone_h = max(1, int(zone_h))
        self.spatial_index = spatial_index
        self._spatial_ids: Dict[str, str] = {}

    def clear(self) -> None:
        if self.spatial_index is not None:
            try:
                for spatial_id in list(self._spatial_ids.values()):
                    self.spatial_index.remove(spatial_id, source="world_entity_index")
            except Exception:
                pass
        self._spatial_ids.clear()

    @staticmethod
    def _ent_key(ent: object) -> str:
        try:
            eid = str(getattr(ent, "id", "") or "").strip()
        except Exception:
            eid = ""
        if eid:
            return f"id:{eid}"
        return f"obj:{id(ent)}"

    @staticmethod
    def _normalize_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x0, y0, x1, y1 = rect
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        return (float(x0), float(y0), float(x1), float(y1))

    @staticmethod
    def _coerce_rect(raw: object) -> Optional[Tuple[float, float, float, float]]:
        if isinstance(raw, dict):
            if all(k in raw for k in ("x0", "y0", "x1", "y1")):
                try:
                    return WorldEntityIndex._normalize_rect(
                        (
                            float(raw["x0"]),
                            float(raw["y0"]),
                            float(raw["x1"]),
                            float(raw["y1"]),
                        )
                    )
                except Exception:
                    return None
            return None
        if isinstance(raw, (tuple, list)) and len(raw) >= 4:
            try:
                return WorldEntityIndex._normalize_rect(
                    (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
                )
            except Exception:
                return None
        return None

    def _abs_anchor(
        self,
        ent: object,
        *,
        zone_coord: Tuple[int, int, int],
        local_pos: Tuple[int, int],
    ) -> Tuple[int, int]:
        ap = getattr(ent, "abs_pos", None)
        if isinstance(ap, (tuple, list)) and len(ap) >= 2:
            try:
                return (int(ap[0]), int(ap[1]))
            except Exception:
                pass
        zx, zy, _ = zone_coord
        return (
            int(zx) * self.zone_w + int(local_pos[0]),
            int(zy) * self.zone_h + int(local_pos[1]),
        )

    def _coverage_zones_for(
        self,
        ent: object,
        *,
        zone_coord: Tuple[int, int, int],
        local_pos: Tuple[int, int],
    ) -> Set[Tuple[int, int, int]]:
        _, _, z = zone_coord
        rect = self._abs_rect_for(ent, zone_coord=zone_coord, local_pos=local_pos)
        x0, y0, x1, y1 = self._normalize_rect(rect)
        zx0 = int(math.floor(float(x0) / float(self.zone_w)))
        zy0 = int(math.floor(float(y0) / float(self.zone_h)))
        zx1 = int(math.floor((float(x1) - 1e-9) / float(self.zone_w)))
        zy1 = int(math.floor((float(y1) - 1e-9) / float(self.zone_h)))
        out: Set[Tuple[int, int, int]] = set()
        for cx in range(int(zx0), int(zx1) + 1):
            for cy in range(int(zy0), int(zy1) + 1):
                out.add((int(cx), int(cy), int(z)))
        if not out:
            zx, zy, _ = zone_coord
            out.add((int(zx), int(zy), int(z)))
        return out

    def _abs_rect_for(
        self,
        ent: object,
        *,
        zone_coord: Tuple[int, int, int],
        local_pos: Tuple[int, int],
    ) -> Tuple[float, float, float, float]:
        ax, ay = self._abs_anchor(ent, zone_coord=zone_coord, local_pos=local_pos)
        rect = self._coerce_rect(getattr(ent, "footprint_abs", None))
        if rect is None:
            tags = getattr(ent, "tags", None)
            if isinstance(tags, dict):
                rect = self._coerce_rect(tags.get("footprint_abs"))

        if rect is None:
            size_val = None
            for candidate in (
                getattr(ent, "abs_size", None),
                getattr(ent, "base_size", None),
            ):
                if isinstance(candidate, (int, float)) and float(candidate) > 1.0:
                    size_val = float(candidate)
                    break
            if size_val is None:
                tags = getattr(ent, "tags", None)
                if isinstance(tags, dict):
                    c = tags.get("abs_size", None)
                    if isinstance(c, (int, float)) and float(c) > 1.0:
                        size_val = float(c)
            if size_val is not None:
                half = 0.5 * float(size_val)
                rect = (float(ax) - half, float(ay) - half, float(ax) + half, float(ay) + half)
            else:
                rect = (float(ax), float(ay), float(ax) + 1.0, float(ay) + 1.0)

        return self._normalize_rect(rect)

    def _remove_ent_key(self, ent_key: str) -> None:
        prev_cells = self._ent_cells.pop(str(ent_key), None)
        spatial_id = self._spatial_ids.pop(str(ent_key), None)
        if spatial_id and self.spatial_index is not None:
            try:
                self.spatial_index.remove(spatial_id, source="world_entity_index")
            except Exception:
                pass

    def add(self, ent: object, *, zone_coord: Tuple[int, int, int], local_pos: Tuple[int, int]) -> None:
        key = self._ent_key(ent)
        self._remove_ent_key(key)

        # Helpful metadata for debugging; harmless if unused.
        try:
            setattr(ent, "zone_coord", zone_coord)
        except Exception:
            pass
        try:
            setattr(ent, "local_pos", (int(local_pos[0]), int(local_pos[1])))
        except Exception:
            pass
        try:
            ax, ay = self._abs_anchor(ent, zone_coord=zone_coord, local_pos=local_pos)
            setattr(ent, "abs_pos", (int(ax), int(ay)))
        except Exception:
            pass
        if self.spatial_index is not None:
            try:
                spatial_id = self.spatial_index.add_or_update(
                    ent,
                    self._abs_rect_for(ent, zone_coord=zone_coord, local_pos=local_pos),
                    int(zone_coord[2]),
                    "proxy",
                    source="world_entity_index",
                )
                self._spatial_ids[key] = str(spatial_id)
            except Exception:
                pass
