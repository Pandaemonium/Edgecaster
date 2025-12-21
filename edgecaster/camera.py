"""Camera utilities.

Step 1: a minimal, disciplined tile-camera for the ASCII renderer.

This camera intentionally does *not* depend on pygame and is safe to reuse for:
- local dungeon view (tile grid -> pixels)
- future hierarchy views (local -> zone -> world)

The ASCII renderer remains responsible for:
- choosing what to render (local tiles vs. collapsed glyphs)
- font/surface recreation when tile size changes
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class TileCamera:
    """A 2D camera for a tile grid.

    Coordinates:
    - World coordinates are in *tiles* (float), where integer centers are tile centers.
    - Screen coordinates are in *pixels* (float).

    The camera itself only stores a zoom and a pan relative to a UI-provided
    base origin (e.g., after accounting for top bars / side panels).
    """

    base_tile: int
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    # Guardrails (kept conservative for legibility).
    min_zoom: float = 0.01
    max_zoom: float = 6.0
    min_tile_px: int = 1

    @property
    def tile_px(self) -> int:
        return max(self.min_tile_px, int(self.base_tile * self.zoom))

    def map_origin_px(self, base_origin_px: Tuple[float, float]) -> Tuple[float, float]:
        """Return the pixel origin for world tile (0,0) given the UI base origin."""
        bx, by = base_origin_px
        return bx + self.pan_x, by + self.pan_y

    def world_from_screen_px(
        self,
        screen_px: Tuple[float, float],
        base_origin_px: Tuple[float, float],
    ) -> Tuple[float, float]:
        """Convert screen pixel coordinates to world tile coordinates (float)."""
        sx, sy = screen_px
        ox, oy = self.map_origin_px(base_origin_px)
        t = float(max(1, self.tile_px))
        return (sx - ox) / t, (sy - oy) / t

    def screen_from_world(
        self,
        world_xy: Tuple[float, float],
        base_origin_px: Tuple[float, float],
    ) -> Tuple[float, float]:
        """Convert world tile coordinates (float) to screen pixel coordinates."""
        wx, wy = world_xy
        ox, oy = self.map_origin_px(base_origin_px)
        t = float(max(1, self.tile_px))
        return wx * t + ox, wy * t + oy

    def pan_by_px(self, dx: float, dy: float) -> None:
        self.pan_x += float(dx)
        self.pan_y += float(dy)

    def zoom_steps(
        self,
        delta_steps: int,
        cursor_px: Tuple[int, int],
        base_origin_px: Tuple[float, float],
    ) -> bool:
        """Zoom in/out by wheel 'steps', keeping the world point under cursor fixed.

        Returns True if the camera changed.
        """
        if delta_steps == 0:
            return False

        cx, cy = cursor_px

        # World position under cursor before zoom.
        before_wx, before_wy = self.world_from_screen_px((cx, cy), base_origin_px)

        new_zoom = float(self.zoom) + float(delta_steps) * 0.1
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        if abs(new_zoom - float(self.zoom)) < 1e-6:
            return False

        # Apply zoom.
        self.zoom = new_zoom

        # After zoom, adjust pan so that (before_wx, before_wy) stays under cursor.
        # screen = world * tile_px + (base + pan)
        t = float(max(1, self.tile_px))
        bx, by = base_origin_px
        target_origin_x = float(cx) - before_wx * t
        target_origin_y = float(cy) - before_wy * t
        self.pan_x = target_origin_x - float(bx)
        self.pan_y = target_origin_y - float(by)
        return True
