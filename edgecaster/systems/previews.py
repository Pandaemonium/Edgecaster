from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns import builder
from edgecaster.state.patterns import Pattern

TilePos = Tuple[int, int]
RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class PreviewText:
    """A single floating label for the renderer to draw."""

    pos: TilePos
    text: str
    color: RGB = (255, 255, 255)


@dataclass(frozen=True)
class PreviewCircle:
    """A circular area indicator centered in world tile-space."""

    # World tile position (can be float for sub-tile centers).
    pos: Tuple[float, float]
    # Radius in tile units.
    radius: float
    # RGBA base color (alpha is pulse-scaled by renderer).
    color: Tuple[int, int, int, int] = (140, 220, 255, 130)
    # Pulse style used by renderer (`sawtooth` or `sine`).
    pulse: str = "sawtooth"
    # Pulse period in milliseconds.
    period_ms: int = 1500


@dataclass(frozen=True)
class ActionPreview:
    """Immutable preview data for an action.

    This is computed in the logic layer and consumed by renderers as pure data.
    """

    action: str
    # Optional "ghost pattern" overlay.
    pattern: Optional[Pattern] = None
    pattern_anchor: Optional[TilePos] = None
    pattern_edge_alpha: int = 150
    pattern_color: Optional[RGB] = None
    pattern_color_end: Optional[RGB] = None

    # Optional tile highlights (e.g. Ignite/Regrow).
    direct_tiles: Tuple[TilePos, ...] = ()
    indirect_tiles: Tuple[TilePos, ...] = ()
    direct_color: Optional[Tuple[int, int, int, int]] = None
    indirect_color: Optional[Tuple[int, int, int, int]] = None

    # Optional circular indicators (e.g. radial hit zones around vertices).
    circles: Tuple[PreviewCircle, ...] = ()

    # Optional per-entity/actor labels.
    texts: Tuple[PreviewText, ...] = ()


PREVIEWABLE_ACTIONS: set[str] = {
    # Pattern modifiers
    "subdivide",
    "extend",
    "koch",
    "branch",
    "zigzag",
    "custom",
    # Color pattern
    "rainbow_edges",
    "verdant_edges",
    "winter_hue",
    # Color activators
    "regrow",
    "ignite",
    "freeze",
    # Chakra activator previews
    "energy_kick",
    "palm_burst",
}


def is_previewable_action(action: str) -> bool:
    return action in PREVIEWABLE_ACTIONS or action.startswith("custom_")


def build_action_preview(game: Any, action: str, actor_id: str | None = None) -> Optional[ActionPreview]:
    """Build an ActionPreview for a supported action without mutating game state."""
    action = str(action or "")
    if not action:
        return None

    pattern = getattr(game, "pattern", None)
    anchor = getattr(game, "pattern_anchor", None)

    # Pattern geometry previews (fractal ops)
    if action in {"subdivide", "extend", "koch", "branch", "zigzag"} or action.startswith("custom"):
        if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
            return None
        new_pattern = _preview_fractal_op(game, pattern, action)
        if new_pattern is None:
            return None
        # Subtle cyan tint to distinguish from the live rune.
        return ActionPreview(
            action=action,
            pattern=new_pattern,
            pattern_anchor=anchor,
            pattern_edge_alpha=150,
            pattern_color=(170, 220, 255),
            pattern_color_end=(120, 190, 255),
        )

    # Pattern color previews
    if action in {"rainbow_edges", "verdant_edges", "winter_hue"}:
        if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
            return None
        colored = _preview_colored_pattern(game, pattern, anchor, action)
        if colored is None:
            return None
        return ActionPreview(
            action=action,
            pattern=colored,
            pattern_anchor=anchor,
            pattern_edge_alpha=220,
        )

    # Tile-effect previews (ignite/regrow/freeze)
    if action in {"ignite", "regrow"}:
        if pattern is None or anchor is None or not getattr(pattern, "edges", None):
            return None
        return _preview_edge_aura(game, pattern, anchor, action)

    if action == "freeze":
        if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
            return None
        return _preview_freeze(game, pattern, anchor)

    if action == "energy_kick":
        if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
            return None
        return _preview_energy_kick(game, pattern, anchor)

    if action == "palm_burst":
        if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
            return None
        return _preview_palm_burst(game, pattern, anchor)

    return None


# ---------------------------------------------------------------------------
# Geometry previews (fractal ops)
# ---------------------------------------------------------------------------


def _preview_fractal_op(game: Any, pattern: Pattern, kind: str) -> Optional[Pattern]:
    """Return the resulting pattern if `kind` were applied, without side effects."""
    if not getattr(pattern, "vertices", None) or not getattr(pattern, "edges", None):
        return None

    max_segments = int(getattr(getattr(game, "cfg", None), "max_vertices", 20000))

    segs = pattern.to_segments()

    def pv(action: str, key: str, default: Any) -> Any:
        getter = getattr(game, "get_param_value", None)
        if callable(getter):
            try:
                return getter(action, key)
            except Exception:
                return default
        priv = getattr(game, "_param_value", None)
        if callable(priv):
            try:
                return priv(action, key)
            except Exception:
                return default
        return default

    if kind == "subdivide":
        parts = pv("subdivide", "parts", 3)
        gen = builder.SubdivideGenerator(parts=int(parts))
    elif kind == "koch":
        height = pv("koch", "height", 0.25)
        flip = pv("koch", "flip", False)
        gen = builder.KochGenerator(height_factor=float(height), flip=bool(flip))
    elif kind == "branch":
        angle = pv("branch", "angle", 30)
        count = pv("branch", "count", 2)
        gen = builder.BranchGenerator(angle_deg=float(angle), length_factor=0.45, branch_count=int(count))
    elif kind == "extend":
        gen = builder.ExtendGenerator()
    elif kind == "zigzag":
        parts = pv("zigzag", "parts", 6)
        amp = pv("zigzag", "amp", 0.2)
        gen = builder.ZigzagGenerator(parts=int(parts), amplitude_factor=float(amp))
    elif kind.startswith("custom"):
        idx = 0
        if kind != "custom":
            try:
                idx = int(kind.split("_", 1)[1])
            except Exception:
                idx = 0
        customs = getattr(game, "custom_patterns", None) or []
        if not customs or idx < 0 or idx >= len(customs):
            return None
        custom = customs[idx]
        verts = None
        edges: list = []
        if isinstance(custom, dict):
            verts = custom.get("vertices")
            edges = custom.get("edges", []) or []
        else:
            verts = custom
        if not verts or len(verts) < 2:
            return None
        amp = pv("custom", "amplitude", 1.0)
        if edges:
            gen = builder.CustomGraphGenerator(verts, edges, amplitude=float(amp))
        else:
            gen = builder.CustomPolyGenerator(verts, amplitude=float(amp))
    else:
        return None

    segs = gen.apply_segments(segs, max_segments=max_segments)
    segs = builder.cleanup_duplicates(segs)
    if len(segs) > max_segments:
        segs = segs[:max_segments]
    return Pattern.from_segments(segs)


# ---------------------------------------------------------------------------
# Color previews (pure, no mutation)
# ---------------------------------------------------------------------------


def _normalize_edge_key(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _edge_adjacency(pattern: Pattern) -> Dict[int, List[int]]:
    adj: Dict[int, List[int]] = {}
    for edge in getattr(pattern, "edges", []) or []:
        a = getattr(edge, "a", None)
        b = getattr(edge, "b", None)
        if a is None or b is None:
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    return adj


def _root_vertex_index(pattern: Pattern, anchor: TilePos) -> int:
    verts_world = project_vertices(pattern, anchor)
    rx, ry = anchor
    return min(range(len(verts_world)), key=lambda i: (verts_world[i][0] - rx) ** 2 + (verts_world[i][1] - ry) ** 2)


def _preview_colored_pattern(game: Any, pattern: Pattern, anchor: TilePos, action: str) -> Optional[Pattern]:
    import copy

    out = copy.deepcopy(pattern)
    if action == "rainbow_edges":
        edge_colors = _compute_rainbow_edge_colors(out, anchor)
        if not edge_colors:
            return None
        setattr(out, "edge_colors", edge_colors)
        return out
    if action == "verdant_edges":
        edge_colors = _compute_depth_green_edge_colors(out, anchor)
        if not edge_colors:
            return None
        setattr(out, "edge_colors", edge_colors)
        return out
    if action == "winter_hue":
        edge_colors, vertex_colors = _compute_winter_hue(out, anchor, game)
        if not edge_colors:
            return None
        setattr(out, "edge_colors", edge_colors)
        setattr(out, "vertex_colors", vertex_colors)
        return out
    return None


def _compute_rainbow_edge_colors(pattern: Pattern, anchor: TilePos) -> Dict[Tuple[int, int], RGB]:
    palette: List[RGB] = [
        (255, 0, 0),
        (255, 165, 0),
        (255, 255, 0),
        (0, 128, 0),
        (0, 0, 255),
        (75, 0, 130),
        (238, 130, 238),
    ]

    if not getattr(pattern, "edges", None):
        return {}

    adj = _edge_adjacency(pattern)
    root_idx = _root_vertex_index(pattern, anchor)

    from collections import deque

    depth: Dict[int, int] = {root_idx: 0}
    q = deque([root_idx])
    while q:
        cur = q.popleft()
        for nb in adj.get(cur, []):
            if nb in depth:
                continue
            depth[nb] = depth[cur] + 1
            q.append(nb)

    edge_colors: Dict[Tuple[int, int], RGB] = {}
    for edge in pattern.edges:
        a = getattr(edge, "a", None)
        b = getattr(edge, "b", None)
        if a is None or b is None:
            continue
        d = max(depth.get(a, 0), depth.get(b, 0))
        edge_colors[_normalize_edge_key(a, b)] = palette[d % len(palette)]
    return edge_colors


def _compute_depth_green_edge_colors(pattern: Pattern, anchor: TilePos) -> Dict[Tuple[int, int], RGB]:
    if not getattr(pattern, "edges", None):
        return {}

    adj = _edge_adjacency(pattern)
    root_idx = _root_vertex_index(pattern, anchor)

    from collections import deque

    depth: Dict[int, int] = {root_idx: 0}
    q = deque([root_idx])
    while q:
        cur = q.popleft()
        for nb in adj.get(cur, []):
            if nb in depth:
                continue
            depth[nb] = depth[cur] + 1
            q.append(nb)

    if not depth:
        return {}

    max_depth = max(depth.values()) or 1
    target_green = (34, 139, 34)
    edge_colors: Dict[Tuple[int, int], RGB] = {}
    for edge in pattern.edges:
        a = getattr(edge, "a", None)
        b = getattr(edge, "b", None)
        if a is None or b is None:
            continue
        d = max(depth.get(a, 0), depth.get(b, 0))
        t = d / max_depth if max_depth > 0 else 0.0
        r = int(255 + (target_green[0] - 255) * t)
        g = int(255 + (target_green[1] - 255) * t)
        bcol = int(255 + (target_green[2] - 255) * t)
        edge_colors[_normalize_edge_key(a, b)] = (r, g, bcol)
    return edge_colors


def _compute_winter_hue(pattern: Pattern, anchor: TilePos, game: Any) -> Tuple[Dict[Tuple[int, int], Tuple[RGB, RGB]], List[RGB]]:
    verts_world = project_vertices(pattern, anchor)
    if not verts_world:
        return {}, []

    try:
        radius = float(getattr(game, "get_param_value")("winter_hue", "radius"))
    except Exception:
        radius = 2.0
    try:
        scale = float(getattr(game, "get_param_value")("winter_hue", "scale"))
    except Exception:
        scale = 2.0

    r2 = radius * radius
    softness = max(1e-3, scale * 4.0)
    white: RGB = (255, 255, 255)
    deep_blue: RGB = (10, 60, 180)

    vertex_colors: List[RGB] = []
    for i, (x, y) in enumerate(verts_world):
        cnt = 0
        for j, (ox, oy) in enumerate(verts_world):
            if i == j:
                continue
            dx = ox - x
            dy = oy - y
            if dx * dx + dy * dy <= r2:
                cnt += 1
        intensity = math.sqrt(cnt) / softness
        if intensity > 1.0:
            intensity = 1.0
        if intensity < 0.0:
            intensity = 0.0
        vertex_colors.append(
            (
                int(white[0] + (deep_blue[0] - white[0]) * intensity),
                int(white[1] + (deep_blue[1] - white[1]) * intensity),
                int(white[2] + (deep_blue[2] - white[2]) * intensity),
            )
        )

    edge_colors: Dict[Tuple[int, int], Tuple[RGB, RGB]] = {}
    for edge in getattr(pattern, "edges", []) or []:
        a = getattr(edge, "a", None)
        b = getattr(edge, "b", None)
        if a is None or b is None:
            continue
        ca = vertex_colors[a] if 0 <= a < len(vertex_colors) else white
        cb = vertex_colors[b] if 0 <= b < len(vertex_colors) else white
        edge_colors[_normalize_edge_key(a, b)] = (ca, cb)

    return edge_colors, vertex_colors


# ---------------------------------------------------------------------------
# Tile-effect previews (ignite / regrow / freeze)
# ---------------------------------------------------------------------------


def _line_points(x0: int, y0: int, x1: int, y1: int) -> List[TilePos]:
    """Bresenham; matches the helper used in the game simulation."""
    points: List[TilePos] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def _edge_rgb(pattern: Pattern, a: int, b: int) -> Optional[RGB]:
    edge_colors = getattr(pattern, "edge_colors", {}) or {}
    col = edge_colors.get(_normalize_edge_key(a, b))
    if col is None:
        # Fall back to the Edge.color attribute (may be a string or tuple).
        for e in getattr(pattern, "edges", []) or []:
            if (getattr(e, "a", None), getattr(e, "b", None)) in {(a, b), (b, a)}:
                col = getattr(e, "color", None)
                break
    if not isinstance(col, (list, tuple)) or len(col) < 3:
        return None
    # If this is a gradient pair ((r,g,b),(r,g,b)), treat as unsupported here.
    if len(col) == 2 and all(isinstance(c, (list, tuple)) for c in col):
        return None
    try:
        r = int(col[0])
        g = int(col[1])
        bcol = int(col[2])
    except Exception:
        return None
    return (r, g, bcol)


def _preview_edge_aura(game: Any, pattern: Pattern, anchor: TilePos, action: str) -> Optional[ActionPreview]:
    verts_world = project_vertices(pattern, anchor)
    if not verts_world:
        return None

    is_ignite = action == "ignite"
    base_direct = 4.0 if is_ignite else 3.5
    base_indirect = 2.0 if is_ignite else 1.5

    direct_color = (255, 80, 40, 150) if is_ignite else (80, 255, 120, 150)
    indirect_color = (255, 180, 80, 90) if is_ignite else (80, 220, 200, 90)
    label_color: RGB = (255, 160, 160) if is_ignite else (140, 255, 180)
    sign = "-" if is_ignite else "+"

    direct_tiles: Dict[TilePos, float] = {}
    for edge in getattr(pattern, "edges", []) or []:
        a = getattr(edge, "a", None)
        b = getattr(edge, "b", None)
        if a is None or b is None:
            continue
        col = _edge_rgb(pattern, a, b)
        if col is None:
            continue
        r, g, bl = col
        strength = max(0, (r - max(g, bl))) if is_ignite else max(0, (g - max(r, bl)))
        if strength <= 0:
            continue
        if not (0 <= a < len(verts_world) and 0 <= b < len(verts_world)):
            continue
        ax, ay = verts_world[a]
        bx, by = verts_world[b]
        for t in _line_points(int(round(ax)), int(round(ay)), int(round(bx)), int(round(by))):
            prev = direct_tiles.get(t, 0.0)
            if strength > prev:
                direct_tiles[t] = float(strength)

    if not direct_tiles:
        return ActionPreview(action=action)

    indirect_tiles: Dict[TilePos, float] = {}
    for (tx, ty), val in direct_tiles.items():
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                nx, ny = tx + ox, ty + oy
                if (nx, ny) in direct_tiles:
                    continue
                prev = indirect_tiles.get((nx, ny), 0.0)
                if val > prev:
                    indirect_tiles[(nx, ny)] = val

    texts: List[PreviewText] = []
    try:
        level = game._level()
        combined: Dict[str, Any] = {}
        combined.update(getattr(level, "actors", {}) or {})
        combined.update(getattr(level, "entities", {}) or {})

        for obj in combined.values():
            pos = getattr(obj, "pos", None)
            if not pos:
                continue
            tile = (int(pos[0]), int(pos[1]))
            strength = direct_tiles.get(tile)
            indirect_strength = indirect_tiles.get(tile)
            if strength is None and indirect_strength is None:
                continue
            use_strength = strength if strength is not None else indirect_strength
            if use_strength is None:
                continue
            per_tick = (
                base_direct if strength is not None else base_indirect
            ) * (float(use_strength) / 255.0)
            if per_tick <= 0:
                continue
            # Use a compact label: integers as ints; small values as 1dp.
            if per_tick >= 1.0:
                label = f"{sign}{int(per_tick)}"
            else:
                label = f"{sign}{per_tick:.1f}"
            texts.append(PreviewText(pos=tile, text=label, color=label_color))
    except Exception:
        pass

    return ActionPreview(
        action=action,
        direct_tiles=tuple(direct_tiles.keys()),
        indirect_tiles=tuple(indirect_tiles.keys()),
        direct_color=direct_color,
        indirect_color=indirect_color,
        texts=tuple(texts),
    )


def _freeze_condition_label(mult: float) -> str:
    if mult < 1.5:
        return "chilled"
    if mult < 2.0:
        return "frosty"
    if mult < 2.5:
        return "icy"
    if mult < 3.5:
        return "frozen"
    return "deep freeze"


def _preview_freeze(game: Any, pattern: Pattern, anchor: TilePos) -> Optional[ActionPreview]:
    verts_world = project_vertices(pattern, anchor)
    if not verts_world:
        return None

    vcolors = getattr(pattern, "vertex_colors", None) or []

    def blueness(idx: int) -> float:
        try:
            col = vcolors[idx]
        except Exception:
            return 0.0
        if not col or not isinstance(col, (list, tuple)) or len(col) < 3:
            return 0.0
        r, g, b = float(col[0]), float(col[1]), float(col[2])
        return max(0.0, b - max(r, g))

    try:
        dmg_scale = float(getattr(game, "get_param_value")("freeze", "damage_scale") or 0.1)
    except Exception:
        dmg_scale = 0.1
    try:
        slow_scale = float(getattr(game, "get_param_value")("freeze", "slow_scale") or 0.04)
    except Exception:
        slow_scale = 0.04

    tile_blue: Dict[TilePos, float] = {}
    for i, (vx, vy) in enumerate(verts_world):
        blue = blueness(i)
        if blue <= 0:
            continue
        tile = (int(round(vx)), int(round(vy)))
        tile_blue[tile] = tile_blue.get(tile, 0.0) + blue

    if not tile_blue:
        return ActionPreview(action="freeze")

    direct_tiles = tuple(tile_blue.keys())
    direct_color = (120, 170, 255, 120)

    texts: List[PreviewText] = []
    try:
        level = game._level()
        for actor in getattr(level, "actors", {}).values():
            if not getattr(actor, "alive", True):
                continue
            pos = getattr(actor, "pos", None)
            if not pos:
                continue
            tile = (int(pos[0]), int(pos[1]))
            bsum = tile_blue.get(tile, 0.0)
            if bsum <= 0:
                continue
            dmg = int(max(0.0, bsum * dmg_scale))
            mult = 1.0 + bsum * slow_scale
            if mult > 4.0:
                mult = 4.0
            label = _freeze_condition_label(mult)
            # Damage may be 0 at low blueness; still show slow label.
            if dmg > 0:
                text = f"-{dmg} x{mult:.1f} {label}"
            else:
                text = f"x{mult:.1f} {label}"
            texts.append(PreviewText(pos=tile, text=text, color=(180, 220, 255)))
    except Exception:
        pass

    return ActionPreview(
        action="freeze",
        direct_tiles=direct_tiles,
        direct_color=direct_color,
        texts=tuple(texts),
    )


def _preview_energy_kick(_game: Any, pattern: Pattern, anchor: TilePos) -> Optional[ActionPreview]:
    """Preview circles for Energy Kick's per-foot-vertex pulse zones."""

    def _is_foot_lineage(node_id: str) -> bool:
        n = str(node_id or "").lower()
        # Keep matching logic aligned with Game.act_energy_kick.
        return (
            "foot" in n
            or "toe" in n
            or "ankle" in n
            or "heel" in n
        )

    kick_points: List[Tuple[float, float]] = []
    for v in getattr(pattern, "vertices", ()) or ():
        tags = getattr(v, "tags", {}) or {}
        nodes: List[str] = []
        single = str(tags.get("chakra_node", "")).strip()
        if single:
            nodes.append(single)
        many = str(tags.get("chakra_nodes", "")).strip()
        if many:
            nodes.extend([s for s in many.split("|") if s])
        if not nodes:
            continue
        if any(_is_foot_lineage(node_id) for node_id in nodes):
            kick_points.append((float(v.pos[0] + anchor[0]), float(v.pos[1] + anchor[1])))

    if not kick_points:
        return ActionPreview(action="energy_kick")

    # Match the runtime kick radius from Game.act_energy_kick.
    radius = 1.8
    circles = tuple(
        PreviewCircle(
            pos=(px, py),
            radius=radius,
            color=(125, 220, 255, 150),
            pulse="sawtooth",
            period_ms=1500,
        )
        for px, py in kick_points
    )
    return ActionPreview(action="energy_kick", circles=circles)


def _preview_palm_burst(_game: Any, pattern: Pattern, anchor: TilePos) -> Optional[ActionPreview]:
    """Preview circles for Palm Burst's hand/palm/finger pulse zones."""

    def _is_palm_lineage(node_id: str) -> bool:
        n = str(node_id or "").lower()
        return (
            "hand" in n
            or "palm" in n
            or "finger" in n
            or "thumb" in n
            or "index" in n
            or "middle" in n
            or "ring" in n
            or "pinky" in n
            or "knuckle" in n
            or "wrist" in n
        )

    burst_points: List[Tuple[float, float]] = []
    for v in getattr(pattern, "vertices", ()) or ():
        tags = getattr(v, "tags", {}) or {}
        nodes: List[str] = []
        single = str(tags.get("chakra_node", "")).strip()
        if single:
            nodes.append(single)
        many = str(tags.get("chakra_nodes", "")).strip()
        if many:
            nodes.extend([s for s in many.split("|") if s])
        if not nodes:
            continue
        if any(_is_palm_lineage(node_id) for node_id in nodes):
            burst_points.append((float(v.pos[0] + anchor[0]), float(v.pos[1] + anchor[1])))

    if not burst_points:
        return ActionPreview(action="palm_burst")

    # Match runtime radius from Game.act_palm_burst.
    radius = 1.6
    circles = tuple(
        PreviewCircle(
            pos=(px, py),
            radius=radius,
            color=(255, 228, 120, 155),
            pulse="sine",
            period_ms=1000,
        )
        for px, py in burst_points
    )
    return ActionPreview(action="palm_burst", circles=circles)
