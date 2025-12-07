"""Fractal-style pattern builders similar to fractal_lab."""
import math
from dataclasses import dataclass
from typing import List, Tuple

from edgecaster.state.patterns import Pattern, Segment, Vec2


def line_pattern(a: Vec2, b: Vec2) -> Pattern:
    """Create a simple single-segment pattern from A to B."""
    seg = Segment(a=a, b=b, color="neutral")
    return Pattern.from_segments([seg])


@dataclass
class GeneratorBase:
    name: str

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        raise NotImplementedError


@dataclass
class SubdivideGenerator(GeneratorBase):
    parts: int = 3

    def __init__(self, parts: int = 3) -> None:
        super().__init__(name="Subdivide")
        self.parts = max(2, parts)

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        out: List[Segment] = []
        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            parts = max(2, self.parts)
            points: List[Vec2] = []
            for i in range(parts + 1):
                t = i / parts
                x = ax + (bx - ax) * t
                y = ay + (by - ay) * t
                points.append((x, y))
            c = seg.color
            for i in range(parts):
                out.append(Segment(points[i], points[i + 1], c, seg.weight))
                if len(out) >= max_segments:
                    return out
        return out


@dataclass
class KochGenerator(GeneratorBase):
    height_factor: float = 0.25
    flip: bool = False

    def __init__(self, height_factor: float = 0.25, flip: bool = False) -> None:
        super().__init__(name="Koch-like")
        self.height_factor = height_factor
        self.flip = flip

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        out: List[Segment] = []
        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            dx = bx - ax
            dy = by - ay
            length = math.hypot(dx, dy)
            if length == 0:
                out.append(seg)
                continue
            p1 = (ax + dx / 3.0, ay + dy / 3.0)
            p3 = (ax + 2.0 * dx / 3.0, ay + 2.0 * dy / 3.0)
            nx, ny = -dy / length, dx / length
            if self.flip:
                nx, ny = -nx, -ny
            height = self.height_factor * length
            peak = ((p1[0] + p3[0]) / 2.0 + nx * height, (p1[1] + p3[1]) / 2.0 + ny * height)
            c = seg.color
            out.extend(
                [
                    Segment((ax, ay), p1, c, seg.weight),
                    Segment(p1, peak, c, seg.weight),
                    Segment(peak, p3, c, seg.weight),
                    Segment(p3, (bx, by), c, seg.weight),
                ]
            )
            if len(out) >= max_segments:
                return out[:max_segments]
        return out


@dataclass
class BranchGenerator(GeneratorBase):
    angle_deg: float = 30.0
    length_factor: float = 0.6
    branch_count: int = 2

    def __init__(self, angle_deg: float = 30.0, length_factor: float = 0.6, branch_count: int = 2) -> None:
        super().__init__(name="Branch")
        self.angle_deg = angle_deg
        self.length_factor = length_factor
        self.branch_count = max(2, branch_count)

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        out: List[Segment] = []
        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            dx = bx - ax
            dy = by - ay
            length = math.hypot(dx, dy)
            if length == 0:
                out.append(seg)
                continue
            mx = (ax + bx) / 2.0
            my = (ay + by) / 2.0
            ux, uy = dx / length, dy / length
            base_angle = math.atan2(uy, ux)
            spread = math.radians(self.angle_deg)
            b_len = self.length_factor * length
            branches = []
            if self.branch_count == 2:
                angles = [base_angle + spread, base_angle - spread]
            else:
                angles = []
                for i in range(self.branch_count):
                    t = 0 if self.branch_count == 1 else i / (self.branch_count - 1)
                    ang = base_angle - spread + spread * 2 * t
                    angles.append(ang)
            for ang in angles:
                branches.append(
                    (
                        mx + b_len * math.cos(ang),
                        my + b_len * math.sin(ang),
                    )
                )
            c = seg.color
            out.extend(
                [
                    Segment((ax, ay), (mx, my), c, seg.weight),
                    Segment((mx, my), (bx, by), c, seg.weight),
                ]
            )
            for (bxp, byp) in branches:
                out.append(Segment((mx, my), (bxp, byp), c, seg.weight))
            if len(out) >= max_segments:
                return out[:max_segments]
        return out


@dataclass
class ZigzagGenerator(GeneratorBase):
    parts: int = 6
    amplitude_factor: float = 0.2

    def __init__(self, parts: int = 6, amplitude_factor: float = 0.2) -> None:
        super().__init__(name="Zigzag")
        self.parts = max(2, parts)
        self.amplitude_factor = amplitude_factor

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        out: List[Segment] = []
        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            dx = bx - ax
            dy = by - ay
            length = math.hypot(dx, dy)
            if length == 0:
                out.append(seg)
                continue
            nx, ny = -dy / length, dx / length
            amp = self.amplitude_factor * length
            points: List[Vec2] = []
            for i in range(self.parts + 1):
                t = i / self.parts
                x = ax + dx * t
                y = ay + dy * t
                if 0 < i < self.parts:
                    sign = 1 if (i % 2 == 1) else -1
                    x += nx * amp * sign
                    y += ny * amp * sign
                points.append((x, y))
            c = seg.color
            for i in range(self.parts):
                out.append(Segment(points[i], points[i + 1], c, seg.weight))
                if len(out) >= max_segments:
                    return out[:max_segments]
        return out


@dataclass
class JitterGenerator(GeneratorBase):
    magnitude_factor: float = 0.1

    def __init__(self, magnitude_factor: float = 0.1) -> None:
        super().__init__(name="Jitter")
        self.magnitude_factor = magnitude_factor

    def _jitter_point(self, x: float, y: float, mag: float) -> Vec2:
        seed = math.sin(x * 12.9898 + y * 78.233) * 43758.5453
        frac = seed - math.floor(seed)
        angle = 2.0 * math.pi * frac
        r = mag * frac
        return x + math.cos(angle) * r, y + math.sin(angle) * r

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        out: List[Segment] = []
        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            dx = bx - ax
            dy = by - ay
            length = math.hypot(dx, dy)
            if length == 0:
                out.append(seg)
                continue
            mag = self.magnitude_factor * length
            na = self._jitter_point(ax, ay, mag)
            nb = self._jitter_point(bx, by, mag)
            out.append(Segment(na, nb, seg.color, seg.weight))
            if len(out) >= max_segments:
                return out
        return out


@dataclass
class ExtendGenerator(GeneratorBase):
    """Copy pattern and translate by vector from first.a to last.b."""

    def __init__(self) -> None:
        super().__init__(name="Extend")

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        if not segments:
            return segments
        out = list(segments)
        start = segments[0].a
        end = segments[-1].b
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        for seg in segments:
            na = (seg.a[0] + dx, seg.a[1] + dy)
            nb = (seg.b[0] + dx, seg.b[1] + dy)
            out.append(Segment(na, nb, seg.color, seg.weight))
            if len(out) >= max_segments:
                return out[:max_segments]
        return out


@dataclass
class CustomPolyGenerator(GeneratorBase):
    """Apply a user-drawn polyline normalized to the base segment, with optional amplitude scaling."""

    points: List[Vec2]
    amplitude: float = 1.0

    def __init__(self, points: List[Vec2], amplitude: float = 1.0) -> None:
        super().__init__(name="CustomPoly")
        self.points = points
        self.amplitude = amplitude

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        out: List[Segment] = []
        if not self.points or len(self.points) < 2:
            return segments
        base_dx = self.points[-1][0] - self.points[0][0]
        base_dy = self.points[-1][1] - self.points[0][1]
        base_len = math.hypot(base_dx, base_dy)
        if base_len <= 0:
            return segments
        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            dx = bx - ax
            dy = by - ay
            seg_len = math.hypot(dx, dy)
            if seg_len == 0:
                out.append(seg)
                continue
            # directional basis
            dir_x = dx / seg_len
            dir_y = dy / seg_len
            perp_x = -dir_y
            perp_y = dir_x

            def map_point(px: float, py: float) -> Vec2:
                # normalize relative to base first point
                rel_x = (px - self.points[0][0]) / base_len
                rel_y = ((py - self.points[0][1]) / base_len) * self.amplitude  # amplitude only on lateral (Y) component
                wx = ax + rel_x * dx + rel_y * perp_x * seg_len
                wy = ay + rel_y * perp_y * seg_len + rel_x * dy
                return (wx, wy)

            c = seg.color
            for i in range(len(self.points) - 1):
                pa = map_point(self.points[i][0], self.points[i][1])
                pb = map_point(self.points[i + 1][0], self.points[i + 1][1])
                out.append(Segment(pa, pb, c, seg.weight))
                if len(out) >= max_segments:
                    return out[:max_segments]
        return out


@dataclass
class CustomGraphGenerator(GeneratorBase):
    """
    Apply a user-drawn graph (vertices + explicit edges) normalized to the base segment.
    The first and last vertices define the baseline; amplitude scales lateral displacement.
    """

    vertices: List[Vec2]
    edges: List[Tuple[int, int]]
    amplitude: float = 1.0

    def __init__(self, vertices: List[Vec2], edges: List[Tuple[int, int]], amplitude: float = 1.0) -> None:
        super().__init__(name="CustomPoly")
        self.vertices = vertices
        self.edges = edges
        self.amplitude = amplitude

    def apply_segments(self, segments: List[Segment], max_segments: int = 20000) -> List[Segment]:
        # Fallback to polyline behavior if edges are missing.
        if not self.edges:
            return CustomPolyGenerator(self.vertices, amplitude=self.amplitude).apply_segments(segments, max_segments)

        out: List[Segment] = []
        if len(self.vertices) < 2:
            return segments

        base_dx = self.vertices[-1][0] - self.vertices[0][0]
        base_dy = self.vertices[-1][1] - self.vertices[0][1]
        base_len = math.hypot(base_dx, base_dy)
        if base_len <= 0:
            return segments

        for seg in segments:
            ax, ay = seg.a
            bx, by = seg.b
            dx = bx - ax
            dy = by - ay
            seg_len = math.hypot(dx, dy)
            if seg_len == 0:
                out.append(seg)
                continue

            dir_x = dx / seg_len
            dir_y = dy / seg_len
            perp_x = -dir_y
            perp_y = dir_x

            def map_point(px: float, py: float) -> Vec2:
                rel_x = (px - self.vertices[0][0]) / base_len
                rel_y = ((py - self.vertices[0][1]) / base_len) * self.amplitude
                wx = ax + rel_x * dx + rel_y * perp_x * seg_len
                wy = ay + rel_y * perp_y * seg_len + rel_x * dy
                return (wx, wy)

            mapped = [map_point(px, py) for (px, py) in self.vertices]
            c = seg.color
            for ea, eb in self.edges:
                if ea < 0 or eb < 0 or ea >= len(mapped) or eb >= len(mapped):
                    continue
                out.append(Segment(mapped[ea], mapped[eb], c, seg.weight))
                if len(out) >= max_segments:
                    return out[:max_segments]

        return out


def cleanup_duplicates(segments: List[Segment], ndigits: int = 9) -> List[Segment]:
    """Remove exact duplicate oriented segments."""
    if not segments:
        return segments

    def round_point(p: Vec2) -> Vec2:
        return (round(p[0], ndigits), round(p[1], ndigits))

    seen = set()
    out: List[Segment] = []
    for seg in segments:
        key = (round_point(seg.a), round_point(seg.b))
        if key in seen:
            continue
        seen.add(key)
        out.append(seg)
    return out


def apply_chain(initial: Pattern, steps: List[tuple], max_segments: int = 20000, dedup: bool = True) -> Pattern:
    """
    Apply a sequence of (generator, repeats) over a pattern and return the new pattern.
    """
    segs = initial.to_segments()
    for gen, count in steps:
        for _ in range(count):
            segs = gen.apply_segments(segs, max_segments=max_segments)
            if dedup:
                segs = cleanup_duplicates(segs)
            if len(segs) >= max_segments:
                segs = segs[:max_segments]
                break
    return Pattern.from_segments(segs)
