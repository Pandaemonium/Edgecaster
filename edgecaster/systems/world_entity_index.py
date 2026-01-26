# edgecaster/systems/world_entity_index.py
"""
WorldEntityIndex

A lightweight spatial index for *world-level* entities that should be renderable
at macro scales without instantiating gameplay zones (LevelState).

Invariant:
- Querying this index must have no gameplay side effects.
- These entities are not owned by a LevelState; they exist "on the overmap".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple


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

    def clear(self) -> None:
        self._by_zone.clear()

    def add(self, ent: object, *, zone_coord: Tuple[int, int, int], local_pos: Tuple[int, int]) -> None:
        ref = WorldEntRef(ent=ent, zone_coord=zone_coord, local_pos=local_pos)
        self._by_zone.setdefault(zone_coord, []).append(ref)

        # Helpful metadata for debugging; harmless if unused.
        try:
            setattr(ent, "zone_coord", zone_coord)
        except Exception:
            pass
        try:
            setattr(ent, "pos", tuple(local_pos))
        except Exception:
            pass

    def extend(self, refs: Iterable[WorldEntRef]) -> None:
        for r in refs:
            self._by_zone.setdefault(r.zone_coord, []).append(r)

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
        for zx in range(zx0, zx1 + 1):
            for zy in range(zy0, zy1 + 1):
                out.extend(self._by_zone.get((zx, zy, int(z)), ()))
        return out
