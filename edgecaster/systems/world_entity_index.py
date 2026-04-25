"""
register_proxy_entity: free function for adding world-level proxy entities into SpatialIndex.

World-level entities are macro-scale objects renderable without instantiating a gameplay zone.
They are registered directly into the shared SpatialIndex (kind="proxy").
"""

from __future__ import annotations

from typing import Optional, Tuple
from edgecaster.systems.spatial_index import rect_for_obj


def register_proxy_entity(
    game: object,
    ent: object,
    *,
    zone_coord: Tuple[int, int, int],
    local_pos: Tuple[int, int],
) -> None:
    """Register a world-level proxy entity into game.spatial_index."""
    spatial_index = getattr(game, "spatial_index", None)
    if spatial_index is None:
        return

    cfg = getattr(game, "cfg", None)
    zone_w = max(1, int(getattr(cfg, "world_width", 60) or 60))
    zone_h = max(1, int(getattr(cfg, "world_height", 40) or 40))

    try:
        setattr(ent, "zone_coord", zone_coord)
    except Exception:
        pass
    try:
        setattr(ent, "local_pos", (int(local_pos[0]), int(local_pos[1])))
    except Exception:
        pass
    try:
        ap = getattr(ent, "abs_pos", None)
        if isinstance(ap, (tuple, list)) and len(ap) >= 2:
            ax, ay = int(ap[0]), int(ap[1])
        else:
            zx, zy, _ = zone_coord
            ax, ay = int(zx) * zone_w + int(local_pos[0]), int(zy) * zone_h + int(local_pos[1])
        setattr(ent, "abs_pos", (int(ax), int(ay)))
    except Exception:
        ax, ay = 0, 0

    try:
        spatial_index.add_or_update(
            ent,
            rect_for_obj(ent, abs_x=float(ax), abs_y=float(ay)),
            int(zone_coord[2]),
            "proxy",
            source="world_entity_index",
        )
    except Exception:
        pass
