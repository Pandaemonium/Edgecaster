import math
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

import pygame

# ---------- basic math types ----------
Vec2 = Tuple[float, float]

# ---------- directional gradient colors ----------
DIR_START_COLOR = (160, 80, 255)   # purple-ish
DIR_END_COLOR   = (255, 210, 80)   # gold-ish


def lerp_color(c1: Tuple[int, int, int],
               c2: Tuple[int, int, int],
               t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def draw_directional_gradient_line(
    surface: pygame.Surface,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    width: int = 2,
    dashed: bool = False,
) -> None:
    """
    Draw a line from p1 -> p2 with a color gradient from DIR_START_COLOR to
    DIR_END_COLOR along its direction. If dashed=True, draw every other
    segment, giving a dotted/dashed look.
    """
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length < 1e-3:
        # Too short: just draw a small point-ish segment
        pygame.draw.line(surface, DIR_END_COLOR, p1, p2, width)
        return

    # Choose number of segments based on pixel length
    # ~5 pixels per subsegment
    segments = max(2, int(length / 5))

    for i in range(segments):
        t0 = i / segments
        t1 = (i + 1) / segments

        if dashed and (i % 2 == 1):
            continue  # skip every other subsegment

        sx0 = x1 + dx * t0
        sy0 = y1 + dy * t0
        sx1 = x1 + dx * t1
        sy1 = y1 + dy * t1

        # Use midpoint along this subsegment to pick color
        tm = (t0 + t1) * 0.5
        col = lerp_color(DIR_START_COLOR, DIR_END_COLOR, tm)
        pygame.draw.line(surface, col, (sx0, sy0), (sx1, sy1), width)


# ---------- segment & pattern ----------

@dataclass
class Segment:
    a: Vec2
    b: Vec2
    color: Tuple[int, int, int] = (230, 240, 255)  # kept for logic; not used for drawing now


class Pattern:
    def __init__(self) -> None:
        self.segments: List[Segment] = []

    def reset_line(self, a: Vec2, b: Vec2) -> None:
        """Reset pattern to a single segment A -> B."""
        self.segments = [Segment(a, b)]

    def all_vertices(self) -> List[Vec2]:
        """Return list of all segment endpoints (not deduplicated)."""
        pts: List[Vec2] = []
        for seg in self.segments:
            pts.append(seg.a)
            pts.append(seg.b)
        return pts

    def first_point(self) -> Optional[Vec2]:
        if not self.segments:
            return None
        return self.segments[0].a

    def last_point(self) -> Optional[Vec2]:
        if not self.segments:
            return None
        return self.segments[-1].b

    def cleanup_duplicates(self, ndigits: int = 9) -> None:
        """
        Remove exact duplicate segments with the same orientation.
        (a->b) and (b->a) are treated as distinct; only identical (a,b)
        pairs (after rounding) are deduped.
        """
        if not self.segments:
            return

        def round_point(p: Vec2) -> Vec2:
            return (round(p[0], ndigits), round(p[1], ndigits))

        seen = set()
        unique: List[Segment] = []
        for seg in self.segments:
            key = (round_point(seg.a), round_point(seg.b))
            if key in seen:
                continue
            seen.add(key)
            unique.append(seg)
        self.segments = unique


# ---------- generators ----------
class GeneratorBase:
    name: str

    def apply(self, pattern: Pattern, max_segments: int = 20000) -> None:
        """
        Default implementation: segment-wise transform.
        Pattern-level generators can override apply().
        """
        if not pattern.segments:
            return
        new_segments: List[Segment] = []
        for seg in pattern.segments:
            new_segments.extend(self.apply_to_segment(seg))
            if len(new_segments) > max_segments:
                print("Too many segments; truncating.")
                break
        pattern.segments = new_segments[:max_segments]

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        raise NotImplementedError


class SubdivideGenerator(GeneratorBase):
    def __init__(self, parts: int = 3) -> None:
        self.name = "Subdivide"
        self.parts = max(2, parts)

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        """Split AB into 'parts' equal segments."""
        ax, ay = seg.a
        bx, by = seg.b
        parts = max(2, self.parts)
        points: List[Vec2] = []
        for i in range(parts + 1):
            t = i / parts
            x = ax + (bx - ax) * t
            y = ay + (by - ay) * t
            points.append((x, y))
        new_segments: List[Segment] = []
        c = seg.color
        for i in range(parts):
            new_segments.append(Segment(points[i], points[i + 1], c))
        return new_segments


class KochGenerator(GeneratorBase):
    def __init__(self, height_factor: float = 0.25) -> None:
        self.name = "Koch-like"
        self.height_factor = height_factor

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        """Single-step Koch-like triangular bump on segment AB."""
        (ax, ay) = seg.a
        (bx, by) = seg.b

        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        # 1/3 and 2/3 points along AB
        p1 = (ax + dx / 3.0, ay + dy / 3.0)
        p3 = (ax + 2.0 * dx / 3.0, ay + 2.0 * dy / 3.0)

        # Perpendicular unit vector (rotate by 90 degrees)
        nx, ny = -dy / length, dx / length
        height = self.height_factor * length

        peak = ((p1[0] + p3[0]) / 2.0 + nx * height,
                (p1[1] + p3[1]) / 2.0 + ny * height)

        c = seg.color
        # A -- p1 -- peak -- p3 -- B
        return [
            Segment((ax, ay), p1, c),
            Segment(p1, peak, c),
            Segment(peak, p3, c),
            Segment(p3, (bx, by), c),
        ]


class BranchGenerator(GeneratorBase):
    def __init__(self, angle_deg: float = 30.0, length_factor: float = 0.6) -> None:
        self.name = "Branch"
        self.angle_deg = angle_deg
        self.length_factor = length_factor

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        """Branch from midpoint with two angled segments, plus keep the main segment."""
        (ax, ay) = seg.a
        (bx, by) = seg.b
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        mx = (ax + bx) / 2.0
        my = (ay + by) / 2.0

        ux, uy = dx / length, dy / length  # unit direction
        base_angle = math.atan2(uy, ux)
        theta = math.radians(self.angle_deg)

        b_len = self.length_factor * length

        # left branch
        left_angle = base_angle + theta
        lx = mx + b_len * math.cos(left_angle)
        ly = my + b_len * math.sin(left_angle)
        # right branch
        right_angle = base_angle - theta
        rx = mx + b_len * math.cos(right_angle)
        ry = my + b_len * math.sin(right_angle)

        c = seg.color
        # keep trunk as two segments A-M, M-B to keep things connected
        return [
            Segment((ax, ay), (mx, my), c),
            Segment((mx, my), (bx, by), c),
            Segment((mx, my), (lx, ly), c),
            Segment((mx, my), (rx, ry), c),
        ]


class ZigzagGenerator(GeneratorBase):
    """
    Replace AB with a zigzag path of 'parts' subsegments,
    alternating offsets above and below the original line.
    """
    def __init__(self, parts: int = 6, amplitude_factor: float = 0.2) -> None:
        self.name = "Zigzag"
        self.parts = max(2, parts)
        self.amplitude_factor = amplitude_factor

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        (ax, ay) = seg.a
        (bx, by) = seg.b

        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        nx, ny = -dy / length, dx / length  # unit perpendicular
        amplitude = self.amplitude_factor * length

        # generate points along AB with alternating offsets
        points: List[Vec2] = []
        for i in range(self.parts + 1):
            t = i / self.parts
            x = ax + dx * t
            y = ay + dy * t
            if 0 < i < self.parts:
                # interior points: alternate sign
                sign = 1 if (i % 2 == 1) else -1
                x += nx * amplitude * sign
                y += ny * amplitude * sign
            points.append((x, y))

        c = seg.color
        new_segments: List[Segment] = []
        for i in range(self.parts):
            new_segments.append(Segment(points[i], points[i + 1], c))
        return new_segments


class MidpointDisplacementGenerator(GeneratorBase):
    """
    Classic midpoint displacement: each segment AB becomes A-M-B,
    where M is midpoint plus random-ish perpendicular offset.
    """
    def __init__(self, strength: float = 0.3) -> None:
        self.name = "Mid-displace"
        self.strength = strength

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        (ax, ay) = seg.a
        (bx, by) = seg.b
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        mx = (ax + bx) / 2.0
        my = (ay + by) / 2.0

        nx, ny = -dy / length, dx / length  # unit perpendicular

        # pseudo-random based on coordinates
        seed = math.sin(ax * 12.9898 + ay * 78.233 + bx * 37.719 + by * 11.13) * 43758.5453
        frac = seed - math.floor(seed)
        offset = (frac * 2.0 - 1.0) * self.strength * length

        mx_off = mx + nx * offset
        my_off = my + ny * offset

        c = seg.color
        return [
            Segment((ax, ay), (mx_off, my_off), c),
            Segment((mx_off, my_off), (bx, by), c),
        ]


class ArcGenerator(GeneratorBase):
    """
    Replace a straight segment with a simple circular arc (approx with segments).
    """
    def __init__(self, steps: int = 8, bulge_factor: float = 0.5) -> None:
        self.name = "Arc"
        self.steps = max(2, steps)
        self.bulge_factor = bulge_factor  # how "curved" the arc is

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        (ax, ay) = seg.a
        (bx, by) = seg.b
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        # midpoint and perpendicular
        mx = (ax + bx) / 2.0
        my = (ay + by) / 2.0
        nx, ny = -dy / length, dx / length
        # center offset controls bulge
        radius = length / 2.0 / max(0.01, self.bulge_factor)
        cx = mx + nx * (radius - length / 2.0) * self.bulge_factor
        cy = my + ny * (radius - length / 2.0) * self.bulge_factor

        # angles from center to endpoints
        ang_a = math.atan2(ay - cy, ax - cx)
        ang_b = math.atan2(by - cy, bx - cx)

        # ensure direction (shorter arc)
        while ang_b < ang_a:
            ang_b += 2 * math.pi

        c = seg.color
        points: List[Vec2] = []
        for i in range(self.steps + 1):
            t = i / self.steps
            ang = ang_a + (ang_b - ang_a) * t
            x = cx + math.cos(ang) * radius
            y = cy + math.sin(ang) * radius
            points.append((x, y))

        new_segments: List[Segment] = []
        for i in range(self.steps):
            new_segments.append(Segment(points[i], points[i + 1], c))
        return new_segments


class ExtendGenerator(GeneratorBase):
    """
    Pattern-level: copy+paste pattern end-to-end.
    Base vector = last_point - first_point; paste translated copy.
    """
    def __init__(self) -> None:
        self.name = "Extend"

    def apply(self, pattern: Pattern, max_segments: int = 20000) -> None:
        if not pattern.segments:
            return
        start = pattern.first_point()
        end = pattern.last_point()
        if start is None or end is None:
            return
        dx = end[0] - start[0]
        dy = end[1] - start[1]

        current = list(pattern.segments)
        new_segments = list(pattern.segments)
        for seg in current:
            na = (seg.a[0] + dx, seg.a[1] + dy)
            nb = (seg.b[0] + dx, seg.b[1] + dy)
            new_segments.append(Segment(na, nb, seg.color))

        if len(new_segments) > max_segments:
            new_segments = new_segments[:max_segments]
        pattern.segments = new_segments

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        return [seg]


class ColorAngleGenerator(GeneratorBase):
    """
    Color-only generator: recolor segments based on their orientation.
    Geometry stays the same.

    NOTE: segment.color is updated, but drawing currently uses a
    direction gradient; this is still useful if later you decide to
    visualize stored colors or use them as metadata.
    """
    def __init__(self) -> None:
        self.name = "Color-angle"

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        ax, ay = seg.a
        bx, by = seg.b
        dx = bx - ax
        dy = by - ay
        angle = math.atan2(dy, dx)  # -pi..pi
        t = (angle + math.pi) / (2 * math.pi)  # 0..1

        # map t to a simple red-blue gradient with some green
        r = int(60 + 195 * t)
        g = int(40 + 150 * (1 - abs(t - 0.5) * 2))  # more green in the middle
        b = int(60 + 195 * (1 - t))
        c = (r, g, b)

        return [Segment(seg.a, seg.b, c)]


class StarburstGenerator(GeneratorBase):
    """
    From each segment's midpoint, emit several rays outward.
    Keeps the original segment and adds rays.
    """
    def __init__(self, rays: int = 6, length_factor: float = 0.4) -> None:
        self.name = "Starburst"
        self.rays = max(1, rays)
        self.length_factor = length_factor

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        (ax, ay) = seg.a
        (bx, by) = seg.b
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        mx = (ax + bx) / 2.0
        my = (ay + by) / 2.0
        base_len = self.length_factor * length
        c = seg.color

        new_segments: List[Segment] = [seg]  # keep original
        for i in range(self.rays):
            angle = 2.0 * math.pi * i / self.rays
            ex = mx + base_len * math.cos(angle)
            ey = my + base_len * math.sin(angle)
            new_segments.append(Segment((mx, my), (ex, ey), c))
        return new_segments


class JitterGenerator(GeneratorBase):
    """
    Add random-like jitter to each endpoint of each segment.
    Geometry becomes noisy; connectivity is approximate but visually continuous.
    """
    def __init__(self, magnitude_factor: float = 0.1) -> None:
        self.name = "Jitter"
        self.magnitude_factor = magnitude_factor

    def _jitter_point(self, x: float, y: float, mag: float) -> Vec2:
        # pseudo-random based on coordinates
        seed = math.sin(x * 12.9898 + y * 78.233) * 43758.5453
        frac = seed - math.floor(seed)
        angle = 2.0 * math.pi * frac
        r = mag * frac
        return x + math.cos(angle) * r, y + math.sin(angle) * r

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        (ax, ay) = seg.a
        (bx, by) = seg.b
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [seg]

        mag = self.magnitude_factor * length
        na = self._jitter_point(ax, ay, mag)
        nb = self._jitter_point(bx, by, mag)
        return [Segment(na, nb, seg.color)]


class ScaleInGenerator(GeneratorBase):
    """
    Pattern-level: add a smaller scaled copy of the pattern towards its centroid.
    """
    def __init__(self, factor: float = 0.5) -> None:
        self.name = "Scale-in"
        self.factor = factor

    def apply(self, pattern: Pattern, max_segments: int = 20000) -> None:
        if not pattern.segments:
            return
        verts = pattern.all_vertices()
        xs = [p[0] for p in verts]
        ys = [p[1] for p in verts]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        f = self.factor

        current = list(pattern.segments)
        new_segments = list(pattern.segments)
        for seg in current:
            ax, ay = seg.a
            bx, by = seg.b
            na = (cx + (ax - cx) * f, cy + (ay - cy) * f)
            nb = (cx + (bx - cx) * f, cy + (by - cy) * f)
            new_segments.append(Segment(na, nb, seg.color))
        if len(new_segments) > max_segments:
            new_segments = new_segments[:max_segments]
        pattern.segments = new_segments

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        return [seg]


class RotateCopyGenerator(GeneratorBase):
    """
    Pattern-level: add a rotated copy of the pattern around its centroid.
    """
    def __init__(self, angle_deg: float = 45.0) -> None:
        self.name = "Rotate-copy"
        self.angle_deg = angle_deg

    def apply(self, pattern: Pattern, max_segments: int = 20000) -> None:
        if not pattern.segments:
            return
        verts = pattern.all_vertices()
        xs = [p[0] for p in verts]
        ys = [p[1] for p in verts]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        theta = math.radians(self.angle_deg)
        ct = math.cos(theta)
        st = math.sin(theta)

        current = list(pattern.segments)
        new_segments = list(pattern.segments)
        for seg in current:
            ax, ay = seg.a
            bx, by = seg.b

            dax, day = ax - cx, ay - cy
            dbx, dby = bx - cx, by - cy

            rax = cx + dax * ct - day * st
            ray = cy + dax * st + day * ct
            rbx = cx + dbx * ct - dby * st
            rby = cy + dbx * st + dby * ct

            new_segments.append(Segment((rax, ray), (rbx, rby), seg.color))

        if len(new_segments) > max_segments:
            new_segments = new_segments[:max_segments]
        pattern.segments = new_segments

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        return [seg]


class CustomPatternGenerator(GeneratorBase):
    """
    User-defined pattern: replaces each segment with a template pattern
    drawn in a local coordinate system.

    The template is defined in (u,v) coordinates relative to a base segment
    (the red/gradient reference line in the editor).
    """
    def __init__(self) -> None:
        self.name = "Custom"
        self.local_segments: List[Tuple[Vec2, Vec2]] = []  # ((u1,v1),(u2,v2))
        self.base_length: float = 1.0
        self.has_pattern: bool = False

    def set_pattern(self, segments: List[Segment], base_seg: Segment) -> None:
        # Compute local coordinate frame from base_seg
        (ax, ay) = base_seg.a
        (bx, by) = base_seg.b
        dx = bx - ax
        dy = by - ay
        base_len = math.hypot(dx, dy)
        if base_len < 1e-6:
            # degenerate; fallback
            self.local_segments = [((0.0, 0.0), (1.0, 0.0))]
            self.base_length = 1.0
            self.has_pattern = True
            return

        e1x, e1y = dx / base_len, dy / base_len
        e2x, e2y = -e1y, e1x  # perpendicular
        base_origin = (ax, ay)

        def to_local(px: float, py: float) -> Vec2:
            vx = px - base_origin[0]
            vy = py - base_origin[1]
            u = vx * e1x + vy * e1y
            v = vx * e2x + vy * e2y
            return (u, v)

        local_segments: List[Tuple[Vec2, Vec2]] = []

        if segments:
            for seg in segments:
                u1, v1 = to_local(*seg.a)
                u2, v2 = to_local(*seg.b)
                local_segments.append(((u1, v1), (u2, v2)))
        else:
            # No user segments => identity pattern equal to the base line
            local_segments.append(((0.0, 0.0), (base_len, 0.0)))

        self.local_segments = local_segments
        self.base_length = base_len
        self.has_pattern = True

    def apply_to_segment(self, seg: Segment) -> List[Segment]:
        if not self.has_pattern or not self.local_segments:
            return [seg]

        (ax, ay) = seg.a
        (bx, by) = seg.b
        dx = bx - ax
        dy = by - ay
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-6 or self.base_length < 1e-6:
            return [seg]

        scale = seg_len / self.base_length

        f1x, f1y = dx / seg_len, dy / seg_len
        f2x, f2y = -f1y, f1x

        c = seg.color
        new_segments: List[Segment] = []
        for (u1, v1), (u2, v2) in self.local_segments:
            x1 = ax + scale * (u1 * f1x + v1 * f2x)
            y1 = ay + scale * (u1 * f1y + v1 * f2y)
            x2 = ax + scale * (u2 * f1x + v2 * f2x)
            y2 = ay + scale * (u2 * f1y + v2 * f2y)
            new_segments.append(Segment((x1, y1), (x2, y2), c))

        return new_segments


# ---------- ability metadata ----------
@dataclass
class Ability:
    generator: GeneratorBase
    hotkey: int     # 1..10 for numeric hotkeys; 0 or >10 = no numeric hotkey
    label: str      # text inside button
    rect: Optional[pygame.Rect] = None


# ---------- generator editor (new screen) ----------

class GeneratorEditor:
    """
    Simple editor for drawing a custom pattern on a snapped grid.

    - Shows a base reference segment from (-4,0) to (4,0) as a dotted
      purple->gold gradient.
    - Left-click: first click sets start, second click sets end (snapped).
    - Whenever a new segment's endpoint lies on an existing segment,
      that existing segment is automatically split there.
    - C: clear (restore only the base reference line; no pattern segments).
    - Enter: save and return (base_segment, segments)
    - Esc: cancel (return None)
    """

    def __init__(self, screen: pygame.Surface, width: int, height: int, font: pygame.font.Font) -> None:
        self.screen = screen
        self.width = width
        self.height = height
        self.font = font

        self.bg_color = (10, 10, 20)
        self.grid_color = (40, 40, 60)
        self.base_color = (220, 60, 60)   # kept but not used for drawing
        self.line_color = (220, 220, 230)

        self.scale = 40.0  # pixels per world unit
        self.grid_range = 10  # +/- in world units

        self.base_segment: Segment = Segment((-4.0, 0.0), (4.0, 0.0), self.base_color)
        # user segments in editor-local coordinates
        self.segments: List[Segment] = []

        self.pending_start: Optional[Vec2] = None
        self.mouse_pos: Tuple[int, int] = (0, 0)

    def _init_default_pattern(self) -> None:
        self.base_segment = Segment((-4.0, 0.0), (4.0, 0.0), self.base_color)
        self.segments.clear()
        self.pending_start = None

    def world_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        cx = self.width // 2
        cy = self.height // 2
        sx = cx + x * self.scale
        sy = cy - y * self.scale
        return int(round(sx)), int(round(sy))

    def screen_to_world(self, sx: float, sy: float) -> Vec2:
        cx = self.width // 2
        cy = self.height // 2
        x = (sx - cx) / self.scale
        y = (cy - sy) / self.scale
        return x, y

    def snap_to_grid(self, x: float, y: float) -> Vec2:
        return round(x), round(y)

    def draw_grid(self) -> None:
        for i in range(-self.grid_range, self.grid_range + 1):
            # vertical
            x0, y0 = self.world_to_screen(i, -self.grid_range)
            x1, y1 = self.world_to_screen(i, self.grid_range)
            pygame.draw.line(self.screen, self.grid_color, (x0, y0), (x1, y1), 1)

            # horizontal
            x0, y0 = self.world_to_screen(-self.grid_range, i)
            x1, y1 = self.world_to_screen(self.grid_range, i)
            pygame.draw.line(self.screen, self.grid_color, (x0, y0), (x1, y1), 1)

        # axis lines
        x0, y0 = self.world_to_screen(-self.grid_range, 0)
        x1, y1 = self.world_to_screen(self.grid_range, 0)
        pygame.draw.line(self.screen, (90, 90, 130), (x0, y0), (x1, y1), 2)
        x0, y0 = self.world_to_screen(0, -self.grid_range)
        x1, y1 = self.world_to_screen(0, self.grid_range)
        pygame.draw.line(self.screen, (90, 90, 130), (x0, y0), (x1, y1), 2)

    def draw_segments(self) -> None:
        # draw base reference segment as dotted purple->gold gradient
        x1, y1 = self.world_to_screen(*self.base_segment.a)
        x2, y2 = self.world_to_screen(*self.base_segment.b)
        draw_directional_gradient_line(self.screen, (x1, y1), (x2, y2), width=3, dashed=True)

        # draw user segments as solid gradient
        for seg in self.segments:
            x1, y1 = self.world_to_screen(*seg.a)
            x2, y2 = self.world_to_screen(*seg.b)
            draw_directional_gradient_line(self.screen, (x1, y1), (x2, y2), width=3, dashed=False)

        # pending preview
        if self.pending_start is not None:
            wx, wy = self.screen_to_world(*self.mouse_pos)
            ex, ey = self.snap_to_grid(wx, wy)
            sx1, sy1 = self.world_to_screen(*self.pending_start)
            sx2, sy2 = self.world_to_screen(ex, ey)
            draw_directional_gradient_line(self.screen, (sx1, sy1), (sx2, sy2), width=2, dashed=False)

    def draw_ui(self) -> None:
        lines = [
            "Custom Generator Editor",
            "Base dotted line (purple->gold) is a reference only; pattern is built from your segments.",
            "Left-click: start/end points for segments (snapped to grid).",
            "Segments split automatically when you place a vertex on them.",
            "C: clear to base   Enter: save   Esc: cancel",
        ]
        y = 10
        for line in lines:
            surf = self.font.render(line, True, (220, 230, 240))
            self.screen.blit(surf, (10, y))
            y += surf.get_height() + 2

    # --- segment splitting helpers ---

    @staticmethod
    def _point_on_segment(p: Vec2, seg: Segment) -> bool:
        (px, py) = p
        (ax, ay) = seg.a
        (bx, by) = seg.b
        # collinearity via cross product (exact ints after snap)
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        if cross != 0:
            return False
        # bounding box
        if px < min(ax, bx) or px > max(ax, bx):
            return False
        if py < min(ay, by) or py > max(ay, by):
            return False
        return True

    @staticmethod
    def _split_segment_at_point(seg: Segment, p: Vec2) -> List[Segment]:
        if not GeneratorEditor._point_on_segment(p, seg):
            return [seg]
        if p == seg.a or p == seg.b:
            return [seg]
        return [Segment(seg.a, p, seg.color), Segment(p, seg.b, seg.color)]

    def _insert_segment_with_splitting(self, new_seg: Segment) -> None:
        """Insert new_seg, splitting existing segments at its endpoints if needed."""
        pA = new_seg.a
        pB = new_seg.b

        updated: List[Segment] = []
        for seg in self.segments:
            pieces = [seg]
            for p in (pA, pB):
                tmp: List[Segment] = []
                for s in pieces:
                    tmp.extend(self._split_segment_at_point(s, p))
                pieces = tmp
            updated.extend(pieces)
        self.segments = updated
        self.segments.append(new_seg)

    def run(self) -> Optional[Tuple[Segment, List[Segment]]]:
        clock = pygame.time.Clock()
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    raise SystemExit
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return None  # cancel
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        # return base + user pattern segments
                        return self.base_segment, self.segments[:]
                    elif event.key == pygame.K_c:
                        self._init_default_pattern()
                elif event.type == pygame.MOUSEMOTION:
                    self.mouse_pos = event.pos
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    # left-click: start or finish segment
                    wx, wy = self.screen_to_world(*event.pos)
                    gx, gy = self.snap_to_grid(wx, wy)
                    if self.pending_start is None:
                        self.pending_start = (gx, gy)
                    else:
                        if (gx, gy) != self.pending_start:
                            color = self.line_color
                            new_seg = Segment(self.pending_start, (gx, gy), color)
                            self._insert_segment_with_splitting(new_seg)
                        self.pending_start = None

            self.screen.fill(self.bg_color)
            self.draw_grid()
            self.draw_segments()
            self.draw_ui()

            pygame.display.flip()
            clock.tick(60)


# ---------- visualization with pygame ----------

class FractalApp:
    def __init__(self, width: int = 1400, height: int = 900) -> None:
        pygame.init()
        pygame.display.set_caption("Edgecaster Fractal Lab (MVP)")
        self.screen = pygame.display.set_mode((width, height))
        self.clock = pygame.time.Clock()
        self.width = width
        self.height = height
        self.running = True

        # visual
        self.bg_color = (15, 15, 25)
        self.grid_color = (45, 45, 60)
        self.grid_spacing_world = 80.0

        # pattern
        self.pattern = Pattern()
        self._reset_pattern()

        # font
        self.font = pygame.font.SysFont("consolas", 18)

        # generators
        self.subdiv_gen = SubdivideGenerator(parts=3)
        self.koch_gen = KochGenerator(height_factor=0.25)
        self.branch_gen = BranchGenerator(angle_deg=30.0, length_factor=0.6)
        self.zigzag_gen = ZigzagGenerator(parts=6, amplitude_factor=0.2)
        self.middisp_gen = MidpointDisplacementGenerator(strength=0.3)
        self.arc_gen = ArcGenerator(steps=8, bulge_factor=0.5)
        self.extend_gen = ExtendGenerator()
        self.color_angle_gen = ColorAngleGenerator()
        self.starburst_gen = StarburstGenerator(rays=6, length_factor=0.4)
        self.jitter_gen = JitterGenerator(magnitude_factor=0.1)
        self.scale_in_gen = ScaleInGenerator(factor=0.5)
        self.rotate_copy_gen = RotateCopyGenerator(angle_deg=45.0)
        self.custom_gen = CustomPatternGenerator()

        self.abilities: List[Ability] = [
            Ability(self.subdiv_gen, 1, "1: Subdivide"),
            Ability(self.koch_gen, 2, "2: Koch"),
            Ability(self.branch_gen, 3, "3: Branch"),
            Ability(self.zigzag_gen, 4, "4: Zigzag"),
            Ability(self.middisp_gen, 5, "5: Mid-displace"),
            Ability(self.extend_gen, 6, "6: Extend"),
            Ability(self.arc_gen, 7, "7: Arc"),
            Ability(self.color_angle_gen, 8, "8: Color-angle"),
            Ability(self.starburst_gen, 9, "9: Starburst"),
            Ability(self.jitter_gen, 10, "0: Jitter"),
            Ability(self.scale_in_gen, 11, "Scale-in"),
            Ability(self.rotate_copy_gen, 12, "Rotate-copy"),
            Ability(self.custom_gen, 0, "Custom"),  # clickable only
        ]
        self.current_ability_index = 0

        # view / camera (auto-zoom)
        self.view_scale = 1.0
        self.view_off_x = 0.0
        self.view_off_y = 0.0
        self.world_min_x = 0.0
        self.world_max_x = float(self.width)
        self.world_min_y = 0.0
        self.world_max_y = float(self.height)

        # heatmap
        self.show_heatmap = False

        # layout
        self.ability_bar_height = 80
        self.ability_bar_margin = 8
        self.layout_ability_boxes()

    def _reset_pattern(self) -> None:
        """
        Initial pattern defined in world coordinates around y=0,
        so auto-zoom centers it in the grid area.
        """
        length = 800.0
        self.pattern.reset_line((-length / 2.0, 0.0), (length / 2.0, 0.0))

    # ---- camera / transform ----

    def update_view(self) -> None:
        """Auto-zoom so all vertices fit in the view."""
        if self.pattern.segments:
            verts = self.pattern.all_vertices()
            xs = [p[0] for p in verts]
            ys = [p[1] for p in verts]
            min_x = min(xs)
            max_x = max(xs)
            min_y = min(ys)
            max_y = max(ys)
        else:
            min_x, max_x = 0.0, float(self.width)
            min_y, max_y = 0.0, float(self.height)

        # avoid zero-width / zero-height
        if abs(max_x - min_x) < 1e-6:
            cx = 0.5 * (min_x + max_x)
            min_x = cx - 1.0
            max_x = cx + 1.0
        if abs(max_y - min_y) < 1e-6:
            cy = 0.5 * (min_y + max_y)
            min_y = cy - 1.0
            max_y = cy + 1.0

        # pad a bit
        pad_x = 0.1 * (max_x - min_x)
        pad_y = 0.1 * (max_y - min_y)
        min_x -= pad_x
        max_x += pad_x
        min_y -= pad_y
        max_y += pad_y

        self.world_min_x, self.world_max_x = min_x, max_x
        self.world_min_y, self.world_max_y = min_y, max_y

        world_w = max_x - min_x
        world_h = max_y - min_y

        # camera margins; leave space for ability bar at bottom
        margin_side = 40
        margin_top = 40
        margin_bottom = self.ability_bar_height + 40

        avail_w = self.width - 2 * margin_side
        avail_h = self.height - margin_top - margin_bottom

        if world_w <= 0 or world_h <= 0:
            self.view_scale = 1.0
            self.view_off_x = margin_side - min_x
            self.view_off_y = margin_top - min_y
            return

        scale_x = avail_w / world_w
        scale_y = avail_h / world_h
        self.view_scale = min(scale_x, scale_y)

        # compute offset so that min_x maps to margin_side, min_y to margin_top
        self.view_off_x = margin_side - min_x * self.view_scale
        self.view_off_y = margin_top - min_y * self.view_scale

    def world_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        sx = x * self.view_scale + self.view_off_x
        sy = y * self.view_scale + self.view_off_y
        return int(round(sx)), int(round(sy))

    def screen_to_world(self, sx: float, sy: float) -> Vec2:
        x = (sx - self.view_off_x) / self.view_scale
        y = (sy - self.view_off_y) / self.view_scale
        return x, y

    # ---- ability bar ----

    def layout_ability_boxes(self) -> None:
        n = len(self.abilities)
        if n == 0:
            return
        margin = self.ability_bar_margin
        bar_top = self.height - self.ability_bar_height + margin
        bar_bottom = self.height - margin
        bar_height = bar_bottom - bar_top
        total_width = self.width - 2 * margin
        gap = 8
        box_width = (total_width - gap * (n - 1)) / n

        x = margin
        for ability in self.abilities:
            rect = pygame.Rect(int(x), int(bar_top), int(box_width), int(bar_height))
            ability.rect = rect
            x += box_width + gap

    @property
    def current_generator(self) -> GeneratorBase:
        return self.abilities[self.current_ability_index].generator

    # --- heat color helper ---

    @staticmethod
    def heat_color_from_t(t: float, with_alpha: bool = True) -> Tuple[int, int, int] | Tuple[int, int, int, int]:
        t = max(0.0, min(1.0, t))
        r = 180 + int(75 * t)
        g = int(40 + 180 * t)
        b = 0
        if with_alpha:
            alpha = int(40 + 180 * t)
            return (r, g, b, alpha)
        else:
            return (r, g, b)

    # --- heatmap computation & drawing ---

    def compute_heatmap(self, spacing_world: float) -> Dict[Tuple[int, int], int]:
        counts: Dict[Tuple[int, int], int] = {}
        if not self.pattern.segments:
            return counts
        for (x, y) in self.pattern.all_vertices():
            ix = math.floor(x / spacing_world)
            iy = math.floor(y / spacing_world)
            key = (ix, iy)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def draw_heatmap(self, spacing_world: float, counts: Dict[Tuple[int, int], int], max_count: int) -> None:
        if not counts or max_count <= 0:
            return

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        for (ix, iy), c in counts.items():
            # world rect of this cell
            x0 = ix * spacing_world
            y0 = iy * spacing_world
            x1 = x0 + spacing_world
            y1 = y0 + spacing_world

            # convert to screen rect
            sx0, sy0 = self.world_to_screen(x0, y0)
            sx1, sy1 = self.world_to_screen(x1, y1)

            rx = min(sx0, sx1)
            ry = min(sy0, sy1)
            rw = abs(sx1 - sx0)
            rh = abs(sy1 - sy0)
            if rw == 0 or rh == 0:
                continue

            t = c / max_count  # 0..1
            color_rgba = self.heat_color_from_t(t, with_alpha=True)
            pygame.draw.rect(overlay, color_rgba, pygame.Rect(rx, ry, rw, rh))

        self.screen.blit(overlay, (0, 0))

    def draw_heatmap_scale(self, max_count: int) -> None:
        if max_count <= 0:
            return

        margin_right = 20
        bar_width = 24
        top = 40
        bottom = self.height - self.ability_bar_height - 40
        bar_rect = pygame.Rect(
            self.width - margin_right - bar_width,
            top,
            bar_width,
            bottom - top,
        )

        # border
        pygame.draw.rect(self.screen, (200, 200, 200), bar_rect, 1)

        # gradient fill (top = t=1, bottom = t=0)
        for y in range(bar_rect.top + 1, bar_rect.bottom - 1):
            t = (bar_rect.bottom - 1 - y) / (bar_rect.height - 2)
            r, g, b = self.heat_color_from_t(t, with_alpha=False)
            pygame.draw.line(self.screen, (r, g, b), (bar_rect.left + 1, y), (bar_rect.right - 1, y))

        # labels
        label_max = self.font.render(f"{max_count}", True, (220, 230, 240))
        label_min = self.font.render("0", True, (220, 230, 240))
        self.screen.blit(label_max, (bar_rect.left - label_max.get_width() - 4, bar_rect.top - 2))
        self.screen.blit(label_min, (bar_rect.left - label_min.get_width() - 4, bar_rect.bottom - label_min.get_height()))

    # --- custom pattern preview in ability bar ---

    def draw_custom_preview(self, rect: pygame.Rect, gen: CustomPatternGenerator) -> None:
        if not gen.has_pattern or not gen.local_segments:
            return

        # Collect local points
        us: List[float] = []
        vs: List[float] = []
        for (u1, v1), (u2, v2) in gen.local_segments:
            us.append(u1)
            us.append(u2)
            vs.append(v1)
            vs.append(v2)

        min_u, max_u = min(us), max(us)
        min_v, max_v = min(vs), max(vs)

        # Avoid degenerate
        if abs(max_u - min_u) < 1e-6:
            max_u = min_u + 1.0
        if abs(max_v - min_v) < 1e-6:
            max_v = min_v + 1.0

        pad = 4
        avail_w = rect.w - 2 * pad
        avail_h = rect.h - 2 * pad

        scale_x = avail_w / (max_u - min_u)
        scale_y = avail_h / (max_v - min_v)
        scale = min(scale_x, scale_y)

        mid_u = 0.5 * (min_u + max_u)
        mid_v = 0.5 * (min_v + max_v)
        cx = rect.x + rect.w / 2
        cy = rect.y + rect.h / 2

        def local_to_screen(u: float, v: float) -> Tuple[int, int]:
            sx = cx + (u - mid_u) * scale
            sy = cy - (v - mid_v) * scale
            return int(round(sx)), int(round(sy))

        # Draw with a tiny directional gradient from start of each local segment
        for (u1, v1), (u2, v2) in gen.local_segments:
            p1 = local_to_screen(u1, v1)
            p2 = local_to_screen(u2, v2)
            draw_directional_gradient_line(self.screen, p1, p2, width=2, dashed=False)

    # --- input handling ---

    def handle_keydown(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self.running = False
            return

        if key == pygame.K_r:
            self._reset_pattern()
            return

        if key == pygame.K_SPACE:
            self.current_generator.apply(self.pattern)
            # auto clean duplicates (same-orientation only) after every apply
            self.pattern.cleanup_duplicates()
            return

        if key == pygame.K_h:
            self.show_heatmap = True

        if key == pygame.K_g:
            self.edit_custom_generator()
            return

        # number keys for selecting abilities
        if pygame.K_1 <= key <= pygame.K_9:
            hotkey = key - pygame.K_1 + 1  # 1..9
            self.select_ability_by_hotkey(hotkey)
        elif key == pygame.K_0:
            self.select_ability_by_hotkey(10)  # 0 -> 10th ability

        # adjust parameters of current generator
        if key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS):
            self._adjust_param(delta=1)
        if key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self._adjust_param(delta=-1)

    def handle_keyup(self, key: int) -> None:
        if key == pygame.K_h:
            self.show_heatmap = False

    def edit_custom_generator(self) -> None:
        editor = GeneratorEditor(self.screen, self.width, self.height, self.font)
        result = editor.run()
        if result is not None:
            base_seg, segments = result
            # set pattern even if segments is empty (identity)
            self.custom_gen.set_pattern(segments, base_seg)
            # auto-select the custom ability
            for idx, ability in enumerate(self.abilities):
                if ability.generator is self.custom_gen:
                    self.current_ability_index = idx
                    break

    def select_ability_by_hotkey(self, hotkey: int) -> None:
        for idx, ability in enumerate(self.abilities):
            if ability.hotkey == hotkey:
                self.current_ability_index = idx
                break

    def handle_mouse_down(self, pos: Tuple[int, int], button: int) -> None:
        if button != 1:  # left-click only for selection
            return
        x, y = pos
        for idx, ability in enumerate(self.abilities):
            if ability.rect and ability.rect.collidepoint(x, y):
                self.current_ability_index = idx
                break

    def _adjust_param(self, delta: int) -> None:
        gen = self.current_generator
        if isinstance(gen, SubdivideGenerator):
            gen.parts = max(2, gen.parts + delta)
        elif isinstance(gen, KochGenerator):
            gen.height_factor = max(0.0, gen.height_factor + delta * 0.05)
        elif isinstance(gen, BranchGenerator):
            gen.angle_deg = max(0.0, gen.angle_deg + delta * 5.0)
        elif isinstance(gen, ZigzagGenerator):
            gen.parts = max(2, gen.parts + delta)
        elif isinstance(gen, MidpointDisplacementGenerator):
            gen.strength = max(0.0, gen.strength + delta * 0.05)
        elif isinstance(gen, ArcGenerator):
            gen.steps = max(2, gen.steps + delta)
        elif isinstance(gen, StarburstGenerator):
            gen.rays = max(1, gen.rays + delta)
        elif isinstance(gen, JitterGenerator):
            gen.magnitude_factor = max(0.0, gen.magnitude_factor + delta * 0.02)
        elif isinstance(gen, ScaleInGenerator):
            gen.factor = max(0.1, min(2.0, gen.factor + delta * 0.1))
        elif isinstance(gen, RotateCopyGenerator):
            gen.angle_deg = (gen.angle_deg + delta * 5.0) % 360
        # Extend, ColorAngle, Custom have no numeric params here

    # --- drawing ---

    def draw_grid(self, spacing_world: float) -> None:
        # Grid should cover from a fixed top to just above the buttons.
        grid_top_screen = 40
        grid_bottom_screen = self.height - self.ability_bar_height - 10

        # Convert screen vertical bounds to world y-range
        _, world_y_top = self.screen_to_world(0, grid_top_screen)
        _, world_y_bottom = self.screen_to_world(0, grid_bottom_screen)
        min_y = min(world_y_top, world_y_bottom)
        max_y = max(world_y_top, world_y_bottom)

        # For x, cover the whole screen width
        world_x_left, _ = self.screen_to_world(0, 0)
        world_x_right, _ = self.screen_to_world(self.width, 0)
        min_x = min(world_x_left, world_x_right)
        max_x = max(world_x_left, world_x_right)

        # vertical lines
        start_x = math.floor(min_x / spacing_world) * spacing_world
        end_x = math.ceil(max_x / spacing_world) * spacing_world
        x = start_x
        while x <= end_x:
            x1, y1 = self.world_to_screen(x, min_y)
            x2, y2 = self.world_to_screen(x, max_y)
            pygame.draw.line(self.screen, self.grid_color, (x1, y1), (x2, y2), 1)
            x += spacing_world

        # horizontal lines
        start_y = math.floor(min_y / spacing_world) * spacing_world
        end_y = math.ceil(max_y / spacing_world) * spacing_world
        y = start_y
        while y <= end_y:
            x1, y1 = self.world_to_screen(min_x, y)
            x2, y2 = self.world_to_screen(max_x, y)
            pygame.draw.line(self.screen, self.grid_color, (x1, y1), (x2, y2), 1)
            y += spacing_world

    def draw_pattern(self) -> None:
        # edges: direction gradient
        for seg in self.pattern.segments:
            p1 = self.world_to_screen(*seg.a)
            p2 = self.world_to_screen(*seg.b)
            draw_directional_gradient_line(self.screen, p1, p2, width=2, dashed=False)
        # vertices as bright markers
        for (x, y) in self.pattern.all_vertices():
            px, py = self.world_to_screen(x, y)
            pygame.draw.circle(self.screen, (255, 230, 160), (px, py), 3)

    def draw_ability_bar(self) -> None:
        # bar background
        bar_rect = pygame.Rect(
            0,
            self.height - self.ability_bar_height,
            self.width,
            self.ability_bar_height,
        )
        pygame.draw.rect(self.screen, (10, 10, 20), bar_rect)

        for idx, ability in enumerate(self.abilities):
            if ability.rect is None:
                continue
            # highlight current
            if idx == self.current_ability_index:
                border_color = (255, 255, 180)
                fill_color = (40, 40, 70)
            else:
                border_color = (120, 120, 160)
                fill_color = (25, 25, 45)

            pygame.draw.rect(self.screen, fill_color, ability.rect)
            pygame.draw.rect(self.screen, border_color, ability.rect, 2)

            # label
            text_surf = self.font.render(ability.label, True, (220, 230, 240))
            tw, th = text_surf.get_size()
            cx = ability.rect.x + ability.rect.w // 2
            cy = ability.rect.y + ability.rect.h // 2
            self.screen.blit(text_surf, (cx - tw // 2, cy - th // 2))

            # custom pattern preview
            if isinstance(ability.generator, CustomPatternGenerator):
                self.draw_custom_preview(ability.rect, ability.generator)

    def draw_info(self) -> None:
        gen = self.current_generator
        seg_count = len(self.pattern.segments)

        if isinstance(gen, SubdivideGenerator):
            param_text = f"parts = {gen.parts}"
        elif isinstance(gen, KochGenerator):
            param_text = f"height_factor = {gen.height_factor:.2f}"
        elif isinstance(gen, BranchGenerator):
            param_text = f"angle_deg = {gen.angle_deg:.1f}"
        elif isinstance(gen, ZigzagGenerator):
            param_text = f"parts = {gen.parts}, amp = {gen.amplitude_factor:.2f}"
        elif isinstance(gen, MidpointDisplacementGenerator):
            param_text = f"strength = {gen.strength:.2f}"
        elif isinstance(gen, ArcGenerator):
            param_text = f"steps = {gen.steps}, bulge = {gen.bulge_factor:.2f}"
        elif isinstance(gen, ExtendGenerator):
            param_text = "copy pattern end-to-end"
        elif isinstance(gen, ColorAngleGenerator):
            param_text = "updates segment.color based on orientation"
        elif isinstance(gen, StarburstGenerator):
            param_text = f"rays = {gen.rays}, length_factor = {gen.length_factor:.2f}"
        elif isinstance(gen, JitterGenerator):
            param_text = f"magnitude_factor = {gen.magnitude_factor:.2f}"
        elif isinstance(gen, ScaleInGenerator):
            param_text = f"factor = {gen.factor:.2f}"
        elif isinstance(gen, RotateCopyGenerator):
            param_text = f"angle_deg = {gen.angle_deg:.1f}"
        elif isinstance(gen, CustomPatternGenerator):
            count = len(gen.local_segments)
            state = "defined" if gen.has_pattern else "not defined"
            param_text = f"{state}, segments = {count}  (G to edit)"
        else:
            param_text = ""

        lines = [
            "SPACE: apply   R: reset   ESC: quit   H: hold heatmap   G: edit custom generator",
            "Edges show direction: purple (start) -> gold (end).",
            f"Current: {gen.name}",
            f"Param: {param_text}   (+/- to adjust where applicable)",
            f"Segments: {seg_count}",
        ]

        y = 10
        for line in lines:
            surf = self.font.render(line, True, (220, 230, 240))
            self.screen.blit(surf, (10, y))
            y += surf.get_height() + 2

    # --- main loop ---

    def run(self) -> None:
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    self.handle_keydown(event.key)
                elif event.type == pygame.KEYUP:
                    self.handle_keyup(event.key)
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self.handle_mouse_down(event.pos, event.button)

            # update camera / auto-zoom
            self.update_view()

            self.screen.fill(self.bg_color)
            self.draw_grid(self.grid_spacing_world)
            self.draw_pattern()

            max_count = 0
            counts: Dict[Tuple[int, int], int] = {}
            if self.show_heatmap:
                counts = self.compute_heatmap(self.grid_spacing_world)
                if counts:
                    max_count = max(counts.values())
                    self.draw_heatmap(self.grid_spacing_world, counts, max_count)
                    self.draw_heatmap_scale(max_count)

            self.draw_ability_bar()
            self.draw_info()

            pygame.display.flip()
            self.clock.tick(60)  # limit FPS

        pygame.quit()


if __name__ == "__main__":
    app = FractalApp()
    app.run()
