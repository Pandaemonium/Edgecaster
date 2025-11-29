"""Pattern activation helpers for gameplay."""
import math
from typing import List, Tuple, Optional

from edgecaster.state.patterns import Pattern

Vec2 = Tuple[float, float]
GridPos = Tuple[int, int]


def project_vertices(pattern: Pattern, origin: GridPos) -> List[Vec2]:
    """Translate pattern vertices to world space anchored at origin."""
    ox, oy = origin
    return [(v.pos[0] + ox, v.pos[1] + oy) for v in pattern.vertices]


def damage_from_vertices(vertices: List[Vec2], actor_pos: GridPos, radius: float, per_vertex: int, cap: Optional[int]) -> int:
    """Compute damage as per_vertex for each vertex within radius of actor center, capped if provided."""
    ax = actor_pos[0] + 0.5
    ay = actor_pos[1] + 0.5
    r2 = radius * radius
    hits = 0
    for vx, vy in vertices:
        dx = vx - ax
        dy = vy - ay
        if dx * dx + dy * dy <= r2:
            hits += 1
    dmg = hits * per_vertex
    if cap is not None:
        dmg = min(cap, dmg)
    return dmg
