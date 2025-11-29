"""Fractal-style pattern builders similar to fractal_lab."""
import math
from dataclasses import dataclass
from typing import List

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

    def __init__(self, height_factor: float = 0.25) -> None:
        super().__init__(name="Koch-like")
        self.height_factor = height_factor

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

    def __init__(self, angle_deg: float = 30.0, length_factor: float = 0.6) -> None:
        super().__init__(name="Branch")
        self.angle_deg = angle_deg
        self.length_factor = length_factor

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
            theta = math.radians(self.angle_deg)
            b_len = self.length_factor * length
            left_angle = base_angle + theta
            right_angle = base_angle - theta
            lx = mx + b_len * math.cos(left_angle)
            ly = my + b_len * math.sin(left_angle)
            rx = mx + b_len * math.cos(right_angle)
            ry = my + b_len * math.sin(right_angle)
            c = seg.color
            out.extend(
                [
                    Segment((ax, ay), (mx, my), c, seg.weight),
                    Segment((mx, my), (bx, by), c, seg.weight),
                    Segment((mx, my), (lx, ly), c, seg.weight),
                    Segment((mx, my), (rx, ry), c, seg.weight),
                ]
            )
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
