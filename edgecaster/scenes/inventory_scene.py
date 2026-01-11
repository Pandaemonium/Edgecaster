from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

import pygame
import math

# Font sizing / glyph sizing limits.
# Big on purpose: user wants proportionality over crispness.
FONT_PX_MIN = 1
FONT_PX_MAX = 10000
# Cap how big we ever rasterize a font glyph.
# Anything bigger than this will be rendered at this size, then scaled up.
FONT_RASTER_PX_MAX = 512

# Quantize font sizes so smooth zoom doesn't generate a new cached font every frame.
FONT_PX_STEP = 4  # 1=exact, 2/4/8 reduces cache churn

def _body_zoom_pan_t(obj: object, zoom_scale: float) -> float:
    """Blend factor for body-graph camera panning.

    Returns 0..1 and is used to blend the camera pivot between the region center
    (0) and the current focus position (1).

    Important: during an active zoom animation we must *reverse* the blend when
    zooming out, so that pan+zoom are the exact inverse of zoom-in (no snap/
    boomerang on offset nodes).
    """
    try:
        # Default when idle: if zoomed in (scale != 1), pivot fully on focus.
        if abs(float(zoom_scale) - 1.0) <= 1e-3:
            idle_pan = 0.0
        else:
            idle_pan = 1.0

        anim = getattr(obj, "_body_zoom_anim", None)
        if anim is None:
            return float(idle_pan)

        # Phase 1.5: camera-state anim no longer encodes focus ids.
        # Keep focus_pos stable (or None) to avoid coupling focus UI to camera animation.
        zoom_focus = getattr(scene, "_body_zoom_focus_nid", None)
        focus_pos = pos_px.get(zoom_focus) if zoom_focus else None
        setattr(scene, "_body_zoom_focus_pos", focus_pos)
        now = int(pygame.time.get_ticks())
        if int(dur_ms) <= 0:
            t = 1.0
        else:
            t = (now - int(start_ms)) / float(dur_ms)
            if t < 0.0:
                t = 0.0
            if t > 1.0:
                t = 1.0

        # smoothstep easing
        t = t * t * (3.0 - 2.0 * t)

        # If we're zooming out (scale decreasing), reverse the pan blend.
        if float(to_s) < float(from_s):
            t = 1.0 - t

        return float(t)
    except Exception:
        return 0.0



def _apply_body_zoom_to_points(
    pos_px: dict[str, tuple[float, float]],
    *,
    region_w: float,
    region_h: float,
    focus_pos: tuple[float, float] | None,
    zoom_scale: float,
    pan_t: float,
) -> dict[str, tuple[float, float]]:
    """Apply the body-graph camera transform to a dict of point positions."""
    if not focus_pos:
        return pos_px

    cx, cy = (float(region_w) * 0.5), (float(region_h) * 0.5)
    fx, fy = float(focus_pos[0]), float(focus_pos[1])

    # Pivot shifts from center -> focus as pan_t goes 0 -> 1.
    px = (cx * (1.0 - float(pan_t))) + (fx * float(pan_t))
    py = (cy * (1.0 - float(pan_t))) + (fy * float(pan_t))

    s = float(zoom_scale)
    return {nid: ((x - px) * s + cx, (y - py) * s + cy) for nid, (x, y) in pos_px.items()}


def _apply_body_zoom_to_point(
    x: float,
    y: float,
    *,
    region_w: float,
    region_h: float,
    focus_pos: tuple[float, float] | None,
    zoom_scale: float,
    pan_t: float,
) -> tuple[float, float]:
    """Apply the body-graph camera transform to a single point."""
    if not focus_pos:
        return (float(x), float(y))

    cx, cy = (float(region_w) * 0.5), (float(region_h) * 0.5)
    fx, fy = float(focus_pos[0]), float(focus_pos[1])

    px = (cx * (1.0 - float(pan_t))) + (fx * float(pan_t))
    py = (cy * (1.0 - float(pan_t))) + (fy * float(pan_t))

    s = float(zoom_scale)
    return ((float(x) - px) * s + cx, (float(y) - py) * s + cy)


from .base import PopupMenuScene
from .urgent_message_scene import UrgentMessageScene
from .quantity_prompt_scene import QuantityPromptScene

from edgecaster.systems.actions import describe_entity_for_look

from edgecaster.visuals import VisualProfile, apply_visual_panel
from edgecaster.visual_effects import (
    effect_names_from_obj,
    concat_effect_names,
    apply_entity_color_effects,
    apply_surface_overlays,
    compute_overlay_union_rect,
    build_visual_profile,
)

from edgecaster.ui.widgets import (
    Widget,
    WidgetContext,
    VBox,
    LabelWidget,
    ScaledLabelWidget,
    ListWidget,
    _wrap_text_px
)

from edgecaster.prototypes import resolve_body_schema

if TYPE_CHECKING:
    from .manager import SceneManager


# ---------------------------------------------------------------------------
# Small helpers: smooth animation
# ---------------------------------------------------------------------------

def _resolve_body_schema_for_zoom_path(owner: object | None, zoom_stack: list[str] | tuple[str, ...]) -> dict:
    """
    Resolve the *currently viewed* body schema, following the scene's zoom path.

    Minimal schema switching step:
      - Start at resolve_body_schema(owner)
      - For each nid in zoom_stack, follow that node's "proto" to the next schema
      - Render ONLY the final schema (no ghost layers / fades yet)

    Fail-soft: if any hop is missing or invalid, stop descending.
    """
    try:
        schema = resolve_body_schema(owner) if owner is not None else {"root": None, "nodes": {}}
    except Exception:
        schema = {"root": None, "nodes": {}}

    zs = list(zoom_stack) if zoom_stack else []
    for nid in zs:
        nid = str(nid)
        nodes = schema.get("nodes", {}) or {}
        node = nodes.get(nid)
        if not isinstance(node, dict):
            break
        proto = node.get("proto")
        if not proto:
            break
        try:
            schema = resolve_body_schema(proto) or {"root": None, "nodes": {}}
        except Exception:
            schema = {"root": None, "nodes": {}}
            break

    if not isinstance(schema, dict):
        schema = {"root": None, "nodes": {}}
    if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
        schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}
    return schema

def _resolve_body_view_for_zoom_path(owner: object | None, zoom_stack: list[str] | tuple[str, ...]) -> tuple[dict, tuple[float, float], float]:
    """Resolve (schema, embed_offset_u, embed_scale) for the current zoom path.

    IMPORTANT invariant:
      - Each schema is defined in its own local coordinates.
      - Descending into a node's sub-schema *embeds* that child's coordinate chart
        at the parent node's local position, scaled by that node's props.size (default 1).

    This function accumulates those embedding transforms so the final schema can be
    rendered in the same absolute world-space as the root schema.
    """
    try:
        schema = resolve_body_schema(owner) if owner is not None else {"root": None, "nodes": {}}
    except Exception:
        schema = {"root": None, "nodes": {}}

    offset_x, offset_y = 0.0, 0.0
    scale = 1.0

    zs = list(zoom_stack) if zoom_stack else []
    for nid in zs:
        nid = str(nid)
        nodes = schema.get("nodes", {}) or {}
        node = nodes.get(nid)
        if not isinstance(node, dict):
            break

        # Local position of the *embedding node* in the current chart.
        layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}
        try:
            nx = float(layout.get("x", 0.0) or 0.0)
            ny = float(layout.get("y", 0.0) or 0.0)
        except Exception:
            nx, ny = 0.0, 0.0

        # Node "size" controls *how far we zoom in* when entering this node.
        # Larger size => deeper zoom => embedded chart is smaller in parent/world units.
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        try:
            size = float(props.get("size", 1.0) or 1.0)
        except Exception:
            size = 1.0
        if size <= 0.0:
            size = 1.0

        nscale = size

        # Update accumulated embedding: child chart origin is at parent node position.
        offset_x += scale * nx
        offset_y += scale * ny
        scale *= nscale

        proto = node.get("proto")
        if not proto:
            break
        try:
            schema = resolve_body_schema(proto) or {"root": None, "nodes": {}}
        except Exception:
            schema = {"root": None, "nodes": {}}
            break

    if not isinstance(schema, dict):
        schema = {"root": None, "nodes": {}}
    if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
        schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}

    return schema, (float(offset_x), float(offset_y)), float(scale)


def _resolve_body_view_chain_for_zoom_path(owner: object | None, zoom_stack: list[str] | tuple[str, ...]) -> list[tuple[dict, tuple[float, float], float]]:
    """Resolve a chain of embedded schemas along the zoom path.

    Returns a list of (schema, embed_offset_u, embed_scale_u) from root -> active.
    Each entry is already embedded into the same world-space chart as the root.

    This is intentionally *render-only* plumbing for Phase 2 (ghost layers).
    Camera fitting and interaction should still be computed from the active schema only.
    """
    try:
        schema = resolve_body_schema(owner) if owner is not None else {"root": None, "nodes": {}}
    except Exception:
        schema = {"root": None, "nodes": {}}

    if not isinstance(schema, dict):
        schema = {"root": None, "nodes": {}}
    if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
        schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}

    offset_x = 0.0
    offset_y = 0.0
    scale = 1.0

    chain: list[tuple[dict, tuple[float, float], float]] = [(schema, (offset_x, offset_y), scale)]

    zs = list(zoom_stack) if zoom_stack else []
    for nid in zs:
        nid = str(nid)
        nodes = schema.get("nodes", {}) or {}
        node = nodes.get(nid)
        if not isinstance(node, dict):
            break

        # Local position of the *embedding node* in the current chart.
        layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}
        try:
            nx = float(layout.get("x", 0.0) or 0.0)
            ny = float(layout.get("y", 0.0) or 0.0)
        except Exception:
            nx, ny = 0.0, 0.0

        # Node "size" controls *how far we zoom in* when entering this node.
        # Larger size => deeper zoom => embedded chart is smaller in parent/world units.
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        try:
            size = float(props.get("size", 1.0) or 1.0)
        except Exception:
            size = 1.0
        if size <= 0.0:
            size = 1.0

        nscale = size

        # Update accumulated embedding: child chart origin is at parent node position.
        offset_x += scale * nx
        offset_y += scale * ny
        scale *= nscale

        proto = node.get("proto")
        if not proto:
            break

        try:
            schema = resolve_body_schema(proto) or {"root": None, "nodes": {}}
        except Exception:
            schema = {"root": None, "nodes": {}}
            break

        if not isinstance(schema, dict):
            schema = {"root": None, "nodes": {}}
        if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
            schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}

        chain.append((schema, (float(offset_x), float(offset_y)), float(scale)))

    return chain


def _embed_positions(pos_local_u: dict[str, tuple[float, float]], offset_u: tuple[float, float], scale: float) -> dict[str, tuple[float, float]]:
    ox, oy = float(offset_u[0]), float(offset_u[1])
    s = float(scale)
    out: dict[str, tuple[float, float]] = {}
    for k, (x, y) in (pos_local_u or {}).items():
        out[str(k)] = (ox + s * float(x), oy + s * float(y))
    return out


def _project_point_with_camera(
    point_u: tuple[float, float],
    region_rect_local: pygame.Rect,
    *,
    center_u: tuple[float, float],
    scale: float,
) -> tuple[float, float]:
    """Project a single world-space point into region-local pixel coordinates."""
    x_u, y_u = float(point_u[0]), float(point_u[1])
    cx_u, cy_u = float(center_u[0]), float(center_u[1])
    px = (x_u - cx_u) * float(scale) + (float(region_rect_local.w) * 0.5)
    py = (y_u - cy_u) * float(scale) + (float(region_rect_local.h) * 0.5)
    return (px, py)





def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _smoothstep(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t

# ---------------------------------------------------------------------------
# Body-plan overlay helpers (read-only for now)
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None

def _display_body_node_label(
    nid: str,
    node_spec: dict | None = None,
    *,
    cur_nodes: dict | None = None,
) -> str:
    """
    UI label for a body node.

    Append '*' ONLY if this node has a meaningful sub-schema (i.e. proto resolves to
    a schema that actually differs from the current schema), not merely because we're
    inside some zoomed subtree.
    """
    has_children = False
    if isinstance(node_spec, dict):
        proto = node_spec.get("proto")
        if proto:
            try:
                sub = resolve_body_schema(proto) or {"root": None, "nodes": {}}
            except Exception:
                sub = {"root": None, "nodes": {}}

            sub_nodes = sub.get("nodes") if isinstance(sub, dict) else None
            meaningful = bool(isinstance(sub_nodes, dict) and len(sub_nodes) > 1)

            # If we know the current schema's nodes, suppress '*' when proto doesn't
            # actually change the node set (common with inherited/alias protos).
            if meaningful and isinstance(cur_nodes, dict) and isinstance(sub_nodes, dict):
                try:
                    meaningful = {str(k) for k in sub_nodes.keys()} != {str(k) for k in cur_nodes.keys()}
                except Exception:
                    pass

            has_children = bool(meaningful)

    s = str(nid)
    is_mirrored = s.endswith("_m")
    base_id = s[:-2] if is_mirrored else s

    # Prefer name from the node spec (if the schema provides it).
    label = None
    if isinstance(node_spec, dict):
        for k in ("name", "Name", "display_name", "label"):
            v = node_spec.get(k)
            if isinstance(v, str) and v.strip():
                label = v.strip()
                break

    # Fallback: prettify the id
    if not label:
        label = base_id.replace("_", " ")

    if has_children:
        label = f"{label}*"

    if is_mirrored:
        if label:
            label = label[0].lower() + label[1:]
        return f"mirrored {label}"

    return label





def _node_layout_xy(node_spec: dict) -> Optional[tuple[float, float]]:
    if not isinstance(node_spec, dict):
        return None
    layout = node_spec.get("layout")
    if not isinstance(layout, dict):
        return None
    x = _safe_float(layout.get("x"))
    y = _safe_float(layout.get("y"))
    if x is None or y is None:
        return None
    return (x, y)


def _children_of(node_spec: dict) -> list[str]:
    if not isinstance(node_spec, dict):
        return []
    ch = node_spec.get("children") or []
    if isinstance(ch, list):
        out: list[str] = []
        for c in ch:
            if c is None:
                continue
            out.append(str(c))
        return out
    return []


def _default_offsets() -> list[tuple[int, int]]:
    # "Convenient" placements around parent; expands outward.
    return [
        (-1, 0), (1, 0), (0, -1), (0, 1),
        (-1, -1), (1, -1), (-1, 1), (1, 1),
        (-2, 0), (2, 0), (0, -2), (0, 2),
        (-2, -1), (-2, 1), (2, -1), (2, 1),
        (-1, -2), (1, -2), (-1, 2), (1, 2),
        (-2, -2), (2, -2), (-2, 2), (2, 2),
    ]


def _compute_body_positions(schema: dict) -> dict[str, tuple[float, float]]:
    """
    Returns node_id -> (x, y) in abstract layout units.
    Uses YAML coords when present; otherwise assigns positions near parent.
    """
    if not isinstance(schema, dict):
        return {}
    nodes = schema.get("nodes") or {}
    if not isinstance(nodes, dict):
        return {}
    root = schema.get("root")
    root_id = str(root) if root is not None else None

    # 1) Start with explicit coords where provided.
    pos: dict[str, tuple[float, float]] = {}
    for nid, spec in nodes.items():
        nid_s = str(nid)
        xy = _node_layout_xy(spec if isinstance(spec, dict) else {})
        if xy is not None:
            pos[nid_s] = xy

    # 2) Ensure root exists; if no explicit position, place at origin.
    if root_id and root_id in nodes and root_id not in pos:
        pos[root_id] = (0.0, 0.0)

    # If schema has no root, pick a stable "first" node.
    if root_id is None:
        for nid in nodes.keys():
            root_id = str(nid)
            break
        if root_id is not None and root_id not in pos:
            pos[root_id] = (0.0, 0.0)

    if root_id is None:
        return pos

    # Occupancy set for integer-ish collision checks.
    occupied: set[tuple[int, int]] = set()
    for p in pos.values():
        occupied.add((int(round(p[0])), int(round(p[1]))))

    # 3) BFS assign missing nodes relative to parent.
    from collections import deque
    q = deque([root_id])
    seen: set[str] = set()

    offsets = _default_offsets()

    while q:
        cur = q.popleft()
        if cur in seen:
            continue
        seen.add(cur)

        cur_spec = nodes.get(cur) if isinstance(nodes.get(cur), dict) else {}
        cur_pos = pos.get(cur)
        if cur_pos is None:
            # If parent didn't get a position somehow, pin it.
            cur_pos = (0.0, 0.0)
            pos[cur] = cur_pos
            occupied.add((0, 0))

        children = _children_of(cur_spec)
        for idx, ch in enumerate(children):
            if ch not in nodes:
                continue
            if ch not in pos:
                # Propose an offset near parent, avoiding collisions.
                base_x, base_y = cur_pos
                placed = None
                for j, (ox, oy) in enumerate(offsets):
                    # Rotate starting offset based on child index for variety.
                    k = (idx + j) % len(offsets)
                    ox2, oy2 = offsets[k]
                    tx = int(round(base_x + ox2))
                    ty = int(round(base_y + oy2))
                    if (tx, ty) not in occupied:
                        placed = (float(tx), float(ty))
                        occupied.add((tx, ty))
                        break
                if placed is None:
                    # Worst-case: just shove it somewhere far.
                    tx = int(round(base_x)) + 3 + idx
                    ty = int(round(base_y)) + 3
                    placed = (float(tx), float(ty))
                    occupied.add((tx, ty))
                pos[ch] = placed
            q.append(ch)

    # 4) Any orphan nodes not reached from root: sprinkle them.
    if nodes:
        i = 0
        for nid in nodes.keys():
            nid = str(nid)
            if nid in pos:
                continue
            tx = 3 + (i % 6)
            ty = -3 - (i // 6)
            while (tx, ty) in occupied:
                tx += 1
            pos[nid] = (float(tx), float(ty))
            occupied.add((tx, ty))
            i += 1

    return pos


def _map_positions_to_rect(
    positions: dict[str, tuple[float, float]],
    target_rect: pygame.Rect,
    *,
    margin_frac: float = 0.12,
) -> tuple[dict[str, tuple[int, int]], float]:
    """
    Map abstract (x,y) positions into pixel coords in target_rect.
    Returns (node_id -> (px, py), scale_px_per_unit).
    """
    if not positions:
        return {}, 1.0

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    # Avoid zero spans
    spanx = max(1e-6, (maxx - minx))
    spany = max(1e-6, (maxy - miny))

    m = int(min(target_rect.w, target_rect.h) * float(margin_frac))
    inner = target_rect.inflate(-2 * m, -2 * m)
    if inner.w <= 1 or inner.h <= 1:
        inner = target_rect.copy()

    # scale so it "mostly fills"
    sx = inner.w / spanx
    sy = inner.h / spany
    scale = float(min(sx, sy))

    # center in inner rect
    cx_u = (minx + maxx) * 0.5
    cy_u = (miny + maxy) * 0.5
    cx_px = inner.centerx
    cy_px = inner.centery

    out: dict[str, tuple[int, int]] = {}
    for nid, (x, y) in positions.items():
        px = int(round(cx_px + (x - cx_u) * scale))
        py = int(round(cy_px + (y - cy_u) * scale))
        out[nid] = (px, py)

    return out, scale


def _fit_camera_to_positions(
    positions: dict[str, tuple[float, float]],
    target_rect: pygame.Rect,
    *,
    margin_frac: float = 0.12,
    anchor_u: tuple[float, float] | None = None,
) -> tuple[tuple[float, float], float]:
    """
    Fit a camera to a set of world-space (x,y) positions.

    Returns:
      (center_u_x, center_u_y), scale_px_per_unit

    If anchor_u is provided, the camera is centered on anchor_u (NOT bbox center),
    and the scale is chosen to fit all points relative to that anchor. This keeps
    LoD0 stable even when anatomy becomes asymmetric (missing limbs, etc.).
    """
    if not positions:
        return (0.0, 0.0), 1.0

    xs = [float(p[0]) for p in positions.values()]
    ys = [float(p[1]) for p in positions.values()]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)

    m = int(min(target_rect.w, target_rect.h) * float(margin_frac))
    inner = target_rect.inflate(-2 * m, -2 * m)
    if inner.w <= 1 or inner.h <= 1:
        inner = target_rect.copy()

    # Choose center.
    if anchor_u is not None:
        cx_u, cy_u = float(anchor_u[0]), float(anchor_u[1])

        # Fit extents around the anchor so all points remain visible.
        max_dx = 0.0
        max_dy = 0.0
        for (x, y) in positions.values():
            dx = abs(float(x) - cx_u)
            dy = abs(float(y) - cy_u)
            if dx > max_dx:
                max_dx = dx
            if dy > max_dy:
                max_dy = dy

        # Avoid zero spans. (Use symmetric spans around the anchor.)
        spanx = max(1e-6, 2.0 * max_dx)
        spany = max(1e-6, 2.0 * max_dy)
    else:
        cx_u = (minx + maxx) * 0.5
        cy_u = (miny + maxy) * 0.5

        # Avoid zero spans (bbox-based).
        spanx = max(1e-6, (maxx - minx))
        spany = max(1e-6, (maxy - miny))

    # scale so it "mostly fills"
    sx = inner.w / spanx
    sy = inner.h / spany
    scale = float(min(sx, sy))

    return (float(cx_u), float(cy_u)), float(scale)



def _project_positions_with_camera(
    positions_u: dict[str, tuple[float, float]],
    target_rect: pygame.Rect,
    *,
    center_u: tuple[float, float],
    scale: float,
) -> dict[str, tuple[int, int]]:
    """
    Project world-space node positions into pixel coords inside target_rect,
    using a camera defined by (center_u, scale).

    The returned pixels are in the same coordinate space as target_rect
    (so if target_rect is (0,0,w,h), pixels are panel-local).
    """
    if not positions_u:
        return {}

    cx_u, cy_u = float(center_u[0]), float(center_u[1])
    cx_px, cy_px = int(target_rect.centerx), int(target_rect.centery)

    out: dict[str, tuple[int, int]] = {}
    for nid, (x, y) in positions_u.items():
        px = int(round(cx_px + (float(x) - cx_u) * float(scale)))
        py = int(round(cy_px + (float(y) - cy_u) * float(scale)))
        out[str(nid)] = (px, py)
    return out

def _get_schema_anchor_u(schema: dict, positions_u: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """
    Pick a stable anchor point for camera centering (in the SAME coord space as positions_u).
    Prefer schema['root'] if present; otherwise fall back to first node; else (0,0).
    """
    try:
        root = schema.get("root") if isinstance(schema, dict) else None
        root_id: str | None = str(root) if root is not None else None

        if root_id is None:
            nodes = schema.get("nodes") if isinstance(schema, dict) else None
            if isinstance(nodes, dict) and nodes:
                root_id = str(next(iter(nodes.keys())))

        if root_id and root_id in positions_u:
            x, y = positions_u[root_id]
            return (float(x), float(y))
    except Exception:
        pass
    return (0.0, 0.0)



def _compute_body_graph_base_camera(
    scene: object,
    positions_u: dict[str, tuple[float, float]],
    region_rect_local: pygame.Rect,
    *,
    margin_frac: float = 0.12,
    anchor_u: tuple[float, float] | None = None,
) -> tuple[tuple[float, float], float]:
    """
    Compute the *non-animated* body-graph camera.

    LoD 0 is treated as a presentation/composition view:
      - center is anchored (schema root if available)
      - scale is fixed (NOT fit-to-bounds), so YAML coordinate tweaks actually matter.

    LoD >= 1 remains fit-to-bounds for inspection.
    """
    stack_depth = len(getattr(scene, "_body_zoom_stack", []) or [])

    # LoD 0: fixed scale camera so skeleton can be composed against the glyph/sprite.
    if stack_depth == 0:
        cx_u, cy_u = (anchor_u if anchor_u is not None else (0.0, 0.0))

        # Allow easy tuning from the scene if needed.
        # Interpretation: the camera will fit a square of size `unit_span_u` (in world units)
        # into the available rect (minus margins).
        unit_span_u = float(getattr(scene, "_body_lod0_unit_span_u", 2.0))

        # Mirror the margin behavior of _fit_camera_to_positions.
        inner = region_rect_local.copy()
        if inner.w > 0 and inner.h > 0:
            mx = int(inner.w * float(margin_frac))
            my = int(inner.h * float(margin_frac))
            inner = inner.inflate(-2 * mx, -2 * my)
            if inner.w <= 0 or inner.h <= 0:
                inner = region_rect_local.copy()

        denom = max(1e-6, unit_span_u)
        scale = float(min(inner.w, inner.h) / denom) if inner.w > 0 and inner.h > 0 else 1.0
        return (float(cx_u), float(cy_u)), float(scale)

    # LoD >= 1: inspection mode (fit-to-bounds).
    return _fit_camera_to_positions(
        positions_u, region_rect_local, margin_frac=margin_frac, anchor_u=anchor_u
    )


def _compute_body_graph_camera(
    scene: object,
    positions_u: dict[str, tuple[float, float]],
    region_rect_local: pygame.Rect,
    *,
    margin_frac: float = 0.12,
    anchor_u: tuple[float, float] | None = None,
) -> tuple[tuple[float, float], float]:
    """
    The single source of truth for the body-graph camera.

    Returns:
      center_u, scale_px_per_unit

    - Base camera is a fit-to-positions camera (computed from active schema only).
    - Optional Phase 1.5 animation interpolates BOTH center and scale together,
      between two fully-defined camera states captured at the zoom event.
    """
    base_center_u, base_scale = _compute_body_graph_base_camera(
        scene, positions_u, region_rect_local, margin_frac=margin_frac, anchor_u=anchor_u
    )

    anim = getattr(scene, "_body_zoom_anim", None)
    if anim is None:
        return base_center_u, float(base_scale)

    try:
        from_center_u, from_scale, to_center_u, to_scale, start_ms, dur_ms = anim
        now = int(pygame.time.get_ticks())

        if dur_ms <= 0:
            t = 1.0
        else:
            t = (now - int(start_ms)) / float(dur_ms)
            if t < 0.0:
                t = 0.0
            if t > 1.0:
                t = 1.0

        # Smoothstep
        t = t * t * (3.0 - 2.0 * t)

        cx = float(from_center_u[0]) * (1.0 - t) + float(to_center_u[0]) * t
        cy = float(from_center_u[1]) * (1.0 - t) + float(to_center_u[1]) * t
        sc = float(from_scale) * (1.0 - t) + float(to_scale) * t

        # When done, snap-clean to target and clear the anim.
        if t >= 0.999:
            try:
                setattr(scene, "_body_zoom_anim", None)
                try:
                    setattr(scene, "_body_zoom_fade", None)
                except Exception:
                    pass
            except Exception:
                pass
            return (float(to_center_u[0]), float(to_center_u[1])), float(to_scale)

        return (cx, cy), float(sc)

    except Exception:
        # Fail-soft to base camera
        try:
            setattr(scene, "_body_zoom_anim", None)
            try:
                setattr(scene, "_body_zoom_fade", None)
            except Exception:
                pass
        except Exception:
            pass
        return base_center_u, float(base_scale)




def _render_entity_glyph_canvas(
    renderer,
    ent: Any,
    *,
    font: pygame.font.Font,
    base_px: int,
    scene_effects: list[str] | None = None,
) -> pygame.Surface:
    """
    Render a single entity glyph into an RGBA canvas.

    Key policy:
      - base_px is the desired *display* size.
      - we only rasterize up to FONT_RASTER_PX_MAX, then scale the surface up.
    """
    want_px = int(max(1, base_px))
    raster_px = int(max(1, min(want_px, FONT_RASTER_PX_MAX)))
    scale_up = float(want_px) / float(raster_px) if raster_px > 0 else 1.0

    # Prefer renderer-provided icon/sprite rendering if available.
    # IMPORTANT: still honor raster-capping by requesting a raster-sized surface,
    # then scaling up ourselves (pixelization is fine).
    if hasattr(renderer, "get_entity_icon_surface"):
        try:
            rsurf = renderer.get_entity_icon_surface(
                ent,
                size_px=int(raster_px),  # raster size, NOT want_px
                scene_effects=scene_effects or [],
            )
            if rsurf is not None and scale_up != 1.0:
                w2 = max(1, int(round(rsurf.get_width() * scale_up)))
                h2 = max(1, int(round(rsurf.get_height() * scale_up)))
                rsurf = pygame.transform.scale(rsurf, (w2, h2))
            return rsurf
        except Exception:
            pass


    glyph = str(getattr(ent, "glyph", "@"))[:1]

    base_color = getattr(renderer, "fg", (240, 240, 255))
    if hasattr(renderer, "_entity_visual"):
        try:
            _, base_color = renderer._entity_visual(ent)  # type: ignore[attr-defined]
        except Exception:
            pass

    eff = concat_effect_names(scene_effects or [], effect_names_from_obj(ent))
    color = apply_entity_color_effects(ent, base_color, eff)

    # IMPORTANT: all overlay rects etc. are computed in raster space.
    base_rect = pygame.Rect(0, 0, raster_px, raster_px)
    union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)

    canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
    ox, oy = -union_rect.left, -union_rect.top

    # Render glyph at raster size (font itself is already raster-capped via _get_font()).
    gsurf = font.render(glyph, True, color)
    gx = ox + (raster_px - gsurf.get_width()) // 2
    gy = oy + (raster_px - gsurf.get_height()) // 2
    canvas.blit(gsurf, (gx, gy))

    if eff:
        shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
        apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

    # Some effects include geometry transforms; apply them to the glyph canvas only.
    if eff:
        try:
            visual = build_visual_profile(VisualProfile(), eff)
            out = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
            apply_visual_panel(out, canvas, out.get_rect(), visual)
            canvas = out
        except Exception:
            pass

    # Scale up to desired display size (pixelly is fine; use scale not smoothscale).
    if scale_up != 1.0:
        try:
            w2 = max(1, int(round(canvas.get_width() * scale_up)))
            h2 = max(1, int(round(canvas.get_height() * scale_up)))
            canvas = pygame.transform.scale(canvas, (w2, h2))
        except Exception:
            pass

    return canvas



def _render_entity_glyph_canvas_with_anchor(
    renderer,
    ent: Any,
    *,
    font: pygame.font.Font,
    base_px: int,
    scene_effects: list[str] | None = None,
) -> tuple[pygame.Surface, tuple[float, float]]:
    """
    Like _render_entity_glyph_canvas(), but also returns the pixel coordinate of the
    *glyph cell center* inside the returned surface.

    Policy:
      - base_px is desired *display* size.
      - rasterize up to FONT_RASTER_PX_MAX, then scale surface + anchor.
    """
    want_px = int(max(1, base_px))
    raster_px = int(max(1, min(want_px, FONT_RASTER_PX_MAX)))
    scale_up = float(want_px) / float(raster_px) if raster_px > 0 else 1.0

    if hasattr(renderer, "get_entity_icon_surface"):
        try:
            rsurf = renderer.get_entity_icon_surface(
                ent,
                size_px=int(raster_px),
                scene_effects=scene_effects or [],
            )
            if rsurf is None:
                raise RuntimeError("renderer returned None")

            # Best-effort anchor: assume glyph cell center is surface center.
            anchor = (rsurf.get_width() * 0.5, rsurf.get_height() * 0.5)

            if scale_up != 1.0:
                w2 = max(1, int(round(rsurf.get_width() * scale_up)))
                h2 = max(1, int(round(rsurf.get_height() * scale_up)))
                rsurf = pygame.transform.scale(rsurf, (w2, h2))
                anchor = (anchor[0] * scale_up, anchor[1] * scale_up)

            return rsurf, anchor
        except Exception:
            pass


    glyph = str(getattr(ent, "glyph", "@"))[:1]

    base_color = getattr(renderer, "fg", (240, 240, 255))
    if hasattr(renderer, "_entity_visual"):
        try:
            _, base_color = renderer._entity_visual(ent)  # type: ignore[attr-defined]
        except Exception:
            pass

    eff = concat_effect_names(scene_effects or [], effect_names_from_obj(ent))
    color = apply_entity_color_effects(ent, base_color, eff)

    base_rect = pygame.Rect(0, 0, raster_px, raster_px)
    union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)

    canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
    ox, oy = -union_rect.left, -union_rect.top

    gsurf = font.render(glyph, True, color)
    gx = ox + (raster_px - gsurf.get_width()) // 2
    gy = oy + (raster_px - gsurf.get_height()) // 2
    canvas.blit(gsurf, (gx, gy))

    if eff:
        shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
        apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

    if eff:
        try:
            visual = build_visual_profile(VisualProfile(), eff)
            out = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
            apply_visual_panel(out, canvas, out.get_rect(), visual)
            canvas = out
        except Exception:
            pass

    # Anchor = glyph cell center in *canvas* coordinates (raster space)
    anchor = (float(ox) + float(raster_px) * 0.5, float(oy) + float(raster_px) * 0.5)

    # Scale up surface + anchor together
    if scale_up != 1.0:
        try:
            w2 = max(1, int(round(canvas.get_width() * scale_up)))
            h2 = max(1, int(round(canvas.get_height() * scale_up)))
            canvas = pygame.transform.scale(canvas, (w2, h2))
            anchor = (float(anchor[0]) * scale_up, float(anchor[1]) * scale_up)
        except Exception:
            pass

    return canvas, anchor


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class InventoryListWidget(ListWidget):
    """ListWidget that draws entity glyphs with per-entity color/effects.

    Extended: supports click/hold-to-drag rows into container inventories.
    - Quick click/release: activates as usual.
    - Click + hold (or small drag threshold): begins a drag, shows a ghost label, and
      supports dropping onto container rows (tags['container']).
    """

    # Drag gesture tuning (panel-local coords)
    DRAG_HOLD_MS: int = 220
    DRAG_MIN_PX: int = 6

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._press_idx: int | None = None
        self._press_pos: tuple[int, int] | None = None
        self._press_ms: int = 0
        self._dragging: bool = False

        # Double-click tracking (for folder-like 'Open' behavior)
        self._last_click_ms: int = 0
        self._last_click_idx: int | None = None
        self.DOUBLE_CLICK_MS: int = 330

    def pick_index_at(self, pos: tuple[int, int] | None) -> int | None:
        """Return the item index under pos (panel-local), or None."""
        if pos is None:
            return None
        if not self.rect.collidepoint(pos):
            return None
        _x, y = pos
        y0 = self.rect.y + self.padding
        rel_y = y - y0
        if rel_y < 0:
            return None
        idx_in_view = int(rel_y // max(1, int(self._line_height)))
        idx = int(self.scroll_offset) + idx_in_view
        if 0 <= idx < len(self.items):
            return idx
        return None

    def _begin_drag_if_ready(self, ctx: WidgetContext, pos: tuple[int, int]) -> bool:
        if self._press_idx is None or self._dragging:
            return False

        now = pygame.time.get_ticks()
        held = (now - int(self._press_ms)) >= int(self.DRAG_HOLD_MS)

        moved = False
        if self._press_pos is not None:
            dx = int(pos[0]) - int(self._press_pos[0])
            dy = int(pos[1]) - int(self._press_pos[1])
            moved = (dx * dx + dy * dy) >= int(self.DRAG_MIN_PX * self.DRAG_MIN_PX)

        if not (held or moved):
            return False

        row = self.items[self._press_idx]
        ent = getattr(row, "ent", None)
        if ent is None:
            return False

        scene = getattr(ctx, "scene", None)
        if scene is None:
            return False

        cb = getattr(scene, "_inv_drag_begin", None)
        if callable(cb):
            try:
                if bool(cb(row=row, pos=pos)):
                    self._dragging = True
                    return True
            except Exception:
                return False

        return False

    def _cancel_press(self) -> None:
        self._press_idx = None
        self._press_pos = None
        self._press_ms = 0
        self._dragging = False








    def handle_event(self, event, ctx: WidgetContext) -> bool:
        # If we're currently dragging, eat mouse events and forward to scene.
        if event.type == pygame.MOUSEMOTION and hasattr(event, "pos"):
            pos = event.pos
            if self._press_idx is not None:
                # allow drag start on motion (if held enough / moved enough)
                if self._begin_drag_if_ready(ctx, pos):
                    cb = getattr(getattr(ctx, "scene", None), "_inv_drag_update", None)
                    if callable(cb):
                        try:
                            cb(pos=pos)
                        except Exception:
                            pass
                    return True

                if self._dragging:
                    cb = getattr(getattr(ctx, "scene", None), "_inv_drag_update", None)
                    if callable(cb):
                        try:
                            cb(pos=pos)
                        except Exception:
                            pass
                    return True

            # Normal hover updates (let base class update selection + hover)
            return super().handle_event(event, ctx)

        if event.type == pygame.MOUSEBUTTONDOWN and getattr(event, "button", None) == 1:
            idx = self.pick_index_at(getattr(event, "pos", None))
            if idx is None:
                return super().handle_event(event, ctx)

            # Select immediately, but delay activation until mouse-up (unless drag)
            self.selected_index = idx
            self.ensure_visible(self.selected_index)
            self._press_idx = idx
            self._press_pos = getattr(event, "pos", None)
            self._press_ms = pygame.time.get_ticks()
            self._dragging = False
            return True

        if event.type == pygame.MOUSEBUTTONUP and getattr(event, "button", None) == 1:
            if self._press_idx is None:
                return super().handle_event(event, ctx)

            scene = getattr(ctx, "scene", None)

            if self._dragging:
                cb = getattr(scene, "_inv_drag_end", None) if scene is not None else None
                if callable(cb):
                    try:
                        cb(pos=getattr(event, "pos", None))
                    except Exception:
                        pass

                # IMPORTANT: let the base widget logic see the button-up so it can
                # release any internal mouse-capture/pressed state; otherwise hover can “freeze”
                # until the next click.
                try:
                    super().handle_event(event, ctx)
                except Exception:
                    pass

                self._cancel_press()
                return True


            # Not dragging: treat as a click-activate *if* we release on the same row.
            release_idx = self.pick_index_at(getattr(event, "pos", None))
            press_idx = self._press_idx
            self._cancel_press()

            if release_idx is not None and release_idx == press_idx and 0 <= release_idx < len(self.items):
                # Contextual double-click handling:
                # - For most rows (Back / Empty / non-containers): activate immediately (snappy UX).
                # - For containers only: delay the single-click activation briefly so a second click
                #   can be interpreted as "Open" without flashing the action menu.
                row0 = self.items[release_idx]
                ent0 = getattr(row0, "ent", None)
                tags0 = getattr(ent0, "tags", {}) or {} if ent0 is not None else {}
                can_double_open = bool(ent0 is not None and tags0.get("container"))

                if not can_double_open:
                    try:
                        if callable(getattr(self, "on_activate", None)):
                            self.on_activate(release_idx, self.items[release_idx])
                            return True
                    except Exception:
                        return True
                    return True

                # Container row: check for a double click.
                now = pygame.time.get_ticks()
                is_double = (
                    self._last_click_idx == release_idx
                    and (now - int(self._last_click_ms)) <= int(self.DOUBLE_CLICK_MS)
                )
                self._last_click_ms = int(now)
                self._last_click_idx = int(release_idx)

                scene2 = getattr(ctx, "scene", None)

                if is_double:
                    # Open directly (skip action menu), and cancel any pending delayed activation.
                    if scene2 is not None:
                        setattr(scene2, "_pending_click_activate_index", None)
                        setattr(scene2, "_pending_click_activate_due_ms", 0)
                        setattr(scene2, "_pending_double_open_index", int(release_idx))
                    return True

                # Single click on a container: schedule delayed activation (action menu) after the
                # double-click window. If a second click arrives, the widget will cancel this.
                if scene2 is not None:
                    setattr(scene2, "_pending_click_activate_index", int(release_idx))
                    setattr(scene2, "_pending_click_activate_due_ms", int(now) + int(self.DOUBLE_CLICK_MS))
                    return True

                # Fallback: behave like immediate activate.
                try:
                    if callable(getattr(self, "on_activate", None)):
                        self.on_activate(release_idx, self.items[release_idx])
                        return True
                except Exception:
                    return True

            return True

        # Keyboard / mousewheel etc: fall back to base behavior
        return super().handle_event(event, ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self._pick_font(ctx)
        fg = getattr(ctx.renderer, "fg", (255, 255, 255))
        sel = getattr(
            ctx.renderer,
            "player_color",
            getattr(ctx.renderer, "sel", (255, 255, 0)),
        )

        cap = self._visible_capacity()
        start = self.scroll_offset
        end = min(len(self.items), start + cap)

        x0 = self.rect.x + self.padding
        y = self.rect.y + self.padding

        scene = ctx.scene
        scene_effects = list(getattr(scene, "visual_effects", []) or [])

        base_px = max(14, int(font.get_height() * 1.15))

        # Drag highlight: during click-and-drag, softly highlight both the dragged
        # source item and the current container target (if any).
        drag_active = bool(getattr(scene, "_drag_active", False))
        dragged_ent = getattr(scene, "_drag_ent", None) if drag_active else None
        dragged_id = getattr(dragged_ent, "id", None) if dragged_ent is not None else None
        target_owner_id = getattr(scene, "_drag_target_owner_id", None) if drag_active else None

        def _half_mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float = 0.7) -> tuple[int, int, int]:
            # t=0 → a (normal), t=1 → b (full yellow)
            def ch(i: int) -> int:
                v = int(a[i] + (b[i] - a[i]) * t)
                return 0 if v < 0 else 255 if v > 255 else v
            return (ch(0), ch(1), ch(2))


        half_sel = _half_mix(tuple(fg[:3]), tuple(sel[:3]))

        for idx in range(start, end):
            row = self.items[idx]
            ent = getattr(row, "ent", None)
            selected = (idx == self.selected_index)

            # During a drag, show a 'half-selected' highlight for the dragged
            # item and the current drop target to make the pairing clearer.
            ent_id = getattr(ent, "id", None) if ent is not None else None
            is_drag_source = drag_active and (dragged_id is not None) and (ent_id == dragged_id)
            is_drag_target = drag_active and (target_owner_id is not None) and (ent_id is not None) and (str(ent_id) == str(target_owner_id))
            drag_mark = bool(is_drag_source or is_drag_target)

            prefix = "▶ " if selected else "  "
            prefix_col = half_sel if drag_mark else (sel if selected else fg)
            prefix_surf = font.render(prefix, True, prefix_col)
            ctx.surface.blit(prefix_surf, (x0, y))

            x = x0 + prefix_surf.get_width()

            if ent is not None:
                glyph_canvas, _glyph_anchor = _render_entity_glyph_canvas_with_anchor(
                    ctx.renderer,
                    ent,
                    font=font,
                    base_px=base_px,
                    scene_effects=scene_effects,
                )
                ctx.surface.blit(
                    glyph_canvas,
                    (x, y - (glyph_canvas.get_height() - font.get_height()) // 2),
                )
                x += glyph_canvas.get_width() + int(font.size("  ")[0] * 0.5)

                name = getattr(ent, "name", None) or "(unnamed item)"
                # Show remaining charges for charged items (e.g. wands).
                tags = getattr(ent, "tags", None) or {}
                if "charges" in tags:
                    try:
                        cur = int(tags.get("charges", 0))
                    except Exception:
                        cur = 0
                    raw_max = tags.get("max_charges")
                    if raw_max is None:
                        raw_max = tags.get("charges_max")
                    try:
                        maxc = int(raw_max) if raw_max is not None else None
                    except Exception:
                        maxc = None
                    if maxc is not None:
                        name = f"{name} ({cur}/{maxc} charges)"
                    else:
                        name = f"{name} ({cur} charges)"
                else:
                    # Show quantity for stacked items (if not showing charges)
                    from edgecaster.systems.inventory import get_quantity
                    qty = get_quantity(ent)
                    if qty > 1:
                        name = f"{name} ({qty})"
                name_col = half_sel if drag_mark else (sel if selected else fg)
                name_surf = font.render(str(name), True, name_col)
                ctx.surface.blit(name_surf, (x, y))
            else:
                label = getattr(row, "label", str(row))
                label_col = half_sel if drag_mark else (sel if selected else fg)
                surf = font.render(label, True, label_col)
                ctx.surface.blit(surf, (x, y))

            y += self._line_height

        Widget.draw(self, ctx)


class EntityPreviewWidget(Widget):
    """
    Right pane: magnified glyph preview.

    Diagrammatic zoom happens at the scene level (VisualProfile anchored on this
    pane's center), so the preview itself should be stable.

    During the zoom, the panel fades in but the glyph should remain opaque;
    InventoryScene re-draws the glyph as an opaque overlay layer, so this widget
    can skip drawing the glyph to avoid double brightening.
    """

    def __init__(self) -> None:
        super().__init__()
        self._font_cache: dict[int, pygame.font.Font] = {}

    def _get_font(self, size: int) -> pygame.font.Font:
        # Quantize so smooth zoom doesn't generate a new cached font every frame.
        s = int(round(float(size) / float(FONT_PX_STEP))) * int(FONT_PX_STEP)

        # IMPORTANT: "size" here is the *desired display size*, but we only rasterize up to FONT_RASTER_PX_MAX.
        s = int(max(FONT_PX_MIN, min(FONT_RASTER_PX_MAX, s)))

        f = self._font_cache.get(s)
        if f is None:
            f = pygame.font.SysFont("consolas", s, bold=True)
            self._font_cache[s] = f
        return f



    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible or self.rect.width <= 0 or self.rect.height <= 0:
            return

        scene = ctx.scene
        renderer = ctx.renderer
        surf = ctx.surface

        owner = getattr(scene, "_preview_entity", None)
        owner = owner() if callable(owner) else getattr(scene, "_find_owner_entity", lambda: None)()
        info = describe_entity_for_look(owner) if owner is not None else {}
        name = info.get("name") or getattr(owner, "name", None) or getattr(scene, "explicit_title", None) or "Entity"
        glyph = str(info.get("glyph") or getattr(owner, "glyph", "@"))[:1]

        r = self.rect
        card = pygame.Surface((r.w, r.h), pygame.SRCALPHA)

        # Clear any previous frame's body overlay (we'll set it again if we draw one).
        try:
            setattr(scene, "_body_overlay_panel_surface", None)
        except Exception:
            pass

        bg = getattr(renderer, "bg", (10, 10, 20))
        fg = getattr(renderer, "fg", (240, 240, 255))

        fill = (min(255, bg[0] + 12), min(255, bg[1] + 12), min(255, bg[2] + 18), 235)
        card.fill(fill)
        pygame.draw.rect(card, (*fg, 120), card.get_rect(), 2, border_radius=10)

        title_font = getattr(renderer, "menu_title_font", None)
        if title_font is None:
            title_font = getattr(renderer, "menu_font", None)
        if title_font is None:
            title_font = pygame.font.SysFont("consolas", 22, bold=True)

        # Title: entity name (no \"inhabiting\" label).
        ts = title_font.render(str(name), True, fg)
        card.blit(ts, (14, 12))

        # Glyph region matches BodyPlanGraphWidget's reserved header/footer space.
        desc = info.get("description") or getattr(owner, "description", None)
        top_reserved = 70
        bottom_reserved = 80 if desc else 56
        region_w = max(1, int(r.w) - 28)
        region_h = max(1, int(r.h) - int(top_reserved) - int(bottom_reserved))

        # Local center within the reserved glyph region.
        cx_local = float(region_w) * 0.5
        cy_local = float(region_h) * 0.5

        # Phase 5: body-graph "camera zoom" should pan/scale the background glyph rigidly with the skeleton.
        try:
            zoom_scale = float(getattr(scene, "_body_zoom_scale", 1.0) or 1.0)
            focus_pos = getattr(scene, "_body_zoom_focus_pos", None)
            pan_t = _body_zoom_pan_t(scene, zoom_scale)
            cx_local, cy_local = _apply_body_zoom_to_point(
                cx_local,
                cy_local,
                region_w=float(region_w),
                region_h=float(region_h),
                focus_pos=focus_pos,
                zoom_scale=zoom_scale,
                pan_t=pan_t,
            )
        except Exception:
            zoom_scale = 1.0

        # Only draw the glyph here if the scene is NOT doing the external opaque overlay.
        #
        # Phase 5: when the body-graph camera zooms, the background glyph should zoom/pan
        # in the SAME way as the node skeleton (and stay clipped inside this pane).
        if not bool(getattr(scene, "_external_opaque_glyph", False)):
            # Match BodyPlanGraphWidget's overlay region so the glyph and nodes share a space.
            top_reserved = 70
            bottom_reserved = 80 if info.get("description") or getattr(owner, "description", None) else 56
            region_w = max(1, int(r.w) - 28)
            region_h = max(1, int(r.h) - int(top_reserved) - int(bottom_reserved))
            region = pygame.Rect(14, top_reserved, region_w, region_h)

            # Resolve the currently viewed schema and its embedding transform in absolute world-space.
            try:
                schema, embed_off_u, embed_scale_u = _resolve_body_view_for_zoom_path(
                    owner, getattr(scene, "_body_zoom_stack", [])
                )
            except Exception:
                schema, embed_off_u, embed_scale_u = {"root": None, "nodes": {}}, (0.0, 0.0), 1.0

            pos_local_u = _compute_body_positions(schema)
            pos_u = _embed_positions(pos_local_u, embed_off_u, embed_scale_u)

            # Unify preview + overlay: use the SAME world-space camera as BodyPlanGraphWidget.
            region_local = pygame.Rect(0, 0, region.w, region.h)
            # Option A: only anchor to schema root at LoD 0; deeper views use bbox-centered framing.
            stack_depth = len(getattr(scene, "_body_zoom_stack", []) or [])
            anchor_u = _get_schema_anchor_u(schema, pos_u) if stack_depth == 0 else None
            _base_center_u, base_scale = _compute_body_graph_base_camera(
                scene, pos_u, region_local, margin_frac=0.12, anchor_u=anchor_u
            )
            cam_center_u, cam_scale = _compute_body_graph_camera(scene, pos_u, region_local, margin_frac=0.12, anchor_u=anchor_u)


            # Cache last camera so zoom-in/out can animate cleanly without widget wiring.
            try:
                setattr(scene, "_last_body_cam_center_u", tuple(cam_center_u))
                setattr(scene, "_last_body_cam_scale", float(cam_scale))
                setattr(scene, "_last_body_cam_region", (int(region.w), int(region.h)))
                setattr(scene, "_last_body_cam_base_scale", float(base_scale))
            except Exception:
                pass

            pos_px = _project_positions_with_camera(pos_u, region_local, center_u=cam_center_u, scale=cam_scale)

            # Derive a multiplicative zoom factor for glyph sizing.
            #
            # IMPORTANT: comparing cam_scale to *this layer's* base_scale will cancel out,
            # because both are computed from the same filtered pos_u. So instead, we compare
            # against a persistent "root" reference scale captured at stack depth 0.
            zoom_mul = 1.0
            try:
                stack = getattr(scene, "_body_zoom_stack", []) or []
                root_scale = getattr(scene, "_body_zoom_root_cam_scale", None)

                # Update the root reference whenever we're at the top level.
                if len(stack) == 0 and getattr(scene, "_body_zoom_anim", None) is None:
                    root_scale = float(cam_scale)
                    setattr(scene, "_body_zoom_root_cam_scale", root_scale)

                if root_scale is None or float(root_scale) <= 1e-6:
                    root_scale = float(base_scale) if float(base_scale) > 1e-6 else 1.0

                zoom_mul = float(cam_scale) / float(root_scale)
            except Exception:
                zoom_mul = 1.0




            # Anchor the glyph to the *root entity frame* (world origin), not to the currently viewed sub-schema.
            anchor = _project_point_with_camera((0.0, 0.0), region_local, center_u=cam_center_u, scale=cam_scale)
            # Use the scene-derived base glyph size so the "external opaque glyph" overlay and
            # the in-widget glyph match exactly at the moment we switch modes.
            base_px = int(getattr(scene, "_zoom_glyph_base_px", 0) or 0)
            if base_px <= 0:
                # Make the glyph ~2× bigger, but keep a little breathing room.
                base_px = max(12, int(min(region.w, region.h) * 0.50 * 2.0))
                base_px = min(base_px, int(min(region.w, region.h) * 0.90))


            # IMPORTANT: Even with a raster cap, scaling to absurd pixel sizes will stutter.
            # We don't need more pixels than we can possibly see in the clipped preview region.
            # Apply body-graph camera zoom here (this is the intended place for it).
            want_px = float(base_px) * float(zoom_mul)

            # NEW: only cap the DISPLAY size to something "safe" for allocations,
            # not to a tiny multiple of the preview region.
            # (4096 is usually plenty; 8192 if you want more.)
            DISPLAY_PX_MAX = 4096

            glyph_px = int(max(1, min(FONT_PX_MAX, want_px, DISPLAY_PX_MAX)))

            # Optional: quantize to reduce "new scale every frame" churn
            glyph_px = int(round(float(glyph_px) / float(FONT_PX_STEP))) * int(FONT_PX_STEP)
            glyph_px = max(1, glyph_px)

            font = self._get_font(glyph_px)   # NOTE: _get_font will raster-cap internally
            gcanvas = _render_entity_glyph_canvas(
                renderer,
                owner if owner is not None else type("X", (), {"glyph": glyph})(),
                font=font,
                base_px=glyph_px,             # NOTE: base_px is DISPLAY size
                scene_effects=list(getattr(scene, "visual_effects", []) or []),
            )


            # Fade the glyph when hovering the right pane so the node skeleton is easier to see.
            hovered_right = bool(getattr(scene, "_right_panel_hovered", False))
            gcanvas.set_alpha(120 if hovered_right else 255)

            # Clip to the glyph region (prevents spilling outside the right pane / over footer text).
            old_clip = card.get_clip()
            try:
                card.set_clip(region)
                gx = int(region.x + float(anchor[0]) - gcanvas.get_width() // 2)
                gy = int(region.y + float(anchor[1]) - gcanvas.get_height() // 2)
                card.blit(gcanvas, (gx, gy))
            finally:
                card.set_clip(old_clip)


        # --- Description footer (Magic-card style) -------------------------
        if desc:
            try:
                # Italic + slightly grayed out
                dfont = pygame.font.SysFont("consolas", 16, italic=True)
            except Exception:
                dfont = pygame.font.SysFont("consolas", 16)

            # Wrap to the inner card width
            max_w = max(1, r.w - 28)
            lines = _wrap_text_px(dfont, str(desc), max_w)

            # Draw from the bottom up so it hugs the bottom margin consistently
            y = r.h - 16  # bottom padding
            color = (185, 185, 195)  # gray-ish
            alpha = 210

            # Render lines bottom-up
            for line in reversed(lines):
                if not line:
                    y -= dfont.get_height()
                    continue
                s = dfont.render(line, True, color).convert_alpha()
                s.set_alpha(alpha)
                y -= s.get_height()
                card.blit(s, (14, y))

        # (Body-plan node overlay is drawn by BodyPlanGraphWidget.)

        surf.blit(card, r.topleft)



class BodyPlanGraphWidget(Widget):
    """Read-only body-plan node graph overlay for the right pane.

    Implemented as a widget so hover/collision uses PanelScene's standardized
    event -> panel logical coordinate conversion (including renderer._to_surface
    and VisualProfile unprojection).
    """

    def __init__(self) -> None:
        super().__init__()
        self.hovered_nid: str | None = None
        # Click/drag gesture tracking (panel-local coords)
        self._press_nid: str | None = None
        self._press_pos: tuple[int, int] | None = None
        self._press_ms: int = 0
        self._dragging: bool = False

        self.DRAG_HOLD_MS: int = 220
        self.DRAG_MIN_PX: int = 6
        # Double-click tracking (panel-local)
        self._last_click_nid: str | None = None
        self._last_click_ms: int = 0

    # ----------------------------
    # Rendering
    # ----------------------------

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible or self.rect.width <= 0 or self.rect.height <= 0:
            return

        scene = ctx.scene

        owner = getattr(scene, "_preview_entity", None)
        owner = owner() if callable(owner) else getattr(scene, "_find_owner_entity", lambda: None)()
        if owner is None:
            try:
                setattr(scene, "_body_overlay_panel_surface", None)
            except Exception:
                pass
            return

        try:
            info = describe_entity_for_look(owner) or {}
        except Exception:
            info = {}

        desc = info.get("description") or getattr(owner, "description", None)

        # Reserve a region that mostly covers the glyph area, not the header/footer text.
        r = self.rect
        top_reserved = 70
        bottom_reserved = 80 if desc else 56
        region = pygame.Rect(r.x + 14, r.y + top_reserved, r.w - 28, r.h - top_reserved - bottom_reserved)
        if region.w <= 10 or region.h <= 10:
            try:
                setattr(scene, "_body_overlay_panel_surface", None)
            except Exception:
                pass
            return

        # Chain (root -> ... -> active). Active schema is last.
        try:
            chain = _resolve_body_view_chain_for_zoom_path(owner, getattr(scene, "_body_zoom_stack", []))
            if not chain:
                chain = [({"root": None, "nodes": {}}, (0.0, 0.0), 1.0)]
        except Exception:
            chain = [({"root": None, "nodes": {}}, (0.0, 0.0), 1.0)]

        schema, embed_off_u, embed_scale_u = chain[-1]

        pos_u = _embed_positions(_compute_body_positions(schema), embed_off_u, embed_scale_u)

        # Camera: active schema only (ghosts excluded by design).
        region_local = pygame.Rect(0, 0, region.w, region.h)
        # Option A: only anchor to schema root at LoD 0; deeper views use bbox-centered framing.
        stack_depth = len(getattr(scene, "_body_zoom_stack", []) or [])
        anchor_u = _get_schema_anchor_u(schema, pos_u) if stack_depth == 0 else None
        cam_center_u, cam_scale = _compute_body_graph_camera(scene, pos_u, region_local, anchor_u=anchor_u)


        pos_px = _project_positions_with_camera(pos_u, region_local, center_u=cam_center_u, scale=cam_scale)

        # Combined position map (active + any ghost layers).
        # Used for rendering optional cross-layer links (e.g. torso -> head ghost).
        all_pos_px: dict[str, tuple[float, float]] = dict(pos_px)

        scale = float(cam_scale)
        node_size = int(max(18, min(56, scale * 0.45)))
        half = node_size // 2

        hovered_right = bool(getattr(scene, "_right_panel_hovered", False))
        alpha_base = 150 if hovered_right else 70
        alpha = int(alpha_base)

        # Adjacent-LoD fade during camera transition.
        fade = getattr(scene, "_body_zoom_fade", None)
        fade_dir: str | None = None
        fade_t: float = 0.0
        fade_outgoing_layer = None
        try:
            if isinstance(fade, tuple) and len(fade) >= 6:
                fade_dir, _from_stack, _to_stack, _start_ms, _dur_ms, fade_outgoing_layer = fade
                cur_stack = tuple(str(x) for x in (getattr(scene, "_body_zoom_stack", []) or []))
                if _to_stack == cur_stack and isinstance(_start_ms, int) and isinstance(_dur_ms, int) and _dur_ms > 0:
                    now = int(pygame.time.get_ticks())
                    fade_t = _smoothstep((float(now - _start_ms)) / float(_dur_ms))
                    fade_dir = str(fade_dir)
                else:
                    fade_dir = None
        except Exception:
            fade_dir = None


        drag_active = bool(getattr(scene, "_drag_active", False))
        drag_kind = getattr(scene, "_drag_target_kind", None) if drag_active else None
        drag_node = getattr(scene, "_drag_target_node_id", None) if drag_active else None

        fg = getattr(scene, "fg", (230, 230, 230))
        hilite = getattr(scene, "hilite", (255, 255, 100))

        def _half_mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float = 0.7) -> tuple[int, int, int]:
            def ch(i: int) -> int:
                v = int(a[i] + (b[i] - a[i]) * t)
                return 0 if v < 0 else 255 if v > 255 else v
            return (ch(0), ch(1), ch(2))

        half_yellow = _half_mix(tuple(fg[:3]), tuple(hilite[:3]), 0.7)

        # Panel-sized overlay (IMPORTANT: panel coords, not region-local coords).
        overlay = pygame.Surface(ctx.surface.get_size(), pygame.SRCALPHA)
        overlay.set_clip(region)

        # ----------------------------
        # Ghost layers: parents only
        # ----------------------------
        if len(chain) > 1:
            def _ghost_alpha(base_alpha: int, dist: int) -> int:
                try:
                    a = float(base_alpha) * (0.55 ** max(1, int(dist)))
                    return int(max(10, min(255, a)))
                except Exception:
                    return int(max(10, min(255, base_alpha // 3)))

            zoom_stack = [str(x) for x in (getattr(scene, "_body_zoom_stack", []) or [])]
            # Apply fade to the active layer alpha (ghost layers use alpha_base).
            if fade_dir == "in":
                alpha = int(round(float(alpha_base) * float(fade_t)))
            elif fade_dir == "out":
                g1 = _ghost_alpha(alpha_base, 1)
                alpha = int(round(float(g1) * (1.0 - float(fade_t)) + float(alpha_base) * float(fade_t)))

            for i, (g_schema, g_off_u, g_scale_u) in enumerate(chain[:-1]):
                dist = (len(chain) - 1) - i  # +1, +2, ...
                if fade_dir == "in" and dist == 1:
                    continue  # dist=1 handled by crossfade layer
                g_alpha = _ghost_alpha(alpha_base, dist)
                if g_alpha < 12:
                    continue

                # IMPORTANT: don't draw ghost edges *out of* the node we zoomed through
                # at this layer (that node is being "replaced" by the active subgraph).
                zs = list(getattr(scene, "_body_zoom_stack", []) or [])
                skip_from_nid = str(zs[i]) if i < len(zs) else None


                g_pos_u = _embed_positions(_compute_body_positions(g_schema), g_off_u, g_scale_u)
                g_pos_px = _project_positions_with_camera(
                    g_pos_u, region_local, center_u=cam_center_u, scale=cam_scale
                )

                # Merge ghost node positions into combined map (do not clobber active positions).
                for _nid, _p in (g_pos_px or {}).items():
                    if _nid not in all_pos_px and _p is not None:
                        all_pos_px[_nid] = _p

                g_nodes = g_schema.get("nodes") if isinstance(g_schema, dict) else None
                if isinstance(g_nodes, dict):
                    line_col = (*fg, int(g_alpha * 0.65))
                    for nid, spec in g_nodes.items():
                        if skip_from_nid is not None and str(nid) == skip_from_nid:
                            continue  # suppress outgoing ghost edges from the embedding node

                        a = g_pos_px.get(str(nid))
                        if a is None:
                            continue
                        for ch in _children_of(spec if isinstance(spec, dict) else {}):
                            b = g_pos_px.get(str(ch))
                            if b is None:
                                continue
                            ax, ay = int(a[0] + region.x), int(a[1] + region.y)
                            bx, by = int(b[0] + region.x), int(b[1] + region.y)
                            pygame.draw.line(overlay, line_col, (ax, ay), (bx, by), 1)

                fill = (*fg, int(g_alpha * 0.07))
                border = (*fg, int(g_alpha * 0.55))
                for nid, p in g_pos_px.items():
                    if p is None:
                        continue
                    cx, cy = int(p[0] + region.x), int(p[1] + region.y)
                    pygame.draw.rect(overlay, fill, pygame.Rect(cx - half, cy - half, node_size, node_size), 0, 2)
                    pygame.draw.rect(overlay, border, pygame.Rect(cx - half, cy - half, node_size, node_size), 1, 2)

        # ----------------------------
        
        # ----------------------------
        # Adjacent-LoD crossfade: draw outgoing parent layer (zoom-in)
        # ----------------------------
        def _draw_simple_layer(
            layer_schema: dict,
            layer_pos_px: dict[str, tuple[float, float]],
            layer_alpha: int,
        ) -> None:
            if layer_alpha <= 0:
                return
            nodes_l = layer_schema.get("nodes") if isinstance(layer_schema, dict) else None
            if not isinstance(nodes_l, dict):
                return

            # edges
            try:
                line_col_l = (*fg, int(layer_alpha * 0.85))
                for _nid, _spec in nodes_l.items():
                    a = layer_pos_px.get(str(_nid))
                    if a is None:
                        continue
                    for ch in _children_of(_spec if isinstance(_spec, dict) else {}):
                        b = layer_pos_px.get(str(ch))
                        if b is None:
                            continue
                        ax, ay = int(a[0] + region.x), int(a[1] + region.y)
                        bx, by = int(b[0] + region.x), int(b[1] + region.y)
                        pygame.draw.line(overlay, line_col_l, (ax, ay), (bx, by), 2)
            except Exception:
                pass

            # boxes + labels (no equips, no hover)
            try:
                node_fill_l = (*fg, int(layer_alpha * 0.08))
                node_border_l = (*fg, int(layer_alpha * 0.78))
                label_col_l = (int(fg[0] * 0.95), int(fg[1] * 0.95), int(fg[2] * 0.95), int(layer_alpha * 0.95))
                try:
                    label_font_l = pygame.font.SysFont("consolas", max(11, int(node_size * 0.26)), bold=True)
                except Exception:
                    label_font_l = pygame.font.SysFont("consolas", max(11, int(node_size * 0.26)))

                for _nid, (px, py) in layer_pos_px.items():
                    # region-local clip test
                    if not pygame.Rect(0, 0, region.w, region.h).collidepoint(px, py):
                        continue
                    sq = pygame.Rect(int(px - half) + region.x, int(py - half) + region.y, int(node_size), int(node_size))
                    pygame.draw.rect(overlay, node_fill_l, sq)
                    pygame.draw.rect(overlay, node_border_l, sq, 2)

                    try:
                        label = _display_body_node_label(str(_nid), nodes_l.get(str(_nid)), cur_nodes=nodes_l)
                        ls = label_font_l.render(label, True, label_col_l).convert_alpha()
                        ls.set_alpha(int(layer_alpha * 0.92))
                        lx = sq.centerx - ls.get_width() // 2
                        ly = sq.top - ls.get_height() - 3
                        overlay.blit(ls, (lx, ly))
                    except Exception:
                        pass
            except Exception:
                pass

        if fade_dir == "in" and len(chain) >= 2:
            try:
                # outgoing parent is the immediate ancestor layer
                p_schema, p_off_u, p_scale_u = chain[-2]
                p_pos_u = _embed_positions(_compute_body_positions(p_schema), p_off_u, p_scale_u)
                p_pos_px = _project_positions_with_camera(p_pos_u, region_local, center_u=cam_center_u, scale=cam_scale)

                g1 = _ghost_alpha(alpha_base, 1)
                out_a = int(round(float(alpha_base) * (1.0 - float(fade_t)) + float(g1) * float(fade_t)))
                _draw_simple_layer(p_schema, p_pos_px, out_a)

                # ensure these positions are available for optional cross-layer links
                all_pos_px.update({str(k): v for k, v in p_pos_px.items() if v is not None})
            except Exception:
                pass

        # Active edges
        # ----------------------------
        nodes = schema.get("nodes") if isinstance(schema, dict) else None
        if isinstance(nodes, dict):
            line_col = (*fg, int(alpha * 0.85))
            for nid, spec in nodes.items():
                a = pos_px.get(str(nid))
                if a is None:
                    continue
                for ch in _children_of(spec if isinstance(spec, dict) else {}):
                    b = pos_px.get(str(ch))
                    if b is None:
                        continue
                    ax, ay = int(a[0] + region.x), int(a[1] + region.y)
                    bx, by = int(b[0] + region.x), int(b[1] + region.y)
                    pygame.draw.line(overlay, line_col, (ax, ay), (bx, by), 2)

        # Optional extra links (schema-level). These are render-only edges that may
        # connect nodes across layers, e.g. torso -> head (ghost) or hips -> leg (ghost).
        links = schema.get("links") if isinstance(schema, dict) else None
        if isinstance(links, list) and links:
            link_col = (*fg, int(alpha * 0.70))
            for ln in links:
                a_id = None
                b_id = None
                if isinstance(ln, (list, tuple)) and len(ln) >= 2:
                    a_id, b_id = str(ln[0]), str(ln[1])
                elif isinstance(ln, dict):
                    a_id = ln.get("from", ln.get("a"))
                    b_id = ln.get("to", ln.get("b"))
                    a_id = str(a_id) if a_id is not None else None
                    b_id = str(b_id) if b_id is not None else None
                if not a_id or not b_id:
                    continue
                a = all_pos_px.get(a_id)
                b = all_pos_px.get(b_id)
                if a is None or b is None:
                    continue
                ax, ay = int(a[0] + region.x), int(a[1] + region.y)
                bx, by = int(b[0] + region.x), int(b[1] + region.y)
                pygame.draw.line(overlay, link_col, (ax, ay), (bx, by), 1)

        node_fill = (*fg, int(alpha * 0.10))
        node_border = (*fg, int(alpha * 0.85))
        hi_border = (*hilite, int(alpha * 1.0))
        half_border = (*half_yellow, int(alpha * 0.98))

        label_col = (int(fg[0] * 0.95), int(fg[1] * 0.95), int(fg[2] * 0.95), int(alpha * 0.95))
        label_hi = (int(hilite[0]), int(hilite[1]), int(hilite[2]), int(alpha * 0.98))
        label_half = (int(half_yellow[0]), int(half_yellow[1]), int(half_yellow[2]), int(alpha * 0.97))

        hovered_nid = self.hovered_nid

        try:
            glyph_font = pygame.font.SysFont("consolas", max(FONT_PX_MIN, int(node_size * 0.78)), bold=True)
            label_font = pygame.font.SysFont("consolas", max(FONT_PX_MIN, int(node_size * 0.26)), bold=True)
            item_font  = pygame.font.SysFont("consolas", max(FONT_PX_MIN, int(node_size * 0.24)), bold=False)

        except Exception:
            glyph_font = pygame.font.SysFont("consolas", max(FONT_PX_MIN, int(node_size * 0.78)))
            label_font = pygame.font.SysFont("consolas", max(FONT_PX_MIN, int(node_size * 0.26)))
            item_font  = pygame.font.SysFont("consolas", max(FONT_PX_MIN, int(node_size * 0.24)))

        owner_id = str(getattr(owner, "id", ""))

        def _equipped_for(nid: str):
            # IMPORTANT: equip slots must be unique per *instance*, not per proto-id.
            # Use the current zoom stack + local nid as a stable path key.
            stack = [str(x) for x in (getattr(scene, "_body_zoom_stack", []) or [])]
            slot_id = "/".join(stack + [str(nid)]) if stack else str(nid)

            if hasattr(scene, "game") and hasattr(scene.game, "get_equipped_in_slot"):
                try:
                    return scene.game.get_equipped_in_slot(owner_id, slot_id)
                except Exception:
                    return None

            # Fallback: scan inventory tags
            try:
                inv = scene.game.get_inventory(owner_id)
            except Exception:
                inv = None
            if inv:
                for it in inv:
                    tags = getattr(it, "tags", {}) or {}
                    if str(tags.get("equipped_slot") or tags.get("equipped") or "") == slot_id:
                        return it
            return None


        for nid, (px, py) in pos_px.items():
            # region-local clip test
            if not pygame.Rect(0, 0, region.w, region.h).collidepoint(px, py):
                continue

            is_hover = (hovered_nid is not None and str(nid) == hovered_nid)
            is_target = (drag_kind == "body_node" and drag_node is not None and str(nid) == str(drag_node))
            is_hot = bool(is_hover or is_target)

            sq = pygame.Rect(int(px - half) + region.x, int(py - half) + region.y, int(node_size), int(node_size))
            pygame.draw.rect(overlay, node_fill, sq)

            border_col = hi_border if is_hover else (half_border if is_target else node_border)
            pygame.draw.rect(overlay, border_col, sq, 3 if is_hover else 2)

            eq = _equipped_for(str(nid))

            # label above
            try:
                label = _display_body_node_label(str(nid), nodes.get(str(nid)), cur_nodes=nodes)
                ls = label_font.render(label, True, label_hi if is_hover else (label_half if is_target else label_col)).convert_alpha()
                ls.set_alpha(int(alpha * (0.98 if is_hot else 0.90)))
                lx = sq.centerx - ls.get_width() // 2
                ly = sq.top - ls.get_height() - 3
                overlay.blit(ls, (lx, ly))
            except Exception:
                pass

            # equipped glyph
            if eq is not None:
                try:
                    r2 = ctx.renderer
                    base_px = int(node_size * 0.86)

                    eff_scene = []
                    try:
                        eff_scene = list(getattr(scene, "scene_effects", []) or [])
                    except Exception:
                        eff_scene = []

                    gcanvas, anchor = _render_entity_glyph_canvas_with_anchor(
                        r2,
                        eq,
                        font=glyph_font,
                        base_px=base_px,
                        scene_effects=eff_scene,
                    )
                    gx = int(round(sq.centerx - float(anchor[0])))
                    gy = int(round(sq.centery - float(anchor[1])))

                    if is_hot:
                        glyph_alpha = 245
                    else:
                        glyph_alpha = 120 if hovered_right else 85

                    tmp = gcanvas.convert_alpha()
                    tmp.set_alpha(int(glyph_alpha))
                    overlay.blit(tmp, (gx, gy))
                except Exception:
                    pass

            # equipped item label below
            try:
                if eq is not None:
                    item_name = str(getattr(eq, "name", None) or "Item")
                    ns = item_font.render(item_name, True, label_hi if is_hover else (label_half if is_target else label_col)).convert_alpha()
                    ns.set_alpha(int(alpha * (0.98 if is_hot else 0.90)))
                    nx = sq.centerx - ns.get_width() // 2
                    ny = sq.bottom + 3
                    overlay.blit(ns, (nx, ny))
            except Exception:
                pass

        
        # ----------------------------
        # Adjacent-LoD crossfade: draw outgoing child layer (zoom-out) on top
        # ----------------------------
        if fade_dir == "out" and fade_outgoing_layer is not None:
            try:
                c_schema, c_off_u, c_scale_u = fade_outgoing_layer
                c_pos_u = _embed_positions(_compute_body_positions(c_schema), c_off_u, c_scale_u)
                c_pos_px = _project_positions_with_camera(c_pos_u, region_local, center_u=cam_center_u, scale=cam_scale)

                out_a = int(round(float(alpha_base) * (1.0 - float(fade_t))))
                _draw_simple_layer(c_schema, c_pos_px, out_a)

                # (No equip rendering for outgoing layer: slot paths differ.)
            except Exception:
                pass

        overlay.set_clip(None)

        # Cache for InventoryScene.render() to composite above opaque glyph when needed.
        try:
            setattr(scene, "_body_overlay_panel_surface", overlay)
        except Exception:
            pass

        # If we're not in external glyph overlay mode, draw immediately.
        if not bool(getattr(scene, "_external_opaque_glyph", False)):
            ctx.surface.blit(overlay, (0, 0))

        super().draw(ctx)

    # ----------------------------
    # Hit testing (no drawing)
    # ----------------------------

    def _hit_test_node(self, mp: tuple[int, int], ctx: WidgetContext) -> str | None:
        scene = ctx.scene

        owner = getattr(scene, "_preview_entity", None)
        owner = owner() if callable(owner) else getattr(scene, "_find_owner_entity", lambda: None)()
        if owner is None:
            return None

        try:
            info = describe_entity_for_look(owner) or {}
        except Exception:
            info = {}

        desc = info.get("description") or getattr(owner, "description", None)

        r = self.rect
        top_reserved = 70
        bottom_reserved = 80 if desc else 56
        region = pygame.Rect(r.x + 14, r.y + top_reserved, r.w - 28, r.h - top_reserved - bottom_reserved)
        if region.w <= 10 or region.h <= 10:
            return None

        try:
            chain = _resolve_body_view_chain_for_zoom_path(owner, getattr(scene, "_body_zoom_stack", []))
            if not chain:
                chain = [({"root": None, "nodes": {}}, (0.0, 0.0), 1.0)]
        except Exception:
            chain = [({"root": None, "nodes": {}}, (0.0, 0.0), 1.0)]

        schema, embed_off_u, embed_scale_u = chain[-1]
        pos_u = _embed_positions(_compute_body_positions(schema), embed_off_u, embed_scale_u)

        region_local = pygame.Rect(0, 0, region.w, region.h)
        # Option A: only anchor to schema root at LoD 0; deeper views use bbox-centered framing.
        stack_depth = len(getattr(scene, "_body_zoom_stack", []) or [])
        anchor_u = _get_schema_anchor_u(schema, pos_u) if stack_depth == 0 else None
        cam_center_u, cam_scale = _compute_body_graph_camera(scene, pos_u, region_local, anchor_u=anchor_u)
        pos_px = _project_positions_with_camera(pos_u, region_local, center_u=cam_center_u, scale=cam_scale)

        node_size = int(max(18, min(56, float(cam_scale) * 0.45)))
        half = node_size // 2

        mx, my = int(mp[0]), int(mp[1])

        for nid, (px, py) in pos_px.items():
            sq = pygame.Rect(int(px - half) + region.x, int(py - half) + region.y, int(node_size), int(node_size))
            if sq.collidepoint((mx, my)):
                return str(nid)

        return None

    # ----------------------------
    # Event handling (unchanged)
    # ----------------------------

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        if not self.visible or self.rect.width <= 0 or self.rect.height <= 0:
            return False

        scene = ctx.scene

        def _cancel_press() -> None:
            self._press_nid = None
            self._press_pos = None
            self._press_ms = 0
            self._dragging = False

        def _begin_drag_if_ready(cur_pos: tuple[int, int]) -> bool:
            if self._press_nid is None or self._dragging:
                return False

            now = int(pygame.time.get_ticks())
            held = (now - int(self._press_ms)) >= int(self.DRAG_HOLD_MS)

            moved = False
            if self._press_pos is not None:
                dx = int(cur_pos[0]) - int(self._press_pos[0])
                dy = int(cur_pos[1]) - int(self._press_pos[1])
                moved = (dx * dx + dy * dy) >= int(self.DRAG_MIN_PX * self.DRAG_MIN_PX)

            if not (held or moved):
                return False

            cb = getattr(scene, "_body_drag_begin", None)
            if callable(cb):
                try:
                    if cb(node_id=str(self._press_nid), pos=cur_pos):
                        self._dragging = True
                        return True
                except Exception:
                    pass
            return False

        if event.type == pygame.MOUSEMOTION:
            pos = getattr(event, "pos", None)
            if pos is None:
                return False
            mx, my = int(pos[0]), int(pos[1])

            try:
                setattr(scene, "_right_panel_hovered", bool(self.rect.collidepoint((mx, my))))
            except Exception:
                pass

            if self._press_nid is not None and not bool(getattr(scene, "_drag_active", False)):
                if _begin_drag_if_ready((mx, my)):
                    cb2 = getattr(scene, "_inv_drag_update", None)
                    if callable(cb2):
                        try:
                            cb2(pos=(mx, my))
                        except Exception:
                            pass
                    self.hovered_nid = self._hit_test_node((mx, my), ctx)
                    return True

            if bool(getattr(scene, "_drag_active", False)):
                cb = getattr(scene, "_inv_drag_update", None)
                if callable(cb):
                    try:
                        cb(pos=(mx, my))
                    except Exception:
                        pass
                self.hovered_nid = self._hit_test_node((mx, my), ctx)
                return True

            self.hovered_nid = self._hit_test_node((mx, my), ctx)
            return False

        if event.type == pygame.MOUSEBUTTONDOWN:
            pos = getattr(event, "pos", None)
            if pos is None:
                return False
            mx, my = int(pos[0]), int(pos[1])

            if getattr(event, "button", None) == 1 and self.rect.collidepoint((mx, my)):
                nid = self._hit_test_node((mx, my), ctx)
                if nid:
                    self._press_nid = str(nid)
                    self._press_pos = (mx, my)
                    self._press_ms = int(pygame.time.get_ticks())
                    self._dragging = False
                    return True
            return False

        if event.type == pygame.MOUSEBUTTONUP:
            pos = getattr(event, "pos", None)
            if pos is None:
                return False
            mx, my = int(pos[0]), int(pos[1])

            if getattr(event, "button", None) == 1:
                was_dragging = bool(getattr(scene, "_drag_active", False))
                if was_dragging:
                    cb = getattr(scene, "_inv_drag_end", None)
                    if callable(cb):
                        try:
                            cb(pos=(mx, my))
                        except Exception:
                            pass
                    try:
                        super().handle_event(event, ctx)
                    except Exception:
                        pass
                    _cancel_press()
                    return True

                press_nid = self._press_nid
                _cancel_press()

                if press_nid and self.rect.collidepoint((mx, my)):
                    release_nid = self._hit_test_node((mx, my), ctx)
                    if release_nid and str(release_nid) == str(press_nid):
                        now_ms = int(pygame.time.get_ticks())

                        # double-click
                        try:
                            if (
                                self._last_click_nid is not None
                                and str(self._last_click_nid) == str(release_nid)
                                and (now_ms - int(self._last_click_ms)) <= 320
                            ):
                                try:
                                    owner = getattr(scene, "_preview_entity", None)
                                    owner = owner() if callable(owner) else getattr(scene, "_find_owner_entity", lambda: None)()
                                except Exception:
                                    owner = None

                                can_zoom = False
                                if owner is not None:
                                    try:
                                        schema0, _off0, _s0 = _resolve_body_view_for_zoom_path(
                                            owner, getattr(scene, "_body_zoom_stack", [])
                                        )
                                    except Exception:
                                        schema0 = {"root": None, "nodes": {}}
                                    node = (schema0.get("nodes", {}) or {}).get(str(release_nid))
                                    if isinstance(node, dict):
                                        proto = node.get("proto")
                                        if proto:
                                            try:
                                                sub = resolve_body_schema(proto) or {"root": None, "nodes": {}}
                                            except Exception:
                                                sub = {"root": None, "nodes": {}}
                                            sub_nodes = sub.get("nodes") if isinstance(sub, dict) else None
                                            if isinstance(sub_nodes, dict):
                                                cur_nodes = (schema0.get("nodes", {}) or {}) if isinstance(schema0, dict) else {}
                                                meaningful = len(sub_nodes) > 1
                                                if meaningful and isinstance(cur_nodes, dict):
                                                    meaningful = set(sub_nodes.keys()) != set(cur_nodes.keys())
                                                can_zoom = bool(meaningful)
                                if can_zoom:
                                    # Double-click zoom wins: cancel any pending delayed single-click activation.
                                    try:
                                        setattr(scene, "_pending_node_activate_nid", None)
                                        setattr(scene, "_pending_node_activate_due_ms", 0)
                                    except Exception:
                                        pass
                                    setattr(scene, "_pending_body_zoom_in", str(release_nid))
                                self._last_click_nid = None
                                self._last_click_ms = 0
                                return True
                        except Exception:
                            pass

                        self._last_click_nid = str(release_nid)
                        self._last_click_ms = now_ms

                        if hasattr(scene, "_equipped_entity_for_slot"):
                            stack = [str(x) for x in (getattr(scene, "_body_zoom_stack", []) or [])]
                            slot_id = "/".join(stack + [str(press_nid)]) if stack else str(press_nid)
                            eq = scene._equipped_entity_for_slot(slot_id)
                        else:
                            eq = None

                        if eq is not None:
                            # Delay the single-click context menu so a possible double-click zoom
                            # can "win" without the menu stealing focus.
                            try:
                                setattr(scene, "_pending_node_activate_nid", str(press_nid))
                                setattr(scene, "_pending_node_activate_due_ms", int(now_ms) + 320)
                            except Exception:
                                # Fallback: behave like immediate activate.
                                try:
                                    setattr(scene, "_pending_node_activate", str(press_nid))
                                except Exception:
                                    pass
                            return True

            return False

        return False




class RightPaneWidget(Widget):
    """Layered right pane: base preview + body-plan graph overlay."""

    def __init__(self, *, preview: Widget, body_graph: Widget) -> None:
        super().__init__()
        self.preview = preview
        self.body_graph = body_graph
        # Draw order: preview first, overlay second.
        self.add_child(self.preview)
        self.add_child(self.body_graph)

    def layout(self, ctx: WidgetContext) -> None:
        # Both layers occupy the same rect.
        self.preview.rect = pygame.Rect(self.rect)
        self.body_graph.rect = pygame.Rect(self.rect)

        # IMPORTANT: compute the body-zoom focus position *during layout* so BOTH
        # the preview glyph and the body overlay use the same focus point this frame.
        try:
            scene = ctx.scene
            owner = getattr(scene, "_preview_entity", None)
            owner = owner() if callable(owner) else getattr(scene, "_find_owner_entity", lambda: None)()
            if owner is not None:
                try:
                    info = describe_entity_for_look(owner) or {}
                except Exception:
                    info = {}
                desc = info.get("description") or getattr(owner, "description", None)

                r = self.body_graph.rect
                top_reserved = 70
                bottom_reserved = 80 if desc else 56
                region = pygame.Rect(r.x + 14, r.y + top_reserved, r.w - 28, r.h - top_reserved - bottom_reserved)

                if region.w > 10 and region.h > 10:
                    try:
                        schema, embed_off_u, embed_scale_u = _resolve_body_view_for_zoom_path(
                            owner, getattr(scene, "_body_zoom_stack", [])
                        )
                    except Exception:
                        schema, embed_off_u, embed_scale_u = {"root": None, "nodes": {}}, (0.0, 0.0), 1.0

                    pos_u = _embed_positions(_compute_body_positions(schema), embed_off_u, embed_scale_u)
                    # Unify preview + overlay: use the SAME world-space camera as BodyPlanGraphWidget.
                    region_local = pygame.Rect(0, 0, region.w, region.h)
                    # Option A: only anchor to schema root at LoD 0; deeper views use bbox-centered framing.
                    stack_depth = len(getattr(scene, "_body_zoom_stack", []) or [])
                    anchor_u = _get_schema_anchor_u(schema, pos_u) if stack_depth == 0 else None
                    cam_center_u, cam_scale = _compute_body_graph_camera(scene, pos_u, region_local, anchor_u=anchor_u)
                    pos_px = _project_positions_with_camera(pos_u, region_local, center_u=cam_center_u, scale=cam_scale)

                    # Match BodyPlanGraphWidget.draw(): interpolate focus when animating.
                    focus_pos = None
                    anim = getattr(scene, "_body_zoom_anim", None)
                    if anim is not None:
                        # Phase 1.5: camera-state anim no longer encodes focus ids.
                        # Keep focus_pos stable (or None) to avoid coupling focus UI to camera animation.
                        zoom_focus = getattr(scene, "_body_zoom_focus_nid", None)
                        focus_pos = pos_px.get(zoom_focus) if zoom_focus else None
                        setattr(scene, "_body_zoom_focus_pos", focus_pos)
                        now = int(pygame.time.get_ticks())
                        if dur_ms <= 0:
                            t = 1.0
                        else:
                            t = (now - int(start_ms)) / float(dur_ms)
                            if t < 0.0:
                                t = 0.0
                            if t > 1.0:
                                t = 1.0
                        t = t * t * (3.0 - 2.0 * t)

                        if from_focus and to_focus and from_focus in pos_px and to_focus in pos_px:
                            x0, y0 = pos_px[from_focus]
                            x1, y1 = pos_px[to_focus]
                            focus_pos = (x0 * (1.0 - t) + x1 * t, y0 * (1.0 - t) + y1 * t)
                        elif to_focus and to_focus in pos_px:
                            focus_pos = pos_px[to_focus]
                        elif from_focus and from_focus in pos_px:
                            focus_pos = pos_px[from_focus]
                    else:
                        zoom_focus = getattr(scene, "_body_zoom_focus_nid", None)
                        focus_pos = pos_px.get(zoom_focus) if zoom_focus else None

                    setattr(scene, "_body_zoom_focus_pos", focus_pos)
        except Exception:
            pass

        # Now do normal child layout.
        self.preview.layout(ctx)
        self.body_graph.layout(ctx)

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        # Keep the "right pane hovered" flag authoritative at the pane level,
        # not just on the body graph layer.
        pos = getattr(event, "pos", None)
        if pos is not None:
            try:
                mx, my = int(pos[0]), int(pos[1])
                ctx.scene._right_panel_hovered = bool(self.rect.collidepoint((mx, my)))
            except Exception:
                pass

        return super().handle_event(event, ctx)




class DragOverlayWidget(Widget):
    """Draws the active inventory drag ghost + drop hint on top of the UI."""

    def draw(self, ctx: WidgetContext) -> None:
        scene = getattr(ctx, "scene", None)
        if scene is None or not getattr(scene, "_drag_active", False):
            return

        pos = getattr(scene, "_drag_pos", None)
        if pos is None:
            return

        mx, my = int(pos[0]), int(pos[1])

        r = ctx.renderer
        font = getattr(r, "menu_font", getattr(r, "small_font", getattr(r, "font", None)))
        if font is None:
            return

        fg = getattr(r, "fg", (220, 230, 240))
        sel = getattr(r, "sel", (255, 255, 0))

        label = str(getattr(scene, "_drag_label", "") or "")
        hint = str(getattr(scene, "_drag_hint", "") or "")

        if not label and not hint:
            return

        # Build text surfaces
        label_surf = font.render(label, True, fg) if label else None
        hint_surf = font.render(hint, True, sel) if hint else None

        pad = 6
        gap = 4
        w = 0
        h = 0
        if label_surf:
            w = max(w, label_surf.get_width())
            h += label_surf.get_height()
        if hint_surf:
            if h:
                h += gap
            w = max(w, hint_surf.get_width())
            h += hint_surf.get_height()

        box = pygame.Surface((w + 2 * pad, h + 2 * pad), pygame.SRCALPHA)
        # Translucent background + border
        box.fill((10, 10, 20, 160))
        pygame.draw.rect(box, (220, 220, 240, 180), box.get_rect(), 1)

        y = pad
        if label_surf:
            tmp = label_surf.convert_alpha()
            tmp.set_alpha(180)  # ghost
            box.blit(tmp, (pad, y))
            y += label_surf.get_height()
        if hint_surf:
            if label_surf:
                y += gap
            tmp = hint_surf.convert_alpha()
            tmp.set_alpha(220)
            box.blit(tmp, (pad, y))

        # Slight offset from cursor; clamp into panel
        x = mx + 12
        y = my + 12
        panel_rect = ctx.surface.get_rect()
        if x + box.get_width() > panel_rect.right:
            x = mx - 12 - box.get_width()
        if y + box.get_height() > panel_rect.bottom:
            y = my - 12 - box.get_height()

        ctx.surface.blit(box, (x, y))
        super().draw(ctx)





class TwoPaneInventoryRoot(Widget):
    def __init__(
        self,
        *,
        header: Widget,
        left: Widget,
        right: Widget,
        footer: Widget,
        padding: int = 14,
        spacing: int = 12,
        col_spacing: int = 14,
        left_frac: float = 0.46,
        min_right_w: int = 220,
    ) -> None:
        super().__init__()
        self.header = header
        self.left = left
        self.right = right
        self.footer = footer

        self.padding = int(padding)
        self.spacing = int(spacing)
        self.col_spacing = int(col_spacing)
        self.left_frac = float(left_frac)
        self.min_right_w = int(min_right_w)

        self.add_child(self.header)
        self.add_child(self.left)
        self.add_child(self.right)
        self.add_child(self.footer)

    def layout(self, ctx: WidgetContext) -> None:
        for c in self.children:
            c.layout(ctx)

        if self.rect.width == 0 or self.rect.height == 0:
            self.rect = ctx.surface.get_rect()

        r = self.rect
        pad = self.padding

        # header at top
        self.header.rect.topleft = (r.x + pad, r.y + pad)

        # footer at bottom
        self.footer.rect.topleft = (r.x + pad, r.bottom - pad - self.footer.rect.height)

        # body region
        top = self.header.rect.bottom + self.spacing
        bottom = self.footer.rect.top - self.spacing
        avail_h = max(1, bottom - top)

        inner_w = max(1, r.width - 2 * pad)
        left_w = int(inner_w * self.left_frac)
        right_w = inner_w - left_w - self.col_spacing
        if right_w < self.min_right_w:
            right_w = self.min_right_w
            left_w = max(1, inner_w - right_w - self.col_spacing)

        # left column
        self.left.rect.topleft = (r.x + pad, top)
        self.left.rect.width = max(1, left_w)
        self.left.rect.height = avail_h
        self.left.layout(ctx)

        # right column
        rx = self.left.rect.right + self.col_spacing
        self.right.rect.topleft = (rx, top)
        self.right.rect.width = max(1, right_w)
        self.right.rect.height = avail_h
        self.right.layout(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        for child in self.children:
            child.draw(ctx)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

@dataclass
class _InvRow:
    label: str
    ent: Any | None = None  # None for Back / Empty rows

    def __str__(self) -> str:
        return self.label


class InventoryScene(PopupMenuScene):
    """
    Two-pane inventory prototype (popup PanelScene):
      - Left: list of items (glyph + name), keyboard + mouse
      - Right: magnified “inhabited entity” preview
      - Activate: context menu (UrgentMessageScene)

    Diagrammatic zoom:
      - The panel transform is anchored on the preview glyph center.
      - That anchor point travels from the owner's *exact* map pixel to its final location.
      - Panel alpha fades in; glyph stays solid (drawn as an extra overlay pass).

    This version also scales proportionately:
      - Start scale is chosen so the *final preview glyph*, after transform,
        starts at the same on-screen size as the map glyph (respecting mousewheel zoom).
      - Then it lerps up to full panel size.
    """

    FOOTER_TEXT = "↑/↓ or W/S to move • Enter/Space to select • Esc/i to go back"

    ZOOM_MS: int = 900

    CLOSE_MS: int = 700

    # Artistic tweak multipliers (applied on top of the physically-derived scale).
    PANEL_SCALE_START: float = 1.00
    PANEL_SCALE_END: float = 1.00

    PANEL_ALPHA_START: float = 0.00
    PANEL_ALPHA_END: float = 1.00


    # Each nested inventory is multiplied by this factor (recursion CRT effect).
    DEPTH_SCALE: float = 0.90
    def __init__(
        self,
        game,
        *,
        owner_id: Optional[str] = None,
        mode: str = "inventory",
        window_rect: Optional[pygame.Rect] = None,
        parent_owner_id: Optional[str] = None,
        title: Optional[str] = None,
        base_effects: Optional[list[str]] = None,
        source_px: tuple[int, int] | None = None,
        source_glyph_px: int | None = None,
        stack_depth: int = 0,
        animate_affine: bool = False,
        ) -> None:
        self.game = game
        self.owner_id = owner_id
        self.parent_owner_id = parent_owner_id
        self.explicit_title = title

        # Inspect mode: 'inventory' (full control) or 'look' (read-only inspect)
        self.inspect_mode = str(mode or "inventory")
        self.allow_open_containers = (self.inspect_mode == "inventory")
        self.allow_drag_drop = (self.inspect_mode == "inventory")
        self.allow_item_actions = (self.inspect_mode == "inventory")

        self.visual_effects: list[str] = list(base_effects or [])

        self.stack_depth = int(stack_depth)

        # ---- Phase 5 foundation: body-graph camera zoom ----
        self._body_zoom_stack: list[str] = []
        self._body_zoom_focus_nid: str | None = None
        self._body_zoom_scale: float = 1.0

        # Phase 1.5: camera-state interpolation (pan + scale) ONLY.
        # (from_center_u, from_scale, to_center_u, to_scale, start_ms, duration_ms)
        self._body_zoom_anim: tuple[
            tuple[float, float],
            float,
            tuple[float, float],
            float,
            int,
            int,
        ] | None = None

        # Toggle + timing
        self._body_zoom_anim_enabled: bool = True
        self._body_zoom_anim_duration_ms: int = 220


        # Fade state for adjacent-LoD crossfade during camera zoom.
        # (dir, from_stack, to_stack, start_ms, dur_ms, outgoing_layer_bundle)
        # outgoing_layer_bundle is only used for dir=='out' and is a (schema, embed_off_u, embed_scale_u)
        # already embedded into root/world-space.
        self._body_zoom_fade: tuple[
            str,
            tuple[str, ...],
            tuple[str, ...],
            int,
            int,
            tuple[dict, tuple[float, float], float] | None,
        ] | None = None

        # set by BodyPlanGraphWidget on double-click
        self._pending_body_zoom_in: str | None = None



        # If True, we *animate* rotation/flips during the zoom.
        # Default is False: the panel starts already transformed (less distracting).
        self.animate_affine = bool(animate_affine)

        # Depth-based “CRT recursion” scaling that actually scales rendered text.
        self._depth_visual_scale = float(self.DEPTH_SCALE ** max(0, self.stack_depth))
        self._body_zoom_anim_duration_ms = 800

        self._zoom_elapsed = 0
        self._zoom_progress = 0.0

        # Closing animation (reverse of the diagrammatic zoom).
        self._closing: bool = False
        self._close_elapsed: int = 0

        # Source in renderer.surface coords.
        self._source_from_parent_panel: bool = False

        self._zoom_source_px: tuple[int, int] | None = None

        # Approximate on-screen pixel size of the source glyph (used for nested inventory zoom scaling).
        self._zoom_source_glyph_px: int | None = None
        self._zoom_owner_world: tuple[int, int] | None = None

        # Optional override: when opening a nested inventory, the source glyph
        # is often a glyph in the *parent* inventory list (not a world tile).
        if source_px is not None:
            self._source_from_parent_panel = True
            try:
                self._zoom_source_px = (int(source_px[0]), int(source_px[1]))
            except Exception:
                self._zoom_source_px = None


        if source_glyph_px is not None:
            try:
                self._zoom_source_glyph_px = int(source_glyph_px)
            except Exception:
                self._zoom_source_glyph_px = None

        # Cached map tile pixel size (respects mousewheel zoom).
        self._zoom_map_tile_px: float = 32.0

        # Panel-local anchor (center of the preview glyph) we keep glued to the source.
        self._zoom_anchor_panel: tuple[float, float] | None = None

        # Base pixel size of the *final* glyph inside the preview pane (panel space).
        self._zoom_glyph_base_px: int = 48

        # If we can derive the initial panel scale from glyph sizes, store it here.
        self._zoom_start_scale: float | None = None

        # Preview will skip glyph; we redraw it as an opaque overlay.
        self._external_opaque_glyph: bool = False

        self._rows: list[_InvRow] = []

        self._list: Optional[ListWidget] = None
        self._preview: Optional[EntityPreviewWidget] = None

        # ---- drag & drop state (inventory UI prototype) -----------------
        self._drag_active: bool = False
        self._drag_row: Any | None = None
        self._drag_ent: Any | None = None
        self._drag_src_owner_id: str | None = None
        self._drag_pos: tuple[int, int] | None = None  # panel-local cursor pos
        self._drag_label: str = ""
        self._drag_target_owner_id: str | None = None  # container/back target (left list)
        self._drag_target_kind: str | None = None  # "container" | "body_node" | None
        self._drag_target_node_id: str | None = None  # for body_node targets
        self._drag_hint: str = ""

        # Drag source metadata
        self._drag_src_kind: str | None = None  # "list" | "body_node"
        self._drag_src_slot_id: str | None = None  # node id if dragging an equipped item


        # Pending action requested by widgets (handled in Scene.handle_event where we have a manager)
        self._pending_double_open_index: int | None = None
        # Pending delayed single-click activation (for contextual double-click handling)
        self._pending_click_activate_index: int | None = None
        self._pending_click_activate_due_ms: int = 0
        # Pending delayed single-click activation for equipped body nodes
        # (allows double-click zoom to win over single-click context menu)
        self._pending_node_activate_nid: str | None = None
        self._pending_node_activate_due_ms: int = 0


        

        super().__init__(window_rect=window_rect, dim_background=True,
                         scale=0.78)
        self.overlay_layers = {"hud"}

        self._inherit_owner_visual_effects()

        # Cache owner's world position for source pixel calc.
        _owner = self._find_owner_entity()
        _pos = getattr(_owner, "pos", None)
        if _pos is not None:
            try:
                self._zoom_owner_world = (int(_pos[0]), int(_pos[1]))
            except Exception:
                self._zoom_owner_world = None

        self._refresh_rows()
        if self._list:
            self._list.set_items(self._rows)


        # Execute delayed single-click activation (containers only) once the double-click window expires.
        if self._pending_click_activate_index is not None and not self._closing and not bool(getattr(self, "_drag_active", False)):
            try:
                now = int(pygame.time.get_ticks())
                if now >= int(self._pending_click_activate_due_ms):
                    self._pending_mouse_activate = int(self._pending_click_activate_index)  # type: ignore[attr-defined]
                    self._pending_click_activate_index = None
                    self._pending_click_activate_due_ms = 0
            except Exception:
                self._pending_click_activate_index = None
                self._pending_click_activate_due_ms = 0

        # Execute delayed single-click activation (equipped body nodes) once the double-click window expires.
        if self._pending_node_activate_nid is not None and not self._closing and not bool(getattr(self, "_drag_active", False)):
            try:
                now = int(pygame.time.get_ticks())
                if now >= int(self._pending_node_activate_due_ms):
                    self._pending_node_activate = str(self._pending_node_activate_nid)  # type: ignore[attr-defined]
                    self._pending_node_activate_nid = None
                    self._pending_node_activate_due_ms = 0
            except Exception:
                self._pending_node_activate_nid = None
                self._pending_node_activate_due_ms = 0


        # -----------------------------------------------------------------
        # Flush pending widget actions here (not only in handle_event),
        # so delayed single-clicks work even if no further events arrive.
        # -----------------------------------------------------------------

        # Widget-triggered double-click open: needs manager to push InventoryScene.
        idx = getattr(self, "_pending_double_open_index", None)
        if idx is not None:
            try:
                self._pending_double_open_index = None
            except Exception:
                pass
            try:
                if bool(getattr(self, "allow_open_containers", True)):
                    self._open_container_from_index(int(idx), manager)
            except Exception:
                pass

        # Delayed single-click activation for the left inventory list (container action menu).
        midx = getattr(self, "_pending_mouse_activate", None)
        if midx is not None:
            try:
                self._pending_mouse_activate = None
            except Exception:
                pass
            try:
                # Provided by GeneralMenuScene
                self._on_list_activate(int(midx), manager)
            except Exception:
                pass

        # Widget-triggered double-click on a body node -> zoom in.
        pending_zoom = getattr(self, "_pending_body_zoom_in", None)
        if pending_zoom is not None:
            try:
                self._pending_body_zoom_in = None
            except Exception:
                pass
            try:
                self._body_zoom_in(str(pending_zoom))
            except Exception:
                pass

        # Delayed single-click on an equipped body node -> open context menu.
        pending_node = getattr(self, "_pending_node_activate", None)
        if pending_node is not None:
            try:
                self._pending_node_activate = None
            except Exception:
                pass
            try:
                node_id = str(pending_node)
                slot_id = self._canonical_body_slot_id(node_id)

                eq = self._equipped_entity_for_slot(slot_id)
                if eq is not None:
                    src_px, src_sz = self._node_glyph_screen_info(node_id, manager)
                    self._open_entity_context_menu(
                        eq,
                        manager,
                        source_px=src_px,
                        source_glyph_px=src_sz,
                        equipped_slot_id=slot_id,
                    )
            except Exception:
                pass


    # ---------------------------------------------------------------------
    # Effects inheritance (names only)
    # ---------------------------------------------------------------------

    def _owner_id(self) -> str:
        return self.owner_id or self.game.player_id


    def _find_container_targets(self, exclude_id: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Return a list of (owner_id, label) container inventories in the *same
        inventory space* as the currently viewed items.
        """
        space_owner_id = self._owner_id()
        inv = self.game.get_inventory(space_owner_id)
        candidates: list[tuple[str, str]] = []

        for ent in inv:
            tags = getattr(ent, "tags", {}) or {}
            if not tags.get("container"):
                continue
            ent_id = getattr(ent, "id", None)
            if exclude_id is not None and ent_id == exclude_id:
                continue
            name = getattr(ent, "name", None) or "Container"
            if ent_id is not None:
                candidates.append((str(ent_id), str(name)))

        return candidates

# ------------------------------------------------------------------
    # Drag & drop hooks (called by InventoryListWidget)
    # ------------------------------------------------------------------

    def _inv_drag_begin(self, *, row: Any, pos: tuple[int, int]) -> bool:
        """Begin dragging an inventory row. Return True if drag started."""
        if not bool(getattr(self, "allow_drag_drop", True)):
            return False
        ent = getattr(row, "ent", None)
        if ent is None:
            return False

        self._drag_active = True
        self._drag_row = row
        self._drag_ent = ent
        self._drag_src_owner_id = self._owner_id()
        self._drag_pos = (int(pos[0]), int(pos[1]))

        self._drag_src_kind = "list"
        self._drag_src_slot_id = None

        glyph = str(getattr(ent, "glyph", "?"))[:1]
        name = getattr(ent, "name", None) or getattr(row, "label", "Item")
        self._drag_label = f"{glyph} {name}"

        self._drag_target_owner_id = None
        self._drag_target_kind = None
        self._drag_target_node_id = None
        self._drag_hint = ""
        return True


    def _body_drag_begin(self, *, node_id: str, pos: tuple[int, int]) -> bool:
        """Begin dragging an equipped item out of a body node."""
        if not bool(getattr(self, "allow_drag_drop", True)):
            return False

        owner = self._preview_entity() if callable(getattr(self, "_preview_entity", None)) else self._find_owner_entity()
        if owner is None:
            return False

        owner_id = str(getattr(owner, "id", self._owner_id()))
        slot_id = self._canonical_body_slot_id(str(node_id))

        ent = None
        if hasattr(self.game, "get_equipped_in_slot"):
            try:
                ent = self.game.get_equipped_in_slot(owner_id, slot_id)
            except Exception:
                ent = None
        if ent is None:
            # Fallback: scan inventory tags
            try:
                inv = self.game.get_inventory(owner_id)
                for it in inv:
                    tags = getattr(it, "tags", {}) or {}
                    if str(tags.get("equipped_slot") or tags.get("equipped") or "") == slot_id:
                        ent = it
                        break
            except Exception:
                ent = None

        if ent is None:
            return False

        self._drag_active = True
        self._drag_row = None
        self._drag_ent = ent
        self._drag_src_owner_id = owner_id
        self._drag_pos = (int(pos[0]), int(pos[1]))

        self._drag_src_kind = "body_node"
        self._drag_src_slot_id = str(node_id)

        glyph = str(getattr(ent, "glyph", "?"))[:1]
        name = getattr(ent, "name", None) or "Item"
        self._drag_label = f"{glyph} {name}"

        self._drag_target_owner_id = None
        self._drag_target_kind = None
        self._drag_target_node_id = None
        self._drag_hint = ""
        return True

    def _body_zoom_in(self, nid: str) -> None:
        """
        Phase 1.5: schema-switch zoom step + optional camera-state animation.

        IMPORTANT: animation interpolates between two fully-defined camera states.
        No anatomy or LoD logic is allowed in the animation itself.
        """
        nid = str(nid)

        # Helper: obtain the current preview owner (matches widget logic).
        owner = getattr(self, "_preview_entity", None)
        owner = owner() if callable(owner) else getattr(self, "_find_owner_entity", lambda: None)()

        # If animation disabled or no region info yet, keep Phase 1 snap behavior.
        if not bool(getattr(self, "_body_zoom_anim_enabled", True)):
            self._body_zoom_stack.append(nid)
            self._body_zoom_focus_nid = None
            self._body_zoom_scale = 1.0
            self._body_zoom_anim = None
            return

        region_wh = getattr(self, "_last_body_cam_region", None)
        if not (isinstance(region_wh, tuple) and len(region_wh) == 2):
            self._body_zoom_stack.append(nid)
            self._body_zoom_focus_nid = None
            self._body_zoom_scale = 1.0
            self._body_zoom_anim = None
            return

        rw, rh = int(region_wh[0]), int(region_wh[1])
        if rw <= 0 or rh <= 0:
            self._body_zoom_stack.append(nid)
            self._body_zoom_focus_nid = None
            self._body_zoom_scale = 1.0
            self._body_zoom_anim = None
            return

        region_local = pygame.Rect(0, 0, rw, rh)

        def _camera_for_stack(stack: list[str]) -> tuple[tuple[float, float], float]:
            try:
                schema, embed_off_u, embed_scale_u = _resolve_body_view_for_zoom_path(owner, stack)
            except Exception:
                schema, embed_off_u, embed_scale_u = {"root": None, "nodes": {}}, (0.0, 0.0), 1.0
            pos_u = _embed_positions(_compute_body_positions(schema), embed_off_u, embed_scale_u)
            # Option A: only anchor to schema root at LoD 0; deeper views use bbox-centered framing.
            anchor_u = _get_schema_anchor_u(schema, pos_u) if len(stack) == 0 else None
            center_u, scale = _compute_body_graph_base_camera(
                self, pos_u, region_local, margin_frac=0.12, anchor_u=anchor_u
            )

            return (float(center_u[0]), float(center_u[1])), float(scale)

        # From-state: prefer last cached camera (what we were actually using on-screen).
        from_center_u = getattr(self, "_last_body_cam_center_u", None)
        from_scale = getattr(self, "_last_body_cam_scale", None)
        if (
            not (isinstance(from_center_u, tuple) and len(from_center_u) == 2)
            or not isinstance(from_scale, (int, float))
        ):
            from_center_u, from_scale = _camera_for_stack(list(getattr(self, "_body_zoom_stack", []) or []))

        # Apply the schema switch immediately (Phase 1 behavior), but animate the camera.
        from_stack = tuple(str(x) for x in (getattr(self, "_body_zoom_stack", []) or []))

        self._body_zoom_stack.append(nid)
        to_stack = tuple(str(x) for x in (getattr(self, "_body_zoom_stack", []) or []))

        self._body_zoom_focus_nid = None
        self._body_zoom_scale = 1.0

        to_center_u, to_scale = _camera_for_stack(list(getattr(self, "_body_zoom_stack", []) or []))

        dur_ms = int(getattr(self, "_body_zoom_anim_duration_ms", 220) or 220)
        start_ms = int(pygame.time.get_ticks())
        self._body_zoom_anim = (tuple(from_center_u), float(from_scale), tuple(to_center_u), float(to_scale), start_ms, dur_ms)
        try:
            self._body_zoom_fade = ("in", from_stack, to_stack, start_ms, dur_ms, None)
        except Exception:
            self._body_zoom_fade = None

    def _body_zoom_out(self) -> None:
        """Pop one zoom level (if any), with optional camera animation + adjacent-LoD fade."""

        stack_now = list(getattr(self, "_body_zoom_stack", []) or [])
        if not stack_now:
            return

        owner = getattr(self, "_preview_entity", None)
        owner = owner() if callable(owner) else getattr(self, "_find_owner_entity", lambda: None)()

        # Snap behavior
        if not bool(getattr(self, "_body_zoom_anim_enabled", True)):
            try:
                self._body_zoom_stack.pop()
            except Exception:
                return
            self._body_zoom_focus_nid = None
            self._body_zoom_scale = 1.0
            self._body_zoom_anim = None
            self._body_zoom_fade = None
            return

        region_wh = getattr(self, "_last_body_cam_region", None)
        if not (isinstance(region_wh, tuple) and len(region_wh) == 2):
            try:
                self._body_zoom_stack.pop()
            except Exception:
                return
            self._body_zoom_focus_nid = None
            self._body_zoom_scale = 1.0
            self._body_zoom_anim = None
            self._body_zoom_fade = None
            return

        rw, rh = int(region_wh[0]), int(region_wh[1])
        if rw <= 0 or rh <= 0:
            try:
                self._body_zoom_stack.pop()
            except Exception:
                return
            self._body_zoom_focus_nid = None
            self._body_zoom_scale = 1.0
            self._body_zoom_anim = None
            self._body_zoom_fade = None
            return

        region_local = pygame.Rect(0, 0, rw, rh)

        def _camera_for_stack(stack: list[str]) -> tuple[tuple[float, float], float]:
            try:
                chain = _resolve_body_view_chain_for_zoom_path(owner, stack)
                if not chain:
                    chain = [({"root": None, "nodes": {}}, (0.0, 0.0), 1.0)]
            except Exception:
                chain = [({"root": None, "nodes": {}}, (0.0, 0.0), 1.0)]

            schema, embed_off_u, embed_scale_u = chain[-1]
            pos_u = _embed_positions(_compute_body_positions(schema), embed_off_u, embed_scale_u)

            # IMPORTANT: match render camera logic (anchored fit) to avoid end-of-zoom snap.
            # Option A: only anchor to schema root at LoD 0; deeper views use bbox-centered framing.
            anchor_u = _get_schema_anchor_u(schema, pos_u) if len(stack) == 0 else None
            center_u, scale = _compute_body_graph_base_camera(
                self, pos_u, region_local, margin_frac=0.12, anchor_u=anchor_u
            )
            return (float(center_u[0]), float(center_u[1])), float(scale)


        # Capture "from" camera based on last render (fail-soft to computed).
        from_center_u = getattr(self, "_last_body_cam_center_u", None)
        from_scale = getattr(self, "_last_body_cam_scale", None)
        if (
            not (isinstance(from_center_u, tuple) and len(from_center_u) == 2)
            or not isinstance(from_scale, (int, float))
        ):
            from_center_u, from_scale = _camera_for_stack([str(x) for x in stack_now])

        # Capture outgoing (child) layer bundle for fade-out.
        from_stack = tuple(str(x) for x in stack_now)
        outgoing_layer: tuple[dict, tuple[float, float], float] | None = None
        try:
            ch = _resolve_body_view_chain_for_zoom_path(owner, stack_now)
            if ch:
                outgoing_layer = ch[-1]
        except Exception:
            outgoing_layer = None

        # Pop to the new active view, then animate toward its camera.
        try:
            self._body_zoom_stack.pop()
        except Exception:
            return

        to_stack = tuple(str(x) for x in (getattr(self, "_body_zoom_stack", []) or []))
        to_center_u, to_scale = _camera_for_stack([str(x) for x in (getattr(self, "_body_zoom_stack", []) or [])])

        self._body_zoom_focus_nid = None
        self._body_zoom_scale = 1.0

        start_ms = int(pygame.time.get_ticks())
        dur_ms = int(getattr(self, "_body_zoom_anim_duration_ms", 220) or 220)

        self._body_zoom_anim = (
            tuple(from_center_u),
            float(from_scale),
            tuple(to_center_u),
            float(to_scale),
            start_ms,
            dur_ms,
        )
        self._body_zoom_fade = ("out", from_stack, to_stack, start_ms, dur_ms, outgoing_layer)


    def _body_zoom_tick(self) -> None:
        anim = self._body_zoom_anim
        if anim is None:
            return
        try:
            _from_c, _from_s, _to_c, _to_s, start_ms, dur_ms = anim
            now = int(pygame.time.get_ticks())
            if dur_ms <= 0 or (now - int(start_ms)) >= int(dur_ms) + 5:
                self._body_zoom_anim = None
        except Exception:
            self._body_zoom_anim = None




    def _inv_drag_update(self, *, pos: tuple[int, int]) -> None:
        if not self._drag_active:
            return
        self._drag_pos = (int(pos[0]), int(pos[1]))
        self._update_drag_target()

    def _inv_drag_end(self, *, pos: tuple[int, int] | None) -> None:
        if not self._drag_active:
            return
        if pos is not None:
            self._drag_pos = (int(pos[0]), int(pos[1]))

        dragged_ent = self._drag_ent
        src_owner_id = self._drag_src_owner_id

        def _refresh_ui() -> None:
            try:
                self._refresh_rows()
                if self._list is not None:
                    self._list.items = self._rows
            except Exception:
                pass

        # ------------------------------------------------------------
        # Drop onto a body node => equip / re-slot
        # ------------------------------------------------------------
        if (
            self._drag_target_kind == "body_node"
            and self._drag_target_node_id
            and dragged_ent is not None
            and src_owner_id is not None
        ):
            try:
                ent_id = str(getattr(dragged_ent, "id", ""))
                if hasattr(self.game, "equip_item_to_slot"):
                    self.game.equip_item_to_slot(str(src_owner_id), ent_id, str(self._drag_target_node_id))
                else:
                    tags = getattr(dragged_ent, "tags", {}) or {}
                    tags["equipped_slot"] = str(self._drag_target_node_id)
                    try:
                        setattr(dragged_ent, "tags", tags)
                    except Exception:
                        pass
            except Exception:
                pass

            _refresh_ui()

            # Clear state and return
            self._drag_active = False
            self._drag_row = None
            self._drag_ent = None
            self._drag_src_owner_id = None
            self._drag_pos = None
            self._drag_label = ""
            self._drag_target_owner_id = None
            self._drag_target_kind = None
            self._drag_target_node_id = None
            self._drag_hint = ""
            self._drag_src_kind = None
            self._drag_src_slot_id = None
            return

        # ------------------------------------------------------------
        # Drop into left-list "unequip zone" => unequip (but keep in same inventory)
        # ------------------------------------------------------------
        if (
            self._drag_target_kind == "unequip_zone"
            and dragged_ent is not None
            and src_owner_id is not None
        ):
            try:
                if hasattr(self.game, "unequip_item"):
                    self.game.unequip_item(str(src_owner_id), str(getattr(dragged_ent, "id", "")))
                else:
                    tags = getattr(dragged_ent, "tags", {}) or {}
                    tags.pop("equipped_slot", None)
                    tags.pop("equipped", None)
                    try:
                        setattr(dragged_ent, "tags", tags)
                    except Exception:
                        pass
            except Exception:
                pass

            _refresh_ui()

            # Clear state and return (PREVENTS falling through into move_item_between_inventories)
            self._drag_active = False
            self._drag_row = None
            self._drag_ent = None
            self._drag_src_owner_id = None
            self._drag_pos = None
            self._drag_label = ""
            self._drag_target_owner_id = None
            self._drag_target_kind = None
            self._drag_target_node_id = None
            self._drag_hint = ""
            self._drag_src_kind = None
            self._drag_src_slot_id = None
            return


        # ------------------------------------------------------------
        # Drop onto a container/back in the left list => existing move
        # (If the item was equipped, unequip first.)
        # ------------------------------------------------------------
        if self._drag_target_owner_id and dragged_ent is not None and src_owner_id is not None:
            dest_owner_id = self._drag_target_owner_id

            # Unequip if needed
            try:
                tags = getattr(dragged_ent, "tags", {}) or {}
                if tags.get("equipped_slot") or tags.get("equipped"):
                    if hasattr(self.game, "unequip_item"):
                        self.game.unequip_item(str(src_owner_id), str(getattr(dragged_ent, "id", "")))
                    else:
                        tags.pop("equipped_slot", None)
                        tags.pop("equipped", None)
                        try:
                            setattr(dragged_ent, "tags", tags)
                        except Exception:
                            pass
            except Exception:
                pass

            src_inv = None
            try:
                src_inv = self.game.get_inventory(str(src_owner_id))
            except Exception:
                src_inv = None

            src_index = None
            if src_inv:
                try:
                    src_index = src_inv.index(dragged_ent)
                except Exception:
                    src_index = None

            if src_index is not None:
                if dest_owner_id == "__BACK__":
                    # Pop outward from current inventory
                    if str(src_owner_id) == str(getattr(self.game, "player_id", "")) and self.parent_owner_id is None:
                        # Root: treat as drop-to-ground (via existing drop API)
                        if hasattr(self.game, "drop_inventory_item"):
                            try:
                                self.game.drop_inventory_item(int(src_index))
                            except Exception:
                                pass
                    else:
                        out_owner = self.parent_owner_id or str(getattr(self.game, "player_id", ""))
                        if hasattr(self.game, "move_item_between_inventories"):
                            try:
                                self.game.move_item_between_inventories(str(src_owner_id), int(src_index), str(out_owner))
                            except Exception:
                                pass
                else:
                    if hasattr(self.game, "move_item_between_inventories"):
                        try:
                            self.game.move_item_between_inventories(str(src_owner_id), int(src_index), str(dest_owner_id))
                        except Exception:
                            pass

            _refresh_ui()

        else:
            # No valid target: cancel drag (keep item where it was).
            # This enables "drop in dead zone to cancel" and "drop back onto same slot".
            pass


        # Clear state
        self._drag_active = False
        self._drag_row = None
        self._drag_ent = None
        self._drag_src_owner_id = None
        self._drag_pos = None
        self._drag_label = ""
        self._drag_target_owner_id = None
        self._drag_target_kind = None
        self._drag_target_node_id = None
        self._drag_hint = ""
        self._drag_src_kind = None
        self._drag_src_slot_id = None




    def _update_drag_target(self) -> None:
        """Recompute which target (container row / back / body node / unequip-zone) is under the drag ghost.

        Priority:
          1) Right pane body-node targets
          2) Specific actionable left-row targets (container row, Back)
          3) Left-half deadzone => Unequip (only when dragging from a body node)
        """
        self._drag_target_owner_id = None
        self._drag_target_kind = None
        self._drag_target_node_id = None
        self._drag_hint = ""


        if not self._drag_active or self._drag_pos is None:
            return

        # -------------------------
        # 1) Prefer body-node targets when hovering the right pane.
        # -------------------------
        try:
            hovered_right = bool(getattr(self, "_right_panel_hovered", False))
        except Exception:
            hovered_right = False

        if hovered_right:
            nid = getattr(self._body_graph, "hovered_nid", None)
            if nid:
                self._drag_target_kind = "body_node"
                self._drag_target_node_id = self._canonical_body_slot_id(str(nid))
                sn = getattr(self._drag_ent, "name", None) or "Item"
                dn = _display_body_node_label(str(nid))
                self._drag_hint = f"Equip {sn} to {dn}"
                return

        # -------------------------
        # 2) If we're over a *specific* left list row that is actionable, target it.
        #    (This must run BEFORE the generic unequip deadzone.)
        # -------------------------
        if self._list is not None:
            try:
                idx = self._list.pick_index_at(self._drag_pos) if hasattr(self._list, "pick_index_at") else None
            except Exception:
                idx = None

            if idx is not None:
                try:
                    idx_i = int(idx)
                except Exception:
                    idx_i = None

                if idx_i is not None and 0 <= idx_i < len(self._rows):
                    row = self._rows[idx_i]
                    ent = getattr(row, "ent", None)

                    # 'Back' row: pop outward / drop-to-ground behavior
                    if ent is None and str(getattr(row, "label", "")).strip().lower() == "back":
                        self._drag_target_kind = "container"
                        self._drag_target_owner_id = "__BACK__"
                        sn = getattr(self._drag_ent, "name", None) or "Item"

                        # If we're at the base inventory depth, "Back" means dropping to terrain.
                        is_root = (
                            str(self._owner_id()) == str(getattr(self.game, "player_id", ""))
                            and self.parent_owner_id is None
                        )
                        self._drag_hint = f"Drop {sn}" if is_root else f"Take {sn}"
                        return


                    if ent is not None:
                        tags = getattr(ent, "tags", {}) or {}
                        if bool(tags.get("container")):
                            ent_id = getattr(ent, "id", None)
                            if ent_id is not None:
                                # Don't allow dropping an item into itself.
                                if self._drag_ent is not None and getattr(self._drag_ent, "id", None) == ent_id:
                                    pass
                                else:
                                    # Validate as a target (exclude recursive pitfalls)
                                    try:
                                        ok = (
                                            str(ent_id) in set(
                                                str(cid) for cid, _ in self._find_container_targets(
                                                    exclude_id=str(getattr(self._drag_ent, "id", ""))
                                                )
                                            )
                                        )
                                    except Exception:
                                        ok = True

                                    if ok:
                                        self._drag_target_kind = "container"
                                        self._drag_target_owner_id = str(ent_id)
                                        dn = getattr(ent, "name", None) or "Container"
                                        sn = getattr(self._drag_ent, "name", None) or "Item"
                                        self._drag_hint = f"Put {sn} into {dn}"
                                        return

        # -------------------------
        # 3) Generic "unequip zone" deadzone: left half of the panel
        #    (Only applies when dragging from a body node.)
        # -------------------------
        if self._drag_src_kind == "body_node":
            try:
                panel = getattr(self, "_panel", None)
                if panel is not None:
                    pw, ph = panel.get_size()
                elif getattr(self, "root", None) is not None and getattr(self.root, "rect", None) is not None:
                    pw, ph = int(self.root.rect.w), int(self.root.rect.h)
                else:
                    pw, ph = 0, 0

                if pw > 0:
                    mx = int(self._drag_pos[0])
                    if mx <= int(pw * 0.5):
                        self._drag_target_kind = "unequip_zone"
                        self._drag_target_owner_id = None
                        sn = getattr(self._drag_ent, "name", None) or "Item"
                        self._drag_hint = f"Unequip {sn}"
                        return
            except Exception:
                pass

        # Otherwise: no target (drop cancels)
        return



    

    
    def _open_container_from_index(self, index: int, manager: "SceneManager") -> None:
        """Open the container at the given list index directly (folder-style)."""
        try:
            rows = list(self._rows or [])
        except Exception:
            rows = []
        if index < 0 or index >= len(rows):
            return
        row = rows[index]
        ent = getattr(row, "ent", None)
        if ent is None:
            return
        tags = getattr(ent, "tags", {}) or {}
        if not tags.get("container"):
            return

        nested_owner_id = getattr(ent, "id", None)
        if nested_owner_id is None:
            return

        # When opening a nested inventory, make the new panel "emerge" from the glyph
        # that represents this item in the current list.
        src_px, src_sz = self._row_glyph_screen_info(index, manager)

        manager.push_scene(
            InventoryScene(
                self.game,
                owner_id=str(nested_owner_id),
                parent_owner_id=self._owner_id(),
                title=getattr(ent, "name", None) or "Container",
                base_effects=list(self.visual_effects),
                source_px=src_px,
                source_glyph_px=src_sz,
                stack_depth=self.stack_depth + 1,
                animate_affine=self.animate_affine,
            )
        )


    @staticmethod
    def _is_berry_from_tags(tags: dict) -> bool:
        return bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }

    def _find_owner_entity(self):
        owner_id = self._owner_id()

        level = self.game._level()
        if level is not None:
            ent = level.entities.get(owner_id) or level.actors.get(owner_id)
            if ent is not None:
                return ent

        for cand in getattr(self.game, "player_inventory", []):
            if getattr(cand, "id", None) == owner_id:
                return cand

        for inv_list in getattr(self.game, "inventories", {}).values():
            for cand in inv_list:
                if getattr(cand, "id", None) == owner_id:
                    return cand

        return None


    def _preview_entity(self):
        """Entity shown in the right preview pane.

        - Inventory mode: keep the *owner/player* stable on the right, while the left list
          is the owner's inventory.
        - Look mode (and other future browse modes): the right pane reflects the currently
          selected row (so you can cycle through multiple things on a tile, etc.).

        Falls back to the owner entity if nothing is selected / row has no entity.
        """
        # Inventory screen: always preview the owner (player / container we're inside).
        try:
            if str(getattr(self, "mode", "inventory")) == "inventory":
                return self._find_owner_entity()
        except Exception:
            # Fail-soft: if mode is weird, treat as inventory.
            return self._find_owner_entity()

        # Otherwise (e.g. look screen): follow selection.
        try:
            if self._list is not None and getattr(self._list, "selected_index", None) is not None:
                sel = int(self._list.selected_index)
            else:
                sel = int(getattr(self, "selected_idx", 0))
        except Exception:
            sel = 0

        try:
            if 0 <= sel < len(self._rows):
                ent = getattr(self._rows[sel], "ent", None)
                if ent is not None:
                    return ent
        except Exception:
            pass

        return self._find_owner_entity()

    def _inherit_owner_visual_effects(self) -> None:
        ent = self._find_owner_entity()
        if ent is None:
            return
        self.visual_effects = concat_effect_names(self.visual_effects, effect_names_from_obj(ent))

    # ---------------------------------------------------------------------
    # Inventory rows
    # ---------------------------------------------------------------------

    def _refresh_rows(self) -> None:
        from edgecaster.systems.inventory import get_quantity

        owner_id = self._owner_id()
        inv = self.game.get_inventory(owner_id)

        rows: list[_InvRow] = []
        if inv:
            for ent in inv:
                # Equipped items remain in the inventory registry, but are hidden from the
                # left list; they show up on the body graph instead.
                tags = getattr(ent, "tags", {}) or {}
                equipped_slot = tags.get("equipped_slot") or tags.get("equipped")
                if equipped_slot:
                    continue

                name = getattr(ent, "name", None) or "(unnamed item)"
                glyph = getattr(ent, "glyph", None) or "?"
                # Show quantity for stacked items
                qty = get_quantity(ent)
                qty_suffix = f" ({qty})" if qty > 1 else ""
                rows.append(_InvRow(f"{str(glyph)[:1]}  {name}{qty_suffix}", ent=ent))
        else:
            rows.append(_InvRow("(Empty)", ent=None))

        rows.append(_InvRow("Back", ent=None))
        self._rows = rows


    # ---------------------------------------------------------------------
    # GeneralMenuScene hooks
    # ---------------------------------------------------------------------

    def get_ascii_art(self) -> str:
        return ""

    def get_body_text(self) -> Optional[str]:
        return None

    def get_menu_items(self) -> list[Any]:
        self._refresh_rows()
        return list(self._rows)

    def wants_wrapped_choices(self) -> bool:
        return False
    # -----------------------------------------------------------------
    # Reverse-zoom pop interception (SceneManager.pop_scene hook)
    # -----------------------------------------------------------------

    def begin_pop(self, manager: "SceneManager") -> bool:
        """Start the closing (reverse) zoom animation instead of popping immediately."""
        if self._closing:
            return True
        self._closing = True
        self._close_elapsed = 0
        return True




    def _screen_pos_to_panel_logical(self, screen_pos: tuple[int, int], manager: "SceneManager") -> tuple[int, int] | None:
        """Convert renderer/screen mouse pos into *logical panel* coords, undoing the current VisualProfile."""
        try:
            self._ensure_window_rect(manager)
            if self.window_rect is None:
                return None

            sx, sy = int(screen_pos[0]), int(screen_pos[1])

            # First get mouse in window-local coords (0..w, 0..h)
            wx = float(sx - int(self.window_rect.x))
            wy = float(sy - int(self.window_rect.y))

            # If we're outside the window, bail.
            if wx < 0 or wy < 0 or wx >= self.window_rect.w or wy >= self.window_rect.h:
                return None

            # We need the same visual used for drawing this frame.
            panel = self._get_panel(manager)
            pw, ph = panel.get_width(), panel.get_height()

            vx = float(self.window_rect.w) / float(max(1, pw))
            vy = float(self.window_rect.h) / float(max(1, ph))
            visual = self._current_visual_profile(logical_to_window_scale_x=vx, logical_to_window_scale_y=vy)

            # Invert apply_visual_panel transform (mirror of _project_point_window_to_screen math)
            cx = float(self.window_rect.w) * 0.5
            cy = float(self.window_rect.h) * 0.5

            # Undo translation (center + offsets)
            dx = wx - (cx + float(getattr(visual, "offset_x", 0.0)))
            dy = wy - (cy + float(getattr(visual, "offset_y", 0.0)))

            # Undo rotation FIRST (reverse of forward order: scale -> flip -> rotate)
            ang = float(getattr(visual, "angle", 0.0))
            if ang:
                rad = math.radians(ang)
                c = math.cos(rad)
                s = math.sin(rad)
                # rotate by -ang
                dx, dy = (dx * c - dy * s, dx * s + dy * c)

            # Undo flips
            if getattr(visual, "flip_x", False):
                dx = -dx
            if getattr(visual, "flip_y", False):
                dy = -dy

            # Undo scale LAST
            scx = float(getattr(visual, "scale_x", 1.0))
            scy = float(getattr(visual, "scale_y", 1.0))
            if abs(scx) < 1e-6 or abs(scy) < 1e-6:
                return None
            dx /= scx
            dy /= scy



            # Back to window-local “panel_to_blit” coords
            px_win = cx + dx
            py_win = cy + dy

            # Map window-local coords back to *logical panel* coords
            lx = px_win * float(pw) / float(max(1, self.window_rect.w))
            ly = py_win * float(ph) / float(max(1, self.window_rect.h))

            return (int(round(lx)), int(round(ly)))
        except Exception:
            return None






    def handle_event(self, event, manager: "SceneManager") -> None:
        # While closing, swallow inputs so the selection doesn't jitter mid-collapse.
        if self._closing:
            return

        # Phase 5: Esc zooms out of body-graph depth first (only if currently zoomed).
        try:
            if event.type == pygame.KEYDOWN and getattr(event, "key", None) == pygame.K_ESCAPE:
                if getattr(self, "_body_zoom_stack", None):
                    if len(self._body_zoom_stack) > 0:
                        self._body_zoom_out()
                        return
        except Exception:
            pass


        # Any keyboard/mouse interaction should cancel a pending delayed click activation.
        if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            self._pending_click_activate_index = None
            self._pending_click_activate_due_ms = 0

        # Track last mouse position in *logical panel coords* (unproject through the current VisualProfile).
        try:
            if hasattr(event, "pos") and event.pos is not None:
                mp = self._screen_pos_to_panel_logical((int(event.pos[0]), int(event.pos[1])), manager)
                if mp is not None:
                    self._mouse_pos = mp
        except Exception:
            pass

        # While dragging: update drag ghost from the scene's canonical panel-logical mouse.
        # IMPORTANT: drag can be initiated from widgets (which may deliver panel-logical pos),
        # but scene events start as screen coords. Mixing these causes consistent hitbox drift.
        if self._drag_active and event.type == pygame.MOUSEMOTION:
            try:
                mp2 = getattr(self, "_mouse_pos", None)
                if mp2 is not None:
                    self._inv_drag_update(pos=mp2)
            except Exception:
                pass





        try:
            if hasattr(event, "pos") and event.pos is not None:
                self._mouse_screen = (int(event.pos[0]), int(event.pos[1]))
        except Exception:
            pass



        # --- NEW: detect drag-end that happens during widget dispatch ---
        was_drag_active = bool(self._drag_active)

        super().handle_event(event, manager)

        # If a drag ended during super().handle_event (i.e. inside widget code),
        # force a "release" + hover refresh immediately so left-list yellow tracking resumes.
        if was_drag_active and (not bool(self._drag_active)):
            try:
                mp3 = getattr(self, "_mouse_pos", None)
                if mp3 is not None and getattr(self, "root", None) is not None:
                    panel = self._get_panel(manager)
                    ctx = WidgetContext(surface=panel, game=self.game, scene=self, renderer=manager.renderer)

                    # 1) Force-release any widget-level pressed/capture state (ListWidget hover can freeze without this).
                    fake_up = pygame.event.Event(
                        pygame.MOUSEBUTTONUP,
                        {"pos": mp3, "button": 1},
                    )
                    try:
                        self.root.handle_event(fake_up, ctx)
                    except Exception:
                        pass

                    # 2) Then re-run hover with a clean "no buttons pressed" motion.
                    fake_motion = pygame.event.Event(
                        pygame.MOUSEMOTION,
                        {"pos": mp3, "rel": (0, 0), "buttons": (0, 0, 0)},
                    )
                    try:
                        self.root.handle_event(fake_motion, ctx)
                    except Exception:
                        pass
            except Exception:
                pass


        # Widget-triggered double-click open: handled here because we need the manager
        # to push the nested InventoryScene.
        idx = getattr(self, "_pending_double_open_index", None)
        if idx is not None:
            try:
                self._pending_double_open_index = None
            except Exception:
                pass
            try:
                if bool(getattr(self, "allow_open_containers", True)):
                    self._open_container_from_index(int(idx), manager)
            except Exception:
                pass

        # Phase 5: Widget-triggered double-click on a body node -> zoom in.
        pending_zoom = getattr(self, "_pending_body_zoom_in", None)
        if pending_zoom is not None:
            # Zoom-in should win over any pending single-click activation (context menus).
            try:
                self._pending_node_activate_nid = None
                self._pending_node_activate_due_ms = 0
                self._pending_click_activate_index = None
                self._pending_click_activate_due_ms = 0
            except Exception:
                pass
            try:
                self._pending_body_zoom_in = None
            except Exception:
                pass
            try:
                self._body_zoom_in(str(pending_zoom))
            except Exception:
                pass



        # Widget-triggered click on an equipped body node: open context menu immediately.
        pending_node = getattr(self, "_pending_node_activate", None)
        if pending_node is not None:
            try:
                self._pending_node_activate = None
            except Exception:
                pass
            try:
                node_id = str(pending_node)
                slot_id = self._canonical_body_slot_id(node_id)

                eq = self._equipped_entity_for_slot(slot_id)
                if eq is not None:
                    src_px, src_sz = self._node_glyph_screen_info(node_id, manager)
                    self._open_entity_context_menu(
                        eq,
                        manager,
                        source_px=src_px,
                        source_glyph_px=src_sz,
                        equipped_slot_id=slot_id,
                    )

            except Exception:
                pass


        return


    def on_back(self, manager: "SceneManager") -> None:
        if hasattr(manager, "pop_scene"):
            manager.pop_scene()
        else:
            manager.set_scene(None)

    # ---------------------------------------------------------------------
    # Context menus / equip helpers
    # ---------------------------------------------------------------------


    def _canonical_body_slot_id(self, node_or_slot_id: str) -> str:
        """
        Convert a *local* node id (e.g. 'wrist', 'fingernail') into a unique, stable
        equip-slot id by prefixing the current zoom stack, e.g.:
            ['arm', 'hand', 'finger2'] + 'fingernail' -> 'arm/hand/finger2/fingernail'

        If the caller already passed a full slot path (contains '/'), leave it alone.
        """
        s = str(node_or_slot_id or "")
        if not s:
            return s
        if "/" in s:
            return s
        stack = [str(x) for x in (getattr(self, "_body_zoom_stack", []) or [])]
        return "/".join(stack + [s]) if stack else s


    def _body_slot_targets(self) -> list[tuple[str, str]]:
        """Return [(node_id, display_label), ...] for the current owner's body schema."""
        owner = None
        try:
            owner = self._find_owner_entity()
        except Exception:
            owner = None
        if owner is None:
            try:
                owner = self._preview_entity()
            except Exception:
                owner = None
        if owner is None:
            return []
        try:
            schema = _resolve_body_schema_for_zoom_path(owner, getattr(self, "_body_zoom_stack", [])) or {}
        except Exception:
            schema = {}
        nodes = schema.get("nodes", {}) or {}
        out: list[tuple[str, str]] = []
        try:
            stack = getattr(self, "_body_zoom_stack", []) or []
            for nid in nodes.keys():
                sn = str(nid)
                # Unique slot id = zoom_path + local nid
                slot_id = "/".join([str(x) for x in list(stack) + [sn]]) if stack else sn
                out.append((slot_id, _display_body_node_label(sn)))

        except Exception:
            return []
        return out

    def _equipped_entity_for_slot(self, slot_id: str):
        """Best-effort: return the entity equipped in the given slot (or None)."""
        owner_id = str(self._owner_id())
        if hasattr(self.game, "get_equipped_in_slot"):
            try:
                return self.game.get_equipped_in_slot(owner_id, str(slot_id))
            except Exception:
                pass
        try:
            inv = self.game.get_inventory(owner_id) or []
        except Exception:
            inv = []
        for it in inv:
            try:
                tags = getattr(it, "tags", {}) or {}
                if str(tags.get("equipped_slot") or tags.get("equipped") or "") == str(slot_id):
                    return it
            except Exception:
                continue
        return None

    def _node_glyph_screen_info(
        self, node_id: str, manager: "SceneManager"
    ) -> tuple[tuple[int, int] | None, int | None]:
        """Return (screen_px, approx_screen_size_px) for the glyph drawn inside a body node."""
        try:
            renderer = manager.renderer
        except Exception:
            return (None, None)
        self._ensure_window_rect(manager)
        if self.window_rect is None or self._body_graph is None:
            return (None, None)
        panel = self._get_panel(manager)
        try:
            # Layout widgets into the panel surface so self._body_graph.rect is up-to-date.
            self.draw_panel(panel, renderer, manager)
        except Exception:
            pass
        region = pygame.Rect(getattr(self._body_graph, "rect", pygame.Rect(0, 0, 0, 0)))
        if region.w <= 0 or region.h <= 0:
            return (None, None)
        owner = None
        try:
            owner = self._find_owner_entity()
        except Exception:
            owner = None
        if owner is None:
            return (None, None)
        try:
            schema, embed_off_u, embed_scale_u = _resolve_body_view_for_zoom_path(
                owner, getattr(self, "_body_zoom_stack", [])
            )
        except Exception:
            schema, embed_off_u, embed_scale_u = {"root": None, "nodes": {}}, (0.0, 0.0), 1.0
        pos_u = _embed_positions(_compute_body_positions(schema), embed_off_u, embed_scale_u)
        pos_px, scale = _map_positions_to_rect(pos_u, pygame.Rect(0, 0, region.w, region.h))
        nid = str(node_id)
        if nid not in pos_px:
            return (None, None)
        node_size = int(max(18, min(56, scale * 0.45)))
        cx, cy = pos_px[nid]
        gx = float(region.x + int(cx))
        gy = float(region.y + int(cy))
        pw, ph = panel.get_size()
        sx = float(self.window_rect.w) / float(max(1, pw))
        sy = float(self.window_rect.h) / float(max(1, ph))
        win_pt = (gx * sx, gy * sy)
        visual = self._current_visual_profile(logical_to_window_scale_x=sx, logical_to_window_scale_y=sy)
        screen_px = self._project_point_window_to_screen(win_pt, visual)
        try:
            base_px = int(node_size * 0.86)
            cell_w_win = float(base_px) * sx
            cell_h_win = float(base_px) * sy
            sw = cell_w_win * float(abs(getattr(visual, "scale_x", 1.0)))
            sh = cell_h_win * float(abs(getattr(visual, "scale_y", 1.0)))
            size_px = int(round(max(sw, sh)))
            size_px = max(4, min(1024, size_px))
        except Exception:
            size_px = None
        return (screen_px, size_px)

    def _open_container_from_entity(
        self,
        ent,
        manager: "SceneManager",
        *,
        source_px: tuple[int, int] | None = None,
        source_glyph_px: int | None = None,
    ) -> None:
        """Open the given container entity directly (folder-style)."""
        try:
            nested_owner_id = getattr(ent, "id", None)
            if nested_owner_id is None:
                return
        except Exception:
            return
        manager.push_scene(
            InventoryScene(
                self.game,
                owner_id=str(nested_owner_id),
                parent_owner_id=self._owner_id(),
                title=getattr(ent, "name", None) or "Container",
                base_effects=list(self.visual_effects),
                source_px=source_px,
                source_glyph_px=source_glyph_px,
                stack_depth=self.stack_depth + 1,
                animate_affine=self.animate_affine,
            )
        )

    def _open_entity_context_menu(
        self,
        ent,
        manager: "SceneManager",
        *,
        source_px: tuple[int, int] | None = None,
        source_glyph_px: int | None = None,
        equipped_slot_id: str | None = None,
    ) -> bool:
        """Open the standard context menu for an entity.

        If equipped_slot_id is provided, show 'Unequip' (and do not show 'Equip...').
        """
        if ent is None:
            return False
        tags = getattr(ent, "tags", {}) or {}
        is_container = bool(tags.get("container"))
        is_berry = self._is_berry_from_tags(tags)
        choices: list[str] = []
        owner_id = self._owner_id()
        container_targets = self._find_container_targets(exclude_id=getattr(ent, "id", None))
        if equipped_slot_id:
            choices.append("Unequip")
        if owner_id == self.game.player_id:
            choices.append("Drop")
            if is_berry or is_container:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")
        else:
            choices.append("Take")
            if is_berry or is_container:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")
        if not equipped_slot_id:
            if self._body_slot_targets():
                choices.append("Equip...")
        if is_container and bool(getattr(self, "allow_open_containers", True)):
            choices.append("Open")
        if not choices:
            return False

        def _handle_choice(choice_idx: int, mgr: "SceneManager") -> None:
            if choice_idx < 0 or choice_idx >= len(choices):
                return
            choice = choices[choice_idx]
            current_owner_id = self._owner_id()
            cur_inv = None
            try:
                cur_inv = self.game.get_inventory(current_owner_id)
            except Exception:
                cur_inv = None
            cur_ent = ent
            cur_index = 0
            if cur_inv:
                try:
                    cur_index = cur_inv.index(ent)
                except Exception:
                    try:
                        cur_index = int(self.selected_idx() or 0)
                    except Exception:
                        cur_index = 0
                try:
                    if 0 <= cur_index < len(cur_inv):
                        cur_ent = cur_inv[cur_index]
                except Exception:
                    cur_ent = ent
            cur_tags = getattr(cur_ent, "tags", {}) or {}
            cur_is_container = bool(cur_tags.get("container"))
            cur_is_berry = self._is_berry_from_tags(cur_tags)

            def _refresh_ui() -> None:
                try:
                    self._refresh_rows()
                    if self._list is not None:
                        self._list.items = self._rows
                except Exception:
                    pass
 
            def _ensure_unequipped(item_ent) -> None:
                """If item is equipped on this owner, clear equip state before moving/dropping/eating."""
                try:
                    t = getattr(item_ent, "tags", {}) or {}
                    if t.get("equipped_slot") or t.get("equipped") or equipped_slot_id:
                        if hasattr(self.game, "unequip_item"):
                            self.game.unequip_item(str(current_owner_id), str(getattr(item_ent, "id", "")))
                        else:
                            t.pop("equipped_slot", None)
                            t.pop("equipped", None)
                            try:
                                setattr(item_ent, "tags", t)
                            except Exception:
                                pass
                except Exception:
                    pass

            if choice == "Unequip":
                try:
                    if hasattr(self.game, "unequip_item"):
                        self.game.unequip_item(str(current_owner_id), str(getattr(cur_ent, "id", "")))
                    else:
                        t = getattr(cur_ent, "tags", {}) or {}
                        t.pop("equipped_slot", None)
                        t.pop("equipped", None)
                        try:
                            setattr(cur_ent, "tags", t)
                        except Exception:
                            pass
                except Exception:
                    pass
                _refresh_ui()
                return

            if choice == "Drop" and current_owner_id == self.game.player_id:
                _ensure_unequipped(cur_ent)

                from edgecaster.systems.inventory import get_quantity, drop_inventory_item_qty
                qty = get_quantity(cur_ent)

                if qty > 1:
                    # Show quantity prompt for stacked items
                    def on_drop_qty(amount: int) -> None:
                        if amount > 0:
                            try:
                                drop_inventory_item_qty(self.game, cur_index, amount)
                            except Exception:
                                pass
                        _refresh_ui()

                    mgr.push_scene(QuantityPromptScene(
                        self.game,
                        qty,
                        on_drop_qty,
                        title="Drop how many?",
                    ))
                    return

                # Single item - drop directly
                if hasattr(self.game, "drop_inventory_item"):
                    try:
                        self.game.drop_inventory_item(cur_index)
                    except Exception:
                        pass
                _refresh_ui()
                return

            if choice == "Take" and current_owner_id != self.game.player_id:
                _ensure_unequipped(cur_ent)

                dest_owner_id = self.parent_owner_id or self.game.player_id
                if hasattr(self.game, "move_item_between_inventories"):
                    try:
                        self.game.move_item_between_inventories(
                            current_owner_id,
                            cur_index,
                            dest_owner_id,
                        )
                    except Exception:
                        pass
                _refresh_ui()
                return

            if choice == "Put into...":
                targets = self._find_container_targets(exclude_id=getattr(cur_ent, "id", None))
                if not targets:
                    return
                target_labels = [label for (_oid, label) in targets]

                def on_target_choice(target_idx: int, mgr2: "SceneManager") -> None:
                    if target_idx < 0 or target_idx >= len(targets):
                        return
                    dest_owner_id, _dest_label = targets[target_idx]
                    src_owner_id = self._owner_id()

                    _ensure_unequipped(cur_ent)


                    src_inv = None
                    try:
                        src_inv = self.game.get_inventory(src_owner_id)
                    except Exception:
                        src_inv = None
                    if not src_inv:
                        return
                    try:
                        src_index = src_inv.index(cur_ent)
                    except Exception:
                        src_index = cur_index
                    if not (0 <= src_index < len(src_inv)):
                        return
                    if hasattr(self.game, "move_item_between_inventories"):
                        try:
                            self.game.move_item_between_inventories(
                                src_owner_id,
                                src_index,
                                dest_owner_id,
                            )
                        except Exception:
                            pass
                    _refresh_ui()

                mgr.push_scene(
                    UrgentMessageScene(
                        self.game,
                        "",
                        title="Put into which container?",
                        choices=target_labels,
                        on_choice=on_target_choice,
                        back_confirms=False,
                    )
                )
                return

            if choice == "Equip...":
                targets = self._body_slot_targets()
                if not targets:
                    return
                target_labels = [lbl for (_nid, lbl) in targets]

                def on_slot_choice(target_idx: int, mgr2: "SceneManager") -> None:
                    if target_idx < 0 or target_idx >= len(targets):
                        return
                    slot_id, _lbl = targets[target_idx]
                    try:
                        ent_id = str(getattr(cur_ent, "id", ""))
                        if hasattr(self.game, "equip_item_to_slot"):
                            self.game.equip_item_to_slot(str(current_owner_id), ent_id, str(slot_id))
                        else:
                            t = getattr(cur_ent, "tags", {}) or {}
                            t["equipped_slot"] = str(slot_id)
                            try:
                                setattr(cur_ent, "tags", t)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    _refresh_ui()

                mgr.push_scene(
                    UrgentMessageScene(
                        self.game,
                        "",
                        title="Equip to which slot?",
                        choices=target_labels,
                        on_choice=on_slot_choice,
                        back_confirms=False,
                    )
                )
                return

            if choice == "Open" and cur_is_container and bool(getattr(self, "allow_open_containers", True)):
                self._open_container_from_entity(cur_ent, mgr, source_px=source_px, source_glyph_px=source_glyph_px)
                return

            if choice == "Eat":
                # If the item is equipped, unequip first (so consuming doesn't leave ghost equip state).
                _ensure_unequipped(cur_ent)

                # Re-fetch inventory in case it changed while popup was open / unequip changed refs.
                current_owner_id = self._owner_id()
                try:
                    src_inv = self.game.get_inventory(current_owner_id) or []
                except Exception:
                    src_inv = []

                # Re-resolve the entity by id if possible (robust across object replacement).
                ent_id = str(getattr(cur_ent, "id", ""))
                src_index: int | None = None
                try:
                    for i, it in enumerate(src_inv):
                        if str(getattr(it, "id", "")) == ent_id:
                            src_index = int(i)
                            cur_ent = it
                            break
                except Exception:
                    src_index = None

                cur_tags = getattr(cur_ent, "tags", {}) or {}
                cur_is_container = bool(cur_tags.get("container"))
                cur_is_berry = self._is_berry_from_tags(cur_tags)

                # ------------------------------------------------------------
                # Eat container (inventory) recursively (the "funny trick")
                # ------------------------------------------------------------
                if cur_is_container and not cur_is_berry:
                    if src_index is None or not (0 <= src_index < len(src_inv)):
                        _refresh_ui()
                        return

                    # 1) Remove the container item itself from the current inventory list
                    eaten_ent = src_inv.pop(int(src_index))
                    eaten_id = getattr(eaten_ent, "id", None)

                    # 2) Walk the inventory tree, collecting effects from:
                    #    - the container itself
                    #    - every item inside it
                    #    - every nested container and its contents, recursively
                    all_effects: list[str] = []
                    all_effects = concat_effect_names(all_effects, effect_names_from_obj(eaten_ent))

                    def _consume_inventory_tree(owner_id: str, visited: set[str]) -> None:
                        if not owner_id or owner_id in visited:
                            return
                        visited.add(owner_id)

                        inv_map = getattr(self.game, "inventories", None)
                        if not isinstance(inv_map, dict):
                            return

                        inv_list = inv_map.get(owner_id)
                        if not inv_list:
                            inv_map.pop(owner_id, None)
                            return

                        # Iterate a snapshot because we'll delete the mapping at the end.
                        for child in list(inv_list):
                            nonlocal all_effects
                            all_effects = concat_effect_names(all_effects, effect_names_from_obj(child))

                            child_id = getattr(child, "id", None)
                            child_tags = getattr(child, "tags", {}) or {}
                            child_is_container = bool(child_tags.get("container"))

                            if child_is_container and child_id is not None and str(child_id) in inv_map:
                                _consume_inventory_tree(str(child_id), visited)

                        # Finally delete this inventory list (consumes its contents)
                        inv_map.pop(owner_id, None)

                    if eaten_id is not None and hasattr(self.game, "inventories"):
                        _consume_inventory_tree(str(eaten_id), set())

                    # 3) Apply ALL collected effects globally (stacking)
                    if all_effects:
                        try:
                            existing = list(getattr(mgr.renderer.visual_fx, "global_effects", []) or [])
                        except Exception:
                            existing = []
                        try:
                            mgr.set_global_visual_effects(concat_effect_names(existing, all_effects))
                        except Exception:
                            pass

                    # 4) Log
                    if hasattr(self.game, "log") and hasattr(self.game.log, "add"):
                        self.game.log.add("You're not sure if you should have eaten that inventory...")
                        seen: set[str] = set()
                        for eff in all_effects:
                            if eff in seen:
                                continue
                            seen.add(eff)
                            self.game.log.add(f"You feel {eff}.")

                    _refresh_ui()
                    return

                # ------------------------------------------------------------
                # Eat berries (and other explicitly edible items)
                # ------------------------------------------------------------
                if cur_is_berry or cur_is_container:
                    # Prefer the game's handler if it exists.
                    if hasattr(self.game, "eat_inventory_item"):
                        fn = self.game.eat_inventory_item
                        tried = False

                        # 1) (owner_id, ent_id)
                        try:
                            fn(current_owner_id, ent_id)
                            tried = True
                        except TypeError:
                            pass
                        except Exception:
                            tried = True

                        # 2) (owner_id, index)
                        if not tried and src_index is not None:
                            try:
                                fn(current_owner_id, int(src_index))
                                tried = True
                            except TypeError:
                                pass
                            except Exception:
                                tried = True

                        # 3) (index)
                        if not tried and src_index is not None:
                            try:
                                fn(int(src_index))
                                tried = True
                            except TypeError:
                                pass
                            except Exception:
                                tried = True

                    _refresh_ui()
                    return


                _refresh_ui()
                return


        manager.push_scene(
            UrgentMessageScene(
                self.game,
                "",
                title=getattr(ent, "name", None) or "",
                choices=choices,
                on_choice=_handle_choice,
                back_confirms=False,
            )
        )

        return True


    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        """
        Selecting an item opens a context submenu:
          - Player inventory: Drop / Eat / Put into...
          - Other inventories: Take / Eat / Put into...
          - If item is a container: Open
        """
        self._refresh_rows()
        rows = self._rows
        if index < 0 or index >= len(rows):
            return False

        row = rows[index]

        # 'Back' (or the '(Empty)' row) → close inventory
        if row.ent is None:
            return self.on_back(manager)

        ent = row.ent
        tags = getattr(ent, "tags", {}) or {}


        # LOOK/INSPECT mode: read-only info popup (no inventory actions).
        if not bool(getattr(self, "allow_item_actions", True)):
            info = describe_entity_for_look(ent)
            title = info.get("name", "You inspect...") or "You inspect..."
            glyph = info.get("glyph", "?")
            desc = info.get("description", "") or "You see nothing remarkable about it."

            lines: list[str] = []
            if glyph:
                lines.append(str(glyph))
                lines.append("")
            lines.append(str(desc))

            hp_txt = info.get("hp_text")
            if hp_txt:
                lines.append("")
                lines.append(str(hp_txt))

            manager.push_scene(
                UrgentMessageScene(
                    self.game,
                    "\n".join(lines),
                    title=title,
                    choices=["OK"],
                )
            )
            return True

        # Normal inventory mode: open the standard context menu (with Equip...).
        src_px, src_sz = self._row_glyph_screen_info(index, manager)
        self._open_entity_context_menu(
            ent,
            manager,
            source_px=src_px,
            source_glyph_px=src_sz,
            equipped_slot_id=None,
        )
        return True

    # ---------------------------------------------------------------------
    # Widget tree
    # ---------------------------------------------------------------------

    def _header_title(self) -> str:
        if self.explicit_title:
            return str(self.explicit_title)
        owner = self._find_owner_entity()
        if owner is not None:
            return str(getattr(owner, "name", "Inventory"))
        return "Inventory"

    def _build_widgets(self, items: list[Any]) -> None:
        title = self._header_title()
        header = ScaledLabelWidget(title, align="left", scale=2)

        left_col = VBox(spacing=8, padding=0, align="left")
        left_col.add_child(LabelWidget("Contents", align="left"))

        self._refresh_rows()
        self._list = InventoryListWidget(
            self._rows,
            selected_index=self.selected_idx,
            on_activate=self._on_list_activate,  # provided by GeneralMenuScene
            line_spacing=6,
            padding=6,
            auto_font=True,
            min_font_size=18,
            max_font_size=48,
            target_visible_items=16,
        )
        left_col.add_child(self._list)

        self._preview = EntityPreviewWidget()
        self._body_graph = BodyPlanGraphWidget()
        self._right_pane = RightPaneWidget(preview=self._preview, body_graph=self._body_graph)
        footer = LabelWidget(self.FOOTER_TEXT, align="left")

        self.root = TwoPaneInventoryRoot(
            header=header,
            left=left_col,
            right=self._right_pane,
            footer=footer,
            padding=14,
            spacing=12,
            col_spacing=14,
            left_frac=0.48,
        )
        self.root.rect = pygame.Rect(0, 0, 0, 0)

        # Draw drag ghost + drop hint above everything else.
        self.root.add_child(DragOverlayWidget())

    # ---------------------------------------------------------------------
    # Animation
    # ---------------------------------------------------------------------

    # ---------------------------------------------------------------------
    # Source glyph utilities (for nested inventory zooms)
    # ---------------------------------------------------------------------

    def _project_point_window_to_screen(self, pt: tuple[float, float], visual: VisualProfile) -> tuple[int, int]:
        """Project a point in window_rect-local coords through a VisualProfile into renderer.surface coords."""
        assert self.window_rect is not None
        x, y = float(pt[0]), float(pt[1])
        cx, cy = float(self.window_rect.w) * 0.5, float(self.window_rect.h) * 0.5
        # to center
        dx, dy = x - cx, y - cy
        # scale
        sx, sy = float(getattr(visual, "scale_x", 1.0)), float(getattr(visual, "scale_y", 1.0))
        dx *= sx
        dy *= sy
        # flips
        if getattr(visual, "flip_x", False):
            dx = -dx
        if getattr(visual, "flip_y", False):
            dy = -dy
        # rotate
        ang = float(getattr(visual, "angle", 0.0))
        if ang:
            rad = math.radians(ang)
            c = math.cos(rad)
            s = math.sin(rad)
            dx, dy = (dx * c + dy * s, -dx * s + dy * c)
        # translate
        ox = float(self.window_rect.centerx) + float(getattr(visual, "offset_x", 0.0))
        oy = float(self.window_rect.centery) + float(getattr(visual, "offset_y", 0.0))
        return (int(round(ox + dx)), int(round(oy + dy)))

    def _row_glyph_screen_info(self, row_index: int, manager: "SceneManager") -> tuple[tuple[int, int] | None, int | None]:
        """Return (screen_px, approx_screen_size_px) for the glyph in the given row.

        Important: the list glyph is not always a base_px-by-base_px square:
        some visual effects (notably clockwise/counter-clockwise) change the glyph
        canvas size. We must match the *actual* draw placement used by InventoryListWidget
        or nested zooms will drift (and compound badly).
        """
        try:
            renderer = manager.renderer
        except Exception:
            return (None, None)

        # Ensure we have a window rect and a freshly-laid-out widget tree.
        self._ensure_window_rect(manager)
        if self.window_rect is None or self._list is None:
            return (None, None)

        panel = self._get_panel(manager)
        # Layout widgets into the panel surface (no present).
        try:
            self.draw_panel(panel, renderer, manager)
        except Exception:
            pass

        lst = self._list
        try:
            ctx = WidgetContext(surface=panel, game=getattr(self, "game", None), scene=self, renderer=renderer)
            font = lst._pick_font(ctx)  # type: ignore[attr-defined]
            line_h = int(getattr(lst, "_line_height", font.get_height()))
        except Exception:
            font = getattr(renderer, "menu_font", getattr(renderer, "font", None))
            if font is None:
                return (None, None)
            line_h = font.get_height()

        cap = 0
        try:
            cap = int(lst._visible_capacity())  # type: ignore[attr-defined]
        except Exception:
            cap = max(1, (lst.rect.h - 2 * int(getattr(lst, "padding", 0))) // max(1, line_h))

        start = int(getattr(lst, "scroll_offset", 0))
        # If the requested row isn't visible, approximate it at the closest visible slot.
        vis_i = row_index
        if vis_i < start:
            vis_i = start
        if vis_i >= start + max(1, cap):
            vis_i = start + max(1, cap) - 1

        pad = int(getattr(lst, "padding", 0))
        x0 = float(lst.rect.x + pad)
        y0 = float(lst.rect.y + pad)
        row_y = y0 + float((vis_i - start) * line_h)

        # Prefix width ("▶ " or "  ") – match ListWidget draw().
        try:
            prefix_w = float(font.size("▶ ")[0])
        except Exception:
            prefix_w = 0.0

        # Compute the *actual* glyph canvas for this row, so we can match its placement.
        ent = None
        try:
            if 0 <= vis_i < len(self._rows):
                ent = getattr(self._rows[vis_i], "ent", None)
        except Exception:
            ent = None

        if ent is None:
            return (None, None)

        scene_effects = list(getattr(self, "visual_effects", []) or [])
        base_px = max(14, int(font.get_height() * 1.15))
        glyph_canvas, glyph_anchor = _render_entity_glyph_canvas_with_anchor(
            renderer,
            ent,
            font=font,
            base_px=base_px,
            scene_effects=scene_effects,
        )

        # In InventoryListWidget.draw():
        #   x = x0 + prefix_w
        #   blit_y = row_y - (glyph_h - font_h)//2
        glyph_x = x0 + prefix_w
        font_h = int(font.get_height())
        blit_y = float(row_y - int((glyph_canvas.get_height() - font_h) // 2))

        # Glyph *cell* center in PANEL coords (logical panel surface).
        # (Not the canvas center — overlays/rotation can expand asymmetrically.)
        gx = float(glyph_x) + float(glyph_anchor[0])
        gy = float(blit_y) + float(glyph_anchor[1])

        # Convert PANEL coords -> WINDOW coords (if logical panel != window size).
        pw, ph = panel.get_size()
        sx = float(self.window_rect.w) / float(max(1, pw))
        sy = float(self.window_rect.h) / float(max(1, ph))
        win_pt = (gx * sx, gy * sy)

        # With the menu fully open, use the current VisualProfile at current progress.
        visual = self._current_visual_profile(logical_to_window_scale_x=sx, logical_to_window_scale_y=sy)
        screen_px = self._project_point_window_to_screen(win_pt, visual)

        # Approximate on-screen size of the glyph (use *base glyph cell* size,
        # not the expanded/rotated canvas which may include asymmetric overlays).
        try:
            cell_w_win = float(base_px) * sx
            cell_h_win = float(base_px) * sy
            sw = cell_w_win * float(abs(getattr(visual, "scale_x", 1.0)))
            sh = cell_h_win * float(abs(getattr(visual, "scale_y", 1.0)))
            size_px = int(round(max(sw, sh)))
            size_px = max(4, min(1024, size_px))
        except Exception:
            size_px = None

        return (screen_px, size_px)

    def _row_glyph_screen_px(self, row_index: int, manager: "SceneManager") -> tuple[int, int] | None:
        """Backwards-compatible: return only the glyph screen pixel."""
        px, _sz = self._row_glyph_screen_info(row_index, manager)
        return px

    def update(self, dt_ms: int, manager: "SceneManager") -> None:
        # Phase 5: advance body-graph zoom animation.
        self._body_zoom_tick()

        self._refresh_rows()
        if self._list:
            self._list.set_items(self._rows)


        # Execute delayed single-click activation (containers only) once the double-click window expires.
        if self._pending_click_activate_index is not None and not self._closing and not bool(getattr(self, "_drag_active", False)):
            try:
                now = int(pygame.time.get_ticks())
                if now >= int(self._pending_click_activate_due_ms):
                    self._pending_mouse_activate = int(self._pending_click_activate_index)  # type: ignore[attr-defined]
                    self._pending_click_activate_index = None
                    self._pending_click_activate_due_ms = 0
            except Exception:
                self._pending_click_activate_index = None
                self._pending_click_activate_due_ms = 0



        # -----------------------------------------------------------------
        # Flush pending widget actions here (not only in handle_event),
        # so delayed single-clicks work even if no further events arrive.
        # -----------------------------------------------------------------

        # Widget-triggered double-click open: needs manager to push InventoryScene.
        idx = getattr(self, "_pending_double_open_index", None)
        if idx is not None:
            try:
                self._pending_double_open_index = None
            except Exception:
                pass
            try:
                if bool(getattr(self, "allow_open_containers", True)):
                    self._open_container_from_index(int(idx), manager)
            except Exception:
                pass

        # Delayed single-click activation for the left inventory list (container action menu).
        midx = getattr(self, "_pending_mouse_activate", None)
        if midx is not None:
            try:
                self._pending_mouse_activate = None
            except Exception:
                pass
            try:
                # Provided by GeneralMenuScene
                self._on_list_activate(int(midx), manager)
            except Exception:
                pass
        # Execute delayed single-click activation for equipped body nodes once the double-click window expires.
        # (BodyPlanGraphWidget sets _pending_node_activate_nid/_due_ms; we "promote" it here.)
        if (
            getattr(self, "_pending_node_activate_nid", None) is not None
            and not self._closing
            and not bool(getattr(self, "_drag_active", False))
        ):
            try:
                now = int(pygame.time.get_ticks())
                if now >= int(getattr(self, "_pending_node_activate_due_ms", 0)):
                    self._pending_node_activate = str(self._pending_node_activate_nid)  # type: ignore[attr-defined]
                    self._pending_node_activate_nid = None
                    self._pending_node_activate_due_ms = 0
            except Exception:
                try:
                    self._pending_node_activate_nid = None
                    self._pending_node_activate_due_ms = 0
                except Exception:
                    pass

        # Widget-triggered double-click on a body node -> zoom in.
        pending_zoom = getattr(self, "_pending_body_zoom_in", None)
        if pending_zoom is not None:
            try:
                self._pending_body_zoom_in = None
            except Exception:
                pass
            try:
                self._body_zoom_in(str(pending_zoom))
            except Exception:
                pass

        # Delayed single-click on an equipped body node -> open context menu.
        pending_node = getattr(self, "_pending_node_activate", None)
        if pending_node is not None:
            try:
                self._pending_node_activate = None
            except Exception:
                pass
            try:
                node_id = str(pending_node)
                slot_id = self._canonical_body_slot_id(node_id)

                eq = self._equipped_entity_for_slot(slot_id)
                if eq is not None:
                    src_px, src_sz = self._node_glyph_screen_info(node_id, manager)
                    self._open_entity_context_menu(
                        eq,
                        manager,
                        source_px=src_px,
                        source_glyph_px=src_sz,
                        equipped_slot_id=slot_id,
                    )
            except Exception:
                pass



        # Opening (zoom-in) vs closing (zoom-out) animation.
        if not self._closing:
            self._zoom_elapsed = min(self.ZOOM_MS, self._zoom_elapsed + int(dt_ms))
            self._zoom_progress = _clamp01(self._zoom_elapsed / float(max(1, self.ZOOM_MS)))
        else:
            self._close_elapsed = min(self.CLOSE_MS, self._close_elapsed + int(dt_ms))
            t = _clamp01(self._close_elapsed / float(max(1, self.CLOSE_MS)))
            self._zoom_progress = _clamp01(1.0 - t)
            if t >= 1.0:
                # Finish: pop ourselves for real (without re-triggering begin_pop).
                if hasattr(manager, "_force_pop_scene"):
                    manager._force_pop_scene()  # type: ignore[attr-defined]
                else:
                    try:
                        manager.pop_scene(force=True)  # type: ignore[call-arg]
                    except Exception:
                        manager.pop_scene()
                return

        # Cache map tile pixel size (tracks mousewheel zoom).
        #
        # IMPORTANT: renderer.tile is now allowed to be non-integer (micro / fractional zoom).
        # If we truncate it to int, tx*tile accumulates error and the computed source point
        # drifts up-left as you zoom (exactly the bug you're seeing).
        try:
            renderer = getattr(manager, "renderer", None)
            if renderer is not None:
                tile_px = getattr(renderer, "tile_px", getattr(renderer, "tile", None))
                if tile_px is not None:
                    self._zoom_map_tile_px = float(tile_px)
        except Exception:
            pass

        # Compute the pixel position of the owner entity in screen space (renderer surface coords).
        if self._zoom_source_px is None and self._zoom_owner_world is not None:
            try:
                renderer = getattr(manager, "renderer", None)
                if renderer is not None:
                    tile_px = float(getattr(renderer, "tile_px", getattr(renderer, "tile", 0.0)))
                    ox = float(getattr(renderer, "origin_x", 0.0))
                    oy = float(getattr(renderer, "origin_y", 0.0))
                    if tile_px > 0.0:
                        tx, ty = self._zoom_owner_world
                        # Center of the tile in *current* screen space.
                        px = int(round(float(tx) * tile_px + ox + tile_px * 0.5))
                        py = int(round(float(ty) * tile_px + oy + tile_px * 0.5))
                        self._zoom_source_px = (px, py)
            except Exception:
                self._zoom_source_px = None

        super().update(dt_ms, manager)
    # ---------------------------------------------------------------------
    # Background dim fade (smooth with zoom)
    # ---------------------------------------------------------------------

    def get_dim_alpha(self, renderer=None, manager=None) -> int:
        """Fade background dim in/out continuously during push/pop zoom."""
        if not getattr(self, "dim_background", True):
            return 0
        try:
            p = _smoothstep(_clamp01(float(self._zoom_progress)))
        except Exception:
            p = 1.0
        return int(140 * float(p))


    # ---------------------------------------------------------------------
    # Diagrammatic zoom transform
    # ---------------------------------------------------------------------

    def _current_visual_profile(
        self,
        *,
        logical_to_window_scale_x: float = 1.0,
        logical_to_window_scale_y: float = 1.0,
    ) -> VisualProfile:
        """Compute the *current* VisualProfile for the panel.

        Goals:
        - successive recursion scaling (handled via PopupMenuScene scale + extra profile scaling here)
        - zoom-in/out that respects *all* affine bits from accumulated visual effects:
          scale, rotation, flips, offsets (e.g. clockwise, mirror_x), plus time-varying effects.
        - keep the preview glyph anchor glued to the source point during the transition.
        """
        # Start with inherited scene effects (time-based too).
        base = build_visual_profile(VisualProfile(), self.visual_effects)

        p = _smoothstep(_clamp01(float(self._zoom_progress)))

        # --- Affine animation (optional) -------------------------------------
        # By default we *do not* animate rotation/flips during the zoom.
        # The panel starts already transformed, matching the list glyph.
        if self.animate_affine:
            # Rotation ramps in.
            try:
                base.angle = _lerp(0.0, float(base.angle), p)
            except Exception:
                pass

            # Flip can't be smoothly interpolated (boolean), but we can time it.
            flip_gate = 0.35
            if getattr(base, "flip_x", False):
                base.flip_x = bool(p >= flip_gate)
            if getattr(base, "flip_y", False):
                base.flip_y = bool(p >= flip_gate)

        
        # If we're being called from mouse unprojection early in the frame, we may be handed
        # default (1,1) logical->window scales even when this popup uses a different logical
        # panel size. If we already have a logical panel, infer the real scales so we don't
        # accidentally cache a bogus zoom start scale (which makes the next zoom start full-size).
        try:
            if (
                abs(float(logical_to_window_scale_x) - 1.0) < 1e-6
                and abs(float(logical_to_window_scale_y) - 1.0) < 1e-6
                and getattr(self, "_panel", None) is not None
                and getattr(self, "window_rect", None) is not None
            ):
                pw, ph = self._panel.get_size()  # type: ignore[union-attr]
                if (pw, ph) != (self.window_rect.w, self.window_rect.h):  # type: ignore[union-attr]
                    logical_to_window_scale_x = float(self.window_rect.w) / float(max(1, pw))  # type: ignore[union-attr]
                    logical_to_window_scale_y = float(self.window_rect.h) / float(max(1, ph))  # type: ignore[union-attr]
        except Exception:
            pass

# --- Proportional scale ----------------------------------------------
        # We want: (panel scale) * (final glyph px) ~= (map tile px) at p=0.
        glyph_full_px = max(1.0, float(self._zoom_glyph_base_px) * float(max(0.01, min(logical_to_window_scale_x, logical_to_window_scale_y))))
        want_px = max(1.0, float(self._zoom_map_tile_px))
        if self._source_from_parent_panel:
            # When the zoom source is a glyph inside a *parent* inventory panel,
            # prefer the measured on-screen size of that glyph (so the new menu truly
            # starts at the same tiny scale as the list icon).
            if self._zoom_source_glyph_px is not None:
                want_px = float(max(4, self._zoom_source_glyph_px))
            else:
                # Fallback: estimate based on map tile size and recursion scale.
                parent_scale = float(self._depth_visual_scale) / float(max(0.0001, self.DEPTH_SCALE))
                want_px *= parent_scale
                want_px = max(6.0, want_px)

        # Guardrail: after certain UI interactions (e.g., drag/drop), the list font can temporarily
        # balloon, making the "source glyph size" comparable to the final preview glyph.
        # If we trust that raw size, the next zoom starts at full scale (no small→big lerp).
        # Clamp want_px so the start scale remains < 1.0 for parent-panel sourced zooms.
        if self._source_from_parent_panel:
            try:
                want_px = min(float(want_px), float(glyph_full_px) * 0.92)
            except Exception:
                pass
        # Compute instantaneous candidate start scale from current geometry.
        s0 = want_px / glyph_full_px
        # Clamp so the whole menu doesn't become astronomically tiny on extreme zoom-out.
        s0 = max(0.04, min(8.0, float(s0)))  # allow >1.0 so the zoom can also *shrink* when starting very zoomed-in

        # IMPORTANT: PanelScene / input hit-testing can call _current_visual_profile() early,
        # before the panel has a trustworthy logical surface / scale. If we cache from that
        # call, we can lock in a bogus start scale (often 1.0), and the next zoom won't
        # scale up. So we only "lock" once context looks trustworthy, and we also allow
        # a later trusted call to correct an earlier accidental ~1.0 lock.
        try:
            panel_ready = getattr(self, "_panel", None) is not None
            trusted_scales = not (
                abs(float(logical_to_window_scale_x) - 1.0) < 1e-6
                and abs(float(logical_to_window_scale_y) - 1.0) < 1e-6
            )
            trusted = (self.window_rect is not None) and (panel_ready or trusted_scales)
        except Exception:
            trusted = False

        if trusted:
            if self._zoom_start_scale is None:
                self._zoom_start_scale = float(s0)
            else:
                # We want the start scale to match the *actual on-screen* size at the moment the
                # popup appears. However, _current_visual_profile() can be called multiple times:
                # - very early (hit-testing / unprojection) with bogus (1,1) scales
                # - later in the frame with correct panel/logical scales
                # If we "lock" too early, the zoom can start at 1.0 (no scaling).
                #
                # Policy:
                #   - allow correction while the animation is still near the beginning
                #   - always treat an exact-ish 1.0 lock as provisional if we later compute != 1.0
                try:
                    if float(self._zoom_start_scale) >= 0.999 and abs(float(s0) - 1.0) > 1e-3:
                        self._zoom_start_scale = float(s0)
                    elif p < 0.08:
                        # Early in the animation: keep it faithful to the most recent trusted geometry.
                        self._zoom_start_scale = float(s0)
                except Exception:
                    pass

        start_scale = float(self._zoom_start_scale) if self._zoom_start_scale is not None else float(s0)
        panel_scale = _lerp(start_scale, 1.0, p)

        # Apply recursion depth scaling at all times (scales text, glyphs, spacing).
        panel_scale *= float(self._depth_visual_scale)

        panel_scale *= _lerp(self.PANEL_SCALE_START, self.PANEL_SCALE_END, p)

        # Fade the PANEL only.
        zoom_alpha = _lerp(self.PANEL_ALPHA_START, self.PANEL_ALPHA_END, p)
        base.alpha = float(base.alpha) * float(zoom_alpha)

        # Apply zoom scale multiplicatively (preserve other effect scales).
        base.scale_x = float(base.scale_x) * float(panel_scale)
        base.scale_y = float(base.scale_y) * float(panel_scale)

        # --- Anchor glue (full affine) ---------------------------------------
        # Solve for (delta_offset_x, delta_offset_y) so that the panel-local anchor
        # lands on the desired screen point, *after* scale/flip/rotate.
        if self.window_rect is not None:
            if self._zoom_anchor_panel is not None:
                # _zoom_anchor_panel is stored in logical panel coords; convert to window_rect-local.
                ax = float(self._zoom_anchor_panel[0]) * float(logical_to_window_scale_x)
                ay = float(self._zoom_anchor_panel[1]) * float(logical_to_window_scale_y)
            else:
                ax, ay = (self.window_rect.width * 0.5, self.window_rect.height * 0.5)
            cx, cy = (self.window_rect.width * 0.5, self.window_rect.height * 0.5)

            # Vector from panel center to anchor, in panel-local coords.
            dx = float(ax) - float(cx)
            dy = float(ay) - float(cy)

            # Apply flip then scale.
            if getattr(base, "flip_x", False):
                dx = -dx
            if getattr(base, "flip_y", False):
                dy = -dy

            dx *= float(base.scale_x)
            dy *= float(base.scale_y)

            # Rotate about the panel center.
            ang = float(getattr(base, "angle", 0.0))
            if ang:
                th = math.radians(ang)
                cth = math.cos(th)
                sth = math.sin(th)
                rdx = dx * cth + dy * sth
                rdy = -dx * sth + dy * cth
            else:
                rdx, rdy = dx, dy

            # Where should the anchor end up when the zoom is finished?
            # Use the *un-glued* base offsets so orbiting/jittery offsets remain part of the final position.
            final_anchor_x = float(self.window_rect.centerx) + float(base.offset_x) + float(rdx)
            final_anchor_y = float(self.window_rect.centery) + float(base.offset_y) + float(rdy)

            if self._zoom_source_px is not None:
                sx, sy = self._zoom_source_px
                desired_x = _lerp(float(sx), float(final_anchor_x), p)
                desired_y = _lerp(float(sy), float(final_anchor_y), p)
            else:
                desired_x, desired_y = float(final_anchor_x), float(final_anchor_y)

            # Current anchor position without glue:
            cur_anchor_x = float(self.window_rect.centerx) + float(base.offset_x) + float(rdx)
            cur_anchor_y = float(self.window_rect.centery) + float(base.offset_y) + float(rdy)

            # Add just the delta needed to move anchor onto desired.
            base.offset_x = float(base.offset_x) + (float(desired_x) - float(cur_anchor_x))
            base.offset_y = float(base.offset_y) + (float(desired_y) - float(cur_anchor_y))

        return base

    # ---------------------------------------------------------------------
    # Panel drawing + opaque glyph overlay
    # ---------------------------------------------------------------------

    def draw_panel(self, panel: pygame.Surface, renderer, manager: "SceneManager") -> None:
        # External glyph overlay mode (panel fades, glyph stays solid) is only
        # needed during the diagrammatic open/close animation. Once settled, we
        # let EntityPreviewWidget draw the glyph so it can participate in body-graph
        # camera zoom and remain clipped inside the right pane.
        try:
            p = float(getattr(self, "_zoom_progress", 0.0) or 0.0)
        except Exception:
            p = 0.0
        # Use the external fully-opaque glyph overlay ONLY while the diagrammatic open/close
        # animation is running (or while closing). Once settled, let EntityPreviewWidget draw
        # the glyph so it participates in body-graph camera zoom (scale + pan) and stays clipped.
        try:
            p = float(getattr(self, "_zoom_progress", 0.0) or 0.0)
        except Exception:
            p = 0.0
        self._external_opaque_glyph = bool(self._closing or p < 0.999)




        super().draw_panel(panel, renderer, manager)

        # Cache anchor + glyph size from preview pane after layout.
        #
        # IMPORTANT: many visual effects expand the glyph canvas asymmetrically
        # (e.g. smoke/flames rising above the base cell). If we anchor the
        # diagrammatic zoom on preview.rect.center, the zoom will appear to
        # originate "from below" for those effects. Instead, we compute the
        # *logical glyph cell center* inside the rendered canvas and store the
        # panel-space coordinate of that logical center.
        #
        # ALSO IMPORTANT: the preview pane now includes footer text (entity description).
        # The opaque glyph overlay should stay inside the main "glyph region" so it doesn't
        # paint over the description at the bottom.
        try:
            if self._preview is not None:
                owner = self._find_owner_entity()

                # Determine whether a description footer will be drawn (affects reserved space).
                try:
                    info = describe_entity_for_look(owner) if owner is not None else {}
                    desc = info.get("description") or getattr(owner, "description", None)
                except Exception:
                    desc = getattr(owner, "description", None)

                # Match EntityPreviewWidget's internal layout margins/reserved header/footer.
                top_reserved = 70
                bottom_reserved = 80 if desc else 56
                region_w = max(1, int(self._preview.rect.w) - 28)
                region_h = max(1, int(self._preview.rect.h) - int(top_reserved) - int(bottom_reserved))

                # IMPORTANT:
                # _zoom_glyph_base_px should represent the *base* glyph-cell size for the preview region
                # at zoom_scale == 1.0. The body-graph camera zoom is applied elsewhere (preview draw),
                # so we do NOT bake it in here (otherwise we double-scale and create pop on mode handoff).
                # Make the glyph ~2× bigger, but keep a little breathing room.
                self._zoom_glyph_base_px = max(12, int(min(region_w, region_h) * 0.50 * 2.0))
                self._zoom_glyph_base_px = min(int(self._zoom_glyph_base_px), int(min(region_w, region_h) * 0.90))



                if owner is not None:
                    base_px = int(self._zoom_glyph_base_px)
                    raster_px = min(int(base_px), FONT_RASTER_PX_MAX)
                    font = pygame.font.SysFont("consolas", max(10, raster_px), bold=True)

                    gcanvas, ganchor = _render_entity_glyph_canvas_with_anchor(
                        renderer,
                        owner,
                        font=font,
                        base_px=base_px,
                        scene_effects=list(getattr(self, "visual_effects", []) or []),
                    )

                    # Place the glyph so that its *logical cell center* lands at the center of the
                    # reserved glyph region (excluding header and description footer).
                    # Base center within the reserved glyph region.
                    local_cx = float(region_w) * 0.5
                    local_cy = float(region_h) * 0.5

                    # Phase 5: pan with the same body-graph camera transform as the node skeleton.
                    try:
                        zoom_scale = float(getattr(self, "_body_zoom_scale", 1.0) or 1.0)
                        focus_pos = getattr(self, "_body_zoom_focus_pos", None)
                        pan_t = _body_zoom_pan_t(self, zoom_scale)
                        local_cx, local_cy = _apply_body_zoom_to_point(
                            local_cx,
                            local_cy,
                            region_w=float(region_w),
                            region_h=float(region_h),
                            focus_pos=focus_pos,
                            zoom_scale=zoom_scale,
                            pan_t=pan_t,
                        )
                    except Exception:
                        zoom_scale = 1.0

                    region_cx = float(self._preview.rect.x) + 14.0 + local_cx
                    region_cy = float(self._preview.rect.y) + float(top_reserved) + local_cy


                    # Store anchor as the *glyph cell center* in panel coords.
                    # (Render() later positions gcanvas so this anchor lands at the same place.)
                    self._zoom_anchor_panel = (region_cx, region_cy)
                else:
                    self._zoom_anchor_panel = (float(self._preview.rect.centerx), float(self._preview.rect.centery))
        except Exception:
            pass

    def render(self, renderer, manager: "SceneManager") -> None:
        """
        Override render so we can:
          1) draw the panel with a fading/scaling VisualProfile
          2) then redraw the anchor glyph as a separate *opaque* overlay using the same transform
        """
        self._ensure_window_rect(manager)
        assert self.window_rect is not None

        self.draw_underlay(renderer, manager)

        panel = self._get_panel(manager)
        self.draw_panel(panel, renderer, manager)

        # If logical != window_rect, scale panel to window_rect.size BEFORE VisualProfile.
        logical = self.get_logical_panel_size(manager)
        panel_to_blit = panel
        logical_to_window = 1.0
        if logical is not None and panel.get_size() != self.window_rect.size:
            try:
                panel_to_blit = pygame.transform.smoothscale(panel, self.window_rect.size)
                logical_w, logical_h = panel.get_size()
                logical_to_window = min(
                    self.window_rect.w / max(1, logical_w),
                    self.window_rect.h / max(1, logical_h),
                )
            except Exception:
                panel_to_blit = panel
                logical_to_window = 1.0

        # Apply the diagrammatic zoom transform (fading + proportional scaling + anchor glue).
        visual = self._current_visual_profile(logical_to_window_scale_x=float(self.window_rect.w) / float(max(1, panel.get_width())),
            logical_to_window_scale_y=float(self.window_rect.h) / float(max(1, panel.get_height())))
        apply_visual_panel(renderer.surface, panel_to_blit, self.window_rect, visual)

        # Redraw the glyph as an opaque overlay at the anchor point using the SAME transform.
        # (Only while the diagrammatic open/close animation is running.)
        owner = self._find_owner_entity()
        if bool(getattr(self, "_external_opaque_glyph", False)) and owner is not None and self._zoom_anchor_panel is not None:

            try:
                glyph_layer = pygame.Surface(self.window_rect.size, pygame.SRCALPHA)

                # Anchor location in the pre-transform panel space.
                ax = float(self._zoom_anchor_panel[0]) * float(self.window_rect.w) / float(max(1, panel.get_width()))
                ay = float(self._zoom_anchor_panel[1]) * float(self.window_rect.h) / float(max(1, panel.get_height()))

                # If we scaled the logical surface to window size, the anchor point lives in
                # window_rect coords already (because we blit a window-sized panel_to_blit).
                # So we do NOT rescale ax/ay here: they're panel_to_blit coords.
                ltw_min = float(min(
                    float(self.window_rect.w) / float(max(1, panel.get_width())),
                    float(self.window_rect.h) / float(max(1, panel.get_height())),
                ))
                base_px = max(8, int(self._zoom_glyph_base_px * float(max(0.25, ltw_min))))
                font = pygame.font.SysFont("consolas", max(10, int(base_px)), bold=True)

                gcanvas, ganchor = _render_entity_glyph_canvas_with_anchor(
                    renderer,
                    owner,
                    font=font,
                    base_px=base_px,
                    scene_effects=list(getattr(self, "visual_effects", []) or []),
                )

                # Place the canvas so that the *glyph cell center* lands on (ax, ay),
                # not the canvas bounding-box center (which can be offset by effects).
                gx = int(round(float(ax) - float(ganchor[0])))
                gy = int(round(float(ay) - float(ganchor[1])))

                # Clip glyph overlay to the preview pane (in window coords) so visual effects
                # don't bleed into the left list.
                try:
                    pr = getattr(self, "_preview", None)
                    if pr is not None and getattr(pr, "rect", None) is not None:
                        sx = float(self.window_rect.w) / float(max(1, panel.get_width()))
                        sy = float(self.window_rect.h) / float(max(1, panel.get_height()))
                        clip = pygame.Rect(
                            int(round(float(pr.rect.x) * sx)),
                            int(round(float(pr.rect.y) * sy)),
                            int(round(float(pr.rect.w) * sx)),
                            int(round(float(pr.rect.h) * sy)),
                        )
                        glyph_layer.set_clip(clip)
                except Exception:
                    pass

                glyph_layer.blit(gcanvas, (gx, gy))
                glyph_layer.set_clip(None)


                # Apply the exact same transform but force alpha to 1.0.
                hovered_right = bool(getattr(self, "_right_panel_hovered", False))
                glyph_alpha = 0.72 if hovered_right else 1.0
                # During diagrammatic open/close, keep the glyph fully opaque regardless of hover.
                try:
                    p = float(getattr(self, "_zoom_progress", 0.0) or 0.0)
                except Exception:
                    p = 0.0
                if bool(getattr(self, "_closing", False)) or p < 0.99999:
                    glyph_alpha = 1.0
                else:
                    glyph_alpha = 0.72 if hovered_right else 1.0

                visual_g = VisualProfile(
                    scale_x=visual.scale_x,
                    scale_y=visual.scale_y,
                    offset_x=visual.offset_x,
                    offset_y=visual.offset_y,
                    angle=visual.angle,
                    alpha=float(glyph_alpha),
                    flip_x=visual.flip_x,
                    flip_y=visual.flip_y,
                )
                apply_visual_panel(renderer.surface, glyph_layer, self.window_rect, visual_g)
            except Exception:
                pass

        # Draw body-plan overlay ABOVE the opaque glyph (so nodes/labels sit on top of the sprite).
        body_overlay = getattr(self, "_body_overlay_panel_surface", None)
        if body_overlay is not None:
            try:
                # Scale overlay to window size if needed (same as panel scaling).
                overlay_to_blit = body_overlay
                if overlay_to_blit.get_size() != self.window_rect.size:
                    overlay_to_blit = pygame.transform.smoothscale(overlay_to_blit, self.window_rect.size)

                visual_o = VisualProfile(
                    scale_x=visual.scale_x,
                    scale_y=visual.scale_y,
                    offset_x=visual.offset_x,
                    offset_y=visual.offset_y,
                    angle=visual.angle,
                    alpha=1.0,  # overlay surface already encodes fade alpha
                    flip_x=visual.flip_x,
                    flip_y=visual.flip_y,
                )
                apply_visual_panel(renderer.surface, overlay_to_blit, self.window_rect, visual_o)
            except Exception:
                pass

        if getattr(renderer, "suspend_present", False):
            return

        if hasattr(renderer, "present"):
            renderer.present()
        else:
            pygame.display.flip()


class LookScene(InventoryScene):
    """Read-only inspect scene (first pass).

    This reuses InventoryScene's two-pane UI + zoom, but disables:
      - drag/drop
      - item action menu (Take/Drop/Eat/Put)
      - opening containers via double-click

    Later we can specialize the row-building for imperfect information.
    """

    def _preview_entity(self):
        """In Look mode, always preview the looked-at entity (owner), not the selected row."""
        return self._find_owner_entity()

    def __init__(
        self,
        game,
        *,
        owner_id: Optional[str] = None,
        window_rect: Optional[pygame.Rect] = None,
        title: Optional[str] = None,
        base_effects: Optional[list[str]] = None,
        source_px: tuple[int, int] | None = None,
        source_glyph_px: int | None = None,
    ) -> None:
        super().__init__(
            game,
            owner_id=owner_id,
            window_rect=window_rect,
            parent_owner_id=None,
            title=title,
            base_effects=base_effects,
            source_px=source_px,
            source_glyph_px=source_glyph_px,
            stack_depth=0,
            animate_affine=False,
            mode="look",
        )
