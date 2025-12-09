"""Simple pattern library with a starter glyph."""
from edgecaster.state.patterns import Pattern
from edgecaster.patterns import builder
import math
from typing import Dict, List, Tuple, Any


def starter_pattern() -> Pattern:
    """
    Start from a long line, apply a Koch bump and a subdivision pass,
    producing a small fractal path similar in spirit to the editor default.
    """
    base = builder.line_pattern((-6.0, 0.0), (6.0, 0.0))
    steps = [
        (builder.KochGenerator(height_factor=0.35), 1),
        (builder.SubdivideGenerator(parts=3), 1),
        (builder.BranchGenerator(angle_deg=22.0, length_factor=0.45), 1),
        (builder.JitterGenerator(magnitude_factor=0.05), 1),
    ]
    return builder.apply_chain(base, steps, max_segments=4000, dedup=True)


def action_preview_geometry(action: str, game: Any = None, overrides: Dict[str, object] | None = None) -> Dict[str, Any] | None:
    """
    Return a normalized preview geometry for an action (0..1 coordinates).

    The renderer uses this purely for icons; keeping it here avoids hard-coding
    fractal shapes in the renderer. Falls back to None if unknown.
    """
    g = lambda key, default: overrides.get(key, default) if overrides else default

    if action == "place":
        verts = [(0.15, 0.5), (0.85, 0.5)]
        segs = [(0, 1)]
        return {"verts": verts, "segs": segs, "strong": [1]}

    if action == "subdivide":
        parts = g("parts", 3)
        step = 1.0 / max(1, parts)
        verts = [(0.1 + 0.8 * i * step, 0.5) for i in range(parts + 1)]
        segs = [(i, i + 1) for i in range(len(verts) - 1)]
        return {"verts": verts, "segs": segs}

    if action == "extend":
        verts = [(0.1, 0.6), (0.5, 0.6), (0.9, 0.6)]
        segs = [(0, 1), (1, 2)]
        return {"verts": verts, "segs": segs, "dotted": [(0, 1)]}

    if action == "koch":
        height = g("height", game.get_param_value("koch", "height") if game else 0.25)
        flip = g("flip", game.get_param_value("koch", "flip") if game else False)
        length = 0.8
        base_y = 0.55
        amp = height * length
        margin = 0.08
        max_amp = max(0.05, min(base_y - margin, 1.0 - margin - base_y))
        if amp > max_amp:
            amp = max_amp
        ax, ay = 0.1, base_y
        bx, by = ax + length, base_y
        p1 = (ax + length / 3.0, base_y)
        p3 = (ax + 2.0 * length / 3.0, base_y)
        dy = amp if not flip else -amp
        peak = ((p1[0] + p3[0]) * 0.5, base_y + dy)
        verts = [ (ax, ay), p1, peak, p3, (bx, by) ]
        segs = [(0,1),(1,2),(2,3),(3,4)]
        return {"verts": verts, "segs": segs}

    if action == "branch":
        angle = g("angle", game.get_param_value("branch", "angle") if game else 45)
        count = g("count", game.get_param_value("branch", "count") if game else 3)
        verts = [(0.15, 0.6), (0.5, 0.6)]
        segs = [(0,1)]
        spread = math.radians(angle)
        length = 0.35
        for i in range(count):
            t = 0 if count == 1 else i / (count - 1)
            ang = -spread + 2 * spread * t
            vx = verts[1][0] + length * math.cos(ang)
            vy = verts[1][1] - length * math.sin(ang)
            verts.append((vx, vy))
            segs.append((1, len(verts) - 1))
        return {"verts": verts, "segs": segs, "strong": [1]}

    if action == "zigzag":
        parts = g("parts", game.get_param_value("zigzag", "parts") if game else 5)
        amp = g("amp", game.get_param_value("zigzag", "amp") if game else 0.2)
        verts = []
        segs = []
        for i in range(parts + 1):
            t = i / parts
            x = 0.1 + 0.8 * t
            y = 0.55 + ((-1) ** i) * amp * 0.6
            verts.append((x, y))
            if i > 0:
                segs.append((i - 1, i))
        return {"verts": verts, "segs": segs}

    if action.startswith("custom"):
        pattern = getattr(game.character, "custom_pattern", None) if game and hasattr(game, "character") else None
        amp = g("amplitude", game.get_param_value("custom", "amplitude") if game else 1.0)
        if game and hasattr(game, "custom_patterns"):
            idx = 0
            if action != "custom":
                try:
                    idx = int(action.split("_", 1)[1])
                except Exception:
                    idx = 0
            if idx < len(game.custom_patterns):
                pattern = game.custom_patterns[idx]
        pts = None
        edges = []
        if isinstance(pattern, dict):
            pts = pattern.get("vertices")
            edges = pattern.get("edges", [])
        else:
            pts = pattern
        if pts and len(pts) >= 2:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            width = max(1e-5, max_x - min_x)
            height = max(1e-5, max_y - min_y)
            height *= amp  # amplitude scales lateral span
            norm = []
            for x, y in pts:
                nx = (x - min_x) / width
                ny = (y - min_y) / height
                norm.append((nx, ny))
            pad = 0.12
            avail = 1.0 - 2 * pad
            aspect = width / height if height > 0 else 1.0
            if aspect >= 1:
                sx = avail
                sy = avail / aspect
            else:
                sx = avail * aspect
                sy = avail
            ox = (1.0 - sx) * 0.5
            oy = (1.0 - sy) * 0.5
            verts = [(ox + p[0] * sx, oy + (1 - p[1]) * sy) for p in norm]
            if edges:
                segs = [(a, b) for a, b in edges if a < len(verts) and b < len(verts)]
            else:
                segs = [(i, i + 1) for i in range(len(verts) - 1)]
            return {"verts": verts, "segs": segs}
        # no geometry known
        return None

    if action == "activate_all":
        radius = g("radius", game.get_param_value("activate_all", "radius") if game else 1.5)
        verts = [(0.25, 0.5), (0.75, 0.5), (0.5, 0.25), (0.5, 0.75)]
        return {"verts": verts, "segs": [], "circle": True, "radius": radius}

    if action == "activate_seed":
        depth = g("neighbor_depth", game.get_param_value("activate_seed", "neighbor_depth") if game else 1)
        verts = [(0.5, 0.5)]
        offsets = [(-0.25, 0), (0.25, 0), (0, -0.25), (0, 0.25)]
        for dx, dy in offsets:
            verts.append((0.5 + dx, 0.5 + dy))
        if depth >= 2:
            far = 0.45
            offsets2 = [(-far, 0), (far, 0), (0, -far), (0, far)]
            for dx, dy in offsets2:
                verts.append((0.5 + dx, 0.5 + dy))
        return {"verts": verts, "segs": [], "strong": [0], "boxes": list(range(1, len(verts)))}

    return None
