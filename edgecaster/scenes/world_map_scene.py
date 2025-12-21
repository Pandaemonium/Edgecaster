from __future__ import annotations

import csv
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

import pygame

from edgecaster import mapgen
from edgecaster.corruption import CorruptionParams, distortion_dz
from .base import Scene


@dataclass
class Viewport:
    """Defines a view into the world map in world coordinates."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    width: int  # Pixel width of the surface this viewport is projected onto
    height: int  # Pixel height of the surface

    def pixel_to_world(self, px: float, py: float) -> tuple[float, float]:
        """Convert pixel coordinates on the surface to world coordinates."""
        span_x = self.x_max - self.x_min
        span_y = self.y_max - self.y_min
        wx = self.x_min + (px / max(1, self.width - 1)) * span_x
        wy = self.y_min + (py / max(1, self.height - 1)) * span_y
        return wx, wy

    def world_to_pixel(self, wx: float, wy: float) -> tuple[int, int]:
        """Convert world coordinates to pixel coordinates on the surface."""
        span_x = self.x_max - self.x_min
        span_y = self.y_max - self.y_min
        if span_x <= 1e-9 or span_y <= 1e-9:
            return 0, 0
        px = (wx - self.x_min) / span_x * max(1, self.width - 1)
        py = (wy - self.y_min) / span_y * max(1, self.height - 1)
        return int(round(px)), int(round(py))

    @property
    def view(self) -> tuple[float, float, float, float]:
        """Return the view as (min_x, min_y, span_x, span_y), for legacy APIs."""
        return (self.x_min, self.y_min, self.x_max - self.x_min, self.y_max - self.y_min)

    def clamp(self, world_width: float, world_height: float):
        """Clamp the viewport to the world boundaries and a minimum span."""
        span_x = self.x_max - self.x_min
        span_y = self.y_max - self.y_min

        # Prevent zooming in too far (span becomes too small)
        min_span = 1e-3
        if span_x < min_span:
            span_x = min_span
        if span_y < min_span:
            span_y = min_span

        # Prevent zooming out too far (span is larger than world)
        if span_x > world_width:
            span_x = world_width
        if span_y > world_height:
            span_y = world_height

        # Clamp min/max to be within world boundaries [0, world_size]
        self.x_min = max(0.0, min(self.x_min, world_width - span_x))
        self.y_min = max(0.0, min(self.y_min, world_height - span_y))
        self.x_max = self.x_min + span_x
        self.y_max = self.y_min + span_y


def zoom_viewport(vp: Viewport, center_px: tuple[int, int], scroll_y: int) -> bool:
    """
    Update a Viewport in-place to zoom in or out, centered on a pixel.
    Returns True if the viewport changed.
    """
    if scroll_y == 0:
        return False

    # Adjust zoom factor based on scroll direction; SHIFT for faster zoom
    step = 1.25 if pygame.key.get_mods() & pygame.KMOD_SHIFT else 1.15
    span_factor = 1.0 / step if scroll_y > 0 else step

    # Get the world coordinate under the cursor before the zoom
    cx, cy = center_px
    world_x_center, world_y_center = vp.pixel_to_world(cx, cy)

    # Calculate the new span of the viewport
    span_x_old = vp.x_max - vp.x_min
    span_y_old = vp.y_max - vp.y_min
    span_x_new = span_x_old * span_factor
    span_y_new = span_y_old * span_factor

    # Determine the ratio of the cursor's position within the viewport
    t_x = cx / max(1, vp.width - 1)
    t_y = cy / max(1, vp.height - 1)

    # Set the new viewport bounds, keeping the point under the cursor fixed
    new_x_min = world_x_center - t_x * span_x_new
    new_y_min = world_y_center - t_y * span_y_new
    new_x_max = new_x_min + span_x_new
    new_y_max = new_y_min + span_y_new

    if (
        abs(new_x_min - vp.x_min) < 1e-9
        and abs(new_y_min - vp.y_min) < 1e-9
        and abs(new_x_max - vp.x_max) < 1e-9
        and abs(new_y_max - vp.y_max) < 1e-9
    ):
        return False

    vp.x_min, vp.x_max = new_x_min, new_x_max
    vp.y_min, vp.y_max = new_y_min, new_y_max
    return True


class WorldMapScene(Scene):
    """World map overlay with Julia-based relief."""

    GOOD_C = [
        complex(-0.40, 0.60),
        complex(-0.70, 0.30),
        complex(0.285, 0.01),
        complex(-0.20, 0.65),
        complex(-0.80, 0.156),
        complex(-0.835, -0.2321),
        complex(-0.70176, -0.3842),
        complex(-0.75, 0.11),
    ]
    _c_path_cache: Optional[List[Dict[str, float]]] = None

    def __init__(self, game, span: int = 16) -> None:
        self.game = game
        self.span = span
        self._cached: dict[tuple[int | None], pygame.Surface] = {}


    def _quantized_view_key(self, viewport: "Viewport") -> tuple[int, int, int, int, int]:
        """Return a stable cache key for the current viewport.

        We quantize the view window so minor float jitter and small mouse-wheel deltas
        don't create an unbounded number of near-duplicate cache entries.
        """
        x_min = float(viewport.x_min)
        x_max = float(viewport.x_max)
        y_min = float(viewport.y_min)
        y_max = float(viewport.y_max)

        vw = max(1.0, x_max - x_min)
        vh = max(1.0, y_max - y_min)

        # Quantization step in world units: roughly 1/512 of the larger span, clamped.
        q = max(1.0, min(64.0, max(vw, vh) / 512.0))
        q_inv = 1.0 / q

        qx = int(round(x_min * q_inv))
        qy = int(round(y_min * q_inv))
        qw = int(round(vw * q_inv))
        qh = int(round(vh * q_inv))
        qk = int(round(q * 1000.0))  # include step size to avoid collisions across scales
        return (qx, qy, qw, qh, qk)
    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        clock = pygame.time.Clock()
        running = True

        # Temporary corruption slider (for tuning feel/scale). Removed once validated.
        slider_min = 0.0
        slider_max = 2.0
        pending_corruption = float(getattr(self.game, "corruption_level", 0.0) or 0.0)
        dragging_slider = False

        # Worldmap zoom setup
        cfg = self.game.cfg
        total_w_i = int(cfg.world_map_screens * cfg.world_width)
        total_h_i = int(cfg.world_map_screens * cfg.world_height)
        world_max_wx = float(max(1, total_w_i - 1))
        world_max_wy = float(max(1, total_h_i - 1))

        # The viewport tracks the visible part of the world in world coordinates.
        # Its pixel dimensions are initialized later, once the map surface is available.
        # Reset the view to the full world every time the scene is entered to ensure
        # the cached full-map is used if available.
        view_token = 0
        setattr(self.game, "world_map_view_token", view_token)
        viewport = Viewport(
            x_min=0.0,
            x_max=world_max_wx,
            y_min=0.0,
            y_max=world_max_wy,
            width=1,
            height=1,
        )

        # Debounced re-render: show a cheap rescale preview while the async render runs.
        rerender_deadline: Optional[float] = None
        preview_surface: Optional[pygame.Surface] = None
        preview_token: Optional[int] = None
        preview_show_corr: Optional[bool] = None

        def build_preview(
            base_surf: pygame.Surface,
            base_view: tuple[float, float, float, float],
            want_view: tuple[float, float, float, float],
        ) -> Optional[pygame.Surface]:
            """Cheaply approximate `want_view` by cropping/rescaling `base_surf`."""
            try:
                bw, bh = base_surf.get_size()
                if bw <= 1 or bh <= 1:
                    return None
            except Exception:
                return None

            bminx, bminy, bspanx, bspany = [float(v) for v in base_view]
            wminx, wminy, wspanx, wspany = [float(v) for v in want_view]
            if bspanx <= 0.0 or bspany <= 0.0 or wspanx <= 0.0 or wspany <= 0.0:
                return None

            bix0 = max(wminx, bminx)
            biy0 = max(wminy, bminy)
            bix1 = min(wminx + wspanx, bminx + bspanx)
            biy1 = min(wminy + wspany, bminy + bspany)
            if bix1 <= bix0 or biy1 <= biy0:
                return None

            # Source crop in base pixels (intersection region).
            sx0 = int(round((bix0 - bminx) / bspanx * bw))
            sy0 = int(round((biy0 - bminy) / bspany * bh))
            sx1 = int(round((bix1 - bminx) / bspanx * bw))
            sy1 = int(round((biy1 - bminy) / bspany * bh))
            sx0 = max(0, min(bw - 1, sx0))
            sy0 = max(0, min(bh - 1, sy0))
            sx1 = max(sx0 + 1, min(bw, sx1))
            sy1 = max(sy0 + 1, min(bh, sy1))
            src_rect = pygame.Rect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0))

            # Destination placement in want-view pixels.
            dx0 = int(round((bix0 - wminx) / wspanx * bw))
            dy0 = int(round((biy0 - wminy) / wspany * bh))
            dx1 = int(round((bix1 - wminx) / wspanx * bw))
            dy1 = int(round((biy1 - wminy) / wspany * bh))
            dw = max(1, min(bw, dx1 - dx0))
            dh = max(1, min(bh, dy1 - dy0))
            dx0 = max(0, min(bw - dw, dx0))
            dy0 = max(0, min(bh - dh, dy0))

            try:
                sub = base_surf.subsurface(src_rect)
                scaled = pygame.transform.smoothscale(sub, (dw, dh))
                out = pygame.Surface((bw, bh))
                out.fill((0, 0, 0))
                out.blit(scaled, (dx0, dy0))
                return out
            except Exception:
                return None

        # Ensure map_surface exists before the first event pump (mouse click safety).
        show_corr = bool(pygame.key.get_mods() & pygame.KMOD_ALT)
        self.game.world_map_view = viewport.view
        self.game.world_map_view_token = int(view_token)
        if not getattr(self.game, "world_map_ready", False) and not getattr(
            self.game, "world_map_rendering", False
        ):
            try:
                self.game._start_world_map_thread(
                    reason="loading",
                    width=int(renderer.width),
                    height=int(renderer.height),
                    span=int(self.span),
                    view=self.game.world_map_view,
                    view_token=int(view_token),
                )
            except Exception:
                pass
        map_surface = self._build_cached_surface(
            renderer, show_corruption=show_corr, view_token=view_token
        )
        viewport.width, viewport.height = map_surface.get_size()
        last_drawn_surface: pygame.Surface = map_surface
        last_drawn_view: tuple[float, float, float, float] = viewport.view

        render_start_time: Optional[float] = None
        if getattr(self.game, "world_map_rendering", False):
            render_start_time = time.perf_counter()

        while running:
            # Slider layout (kept in the top margin area).
            slider_rect = pygame.Rect(24, 24, 280, 16)



            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F11:
                        renderer.toggle_fullscreen()
                        continue
                    if event.key == pygame.K_s:
                        cur = float(
                            getattr(self.game, "corruption_spline_weight", 0.0) or 0.0
                        )
                        self.game.set_corruption_spline_weight(0.0 if cur > 1e-6 else 1.0)
                        continue
                    if event.key == pygame.K_0:
                        viewport.x_min, viewport.y_min = 0.0, 0.0
                        viewport.x_max, viewport.y_max = world_max_wx, world_max_wy
                        view_token = 0 # Reset to full map key
                        self.game.world_map_view = viewport.view
                        self.game.world_map_view_token = int(view_token)
                        rerender_deadline = time.perf_counter()
                        render_start_time = None
                        preview_surface = None
                    if event.key in (
                        pygame.K_ESCAPE,
                        pygame.K_RETURN,
                        pygame.K_SPACE,
                        pygame.K_LESS,
                        pygame.K_COMMA,
                        pygame.K_PERIOD,
                        pygame.K_GREATER,
                    ):
                        running = False
                        break

                if event.type == pygame.MOUSEWHEEL:
                    if dragging_slider:
                        continue

                    mx, my = renderer._to_surface(pygame.mouse.get_pos())
                    map_w, map_h = map_surface.get_size()
                    ox = (renderer.width - map_w) // 2
                    oy = (renderer.height - map_h) // 2
                    rel_x = mx - ox
                    rel_y = my - oy
                    if not (0 <= rel_x < map_w and 0 <= rel_y < map_h):
                        continue
                    
                    current_view_for_preview = last_drawn_view
                    viewport.width, viewport.height = map_w, map_h
                    if zoom_viewport(viewport, (rel_x, rel_y), event.y):
                        viewport.clamp(world_max_wx, world_max_wy)
                        view_token += 1

                        dbg = getattr(self.game, "_debug", None)
                        if callable(dbg):
                            dbg(f"[world_map_scene] Zoom detected. New view_token: {view_token}")
                            dbg(f"[world_map_scene] New viewport: {viewport.view}")
                        
                        # Immediately start the re-render thread on zoom.
                        self.game.world_map_view = viewport.view
                        self.game.world_map_view_token = int(view_token)
                        if callable(dbg):
                            dbg(f"[world_map_scene] Calling _start_world_map_thread with view_token={view_token}")
                        try:
                            self.game._start_world_map_thread(
                                reason="view",
                                width=int(renderer.width),
                                height=int(renderer.height),
                                span=int(self.span),
                                view=self.game.world_map_view,
                                view_token=int(view_token),
                            )
                            render_start_time = time.perf_counter()
                        except Exception:
                            render_start_time = None

                        show_corr_evt = bool(pygame.key.get_mods() & pygame.KMOD_ALT)
                        prev = build_preview(last_drawn_surface, current_view_for_preview, viewport.view)
                        if prev:
                            preview_surface = prev
                            preview_token = int(view_token)
                            preview_show_corr = show_corr_evt

                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = renderer._to_surface(event.pos)
                    if slider_rect.collidepoint(mx, my):
                        dragging_slider = True
                        t = (mx - slider_rect.x) / max(1, slider_rect.w)
                        t = max(0.0, min(1.0, float(t)))
                        pending_corruption = slider_min + t * (slider_max - slider_min)
                        continue

                    if self.game.world_map_ready:
                        map_w, map_h = map_surface.get_size()
                        ox = (renderer.width - map_w) // 2
                        oy = (renderer.height - map_h) // 2
                        rel_x = mx - ox
                        rel_y = my - oy
                        if 0 <= rel_x < map_w and 0 <= rel_y < map_h:
                            wx, wy = viewport.pixel_to_world(rel_x, rel_y)
                            wx = int(max(0, min(total_w_i - 1, wx)))
                            wy = int(max(0, min(total_h_i - 1, wy)))
                            zx = wx // self.game.cfg.world_width
                            zy = wy // self.game.cfg.world_height
                            self.game.fast_travel_to_zone(zx, zy)
                            running = False
                            break

                if event.type == pygame.MOUSEMOTION and dragging_slider:
                    mx, my = renderer._to_surface(event.pos)
                    t = (mx - slider_rect.x) / max(1, slider_rect.w)
                    t = max(0.0, min(1.0, float(t)))
                    pending_corruption = slider_min + t * (slider_max - slider_min)

                if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and dragging_slider:
                    dragging_slider = False
                    new_level = float(pending_corruption)
                    cur = float(getattr(self.game, "corruption_level", 0.0) or 0.0)
                    if abs(new_level - cur) > 1e-6:
                        self.game.set_corruption_level(new_level)

            surf = renderer.surface
            surf.fill(renderer.bg)
            show_corr = bool(pygame.key.get_mods() & pygame.KMOD_ALT)

            # --- Surface selection logic ---
            desired_key = (
                int(renderer.width),
                int(renderer.height),
                int(self.span),
                # Cache by (quantized) viewport window rather than a monotonically increasing token.
                # This allows reuse when the view comes back to a previous window.
                *self._quantized_view_key(viewport),
                int(getattr(self.game, "corruption_version", 0) or 0),
            )
            
            if not hasattr(self.game, "world_map_cache_dict"):
                self.game.world_map_cache_dict = {}
            cache_dict = self.game.world_map_cache_dict
            cached_data = cache_dict.get(desired_key)

            surface_to_draw: Optional[pygame.Surface] = None
            if cached_data:
                surface_to_draw = cached_data.get("surface_corr") if show_corr else cached_data.get("surface")
                if surface_to_draw:
                    map_surface = surface_to_draw
                preview_surface = None
            elif preview_surface is not None and preview_token == int(view_token) and preview_show_corr == show_corr:
                surface_to_draw = preview_surface
            else:
                 surface_to_draw = self._build_cached_surface(
                    renderer, show_corruption=show_corr, view_token=view_token
                )

            if surface_to_draw is None:
                surface_to_draw = map_surface

            map_w, map_h = surface_to_draw.get_size()
            viewport.width, viewport.height = map_w, map_h
            
            ox = (renderer.width - map_w) // 2
            oy = (renderer.height - map_h) // 2

            last_drawn_surface = surface_to_draw
            last_drawn_view = viewport.view

            surf.blit(surface_to_draw, (ox, oy))

            # ... (marker drawing remains the same) ...

            title = renderer.big_label("World Map")
            surf.blit(title, (ox, oy - 36))
            hint = renderer.small_font.render(
                "Esc/Enter/< to return  |  Scroll to zoom (0 resets)",
                True,
                renderer.fg,
            )
            surf.blit(hint, (ox, oy + surface_to_draw.get_height() + 8))

            # ... (slider drawing remains the same) ...
            
            if getattr(self.game, "world_map_rendering", False):
                if render_start_time is None:
                    render_start_time = time.perf_counter()
                
                duration = getattr(self.game, "last_render_duration", 1.5)
                elapsed = time.perf_counter() - render_start_time
                progress = min(1.0, elapsed / max(0.1, duration))
                
                if progress >= 1.0: # Fallback to real progress if animation is done
                    progress = getattr(self.game, "world_map_render_progress", 1.0)

                bar_w = surf.get_width() * 0.8
                bar_h = 8
                bar_x = (surf.get_width() - bar_w) / 2
                bar_y = 10
                fill_w = bar_w * progress
                pygame.draw.rect(surf, (90, 60, 120), (bar_x, bar_y, fill_w, bar_h))
                pygame.draw.rect(surf, (80, 80, 95), (bar_x, bar_y, bar_w, bar_h), 1)
            else:
                render_start_time = None

            renderer._present()
            clock.tick(60)

        # pop map scene, resume dungeon
        manager.pop_scene()

    def _player_world_pos(self) -> tuple[int, int]:
        player = self.game._player()
        zx, zy, _ = self.game.zone
        gx = zx * self.game.cfg.world_width + player.pos[0]
        gy = zy * self.game.cfg.world_height + player.pos[1]
        return gx, gy

    def _world_to_map(self, wx: float, wy: float, size: tuple[int, int]) -> tuple[int, int]:
        # map world tile coords to full-map pixels
        total_w = self.game.cfg.world_map_screens * self.game.cfg.world_width
        total_h = self.game.cfg.world_map_screens * self.game.cfg.world_height
        px = int((wx / max(1, total_w)) * size[0])
        py = int((wy / max(1, total_h)) * size[1])
        return px, py

    def _build_cached_surface(
        self,
        renderer,
        *,
        show_corruption: bool = False,
        view_token: int = 0,
    ) -> pygame.Surface:
        size_key = (
            int(renderer.width),
            int(renderer.height),
            int(self.span),
            int(view_token),
            int(getattr(self.game, "corruption_version", 0) or 0),
        )

        if not hasattr(self.game, "world_map_cache_dict"):
            self.game.world_map_cache_dict = {}

        cached_data = self.game.world_map_cache_dict.get(size_key)
        if cached_data:
            if show_corruption and cached_data.get("surface_corr"):
                return cached_data["surface_corr"]
            return cached_data["surface"]

        # Start rendering lazily so opening the world map doesn't block the game loop.
        if not getattr(self.game, "world_map_rendering", False):
            try:
                self.game._start_world_map_thread(
                    reason="view",
                    width=int(renderer.width),
                    height=int(renderer.height),
                    span=int(self.span),
                    view=getattr(self.game, "world_map_view", None),
                    view_token=int(view_token),
                )
            except Exception:
                pass

        # If background render is running, show placeholder.
        if getattr(self.game, "world_map_rendering", False):
            surf = pygame.Surface(
                (min(640, renderer.width - 32), min(480, renderer.height - 32))
            )
            surf.fill((10, 10, 20))
            reason = getattr(self.game, "world_map_render_reason", "loading")
            if reason == "corruption":
                text = "Corruption reverberating..."
            else:
                text = (
                    "Generating world map..."
                    if not show_corruption
                    else "Generating corruption map..."
                )
            msg = renderer.big_label(text)
            surf.blit(
                msg,
                (
                    (surf.get_width() - msg.get_width()) // 2,
                    (surf.get_height() - msg.get_height()) // 2,
                ),
            )
            return surf

        # Fallback: render synchronously and cache (should be rare; kept for robustness).
        surf, view, surf_corr = self._render_overmap(renderer, view=getattr(self.game, "world_map_view", None))

        if not hasattr(self.game, "world_map_cache_dict"):
            self.game.world_map_cache_dict = {}
        self.game.world_map_cache_dict[size_key] = {"surface": surf, "surface_corr": surf_corr, "view": view, "key": size_key}

        self.game.world_map_ready = True
        return surf_corr if show_corruption else surf

    def _render_overmap(
        self,
        renderer,
        *,
        view: Optional[tuple[float, float, float, float]] = None,
    ) -> tuple[pygame.Surface, tuple[float, float, float, float], pygame.Surface]:
        """Render a Julia-based relief overmap (optionally for a sub-viewport of the world)."""
        # Render larger map (use most of the viewport with a small margin).
        target_w = max(640, renderer.width - 64)
        target_h = max(480, renderer.height - 180)

        cfg = self.game.cfg
        # Show the full world in a display-friendly resolution.
        total_w = cfg.world_map_screens * cfg.world_width
        total_h = cfg.world_map_screens * cfg.world_height
        world_max_wx = float(max(1, total_w - 1))
        world_max_wy = float(max(1, total_h - 1))
        render_scale = 1.0
        px_w = max(320, int(target_w * render_scale))
        px_h = max(240, int(target_h * render_scale))
        hi_surf = pygame.Surface((px_w, px_h))
        hi_surf_corr = pygame.Surface((px_w, px_h))

        # View window in world-tile coordinates (continuous); used for "infinite zoom".
        if view is None:
            view_min_wx, view_min_wy, view_span_wx, view_span_wy = (
                0.0,
                0.0,
                float(world_max_wx),
                float(world_max_wy),
            )
        else:
            view_min_wx, view_min_wy, view_span_wx, view_span_wy = (
                float(view[0]),
                float(view[1]),
                float(view[2]),
                float(view[3]),
            )

        # Clamp view window to world extents.
        view_span_wx = max(1e-9, min(view_span_wx, float(world_max_wx)))
        view_span_wy = max(1e-9, min(view_span_wy, float(world_max_wy)))
        view_min_wx = max(
            0.0, min(view_min_wx, max(0.0, float(world_max_wx) - view_span_wx))
        )
        view_min_wy = max(
            0.0, min(view_min_wy, max(0.0, float(world_max_wy) - view_span_wy))
        )

        # Calculate zoom factor to adjust level of detail.
        # Higher zoom (smaller span) results in more iterations.
        zoom_factor = max(1.0, float(world_max_wx) / max(1e-9, view_span_wx))
        iters = 64 + int(32 * math.log(max(1.0, zoom_factor)))
        iters = min(1024, iters)  # Cap iterations to prevent extreme render times

        # Legacy names used by the rest of the function.
        min_wx, min_wy = view_min_wx, view_min_wy
        span_x, span_y = view_span_wx, view_span_wy
        max_wx, max_wy = min_wx + span_x, min_wy + span_y

        p = getattr(self.game, "overmap_params", {}) or {}
        if all(
            k in p for k in ("view_min_jx", "view_max_jx", "view_min_jy", "view_max_jy", "visual_c")
        ):
            visual_c = p["visual_c"]
            j_min_x, j_max_x = p["view_min_jx"], p["view_max_jx"]
            j_min_y, j_max_y = p["view_min_jy"], p["view_max_jy"]
        else:
            entry = self._pick_visual_entry()
            visual_c = entry["c"]
            j_min_x, j_max_x = entry["x_min"], entry["x_max"]
            j_min_y, j_max_y = entry["y_min"], entry["y_max"]

        span_jx, span_jy = j_max_x - j_min_x, j_max_y - j_min_y
        corr_level = float(getattr(self.game, "corruption_level", 0.0) or 0.0)
        corr_seed = int(getattr(self.game, "corruption_seed", 1337) or 1337)
        corr_spline_weight = float(
            getattr(self.game, "corruption_spline_weight", 0.0) or 0.0
        )
        hotspots = list(getattr(self.game, "corruption_hotspots", []) or [])
        anchors = list(getattr(self.game, "corruption_anchors", []) or [])
        corr_params = CorruptionParams(
            seed=corr_seed,
            hotspots=hotspots,
            anchors=anchors,
            spline_weight=corr_spline_weight,
        )
        wx_line = [min_wx + (i / max(1, px_w - 1)) * span_x for i in range(px_w)]
        wy_line = [min_wy + (i / max(1, px_h - 1)) * span_y for i in range(px_h)]

        height_fn = mapgen._julia_height_norm_with_corruption
        classify_tile = mapgen._classify_tile
        glyph_to_idx = {"~": 0, ",": 1, ".": 2, "T": 3, "^": 4, "#": 5}
        show_corr = corr_level > 0.0

        dbg = getattr(self.game, "_debug", None)

        self.game.world_map_render_progress = 0.0

        if callable(dbg):

            dbg(

                "[world_map] render begin "

                f"reason={getattr(self.game, 'world_map_render_reason', 'loading')!s} "

                f"target={target_w}x{target_h} render={px_w}x{px_h} "

                f"world_tiles={total_w}x{total_h} iters={iters} "

                f"corr_level={corr_level:.3f} hotspots={len(hotspots)} anchors={len(anchors)}"

            )

        t0 = time.perf_counter()



        used_numpy = False

        try:

            from edgecaster.overmap_accel import render_overmap_buffers_numpy



            rgb_main, rgb_corr, _peak_env = render_overmap_buffers_numpy(

                px_w=px_w,

                px_h=px_h,

                total_w=total_w,

                total_h=total_h,

                j_min_x=j_min_x,

                j_max_x=j_max_x,

                j_min_y=j_min_y,

                j_max_y=j_max_y,

                view_min_wx=min_wx,

                view_min_wy=min_wy,

                view_span_wx=span_x,

                view_span_wy=span_y,

                visual_c=visual_c,

                iters=iters,

                corruption_level=corr_level,

                corruption_seed=corr_seed,

                spline_weight=corr_spline_weight,

                hotspots=hotspots,

                anchors=anchors,

            )

            hi_surf = pygame.surfarray.make_surface(rgb_main.swapaxes(0, 1))

            hi_surf_corr = pygame.surfarray.make_surface(rgb_corr.swapaxes(0, 1))

            used_numpy = True

            if callable(dbg):

                dbg(f"[world_map] numpy accel ok dt={time.perf_counter() - t0:.2f}s")

        except Exception as e:

            if callable(dbg):

                dbg(f"[world_map] numpy accel failed: {e!r}; falling back to python loop")



        if not used_numpy:

            if not show_corr:

                hi_surf_corr.fill((0, 0, 0))

            for py in range(px_h):

                self.game.world_map_render_progress = (py + 1) / px_h

                wy = float(wy_line[py])

                jy = float(j_min_y + (wy / float(world_max_wy)) * span_jy)

                for px in range(px_w):

                    wx = float(wx_line[px])

                    jx = float(j_min_x + (wx / float(world_max_wx)) * span_jx)

                    h_val, corr = height_fn(

                        jx,

                        jy,

                        visual_c,

                        scale=1.0,

                        iters=iters,

                        corruption_level=corr_level,

                        corruption_seed=corr_seed,

                        spline_weight=corr_spline_weight,

                        j_min_x=j_min_x,

                        j_max_x=j_max_x,

                        hotspots=hotspots,

                        anchors=anchors,

                    )

                    fields = {

                        "height": h_val,

                        "moisture": h_val,

                        "pattern": 0.0,

                        "corruption": corr,

                    }

                    glyph, _walk = classify_tile(fields, 0.5)

                    idx = glyph_to_idx.get(glyph, 2)

                    base = self._biome_color_by_index(idx)

                    hi_surf.set_at((px, py), base)

                    if show_corr:

                        dx, dy, env0 = distortion_dz(

                            jx,

                            jy,

                            params=corr_params,

                            j_min_x=j_min_x,

                            j_max_x=j_max_x,

                            corruption_level=corr_level,

                        )

                        env0 = max(0.0, min(1.0, float(env0)))

                        denom = max(

                            1e-6,

                            corr_params.amp

                            * max(0.15, corr_level if corr_level > 0 else 1.0),

                        )

                        dxn = max(-1.0, min(1.0, dx / denom))

                        dyn = max(-1.0, min(1.0, dy / denom))

                        r = int(255 * (0.5 + 0.5 * dxn) * env0)

                        b = int(255 * (0.5 + 0.5 * dyn) * env0)

                        g = int(255 * env0)

                        hi_surf_corr.set_at((px, py), (r, g, b))

                if callable(dbg) and (py % 40 == 0 or py == px_h - 1):

                    dt = time.perf_counter() - t0

                    pct = (py + 1) / max(1, px_h) * 100.0

                    dbg(f"[world_map] rows {py+1}/{px_h} ({pct:.1f}%) dt={dt:.1f}s")



        surf = pygame.transform.smoothscale(hi_surf, (target_w, target_h))

        surf_corr = pygame.transform.smoothscale(hi_surf_corr, (target_w, target_h))



        self.game.world_map_render_progress = 1.0
        dt = time.perf_counter() - t0
        self.game.last_render_duration = dt
        if callable(dbg):
            dbg(f"[world_map] render done dt={dt:.2f}s")
        view_min_wx, view_min_wy = min_wx, min_wy
        view_span_x, view_span_y = span_x, span_y
        view_max_wx, view_max_wy = max_wx, max_wy
        self.game.overmap_params = {
            "min_wx": view_min_wx,
            "min_wy": view_min_wy,
            "span_x": view_span_x,
            "span_y": view_span_y,
            "visual_c": visual_c,
            "corruption_level": float(getattr(self.game, "corruption_level", 0.0) or 0.0),
            "corruption_seed": int(getattr(self.game, "corruption_seed", 1337) or 1337),
            "corruption_hotspots": list(getattr(self.game, "corruption_hotspots", []) or []),
            "surface_size": (surf.get_width(), surf.get_height()),
            "surface": surf.copy(),
            "surface_corr": surf_corr.copy(),
            "orig_min_wx": min_wx,
            "orig_min_wy": min_wy,
            "orig_max_wx": max_wx,
            "orig_max_wy": max_wy,
            "view_max_wx": view_max_wx,
            "view_max_wy": view_max_wy,
            "orig_min_jx": j_min_x,
            "orig_max_jx": j_max_x,
            "orig_min_jy": j_min_y,
            "orig_max_jy": j_max_y,
            "view_min_jx": j_min_x,
            "view_max_jx": j_max_x,
            "view_min_jy": j_min_y,
            "view_max_jy": j_max_y,
        }
        if hasattr(self.game, "build_tile_julia_grid"):
            self.game.build_tile_julia_grid()
        return surf, (view_min_wx, view_min_wy, view_span_x, view_span_y), surf_corr

    def _biome_color_by_index(self, idx: int) -> tuple[int, int, int]:
        palette = [
            (70, 110, 200),
            (120, 170, 190),
            (150, 200, 120),
            (70, 150, 90),
            (170, 140, 100),
            (200, 200, 210),
        ]
        if 0 <= idx < len(palette):
            return palette[idx]
        return palette[2]

    def _glyph_index(self, glyph: str) -> int:
        order = ["~", ",", ".", "T", "^", "#"]
        try:
            return order.index(glyph)
        except ValueError:
            return 2

    def _julia_height(self, x: float, y: float, c: complex, scale: float = 1.0, iters: int = 80) -> float:
        zx = x * scale
        zy = y * scale
        it = 0
        while zx * zx + zy * zy <= 4.0 and it < iters:
            xt = zx * zx - zy * zy + c.real
            zy = 2 * zx * zy + c.imag
            zx = xt
            it += 1
        if it >= iters:
            return 0.0
        mod = math.sqrt(zx * zx + zy * zy)
        smooth = it + 1 - math.log(math.log(max(mod, 1e-6))) / math.log(2)
        return max(0.0, min(1.0, smooth / iters))

    def _load_c_path(self) -> List[Dict[str, float]]:
        """Load curated Julia parameters and bounds from tools/c_path.csv."""
        if self._c_path_cache is not None:
            return self._c_path_cache
        path_file = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "tools", "c_path.csv")
        )
        entries: List[Dict[str, float]] = []
        try:
            with open(path_file, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        re = float(row.get("re", row.get("real", row.get("c_real", 0.0))))
                        im = float(row.get("im", row.get("imag", row.get("c_imag", 0.0))))
                        x_min = float(row.get("x_min", -1.5))
                        x_max = float(row.get("x_max", 1.5))
                        y_min = float(row.get("y_min", -1.0))
                        y_max = float(row.get("y_max", 1.0))
                        entries.append(
                            {
                                "c": complex(re, im),
                                "x_min": x_min,
                                "x_max": x_max,
                                "y_min": y_min,
                                "y_max": y_max,
                            }
                        )
                    except (TypeError, ValueError):
                        continue
        except FileNotFoundError:
            entries = []
        self._c_path_cache = entries
        return entries

    def _pick_visual_entry(self) -> Dict[str, float]:
        """Deterministically pick a curated entry based on the fractal seed."""
        seed = getattr(self.game, "fractal_seed", 0) or 0
        rng = random.Random(seed)
        path = self._load_c_path()
        if path:
            return rng.choice(path)
        # fallback to legacy GOOD_C with default bounds
        c = self.GOOD_C[seed % len(self.GOOD_C)]
        return {"c": c, "x_min": -1.6, "x_max": 1.6, "y_min": -1.1, "y_max": 1.1}