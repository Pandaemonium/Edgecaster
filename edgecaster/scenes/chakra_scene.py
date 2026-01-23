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

from edgecaster.ui.widgets import Widget, WidgetContext, LabelWidget
from edgecaster.systems.chakras import (
    ChakraState,
    can_unlock_chakra,
    toggle_chakra_active,
    check_resonance_bonuses,
    get_chakra_world_positions,
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
    pos_px: Tuple[int, int]  # Pixel coordinates (computed during layout)
    state: str  # "locked", "unlocked", "active"
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
    ) -> None:
        super().__init__()
        self.actor = actor
        self.on_chakra_click = on_chakra_click
        self.on_chakra_hover = on_chakra_hover

        # Cached chakra points (rebuilt on layout)
        self._chakra_points: Dict[str, ChakraPoint] = {}
        self._hovered_id: Optional[str] = None
        self._selected_id: Optional[str] = None

        # Animation state
        self._anim_time_ms: int = 0

        # Camera state for zoom navigation
        self._zoom_stack: List[str] = []  # Stack of node IDs we've zoomed into
        self._cam_center: Tuple[float, float] = (0.0, 0.0)
        self._cam_scale: float = 1.0
        self._zoom_anim: Optional[Tuple] = None  # (from_center, from_scale, to_center, to_scale, start_ms, dur_ms)

        # Parent-child connections for energy flow lines
        self._connections: List[Tuple[str, str]] = []

        # Particle system for activation bursts
        self._particles: List[Particle] = []

    def set_actor(self, actor: Any) -> None:
        """Update the actor being displayed."""
        self.actor = actor
        self._chakra_points.clear()
        self._connections.clear()

    def _rebuild_chakra_points(self) -> None:
        """Rebuild chakra point data from actor's body schema."""
        self._chakra_points.clear()
        self._connections.clear()

        if self.actor is None:
            return

        body_schema = _get_body_schema(self.actor)
        chakra_state = _get_chakra_state(self.actor)
        nodes = _get_nodes_from_schema(body_schema)

        if not nodes:
            return

        # Get positions in unit coordinates
        positions = get_chakra_world_positions(
            body_schema, chakra_state,
            base_scale=1.0,
            include_inactive=True  # Show all unlocked, not just active
        )

        # Also include locked nodes (all nodes from schema)
        for node_id, node in nodes.items():
            if node_id not in positions:
                # Get layout position for locked nodes
                layout = node.get("layout", {}) if isinstance(node, dict) else {}
                x = float(layout.get("x", 0.0)) if isinstance(layout, dict) else 0.0
                y = float(layout.get("y", 0.0)) if isinstance(layout, dict) else 0.0
                positions[node_id] = (x, y)

        # Determine state for each node
        for node_id, pos_u in positions.items():
            if node_id in chakra_state.active:
                state = "active"
            elif node_id in chakra_state.unlocked:
                state = "unlocked"
            else:
                state = "locked"

            # Per-chakra pulse phase offset for visual variety
            phase_offset = hash(node_id) % 1000 / 1000.0

            self._chakra_points[node_id] = ChakraPoint(
                node_id=node_id,
                pos_u=pos_u,
                pos_px=(0, 0),  # Computed during layout
                state=state,
                pulse_phase=phase_offset,
            )

        # Build parent-child connections for energy flow
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            children = node.get("children", [])
            if isinstance(children, list):
                for child_id in children:
                    if child_id and str(child_id) in self._chakra_points:
                        self._connections.append((node_id, str(child_id)))

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

        # Draw background
        pygame.draw.rect(surface, (15, 15, 25), self.rect)
        pygame.draw.rect(surface, (60, 60, 80), self.rect, 1)

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
        chakra_state = _get_chakra_state(self.actor) if self.actor else ChakraState()

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

                return True
            else:
                self._hovered_id = None

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = getattr(event, "pos", None)
            if pos and self.rect.collidepoint(pos):
                clicked_id = self._get_chakra_at(pos)
                if clicked_id and self.on_chakra_click:
                    self.on_chakra_click(clicked_id)
                    return True

        return super().handle_event(event, ctx)

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

    def set_actor(self, actor: Any) -> None:
        """Update the actor and mark pattern as dirty."""
        self.actor = actor
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

        # Background
        pygame.draw.rect(surface, (20, 20, 30), self.rect)
        pygame.draw.rect(surface, (60, 60, 80), self.rect, 1)

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
        chakra_state = _get_chakra_state(self.actor)

        # Get positions of active chakras
        positions = get_chakra_world_positions(
            body_schema, chakra_state,
            base_scale=50.0,  # Larger for preview
            include_inactive=False
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

        # Find bounds
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        if not xs or not ys:
            self._pattern_surface = surf
            return

        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        spanx = max(1, maxx - minx)
        spany = max(1, maxy - miny)
        scale = (size * 0.8) / max(spanx, spany)

        cx = (minx + maxx) / 2
        cy = (miny + maxy) / 2

        # Map positions to surface coordinates
        mapped = {}
        for node_id, (x, y) in positions.items():
            px = int(center + (x - cx) * scale)
            py = int(center + (y - cy) * scale)
            mapped[node_id] = (px, py)

        # Draw edges (body tree connections)
        nodes = _get_nodes_from_schema(body_schema)
        edge_color = (100, 180, 220, 180)

        for node_id, node in nodes.items():
            if node_id not in mapped:
                continue
            children = node.get("children", []) if isinstance(node, dict) else []
            if isinstance(children, list):
                for child_id in children:
                    if child_id and str(child_id) in mapped:
                        pygame.draw.aaline(
                            surf,
                            edge_color[:3],
                            mapped[node_id],
                            mapped[str(child_id)]
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

        # Create widgets
        self._silhouette = ChakraSilhouetteWidget(
            actor=self._actor,
            on_chakra_click=self._on_chakra_click,
            on_chakra_hover=self._on_chakra_hover,
        )

        self._preview = PatternPreviewWidget(actor=self._actor)
        self._info = ChakraInfoWidget()

        # Build widget tree
        self.root.add_child(self._silhouette)
        self.root.add_child(self._preview)
        self.root.add_child(self._info)

        # Input handling
        self._menu_input = MenuInput()

        # Select first unlocked chakra
        self._select_initial_chakra()

    def _select_initial_chakra(self) -> None:
        """Select the first unlocked chakra for keyboard navigation."""
        if not self._actor:
            return

        chakra_state = _get_chakra_state(self._actor)

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

        chakra_state = _get_chakra_state(self._actor)

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

    def _on_chakra_click(self, node_id: str) -> None:
        """Handle chakra click (toggle activation)."""
        self._silhouette.select_chakra(node_id)
        self._toggle_chakra(node_id)

    def _on_chakra_hover(self, node_id: Optional[str]) -> None:
        """Handle chakra hover."""
        if node_id:
            self._update_info_for_chakra(node_id)

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
        self._silhouette._rebuild_chakra_points()
        self._preview.mark_dirty()
        self._update_info_for_chakra(node_id)

    def _panel_event(self, event, manager: "SceneManager") -> None:
        """Handle keyboard input."""
        if event.type == pygame.KEYDOWN:
            action = self._menu_input.handle_keydown(event.key)
            if action:
                self._handle_action(action, manager)

        elif event.type == pygame.KEYUP:
            self._menu_input.handle_keyup(event.key)

    def _handle_action(self, action: str, manager: "SceneManager") -> None:
        """Handle a menu action."""
        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        if action == MENU_ACTION_BACK:
            self._close(manager)
            return

        if action == MENU_ACTION_ACTIVATE:
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
        # Background
        panel.fill((10, 12, 18))

        # Layout widgets
        w = panel.get_width()
        h = panel.get_height()

        body_w = int(w * BODY_PANEL_WIDTH_FRAC)
        preview_w = w - body_w
        info_h = 80

        # Silhouette takes left portion
        self._silhouette.rect = pygame.Rect(0, 0, body_w, h - info_h)

        # Preview takes right portion
        self._preview.rect = pygame.Rect(body_w, 0, preview_w, h - info_h)

        # Info panel at bottom
        self._info.rect = pygame.Rect(0, h - info_h, w, info_h)

        # Draw title
        font = getattr(renderer, "menu_font", None) or getattr(renderer, "font", None)
        if font:
            title = font.render("CHAKRA MANAGEMENT", True, (200, 180, 255))
            tx = (w - title.get_width()) // 2
            panel.blit(title, (tx, 8))

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

        self._silhouette.draw(ctx)
        self._preview.draw(ctx)
        self._info.draw(ctx)

        # Draw footer hint
        small_font = getattr(renderer, "small_font", None) or font
        if small_font:
            hint = "Arrows: Navigate  |  Space: Toggle  |  Esc: Close"
            hint_surf = small_font.render(hint, True, (100, 100, 120))
            hx = (w - hint_surf.get_width()) // 2
            panel.blit(hint_surf, (hx, h - 20))
