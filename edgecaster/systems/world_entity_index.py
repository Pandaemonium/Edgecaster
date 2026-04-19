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

from dataclasses import dataclass
import math
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple


@dataclass(frozen=True)
class WorldEntRef:
    """Stores a world-level entity plus its zone bucket for fast queries."""
    ent: object
    zone_coord: Tuple[int, int, int]  # (zx, zy, z)
    local_pos: Tuple[int, int]        # (x,y) within that zone


class WorldEntityIndex:
    """
    Spatial index keyed by (zone_x, zone_y, depth).

    For now we index by zone buckets; later we can replace with a finer structure
    if needed. This already avoids scanning all world entities for camera-rect queries.
    """

    def __init__(self, *, zone_w: int, zone_h: int) -> None:
        self.zone_w = max(1, int(zone_w))
        self.zone_h = max(1, int(zone_h))
        self._by_zone: Dict[Tuple[int, int, int], List[WorldEntRef]] = {}
        self._ent_cells: Dict[str, Set[Tuple[int, int, int]]] = {}

    def clear(self) -> None:
        self._by_zone.clear()
        self._ent_cells.clear()

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
        ax, ay = self._abs_anchor(ent, zone_coord=zone_coord, local_pos=local_pos)
        zx, zy, z = zone_coord

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
            out.add((int(zx), int(zy), int(z)))
        return out

    def _remove_ent_key(self, ent_key: str) -> None:
        prev_cells = self._ent_cells.pop(str(ent_key), None)
        if not prev_cells:
            return
        for zc in prev_cells:
            bucket = self._by_zone.get(zc)
            if not bucket:
                continue
            kept: List[WorldEntRef] = []
            for r in bucket:
                if self._ent_key(r.ent) != ent_key:
                    kept.append(r)
            if kept:
                self._by_zone[zc] = kept
            else:
                self._by_zone.pop(zc, None)

    def add(self, ent: object, *, zone_coord: Tuple[int, int, int], local_pos: Tuple[int, int]) -> None:
        ref = WorldEntRef(ent=ent, zone_coord=zone_coord, local_pos=local_pos)
        key = self._ent_key(ent)
        self._remove_ent_key(key)
        cells = self._coverage_zones_for(ent, zone_coord=zone_coord, local_pos=local_pos)
        for zc in cells:
            self._by_zone.setdefault(zc, []).append(ref)
        self._ent_cells[key] = set(cells)

        # Helpful metadata for debugging; harmless if unused.
        try:
            setattr(ent, "zone_coord", zone_coord)
        except Exception:
            pass
        try:
            ax, ay = self._abs_anchor(ent, zone_coord=zone_coord, local_pos=local_pos)
            setattr(ent, "abs_pos", (int(ax), int(ay)))
        except Exception:
            pass

    def extend(self, refs: Iterable[WorldEntRef]) -> None:
        for r in refs:
            self.add(r.ent, zone_coord=r.zone_coord, local_pos=r.local_pos)

    def iter_zone(self, zone_coord: Tuple[int, int, int]) -> Iterator[WorldEntRef]:
        yield from self._by_zone.get(zone_coord, ())

    def query_abs_rect(
        self,
        abs_rect: Tuple[float, float, float, float],
        *,
        z: int = 0,
        zone_span_cap: Optional[int] = None,
    ) -> List[WorldEntRef]:
        """
        Return world-entity refs whose zone buckets intersect abs_rect.
        abs_rect = (x0,y0,x1,y1) in absolute tile coords.
        """
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
        if ax1 < ax0:
            ax0, ax1 = ax1, ax0
        if ay1 < ay0:
            ay0, ay1 = ay1, ay0

        zx0 = int((ax0) // self.zone_w)
        zy0 = int((ay0) // self.zone_h)
        zx1 = int(((ax1 - 1e-9)) // self.zone_w)
        zy1 = int(((ay1 - 1e-9)) // self.zone_h)

        if zone_span_cap is not None:
            cap = max(1, int(zone_span_cap))
            span_x = zx1 - zx0 + 1
            span_y = zy1 - zy0 + 1
            if span_x > cap:
                czx = int(((ax0 + ax1) * 0.5) // self.zone_w)
                half = cap // 2
                zx0 = czx - half
                zx1 = zx0 + cap - 1
            if span_y > cap:
                czy = int(((ay0 + ay1) * 0.5) // self.zone_h)
                half = cap // 2
                zy0 = czy - half
                zy1 = zy0 + cap - 1

        out: List[WorldEntRef] = []
        seen: Set[str] = set()
        for zx in range(zx0, zx1 + 1):
            for zy in range(zy0, zy1 + 1):
                for ref in self._by_zone.get((zx, zy, int(z)), ()):
                    k = self._ent_key(ref.ent)
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append(ref)
        return out
