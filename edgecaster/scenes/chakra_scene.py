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
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

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

from edgecaster.ui.widgets import Widget, WidgetContext, LabelWidget, ButtonWidget
from edgecaster.systems.chakras import (
    ChakraState,
    can_unlock_chakra,
    toggle_chakra_active,
    check_resonance_bonuses,
    get_chakra_world_positions,
    get_all_chakra_positions_recursive,
    is_branch_root,
)
from edgecaster.prototypes import resolve_body_schema

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
COLOR_GATED = (180, 60, 60)

# Glow layer multipliers: (radius_mul, alpha_mul)
GLOW_LAYERS = [
    (3.0, 0.15),  # Outer bloom
    (2.0, 0.3),   # Mid glow
    (1.0, 1.0),   # Core
]

# Animation timing
PULSE_PERIOD_MS = 1600
ENERGY_FLOW_PERIOD_MS = 2000
ZOOM_DURATION_MS = 220

# Layout
BODY_PANEL_WIDTH_FRAC = 0.60  # Left 60% for body
PREVIEW_PANEL_WIDTH_FRAC = 0.40  # Right 40% for pattern preview
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
CAMERA_MAX_SCALE = 6.00
CAMERA_ZOOM_STEP = 1.18


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _smoothstep(t: float) -> float:
    """Smooth easing function for animations."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation."""
    return a + (b - a) * t


def _lerp_color(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    """Interpolate between two RGB colors."""
    return (
        int(_lerp(c1[0], c2[0], t)),
        int(_lerp(c1[1], c2[1], t)),
        int(_lerp(c1[2], c2[2], t)),
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
        col = _lerp_color(top, bottom, t)
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


def _get_body_schema(actor: Any) -> Dict[str, Any]:
    """Extract body schema from an actor, handling various storage formats."""
    if actor is None:
        return {"root": None, "nodes": {}}

    # Use resolve_body_schema which handles all the various storage formats
    # (actor object, prototype id, direct schema dict, etc.)
    try:
        schema = resolve_body_schema(actor)
        if schema and isinstance(schema, dict):
            return schema
    except Exception:
        pass

    return {"root": None, "nodes": {}}


def _get_chakra_state(actor: Any) -> ChakraState:
    """Extract chakra state from an actor, creating default if missing."""
    state = getattr(actor, "chakra_state", None)
    if state and isinstance(state, ChakraState):
        return state
    return ChakraState()


def _get_nodes_from_schema(body_schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Extract nodes dict from body schema (handles nesting)."""
    if not body_schema:
        return {}

    # Check nested 'body' key
    body = body_schema.get("body", body_schema)
    if isinstance(body, dict):
        nodes = body.get("nodes", {})
        if isinstance(nodes, dict):
            return nodes

    return body_schema.get("nodes", {})


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
        on_chakra_click: Optional[callable] = None,
        on_chakra_hover: Optional[callable] = None,
        on_chakra_drag_start: Optional[callable] = None,
        on_chakra_drag: Optional[callable] = None,
        on_chakra_drag_end: Optional[callable] = None,
    ) -> None:
        super().__init__()
        self.actor = actor
        self.on_chakra_click = on_chakra_click
        self.on_chakra_hover = on_chakra_hover
        self.on_chakra_drag_start = on_chakra_drag_start
        self.on_chakra_drag = on_chakra_drag
        self.on_chakra_drag_end = on_chakra_drag_end

        # Cached chakra points (rebuilt on layout)
        self._chakra_points: Dict[str, ChakraPoint] = {}
        self._hovered_id: Optional[str] = None
        self._selected_id: Optional[str] = None
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
        self.state_override: Optional[ChakraState] = None

        # Realign mode visualization (optional)
        self.realign_mode: bool = False
        self.realign_radius: float = 0.0  # alignment units (not pixels)

    def set_actor(self, actor: Any) -> None:
        """Update the actor being displayed."""
        self.actor = actor
        self._chakra_points.clear()
        self._connections.clear()

    def set_state_override(self, state: Optional[ChakraState]) -> None:
        """Override chakra state for preview (e.g., while realigning)."""
        self.state_override = state
        self._chakra_points.clear()
        self._connections.clear()

    def set_realign_mode(self, active: bool, max_align: float = 0.0) -> None:
        """Enable/disable realign mode visuals (alignment radius ring)."""
        self.realign_mode = bool(active)
        self.realign_radius = float(max_align)

    def _rebuild_chakra_points(self) -> None:
        """Rebuild chakra point data from actor's body schema.

        Uses recursive traversal to include sub-schema nodes when their
        branch root is unlocked. For example, when 'arm' is unlocked,
        shows shoulder, upper_arm, elbow, forearm, hand nodes.
        """
        self._chakra_points.clear()
        self._connections.clear()

        if self.actor is None:
            return

        body_schema = _get_body_schema(self.actor)
        chakra_state = self._get_state()
        nodes = _get_nodes_from_schema(body_schema)

        if not nodes:
            return

        # Get ALL positions recursively (including sub-schemas)
        # This returns {full_node_id: ((x, y), state, local_scale, base_pos)}
        all_positions = get_all_chakra_positions_recursive(
            body_schema, chakra_state,
            base_scale=CHAKRA_LAYOUT_SCALE,
        )

        # Also include locked top-level nodes that aren't in positions yet
        for node_id, node in nodes.items():
            if node_id not in all_positions:
                layout = node.get("layout", {}) if isinstance(node, dict) else {}
                x = float(layout.get("x", 0.0)) if isinstance(layout, dict) else 0.0
                y = float(layout.get("y", 0.0)) if isinstance(layout, dict) else 0.0
                all_positions[node_id] = ((x, y), "locked", 1.0, (x, y))

        # Build chakra points from all positions
        for full_id, (pos_u, state, local_scale, base_pos) in all_positions.items():
            # Per-chakra pulse phase offset for visual variety
            phase_offset = hash(full_id) % 1000 / 1000.0

            self._chakra_points[full_id] = ChakraPoint(
                node_id=full_id,
                pos_u=pos_u,
                base_pos_u=base_pos,
                pos_px=(0, 0),  # Computed during layout
                state=state,
                local_scale=local_scale,
                pulse_phase=phase_offset,
            )

        # Build parent-child connections for energy flow
        # This now needs to handle prefixed IDs (e.g., arm -> arm.shoulder)
        self._build_connections(body_schema, chakra_state, "")

    def _build_connections(
        self,
        body_schema: Dict[str, Any],
        chakra_state: ChakraState,
        prefix: str,
        depth: int = 0,
    ) -> None:
        """Recursively build parent-child connections for energy flow lines."""
        if depth > 5:
            return

        nodes = _get_nodes_from_schema(body_schema)
        if not nodes:
            return

        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue

            full_id = f"{prefix}{node_id}" if prefix else node_id

            # Add connections to children within this schema
            children = node.get("children", [])
            if isinstance(children, list):
                for child_id in children:
                    if child_id:
                        child_full_id = f"{prefix}{child_id}" if prefix else str(child_id)
                        if child_full_id in self._chakra_points:
                            self._connections.append((full_id, child_full_id))

            # If this is an unlocked branch root, connect to sub-schema root
            # and recurse into sub-schema
            proto_id = node.get("proto", node_id)
            if is_branch_root(proto_id) and full_id in chakra_state.unlocked:
                try:
                    sub_schema = resolve_body_schema(proto_id)
                    if sub_schema and isinstance(sub_schema, dict):
                        sub_root = sub_schema.get("root")
                        if sub_root:
                            sub_root_full = f"{full_id}.{sub_root}"
                            if sub_root_full in self._chakra_points:
                                # Connect branch root to sub-schema root
                                self._connections.append((full_id, sub_root_full))
                        # Recurse into sub-schema
                        self._build_connections(
                            sub_schema, chakra_state,
                            prefix=f"{full_id}.",
                            depth=depth + 1
                        )
                except Exception:
                    pass

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

        t = _smoothstep(max(0.0, min(1.0, t)))

        # Interpolate
        cx = _lerp(from_center[0], to_center[0], t)
        cy = _lerp(from_center[1], to_center[1], t)
        scale = _lerp(from_scale, to_scale, t)

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
            self._draw_chakra_point(surface, point, now_ms)

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
        px = int(_lerp(start[0], end[0], t))
        py = int(_lerp(start[1], end[1], t))

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
            base_color = _lerp_color(base_color, COLOR_HOVER, 0.4)
            base_alpha = 255

        # Selected highlight
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

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self._dragging_id:
                if self.on_chakra_drag_end and hasattr(event, "pos"):
                    try:
                        self.on_chakra_drag_end(self._dragging_id, event.pos)
                    except Exception:
                        pass
                self._dragging_id = None
                return True

        return super().handle_event(event, ctx)

    def _get_state(self) -> ChakraState:
        """Return state override when present, otherwise actor state."""
        if self.state_override is not None:
            return self.state_override
        if self.actor is None:
            return ChakraState()
        return _get_chakra_state(self.actor)

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

    def refresh_points(self) -> None:
        """Force a rebuild of chakra points and pixel positions."""
        self._chakra_points.clear()
        self._connections.clear()
        self._rebuild_chakra_points()
        self._compute_pixel_positions()

    def _get_chakra_at(self, pos: Tuple[int, int]) -> Optional[str]:
        """Get the chakra ID at the given pixel position."""
        click_radius = BASE_CHAKRA_RADIUS * 1.5

        for point in self._chakra_points.values():
            dx = pos[0] - point.pos_px[0]
            dy = pos[1] - point.pos_px[1]
            dist_sq = dx * dx + dy * dy

            if dist_sq <= click_radius * click_radius:
                return point.node_id

        return None

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

    def __init__(self, *, actor: Any = None) -> None:
        super().__init__()
        self.actor = actor
        self._pattern_surface: Optional[pygame.Surface] = None
        self._dirty: bool = True
        self._anim_time_ms: int = 0
        self.state_override: Optional[ChakraState] = None

    def set_actor(self, actor: Any) -> None:
        """Update the actor and mark pattern as dirty."""
        self.actor = actor
        self._dirty = True

    def set_state_override(self, state: Optional[ChakraState]) -> None:
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

        body_schema = _get_body_schema(self.actor)
        chakra_state = self.state_override or _get_chakra_state(self.actor)

        # Get positions of active chakras (includes sub-schema nodes)
        positions = get_chakra_world_positions(
            body_schema, chakra_state,
            base_scale=50.0,  # Larger for preview
            include_inactive=False,
        )

        if not positions:
            self._pattern_surface = None
            return

        # Create pattern surface
        size = min(self.rect.width - 20, self.rect.height - 50)
        if size <= 0:
            size = 100

        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        center = size // 2

        # Choose a root + baseline so the preview scale matches generator math.
        # This avoids misleading size shifts for deeper chakras.
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        if not xs or not ys:
            self._pattern_surface = surf
            return

        root_id = body_schema.get("root")
        if root_id and root_id in positions:
            root_pos = positions[root_id]
        else:
            # Fallback: closest to origin
            root_pos = min(positions.values(), key=lambda p: p[0] ** 2 + p[1] ** 2)

        def dist_sq_from_root(p: tuple) -> float:
            dx = p[0] - root_pos[0]
            dy = p[1] - root_pos[1]
            return dx * dx + dy * dy

        furthest_pos = max(positions.values(), key=dist_sq_from_root)
        base_len = math.hypot(furthest_pos[0] - root_pos[0], furthest_pos[1] - root_pos[1])
        if base_len < 1e-6:
            base_len = 1.0

        # Scale so the baseline (root -> furthest) occupies ~60% of the panel.
        scale = (size * 0.6) / base_len
        cx, cy = root_pos

        # Map positions to surface coordinates
        mapped = {}
        for node_id, (x, y) in positions.items():
            px = int(center + (x - cx) * scale)
            py = int(center + (y - cy) * scale)
            mapped[node_id] = (px, py)

        # Draw edges (recursive body connections, including sub-schemas)
        edge_color = (100, 180, 220, 180)
        try:
            from edgecaster.systems.chakras import get_chakra_connections_recursive
            edges = get_chakra_connections_recursive(body_schema, chakra_state)
        except Exception:
            edges = []

        for parent_id, child_id in edges:
            if parent_id in mapped and child_id in mapped:
                pygame.draw.aaline(
                    surf,
                    edge_color[:3],
                    mapped[parent_id],
                    mapped[child_id],
                )

        # Draw vertices
        vertex_color = (255, 220, 100)
        for pos in mapped.values():
            # Glow
            glow_surf = pygame.Surface((16, 16), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (*vertex_color, 60), (8, 8), 8)
            surf.blit(glow_surf, (pos[0] - 8, pos[1] - 8), special_flags=pygame.BLEND_ADD)

            # Core
            pygame.draw.circle(surf, vertex_color, pos, 4)

        self._pattern_surface = surf


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

        # Ensure chakra state exists so edits persist
        if self._actor is not None and not isinstance(self._actor.chakra_state, ChakraState):
            self._actor.chakra_state = ChakraState()

        # Mode state: "activate" (toggle) or "realign" (drag/commit)
        self._mode: str = "activate"
        self._pending_alignments: Optional[Dict[str, Tuple[float, float]]] = None
        self._original_alignments: Optional[Dict[str, Tuple[float, float]]] = None
        self._working_state: Optional[ChakraState] = None

        # Create widgets
        self._silhouette = ChakraSilhouetteWidget(
            actor=self._actor,
            on_chakra_click=self._on_chakra_click,
            on_chakra_hover=self._on_chakra_hover,
            on_chakra_drag_start=self._on_chakra_drag_start,
            on_chakra_drag=self._on_chakra_drag,
            on_chakra_drag_end=self._on_chakra_drag_end,
        )

        self._preview = PatternPreviewWidget(actor=self._actor)
        self._info = ChakraInfoWidget()

        # Commit/cancel buttons for realign mode
        self._btn_commit = ButtonWidget("Commit", on_click=self._on_commit_click)
        self._btn_cancel = ButtonWidget("Cancel", on_click=self._on_cancel_click)

        # Build widget tree
        self.root.add_child(self._silhouette)
        self.root.add_child(self._preview)
        self.root.add_child(self._info)
        self.root.add_child(self._btn_commit)
        self.root.add_child(self._btn_cancel)

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
            self._silhouette.select_chakra("body")
            self._update_info_for_chakra("body")
        elif chakra_state.unlocked:
            first = next(iter(chakra_state.unlocked))
            self._silhouette.select_chakra(first)
            self._update_info_for_chakra(first)

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

        # Update resonances
        body_schema = _get_body_schema(self._actor)
        resonances = check_resonance_bonuses(body_schema, chakra_state)
        self._info.set_resonances(resonances)

    def _get_ui_state(self) -> ChakraState:
        """Return the chakra state currently driving the UI (realign preview or live)."""
        if self._working_state is not None:
            return self._working_state
        if self._actor is None:
            return ChakraState()
        return _get_chakra_state(self._actor)

    def _get_actor_state(self) -> ChakraState:
        """Return the actor's actual chakra state (for persistent edits)."""
        if self._actor is None:
            return ChakraState()
        return _get_chakra_state(self._actor)

    def _clone_state(self, state: ChakraState, alignments: Dict[str, Tuple[float, float]]) -> ChakraState:
        """Clone a ChakraState but replace alignments (used for realign preview)."""
        return ChakraState(
            unlocked=set(state.unlocked),
            active=set(state.active),
            alignments=dict(alignments),
            generators=dict(state.generators),
        )

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
        state = self._get_actor_state()

        self._original_alignments = dict(state.alignments)
        self._pending_alignments = dict(state.alignments)
        self._working_state = self._clone_state(state, self._pending_alignments)

        # Route preview to the working state
        self._silhouette.set_state_override(self._working_state)
        self._preview.set_state_override(self._working_state)
        self._silhouette.set_realign_mode(True, self._compute_realign_limit())

        self._silhouette.refresh_points()
        self._preview.mark_dirty()

    def _exit_realign_mode(self, *, commit: bool) -> None:
        """Exit realign mode, optionally committing pending alignments."""
        if self._mode != "realign":
            return

        if commit and self._actor is not None and self._pending_alignments is not None:
            state = self._get_actor_state()
            changed = (self._pending_alignments != (self._original_alignments or {}))
            state.alignments = dict(self._pending_alignments)
            if changed:
                self._apply_realign_time_cost()

        # Clear preview overrides
        self._pending_alignments = None
        self._original_alignments = None
        self._working_state = None

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
        """Handle chakra click (toggle activation)."""
        self._silhouette.select_chakra(node_id)
        self._update_info_for_chakra(node_id)

        # In realign mode, clicks are handled by drag logic instead of toggling.
        if self._mode == "realign":
            return

        self._toggle_chakra(node_id)

    def _on_chakra_hover(self, node_id: Optional[str]) -> None:
        """Handle chakra hover."""
        if node_id:
            self._update_info_for_chakra(node_id)

    def _on_chakra_drag_start(self, node_id: str, pos_px: Tuple[int, int]) -> bool:
        """Begin dragging a chakra for alignment."""
        if self._mode != "realign":
            return False

        self._silhouette.select_chakra(node_id)
        self._update_info_for_chakra(node_id)

        # Only unlocked chakras can be realigned.
        state = self._get_actor_state()
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
            self._pending_alignments = dict(self._get_actor_state().alignments)
        if self._working_state is None:
            self._working_state = self._clone_state(self._get_actor_state(), self._pending_alignments)
            self._silhouette.set_state_override(self._working_state)
            self._preview.set_state_override(self._working_state)

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
        self._working_state.alignments = dict(self._pending_alignments)
        self._silhouette.set_realign_mode(True, limit)
        self._silhouette.refresh_points()
        self._preview.mark_dirty()

    def _toggle_chakra(self, node_id: str) -> None:
        """Toggle a chakra's active state."""
        if not self._actor:
            return

        chakra_state = _get_chakra_state(self._actor)

        # Can only toggle if unlocked
        if node_id not in chakra_state.unlocked:
            return

        # Check if activating or deactivating
        was_active = node_id in chakra_state.active

        # Toggle
        toggle_chakra_active(chakra_state, node_id)

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

            # Zoom controls
            if event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self._silhouette.adjust_zoom(1)
                return
            if event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self._silhouette.adjust_zoom(-1)
                return
            if event.key in (pygame.K_0, pygame.K_KP_0):
                self._silhouette.reset_zoom()
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
            elif event.y < 0:
                self._silhouette.adjust_zoom(-1, anchor_px=anchor)

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

            selected = self._silhouette.get_selected_chakra()
            if selected:
                self._toggle_chakra(selected)
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
                self._silhouette.select_chakra(next_id)
                self._update_info_for_chakra(next_id)

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

        # Preview takes right portion
        self._preview.rect = pygame.Rect(body_w, 0, preview_w, h - info_h)

        # Info panel at bottom
        self._info.rect = pygame.Rect(0, h - info_h, w, info_h)

        # Commit/cancel buttons (only visible in realign mode)
        self._btn_commit.visible = (self._mode == "realign")
        self._btn_cancel.visible = (self._mode == "realign")

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
        self._preview.layout(ctx)
        self._info.layout(ctx)
        self._btn_commit.layout(ctx)
        self._btn_cancel.layout(ctx)

        # Now place buttons with correct sizes
        if self._btn_cancel.visible:
            self._btn_cancel.rect.x = w - self._btn_cancel.rect.width - 12
            self._btn_cancel.rect.y = btn_y
        if self._btn_commit.visible:
            self._btn_commit.rect.x = self._btn_cancel.rect.x - self._btn_commit.rect.width - btn_pad
            self._btn_commit.rect.y = btn_y

        self._silhouette.draw(ctx)
        self._preview.draw(ctx)
        self._info.draw(ctx)
        self._btn_commit.draw(ctx)
        self._btn_cancel.draw(ctx)

        # Draw footer hint
        small_font = getattr(renderer, "small_font", None) or font
        if small_font:
            if self._mode == "realign":
                hint = "Drag: Realign  |  Enter: Commit  |  Esc: Cancel  |  R: Toggle  |  +/- or Wheel: Zoom"
            else:
                hint = "Arrows: Navigate  |  Space: Toggle  |  R: Realign  |  +/- or Wheel: Zoom  |  Esc: Close"
            hint_surf = small_font.render(hint, True, (100, 110, 130))
            hx = (w - hint_surf.get_width()) // 2
            panel.blit(hint_surf, (hx, h - 20))

        # Mode indicator in the info panel
        if small_font:
            mode_label = "Mode: Realign" if self._mode == "realign" else "Mode: Activate"
            mode_color = (180, 210, 255) if self._mode == "realign" else (140, 150, 170)
            mode_surf = small_font.render(mode_label, True, mode_color)
            panel.blit(mode_surf, (10, h - info_h + 10))
