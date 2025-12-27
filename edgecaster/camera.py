"""Camera utilities.

A small, renderer-agnostic tile camera.

Goals:
- Keep ASCII renderer "dumb": it asks the camera for transforms.
- Keep input / interaction math out of render/ascii.py.

World coordinates are in *tiles* (float), where integer centers are tile centers.
Screen coordinates are in *pixels* (float).

The camera stores:
- base_tile: the reference pixel size at zoom=1
- zoom: scalar applied to base_tile
- pan: pixel offset added on top of a UI-provided base origin
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class TileCamera:
    base_tile: int
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0

    # Guardrails / legibility
    min_zoom: float = 0.002
    # NOTE: we allow *very* deep zoom-in so negative LoD terrain can be explored.
    # Rough calibration: to reach LoD -10 with base_tile==target_glyph_px==16 you
    # need zoom ~= 1024, so give ourselves headroom.
    max_zoom: float = 2048.0
    min_tile_px: int = 1

    @property
    def tile_px(self) -> int:
        # NOTE: renderer may still choose to clamp fonts separately for legibility.
        return max(self.min_tile_px, int(round(float(self.base_tile) * float(self.zoom))))

    def map_origin_px(self, base_origin_px: Tuple[float, float]) -> Tuple[float, float]:
        bx, by = base_origin_px
        return bx + float(self.pan_x), by + float(self.pan_y)

    def world_from_screen_px(
        self,
        screen_px: Tuple[float, float],
        base_origin_px: Tuple[float, float],
    ) -> Tuple[float, float]:
        sx, sy = screen_px
        ox, oy = self.map_origin_px(base_origin_px)
        t = float(max(1, self.tile_px))
        return (float(sx) - ox) / t, (float(sy) - oy) / t

    def screen_from_world(
        self,
        world_xy: Tuple[float, float],
        base_origin_px: Tuple[float, float],
    ) -> Tuple[float, float]:
        wx, wy = world_xy
        ox, oy = self.map_origin_px(base_origin_px)
        t = float(max(1, self.tile_px))
        return float(wx) * t + ox, float(wy) * t + oy

    def pan_by_px(self, dx: float, dy: float) -> None:
        self.pan_x += float(dx)
        self.pan_y += float(dy)

    def center_on_world(
        self,
        world_xy: Tuple[float, float],
        *,
        base_origin_px: Tuple[float, float],
        target_screen_px: Tuple[float, float],
    ) -> None:
        """Set pan so a given world point appears at a given screen pixel."""
        wx, wy = float(world_xy[0]), float(world_xy[1])
        tx, ty = float(target_screen_px[0]), float(target_screen_px[1])
        bx, by = float(base_origin_px[0]), float(base_origin_px[1])
        t = float(max(1, self.tile_px))
        # screen = world*t + base + pan  =>  pan = screen - base - world*t
        self.pan_x = tx - bx - wx * t
        self.pan_y = ty - by - wy * t

    def zoom_steps(
        self,
        delta_steps: int,
        *,
        cursor_px: Tuple[int, int],
        base_origin_px: Tuple[float, float],
        zoom_rate: float = 0.15,
        damp_exp: float = 0.6,
    ) -> bool:
        """Zoom by mouse wheel steps, keeping the world point under cursor fixed.

        This uses a multiplicative zoom so it behaves well at very small zooms.
        Returns True if the camera changed.
        """
        if delta_steps == 0:
            return False

        cx, cy = int(cursor_px[0]), int(cursor_px[1])

        before_wx, before_wy = self.world_from_screen_px((cx, cy), base_origin_px)

        # Symmetric damping: slow down wheel steps both when extremely zoomed out
        # *and* extremely zoomed in.
        z = float(self.zoom)
        if z <= 0.0:
            return False
        if z < 1.0:
            damp = max(0.05, z ** float(damp_exp))
        else:
            damp = max(0.05, z ** (-float(damp_exp)))
        zoom_factor = 1.0 + float(delta_steps) * float(zoom_rate) * damp
        if zoom_factor <= 0.0:
            return False

        new_zoom = z * zoom_factor
        new_zoom = max(float(self.min_zoom), min(float(self.max_zoom), new_zoom))
        if abs(new_zoom - z) < 1e-9:
            return False

        self.zoom = float(new_zoom)

        # After zoom, adjust pan so the same world point remains under cursor.
        self.center_on_world(
            (before_wx, before_wy),
            base_origin_px=base_origin_px,
            target_screen_px=(float(cx), float(cy)),
        )
        return True
