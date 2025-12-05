from __future__ import annotations

import csv
import math
import os
import random
from typing import Optional, Tuple, List, Dict

import pygame

from edgecaster import mapgen
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

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F11:
                        renderer.toggle_fullscreen()
                        continue
                    if event.key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_SPACE, pygame.K_LESS, pygame.K_COMMA, pygame.K_PERIOD, pygame.K_GREATER):
                        running = False
                        break
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.game.world_map_ready:
                    map_w, map_h = map_surface.get_size()
                    mx, my = event.pos
                    ox = (renderer.width - map_w) // 2
                    oy = (renderer.height - map_h) // 2
                    rel_x = mx - ox
                    rel_y = my - oy
                    if 0 <= rel_x < map_w and 0 <= rel_y < map_h:
                        # convert to world tiles then zones
                        total_w = self.game.cfg.world_map_screens * self.game.cfg.world_width
                        total_h = self.game.cfg.world_map_screens * self.game.cfg.world_height
                        wx = int(rel_x / map_w * total_w)
                        wy = int(rel_y / map_h * total_h)
                        zx = wx // self.game.cfg.world_width
                        zy = wy // self.game.cfg.world_height
                        self.game.fast_travel_to_zone(zx, zy)
                        running = False
                        break

            # Draw map
            surf = renderer.surface
            surf.fill(renderer.bg)
            map_surface = self._build_cached_surface(renderer)
            ox = (renderer.width - map_surface.get_width()) // 2
            oy = (renderer.height - map_surface.get_height()) // 2
            surf.blit(map_surface, (ox, oy))

            # marker for player
            px, py = self._player_world_pos()
            marker = self._world_to_map(px, py, map_surface.get_size())
            pygame.draw.circle(surf, (255, 230, 120), (ox + marker[0], oy + marker[1]), 4)

            title = renderer.big_label("World Map")
            surf.blit(title, (ox, oy - 36))
            hint = renderer.small_font.render("Esc/Enter/< to return", True, renderer.fg)
            surf.blit(hint, (ox, oy + map_surface.get_height() + 8))

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

    def _build_cached_surface(self, renderer) -> pygame.Surface:
        size_key = (renderer.width, renderer.height, self.span)
        if self.game.world_map_cache:
            cached = self.game.world_map_cache
            if cached.get("key") == size_key:
                return cached["surface"]
        # If background render is running, show placeholder
        if getattr(self.game, "world_map_rendering", False):
            surf = pygame.Surface((min(640, renderer.width - 32), min(480, renderer.height - 32)))
            surf.fill((10, 10, 20))
            msg = renderer.big_label("Generating world map...")
            surf.blit(msg, ((surf.get_width() - msg.get_width()) // 2, (surf.get_height() - msg.get_height()) // 2))
            return surf
        # Otherwise, render synchronously and cache
        surf, view = self._render_overmap(renderer)
        self.game.world_map_cache = {"surface": surf, "view": view, "key": size_key}
        self.game.world_map_ready = True
        return surf

    def _render_overmap(self, renderer) -> tuple[pygame.Surface, tuple[float, float, float, float]]:
        """Render a Julia-based relief overmap using fixed bounds from the c_path entry."""
        target_w = min(1024, renderer.width - 32)
        target_h = min(720, renderer.height - 120)
        ss = 2
        px_w = max(1, target_w * ss)
        px_h = max(1, target_h * ss)
        hi_surf = pygame.Surface((px_w, px_h))
        field = self.game.fractal_field
        cfg = self.game.cfg
        # Show the full world: 0..(num_zones*zone_size)
        total_w = cfg.world_map_screens * cfg.world_width
        total_h = cfg.world_map_screens * cfg.world_height
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

        heights = [[0.0 for _ in range(px_w)] for _ in range(px_h)]
        glyph_idx = [[0 for _ in range(px_w)] for _ in range(px_h)]
        span_jx = j_max_x - j_min_x
        span_jy = j_max_y - j_min_y
        for py in range(px_h):
            wy = min_wy + (py / (px_h - 1)) * span_y
            jy = j_min_y + (py / (px_h - 1)) * span_jy
            for px in range(px_w):
                wx = min_wx + (px / (px_w - 1)) * span_x
                jx = j_min_x + (px / (px_w - 1)) * span_jx
                fields = field.sample_full(wx, wy)
                fields["height"] = self._julia_height(jx, jy, visual_c, scale=1.0, iters=96)
                glyph, _walk = mapgen._classify_tile(fields, 0.5)
                heights[py][px] = fields["height"]
                glyph_idx[py][px] = self._glyph_index(glyph)

        light = (0.65, -0.85)
        thresholds = (0.2, 0.35, 0.5, 0.7, 0.85)
        for py in range(px_h):
            for px in range(px_w):
                h = heights[py][px]
                base = self._biome_color_by_index(glyph_idx[py][px])
                if 0 < px < px_w - 1 and 0 < py < px_h - 1:
                    dhx = heights[py][px + 1] - heights[py][px - 1]
                    dhy = heights[py + 1][px] - heights[py - 1][px]
                    dot = -(dhx * light[0] + dhy * light[1])
                    shade = max(0.2, min(1.25, 0.55 + dot * 0.9))
                    spec = max(0.0, dot) ** 8
                    shade += spec * 0.8
                else:
                    shade = 1.0
                col = tuple(max(0, min(255, int(c * shade))) for c in base)
                hi_surf.set_at((px, py), col)

        contour_col = (30, 38, 48)
        for py in range(1, px_h - 1):
            for px in range(1, px_w - 1):
                h = heights[py][px]
                for t in thresholds:
                    if (h < t <= heights[py][px + 1]) or (h < t <= heights[py + 1][px]) or (h >= t > heights[py][px + 1]):
                        hi_surf.set_at((px, py), contour_col)
                        break

        surf = pygame.transform.smoothscale(hi_surf, (target_w, target_h))

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

        return surf, (view_min_wx, view_min_wy, view_span_x, view_span_y)

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
