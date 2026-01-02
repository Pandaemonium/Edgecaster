"""Overmap / zoomed-out LOD terrain rendering helpers.

These functions/classes exist to keep render/ascii.py lightweight.

Key properties:
- Grids are anchored to snapped world-tile boundaries (no 'swimming').
- Sampling goes through overmap_accel when available.
- Results are cached per (lod_id, cell size, cell coord, overmap signature).

This module is pygame-dependent (it produces pygame.Surface objects), but it is
renderer-implementation detail isolated away from the core ASCII renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import math
import pygame


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 == edge0:
        return 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def choose_lod_blend(
    *,
    tile_px: int,
    target_glyph_px: int = 18,
    radix: int = 2,
) -> Tuple[float, float, float, float, int, int]:
    """Pick two adjacent cell sizes (in tiles) and blend weights for crossfade.

    Returns:
      (cell0, cell1, a0, a1, lod0, lod1)

    Where:
      cell0 < cell1, lod0 uses cell0, lod1 uses cell1, and a0+a1 ~= 1.
    """
    tile_px = max(1, int(tile_px))
    target_glyph_px = max(4, int(target_glyph_px))
    radix = max(2, int(radix))

    # "How many tiles should collapse into 1 glyph" for this zoom.
    # Larger tile_px => fewer tiles per glyph (more detail).
    scale = float(target_glyph_px) / float(tile_px)

    # Convert to a cell-size in tiles. Then snap to radix^k.
    # Allow k to go negative so we can render 'micro' LODs where one tile splits
    # into many sub-tiles (cell size < 1 tile).
    ideal = float(scale) if scale > 0 else 1.0
    k = int(math.floor(math.log(ideal, radix))) if ideal > 0 else 0
    cell0 = float(radix ** k)
    cell1 = float(radix ** (k + 1))

    # Blend based on where 'ideal' lies between cell0 and cell1.
    if cell1 == cell0:
        a1 = 0.0
    else:
        a1 = clamp((ideal - float(cell0)) / float(cell1 - cell0), 0.0, 1.0)
    a0 = 1.0 - a1

    # A stable id so caches can separate the two layers.
    lod0 = int(math.log(cell0, radix)) if cell0 > 0 else 0
    lod1 = int(math.log(cell1, radix)) if cell1 > 0 else 0
    return cell0, cell1, float(a0), float(a1), lod0, lod1


def infer_world_center_from_renderer(
    *,
    game: object,
    map_rect: pygame.Rect,
    origin_px: Tuple[float, float],
    world_scale: float,
    zone_w: int,
    zone_h: int,
) -> Tuple[float, float]:
    """Infer absolute world tile coords of the map rect center.

    Used only when no game.camera exists (fallback). The renderer provides:
    - origin_px: where world tile (0,0) of the *current zone* lands in screen pixels.
    """
    cx_px = float(map_rect.centerx)
    cy_px = float(map_rect.centery)

    ox, oy = float(origin_px[0]), float(origin_px[1])
    dx_px = cx_px - ox
    dy_px = cy_px - oy

    s = max(1e-6, float(world_scale))
    local_wx = dx_px / s
    local_wy = dy_px / s

    try:
        zx, zy, _zz = getattr(game, "zone_coord", (0, 0, 0))
        zx = int(zx)
        zy = int(zy)
    except Exception:
        zx = zy = 0

    abs_wx = float(local_wx + zx * int(zone_w))
    abs_wy = float(local_wy + zy * int(zone_h))
    return abs_wx, abs_wy


def overmap_signature(game: object) -> Tuple:
    """A compact signature so cached glyphs invalidate when overmap params change."""

    def _round_f(x: object) -> float:
        try:
            return round(float(x), 6)
        except Exception:
            return 0.0

    p = getattr(game, "overmap_params", None) or {}
    try:
        hot = p.get("corruption_hotspots", None)
        if hot:
            hot_t = tuple(tuple(_round_f(v) for v in h) for h in hot)
        else:
            hot_t = tuple()
    except Exception:
        hot_t = tuple()

    try:
        anc = p.get("corruption_anchors", None)
        if anc:
            anc_t = tuple(tuple(_round_f(v) for v in a) for a in anc)
        else:
            anc_t = tuple()
    except Exception:
        anc_t = tuple()

    try:
        visual_c = complex(p.get("visual_c", -0.8 + 0.156j))
    except Exception:
        visual_c = complex(-0.8 + 0.156j)

    return (
        _round_f(p.get("view_min_jx", -2.0)),
        _round_f(p.get("view_max_jx", 2.0)),
        _round_f(p.get("view_min_jy", -2.0)),
        _round_f(p.get("view_max_jy", 2.0)),
        visual_c,
        _round_f(p.get("corruption_level", 0.0)),
        int(p.get("corruption_seed", 1337) or 1337),
        _round_f(p.get("corruption_spline_weight", 0.0)),
        hot_t,
        anc_t,
    )


@dataclass
class OvermapLodRenderer:
    """Renders a snapped LOD grid of overmap glyphs, with per-cell caching."""

    get_font: Callable[[int], pygame.font.Font]
    cache_max_cells: int = 250_000
    slots_x: int = 3
    slots_y: int = 3

    def __post_init__(self) -> None:
        self._cell_cache: Dict[Tuple, Tuple[str, Tuple[int, int, int]]] = {}
        self._rgb_cache: Dict[Tuple, object] = {}

    def clear(self) -> None:
        self._cell_cache.clear()
        self._rgb_cache.clear()

    def _trim_cache(self) -> None:
        max_cells = int(self.cache_max_cells or 0)
        if max_cells <= 0:
            return
        if len(self._cell_cache) <= max_cells:
            return
        drop_n = max(10_000, len(self._cell_cache) - max_cells)
        for _ in range(drop_n):
            try:
                self._cell_cache.pop(next(iter(self._cell_cache)))
            except Exception:
                break

    def render_lod_grid(
        self,
        *,
        game: object,
        map_rect: pygame.Rect,
        origin_px: Tuple[float, float],
        zone_w: int,
        zone_h: int,
        world_scale: float,
        cell_w_tiles: float,
        cell_h_tiles: float,
        lod_id: int,
        snap_base_tiles: Optional[int] = None,
        alpha: float = 1.0,
        surface_size: Optional[Tuple[int, int]] = None,
    ) -> pygame.Surface:
        """Render a rigid overmap LOD layer into a transparent surface."""
        rect_x, rect_y, rect_w, rect_h = int(map_rect.x), int(map_rect.y), int(map_rect.w), int(map_rect.h)

        w_surf, h_surf = surface_size if surface_size is not None else (rect_x + rect_w, rect_y + rect_h)
        out = pygame.Surface((int(w_surf), int(h_surf)), pygame.SRCALPHA)
        if rect_w <= 0 or rect_h <= 0:
            return out

        # Determine world center in absolute world-tile coordinates.
        cam = getattr(game, "camera", None)
        if cam is not None:
            abs_wx = float(getattr(cam, "world_x", 0.0))
            abs_wy = float(getattr(cam, "world_y", 0.0))
        else:
            abs_wx, abs_wy = infer_world_center_from_renderer(
                game=game, map_rect=map_rect, origin_px=origin_px, world_scale=world_scale, zone_w=zone_w, zone_h=zone_h
            )

        # Visible span in world tiles covered by on-screen map rect.
        world_scale = max(1e-6, float(world_scale))
        view_span_wx = float(rect_w) / world_scale
        view_span_wy = float(rect_h) / world_scale
        view_min_wx = abs_wx - view_span_wx * 0.5
        view_min_wy = abs_wy - view_span_wy * 0.5

        
        # Clamp the view window to world bounds. Without this, when panning against
        # world edges at extreme zoom levels, part of the viewport can fall out of
        # bounds and you'll see persistent 'black bars'.
        cfg = getattr(game, "cfg", None)
        total_w = total_h = 0
        if cfg is not None:
            try:
                total_w = int(cfg.world_map_screens * cfg.world_width)
                total_h = int(cfg.world_map_screens * cfg.world_height)
            except Exception:
                total_w = total_h = 0

        if total_w > 0:
            if view_span_wx >= float(total_w):
                view_min_wx = 0.0
            else:
                view_min_wx = max(0.0, min(view_min_wx, float(total_w) - view_span_wx))
        if total_h > 0:
            if view_span_wy >= float(total_h):
                view_min_wy = 0.0
            else:
                view_min_wy = max(0.0, min(view_min_wy, float(total_h) - view_span_wy))

        cell_w_tiles = float(cell_w_tiles) if cell_w_tiles else 1.0
        cell_h_tiles = float(cell_h_tiles) if cell_h_tiles else 1.0

        # Clamp effective cell size so each cell is at least 1px in screen space.
        # This prevents enormous loops when zooming so far in that cells would be sub-pixel.
        layout_cell_w_tiles = max(cell_w_tiles, 1.0 / world_scale)
        layout_cell_h_tiles = max(cell_h_tiles, 1.0 / world_scale)


        # Snap view_min to cell boundaries so the grid is rigid/stable.
        # For macro LODs (cell >= 1), snap to the cell size. For micro LODs (cell < 1),
        # snap to whole-tile boundaries so sub-tiles remain rigid within each tile.
        snap_w = float(snap_base_tiles) if snap_base_tiles else (cell_w_tiles if cell_w_tiles >= 1.0 else 1.0)
        snap_h = float(snap_base_tiles) if snap_base_tiles else (cell_h_tiles if cell_h_tiles >= 1.0 else 1.0)
        snap_min_wx = math.floor(view_min_wx / snap_w) * snap_w
        snap_min_wy = math.floor(view_min_wy / snap_h) * snap_h

        
        # Snap-min itself must be clamped as well. Even if view_min is clamped,
        # floor-snapping can push snap_min *backward* by up to one snap cell,
        # which may cause (snap_min + view_span) to exceed the world bounds at the
        # far edge. That manifests as bars that never fill at max X/Y.
        if total_w > 0:
            max_snap_min_wx = max(0.0, float(total_w) - view_span_wx)
            snap_min_wx = max(0.0, min(snap_min_wx, max_snap_min_wx))
        if total_h > 0:
            max_snap_min_wy = max(0.0, float(total_h) - view_span_wy)
            snap_min_wy = max(0.0, min(snap_min_wy, max_snap_min_wy))

        # Fractional scroll within the snapped cell grid (in pixels).
        offset_px_x = (view_min_wx - snap_min_wx) * world_scale
        offset_px_y = (view_min_wy - snap_min_wy) * world_scale

        # Extra safety margin (in cells) to absorb rounding at extreme zoom.
        PAD_CELLS = 2

        cols = max(
            1,
            int(math.ceil(view_span_wx / float(layout_cell_w_tiles))) + 3 + 2 * PAD_CELLS,
        )
        rows = max(
            1,
            int(math.ceil(view_span_wy / float(layout_cell_h_tiles))) + 3 + 2 * PAD_CELLS,
        )

        cell_px_w_f = max(1.0, float(layout_cell_w_tiles) * world_scale)
        cell_px_h_f = max(1.0, float(layout_cell_h_tiles) * world_scale)

        cell_px_w = max(1, int(round(cell_px_w_f)))
        cell_px_h = max(1, int(round(cell_px_h_f)))

        # Base cell coordinate in world-cell units (expanded by padding).
        base_cx = int(math.floor((snap_min_wx + 1e-9) / float(layout_cell_w_tiles))) - PAD_CELLS
        base_cy = int(math.floor((snap_min_wy + 1e-9) / float(layout_cell_h_tiles))) - PAD_CELLS

        # Pixel shift so the extra padded cells are actually drawn above/left of the viewport.
        pad_px_x = float(PAD_CELLS) * float(layout_cell_w_tiles) * float(world_scale)
        pad_px_y = float(PAD_CELLS) * float(layout_cell_h_tiles) * float(world_scale)



        sig = overmap_signature(game)

        # ---------------------------------------------------------------------
        # Accel path: render tiny rgb buffer for the visible window and quantize
        # ---------------------------------------------------------------------
        accel_ok = False
        rgb_main = None

        p = getattr(game, "overmap_params", None)
        sample_w = int(cols * max(1, int(self.slots_x)))
        sample_h = int(rows * max(1, int(self.slots_y)))

        # Absolute world size in tiles (global coordinates) for overmap_accel mapping.
        cfg = getattr(game, "cfg", None)
        world_w = world_h = 0
        if cfg is not None:
            try:
                world_w = int(cfg.world_map_screens * cfg.world_width)
                world_h = int(cfg.world_map_screens * cfg.world_height)
            except Exception:
                world_w = world_h = 0

        if p and sample_w > 1 and sample_h > 1 and world_w > 1 and world_h > 1:
            try:
                import numpy as np  # type: ignore
                from edgecaster.overmap_accel import render_overmap_buffers_numpy  # type: ignore

                # Cache key: the view window in world tiles and the overmap signature.
                key = (
                    int(sample_w),
                    int(sample_h),
                    int(lod_id),
                    int(base_cx),
                    int(base_cy),
                    sig,
                )
                cached = self._rgb_cache
                if key in cached:
                    rgb_main = cached[key]
                    accel_ok = True
                else:
                    # Iter budget: keep it interactive. Tie to zoom depth crudely.
                    zoom_factor = max(1.0, float(sample_w) / max(1e-9, view_span_wx))
                    iters = 48 + int(24 * math.log(max(1.0, zoom_factor)))
                    iters = int(max(24, min(iters, 320)))

                    rgb_main, _rgb_corr, _peak2 = render_overmap_buffers_numpy(
                        px_w=int(sample_w),
                        px_h=int(sample_h),
                        total_w=int(world_w),
                        total_h=int(world_h),
                        j_min_x=float(p["view_min_jx"]),
                        j_max_x=float(p["view_max_jx"]),
                        j_min_y=float(p["view_min_jy"]),
                        j_max_y=float(p["view_max_jy"]),
                        view_min_wx=float(snap_min_wx),
                        view_min_wy=float(snap_min_wy),
                        view_span_wx=float(view_span_wx),
                        view_span_wy=float(view_span_wy),
                        visual_c=p["visual_c"],
                        iters=int(iters),
                        corruption_level=float(p.get("corruption_level", 0.0) or 0.0),
                        corruption_seed=int(p.get("corruption_seed", 1337) or 1337),
                        corruption_spline_weight=float(p.get("corruption_spline_weight", 0.0) or 0.0),
                        hotspots=p.get("corruption_hotspots", None),
                        anchors=p.get("corruption_anchors", None),
                    )

                    cached[key] = rgb_main
                    if len(cached) > 32:
                        cached.pop(next(iter(cached)))

                    accel_ok = True
            except Exception:
                accel_ok = False
                rgb_main = None

        # ---------------------------------------------------------------------
        # Quantize RGB -> glyph + color (cheap + stable) and cache per cell
        # ---------------------------------------------------------------------
        if accel_ok and rgb_main is not None:
            try:
                import numpy as np  # type: ignore
                from edgecaster.biome import ANCHORS_RGB, BIOME_GLYPHS_NP  # type: ignore

                anchors_rgb = np.asarray(ANCHORS_RGB, dtype=np.int16)
                glyph_lut = BIOME_GLYPHS_NP.astype(np.int16, copy=False)

                rgb16 = rgb_main.astype(np.int16)
                dif = rgb16[:, :, None, :] - anchors_rgb[None, None, :, :]
                dist2 = (dif * dif).sum(axis=3)
                idx = dist2.argmin(axis=2).astype(np.int16)

                glyph_codes = glyph_lut[idx]  # (total_h, total_w)
                col = anchors_rgb[idx].astype(np.int16)
                col = np.clip(col + 28, 0, 255).astype(np.uint8)

                slots_x = max(1, int(self.slots_x))
                slots_y = max(1, int(self.slots_y))

                for r in range(rows):
                    for c in range(cols):
                        # aggregate subslots for this *cell*
                        x0 = c * slots_x
                        y0 = r * slots_y
                        block = glyph_codes[y0:y0 + slots_y, x0:x0 + slots_x].reshape(-1)
                        if block.size == 0:
                            continue

                        bc = np.bincount(block.astype(np.int32))
                        g = int(bc.argmax())
                        ch = chr(g)

                        col_block = col[y0:y0 + slots_y, x0:x0 + slots_x, :].reshape(-1, 3)
                        if col_block.size == 0:
                            continue
                        rr = int(col_block[:, 0].mean())
                        gg = int(col_block[:, 1].mean())
                        bb = int(col_block[:, 2].mean())
                        color = (rr, gg, bb)

                        cx = base_cx + c
                        cy = base_cy + r
                        k = (int(lod_id), int(cx), int(cy), sig)
                        self._cell_cache[k] = (ch, color)

                self._trim_cache()
            except Exception:
                pass

        # ---------------------------------------------------------------------
        # Draw cached cells
        # ---------------------------------------------------------------------
        font_px = max(8, min(256, int(min(cell_px_w, cell_px_h) * 0.90)))
        font = self.get_font(int(font_px))
        dim = (140, 140, 150)

        a = clamp(float(alpha), 0.0, 1.0)

        for rr in range(rows):
            cy = base_cy + rr
            py_f = (
                float(rect_y)
                + float(rr) * float(layout_cell_h_tiles) * float(world_scale)
                - float(offset_px_y)
                - float(pad_px_y)
            )
            if py_f + float(cell_px_h_f) < float(rect_y - cell_px_h) or py_f > float(rect_y + rect_h + cell_px_h):
                continue

            for cc in range(cols):
                cx = base_cx + cc
                px_f = (
                    float(rect_x)
                    + float(cc) * float(layout_cell_w_tiles) * float(world_scale)
                    - float(offset_px_x)
                    - float(pad_px_x)
                )
                if px_f + float(cell_px_w_f) < float(rect_x - cell_px_w) or px_f > float(rect_x + rect_w + cell_px_w):
                    continue

                px = int(round(px_f))
                py = int(round(py_f))

                k = (int(lod_id), int(cx), int(cy), sig)
                val = self._cell_cache.get(k)

                if not val:
                    # Fallback placeholder keeps the grid visible even if accel isn't ready.
                    surf = font.render("·", True, dim)
                else:
                    ch, color = val
                    surf = font.render(ch, True, color)

                if a < 0.999:
                    try:
                        surf = surf.copy()
                        surf.set_alpha(int(round(a * 255)))
                    except Exception:
                        pass

                gx = px + (cell_px_w - surf.get_width()) // 2
                gy = py + (cell_px_h - surf.get_height()) // 2
                out.blit(surf, (gx, gy))

        return out