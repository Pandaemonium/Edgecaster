from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Vec2 = Tuple[float, float]


@dataclass
class Vertex:
    pos: Vec2
    color: str = "neutral"
    power: float = 1.0
    # Optional semantic tags (e.g. {"chakra_node": "leg.foot.toe_1"}).
    # Most generators ignore this; chakra-aware abilities can consume it.
    tags: Dict[str, str] = field(default_factory=dict)


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
    # Optional provenance that can be propagated through generators.
    # For chakra-generated geometry this tracks which chakra endpoints a
    # segment came from.
    src_kind: str = ""
    src_node_a: str = ""
    src_node_b: str = ""


@dataclass
class Pattern:
    vertices: List[Vertex] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)

    def add_vertex(
        self,
        pos: Vec2,
        color: str = "neutral",
        power: float = 1.0,
        tags: Dict[str, str] | None = None,
    ) -> int:
        self.vertices.append(
            Vertex(pos=pos, color=color, power=power, tags=dict(tags or {}))
        )
        return len(self.vertices) - 1

    def add_edge(self, a: int, b: int, color: str = "neutral", weight: float = 1.0) -> None:
        self.edges.append(Edge(a=a, b=b, color=color, weight=weight))

    def to_segments(self) -> List[Segment]:
        """Expand into a list of segments for geometry processing."""
        segs: List[Segment] = []
        for e in self.edges:
            try:
                va = self.vertices[e.a]
                vb = self.vertices[e.b]
                a = va.pos
                b = vb.pos
            except IndexError:
                continue

            src_node_a = str(va.tags.get("chakra_node", ""))
            src_node_b = str(vb.tags.get("chakra_node", ""))
            src_kind = ""
            if src_node_a or src_node_b:
                src_kind = "chakra"

            segs.append(
                Segment(
                    a=a,
                    b=b,
                    color=e.color,
                    weight=e.weight,
                    src_kind=src_kind,
                    src_node_a=src_node_a,
                    src_node_b=src_node_b,
                )
            )
        return segs

    @classmethod
    def from_segments(cls, segments: List[Segment]) -> "Pattern":
        """Create a pattern from raw segments; vertices are deduped by position."""
        pattern = cls()
        index_by_pos: Dict[Tuple[float, float], int] = {}

        def _merge_vertex_tags(v: Vertex, node_id: str) -> None:
            """Merge chakra-node provenance into an existing vertex."""
            if not node_id:
                return
            existing = str(v.tags.get("chakra_node", ""))
            if not existing:
                v.tags["chakra_node"] = node_id
                return
            if existing == node_id:
                return

            # Preserve a compact union for overlaps (rare but possible when
            # distinct chakra endpoints quantize to the same vertex).
            values = set(filter(None, [existing, node_id]))
            extra = str(v.tags.get("chakra_nodes", ""))
            if extra:
                values.update([s for s in extra.split("|") if s])
            v.tags["chakra_node"] = sorted(values)[0]
            v.tags["chakra_nodes"] = "|".join(sorted(values))

        def key(pos: Vec2, ndigits: int = 6) -> Tuple[float, float]:
            return (round(pos[0], ndigits), round(pos[1], ndigits))

        for seg in segments:
            ka = key(seg.a)
            kb = key(seg.b)
            if ka in index_by_pos:
                a_idx = index_by_pos[ka]
                _merge_vertex_tags(pattern.vertices[a_idx], str(getattr(seg, "src_node_a", "")))
            else:
                tags_a: Dict[str, str] = {}
                if getattr(seg, "src_node_a", ""):
                    tags_a["chakra_node"] = str(seg.src_node_a)
                a_idx = pattern.add_vertex(seg.a, color=seg.color, tags=tags_a)
                index_by_pos[ka] = a_idx

            if kb in index_by_pos:
                b_idx = index_by_pos[kb]
                _merge_vertex_tags(pattern.vertices[b_idx], str(getattr(seg, "src_node_b", "")))
            else:
                tags_b: Dict[str, str] = {}
                if getattr(seg, "src_node_b", ""):
                    tags_b["chakra_node"] = str(seg.src_node_b)
                b_idx = pattern.add_vertex(seg.b, color=seg.color, tags=tags_b)
                index_by_pos[kb] = b_idx

            pattern.add_edge(a_idx, b_idx, color=seg.color, weight=seg.weight)

        return pattern
