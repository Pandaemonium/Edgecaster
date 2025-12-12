"""Visual profile helpers for rendering adjustments."""
from dataclasses import dataclass
import math
import pygame


@dataclass
class VisualProfile:
    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    angle: float = 0.0
    alpha: float = 1.0
    flip_x: bool = False
    flip_y: bool = False


def apply_visual_panel(base_surface, logical_surface, window_rect, visual: VisualProfile) -> None:
    """Apply the visual profile to draw a logical panel surface into the base surface."""
    # Start from the logical surface scaled to the panel's window size, then apply
    # the profile's per-axis scaling.
    target_width = max(1, int(window_rect.width * visual.scale_x))
    target_height = max(1, int(window_rect.height * visual.scale_y))
    transformed = pygame.transform.scale(logical_surface, (target_width, target_height))

    # Apply flips if requested.
    if visual.flip_x or visual.flip_y:
        transformed = pygame.transform.flip(transformed, visual.flip_x, visual.flip_y)

    # Rotate around the panel center. rotozoom keeps the center stable while applying
    # rotation and can also handle extra scaling (kept at 1.0 here because we already
    # applied scale_x/scale_y above).
    if visual.angle:
        transformed = pygame.transform.rotozoom(transformed, visual.angle, 1.0)

    # Apply alpha, ensuring the surface never becomes fully invisible.
    alpha_value = max(1, min(255, int(visual.alpha * 255)))
    transformed.set_alpha(alpha_value)

    # Position the transformed surface so its center aligns with the target window,
    # allowing optional offsets.
    target_rect = transformed.get_rect()
    target_rect.center = (
        window_rect.centerx + visual.offset_x,
        window_rect.centery + visual.offset_y,
    )

    # Blit the transformed panel into the base surface.
    base_surface.blit(transformed, target_rect)


def unproject_mouse(pos_display, window_rect, visual: VisualProfile) -> tuple[float, float]:
    """Convert a display- or surface-space mouse position back into panel-local coordinates.

    This is the exact inverse of apply_visual_panel(), assuming:
      - logical panel size = window_rect.size
      - scale_x/scale_y are applied about the panel center
      - flips are applied after scaling
      - rotation is about the panel center
      - final blit centers the transformed panel on window_rect.center + offset
    """
    # Translate so that the transformed panel center is at the origin
    dx = pos_display[0] - (window_rect.centerx + visual.offset_x)
    dy = pos_display[1] - (window_rect.centery + visual.offset_y)

    # Undo rotation (inverse angle)
    if visual.angle:
        angle_rad = math.radians(visual.angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        dx, dy = (dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)

    # Undo flips
    if visual.flip_x:
        dx = -dx
    if visual.flip_y:
        dy = -dy

    # Undo scaling (guard against zero/near-zero)
    safe_scale_x = visual.scale_x if abs(visual.scale_x) > 1e-6 else 1.0
    safe_scale_y = visual.scale_y if abs(visual.scale_y) > 1e-6 else 1.0
    dx /= safe_scale_x
    dy /= safe_scale_y

    # Convert back from centered coords to panel-local top-left origin
    panel_x = dx + window_rect.width / 2
    panel_y = dy + window_rect.height / 2
    return (panel_x, panel_y)
