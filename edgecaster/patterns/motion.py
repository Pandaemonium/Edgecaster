from __future__ import annotations

import math
from typing import Dict, List, Tuple, Any

from edgecaster.patterns.activation import project_vertices
from edgecaster.state.patterns import Pattern

Vec2 = Tuple[float, float]


def center_of_mass(pattern: Pattern) -> Vec2:
    if not pattern.vertices:
        return (0.0, 0.0)
    sx = sum(v.pos[0] for v in pattern.vertices)
    sy = sum(v.pos[1] for v in pattern.vertices)
    n = len(pattern.vertices)
    return (sx / n, sy / n)


def rotate_point(px: float, py: float, cx: float, cy: float, deg: float) -> Tuple[float, float]:
    rad = math.radians(deg)
    s = math.sin(rad)
    c = math.cos(rad)
    tx = px - cx
    ty = py - cy
    rx = tx * c - ty * s
    ry = tx * s + ty * c
    return (rx + cx, ry + cy)


def transform_pattern(pattern: Pattern, rotation_deg: float) -> None:
    """
    Rotate vertices in-place around the pattern COM by rotation_deg.
    """
    com = center_of_mass(pattern)
    cx, cy = com
    for v in pattern.vertices:
        vx, vy = v.pos
        v.pos = rotate_point(vx, vy, cx, cy, rotation_deg)


def start_motion(level, delta: Tuple[float, float], rotation_deg: float, interval: int = 10) -> None:
    """
    Store motion state on the level so _advance_time can step it.
    """
    level.pattern_motion = {
        "delta": delta,
        "rotation": rotation_deg,
        "interval": interval,
        "accum": 0,
    }


def step_motion(game: Any, level, delta_ticks: int) -> None:
    """
    Advance pattern motion based on elapsed ticks.
    """
    motion = getattr(level, "pattern_motion", None)
    if not motion:
        return
    motion["accum"] += delta_ticks
    interval = motion.get("interval", 10)
    while motion["accum"] >= interval:
        motion["accum"] -= interval
        _apply_motion_step(game, level, motion)

    # Clear if pattern is offscreen/world
    if _pattern_off_world(level):
        level.pattern_motion = None


def _apply_motion_step(game: Any, level, motion: Dict[str, Any]) -> None:
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None:
        return
    dx, dy = motion.get("delta", (0, 0))
    rot = motion.get("rotation", 0)
    # Move anchor by delta
    level.pattern_anchor = (anchor[0] + dx, anchor[1] + dy)
    # Rotate vertices around their COM (pattern space)
    if rot:
        transform_pattern(pattern, rot)


def _pattern_off_world(level) -> bool:
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    world = getattr(level, "world", None)
    if pattern is None or anchor is None or world is None or not pattern.vertices:
        return False
    verts = project_vertices(pattern, anchor)
    # If every vertex is outside bounds, consider off world
    all_out = True
    for vx, vy in verts:
        tx = int(round(vx))
        ty = int(round(vy))
        if world.in_bounds(tx, ty):
            all_out = False
            break
    return all_out


def build_push_preview(pattern: Pattern, anchor: Vec2, target: Vec2, rotation_deg: float, max_range: float = 5.0) -> Dict[str, Any]:
    """
    Prepare draw-ready preview data for a push/rotate.
    """
    if max_range is None:
        max_range = 5.0
    verts_world = project_vertices(pattern, anchor)
    com = center_of_mass(pattern)
    com_world = (com[0] + anchor[0], com[1] + anchor[1])

    dx = target[0] - com_world[0]
    dy = target[1] - com_world[1]
    dist = math.hypot(dx, dy)
    if dist > max_range and dist > 0:
        scale = max_range / dist
        dx *= scale
        dy *= scale
    target_com = (com_world[0] + dx, com_world[1] + dy)

    # Compute bounding box to size the square
    xs = [v[0] for v in verts_world]
    ys = [v[1] for v in verts_world]
    w = max(xs) - min(xs) if xs else 1.0
    h = max(ys) - min(ys) if ys else 1.0
    half_size = (max(w, h) / 2.0) + 1.0

    # Build rotated/translated preview verts
    tgt_verts: List[Tuple[float, float]] = []
    for vx, vy in verts_world:
        rx, ry = rotate_point(vx, vy, com_world[0], com_world[1], rotation_deg)
        tgt_verts.append((rx + dx, ry + dy))

    # Edges carry indices
    edges = [(e.a, e.b) for e in pattern.edges]

    return {
        "source_com": com_world,
        "target_com": target_com,
        "half_size": half_size,
        "target_verts": tgt_verts,
        "edges": edges,
        "rotation": rotation_deg,
        "delta": (dx, dy),
    }
