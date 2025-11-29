from dataclasses import dataclass, field
from typing import List, Tuple, Dict

Vec2 = Tuple[float, float]


@dataclass
class Vertex:
    pos: Vec2
    color: str = "neutral"
    power: float = 1.0


@dataclass
class Edge:
    a: int
    b: int
    color: str = "neutral"
    weight: float = 1.0


@dataclass
class Segment:
    """Convenience type for geometry-focused generators."""

    a: Vec2
    b: Vec2
    color: str = "neutral"
    weight: float = 1.0


@dataclass
class Pattern:
    vertices: List[Vertex] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)

    def add_vertex(self, pos: Vec2, color: str = "neutral", power: float = 1.0) -> int:
        self.vertices.append(Vertex(pos=pos, color=color, power=power))
        return len(self.vertices) - 1

    def add_edge(self, a: int, b: int, color: str = "neutral", weight: float = 1.0) -> None:
        self.edges.append(Edge(a=a, b=b, color=color, weight=weight))

    def to_segments(self) -> List[Segment]:
        """Expand into a list of segments for geometry processing."""
        segs: List[Segment] = []
        for e in self.edges:
            try:
                a = self.vertices[e.a].pos
                b = self.vertices[e.b].pos
            except IndexError:
                continue
            segs.append(Segment(a=a, b=b, color=e.color, weight=e.weight))
        return segs

    @classmethod
    def from_segments(cls, segments: List[Segment]) -> "Pattern":
        """Create a pattern from raw segments; vertices are not deduped."""
        pattern = cls()
        for seg in segments:
            a_idx = pattern.add_vertex(seg.a, color=seg.color)
            b_idx = pattern.add_vertex(seg.b, color=seg.color)
            pattern.add_edge(a_idx, b_idx, color=seg.color, weight=seg.weight)
        return pattern
