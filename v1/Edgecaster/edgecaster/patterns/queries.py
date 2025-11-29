"""Pattern query helpers."""
from typing import List, Tuple
from edgecaster.state.patterns import Pattern, Segment

Vec2 = Tuple[float, float]


def vertices_in_radius(pattern: Pattern, center: Vec2, radius: float) -> List[int]:
    """Return indices of vertices within radius of a world-space point."""
    r2 = radius * radius
    out: List[int] = []
    for idx, v in enumerate(pattern.vertices):
        dx = v.pos[0] - center[0]
        dy = v.pos[1] - center[1]
        if dx * dx + dy * dy <= r2:
            out.append(idx)
    return out


def segments_bounding_box(segments: List[Segment]) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for seg in segments:
        xs.extend([seg.a[0], seg.b[0]])
        ys.extend([seg.a[1], seg.b[1]])
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return min(xs), max(xs), min(ys), max(ys)
