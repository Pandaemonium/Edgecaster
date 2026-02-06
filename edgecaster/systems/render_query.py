"""Render query helpers for ABS-rect candidate assembly.

These helpers keep `game.py` thinner by centralizing utility logic used by
render candidate selection and spatial bin maintenance.
"""

from __future__ import annotations

from typing import Dict, List, Tuple
import math


def size_for_render(obj: object) -> float:
    """Estimate absolute size for LoD/render filtering.

    Preference order:
    1) explicit numeric attrs (`abs_size`, `base_size`, `size`)
    2) kind/tags heuristics
    """
    for attr in ("abs_size", "base_size", "size"):
        v = getattr(obj, attr, None)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v)

    kind = getattr(obj, "kind", "") or ""
    if kind == "item":
        return 0.25
    if kind in ("feature", "structure"):
        return 2.0

    tags = getattr(obj, "tags", {}) or {}
    if isinstance(tags, dict) and "item_type" in tags:
        return 0.25

    return 1.0


def rebuild_spatial_bins(level) -> None:
    """Rebuild per-level spatial bins for fast camera-rect queries."""
    bs = int(getattr(level, "spatial_bin_size", 16) or 16)
    bs = max(1, bs)
    bins: Dict[Tuple[int, int], List[str]] = {}

    seen: set[str] = set()

    for a in level.actors.values():
        try:
            if not a.alive:
                continue
        except Exception:
            pass
        if a.id in seen:
            continue
        seen.add(a.id)
        x, y = a.pos
        key = (int(x) // bs, int(y) // bs)
        bins.setdefault(key, []).append(a.id)

    for e in level.entities.values():
        if e.id in seen:
            continue
        seen.add(e.id)
        x, y = e.pos
        key = (int(x) // bs, int(y) // bs)
        bins.setdefault(key, []).append(e.id)

    level.spatial_bins = bins
    level.spatial_dirty = False


def clamp_zone_window(
    zx0: int,
    zx1: int,
    zy0: int,
    zy1: int,
    *,
    zone_span_cap: int | None,
    ccx: float,
    ccy: float,
    zone_w: int,
    zone_h: int,
) -> tuple[int, int, int, int, bool]:
    """Clamp zone-window around camera center to at most `zone_span_cap`."""
    if not zone_span_cap or zone_span_cap <= 0:
        return zx0, zx1, zy0, zy1, False

    span_x = (zx1 - zx0 + 1)
    span_y = (zy1 - zy0 + 1)
    if span_x <= zone_span_cap and span_y <= zone_span_cap:
        return zx0, zx1, zy0, zy1, False

    try:
        czx = int(float(ccx) // float(zone_w))
        czy = int(float(ccy) // float(zone_h))
    except Exception:
        czx, czy = 0, 0

    half = zone_span_cap // 2
    nzx0 = czx - half
    nzx1 = nzx0 + zone_span_cap - 1
    nzy0 = czy - half
    nzy1 = nzy0 + zone_span_cap - 1
    return int(nzx0), int(nzx1), int(nzy0), int(nzy1), True

