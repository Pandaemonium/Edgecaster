from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

from edgecaster.patterns.activation import project_vertices


@dataclass
class AimPrediction:
    """Immutable preview of an aimed ability: damage map + fail text."""

    action: str
    hover_vertex: int
    center: Tuple[float, float]
    radius: Optional[float]
    dmg_map: Dict[Tuple[int, int], int]
    fail_text: Optional[str]
    target_vertices: List[int]
    per_actor_damage: Dict[str, int]
    fail_pct: Optional[float]


def _unique_ordered(seq) -> List[Any]:
    seen = set()
    out = []
    for val in seq:
        if val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def predict_aim_preview(
    game: Any,
    action: str,
    hover_idx: Optional[int],
    *,
    neighbors: Optional[List[int]] = None,
) -> Optional[AimPrediction]:
    """
    Compute the preview for an aimed ability (activate_all / activate_seed).

    Returns an AimPrediction containing damage numbers + fail text only;
    caller/renderer can use pattern geometry to draw.
    """
    if action not in ("activate_all", "activate_seed"):
        return None

    origin = getattr(game, "pattern_anchor", None)
    pattern = getattr(game, "pattern", None)
    if origin is None or pattern is None or not getattr(pattern, "vertices", None):
        return None

    verts = project_vertices(pattern, origin)
    if hover_idx is None or hover_idx < 0 or hover_idx >= len(verts):
        return None

    dmg_map: Dict[Tuple[int, int], int] = {}
    fail_text: Optional[str] = None
    fail_pct: Optional[float] = None
    per_actor_damage: Dict[str, int] = {}

    if action == "activate_all":
        try:
            radius = game.get_param_value("activate_all", "radius")
            dmg_per_vertex = game.get_param_value("activate_all", "damage")
        except Exception:
            radius = getattr(getattr(game, "cfg", None), "pattern_damage_radius", 1.25)
            dmg_per_vertex = 1

        center = verts[hover_idx]
        r2 = radius * radius

        active_indices = [i for i, v in enumerate(verts) if (v[0] - center[0]) ** 2 + (v[1] - center[1]) ** 2 <= r2]

        # Collect fail chances from both coherence and strength caps.
        fail_candidates: List[float] = []
        try:
            coh_limit = game._coherence_limit()
            over = max(0, len(verts) - coh_limit)
            if over > 0:
                fail_candidates.append(over / (coh_limit + over) * 100)
        except Exception:
            pass

        try:
            str_limit = game._strength_limit()
            over = max(0, len(active_indices) - str_limit)
            if over > 0:
                fail_candidates.append(over / (str_limit + over) * 100)
        except Exception:
            pass

        if fail_candidates:
            fail_pct = max(fail_candidates)
            fail_text = f"fail {int(round(fail_pct))}%"
        else:
            fail_text = None

        # Summarize damage per-actor using the same coverage math as activation.
        try:
            level = game._level()
            for actor in getattr(level, "actors", {}).values():
                if not getattr(actor, "alive", False):
                    continue
                ax, ay = getattr(actor, "pos", (None, None))
                if ax is None or ay is None:
                    continue
                if actor.id == getattr(game, "player_id", None) or getattr(actor, "faction", None) == "player":
                    continue
                tile = level.world.get_tile(ax, ay) if hasattr(level, "world") else None
                if tile is not None and hasattr(tile, "visible") and not tile.visible:
                    continue
                # tile square center distance to circle, approximate coverage factor
                dx = (ax + 0.5) - center[0]
                dy = (ay + 0.5) - center[1]
                dist = math.hypot(dx, dy)
                half_diag = 0.7071
                if dist <= radius - half_diag:
                    coverage = 1.0
                elif dist >= radius + half_diag:
                    coverage = 0.0
                else:
                    span = (radius + half_diag) - (radius - half_diag)
                    coverage = max(0.0, min(1.0, 1 - (dist - (radius - half_diag)) / span))
                if coverage <= 0:
                    continue
                dmg = int(dmg_per_vertex * len(active_indices) * coverage)
                if dmg <= 0:
                    continue
                per_actor_damage[actor.id] = dmg
        except Exception:
            # If level/world not ready, we can still render circle/vertices.
            pass

        return AimPrediction(
            action=action,
            hover_vertex=hover_idx,
            center=center,
            radius=radius,
            dmg_map=dmg_map,
            fail_text=fail_text,
            target_vertices=active_indices,
            per_actor_damage=per_actor_damage,
            fail_pct=fail_pct,
        )

    # activate_seed
    try:
        dmg_per_vertex = game.get_param_value("activate_seed", "damage")
    except Exception:
        dmg_per_vertex = 1

    targets = _unique_ordered([hover_idx] + [n for n in (neighbors or []) if n is not None])

    fail_candidates: List[float] = []
    try:
        coh_limit = game._coherence_limit()
        over = max(0, len(verts) - coh_limit)
        if over > 0:
            fail_candidates.append(over / (coh_limit + over) * 100)
    except Exception:
        pass
    try:
        str_limit = game._strength_limit()
        over = max(0, len(targets) - str_limit)
        if over > 0:
            fail_candidates.append(over / (str_limit + over) * 100)
    except Exception:
        pass
    if fail_candidates:
        fail_pct = max(fail_candidates)
        fail_text = f"fail {int(round(fail_pct))}%"
    else:
        fail_text = None

    for idx in targets:
        if idx < 0 or idx >= len(verts):
            continue
        vx, vy = verts[idx]
        tx = int(round(vx))
        ty = int(round(vy))
        dmg_map[(tx, ty)] = dmg_map.get((tx, ty), 0) + dmg_per_vertex

    try:
        level = game._level()
        for actor in getattr(level, "actors", {}).values():
            if not getattr(actor, "alive", False):
                continue
            ax, ay = getattr(actor, "pos", (None, None))
            if ax is None or ay is None:
                continue
            dmg = dmg_map.get((ax, ay))
        if dmg:
            per_actor_damage[actor.id] = dmg
    except Exception:
        pass

    center = verts[hover_idx]
    return AimPrediction(
        action=action,
        hover_vertex=hover_idx,
        center=center,
        radius=None,
        dmg_map=dmg_map,
        fail_text=fail_text,
        target_vertices=targets,
        per_actor_damage=per_actor_damage,
        fail_pct=fail_pct,
    )
