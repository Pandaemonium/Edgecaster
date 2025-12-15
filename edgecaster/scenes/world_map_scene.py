from __future__ import annotations

import csv
import math
import os
import random
import time
from typing import Optional, Tuple, List, Dict

import pygame

from edgecaster import mapgen
from edgecaster.corruption import CorruptionParams, distortion_dz
from .base import Scene


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

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        clock = pygame.time.Clock()
        running = True

        # Temporary corruption slider (for tuning feel/scale). Removed once validated.
        slider_min = 0.0
        slider_max = 2.0
        pending_corruption = float(getattr(self.game, "corruption_level", 0.0) or 0.0)
        dragging_slider = False

        # Worldmap zoom (digital zoom of the rendered surface for now).
        zoom = 1.0
        view_min_wx = 0.0
        view_min_wy = 0.0
        # Cache the zoomed/cropped surface so we don't rescale every frame.
        zoomed_surface: Optional[pygame.Surface] = None
        zoomed_last_src_id: Optional[int] = None
        zoomed_last_crop: Optional[pygame.Rect] = None
        zoomed_last_show_corr: Optional[bool] = None

        # Ensure map_surface exists before the first event pump (mouse click safety).
        show_corr = bool(pygame.key.get_mods() & pygame.KMOD_ALT)
        map_surface = self._build_cached_surface(renderer, show_corruption=show_corr)

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
                        cur = float(getattr(self.game, "corruption_spline_weight", 0.0) or 0.0)
                        # Toggle between off and a sensible-on default for quick testing.
                        self.game.set_corruption_spline_weight(0.0 if cur > 1e-6 else 1.0)
                        continue
                    if event.key == pygame.K_0:
                        zoom = 1.0
                        view_min_wx = 0.0
                        view_min_wy = 0.0
                        zoomed_surface = None
                    if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE, pygame.K_LESS, pygame.K_COMMA, pygame.K_PERIOD, pygame.K_GREATER):
                        running = False
                        break

                # Mousewheel zoom (pygame 2.x)
                if event.type == pygame.MOUSEWHEEL:
                    if dragging_slider:
                        continue
                    if not self.game.world_map_ready:
                        continue

                    mx, my = renderer._to_surface(pygame.mouse.get_pos())
                    map_w, map_h = map_surface.get_size()
                    ox = (renderer.width - map_w) // 2
                    oy = (renderer.height - map_h) // 2
                    rel_x = mx - ox
                    rel_y = my - oy
                    if not (0 <= rel_x < map_w and 0 <= rel_y < map_h):
                        continue

                    total_w = float(self.game.cfg.world_map_screens * self.game.cfg.world_width)
                    total_h = float(self.game.cfg.world_map_screens * self.game.cfg.world_height)
                    if total_w <= 1 or total_h <= 1:
                        continue

                    old_zoom = float(zoom)
                    step = 1.15
                    if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                        step = 1.25
                    if event.y > 0:
                        zoom = min(8.0, zoom * (step ** event.y))
                    elif event.y < 0:
                        zoom = max(1.0, zoom / (step ** (-event.y)))

                    if abs(zoom - old_zoom) < 1e-9:
                        continue

                    old_span_wx = total_w / old_zoom
                    old_span_wy = total_h / old_zoom
                    wx_under = view_min_wx + (rel_x / max(1, map_w)) * old_span_wx
                    wy_under = view_min_wy + (rel_y / max(1, map_h)) * old_span_wy

                    new_span_wx = total_w / zoom
                    new_span_wy = total_h / zoom
                    view_min_wx = wx_under - (rel_x / max(1, map_w)) * new_span_wx
                    view_min_wy = wy_under - (rel_y / max(1, map_h)) * new_span_wy

                    # Clamp view window to world extents.
                    view_min_wx = max(0.0, min(view_min_wx, max(0.0, total_w - new_span_wx)))
                    view_min_wy = max(0.0, min(view_min_wy, max(0.0, total_h - new_span_wy)))
                    zoomed_surface = None

                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    # Convert display coords to render-surface coords to account for letterboxing/fullscreen.
                    mx, my = renderer._to_surface(event.pos)

                    # Slider drag starts here; do not fast-travel.
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
                            # convert to world tiles then zones
                            total_w = float(self.game.cfg.world_map_screens * self.game.cfg.world_width)
                            total_h = float(self.game.cfg.world_map_screens * self.game.cfg.world_height)
                            span_wx = total_w / max(1.0, float(zoom))
                            span_wy = total_h / max(1.0, float(zoom))
                            wx = int(view_min_wx + (rel_x / map_w) * span_wx)
                            wy = int(view_min_wy + (rel_y / map_h) * span_wy)
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

            # Draw map
            surf = renderer.surface
            surf.fill(renderer.bg)
            show_corr = bool(pygame.key.get_mods() & pygame.KMOD_ALT)
            map_surface = self._build_cached_surface(renderer, show_corruption=show_corr)

            map_w, map_h = map_surface.get_size()
            ox = (renderer.width - map_w) // 2
            oy = (renderer.height - map_h) // 2

            draw_surface = map_surface
            if self.game.world_map_ready and float(zoom) > 1.0001:
                total_w = float(self.game.cfg.world_map_screens * self.game.cfg.world_width)
                total_h = float(self.game.cfg.world_map_screens * self.game.cfg.world_height)
                span_wx = total_w / float(zoom)
                span_wy = total_h / float(zoom)

                crop_w = max(1, int(round(map_w / float(zoom))))
                crop_h = max(1, int(round(map_h / float(zoom))))
                crop_x = int(round((view_min_wx / max(1.0, total_w)) * map_w))
                crop_y = int(round((view_min_wy / max(1.0, total_h)) * map_h))
                crop_rect = pygame.Rect(crop_x, crop_y, crop_w, crop_h)
                crop_rect.clamp_ip(pygame.Rect(0, 0, map_w, map_h))

                src_id = id(map_surface)
                if (
                    zoomed_surface is None
                    or zoomed_last_src_id != src_id
                    or zoomed_last_show_corr != show_corr
                    or zoomed_last_crop != crop_rect
                ):
                    zoomed_last_src_id = src_id
                    zoomed_last_show_corr = show_corr
                    zoomed_last_crop = crop_rect.copy()
                    sub = map_surface.subsurface(crop_rect)
                    zoomed_surface = pygame.transform.smoothscale(sub, (map_w, map_h))

                draw_surface = zoomed_surface or map_surface

            surf.blit(draw_surface, (ox, oy))

            # Helper: world tile -> on-screen map pixel (supports zoomed view).
            def world_to_view(wx: float, wy: float) -> Optional[tuple[int, int]]:
                total_w = float(self.game.cfg.world_map_screens * self.game.cfg.world_width)
                total_h = float(self.game.cfg.world_map_screens * self.game.cfg.world_height)
                span_wx = total_w / max(1.0, float(zoom))
                span_wy = total_h / max(1.0, float(zoom))
                mx = int((float(wx) - view_min_wx) / max(1e-9, span_wx) * map_w)
                my = int((float(wy) - view_min_wy) / max(1e-9, span_wy) * map_h)
                if 0 <= mx < map_w and 0 <= my < map_h:
                    return (mx, my)
                return None

            # marker for player
            px, py = self._player_world_pos()
            marker = world_to_view(px, py)
            if marker is not None:
                pygame.draw.circle(surf, (255, 230, 120), (ox + marker[0], oy + marker[1]), 4)

            # marker for lab zone (if known)
            if hasattr(self.game, "lab_zone") and self.game.lab_zone:
                lab_zx, lab_zy = self.game.lab_zone
                lab_wx = lab_zx * self.game.cfg.world_width + self.game.cfg.world_width // 2
                lab_wy = lab_zy * self.game.cfg.world_height + self.game.cfg.world_height // 2
                lab_marker = world_to_view(lab_wx, lab_wy)
                if lab_marker is not None:
                    pygame.draw.circle(surf, (200, 120, 255), (ox + lab_marker[0], oy + lab_marker[1]), 4)

            # markers for POIs (e.g., Academy)
            if getattr(self.game, "poi_locations", None):
                for pid, (pz_x, pz_y, _pz_z) in self.game.poi_locations.items():
                    wx = pz_x * self.game.cfg.world_width + self.game.cfg.world_width // 2
                    wy = pz_y * self.game.cfg.world_height + self.game.cfg.world_height // 2
                    poi_marker = world_to_view(wx, wy)
                    if poi_marker is not None:
                        sx = ox + poi_marker[0]
                        sy = oy + poi_marker[1]
                        if str(pid).startswith("rune_anchor_"):
                            # Rune anchors: tiny cross marker.
                            color = (235, 235, 190)
                            pygame.draw.line(surf, color, (sx - 1, sy), (sx + 1, sy), 1)
                            pygame.draw.line(surf, color, (sx, sy - 1), (sx, sy + 1), 1)
                        else:
                            color = (120, 210, 240) if pid == "academy" else (180, 180, 200)
                            pygame.draw.circle(surf, color, (sx, sy), 3)

            # If corruption changed and we're regenerating the overmap, surface a distinct message.
            if getattr(self.game, "world_map_rendering", False) and getattr(self.game, "world_map_render_reason", "") == "corruption":
                msg = renderer.big_label("Corruption reverberating...")
                surf.blit(msg, (ox, max(4, oy - 72)))

            title = renderer.big_label("World Map")
            surf.blit(title, (ox, oy - 36))
            hint = renderer.small_font.render("Esc/Enter/< to return  |  Scroll to zoom (0 resets)", True, renderer.fg)
            surf.blit(hint, (ox, oy + map_surface.get_height() + 8))

            # --- Temporary corruption slider UI (tuning) ------------------------
            slider_val = pending_corruption if dragging_slider else float(getattr(self.game, "corruption_level", 0.0) or 0.0)
            slider_val = max(slider_min, min(slider_max, float(slider_val)))
            t = (slider_val - slider_min) / max(1e-9, (slider_max - slider_min))
            knob_x = slider_rect.x + int(t * slider_rect.w)

            pygame.draw.rect(surf, (30, 30, 40), slider_rect)
            pygame.draw.rect(surf, (80, 80, 95), slider_rect, 1)
            fill_rect = pygame.Rect(slider_rect.x, slider_rect.y, max(0, knob_x - slider_rect.x), slider_rect.h)
            pygame.draw.rect(surf, (90, 60, 120), fill_rect)

            knob_rect = pygame.Rect(0, 0, 10, slider_rect.h + 6)
            knob_rect.center = (knob_x, slider_rect.centery)
            pygame.draw.rect(surf, (200, 180, 220), knob_rect)
            pygame.draw.rect(surf, (40, 40, 50), knob_rect, 1)

            label = f"Corruption scale: {slider_val:.2f}  (ALT: view field)"
            text = renderer.small_font.render(label, True, renderer.fg)
            surf.blit(text, (slider_rect.x, slider_rect.y + slider_rect.h + 6))

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

    def _build_cached_surface(self, renderer, *, show_corruption: bool = False) -> pygame.Surface:
        size_key = (renderer.width, renderer.height, self.span)
        if self.game.world_map_cache:
            cached = self.game.world_map_cache
            if cached.get("key") == size_key:
                if show_corruption and cached.get("surface_corr") is not None:
                    return cached["surface_corr"]
                return cached["surface"]

        # Start rendering lazily so opening the world map doesn't block the game loop.
        if not getattr(self.game, "world_map_ready", False) and not getattr(self.game, "world_map_rendering", False):
            try:
                self.game._start_world_map_thread(reason="loading")
            except Exception:
                pass

        # If background render is running, show placeholder.
        if getattr(self.game, "world_map_rendering", False):
            surf = pygame.Surface((min(640, renderer.width - 32), min(480, renderer.height - 32)))
            surf.fill((10, 10, 20))
            reason = getattr(self.game, "world_map_render_reason", "loading")
            if reason == "corruption":
                text = "Corruption reverberating..."
            else:
                text = "Generating world map..." if not show_corruption else "Generating corruption map..."
            msg = renderer.big_label(text)
            surf.blit(msg, ((surf.get_width() - msg.get_width()) // 2, (surf.get_height() - msg.get_height()) // 2))
            return surf

        # Fallback: render synchronously and cache (should be rare; kept for robustness).
        surf, view, surf_corr = self._render_overmap(renderer)
        self.game.world_map_cache = {"surface": surf, "surface_corr": surf_corr, "view": view, "key": size_key}
        self.game.world_map_ready = True
        return surf_corr if show_corruption else surf

    def _render_overmap(self, renderer) -> tuple[pygame.Surface, tuple[float, float, float, float], pygame.Surface]:
        """Render a Julia-based relief overmap using fixed bounds from the c_path entry."""
        # Render larger map (use most of the viewport with a small margin).
        target_w = max(640, renderer.width - 64)
        target_h = max(480, renderer.height - 180)

        cfg = self.game.cfg
        # Show the full world in a display-friendly resolution.
        #
        # Note: cfg.world_map_screens can be very large (e.g. 100), so rendering at
        # world-tile resolution (screens*zone_size) is prohibitively expensive.
        # We instead render a lower-res map and sample the same world->Julia mapping
        # the locals use, keeping the two "intimately tied" while avoiding massive
        # render times.
        total_w = cfg.world_map_screens * cfg.world_width
        total_h = cfg.world_map_screens * cfg.world_height
        render_scale = 1.0
        px_w = max(320, int(target_w * render_scale))
        px_h = max(240, int(target_h * render_scale))
        hi_surf = pygame.Surface((px_w, px_h))
        hi_surf_corr = pygame.Surface((px_w, px_h))
        min_wx = 0.0
        min_wy = 0.0
        span_x = float(total_w)
        span_y = float(total_h)
        max_wx = min_wx + span_x
        max_wy = min_wy + span_y

        p = getattr(self.game, "overmap_params", {}) or {}
        if all(k in p for k in ("view_min_jx", "view_max_jx", "view_min_jy", "view_max_jy", "visual_c")):
            visual_c = p["visual_c"]
            j_min_x = p["view_min_jx"]
            j_max_x = p["view_max_jx"]
            j_min_y = p["view_min_jy"]
            j_max_y = p["view_max_jy"]
        else:
            entry = self._pick_visual_entry()
            visual_c = entry["c"]
            j_min_x = entry["x_min"]
            j_max_x = entry["x_max"]
            j_min_y = entry["y_min"]
            j_max_y = entry["y_max"]

        span_jx = j_max_x - j_min_x
        span_jy = j_max_y - j_min_y
        corr_level = float(getattr(self.game, "corruption_level", 0.0) or 0.0)
        corr_seed = int(getattr(self.game, "corruption_seed", 1337) or 1337)
        corr_spline_weight = float(getattr(self.game, "corruption_spline_weight", 0.0) or 0.0)
        hotspots = list(getattr(self.game, "corruption_hotspots", []) or [])
        anchors = list(getattr(self.game, "corruption_anchors", []) or [])
        corr_params = CorruptionParams(
            seed=corr_seed,
            hotspots=hotspots,
            anchors=anchors,
            spline_weight=corr_spline_weight,
        )
        # Map render pixels -> world tile indices -> Julia coords for consistent sampling.
        wx_map = [int(round((i / max(1, px_w - 1)) * max(0, total_w - 1))) for i in range(px_w)]
        wy_map = [int(round((i / max(1, px_h - 1)) * max(0, total_h - 1))) for i in range(px_h)]

        xgrid = ygrid = None
        grid = getattr(self.game, "tile_julia_grid", None)
        if isinstance(grid, dict) and isinstance(grid.get("x"), list) and isinstance(grid.get("y"), list):
            xgrid = grid.get("x") or None
            ygrid = grid.get("y") or None
            if xgrid is not None and len(xgrid) < total_w:
                xgrid = None
            if ygrid is not None and len(ygrid) < total_h:
                ygrid = None

        height_fn = mapgen._julia_height_norm_with_corruption  # type: ignore[attr-defined]
        classify_tile = mapgen._classify_tile  # type: ignore[attr-defined]
        glyph_to_idx = {"~": 0, ",": 1, ".": 2, "T": 3, "^": 4, "#": 5}
        show_corr = corr_level > 0.0

        dbg = getattr(self.game, "_debug", None)
        if callable(dbg):
            dbg(
                "[world_map] render begin "
                f"reason={getattr(self.game, 'world_map_render_reason', 'loading')!s} "
                f"target={target_w}x{target_h} render={px_w}x{px_h} "
                f"world_tiles={total_w}x{total_h} iters=64 "
                f"corr_level={corr_level:.3f} hotspots={len(hotspots)} anchors={len(anchors)}"
            )
        t0 = time.perf_counter()

        used_numpy = False
        try:
            # Optional fast path: numpy-accelerated Julia iteration across the whole overmap grid.
            # If numpy isn't available (or something fails), we fall back to the slower per-pixel loop.
            #
            # NOTE: Both paths use the same corruption rules from edgecaster.corruption
            # (distortion_dz / distortion_np), so this switch should affect performance only.
            from edgecaster.overmap_accel import render_overmap_buffers_numpy

            rgb_main, rgb_corr, _peak_env = render_overmap_buffers_numpy(
                px_w=px_w,
                px_h=px_h,
                wx_map=wx_map,
                wy_map=wy_map,
                xgrid=xgrid,
                ygrid=ygrid,
                total_w=total_w,
                total_h=total_h,
                j_min_x=j_min_x,
                j_max_x=j_max_x,
                j_min_y=j_min_y,
                j_max_y=j_max_y,
                visual_c=visual_c,
                iters=64,
                corruption_level=corr_level,
                corruption_seed=corr_seed,
                spline_weight=corr_spline_weight,
                hotspots=hotspots,
                anchors=anchors,
            )

            # pygame.surfarray expects (w, h, 3), while our buffer is (h, w, 3).
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
                wy = wy_map[py]
                if ygrid is not None:
                    jy = float(ygrid[wy])
                else:
                    jy = float(j_min_y + (wy / max(1, total_h - 1)) * span_jy)
                for px in range(px_w):
                    wx = wx_map[px]
                    if xgrid is not None:
                        jx = float(xgrid[wx])
                    else:
                        jx = float(j_min_x + (wx / max(1, total_w - 1)) * span_jx)

                    h_val, corr = height_fn(
                        jx,
                        jy,
                        visual_c,
                        scale=1.0,
                        iters=64,
                        corruption_level=corr_level,
                        corruption_seed=corr_seed,
                        spline_weight=corr_spline_weight,
                        j_min_x=j_min_x,
                        j_max_x=j_max_x,
                        hotspots=hotspots,
                        anchors=anchors,
                    )
                    fields = {"height": h_val, "moisture": h_val, "pattern": 0.0, "corruption": corr}
                    glyph, _walk = classify_tile(fields, 0.5)
                    idx = glyph_to_idx.get(glyph, 2)
                    base = self._biome_color_by_index(idx)
                    # Main map view intentionally does NOT visualize corruption directly.
                    # Hold ALT to see the distortion field view instead.
                    hi_surf.set_at((px, py), base)

                    # Corruption visualization (ALT view): encode real/imag distortion at z0 as R/B, magnitude as G.
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
                        denom = max(1e-6, corr_params.amp * max(0.15, corr_level if corr_level > 0 else 1.0))
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

        if callable(dbg):
            dt = time.perf_counter() - t0
            dbg(f"[world_map] render done dt={dt:.2f}s")

        view_min_wx = min_wx
        view_min_wy = min_wy
        view_span_x = span_x
        view_span_y = span_y
        view_max_wx = max_wx
        view_max_wy = max_wy

        # stash corners for locals/diagnostics
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
            "orig_min_wx": min_wx,
            "orig_min_wy": min_wy,
            "orig_max_wx": max_wx,
            "orig_max_wy": max_wy,
            "view_max_wx": view_max_wx,
            "view_max_wy": view_max_wy,
            # julia coords (inputs to _julia_height)
            "orig_min_jx": j_min_x,
            "orig_max_jx": j_max_x,
            "orig_min_jy": j_min_y,
            "orig_max_jy": j_max_y,
            # view julia coords (same as orig because no crop)
            "view_min_jx": j_min_x,
            "view_max_jx": j_max_x,
            "view_min_jy": j_min_y,
            "view_max_jy": j_max_y,
        }
        # build per-tile Julia grid for the whole world using these extents
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
