"""
register_proxy_entity: free function for adding world-level proxy entities into SpatialIndex.

World-level entities are macro-scale objects renderable without instantiating a gameplay zone.
They are registered directly into the shared SpatialIndex (kind="proxy").
"""

from __future__ import annotations

from typing import Optional, Tuple


def _normalize_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (float(x0), float(y0), float(x1), float(y1))


def _coerce_rect(raw: object) -> Optional[Tuple[float, float, float, float]]:
    if isinstance(raw, dict):
        if all(k in raw for k in ("x0", "y0", "x1", "y1")):
            try:
                return _normalize_rect(
                    (float(raw["x0"]), float(raw["y0"]), float(raw["x1"]), float(raw["y1"]))
                )
            except Exception:
                return None
        return None
    if isinstance(raw, (tuple, list)) and len(raw) >= 4:
        try:
            return _normalize_rect((float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])))
        except Exception:
            return None
    return None


def _abs_anchor(
    ent: object,
    *,
    zone_coord: Tuple[int, int, int],
    local_pos: Tuple[int, int],
    zone_w: int,
    zone_h: int,
) -> Tuple[int, int]:
    ap = getattr(ent, "abs_pos", None)
    if isinstance(ap, (tuple, list)) and len(ap) >= 2:
        try:
            return (int(ap[0]), int(ap[1]))
        except Exception:
            pass
    zx, zy, _ = zone_coord
    return (
        int(zx) * zone_w + int(local_pos[0]),
        int(zy) * zone_h + int(local_pos[1]),
    )


def _abs_rect_for(
    ent: object,
    *,
    zone_coord: Tuple[int, int, int],
    local_pos: Tuple[int, int],
    zone_w: int,
    zone_h: int,
) -> Tuple[float, float, float, float]:
    ax, ay = _abs_anchor(ent, zone_coord=zone_coord, local_pos=local_pos, zone_w=zone_w, zone_h=zone_h)
    rect = _coerce_rect(getattr(ent, "footprint_abs", None))
    if rect is None:
        tags = getattr(ent, "tags", None)
        if isinstance(tags, dict):
            rect = _coerce_rect(tags.get("footprint_abs"))

    if rect is None:
        size_val = None
        for candidate in (getattr(ent, "abs_size", None), getattr(ent, "base_size", None)):
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

    return _normalize_rect(rect)


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
        ax, ay = _abs_anchor(ent, zone_coord=zone_coord, local_pos=local_pos, zone_w=zone_w, zone_h=zone_h)
        setattr(ent, "abs_pos", (int(ax), int(ay)))
    except Exception:
        pass

    try:
        spatial_index.add_or_update(
            ent,
            _abs_rect_for(ent, zone_coord=zone_coord, local_pos=local_pos, zone_w=zone_w, zone_h=zone_h),
            int(zone_coord[2]),
            "proxy",
            source="world_entity_index",
        )
    except Exception:
        pass
