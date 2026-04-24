"""Canonical rune pattern state management.

This module holds the ABS-space, per-depth canonical pattern state.
Zones (LevelStates) are just local caches; the pattern bridges its view here.
"""
from __future__ import annotations

from typing import Any, Tuple, Optional
from edgecaster.patterns import builder


def pattern_state(game: Any, depth: int | None = None) -> dict:
    try:
        zone_coord = getattr(game, "zone_coord", (0, 0, 0))
        default_depth = int(zone_coord[2])
    except Exception:
        default_depth = 0
    d = default_depth if depth is None else int(depth)

    if not hasattr(game, "_pattern_state_by_depth"):
        game._pattern_state_by_depth = {}

    state = game._pattern_state_by_depth.get(d)
    if state is None:
        state = {
            "pattern": builder.Pattern(),
            "anchor_abs": None,            # (ax, ay) in ABS tiles
            "activation_points": [],
            "activation_ttl": 0,
            "pattern_motion": None,         # motion dict
            "acidic_pattern": False,
            "fern_active": False,
            "fern_growth_tips": [],
            "fern_accum": 0.0,
        }
        game._pattern_state_by_depth[d] = state

    # Back-compat: earlier versions used "motion"
    if "pattern_motion" not in state and "motion" in state:
        state["pattern_motion"] = state.get("motion")
    return state


def pattern_anchor_abs(game: Any) -> Optional[Tuple[int, int]]:
    return pattern_state(game).get("anchor_abs")


def set_pattern_anchor_abs(game: Any, anchor_abs: Optional[Tuple[int, int]]) -> None:
    st = pattern_state(game)
    st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1])) if anchor_abs is not None else None


def commit_pattern_state_from_level(game: Any, level: Any) -> None:
    """Write the *current zone view* (LevelState) back into canonical pattern state."""
    try:
        zone_coord = getattr(game, "zone_coord", (0, 0, 0))
        coord = getattr(level, "coord", zone_coord)
        zx, zy, d = coord
    except Exception:
        zx, zy, d = 0, 0, 0

    st = pattern_state(game, depth=d)

    st["pattern"] = getattr(level, "pattern", builder.Pattern())

    anchor_local = getattr(level, "pattern_anchor", None)
    if anchor_local is None:
        st["anchor_abs"] = None
    else:
        try:
            zw, zh = game._zone_dims()
        except Exception:
            zw, zh = 60, 40
        ox = zx * zw
        oy = zy * zh
        ax = int(round(anchor_local[0] + ox))
        ay = int(round(anchor_local[1] + oy))
        st["anchor_abs"] = (ax, ay)

    st["activation_points"] = list(getattr(level, "activation_points", []) or [])
    st["activation_ttl"] = int(getattr(level, "activation_ttl", 0) or 0)

    st["pattern_motion"] = getattr(level, "pattern_motion", None)
    st["acidic_pattern"] = bool(getattr(level, "acidic_pattern", False))
    st["fern_active"] = bool(getattr(level, "fern_active", False))
    st["fern_growth_tips"] = list(getattr(level, "fern_growth_tips", []) or [])
    st["fern_accum"] = float(getattr(level, "fern_accum", 0.0) or 0.0)


def sync_level_pattern_view(game: Any, level: Any) -> None:
    """Make the current LevelState view the canonical Game pattern state."""
    try:
        zone_coord = getattr(game, "zone_coord", (0, 0, 0))
        coord = getattr(level, "coord", zone_coord)
        zx, zy, d = coord
    except Exception:
        zx, zy, d = 0, 0, 0

    st = pattern_state(game, depth=d)

    level.pattern = st["pattern"]
    level.pattern_motion = st.get("pattern_motion", None)
    level.acidic_pattern = bool(st.get("acidic_pattern", False))
    level.fern_active = bool(st.get("fern_active", False))
    level.fern_growth_tips = list(st.get("fern_growth_tips", []) or [])
    level.fern_accum = float(st.get("fern_accum", 0.0) or 0.0)

    level.activation_points = list(st.get("activation_points", []) or [])
    level.activation_ttl = int(st.get("activation_ttl", 0) or 0)

    anchor_abs = st.get("anchor_abs")
    if anchor_abs is None:
        level.pattern_anchor = None
        return

    try:
        zw, zh = game._zone_dims()
    except Exception:
        zw, zh = 60, 40
    ox = zx * zw
    oy = zy * zh
    level.pattern_anchor = (int(anchor_abs[0] - ox), int(anchor_abs[1] - oy))
