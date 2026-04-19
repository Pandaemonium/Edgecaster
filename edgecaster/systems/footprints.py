"""Footprint geometry helpers for entity occupancy and interaction checks.

The legacy point-at-tile model remains valid by default: entities without an
explicit footprint are treated as occupying exactly one tile at ``ent.pos``.

This module is intentionally lightweight and side-effect free so it can be used
in hot query paths (collision, pickup, movement blocking).
"""

from __future__ import annotations

import math
from typing import Any, Iterator, Mapping, Optional, Tuple

RectF = Tuple[float, float, float, float]
TilePos = Tuple[int, int]


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def normalize_rect(rect: RectF) -> RectF:
    x0, y0, x1, y1 = rect
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (float(x0), float(y0), float(x1), float(y1))


def tile_rect(pos: TilePos) -> RectF:
    x = int(pos[0])
    y = int(pos[1])
    return (float(x), float(y), float(x + 1), float(y + 1))


def rect_translate(rect: RectF, dx: float, dy: float) -> RectF:
    x0, y0, x1, y1 = normalize_rect(rect)
    return (x0 + float(dx), y0 + float(dy), x1 + float(dx), y1 + float(dy))


def rect_tile_bounds(rect: RectF) -> Tuple[int, int, int, int]:
    """Return overlapped tile bounds as half-open indices (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = normalize_rect(rect)
    tx0 = int(math.floor(x0))
    ty0 = int(math.floor(y0))
    tx1 = int(math.ceil(x1))
    ty1 = int(math.ceil(y1))
    if tx1 <= tx0:
        tx1 = tx0 + 1
    if ty1 <= ty0:
        ty1 = ty0 + 1
    return tx0, ty0, tx1, ty1


def iter_tiles_overlapped_by_rect(rect: RectF) -> Iterator[TilePos]:
    tx0, ty0, tx1, ty1 = rect_tile_bounds(rect)
    for ty in range(ty0, ty1):
        for tx in range(tx0, tx1):
            yield (tx, ty)


def rect_within_bounds(rect: RectF, *, width: int, height: int) -> bool:
    tx0, ty0, tx1, ty1 = rect_tile_bounds(rect)
    return tx0 >= 0 and ty0 >= 0 and tx1 <= int(width) and ty1 <= int(height)


def world_walkable_for_rect(world: object, rect: RectF) -> bool:
    for tx, ty in iter_tiles_overlapped_by_rect(rect):
        try:
            if not bool(world.in_bounds(tx, ty)):
                return False
            if not bool(world.is_walkable(tx, ty)):
                return False
        except Exception:
            return False
    return True


def rect_overlaps(a: RectF, b: RectF) -> bool:
    ax0, ay0, ax1, ay1 = normalize_rect(a)
    bx0, by0, bx1, by1 = normalize_rect(b)
    return not (ax1 <= bx0 or ax0 >= bx1 or ay1 <= by0 or ay0 >= by1)


def rect_contains_point(rect: RectF, p: TilePos) -> bool:
    x0, y0, x1, y1 = normalize_rect(rect)
    px = float(p[0])
    py = float(p[1])
    return x0 <= px < x1 and y0 <= py < y1


def _rect_from_value(raw: Any) -> Optional[RectF]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        required = ("x0", "y0", "x1", "y1")
        if all(k in raw for k in required):
            return normalize_rect((
                _to_float(raw.get("x0", 0.0)),
                _to_float(raw.get("y0", 0.0)),
                _to_float(raw.get("x1", 0.0)),
                _to_float(raw.get("y1", 0.0)),
            ))
        return None
    if isinstance(raw, (tuple, list)) and len(raw) == 4:
        return normalize_rect((
            _to_float(raw[0]),
            _to_float(raw[1]),
            _to_float(raw[2]),
            _to_float(raw[3]),
        ))
    return None


def _as_dict(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    return {}


def _current_local_pos(ent: object) -> TilePos:
    p = getattr(ent, "pos", (0, 0))
    return (int(p[0]), int(p[1]))


def _coerce_tile_pos(raw: Any) -> Optional[TilePos]:
    if raw is None:
        return None
    try:
        if isinstance(raw, (tuple, list)) and len(raw) >= 2:
            return (int(raw[0]), int(raw[1]))
        return (int(raw[0]), int(raw[1]))
    except Exception:
        return None


def _current_abs_pos(ent: object) -> Optional[TilePos]:
    return _coerce_tile_pos(getattr(ent, "abs_pos", None))


def _sync_follow_local(ent: object) -> None:
    if not bool(getattr(ent, "footprint_follows_pos", True)):
        return
    rect = _rect_from_value(getattr(ent, "footprint_local", None))
    if rect is None:
        return
    cur = _current_local_pos(ent)
    anchor = _coerce_tile_pos(getattr(ent, "_footprint_anchor_local", None))
    if anchor is None:
        try:
            setattr(ent, "_footprint_anchor_local", cur)
        except Exception:
            pass
        return
    if (int(anchor[0]), int(anchor[1])) == cur:
        return
    dx = int(cur[0]) - int(anchor[0])
    dy = int(cur[1]) - int(anchor[1])
    moved = rect_translate(rect, dx, dy)
    try:
        setattr(ent, "footprint_local", moved)
        setattr(ent, "_footprint_anchor_local", cur)
    except Exception:
        pass


def _sync_follow_abs(ent: object) -> None:
    if not bool(getattr(ent, "footprint_follows_pos", True)):
        return
    rect = _rect_from_value(getattr(ent, "footprint_abs", None))
    if rect is None:
        return
    cur = _current_abs_pos(ent)
    if cur is None:
        return
    anchor = _coerce_tile_pos(getattr(ent, "_footprint_anchor_abs", None))
    if anchor is None:
        try:
            setattr(ent, "_footprint_anchor_abs", cur)
        except Exception:
            pass
        return
    if (int(anchor[0]), int(anchor[1])) == cur:
        return
    dx = int(cur[0]) - int(anchor[0])
    dy = int(cur[1]) - int(anchor[1])
    moved = rect_translate(rect, dx, dy)
    try:
        setattr(ent, "footprint_abs", moved)
        setattr(ent, "_footprint_anchor_abs", cur)
    except Exception:
        pass


def _first_rect(*values: Any) -> Optional[RectF]:
    for v in values:
        r = _rect_from_value(v)
        if r is not None:
            return r
    return None


def footprint_local_rect_from_spec(
    spec: Optional[Mapping[str, Any]],
    anchor_local: TilePos,
    *,
    entity_tags: Optional[Mapping[str, Any]] = None,
) -> RectF:
    """Resolve a local footprint rect from prototype/spec-style data.

    This is used for pre-spawn placement validation where we only have content
    data + a candidate anchor tile, not a full runtime entity instance.
    """
    spec_map = dict(spec or {})
    tags = _as_dict(entity_tags)
    spec_tags = _as_dict(spec_map.get("tags", None))

    rect = _first_rect(
        spec_map.get("footprint_local"),
        tags.get("footprint_local"),
        spec_tags.get("footprint_local"),
        spec_map.get("footprint"),
        tags.get("footprint"),
        spec_tags.get("footprint"),
    )
    if rect is not None:
        return rect

    fw = None
    fh = None
    for fw_src, fh_src in (
        (tags.get("footprint_w"), tags.get("footprint_h")),
        (spec_tags.get("footprint_w"), spec_tags.get("footprint_h")),
        (spec_map.get("footprint_w"), spec_map.get("footprint_h")),
    ):
        if isinstance(fw_src, (int, float)) and isinstance(fh_src, (int, float)):
            fw = float(fw_src)
            fh = float(fh_src)
            break

    ax, ay = int(anchor_local[0]), int(anchor_local[1])
    if fw is not None and fh is not None:
        return normalize_rect((float(ax), float(ay), float(ax) + max(0.0, fw), float(ay) + max(0.0, fh)))
    return tile_rect((ax, ay))


def initialize_entity_footprints(
    ent: object,
    *,
    spec: Optional[Mapping[str, Any]] = None,
    local_pos: Optional[TilePos] = None,
    abs_pos: Optional[TilePos] = None,
) -> None:
    """Populate canonical runtime footprint fields on an entity-like object."""
    spec_map = dict(spec or {})
    tags = _as_dict(getattr(ent, "tags", None))
    spec_tags = _as_dict(spec_map.get("tags", None))

    local_anchor = _coerce_tile_pos(local_pos) or _current_local_pos(ent)
    abs_anchor = _coerce_tile_pos(abs_pos) or _current_abs_pos(ent)

    follows_raw = getattr(ent, "footprint_follows_pos", None)
    if follows_raw is None:
        follows_raw = spec_map.get("footprint_follows_pos", None)
    if follows_raw is None:
        follows_raw = tags.get("footprint_follows_pos", None)
    if follows_raw is None:
        follows_raw = spec_tags.get("footprint_follows_pos", None)
    follows = True if follows_raw is None else bool(follows_raw)

    local_rect = _first_rect(
        getattr(ent, "footprint_local", None),
        spec_map.get("footprint_local"),
        tags.get("footprint_local"),
        spec_tags.get("footprint_local"),
        getattr(ent, "footprint", None),
        spec_map.get("footprint"),
        tags.get("footprint"),
        spec_tags.get("footprint"),
    )
    if local_rect is None:
        fw = None
        fh = None
        for fw_src, fh_src in (
            (tags.get("footprint_w"), tags.get("footprint_h")),
            (spec_tags.get("footprint_w"), spec_tags.get("footprint_h")),
            (spec_map.get("footprint_w"), spec_map.get("footprint_h")),
        ):
            if isinstance(fw_src, (int, float)) and isinstance(fh_src, (int, float)):
                fw = float(fw_src)
                fh = float(fh_src)
                break
        if fw is not None and fh is not None:
            local_rect = normalize_rect((
                float(local_anchor[0]),
                float(local_anchor[1]),
                float(local_anchor[0]) + max(0.0, fw),
                float(local_anchor[1]) + max(0.0, fh),
            ))
        else:
            local_rect = tile_rect(local_anchor)

    abs_rect = _first_rect(
        getattr(ent, "footprint_abs", None),
        spec_map.get("footprint_abs"),
        tags.get("footprint_abs"),
        spec_tags.get("footprint_abs"),
    )
    if abs_rect is None and abs_anchor is not None:
        dx = int(abs_anchor[0]) - int(local_anchor[0])
        dy = int(abs_anchor[1]) - int(local_anchor[1])
        abs_rect = rect_translate(local_rect, dx, dy)

    try:
        setattr(ent, "footprint_follows_pos", bool(follows))
        setattr(ent, "footprint_local", local_rect)
        setattr(ent, "_footprint_anchor_local", tuple(local_anchor))
        if abs_rect is not None:
            setattr(ent, "footprint_abs", abs_rect)
            if abs_anchor is not None:
                setattr(ent, "_footprint_anchor_abs", tuple(abs_anchor))
    except Exception:
        pass


def sync_entity_footprints(ent: object) -> None:
    _sync_follow_local(ent)
    _sync_follow_abs(ent)


def entity_footprint_local(ent: object) -> RectF:
    """Return entity footprint in local tile coordinates for the current level."""
    sync_entity_footprints(ent)

    explicit = _rect_from_value(getattr(ent, "footprint_local", None))
    if explicit is not None:
        return explicit

    # Runtime field backfill for legacy callsites/entities.
    initialize_entity_footprints(ent)
    explicit = _rect_from_value(getattr(ent, "footprint_local", None))
    if explicit is not None:
        return explicit

    return tile_rect(getattr(ent, "pos", (0, 0)))


def entity_footprint_abs(ent: object) -> RectF:
    """Return entity footprint in absolute tile coordinates when available."""
    sync_entity_footprints(ent)
    explicit = _rect_from_value(getattr(ent, "footprint_abs", None))
    if explicit is not None:
        return explicit

    abs_pos = _current_abs_pos(ent)
    if abs_pos is not None:
        return tile_rect(abs_pos)

    return entity_footprint_local(ent)


def entity_overlaps_tile(ent: object, pos: TilePos) -> bool:
    return rect_overlaps(entity_footprint_local(ent), tile_rect(pos))


def entity_blocks_movement(ent: object) -> bool:
    tags = getattr(ent, "tags", None)
    if not isinstance(tags, dict):
        tags = {}

    # Rule-style override, useful for kind-level policies via content.
    rule = str(tags.get("blocking_rule", "") or "").strip().lower()
    if rule in {"never", "none", "passable", "non_blocking"}:
        return False
    if rule in {"always", "solid", "blocking"}:
        return True

    # Direct data override.
    if "movement_blocking" in tags:
        return bool(tags.get("movement_blocking"))
    if "blocks_movement" in tags:
        return bool(tags.get("blocks_movement"))

    return bool(getattr(ent, "blocks_movement", False))


def entity_blocks_movement_at(ent: object, pos: TilePos) -> bool:
    if not entity_overlaps_tile(ent, pos):
        return False
    return entity_blocks_movement(ent)


def entity_blocks_movement_in_rect(ent: object, rect: RectF) -> bool:
    if not rect_overlaps(entity_footprint_local(ent), rect):
        return False
    return entity_blocks_movement(ent)
