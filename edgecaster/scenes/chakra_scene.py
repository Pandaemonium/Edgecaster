"""
Chakra Selection Scene - "Buttery Smooth" UI
=============================================

A visually impressive scene for managing chakra activations.

Features:
- Body silhouette with glowing chakra points
- 3-layer glow rendering (outer bloom, mid glow, core)
- Pulse animations for active chakras
- Energy flow lines between connected chakras
- Activation burst particles
- Zoom navigation into body regions (arms -> hands -> fingers)
- Real-time pattern preview that updates on toggle
- Resonance indicator badges

Visual Design:
- Locked: Dim gray (80, 80, 90) with lock indicator
- Unlocked (inactive): Soft blue glow (120, 160, 220)
- Active: Bright gold with pulse (255, 220, 100)
- Hover: White ring highlight
- Energy flow: Purple connecting lines (200, 180, 255)
"""

from __future__ import annotations

import math
import pygame
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from .base import (
    PanelScene,
    MenuInput,
    MENU_ACTION_UP,
    MENU_ACTION_DOWN,
    MENU_ACTION_LEFT,
    MENU_ACTION_RIGHT,
    MENU_ACTION_ACTIVATE,
    MENU_ACTION_BACK,
    MENU_ACTION_FULLSCREEN,
)

from edgecaster.ui.widgets import Widget, WidgetContext, ButtonWidget
from edgecaster.math_utils import lerp, smoothstep, lerp_rgb
from edgecaster.systems.chakras import (
    check_resonance_bonuses_from_active_nodes,
    get_resonance_modifiers,
    CHARGE_MAX_BASE,
    is_branch_root,
)
from edgecaster.systems import chakra_items as chakra_items_system
from edgecaster.systems import body_view_queries as body_view_queries_system

if TYPE_CHECKING:
    from .manager import SceneManager

# =============================================================================
# CONSTANTS
# =============================================================================

# Color palette
COLOR_LOCKED = (80, 80, 90)
COLOR_UNLOCKED = (120, 160, 220)
COLOR_ACTIVE = (255, 220, 100)
COLOR_HOVER = (255, 255, 255)
COLOR_ENERGY_FLOW = (200, 180, 255)

# Glow layer multipliers: (radius_mul, alpha_mul)
GLOW_LAYERS = [
    (3.0, 0.15),  # Outer bloom
    (2.0, 0.3),   # Mid glow
    (1.0, 1.0),   # Core
]

# Animation timing
PULSE_PERIOD_MS = 1600
ENERGY_FLOW_PERIOD_MS = 2000

# Layout
BODY_PANEL_WIDTH_FRAC = 0.60  # Left 60% for body
BASE_CHAKRA_RADIUS = 12

# Display-only scale for laying out the body nodes in the Chakra scene.
# This does NOT change gameplay patterns; it only spreads the UI layout.
CHAKRA_LAYOUT_SCALE = 2.6

# Realignment tuning (alignment units, not pixels)
# These numbers are intentionally conservative; tweak as needed.
REALIGN_TIME_TICKS = 20  # Time cost paid on commit (no resource cost)
REALIGN_BASE_LIMIT = 0.35  # Base alignment radius
REALIGN_PER_DEX = 0.02  # Extra alignment radius per DEX/AGI point
REALIGN_MIN_LIMIT = 0.10  # Clamp to avoid zero range
REALIGN_MAX_LIMIT = 1.00  # Clamp to prevent extreme offsets

# Camera zoom controls for the chakra layout view
CAMERA_MIN_SCALE = 0.45
CAMERA_MAX_SCALE = 12.00
CAMERA_ZOOM_STEP = 1.18

# Focus view auto-toggle (zoom-driven). When zoomed in deeply, the scene
# can collapse to a subtree for visual clarity without restricting selection.
FOCUS_AUTO_SCALE = 2.6
FOCUS_AUTO_RELEASE = 1.9

# Tooltip styling
TOOLTIP_PADDING = 8
TOOLTIP_MARGIN = 8
TOOLTIP_MAX_WIDTH = 320
TOOLTIP_BG = (14, 18, 30)
TOOLTIP_BORDER = (80, 100, 130)
TOOLTIP_TITLE = (230, 235, 255)
TOOLTIP_TEXT = (180, 195, 220)
TOOLTIP_ACCENT = (170, 220, 255)
TOOLTIP_WARN = (200, 120, 120)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _is_chakra_state_like(state: Any) -> bool:
    """Return True when *state* exposes the fields Chakra Scene expects."""
    required = (
        "unlocked",
        "active",
        "alignments",
        "generators",
        "charges",
    )
    return all(hasattr(state, attr) for attr in required)


@dataclass
class ChakraEditSession:
    """Non-authoritative scene-local editing snapshot for Chakra Scene.

    This is intentionally not a live runtime mirror. It is initialized from a
    stable view snapshot, mutated locally for preview/undo/drag, and committed
    back through explicit component writes.
    """

    unlocked: Set[str]
    active: Set[str]
    alignments: Dict[str, Tuple[float, float]]
    generators: Dict[str, Any]
    charges: Dict[str, float]
    pattern_root: Optional[str] = None

    @classmethod
    def from_state(cls, state: Any, *, alignments: Optional[Dict[str, Tuple[float, float]]] = None) -> "ChakraEditSession":
        return cls(
            unlocked=set(getattr(state, "unlocked", set()) or set()),
            active=set(getattr(state, "active", set()) or set()),
            alignments=dict(alignments if alignments is not None else (getattr(state, "alignments", {}) or {})),
            generators=dict(getattr(state, "generators", {}) or {}),
            charges=dict(getattr(state, "charges", {}) or {}),
            pattern_root=getattr(state, "pattern_root", None),
        )

def _draw_vertical_gradient(
    surface: pygame.Surface,
    rect: pygame.Rect,
    top: Tuple[int, int, int],
    bottom: Tuple[int, int, int],
) -> None:
    """Draw a vertical gradient into a rect (used for scene backgrounds)."""
    if rect.height <= 1:
        pygame.draw.rect(surface, top, rect)
        return

    for i in range(rect.height):
        t = i / max(1, rect.height - 1)
        col = lerp_rgb(top, bottom, t)
        y = rect.y + i
        pygame.draw.line(surface, col, (rect.x, y), (rect.right - 1, y))


def _draw_starfield(
    surface: pygame.Surface,
    rect: pygame.Rect,
    seed: int = 1337,
    count: int = 140,
) -> None:
    """Draw a faint, deterministic starfield (adds depth without noise)."""
    rng = random.Random(seed)
    for _ in range(count):
        x = rect.x + rng.randrange(0, max(1, rect.width))
        y = rect.y + rng.randrange(0, max(1, rect.height))
        size = rng.choice((1, 1, 1, 2))
        alpha = rng.choice((40, 50, 70))
        col = (120, 140, 190, alpha)
        pygame.draw.circle(surface, col, (x, y), size)


def _draw_subtle_grid(surface: pygame.Surface, rect: pygame.Rect, step: int = 48) -> None:
    """Draw a very faint grid for the preview panel."""
    col = (40, 45, 60)
    for x in range(rect.x, rect.right, step):
        pygame.draw.line(surface, col, (x, rect.y), (x, rect.bottom))
    for y in range(rect.y, rect.bottom, step):
        pygame.draw.line(surface, col, (rect.x, y), (rect.right, y))


def _format_chakra_label(node_id: str) -> str:
    """Turn a node id into a readable label for UI/tooltips."""
    parts = node_id.split(".")
    pretty_parts: List[str] = []
    for part in parts:
        mirrored = part.endswith("_m")
        base = part[:-2] if mirrored else part
        base = base.replace("_", " ").title()
        if mirrored:
            base = f"{base} (Mirror)"
        pretty_parts.append(base)
    return " \u00b7 ".join(pretty_parts)


# =============================================================================
# ENTITY-GRAPH BODY NODE QUERY
# =============================================================================

def _body_nodes_for_actor(game: Any, actor: Any) -> List[Dict[str, Any]]:
    """Return shared body-node rows for Chakra Scene callers."""
    return body_view_queries_system.body_nodes_for_owner(game, actor)


def _runtime_chakra_view(game: Any, actor: Any) -> chakra_items_system.ChakraViewState:
    """Return the preferred non-legacy runtime chakra view for scene reads."""
    if actor is None:
        return chakra_items_system.ChakraViewState()
    view = chakra_items_system.effective_chakra_view(game, actor)
    if view is None:
        return chakra_items_system.ChakraViewState()
    return view


# =============================================================================
# CHAKRA POINT DATA
# =============================================================================

@dataclass
class ChakraPoint:
    """Visual representation of a chakra point."""
    node_id: str
    pos_u: Tuple[float, float]  # Unit coordinates
    base_pos_u: Tuple[float, float]  # Unit coordinates without alignment offsets
    pos_px: Tuple[int, int]  # Pixel coordinates (computed during layout)
    state: str  # "locked", "unlocked", "active"
    local_scale: float  # Local layout scale for alignment math
    is_hovered: bool = False
    pulse_phase: float = 0.0  # For animation offset


@dataclass
class Particle:
    """A single particle for activation burst effects."""
    x: float
    y: float
    vx: float
    vy: float
    life: float  # 0.0 to 1.0 (1.0 = full life)
    color: Tuple[int, int, int]
    size: float


# =============================================================================
# CHAKRA SILHOUETTE WIDGET
# =============================================================================

class ChakraSilhouetteWidget(Widget):
    """
    Widget displaying the body with glowing chakra points.

    Features:
    - Body outline/silhouette
    - Chakra points with state-based coloring
    - 3-layer glow rendering
    - Pulse animation for active chakras
    - Energy flow lines between connected active chakras
    - Hover highlighting
    """

    def __init__(
        self,
        *,
        actor: Any = None,
        game: Any = None,
        state_provider: Optional[Callable[[], Any]] = None,
        on_chakra_click: Optional[callable] = None,
        on_chakra_hover: Optional[callable] = None,
        on_chakra_drag_start: Optional[callable] = None,
        on_chakra_drag: Optional[callable] = None,
        on_chakra_drag_end: Optional[callable] = None,
        on_drag_select: Optional[callable] = None,
    ) -> None:
        super().__init__()
        self.actor = actor
        self.game = game
        self._state_provider = state_provider
        self.on_chakra_click = on_chakra_click
        self.on_chakra_hover = on_chakra_hover
        self.on_chakra_drag_start = on_chakra_drag_start
        self.on_chakra_drag = on_chakra_drag
        self.on_chakra_drag_end = on_chakra_drag_end
        self.on_drag_select = on_drag_select

        # Cached chakra points (rebuilt on layout)
        self._chakra_points: Dict[str, ChakraPoint] = {}
        self._hovered_id: Optional[str] = None
        self._selected_id: Optional[str] = None
        self._selected_nodes: Set[str] = set()
        self._dragging_id: Optional[str] = None

        # Animation state
        self._anim_time_ms: int = 0
        self._px_per_unit: float = 1.0

        # Camera state for zoom navigation
        self._zoom_stack: List[str] = []  # Stack of node IDs we've zoomed into
        self._cam_center: Tuple[float, float] = (0.0, 0.0)
        self._cam_scale: float = 1.0
        self._zoom_anim: Optional[Tuple] = None  # (from_center, from_scale, to_center, to_scale, start_ms, dur_ms)

        # Parent-child connections for energy flow lines
        self._connections: List[Tuple[str, str]] = []

        # Particle system for activation bursts
        self._particles: List[Particle] = []

        # Optional state override (used for "realign preview" without committing)
        self.state_override: Optional[Any] = None

        # Current pattern root (for highlighting)
        self._pattern_root: Optional[str] = None

        # Realign mode visualization (optional)
        self.realign_mode: bool = False
        self.realign_radius: float = 0.0  # alignment units (not pixels)

        # Focus view disabled: we always render all chakras at every zoom level.
        self._focus_enabled: bool = False
        self._focus_node: Optional[str] = None
        self._visible_nodes: Optional[Set[str]] = None

        # Drag-select state (selection rectangle)
        self._drag_select_active: bool = False
        self._drag_select_start: Optional[Tuple[int, int]] = None
        self._drag_select_end: Optional[Tuple[int, int]] = None

    def set_actor(self, actor: Any) -> None:
        """Update the actor being displayed."""
        self.actor = actor
        self._chakra_points.clear()
        self._connections.clear()

    def set_state_override(self, state: Optional[Any]) -> None:
        """Override chakra state for preview (e.g., while realigning)."""
        self.state_override = state
        self._chakra_points.clear()
        self._connections.clear()

    def set_realign_mode(self, active: bool, max_align: float = 0.0) -> None:
        """Enable/disable realign mode visuals (alignment radius ring)."""
        self.realign_mode = bool(active)
        self.realign_radius = float(max_align)

    def set_selected_nodes(self, nodes: Set[str]) -> None:
        """Update the set of selected chakra nodes for highlighting."""
        self._selected_nodes = set(nodes)

    def set_focus_enabled(self, enabled: bool) -> None:
        """Focus view is disabled; keep all chakras visible."""
        self._focus_enabled = False
        self._visible_nodes = None

    def set_focus_node(self, node_id: Optional[str]) -> None:
        """Focus view is disabled; ignore focus node requests."""
        self._focus_node = None
        self._visible_nodes = None

    def _refresh_focus_visibility(self) -> None:
        """Focus view is disabled; all nodes remain visible."""
        self._visible_nodes = None

    def _is_visible(self, node_id: str) -> bool:
        """Always render chakras regardless of zoom."""
        return True

    def get_camera_scale(self) -> float:
        """Expose camera scale for auto-focus decisions."""
        return float(self._cam_scale)

    def _rebuild_chakra_points(self) -> None:
        """Rebuild chakra point data from body-node entities in the entity graph.

        Positions come from body_schema_rel_pos tags stored at entity creation
        time (body-graph-scale units), scaled by CHAKRA_LAYOUT_SCALE for UI
        display.  Alignment offsets from ChakraState are applied on top.

        Uses body_view_queries.body_nodes_for_owner, which falls back to
        deterministic entity_body specs when the realized graph is absent.
        """
        self._chakra_points.clear()
        self._connections.clear()

        if self.actor is None:
            return

        chakra_state = self._get_state()
        if getattr(chakra_state, "pattern_root", None) in chakra_state.active:
            self._pattern_root = chakra_state.pattern_root
        else:
            self._pattern_root = None

        node_list = _body_nodes_for_actor(self.game, self.actor)

        if not node_list:
            self._refresh_focus_visibility()
            return

        # Scale factor from body-graph-scale units to chakra UI unit space.
        actor_tags = getattr(self.actor, "tags", None) or {}
        body_graph_scale = float(actor_tags.get("body_graph_scale", 1.0) or 1.0)
        scale_factor = CHAKRA_LAYOUT_SCALE / max(1e-6, body_graph_scale)

        for node_data in node_list:
            full_id = node_data["full_id"]
            rel = node_data["schema_rel_pos"]
            local_scale_raw = node_data["local_scale"]

            base_x = float(rel[0]) * scale_factor
            base_y = float(rel[1]) * scale_factor
            chakra_local_scale = local_scale_raw * scale_factor

            align = chakra_state.alignments.get(full_id)
            if align and len(align) >= 2:
                pos_x = base_x + float(align[0]) * chakra_local_scale * 0.5
                pos_y = base_y + float(align[1]) * chakra_local_scale * 0.5
            else:
                pos_x, pos_y = base_x, base_y

            if full_id in chakra_state.active:
                state_str = "active"
            elif full_id in chakra_state.unlocked:
                state_str = "unlocked"
            else:
                state_str = "locked"

            self._chakra_points[full_id] = ChakraPoint(
                node_id=full_id,
                pos_u=(pos_x, pos_y),
                base_pos_u=(base_x, base_y),
                pos_px=(0, 0),
                state=state_str,
                local_scale=chakra_local_scale,
                pulse_phase=hash(full_id) % 1000 / 1000.0,
            )

        # Connections: read parent_full_id from entity graph data.
        for node_data in node_list:
            child_id = node_data["full_id"]
            parent_id = node_data["parent_full_id"]
            if (parent_id and parent_id in self._chakra_points
                    and child_id in self._chakra_points):
                self._connections.append((parent_id, child_id))

        self._refresh_focus_visibility()

    def _compute_pixel_positions(self) -> None:
        """Convert unit positions to pixel positions based on current camera."""
        if not self._chakra_points:
            return

        # Get animated camera state
        center_u, scale = self._get_animated_camera()

        # Map to pixel space within our rect
        cx_px = self.rect.centerx
        cy_px = self.rect.centery

        # Scale factor: how many pixels per unit
        min_dim = min(self.rect.width, self.rect.height) * 0.8
        px_per_unit = min_dim * scale
        self._px_per_unit = px_per_unit

        for point in self._chakra_points.values():
            # Transform from unit space to pixel space
            px = int(cx_px + (point.pos_u[0] - center_u[0]) * px_per_unit)
            py = int(cy_px + (point.pos_u[1] - center_u[1]) * px_per_unit)
            point.pos_px = (px, py)

    def _get_animated_camera(self) -> Tuple[Tuple[float, float], float]:
        """Get current camera state, interpolating if animating."""
        if self._zoom_anim is None:
            return self._cam_center, self._cam_scale

        from_center, from_scale, to_center, to_scale, start_ms, dur_ms = self._zoom_anim
        now = pygame.time.get_ticks()

        if dur_ms <= 0:
            t = 1.0
        else:
            t = (now - start_ms) / dur_ms

        t = smoothstep(max(0.0, min(1.0, t)))

        # Interpolate
        cx = lerp(from_center[0], to_center[0], t)
        cy = lerp(from_center[1], to_center[1], t)
        scale = lerp(from_scale, to_scale, t)

        # Clear animation when done
        if t >= 1.0:
            self._cam_center = to_center
            self._cam_scale = to_scale
            self._zoom_anim = None

        return (cx, cy), scale

    def layout(self, ctx: WidgetContext) -> None:
        """Rebuild chakra points and compute positions."""
        if not self._chakra_points:
            self._rebuild_chakra_points()
        self._compute_pixel_positions()
        super().layout(ctx)

    def update(self, dt_ms: int, ctx: WidgetContext) -> None:
        """Update animation time and particles."""
        self._anim_time_ms += dt_ms

        # Check if zoom animation is active
        if self._zoom_anim is not None:
            self._compute_pixel_positions()

        # Update particles
        self._update_particles(dt_ms)

        super().update(dt_ms, ctx)

    def _update_particles(self, dt_ms: int) -> None:
        """Update particle positions and lifetimes."""
        dt = dt_ms / 1000.0  # Convert to seconds
        decay = 2.5  # Life decay rate per second

        alive = []
        for p in self._particles:
            # Update position
            p.x += p.vx * dt
            p.y += p.vy * dt

            # Apply gravity/drag
            p.vy += 50 * dt  # Light gravity
            p.vx *= 0.98  # Drag
            p.vy *= 0.98

            # Decay life
            p.life -= decay * dt

            # Shrink size
            p.size *= 0.97

            if p.life > 0 and p.size > 0.5:
                alive.append(p)

        self._particles = alive

    def spawn_activation_burst(self, x: int, y: int, activating: bool) -> None:
        """
        Spawn a burst of particles at the given position.

        activating: True for outward burst (activation), False for inward collapse (deactivation)
        """
        import random

        num_particles = 16
        color = COLOR_ACTIVE if activating else COLOR_UNLOCKED

        for i in range(num_particles):
            angle = (i / num_particles) * 2 * math.pi + random.uniform(-0.2, 0.2)
            speed = random.uniform(80, 150) if activating else random.uniform(40, 80)

            if not activating:
                # Inward burst starts offset
                start_x = x + math.cos(angle) * 30
                start_y = y + math.sin(angle) * 30
                speed = -speed  # Negative for inward
            else:
                start_x = float(x)
                start_y = float(y)

            vx = math.cos(angle) * speed
            vy = math.sin(angle) * speed

            # Add some color variation
            r = min(255, max(0, color[0] + random.randint(-20, 20)))
            g = min(255, max(0, color[1] + random.randint(-20, 20)))
            b = min(255, max(0, color[2] + random.randint(-20, 20)))

            self._particles.append(Particle(
                x=start_x,
                y=start_y,
                vx=vx,
                vy=vy,
                life=1.0,
                color=(r, g, b),
                size=random.uniform(3, 6),
            ))

    def draw(self, ctx: WidgetContext) -> None:
        """Draw the body silhouette with chakra points."""
        if not self.visible:
            return

        surface = ctx.surface
        now_ms = self._anim_time_ms

        # Draw a translucent panel overlay (scene background is drawn separately)
        panel = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        panel.fill((12, 14, 22, 140))
        surface.blit(panel, self.rect.topleft)
        pygame.draw.rect(surface, (60, 65, 85), self.rect, 1)

        # Draw energy flow lines between connected active chakras
        self._draw_energy_flows(surface, now_ms)

        # Draw chakra points (with glow)
        for point in self._chakra_points.values():
            if not self._is_visible(point.node_id):
                continue
            self._draw_chakra_point(surface, point, now_ms)

        # Drag-select rectangle overlay
        if self._drag_select_active and self._drag_select_start and self._drag_select_end:
            x0, y0 = self._drag_select_start
            x1, y1 = self._drag_select_end
            rect = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            if rect.width > 2 and rect.height > 2:
                overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
                overlay.fill((80, 140, 220, 30))
                surface.blit(overlay, rect.topleft)
                pygame.draw.rect(surface, (110, 160, 230), rect, 1)

        # Draw particles on top
        self._draw_particles(surface)

        super().draw(ctx)

    def _draw_particles(self, surface: pygame.Surface) -> None:
        """Draw all active particles."""
        for p in self._particles:
            if p.life <= 0:
                continue

            # Alpha based on life
            alpha = int(255 * p.life)
            size = int(p.size)

            if size <= 0:
                continue

            # Create particle surface with glow
            glow_size = size * 2
            glow_surf = pygame.Surface((glow_size * 2, glow_size * 2), pygame.SRCALPHA)

            # Outer glow
            pygame.draw.circle(
                glow_surf,
                (*p.color, alpha // 3),
                (glow_size, glow_size),
                glow_size
            )

            # Core
            pygame.draw.circle(
                glow_surf,
                (*p.color, alpha),
                (glow_size, glow_size),
                size
            )

            # Blit with additive blending
            surface.blit(
                glow_surf,
                (int(p.x) - glow_size, int(p.y) - glow_size),
                special_flags=pygame.BLEND_ADD
            )

    def _draw_energy_flows(self, surface: pygame.Surface, now_ms: int) -> None:
        """Draw energy flow lines between connected active chakras."""
        chakra_state = self._get_state()

        for parent_id, child_id in self._connections:
            if not self._is_visible(parent_id) or not self._is_visible(child_id):
                continue
            parent = self._chakra_points.get(parent_id)
            child = self._chakra_points.get(child_id)

            if not parent or not child:
                continue

            # Only draw flow if both are active
            both_active = parent_id in chakra_state.active and child_id in chakra_state.active

            # Base line (always visible but dimmer for inactive)
            alpha = 80 if both_active else 20
            color = (*COLOR_ENERGY_FLOW, alpha)

            # Draw base line
            pygame.draw.aaline(
                surface,
                color[:3],  # aaline doesn't support alpha
                parent.pos_px,
                child.pos_px,
            )

            # Draw traveling pulse for active connections
            if both_active:
                self._draw_flow_pulse(surface, parent.pos_px, child.pos_px, now_ms)

    def _draw_flow_pulse(
        self,
        surface: pygame.Surface,
        start: Tuple[int, int],
        end: Tuple[int, int],
        now_ms: int
    ) -> None:
        """Draw a traveling pulse along an energy flow line."""
        # Pulse travels along line over ENERGY_FLOW_PERIOD_MS
        t = (now_ms % ENERGY_FLOW_PERIOD_MS) / ENERGY_FLOW_PERIOD_MS

        # Position along line
        px = int(lerp(start[0], end[0], t))
        py = int(lerp(start[1], end[1], t))

        # Draw pulse glow
        pulse_radius = 4
        pulse_color = (220, 200, 255)

        # Outer glow
        glow_surf = pygame.Surface((pulse_radius * 4, pulse_radius * 4), pygame.SRCALPHA)
        pygame.draw.circle(glow_surf, (*pulse_color, 40), (pulse_radius * 2, pulse_radius * 2), pulse_radius * 2)
        surface.blit(glow_surf, (px - pulse_radius * 2, py - pulse_radius * 2), special_flags=pygame.BLEND_ADD)

        # Core
        pygame.draw.circle(surface, pulse_color, (px, py), pulse_radius)

    def _draw_chakra_point(
        self,
        surface: pygame.Surface,
        point: ChakraPoint,
        now_ms: int
    ) -> None:
        """Draw a single chakra point with glow effect."""
        px, py = point.pos_px

        # Determine base color and alpha based on state
        if point.state == "active":
            base_color = COLOR_ACTIVE
            base_alpha = 255
            # Pulse animation for active chakras
            phase = ((now_ms / PULSE_PERIOD_MS) + point.pulse_phase) * 2 * math.pi
            pulse = 0.85 + 0.15 * math.sin(phase)
            radius_mul = 0.9 + 0.1 * pulse
        elif point.state == "unlocked":
            base_color = COLOR_UNLOCKED
            base_alpha = 200
            pulse = 1.0
            radius_mul = 1.0
        else:  # locked
            base_color = COLOR_LOCKED
            base_alpha = 150
            pulse = 1.0
            radius_mul = 0.8

        # Hover highlight
        if point.node_id == self._hovered_id:
            base_color = lerp_rgb(base_color, COLOR_HOVER, 0.4)
            base_alpha = 255
            # Extra soft halo for hover (helps eye find tooltips quickly)
            halo_radius = int(BASE_CHAKRA_RADIUS * 3.0)
            halo_surf = pygame.Surface((halo_radius * 2, halo_radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(
                halo_surf,
                (190, 210, 255, 40),
                (halo_radius, halo_radius),
                halo_radius,
            )
            surface.blit(
                halo_surf,
                (px - halo_radius, py - halo_radius),
                special_flags=pygame.BLEND_ADD,
            )

        # Selected highlight (primary)
        if point.node_id == self._selected_id:
            # Draw selection ring
            pygame.draw.circle(surface, COLOR_HOVER, (px, py), int(BASE_CHAKRA_RADIUS * 1.8 * radius_mul), 2)

            # Realign mode: show allowed alignment radius ring
            if self.realign_mode and self.realign_radius > 0:
                # Alignment offsets are scaled by (local_scale * 0.5) in chakra math.
                # Convert the alignment limit into pixels for this node.
                align_radius_px = int(
                    self.realign_radius * point.local_scale * 0.5 * self._px_per_unit
                )
                if align_radius_px > 2:
                    pygame.draw.circle(
                        surface,
                        (120, 150, 200),
                        (px, py),
                        align_radius_px,
                        1,
                    )

        # Multi-selection ring (thin gold halo for non-primary selections)
        if point.node_id in self._selected_nodes and point.node_id != self._selected_id:
            pygame.draw.circle(
                surface,
                (230, 200, 120),
                (px, py),
                int(BASE_CHAKRA_RADIUS * 1.4 * radius_mul),
                1,
            )

        # Pattern root highlight (thin cyan ring)
        if point.node_id == self._pattern_root:
            pygame.draw.circle(
                surface,
                (120, 220, 255),
                (px, py),
                int(BASE_CHAKRA_RADIUS * 2.1 * radius_mul),
                1,
            )

        base_radius = int(BASE_CHAKRA_RADIUS * radius_mul)

        # Draw glow layers (outer to inner)
        for radius_mul_layer, alpha_mul in GLOW_LAYERS:
            r = int(base_radius * radius_mul_layer)
            a = int(base_alpha * alpha_mul * pulse)

            if r <= 0 or a <= 0:
                continue

            # Create glow surface
            glow_surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (*base_color, a), (r, r), r)

            # Blit with additive blending for glow effect
            surface.blit(
                glow_surf,
                (px - r, py - r),
                special_flags=pygame.BLEND_ADD
            )

        # Draw lock indicator for locked chakras
        if point.state == "locked":
            self._draw_lock_icon(surface, px, py, base_radius)

    def _draw_lock_icon(self, surface: pygame.Surface, x: int, y: int, radius: int) -> None:
        """Draw a small lock indicator."""
        # Simple lock shape
        lock_size = max(4, radius // 2)
        lock_color = (100, 100, 110)

        # Lock body
        body_rect = pygame.Rect(
            x - lock_size // 2,
            y - lock_size // 4,
            lock_size,
            lock_size * 3 // 4
        )
        pygame.draw.rect(surface, lock_color, body_rect)

        # Lock shackle
        shackle_rect = pygame.Rect(
            x - lock_size // 3,
            y - lock_size,
            lock_size * 2 // 3,
            lock_size * 3 // 4
        )
        pygame.draw.arc(surface, lock_color, shackle_rect, 0, math.pi, 2)

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        """Handle mouse events for chakra interaction."""
        if not (self.visible and self.enabled):
            return False

        if event.type == pygame.MOUSEMOTION:
            pos = getattr(event, "pos", None)
            if pos and self.rect.collidepoint(pos):
                if self._drag_select_active:
                    self._drag_select_end = pos
                    return True

                # Find hovered chakra
                old_hovered = self._hovered_id
                self._hovered_id = self._get_chakra_at(pos)

                if self._hovered_id != old_hovered and self.on_chakra_hover:
                    self.on_chakra_hover(self._hovered_id)

                if self._dragging_id and self.on_chakra_drag:
                    self.on_chakra_drag(self._dragging_id, pos)
                    return True

                return True
            else:
                self._hovered_id = None
                if self._drag_select_active and pos:
                    self._drag_select_end = pos
                    return True
                # Continue drag even if cursor leaves the widget
                if self._dragging_id and self.on_chakra_drag and pos:
                    self.on_chakra_drag(self._dragging_id, pos)
                    return True

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = getattr(event, "pos", None)
            if pos and self.rect.collidepoint(pos):
                clicked_id = self._get_chakra_at(pos)
                if clicked_id:
                    # Drag start (if handler wants to capture)
                    if self.on_chakra_drag_start:
                        try:
                            if self.on_chakra_drag_start(clicked_id, pos):
                                self._dragging_id = clicked_id
                                return True
                        except Exception:
                            pass

                    # Fall back to click behavior
                    if self.on_chakra_click:
                        self.on_chakra_click(clicked_id)
                        return True
                else:
                    # Begin drag-select in empty space
                    self._drag_select_active = True
                    self._drag_select_start = pos
                    self._drag_select_end = pos
                    return True

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._drag_select_active and self._drag_select_start and self._drag_select_end:
                x0, y0 = self._drag_select_start
                x1, y1 = self._drag_select_end
                rect = pygame.Rect(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                if rect.width >= 2 and rect.height >= 2 and self.on_drag_select:
                    picked: Set[str] = set()
                    for point in self._chakra_points.values():
                        if rect.collidepoint(point.pos_px):
                            picked.add(point.node_id)
                    try:
                        mods = pygame.key.get_mods()
                    except Exception:
                        mods = 0
                    try:
                        self.on_drag_select(picked, mods)
                    except Exception:
                        pass

                self._drag_select_active = False
                self._drag_select_start = None
                self._drag_select_end = None
                return True

            if self._dragging_id:
                if self.on_chakra_drag_end and hasattr(event, "pos"):
                    try:
                        self.on_chakra_drag_end(self._dragging_id, event.pos)
                    except Exception:
                        pass
                self._dragging_id = None
                return True

        return super().handle_event(event, ctx)

    def _get_state(self) -> Any:
        """Return state override when present, otherwise actor state."""
        if self.state_override is not None:
            return self.state_override
        if self._state_provider is not None:
            try:
                state = self._state_provider()
                if _is_chakra_state_like(state):
                    return state
            except Exception:
                pass
        if self.actor is None:
            return chakra_items_system.ChakraViewState()
        return _runtime_chakra_view(self.game, self.actor)

    def screen_to_unit(self, pos_px: Tuple[int, int]) -> Tuple[float, float]:
        """Convert a pixel position to unit coordinates in chakra space."""
        center_u, scale = self._get_animated_camera()
        if self._px_per_unit <= 0:
            return center_u

        cx_px = self.rect.centerx
        cy_px = self.rect.centery

        ux = (pos_px[0] - cx_px) / self._px_per_unit + center_u[0]
        uy = (pos_px[1] - cy_px) / self._px_per_unit + center_u[1]
        return (ux, uy)

    def get_hovered_chakra(self) -> Optional[str]:
        """Expose the currently hovered chakra id (for tooltips)."""
        return self._hovered_id

    def get_chakra_point(self, node_id: str) -> Optional[ChakraPoint]:
        """Return the ChakraPoint for a node id if present."""
        return self._chakra_points.get(node_id)

    def refresh_points(self) -> None:
        """Force a rebuild of chakra points and pixel positions."""
        self._chakra_points.clear()
        self._connections.clear()
        self._rebuild_chakra_points()
        self._compute_pixel_positions()

    def _get_chakra_at(self, pos: Tuple[int, int]) -> Optional[str]:
        """Get the chakra ID at the given pixel position."""
        click_radius = BASE_CHAKRA_RADIUS * 1.5
        best_id: Optional[str] = None
        best_d2 = float("inf")

        for point in self._chakra_points.values():
            if not self._is_visible(point.node_id):
                continue
            dx = pos[0] - point.pos_px[0]
            dy = pos[1] - point.pos_px[1]
            dist_sq = dx * dx + dy * dy

            if dist_sq <= click_radius * click_radius and dist_sq < best_d2:
                best_d2 = dist_sq
                best_id = point.node_id

        return best_id

    def select_chakra(self, node_id: Optional[str]) -> None:
        """Set the selected chakra for keyboard navigation."""
        self._selected_id = node_id

    def get_selected_chakra(self) -> Optional[str]:
        """Get the currently selected chakra ID."""
        return self._selected_id

    def reset_zoom(self) -> None:
        """Reset the camera zoom to the default."""
        self._cam_scale = 1.0
        self._zoom_anim = None
        self._compute_pixel_positions()

    def adjust_zoom(self, direction: int, anchor_px: Optional[Tuple[int, int]] = None) -> None:
        """Zoom in/out while keeping an optional anchor point stable."""
        if direction == 0:
            return

        current = self._cam_scale
        if direction > 0:
            target = current * CAMERA_ZOOM_STEP
        else:
            target = current / CAMERA_ZOOM_STEP

        target = max(CAMERA_MIN_SCALE, min(CAMERA_MAX_SCALE, target))
        if abs(target - current) < 1e-4:
            return

        # Keep a specific point stable in screen space if requested
        if anchor_px is not None and self.rect.width > 0 and self.rect.height > 0:
            center_u, _ = self._get_animated_camera()
            u_anchor = self.screen_to_unit(anchor_px)

            # Recompute px_per_unit for the target scale
            min_dim = min(self.rect.width, self.rect.height) * 0.8
            new_px_per_unit = min_dim * target
            if new_px_per_unit > 0:
                cx_px = self.rect.centerx
                cy_px = self.rect.centery
                new_center_x = u_anchor[0] - (anchor_px[0] - cx_px) / new_px_per_unit
                new_center_y = u_anchor[1] - (anchor_px[1] - cy_px) / new_px_per_unit
                self._cam_center = (new_center_x, new_center_y)

        self._cam_scale = target
        self._zoom_anim = None
        self._compute_pixel_positions()

    def get_adjacent_chakra(self, direction: str) -> Optional[str]:
        """
        Get the chakra adjacent to the current selection in the given direction.

        direction: "up", "down", "left", "right"
        """
        if not self._selected_id or self._selected_id not in self._chakra_points:
            # Select first available if nothing selected
            if self._chakra_points:
                return next(iter(self._chakra_points.keys()))
            return None

        current = self._chakra_points[self._selected_id]
        cx, cy = current.pos_px

        best_id = None
        best_score = float("inf")

        for point in self._chakra_points.values():
            if point.node_id == self._selected_id:
                continue

            dx = point.pos_px[0] - cx
            dy = point.pos_px[1] - cy

            # Score based on direction preference
            if direction == "up" and dy < 0:
                score = abs(dx) - dy  # Prefer directly above
            elif direction == "down" and dy > 0:
                score = abs(dx) + dy
            elif direction == "left" and dx < 0:
                score = abs(dy) - dx
            elif direction == "right" and dx > 0:
                score = abs(dy) + dx
            else:
                continue

            if score < best_score:
                best_score = score
                best_id = point.node_id

        return best_id


# =============================================================================
# PATTERN PREVIEW WIDGET
# =============================================================================

class PatternPreviewWidget(Widget):
    """
    Widget showing a real-time preview of the pattern generated from active chakras.

    Updates whenever chakras are toggled, with a smooth morph animation.
    """

    def __init__(
        self,
        *,
        actor: Any = None,
        game: Any = None,
        state_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        super().__init__()
        self.actor = actor
        self.game = game
        self._state_provider = state_provider
        self._pattern_surface: Optional[pygame.Surface] = None
        self._dirty: bool = True
        self._anim_time_ms: int = 0
        self.state_override: Optional[Any] = None

    def set_actor(self, actor: Any) -> None:
        """Update the actor and mark pattern as dirty."""
        self.actor = actor
        self._dirty = True

    def set_state_override(self, state: Optional[Any]) -> None:
        """Override chakra state for preview (e.g., while realigning)."""
        self.state_override = state
        self._dirty = True

    def mark_dirty(self) -> None:
        """Mark the pattern as needing regeneration."""
        self._dirty = True

    def update(self, dt_ms: int, ctx: WidgetContext) -> None:
        """Update animation."""
        self._anim_time_ms += dt_ms
        super().update(dt_ms, ctx)

    def draw(self, ctx: WidgetContext) -> None:
        """Draw the pattern preview."""
        if not self.visible:
            return

        surface = ctx.surface

        # Background (transparent panel over the scene backdrop)
        panel = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        panel.fill((10, 12, 18, 160))
        surface.blit(panel, self.rect.topleft)
        pygame.draw.rect(surface, (60, 65, 85), self.rect, 1)

        # Subtle grid for compositional framing
        _draw_subtle_grid(surface, self.rect, step=56)

        # Header
        font = getattr(ctx.renderer, "small_font", None) or getattr(ctx.renderer, "font", None)
        if font:
            label = font.render("Pattern Preview", True, (180, 180, 200))
            lx = self.rect.x + (self.rect.width - label.get_width()) // 2
            surface.blit(label, (lx, self.rect.y + 8))

        # Regenerate pattern if dirty
        if self._dirty:
            self._regenerate_pattern()
            self._dirty = False

        # Draw pattern
        if self._pattern_surface:
            # Center in preview area
            preview_rect = pygame.Rect(
                self.rect.x + 10,
                self.rect.y + 30,
                self.rect.width - 20,
                self.rect.height - 50
            )

            # Scale to fit
            pw = self._pattern_surface.get_width()
            ph = self._pattern_surface.get_height()
            if pw > 0 and ph > 0:
                scale = min(preview_rect.width / pw, preview_rect.height / ph, 1.0)
                if scale < 1.0:
                    sw = max(1, int(pw * scale))
                    sh = max(1, int(ph * scale))
                    scaled = pygame.transform.smoothscale(self._pattern_surface, (sw, sh))
                else:
                    scaled = self._pattern_surface

                # Center
                px = preview_rect.x + (preview_rect.width - scaled.get_width()) // 2
                py = preview_rect.y + (preview_rect.height - scaled.get_height()) // 2
                surface.blit(scaled, (px, py))

        super().draw(ctx)

    def _regenerate_pattern(self) -> None:
        """Regenerate the pattern surface from active chakras."""
        if self.actor is None:
            self._pattern_surface = None
            return

        _preview_game = getattr(self, "game", None)

        if self.state_override is not None:
            chakra_state = self.state_override
        elif self._state_provider is not None:
            try:
                chakra_state = self._state_provider()
            except Exception:
                chakra_state = None
        else:
            chakra_state = None

        if not _is_chakra_state_like(chakra_state):
            chakra_state = _runtime_chakra_view(_preview_game, self.actor)

        try:
            # Canonical actor-backed seed helper used by runtime cast as well.
            # Keeping preview on the same helper avoids query-surface drift.
            from edgecaster.systems.chakras import build_chakra_generator_seed_for_actor

            seed = build_chakra_generator_seed_for_actor(
                self.actor,
                chakra_state=chakra_state,
                base_scale=1.0,
                game=_preview_game,
                # Match runtime cast behavior exactly so preview is WYSIWYG.
                require_root=True,
            )
        except Exception:
            self._pattern_surface = None
            return

        # Create pattern surface
        size = min(self.rect.width - 20, self.rect.height - 50)
        if size <= 0:
            size = 100

        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        center = size // 2

        # Render the exact normalized custom graph that runtime applies.
        # This makes the right-side preview a true WYSIWYG of Chakra cast shape.
        xs = [p[0] for p in seed.verts]
        ys = [p[1] for p in seed.verts]
        if not xs or not ys:
            self._pattern_surface = surf
            return

        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)
        span_x = max(1e-6, max_x - min_x)
        span_y = max(1e-6, max_y - min_y)
        # Keep generous margins so long, thin graphs remain readable.
        target_w = size * 0.72
        target_h = size * 0.72
        scale = min(target_w / span_x, target_h / span_y)

        # Map positions to surface coordinates
        graph_cx = 0.5 * (min_x + max_x)
        graph_cy = 0.5 * (min_y + max_y)
        mapped: List[Tuple[int, int]] = []
        for (x, y) in seed.verts:
            px = int(center + (x - graph_cx) * scale)
            py = int(center + (y - graph_cy) * scale)
            mapped.append((px, py))

        # Draw edges (same edge index pairs used by runtime custom generator)
        edge_color = (100, 180, 220, 180)
        for a_idx, b_idx in seed.edges:
            if not (0 <= a_idx < len(mapped) and 0 <= b_idx < len(mapped)):
                continue
            pygame.draw.aaline(
                surf,
                edge_color[:3],
                mapped[a_idx],
                mapped[b_idx],
            )

        # Draw vertices
        default_vertex_color = (255, 220, 100)
        root_vertex_color = (170, 230, 255)
        term_vertex_color = (255, 210, 140)
        root_idx = 0
        term_idx = len(mapped) - 1
        for i, pos in enumerate(mapped):
            if i == root_idx:
                vertex_color = root_vertex_color
            elif i == term_idx:
                vertex_color = term_vertex_color
            else:
                vertex_color = default_vertex_color
            # Glow
            glow_surf = pygame.Surface((16, 16), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (*vertex_color, 60), (8, 8), 8)
            surf.blit(glow_surf, (pos[0] - 8, pos[1] - 8), special_flags=pygame.BLEND_ADD)

            # Core
            pygame.draw.circle(surf, vertex_color, pos, 4)

        self._pattern_surface = surf


# =============================================================================
# CHAKRA LIST WIDGET
# =============================================================================

@dataclass
class ChakraListEntry:
    node_id: str
    label: str
    depth: int


class ChakraListWidget(Widget):
    """
    Scrollable list view of all chakras (for precision selection).

    - Shows active/unlocked/locked state with colored markers
    - Highlights current selection set
    - Supports click selection with modifier keys
    """

    def __init__(
        self,
        *,
        items: Optional[List[ChakraListEntry]] = None,
        get_state: Optional[callable] = None,
        on_select: Optional[callable] = None,
    ) -> None:
        super().__init__()
        self.items = items or []
        self.get_state = get_state
        self.on_select = on_select
        self._selected_nodes: Set[str] = set()
        self.scroll_offset: int = 0
        self.padding: int = 8
        self.line_spacing: int = 4
        self._line_height: int = 20
        self._hover_index: Optional[int] = None

    def set_items(self, items: List[ChakraListEntry]) -> None:
        self.items = list(items)
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, len(self.items) - 1)))

    def set_selected_nodes(self, nodes: Set[str]) -> None:
        self._selected_nodes = set(nodes)

    def _visible_capacity(self) -> int:
        if self._line_height <= 0:
            return max(1, len(self.items))
        usable_h = max(1, self.rect.height - 2 * self.padding)
        return max(1, usable_h // self._line_height)

    def _pick_index_at(self, pos: Tuple[int, int]) -> Optional[int]:
        if not self.rect.collidepoint(pos):
            return None
        rel_y = pos[1] - (self.rect.y + self.padding)
        if rel_y < 0:
            return None
        idx_in_view = int(rel_y // max(1, self._line_height))
        idx = self.scroll_offset + idx_in_view
        if 0 <= idx < len(self.items):
            return idx
        return None

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        if not (self.visible and self.enabled):
            return False

        if event.type == pygame.MOUSEWHEEL:
            if self.rect.collidepoint(pygame.mouse.get_pos()):
                if event.y != 0:
                    self.scroll_offset = max(0, min(self.scroll_offset - event.y, max(0, len(self.items) - 1)))
                    return True

        if event.type == pygame.MOUSEMOTION and hasattr(event, "pos"):
            idx = self._pick_index_at(event.pos)
            self._hover_index = idx

        if event.type == pygame.MOUSEBUTTONDOWN and getattr(event, "button", None) == 1:
            pos = getattr(event, "pos", None)
            if pos is None:
                return False
            idx = self._pick_index_at(pos)
            if idx is None:
                return False
            if 0 <= idx < len(self.items):
                entry = self.items[idx]
                try:
                    mods = pygame.key.get_mods()
                except Exception:
                    mods = 0
                if self.on_select:
                    try:
                        self.on_select(entry.node_id, mods)
                    except Exception:
                        pass
                return True

        return super().handle_event(event, ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        surface = ctx.surface

        panel = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        panel.fill((12, 14, 22, 170))
        surface.blit(panel, self.rect.topleft)
        pygame.draw.rect(surface, (60, 65, 85), self.rect, 1)

        font = getattr(ctx.renderer, "small_font", None) or getattr(ctx.renderer, "font", None)
        if font is None:
            return

        # Title
        title = font.render("Chakra List", True, (170, 180, 205))
        surface.blit(title, (self.rect.x + self.padding, self.rect.y + 4))

        # Layout items
        self._line_height = font.get_height() + self.line_spacing
        cap = self._visible_capacity()
        start = self.scroll_offset
        end = min(len(self.items), start + cap)

        state = self.get_state() if self.get_state else None

        y = self.rect.y + self.padding + title.get_height() + 6
        for idx in range(start, end):
            entry = self.items[idx]
            node_id = entry.node_id
            depth = entry.depth

            # Row highlight
            if node_id in self._selected_nodes:
                row_rect = pygame.Rect(self.rect.x + 2, y - 2, self.rect.width - 4, self._line_height)
                pygame.draw.rect(surface, (40, 50, 80), row_rect)
            elif self._hover_index == idx:
                row_rect = pygame.Rect(self.rect.x + 2, y - 2, self.rect.width - 4, self._line_height)
                pygame.draw.rect(surface, (26, 32, 48), row_rect)

            # State color
            if state and node_id in state.active:
                dot_col = COLOR_ACTIVE
            elif state and node_id in state.unlocked:
                dot_col = COLOR_UNLOCKED
            else:
                dot_col = COLOR_LOCKED

            dot_x = self.rect.x + self.padding + depth * 14
            dot_y = y + self._line_height // 2 - 1
            pygame.draw.circle(surface, dot_col, (dot_x, dot_y), 4)

            label = entry.label
            label_surf = font.render(label, True, (210, 220, 235))
            surface.blit(label_surf, (dot_x + 10, y))

            y += self._line_height

        super().draw(ctx)

# =============================================================================
# INFO PANEL WIDGET
# =============================================================================

class ChakraInfoWidget(Widget):
    """Widget showing information about the selected/hovered chakra."""

    def __init__(self) -> None:
        super().__init__()
        self._node_id: Optional[str] = None
        self._node_state: str = "locked"
        self._resonances: List[str] = []

    def set_chakra(self, node_id: Optional[str], state: str = "locked") -> None:
        """Set the chakra to display info for."""
        self._node_id = node_id
        self._node_state = state

    def set_resonances(self, resonances: List[str]) -> None:
        """Set active resonance bonuses to display."""
        self._resonances = list(resonances)

    def draw(self, ctx: WidgetContext) -> None:
        """Draw chakra info panel."""
        if not self.visible:
            return

        surface = ctx.surface

        # Background
        pygame.draw.rect(surface, (20, 25, 35, 200), self.rect)
        pygame.draw.rect(surface, (80, 80, 100), self.rect, 1)

        font = getattr(ctx.renderer, "small_font", None) or getattr(ctx.renderer, "font", None)
        if not font:
            return

        y = self.rect.y + 8
        x = self.rect.x + 10

        if self._node_id:
            # Chakra name
            name = self._node_id.replace("_", " ").title()
            name_surf = font.render(name, True, (255, 255, 255))
            surface.blit(name_surf, (x, y))
            y += name_surf.get_height() + 4

            # State
            state_colors = {
                "locked": COLOR_LOCKED,
                "unlocked": COLOR_UNLOCKED,
                "active": COLOR_ACTIVE,
            }
            state_color = state_colors.get(self._node_state, (150, 150, 150))
            state_text = f"Status: {self._node_state.title()}"
            state_surf = font.render(state_text, True, state_color)
            surface.blit(state_surf, (x, y))
            y += state_surf.get_height() + 4

            # Controls hint
            hint = "[Space] Toggle" if self._node_state != "locked" else "[Locked]"
            hint_surf = font.render(hint, True, (120, 120, 140))
            surface.blit(hint_surf, (x, y))
        else:
            # No selection
            hint_surf = font.render("Select a chakra", True, (100, 100, 120))
            surface.blit(hint_surf, (x, y))

        # Draw resonance indicators if any
        if self._resonances:
            y = self.rect.y + 8
            rx = self.rect.right - 10

            for resonance in self._resonances[:3]:  # Max 3
                label = resonance.replace("_", " ").title()
                res_surf = font.render(label, True, (180, 255, 180))
                rx2 = rx - res_surf.get_width()
                surface.blit(res_surf, (rx2, y))
                y += res_surf.get_height() + 2

        super().draw(ctx)


# =============================================================================
# MAIN CHAKRA SELECTION SCENE
# =============================================================================

class ChakraSelectionScene(PanelScene):
    """
    Main scene for chakra management.

    Layout:
    - Left 60%: Body silhouette with chakra points (ChakraSilhouetteWidget)
    - Right 40%: Pattern preview (PatternPreviewWidget)
    - Bottom: Info panel and controls (ChakraInfoWidget)

    Input:
    - Arrow keys / WASD: Navigate between chakras
    - Space / Enter: Toggle selected chakra active/inactive
    - Tab: Mirror mode (toggle both sides)
    - Backspace: Zoom out
    - Esc: Close scene
    """

    uses_live_loop: bool = True
    music_key = "chakric"
    music_loop = True


    def __init__(self, *, game: Any = None) -> None:
        super().__init__()
        self.game = game

        # Get player actor
        self._actor: Optional[Any] = None
        if game:
            # The game stores player_id, and actual actor is in level.actors
            try:
                player_id = getattr(game, "player_id", None)
                if player_id:
                    level = game._level()
                    self._actor = level.actors.get(player_id)
            except Exception:
                pass

        # Mode state: "activate" (toggle) or "realign" (drag/commit)
        self._mode: str = "activate"
        self._pending_alignments: Optional[Dict[str, Tuple[float, float]]] = None
        self._original_alignments: Optional[Dict[str, Tuple[float, float]]] = None
        self._working_session: Optional[ChakraEditSession] = None

        # Selection + undo
        self._selected_nodes: Set[str] = set()
        self._undo_stack: List[dict] = []  # ChakraComponent snapshots (from comp.to_dict())

        # Optional list view state
        self._list_view_enabled: bool = False
        self._focus_view_enabled: bool = False

        # Create widgets
        self._silhouette = ChakraSilhouetteWidget(
            actor=self._actor,
            game=self.game,
            state_provider=self._get_ui_state,
            on_chakra_click=self._on_chakra_click,
            on_chakra_hover=self._on_chakra_hover,
            on_chakra_drag_start=self._on_chakra_drag_start,
            on_chakra_drag=self._on_chakra_drag,
            on_chakra_drag_end=self._on_chakra_drag_end,
            on_drag_select=self._on_drag_select,
        )

        self._preview = PatternPreviewWidget(
            actor=self._actor,
            game=self.game,
            state_provider=self._get_ui_state,
        )
        self._list_widget = ChakraListWidget(
            items=[],
            get_state=self._get_ui_state,
            on_select=self._on_list_select,
        )
        self._info = ChakraInfoWidget()

        # Commit/cancel buttons for realign mode
        self._btn_commit = ButtonWidget("Commit", on_click=self._on_commit_click)
        self._btn_cancel = ButtonWidget("Cancel", on_click=self._on_cancel_click)

        # Selection action buttons (activate mode)
        self._btn_activate = ButtonWidget("Activate", on_click=lambda _b: self._apply_selection_action("activate"))
        self._btn_deactivate = ButtonWidget("Deactivate", on_click=lambda _b: self._apply_selection_action("deactivate"))
        self._btn_toggle = ButtonWidget("Toggle", on_click=lambda _b: self._apply_selection_action("toggle"))
        self._btn_clear = ButtonWidget("Clear", on_click=lambda _b: self._apply_selection_action("clear"))
        self._btn_root = ButtonWidget("Set Root", on_click=lambda _b: self._set_pattern_root())
        self._btn_undo = ButtonWidget("Undo", on_click=lambda _b: self._undo_last())
        self._btn_list = ButtonWidget("List", on_click=lambda _b: self._toggle_list_view())

        # Build widget tree
        self.root.add_child(self._silhouette)
        self.root.add_child(self._preview)
        self.root.add_child(self._list_widget)
        self.root.add_child(self._info)
        self.root.add_child(self._btn_commit)
        self.root.add_child(self._btn_cancel)
        self.root.add_child(self._btn_activate)
        self.root.add_child(self._btn_deactivate)
        self.root.add_child(self._btn_toggle)
        self.root.add_child(self._btn_clear)
        self.root.add_child(self._btn_root)
        self.root.add_child(self._btn_undo)
        self.root.add_child(self._btn_list)

        # Input handling
        self._menu_input = MenuInput()

        # Select first unlocked chakra
        self._select_initial_chakra()

        # Background cache for the scene glow-up
        self._bg_cache: Optional[pygame.Surface] = None
        self._bg_cache_key: Optional[Tuple[int, int, int, int]] = None

    def _select_initial_chakra(self) -> None:
        """Select the first unlocked chakra for keyboard navigation."""
        if not self._actor:
            return

        chakra_state = self._get_ui_state()

        # Prefer "body" (the core/torso) as starting point
        if "body" in chakra_state.unlocked:
            self._set_selection({"body"}, "body")
        elif chakra_state.unlocked:
            first = next(iter(chakra_state.unlocked))
            self._set_selection({first}, first)

        self._refresh_list_items()

    def _update_info_for_chakra(self, node_id: Optional[str]) -> None:
        """Update the info panel for a chakra."""
        if not node_id or not self._actor:
            self._info.set_chakra(None)
            return

        chakra_state = self._get_ui_state()

        if node_id in chakra_state.active:
            state = "active"
        elif node_id in chakra_state.unlocked:
            state = "unlocked"
        else:
            state = "locked"

        self._info.set_chakra(node_id, state)

        # Update resonances using active-node set (no body_schema walk needed).
        resonances = check_resonance_bonuses_from_active_nodes(chakra_state.active)
        self._info.set_resonances(resonances)

    def _set_selection(self, nodes: Set[str], primary: Optional[str]) -> None:
        """Apply selection state and keep UI widgets in sync."""
        if primary is None and nodes:
            primary = next(iter(nodes))
        if primary and primary not in nodes:
            nodes = set(nodes)
            nodes.add(primary)

        self._selected_nodes = set(nodes)
        self._silhouette.set_selected_nodes(self._selected_nodes)
        self._list_widget.set_selected_nodes(self._selected_nodes)

        self._silhouette.select_chakra(primary)
        self._update_info_for_chakra(primary)

        # Focus view disabled: keep all chakras visible.

    def _refresh_list_items(self) -> None:
        """Rebuild list view entries from body-node entities in the entity graph."""
        if not self._actor:
            self._list_widget.set_items([])
            return
        chakra_state = self._get_ui_state()

        node_list = _body_nodes_for_actor(self.game, self._actor)

        # Build a lookup so we can walk parent chains for visibility gating.
        node_by_id = {nd["full_id"]: nd for nd in node_list}

        # Determine visibility: a sub-schema node (full_id contains a dot
        # whose prefix is a branch root) is only shown when that branch root
        # is in chakra_state.unlocked.
        def _is_visible(full_id: str) -> bool:
            parts = full_id.split(".")
            for i in range(1, len(parts)):
                ancestor = ".".join(parts[:i])
                anc_data = node_by_id.get(ancestor)
                proto = anc_data["node_proto_id"] if anc_data else ""
                if is_branch_root(proto) and ancestor not in chakra_state.unlocked:
                    return False
            return True

        entries: List[ChakraListEntry] = []
        for nd in node_list:
            fid = nd["full_id"]
            if not _is_visible(fid):
                continue
            # Depth = number of dot-separated segments beyond the top level.
            depth = fid.count(".")
            entries.append(ChakraListEntry(fid, _format_chakra_label(fid), depth))

        self._list_widget.set_items(entries)

    def _push_undo(self) -> None:
        """Push current ChakraComponent state onto the undo stack (if distinct)."""
        if self._actor is None:
            return
        comp = chakra_items_system._coerce_actor_chakra_component(self._actor)
        if comp is None:
            return
        snap = comp.to_dict()
        if self._undo_stack and self._undo_stack[-1] == snap:
            return
        self._undo_stack.append(snap)
        # Keep the stack bounded.
        if len(self._undo_stack) > 40:
            self._undo_stack.pop(0)

    def _undo_last(self) -> None:
        """Undo the most recent activation/alignment change."""
        if self._actor is None or not self._undo_stack:
            return
        snap = self._undo_stack.pop()
        chakra_items_system.restore_actor_chakra_component_snapshot(
            self._actor, snap, game=self.game
        )
        self._silhouette.refresh_points()
        self._preview.mark_dirty()
        primary = self._silhouette.get_selected_chakra()
        self._update_info_for_chakra(primary)
        self._refresh_list_items()

    def _apply_selection_action(self, action: str) -> None:
        """Apply an action (activate/deactivate/toggle/clear) to selection."""
        if self._mode == "realign":
            return

        if action == "clear":
            self._set_selection(set(), None)
            return

        state = _runtime_chakra_view(self.game, self._actor)
        targets = set(self._selected_nodes)
        if not targets:
            primary = self._silhouette.get_selected_chakra()
            if primary:
                targets = {primary}

        if not targets:
            return

        # Determine if anything will change before pushing undo.
        changed = False
        for node_id in targets:
            if node_id not in state.unlocked:
                continue
            if action == "activate" and node_id not in state.active:
                changed = True
                break
            if action == "deactivate" and node_id in state.active:
                changed = True
                break
            if action == "toggle":
                changed = True
                break

        if not changed:
            return

        self._push_undo()

        for node_id in targets:
            if node_id not in state.unlocked:
                continue
            was_active = node_id in state.active
            if action == "activate":
                now_active = chakra_items_system.toggle_actor_chakra(
                    self._actor,
                    node_id,
                    active=True,
                    game=self.game,
                )
            elif action == "deactivate":
                now_active = chakra_items_system.toggle_actor_chakra(
                    self._actor,
                    node_id,
                    active=False,
                    game=self.game,
                )
            else:
                now_active = chakra_items_system.toggle_actor_chakra(
                    self._actor,
                    node_id,
                    active=None,
                    game=self.game,
                )

            # Visual burst at the chakra location
            point = self._silhouette.get_chakra_point(node_id)
            if point:
                self._silhouette.spawn_activation_burst(
                    point.pos_px[0],
                    point.pos_px[1],
                    activating=(now_active and not was_active),
                )
        self._silhouette.refresh_points()
        self._preview.mark_dirty()
        primary = self._silhouette.get_selected_chakra()
        self._update_info_for_chakra(primary)
        self._refresh_list_items()
        # Refresh player actions so chakra-granted abilities appear/disappear.
        try:
            game = self.game
            if hasattr(game, "refresh_actor_actions") and hasattr(game, "player_id"):
                game.refresh_actor_actions(game.player_id)
        except Exception:
            pass

    def _set_pattern_root(self) -> None:
        """Set the chakra pattern root to the primary selected active chakra."""
        if self._mode == "realign":
            return
        if self._actor is None:
            return
        state = _runtime_chakra_view(self.game, self._actor)
        primary = self._silhouette.get_selected_chakra()
        if not primary:
            return
        if primary not in state.active:
            if self.game:
                self.game.log.add("Root must be an active chakra.")
            return
        if getattr(state, "pattern_root", None) == primary:
            return

        self._push_undo()
        comp = chakra_items_system._coerce_actor_chakra_component(self._actor)
        if comp is not None:
            comp.tags["compat_pattern_root"] = primary
        self._preview.mark_dirty()
        self._refresh_list_items()
        self._update_info_for_chakra(primary)

    def _toggle_list_view(self) -> None:
        """Toggle the list view panel."""
        self._list_view_enabled = not self._list_view_enabled
        if self._list_view_enabled:
            self._refresh_list_items()

    def _maybe_auto_focus(self) -> None:
        """No-op: focus view has been removed."""
        return

    def _build_chakra_tooltip(
        self,
        node_id: str,
    ) -> Optional[Tuple[str, List[Tuple[str, Tuple[int, int, int]]]]]:
        """Build tooltip title + lines for the hovered chakra."""
        if self._actor is None:
            return None

        chakra_state = self._get_ui_state()

        title = _format_chakra_label(node_id)
        lines: List[Tuple[str, Tuple[int, int, int]]] = []

        # State label
        if node_id in chakra_state.active:
            state_label = "Active"
            state_color = COLOR_ACTIVE
        elif node_id in chakra_state.unlocked:
            state_label = "Unlocked"
            state_color = COLOR_UNLOCKED
        else:
            state_label = "Locked"
            state_color = COLOR_LOCKED
        lines.append((f"State: {state_label}", state_color))
        if getattr(chakra_state, "pattern_root", None) == node_id:
            lines.append(("Pattern Root: yes", TOOLTIP_ACCENT))

        # Resolve gating chain and node metadata from entity graph.
        node_list = _body_nodes_for_actor(self.game, self._actor)
        if node_list:
            node_by_id = {nd["full_id"]: nd for nd in node_list}
            this_node = node_by_id.get(node_id)
            proto_id = this_node["node_proto_id"] if this_node else node_id

            # Ancestors that are branch roots form the gating chain.
            gating_chain: List[str] = []
            parts = node_id.split(".")
            for i in range(1, len(parts)):
                ancestor = ".".join(parts[:i])
                anc_data = node_by_id.get(ancestor)
                if anc_data and is_branch_root(anc_data["node_proto_id"]):
                    gating_chain.append(ancestor)

            # Child count from graph.
            graph = getattr(self.game, "entity_graph", None)
            node_eid = f"{getattr(self._actor, 'entity_id', None) or getattr(self._actor, 'id', '')}:body:{node_id}"
            child_count = len(graph.get_children(node_eid, socket_id="body")) if graph else 0
        else:
            gating_chain = []
            proto_id = node_id
            child_count = 0

        if gating_chain:
            missing = [g for g in gating_chain if g not in chakra_state.unlocked]
            if missing:
                req = ", ".join(_format_chakra_label(m) for m in missing)
                lines.append((f"Requires: {req}", TOOLTIP_WARN))
            else:
                prereq = ", ".join(_format_chakra_label(g) for g in gating_chain)
                lines.append((f"Prereqs: {prereq}", TOOLTIP_TEXT))

        # Branch root hint
        if is_branch_root(proto_id):
            lines.append(("Branch Root: yes", TOOLTIP_ACCENT))

        # Child count hint
        if child_count:
            lines.append((f"Children: {child_count}", TOOLTIP_TEXT))

        # Charge readout (only meaningful for unlocked/active nodes)
        if node_id in chakra_state.unlocked or node_id in chakra_state.active:
            bonuses = check_resonance_bonuses_from_active_nodes(chakra_state.active)
            mods = get_resonance_modifiers(bonuses)
            cap = max(0.01, CHARGE_MAX_BASE + mods.charge_cap_bonus)
            charge = float(chakra_state.charges.get(node_id, 0.0))
            pct = int(100 * (charge / cap))
            lines.append((f"Charge: {pct}% ({charge:.2f}/{cap:.2f})", TOOLTIP_ACCENT))

        # Alignment offset
        if node_id in chakra_state.alignments:
            dx, dy = chakra_state.alignments.get(node_id, (0.0, 0.0))
            lines.append((f"Alignment: {dx:+.2f}, {dy:+.2f}", TOOLTIP_TEXT))

        # Resonance summary
        if node_id in chakra_state.active:
            resonances = check_resonance_bonuses_from_active_nodes(chakra_state.active)
            if resonances:
                res_label = ", ".join(r.replace("_", " ").title() for r in resonances[:2])
                if len(resonances) > 2:
                    res_label += f" (+{len(resonances) - 2})"
                lines.append((f"Resonance: {res_label}", (180, 255, 180)))

        return (title, lines)

    def _draw_chakra_tooltip(self, panel: pygame.Surface, renderer) -> None:
        """Draw a floating tooltip near the hovered chakra."""
        hover_id = self._silhouette.get_hovered_chakra()
        if not hover_id:
            # Fall back to selected chakra for keyboard navigation.
            hover_id = self._silhouette.get_selected_chakra()
        if not hover_id:
            return

        point = self._silhouette.get_chakra_point(hover_id)
        if point is None:
            return

        tip = self._build_chakra_tooltip(hover_id)
        if tip is None:
            return

        title, lines = tip
        title_font = getattr(renderer, "font", None) or getattr(renderer, "small_font", None)
        body_font = getattr(renderer, "small_font", None) or title_font
        if title_font is None or body_font is None:
            return

        title_surf = title_font.render(title, True, TOOLTIP_TITLE)
        line_surfs = [body_font.render(text, True, color) for text, color in lines]

        width = max(title_surf.get_width(), *(ls.get_width() for ls in line_surfs)) + TOOLTIP_PADDING * 2
        width = min(width, TOOLTIP_MAX_WIDTH)
        height = (
            title_surf.get_height()
            + (len(line_surfs) * body_font.get_height())
            + TOOLTIP_PADDING * 2
        )

        # Position tooltip near the chakra, clamp within the silhouette panel.
        bounds = self._silhouette.rect
        x = point.pos_px[0] + 18
        y = point.pos_px[1] - height // 2

        if x + width + TOOLTIP_MARGIN > bounds.right:
            x = point.pos_px[0] - width - 18
        x = max(bounds.left + TOOLTIP_MARGIN, min(bounds.right - width - TOOLTIP_MARGIN, x))
        y = max(bounds.top + TOOLTIP_MARGIN, min(bounds.bottom - height - TOOLTIP_MARGIN, y))

        tooltip_rect = pygame.Rect(x, y, width, height)

        # Soft shadow
        shadow = pygame.Surface((width + 6, height + 6), pygame.SRCALPHA)
        shadow.fill((0, 0, 0, 120))
        panel.blit(shadow, (x - 3, y - 3))

        # Tooltip body
        box = pygame.Surface((width, height), pygame.SRCALPHA)
        box.fill((*TOOLTIP_BG, 230))
        pygame.draw.rect(box, TOOLTIP_BORDER, box.get_rect(), 1)
        panel.blit(box, (x, y))

        # Pointer line
        pointer_x = x if x > point.pos_px[0] else x + width
        pointer_y = y + height // 2
        pygame.draw.line(panel, TOOLTIP_BORDER, point.pos_px, (pointer_x, pointer_y), 1)

        # Text layout
        tx = x + TOOLTIP_PADDING
        ty = y + TOOLTIP_PADDING
        panel.blit(title_surf, (tx, ty))
        ty += title_surf.get_height() + 2

        for surf in line_surfs:
            panel.blit(surf, (tx, ty))
            ty += body_font.get_height()

    def _get_ui_state(self) -> Any:
        """Return the scene-driving chakra view (edit session or runtime view)."""
        if self._working_session is not None:
            return self._working_session
        return _runtime_chakra_view(self.game, self._actor)

    def _start_edit_session(
        self,
        state: Any,
        *,
        alignments: Dict[str, Tuple[float, float]],
    ) -> ChakraEditSession:
        """Create a scene-local edit session from a runtime chakra snapshot."""
        return ChakraEditSession.from_state(state, alignments=alignments)

    def _get_dexterity(self) -> int:
        """Get the player's dexterity (AGI) stat for alignment limits."""
        # Note: stats live on the Character, not Actor.stats. We treat AGI as DEX.
        try:
            if self.game and getattr(self.game, "character", None):
                stats = getattr(self.game.character, "stats", {}) or {}
                return int(stats.get("agi", stats.get("dex", 0)))
        except Exception:
            pass
        return 0

    def _compute_realign_limit(self) -> float:
        """Compute max alignment offset allowed (in alignment units)."""
        dex = self._get_dexterity()
        limit = REALIGN_BASE_LIMIT + (REALIGN_PER_DEX * dex)
        return max(REALIGN_MIN_LIMIT, min(REALIGN_MAX_LIMIT, limit))

    def _enter_realign_mode(self) -> None:
        """Enter realign mode with a working alignment copy (no commit yet)."""
        if self._mode == "realign":
            return
        if self._actor is None:
            return

        self._mode = "realign"
        state = _runtime_chakra_view(self.game, self._actor)

        self._original_alignments = dict(state.alignments)
        self._pending_alignments = dict(state.alignments)
        self._working_session = self._start_edit_session(
            state,
            alignments=self._pending_alignments,
        )

        # Route preview to the working edit-session snapshot.
        self._silhouette.set_state_override(self._working_session)
        self._preview.set_state_override(self._working_session)
        self._silhouette.set_realign_mode(True, self._compute_realign_limit())

        self._silhouette.refresh_points()
        self._preview.mark_dirty()

    def _exit_realign_mode(self, *, commit: bool) -> None:
        """Exit realign mode, optionally committing pending alignments."""
        if self._mode != "realign":
            return

        if commit and self._actor is not None and self._pending_alignments is not None:
            changed = (self._pending_alignments != (self._original_alignments or {}))
            if changed:
                self._push_undo()
            comp = chakra_items_system._coerce_actor_chakra_component(self._actor)
            if comp is not None:
                comp.tags["compat_alignments"] = dict(self._pending_alignments)
            if changed:
                self._apply_realign_time_cost()

        # Clear preview overrides
        self._pending_alignments = None
        self._original_alignments = None
        self._working_session = None

        self._silhouette.set_state_override(None)
        self._preview.set_state_override(None)
        self._silhouette.set_realign_mode(False, 0.0)
        self._silhouette.refresh_points()
        self._preview.mark_dirty()

        self._mode = "activate"

    def _apply_realign_time_cost(self) -> None:
        """Advance game time to pay the realign cost."""
        if self.game is None:
            return
        try:
            level = self.game._level()
            self.game._advance_time(level, REALIGN_TIME_TICKS)
        except Exception:
            pass

    def _toggle_realign_mode(self) -> None:
        """Toggle between activation and realignment modes."""
        if self._mode == "realign":
            self._exit_realign_mode(commit=False)
        else:
            self._enter_realign_mode()

    def _on_commit_click(self, _btn: ButtonWidget) -> None:
        """Commit pending realignment changes."""
        self._exit_realign_mode(commit=True)

    def _on_cancel_click(self, _btn: ButtonWidget) -> None:
        """Cancel pending realignment changes."""
        self._exit_realign_mode(commit=False)

    def _on_chakra_click(self, node_id: str) -> None:
        """Handle chakra click (selection only)."""
        # In realign mode, clicks are handled by drag logic instead of toggling.
        if self._mode == "realign":
            self._silhouette.select_chakra(node_id)
            self._update_info_for_chakra(node_id)
            return

        try:
            mods = pygame.key.get_mods()
        except Exception:
            mods = 0

        add = bool(mods & pygame.KMOD_SHIFT)
        remove = bool(mods & pygame.KMOD_ALT)

        if add:
            nodes = set(self._selected_nodes)
            nodes.add(node_id)
            self._set_selection(nodes, node_id)
        elif remove:
            nodes = set(self._selected_nodes)
            nodes.discard(node_id)
            primary = node_id if node_id in nodes else (next(iter(nodes), None))
            self._set_selection(nodes, primary)
        else:
            self._set_selection({node_id}, node_id)

    def _on_chakra_hover(self, node_id: Optional[str]) -> None:
        """Handle chakra hover."""
        if node_id:
            self._update_info_for_chakra(node_id)

    def _on_drag_select(self, nodes: Set[str], mods: int) -> None:
        """Handle drag-selection across multiple chakras."""
        add = bool(mods & pygame.KMOD_SHIFT)
        remove = bool(mods & pygame.KMOD_ALT)

        if add:
            merged = set(self._selected_nodes)
            merged.update(nodes)
            primary = next(iter(nodes), None) or (next(iter(merged), None))
            self._set_selection(merged, primary)
        elif remove:
            merged = set(self._selected_nodes)
            merged.difference_update(nodes)
            primary = next(iter(merged), None)
            self._set_selection(merged, primary)
        else:
            primary = next(iter(nodes), None)
            self._set_selection(set(nodes), primary)

    def _on_list_select(self, node_id: str, mods: int) -> None:
        """Handle list selection clicks (same logic as silhouette)."""
        add = bool(mods & pygame.KMOD_SHIFT)
        remove = bool(mods & pygame.KMOD_ALT)

        if add:
            nodes = set(self._selected_nodes)
            nodes.add(node_id)
            self._set_selection(nodes, node_id)
        elif remove:
            nodes = set(self._selected_nodes)
            nodes.discard(node_id)
            primary = next(iter(nodes), None)
            self._set_selection(nodes, primary)
        else:
            self._set_selection({node_id}, node_id)

    def _on_chakra_drag_start(self, node_id: str, pos_px: Tuple[int, int]) -> bool:
        """Begin dragging a chakra for alignment."""
        if self._mode != "realign":
            return False

        self._set_selection({node_id}, node_id)

        # Only unlocked chakras can be realigned.
        state = _runtime_chakra_view(self.game, self._actor)
        if node_id not in state.unlocked:
            return True  # Consume drag attempt but do nothing

        self._update_alignment_from_mouse(node_id, pos_px)
        return True

    def _on_chakra_drag(self, node_id: str, pos_px: Tuple[int, int]) -> None:
        """Update alignment while dragging."""
        if self._mode != "realign":
            return
        self._update_alignment_from_mouse(node_id, pos_px)

    def _on_chakra_drag_end(self, node_id: str, pos_px: Tuple[int, int]) -> None:
        """End a drag gesture (no-op for now)."""
        if self._mode != "realign":
            return
        # Final update in case mouseup happens on a new position
        self._update_alignment_from_mouse(node_id, pos_px)

    def _update_alignment_from_mouse(self, node_id: str, pos_px: Tuple[int, int]) -> None:
        """Compute and clamp alignment based on mouse position."""
        if self._actor is None:
            return

        point = self._silhouette._chakra_points.get(node_id)
        if point is None:
            return

        # Ensure working alignments exist
        if self._pending_alignments is None:
            eff = _runtime_chakra_view(self.game, self._actor)
            self._pending_alignments = dict(eff.alignments)
        if self._working_session is None:
            eff = _runtime_chakra_view(self.game, self._actor)
            self._working_session = self._start_edit_session(
                eff,
                alignments=dict(self._pending_alignments),
            )
            self._silhouette.set_state_override(self._working_session)
            self._preview.set_state_override(self._working_session)

        # Convert mouse to unit space
        target_u = self._silhouette.screen_to_unit(pos_px)

        # Compute alignment in local units (inverse of alignment math)
        local_scale = point.local_scale if point.local_scale else 1.0
        denom = local_scale * 0.5
        if denom <= 1e-6:
            return

        dx = (target_u[0] - point.base_pos_u[0]) / denom
        dy = (target_u[1] - point.base_pos_u[1]) / denom

        # Clamp to dexterity-based alignment radius
        limit = self._compute_realign_limit()
        dist = math.hypot(dx, dy)
        if dist > limit and dist > 0:
            scale = limit / dist
            dx *= scale
            dy *= scale

        self._pending_alignments[node_id] = (dx, dy)

        # Push to preview state
        self._working_session.alignments = dict(self._pending_alignments)
        self._silhouette.set_realign_mode(True, limit)
        self._silhouette.refresh_points()
        self._preview.mark_dirty()

    def _toggle_chakra(self, node_id: str) -> None:
        """Toggle a chakra's active state."""
        if not self._actor:
            return

        chakra_state = _runtime_chakra_view(self.game, self._actor)

        # Can only toggle if unlocked
        if node_id not in chakra_state.unlocked:
            return

        # Push undo snapshot before changing state
        self._push_undo()

        # Check if activating or deactivating
        was_active = node_id in chakra_state.active

        # Toggle
        chakra_items_system.toggle_actor_chakra(self._actor, node_id, active=None, game=self.game)

        # Spawn particle burst
        point = self._silhouette._chakra_points.get(node_id)
        if point:
            self._silhouette.spawn_activation_burst(
                point.pos_px[0],
                point.pos_px[1],
                activating=not was_active  # If was active, now deactivating
            )

        # Update visuals
        self._silhouette.refresh_points()
        self._preview.mark_dirty()
        self._update_info_for_chakra(node_id)
        # Refresh player actions so chakra-granted abilities appear/disappear.
        try:
            game = self.game
            if hasattr(game, "refresh_actor_actions") and hasattr(game, "player_id"):
                game.refresh_actor_actions(game.player_id)
        except Exception:
            pass

    def _ensure_background(self, panel: pygame.Surface, body_w: int, info_h: int) -> None:
        """Build a cached background surface (avoids per-frame gradient work)."""
        key = (panel.get_width(), panel.get_height(), body_w, info_h)
        if self._bg_cache is not None and self._bg_cache_key == key:
            return

        w = panel.get_width()
        h = panel.get_height()
        bg = pygame.Surface((w, h), pygame.SRCALPHA)

        # Base wash across the whole panel
        _draw_vertical_gradient(bg, pygame.Rect(0, 0, w, h), (10, 12, 18), (8, 10, 16))

        # Left/body panel: deeper blues + starfield
        left_rect = pygame.Rect(0, 0, body_w, h - info_h)
        _draw_vertical_gradient(bg, left_rect, (12, 14, 24), (9, 11, 18))
        _draw_starfield(bg, left_rect, seed=137, count=160)

        # Add a soft radial glow behind the body
        glow = pygame.Surface(left_rect.size, pygame.SRCALPHA)
        cx = left_rect.width // 2
        cy = left_rect.height // 2
        max_r = int(min(left_rect.width, left_rect.height) * 0.30)
        for r in range(max_r, 0, -1):
            a = int(40 * (r / max_r) ** 2)
            pygame.draw.circle(glow, (35, 60, 95, a), (cx, cy), r)
        bg.blit(glow, left_rect.topleft, special_flags=pygame.BLEND_ADD)

        # Right/preview panel: cleaner, slightly brighter
        right_rect = pygame.Rect(body_w, 0, w - body_w, h - info_h)
        _draw_vertical_gradient(bg, right_rect, (12, 12, 20), (10, 12, 18))

        # Info bar background
        info_rect = pygame.Rect(0, h - info_h, w, info_h)
        _draw_vertical_gradient(bg, info_rect, (14, 16, 24), (12, 14, 22))

        # Divider and frame lines
        pygame.draw.line(bg, (50, 55, 80), (body_w, 0), (body_w, h - info_h))
        pygame.draw.line(bg, (40, 45, 70), (0, h - info_h), (w, h - info_h))
        pygame.draw.rect(bg, (60, 65, 85), bg.get_rect(), 1)

        self._bg_cache = bg
        self._bg_cache_key = key

    def _panel_event(self, event, manager: "SceneManager") -> None:
        """Handle keyboard input."""
        if event.type == pygame.KEYDOWN:
            # Toggle realign mode
            if event.key == pygame.K_r:
                self._toggle_realign_mode()
                return

            # Toggle list view
            if event.key == pygame.K_l:
                self._toggle_list_view()
                return

            # Undo (Ctrl+Z)
            if event.key == pygame.K_z:
                try:
                    if pygame.key.get_mods() & pygame.KMOD_CTRL:
                        self._undo_last()
                        return
                except Exception:
                    pass

            # Zoom controls
            if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self._silhouette.adjust_zoom(1)
                self._maybe_auto_focus()
                return
            if event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self._silhouette.adjust_zoom(-1)
                self._maybe_auto_focus()
                return
            if event.key in (pygame.K_0, pygame.K_KP_0):
                self._silhouette.reset_zoom()
                self._maybe_auto_focus()
                return

            # In realign mode, ignore Space to avoid accidental commit.
            if self._mode == "realign" and event.key == pygame.K_SPACE:
                return

            action = self._menu_input.handle_keydown(event.key)
            if action:
                self._handle_action(action, manager)

        elif event.type == pygame.KEYUP:
            self._menu_input.handle_keyup(event.key)

        elif event.type == pygame.MOUSEWHEEL:
            # Mouse wheel zoom (centered for simplicity)
            try:
                mx, my = pygame.mouse.get_pos()

                class _E:
                    pass

                e2 = _E()
                e2.pos = (mx, my)
                e2 = self._to_panel_event(e2, manager)
                anchor = getattr(e2, "pos", None)
            except Exception:
                anchor = None

            if event.y > 0:
                self._silhouette.adjust_zoom(1, anchor_px=anchor)
                self._maybe_auto_focus()
            elif event.y < 0:
                self._silhouette.adjust_zoom(-1, anchor_px=anchor)
                self._maybe_auto_focus()

    def _handle_action(self, action: str, manager: "SceneManager") -> None:
        """Handle a menu action."""
        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        if action == MENU_ACTION_BACK:
            # In realign mode, ESC cancels changes instead of closing.
            if self._mode == "realign":
                self._exit_realign_mode(commit=False)
                return
            self._close(manager)
            return

        if action == MENU_ACTION_ACTIVATE:
            # In realign mode, Enter commits the pending alignment.
            if self._mode == "realign":
                self._exit_realign_mode(commit=True)
                return

            self._apply_selection_action("toggle")
            return

        # Navigation
        direction_map = {
            MENU_ACTION_UP: "up",
            MENU_ACTION_DOWN: "down",
            MENU_ACTION_LEFT: "left",
            MENU_ACTION_RIGHT: "right",
        }

        if action in direction_map:
            direction = direction_map[action]
            next_id = self._silhouette.get_adjacent_chakra(direction)
            if next_id:
                # Move primary selection; keep multi-selection intact.
                nodes = set(self._selected_nodes) if self._selected_nodes else {next_id}
                nodes.add(next_id)
                self._set_selection(nodes, next_id)

    def _close(self, manager: "SceneManager") -> None:
        """Close the scene."""
        if hasattr(manager, "pop_scene"):
            manager.pop_scene()
        else:
            manager.set_scene(None)

    def update(self, dt_ms: int, manager: "SceneManager") -> None:
        """Update scene."""
        # Handle key repeat
        repeat_action = self._menu_input.update()
        if repeat_action:
            self._handle_action(repeat_action, manager)

        super().update(dt_ms, manager)

    def draw_panel(self, panel: pygame.Surface, renderer, manager: "SceneManager") -> None:
        """Draw the scene."""
        # Layout widgets
        w = panel.get_width()
        h = panel.get_height()

        body_w = int(w * BODY_PANEL_WIDTH_FRAC)
        preview_w = w - body_w
        info_h = 80

        # Background (cached gradient + starfield)
        self._ensure_background(panel, body_w, info_h)
        if self._bg_cache:
            panel.blit(self._bg_cache, (0, 0))

        # Silhouette takes left portion
        self._silhouette.rect = pygame.Rect(0, 0, body_w, h - info_h)

        # Right side: list + preview (optional list view)
        if self._list_view_enabled:
            list_h = int((h - info_h) * 0.52)
            list_h = max(120, min(list_h, h - info_h - 120))
            self._list_widget.visible = True
            self._list_widget.rect = pygame.Rect(body_w, 0, preview_w, list_h)
            self._preview.rect = pygame.Rect(body_w, list_h, preview_w, h - info_h - list_h)
        else:
            self._list_widget.visible = False
            self._preview.rect = pygame.Rect(body_w, 0, preview_w, h - info_h)

        # Info panel at bottom
        self._info.rect = pygame.Rect(0, h - info_h, w, info_h)

        # Commit/cancel buttons (only visible in realign mode)
        self._btn_commit.visible = (self._mode == "realign")
        self._btn_cancel.visible = (self._mode == "realign")

        # Selection action buttons (only visible in activate mode)
        activate_mode = (self._mode == "activate")
        self._btn_activate.visible = activate_mode
        self._btn_deactivate.visible = activate_mode
        self._btn_toggle.visible = activate_mode
        self._btn_clear.visible = activate_mode
        self._btn_root.visible = activate_mode
        self._btn_undo.visible = True  # Keep undo available in both modes
        self._btn_list.visible = True

        # Layout buttons along the bottom-right
        btn_y = h - info_h + 8
        btn_pad = 8
        self._btn_cancel.rect.topleft = (w - 10, btn_y)  # temp; sized in layout
        self._btn_commit.rect.topleft = (w - 10, btn_y)

        # Draw title
        font = getattr(renderer, "menu_font", None) or getattr(renderer, "font", None)
        if font:
            title = font.render("CHAKRA CONSTELLATION", True, (200, 210, 240))
            tx = (w - title.get_width()) // 2
            panel.blit(title, (tx, 6))

        # Panel labels
        small_font = getattr(renderer, "small_font", None) or font
        if small_font:
            left_label = small_font.render("Body Lattice", True, (140, 160, 190))
            panel.blit(left_label, (16, 28))

            right_label = small_font.render("Pattern Preview", True, (140, 160, 190))
            panel.blit(right_label, (body_w + 16, 28))

        # Draw widgets
        ctx = WidgetContext(
            surface=panel,
            game=self.game,
            scene=self,
            renderer=renderer,
        )

        self._silhouette.layout(ctx)
        self._list_widget.layout(ctx)
        self._preview.layout(ctx)
        self._info.layout(ctx)
        self._btn_commit.layout(ctx)
        self._btn_cancel.layout(ctx)
        self._btn_activate.layout(ctx)
        self._btn_deactivate.layout(ctx)
        self._btn_toggle.layout(ctx)
        self._btn_clear.layout(ctx)
        self._btn_root.layout(ctx)
        self._btn_undo.layout(ctx)
        self._btn_list.layout(ctx)

        # Now place buttons with correct sizes
        if self._btn_cancel.visible:
            self._btn_cancel.rect.x = w - self._btn_cancel.rect.width - 12
            self._btn_cancel.rect.y = btn_y
        if self._btn_commit.visible:
            self._btn_commit.rect.x = self._btn_cancel.rect.x - self._btn_commit.rect.width - btn_pad
            self._btn_commit.rect.y = btn_y

        # Activate-mode buttons aligned to the bottom-right, just left of commit/cancel.
        if activate_mode:
            right_x = w - 12
            for btn in (self._btn_list, self._btn_undo, self._btn_clear, self._btn_toggle, self._btn_root, self._btn_deactivate, self._btn_activate):
                if not btn.visible:
                    continue
                right_x -= btn.rect.width
                btn.rect.x = right_x
                btn.rect.y = btn_y
                right_x -= btn_pad
        else:
            # Still place undo/list even in realign mode.
            right_x = min(self._btn_commit.rect.x, self._btn_cancel.rect.x) - btn_pad
            for btn in (self._btn_list, self._btn_undo):
                if not btn.visible:
                    continue
                right_x -= btn.rect.width
                btn.rect.x = right_x
                btn.rect.y = btn_y
                right_x -= btn_pad

        self._silhouette.draw(ctx)
        self._list_widget.draw(ctx)
        self._preview.draw(ctx)
        self._info.draw(ctx)
        self._btn_commit.draw(ctx)
        self._btn_cancel.draw(ctx)
        self._btn_activate.draw(ctx)
        self._btn_deactivate.draw(ctx)
        self._btn_toggle.draw(ctx)
        self._btn_clear.draw(ctx)
        self._btn_root.draw(ctx)
        self._btn_undo.draw(ctx)
        self._btn_list.draw(ctx)

        # Hover tooltip (drawn on top of the layout for quick context).
        self._draw_chakra_tooltip(panel, renderer)

        # Draw footer hint
        small_font = getattr(renderer, "small_font", None) or font
        if small_font:
            if self._mode == "realign":
                hint = "Drag: Realign  |  Enter: Commit  |  Esc: Cancel  |  R: Toggle  |  L: List  |  +/- or Wheel: Zoom"
            else:
                hint = "Drag: Box Select  |  Shift/Alt: Add/Remove  |  Space: Toggle  |  Ctrl+Z: Undo  |  Set Root button  |  L: List  |  +/- or Wheel: Zoom  |  Esc: Close"
            hint_surf = small_font.render(hint, True, (100, 110, 130))
            hx = (w - hint_surf.get_width()) // 2
            panel.blit(hint_surf, (hx, h - 20))

        # Mode indicator in the info panel
        if small_font:
            mode_label = "Mode: Realign" if self._mode == "realign" else "Mode: Activate"
            mode_color = (180, 210, 255) if self._mode == "realign" else (140, 150, 170)
            mode_surf = small_font.render(mode_label, True, mode_color)
            panel.blit(mode_surf, (10, h - info_h + 10))
