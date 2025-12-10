from __future__ import annotations

import math
from typing import Dict, List, Tuple

from edgecaster.patterns.activation import project_vertices

Color = Tuple[int, int, int]

# ROYGBIV palette (wraps)
ROYGBIV: List[Color] = [
    (255, 0, 0),
    (255, 165, 0),
    (255, 255, 0),
    (0, 128, 0),
    (0, 0, 255),
    (75, 0, 130),
    (238, 130, 238),
]


def _normalize_edge_key(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def apply_rainbow_edges(game) -> None:
    """
    Assign ROYGBIV colors to edges, walking outward from the anchor/root.

    The edge directly connected to the anchor/root vertex is red, its neighbors
    orange, and so on; wraps after violet. Colors are stored on level.pattern.edge_colors.
    """
    try:
        level = game._level()
    except Exception:
        return
    pattern = getattr(level, "pattern", None)
    origin = getattr(game, "pattern_anchor", None)
    if pattern is None or origin is None or not pattern.vertices or not pattern.edges:
        return

    # Build adjacency of vertices via edges
    adj: Dict[int, List[int]] = {}
    for edge in pattern.edges:
        a, b = getattr(edge, "a", None), getattr(edge, "b", None)
        if a is None or b is None:
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    # Choose root vertex: nearest to anchor by distance
    world_vertices = project_vertices(pattern, origin)
    rx, ry = origin
    root_idx = min(range(len(world_vertices)), key=lambda i: (world_vertices[i][0] - rx) ** 2 + (world_vertices[i][1] - ry) ** 2)

    # BFS to assign depth to vertices
    from collections import deque

    depth = {root_idx: 0}
    q = deque([root_idx])
    while q:
        cur = q.popleft()
        for nb in adj.get(cur, []):
            if nb in depth:
                continue
            depth[nb] = depth[cur] + 1
            q.append(nb)

    # Color edges based on the deeper endpoint (approx path outward)
    edge_colors: Dict[Tuple[int, int], Color] = {}
    for edge in pattern.edges:
        a, b = getattr(edge, "a", None), getattr(edge, "b", None)
        if a is None or b is None:
            continue
        da = depth.get(a, 0)
        db = depth.get(b, 0)
        d = max(da, db)
        col = ROYGBIV[d % len(ROYGBIV)]
        edge_colors[_normalize_edge_key(a, b)] = col

    # Persist on the pattern for renderers to consume
    setattr(pattern, "edge_colors", edge_colors)
