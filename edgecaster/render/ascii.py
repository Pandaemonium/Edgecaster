"""Pygame-based ASCII-style renderer with ability bar and targeting."""
import pygame
import math
import random
from pathlib import Path
from typing import Tuple, List, Dict


from edgecaster.game import Game
from edgecaster.state.world import World
from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns.library import action_preview_geometry
from edgecaster.ui.ability_bar import AbilityBarRenderer, AbilityBarWidget
from edgecaster.visuals import VisualProfile, apply_visual_panel
from edgecaster.ui.widgets import WidgetContext, HUDWidget
from edgecaster.ui.status_header import StatusHeaderWidget
from edgecaster.visual_effects import (
    VisualEffectManager,
    build_visual_profile,
    apply_surface_overlays,
    apply_screen_tint_overlays,   # <-- add this
    apply_entity_color_effects,
    concat_effect_names,
    effect_names_from_obj,
    compute_overlay_union_rect
)



class AsciiRenderer:
    def __init__(self, width: int, height: int, tile: int) -> None:
        # refactor: constructor currently owns UI/interaction state; migrate ability/target/config/lorenz state
        # into a scene-level UIState and keep renderer draw-only.
        pygame.init()
        self.width = width
        self.height = height
        self.base_tile = tile
        self.zoom = 1.0
        self.tile = tile
        self.origin_x = 0
        self.origin_y = 0
        self.pan_x = 0.0
        self.pan_y = 0.0
        # Global-zone glyph density tuning (for local->global zoom blend).
        # More slots => less dead space inside each global zone-cell.
        # You can tweak these live in a debug console if you expose them.
        self.zone_grid_slots = (9, 6)  # was (3, 2)
        self.zone_grid_fill = 1.05
        self.zone_grid_fill_x = 0.95
        self.zone_grid_fill_y = 1.50     # let glyphs run slightly "hot" to reduce gaps
        self.surface_flags = pygame.RESIZABLE

        self.visual_fx = VisualEffectManager()

        # Logical render surface stays at the configured resolution.
        self.surface = pygame.Surface((width, height))
        self.fullscreen = False

        # Clamp the initial *windowed* size to the current desktop resolution so
        # we never open a window larger than the screen.
        info = pygame.display.Info()
        max_w, max_h = info.current_w, info.current_h
        win_w = min(width, max_w)
        win_h = min(height, max_h)
        self.windowed_size = (win_w, win_h)

        self.display = pygame.display.set_mode(self.windowed_size, self.surface_flags)

        self.lb_off = (0, 0)  # letterbox offset when centering
        self.lb_scale = 1.0   # letterbox scale factor

        pygame.display.set_caption("Edgecaster (ASCII prototype)")
        self.font = pygame.font.SysFont("consolas", self.base_tile)  # UI font (fixed)
        self.map_font = pygame.font.SysFont("consolas", self.tile)  # map glyphs, scales with zoom
        self.small_font = pygame.font.SysFont("consolas", 16)
        # --- Menu fonts (chunkier defaults for widget-driven menus/popups) ---
        # Keep the log / HUD using small_font; only widgets will prefer menu_font.
        menu_size = int(round(self.base_tile * 1.15))  # e.g. 16 -> 18
        menu_size = max(16, min(26, menu_size))
        self.menu_font = pygame.font.SysFont("consolas", menu_size, bold=True)

        # Optional: a slightly bigger title font for headers (used by ScaledLabelWidget etc. if you want)
        title_size = int(round(menu_size * 1.15))  # e.g. 18 -> 21
        title_size = max(menu_size, min(32, title_size))
        self.menu_title_font = pygame.font.SysFont("consolas", title_size, bold=True)
        self.bg = (10, 10, 20)
        self.fg = (220, 230, 240)
        self.dim = (120, 130, 150)
        self.sel = (255, 230, 120)
        self.player_color = (255, 210, 80)
        self.monster_color = (255, 120, 120)
        self.rune_color = (90, 200, 255)
        self.pattern_color = (80, 180, 240)
        self.pattern_color_end = (180, 255, 220)
        self.edge_width_base = 1
        self.vertex_base_radius = 2
        self.hp_color = (200, 80, 80)
        self.mana_color = (90, 160, 255)
        # Resolve project root (one level above the edgecaster package)
        self._project_root = Path(__file__).resolve().parents[2]
        # Currency icon
        try:
            icon_path = self._project_root / "assets" / "icons" / "bismuth.png"
            self.bismuth_icon = pygame.image.load(str(icon_path)).convert_alpha()
            self.bismuth_icon_hud = pygame.transform.smoothscale(self.bismuth_icon, (24, 24))
            # map-sized icon scaled to current tile size
            self.bismuth_icon_map = pygame.transform.smoothscale(self.bismuth_icon, (self.tile, self.tile))
        except Exception:
            self.bismuth_icon = None
            self.bismuth_icon_hud = None
            self.bismuth_icon_map = None
        self.bar_bg = (40, 40, 60)
        self.ability_bar_height = 72
        self.top_bar_height = 64
        self.log_panel_width = 320
        self.ability_bar_view = AbilityBarRenderer()

        # transient flash message
        self.flash_text: str | None = None
        self.flash_color: Tuple[int, int, int] = (255, 120, 120)
        self.flash_until_ms: int = 0
        # pattern layers
        self.edges_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        self.verts_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        # Lorenz attractor overlay
        self.lorenz_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        # Lorenz view defaults; simulation state now lives in game/lorenz.py
        self.lorenz_scale = 0.18          # maps x,y to tile offsets
        self.lorenz_radius_tiles = 7      # max aura radius in tiles
        # cached glow sprites: key = (radius_px, col

        # cached glow sprites: key = (radius_px, color_tuple)
        self.glow_cache: Dict[Tuple[int, Tuple[int, int, int]], pygame.Surface] = {}
        self.quit_requested = False
        self.pause_requested = False   # NEW: used by DungeonScene to decide on pause
        self.lorenz_center_x: float | None = None
        self.lorenz_center_y: float | None = None
        # Log scroll offset (pixels)
        self.log_scroll_offset: float = 0.0
        self.lorenz_follow = 0.25  # 0 = frozen, 1 = glued to player
        # Phase-space view center (for how we frame the attractor itself)
        self.lorenz_view_cx = 0.0
        self.lorenz_view_cy = 0.0
        self.lorenz_view_initialized = False
        self.lorenz_view_smoothing = 0.15  # how fast we chase the cloud's centroid
        # Afterimage: a semi-transparent black surface to slowly fade old butterflies
        self.lorenz_fade_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        # RGBA: (0, 0, 0, alpha). Higher alpha = faster fade.
        self.lorenz_fade_surface.fill((0, 0, 0, 30))
        # Short history of recent projected positions for tapered trails.
        # Each entry = one frame's list of (cx_px, cy_px, z)
        self.lorenz_trail_frames: list[list[tuple[int, int, float]]] = []
        self.lorenz_trail_max_frames = 3  # keep trails very short
        # Optional world-level visual profile (e.g. curses/blessings).
        # SceneManager can set this via set_global_visual_profile().
        self.global_visual_profile: VisualProfile | None = None
        # Root HUD widget composed of status, log, and ability bar.
        # This will later be replaced or extended by a richer widget tree,
        # but for now it's a thin wrapper around existing draw_* methods.
        self.hud_widget = HUDWidget()

        # Cache for dynamic *map* fonts used by LOD/global layers.
        # IMPORTANT: this cache must match the styling of `self.map_font`.
        # Previously, the LOD layers used bold fonts which made the global glyphs
        # look like a different tileset.
        self._map_font_cache: Dict[int, pygame.font.Font] = {}





    # UI state access (scene-owned view-model preferred)                 #
    # ------------------------------------------------------------------ #


    def handle_resize(self, width: int, height: int) -> None:
        """
        Handle a window resize event.

        We keep the logical render surface (self.surface) at the original
        width/height and only resize the display window. _present() already
        uses display.get_size() to compute letterboxing, so this is enough.
        """
        self.display = pygame.display.set_mode((width, height), self.surface_flags)
        # _present() will recompute lb_off / lb_scale on the next frame.




    def _ui_attr(self, name: str, default=None):
        """Prefer a scene-provided ui_state if attached; fallback to renderer fields."""
        ui = getattr(self, "ui_state", None)
        if ui is not None and hasattr(ui, name):
            return getattr(ui, name, default)
        return getattr(self, name, default)

    def _to_surface(self, pos: Tuple[int, int]) -> Tuple[int, int]:
        """Convert display-space mouse coords to surface-space, accounting for letterbox and scale."""
        return (
            int((pos[0] - self.lb_off[0]) / max(1e-6, self.lb_scale)),
            int((pos[1] - self.lb_off[1]) / max(1e-6, self.lb_scale)),
        )

    def set_global_visual_effects(self, names: list[str] | None) -> None:
        self.visual_fx.set_global_effects(names or [])

    def clear_global_visual_effects(self) -> None:
        self.visual_fx.set_global_effects([])

    def present(self) -> None:
        self._present()

    def toggle_fullscreen(self) -> None:
        flags = self.display.get_flags()
        if flags & pygame.FULLSCREEN:
            # Leaving fullscreen → go back to windowed, but clamp to desktop.
            info = pygame.display.Info()
            max_w, max_h = info.current_w, info.current_h
            win_w = min(self.windowed_size[0], max_w)
            win_h = min(self.windowed_size[1], max_h)
            self.windowed_size = (win_w, win_h)
            self.display = pygame.display.set_mode(self.windowed_size, self.surface_flags)
            self.fullscreen = False
        else:
            # Entering fullscreen → remember current windowed size.
            self.windowed_size = self.display.get_size()
            self.display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            self.fullscreen = True


    def is_fullscreen(self) -> bool:
        return self.fullscreen

    def set_global_visual_profile(self, profile: VisualProfile | None) -> None:
        """
        Set or clear a global visual profile affecting the entire game view.
        For example, a 'cursed' world might flip everything horizontally.
        """
        self.global_visual_profile = profile



    def _map_origin_base(self) -> Tuple[int, int]:
        # Base origin before pan
        map_origin_x = 8
        map_origin_y = self.top_bar_height + 8 + int(self.tile * 4)
        return map_origin_x, map_origin_y


    def pan_by_px(self, dx: float, dy: float) -> None:
        """Pan the camera by a pixel delta (logical surface pixels).

        Used for right-mouse drag panning. Positive dx/dy shifts the map origin
        right/down (i.e., the world appears to move with the mouse).
        """
        self.pan_x += float(dx)
        self.pan_y += float(dy)

    def center_camera_on_player(self, game: Game, *, snap_zoom: bool = False) -> None:
        """Center the camera on the player in the local map view.

        This is a safety valve for when panning/zooming gets lost. If snap_zoom is
        True, zoom is reset to 1.0 (and tile/font scale is rebuilt).
        """
        try:
            player = game.actors[game.player_id]
            px, py = player.pos
        except Exception:
            return

        if snap_zoom:
            self.zoom = 1.0
            self.tile = max(8, int(round(self.base_tile * self.zoom)))
            self.map_font = pygame.font.SysFont("consolas", self.tile)

        map_origin_x, map_origin_y = self._map_origin_base()
        map_w = self.width - self.log_panel_width
        map_h = self.height - self.ability_bar_height - map_origin_y
        # Center of drawable map area (excluding log/bars)
        cx = map_origin_x + map_w // 2
        cy = map_origin_y + map_h // 2

        # origin_x = map_origin_x + pan_x; want px*tile + origin_x = cx
        self.pan_x = float(cx - map_origin_x - px * self.tile)
        self.pan_y = float(cy - map_origin_y - py * self.tile)

    def reset_camera(self, game: Game) -> None:
        """Hard-reset pan/zoom to sane defaults and center on player."""
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom = 1.0
        self.tile = max(8, int(round(self.base_tile * self.zoom)))
        self.map_font = pygame.font.SysFont("consolas", self.tile)
        self.center_camera_on_player(game, snap_zoom=False)


    def draw_world(self, game: Game, world: World) -> None:
        """Draw the local map view.

        For now we render the current zone plus the 8 surrounding adjacent zones
        (a 3x3 neighborhood). This is the minimum we expect to see when zooming
        out before higher-level LOD kicks in.
        """
        self.draw_world_local(game)

    def draw_overworld_zonegrid(self, game: Game, world: World) -> None:
        """Coarse overworld rendering (placeholder).

        Currently unused by the dungeon scene; kept for future LOD work.
        """
        # clear main surface
        self.surface.fill(self.bg)

        map_origin_x, map_origin_y = self._map_origin_base()
        map_w = self.width - self.log_panel_width
        map_h = self.height - self.ability_bar_height

        cx = map_origin_x + map_w // 2
        cy = map_origin_y + map_h // 2

        zx, zy, zz = game.zone_coord

        cols = max(1, map_w // self.tile + 3)
        rows = max(1, map_h // self.tile + 3)
        half_c = cols // 2
        half_r = rows // 2

        grid = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        for dy in range(-half_r, half_r + 1):
            for dx in range(-half_c, half_c + 1):
                tzx = zx + dx
                tzy = zy + dy
                # Bounds check: ensure the represented block overlaps the world bounds.
                if tzx + lod_step <= 0 or tzy + lod_step <= 0 or tzx >= game.cfg.world_map_screens or tzy >= game.cfg.world_map_screens:
                    continue

                px = cx + dx * self.tile + int(self.pan_x)
                py = cy + dy * self.tile + int(self.pan_y)

                if px < map_origin_x - self.tile or py < map_origin_y - self.tile:
                    continue
                if px >= map_origin_x + map_w or py >= map_origin_y + map_h:
                    continue

                col = (230, 230, 230) if (dx == 0 and dy == 0) else (150, 150, 150)
                text = self.map_font.render("X", True, col)
                grid.blit(text, (px, py))

        grid.set_alpha(255)
        self.surface.blit(grid, (0, 0))

    def draw_world_local(self, game: Game) -> None:
        """Draw a 3x3 neighborhood of zones around the current zone.

        Important: we keep the current-zone coordinates unshifted so that entity
        rendering (which uses current-zone tile coords) stays correct.
        """
        self.surface.fill(self.bg)

        map_origin_x, map_origin_y = self._map_origin_base()
        map_w = self.width - self.log_panel_width
        map_h = self.height - self.ability_bar_height - map_origin_y
        map_rect = pygame.Rect(map_origin_x, map_origin_y, map_w, map_h)

        # apply pan offsets (set by zoom/pan)
        self.origin_x = map_origin_x + self.pan_x
        self.origin_y = map_origin_y + self.pan_y

        palette = {
            "~": (90, 130, 255),   # water
            ",": (190, 170, 120),  # shore
            ".": (140, 200, 140),  # grass
            "T": (60, 140, 80),    # trees
            "^": (170, 140, 100),  # hills
            "#": (180, 180, 190),  # mountains/walls
        }

        zx, zy, zz = getattr(game, "zone_coord", (0, 0, 0))

        # Zone dimensions (assumed consistent across zones)
        try:
            zone_w = int(game.world.width)
            zone_h = int(game.world.height)
        except Exception:
            zone_w = 0
            zone_h = 0

        debug_no_fog = bool(getattr(game, "debug_no_fog", False))

        # Helper to fetch a zone without mutating current-level state.
        get_zone_for_render = getattr(game, "get_zone_for_render", None)

        for dz_y in (-1, 0, 1):
            for dz_x in (-1, 0, 1):
                # Neighboring zones around the current zone (3x3).
                tzx = zx + dz_x
                tzy = zy + dz_y

                # Optional bounds check against world size (0-based indices).
                try:
                    screens = int(getattr(game.cfg, "world_map_screens", 0) or 0)
                except Exception:
                    screens = 0
                if screens > 0:
                    if tzx < 0 or tzy < 0 or tzx >= screens or tzy >= screens:
                        continue

                coord = (tzx, tzy, zz)

                lvl = None
                try:
                    if callable(get_zone_for_render):
                        lvl = get_zone_for_render(coord)
                    else:
                        lvl = game.levels.get(coord)
                except Exception:
                    lvl = game.levels.get(coord)

                if lvl is None or not hasattr(lvl, "world"):
                    continue

                w = lvl.world
                off_x = dz_x * zone_w
                off_y = dz_y * zone_h

                for y in range(w.height):

                    py = (y + off_y) * self.tile + self.origin_y
                    # quick vertical reject
                    if py < map_rect.top - self.tile or py >= map_rect.bottom + self.tile:
                        continue

                    row = w.tiles[y]
                    for x in range(w.width):
                        tile = row[x]

                        if not debug_no_fog:
                            if not tile.explored and not tile.visible:
                                continue

                        base_col = tile.tint if getattr(tile, "tint", None) else palette.get(tile.glyph, self.fg)
                        if base_col is None:
                            base_col = self.fg
                        if len(base_col) >= 3:
                            base_col = tuple(max(0, min(255, int(base_col[i]))) for i in range(3))
                        else:
                            base_col = self.fg

                        if debug_no_fog or tile.visible:
                            color = base_col
                        else:
                            color = tuple(max(0, int(c * 0.5)) for c in base_col)

                        px = (x + off_x) * self.tile + self.origin_x
                        if px < map_rect.left - self.tile or px >= map_rect.right + self.tile:
                            continue

                        text = self.map_font.render(tile.glyph, True, color)
                        self.surface.blit(text, (px, py))


    
    def _smoothstep(self, a: float, b: float, x: float) -> float:
        """Smoothly interpolate from 0..1 as x moves from a..b."""
        if a == b:
            return 1.0 if x >= b else 0.0
        t = (x - a) / (b - a)
        t = max(0.0, min(1.0, t))
        return t * t * (3.0 - 2.0 * t)


    def draw_world_zoomed(self, game: Game) -> None:
        """Draw geography using an arbitrary power-of-two LOD stack.

        Philosophical rule: LoD 0 is *not special*.
        Every LOD renders the same way: a rigid grid aligned to world-tile coordinates,
        sampled via the fast numpy overmap renderer and cached per cell.

        At any moment we render at most two adjacent LOD layers and cross-fade between them.
        Entities are drawn separately (and can later pick their own native LOD).
        """
        import math

        # Always clear first to avoid trails while blending.
        self.surface.fill(self.bg)

        # Use the SAME map rect / origin logic as draw_world_local so the camera/pan math
        # stays consistent at every LOD.
        map_origin_x, map_origin_y = self._map_origin_base()
        map_w = self.width - self.log_panel_width
        map_h = self.height - self.ability_bar_height - map_origin_y
        map_rect = pygame.Rect(map_origin_x, map_origin_y, max(1, map_w), max(1, map_h))

        # Apply pan offsets (set by zoom/pan). This makes _render_lod_grid's inference stable.
        self.origin_x = map_origin_x + self.pan_x
        self.origin_y = map_origin_y + self.pan_y

        # World-tile -> screen pixels for a single world tile.
        world_scale = float(self.base_tile) * float(self.zoom)

        # ---------------------------------------------------------------------
        # Choose the two adjacent LODs to render.
        #
        # Each LOD is a cell size in world tiles: 1, 2, 4, 8, ...
        # We want cell_px ~= target_glyph_px, so cell_tiles ~= target / world_scale.
        # ---------------------------------------------------------------------
        target_glyph_px = float(getattr(self, "lod_target_glyph_px", self.base_tile) or self.base_tile)
        raw = target_glyph_px / max(1e-6, world_scale)  # desired cell size in tiles
        raw = max(1.0, raw)

        LOD_RADIX = getattr(self, "lod_radix", 2)
        lod_f = math.log(raw, LOD_RADIX)

        lod0 = int(math.floor(lod_f))
        lod1 = lod0 + 1
        frac = float(lod_f - lod0)  # 0..1

        # Clamp LOD range for now (we can go negative later for "micro" LODs).
        lod0 = max(0, min(lod0, 12))
        lod1 = max(0, min(lod1, 12))
        if lod1 == lod0:
            frac = 0.0

        cell0 = int(LOD_RADIX ** lod0)
        cell1 = int(LOD_RADIX ** lod1)

        snap_base = max(cell0, cell1)  # anchor both blended layers to the same snapped origin

        # Smooth the blend so it's not too "linear" around the midpoint.
        blend = self._smoothstep(0.0, 1.0, frac)

        # Small deadband: when we're effectively at one LOD, don't spend time on the other.
        eps = 0.06
        if blend <= eps:
            a0, a1 = 1.0, 0.0
        elif blend >= 1.0 - eps:
            a0, a1 = 0.0, 1.0
        else:
            a0, a1 = 1.0 - blend, blend

        # Entity fading (for now): tie to LoD 0's opacity when LoD 0 is one of the two layers.
        if cell0 == 1:
            self._local_fade_alpha = int(round(255 * a0))
        elif cell1 == 1:
            self._local_fade_alpha = int(round(255 * a1))
        else:
            self._local_fade_alpha = 0

        zone_w = int(getattr(game.cfg, "world_width", getattr(game.world, "width", 60) or 60))
        zone_h = int(getattr(game.cfg, "world_height", getattr(game.world, "height", 40) or 40))

        # ---------------------------------------------------------------------
        # Render up to two layers. (Terrain only.)
        # ---------------------------------------------------------------------
        if a0 > 0.0:
            grid0 = self._render_lod_grid(
                game,
                map_rect=map_rect,
                zone_w=zone_w,
                zone_h=zone_h,
                world_scale=world_scale,
                cell_w_tiles=cell0,
                cell_h_tiles=cell0,
                lod_id=lod0,
                snap_base_tiles=snap_base,
            )
            grid0.set_alpha(int(round(255 * a0)))
            self.surface.blit(grid0, (0, 0))

        if a1 > 0.0 and cell1 != cell0:
            grid1 = self._render_lod_grid(
                game,
                map_rect=map_rect,
                zone_w=zone_w,
                zone_h=zone_h,
                world_scale=world_scale,
                cell_w_tiles=cell1,
                cell_h_tiles=cell1,
                lod_id=lod1,
                snap_base_tiles=snap_base,
            )
            grid1.set_alpha(int(round(255 * a1)))
            self.surface.blit(grid1, (0, 0))

    def _infer_world_center_from_renderer(self, game, map_rect, world_scale, zone_w, zone_h):
        """
        Infer ABSOLUTE world tile coordinates (global) from renderer pan/origin,
        even when no game.camera exists.

        We convert screen center -> local-tile coords, then add zone offset.
        """
        cx_px = map_rect.centerx
        cy_px = map_rect.centery

        dx_px = cx_px - self.origin_x
        dy_px = cy_px - self.origin_y

        local_wx = dx_px / max(1e-6, world_scale)
        local_wy = dy_px / max(1e-6, world_scale)

        zx, zy, _zz = getattr(game, "zone_coord", (0, 0, 0))
        abs_wx = zx * zone_w + local_wx
        abs_wy = zy * zone_h + local_wy
        return float(abs_wx), float(abs_wy)

    def _overmap_signature(self, game) -> tuple:
        """Hashable signature for overmap sampling parameters (for cache keys)."""
        p = getattr(game, "overmap_params", None) or {}

        # Keep it simple + stable: only include knobs that affect the sampled appearance.
        # (If you add more knobs later, include them here and the cache will self-invalidate.)
        def _round_f(x, q=1e-5):
            try:
                return float(round(float(x) / q) * q)
            except Exception:
                return 0.0

        hot = p.get("corruption_hotspots", None) or []
        anc = p.get("corruption_anchors", None) or []

        # Convert to tuples with rounding so the signature is hashable + stable.
        hot_t = tuple(tuple(_round_f(v) for v in h) for h in hot)
        anc_t = tuple(tuple(_round_f(v) for v in a) for a in anc)

        return (
            _round_f(p.get("view_min_jx", -2.0)),
            _round_f(p.get("view_max_jx", 2.0)),
            _round_f(p.get("view_min_jy", -2.0)),
            _round_f(p.get("view_max_jy", 2.0)),
            complex(p.get("visual_c", -0.8 + 0.156j)),
            _round_f(p.get("corruption_level", 0.0)),
            int(p.get("corruption_seed", 1337) or 1337),
            _round_f(p.get("corruption_spline_weight", 0.0)),
            hot_t,
            anc_t,
        )

    def _render_lod_grid(
        self,
        game,
        *,
        map_rect: "pygame.Rect",
        zone_w: int,
        zone_h: int,
        world_scale: float,
        cell_w_tiles: int,
        cell_h_tiles: int,
        lod_id: str,
        snap_base_tiles: int | None = None,
    ) -> "pygame.Surface":
        """Render a rigid LOD grid aligned to fixed world-tile blocks.

        This is the "best of both worlds" path:
          * rigid blocks (no swimming)
          * all sampling uses overmap_accel (fast)
          * results cached per cell coordinate (stable + cheap when panning)
        """
        import math

        rect_x, rect_y, rect_w, rect_h = int(map_rect.x), int(map_rect.y), int(map_rect.w), int(map_rect.h)

        out = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        if rect_w <= 0 or rect_h <= 0:
            return out

        # Determine world center in absolute world-tile coordinates.
        cam = getattr(game, "camera", None)
        if cam is not None:
            abs_wx = float(getattr(cam, "world_x", 0.0))
            abs_wy = float(getattr(cam, "world_y", 0.0))
        else:
            abs_wx, abs_wy = self._infer_world_center_from_renderer(
                game, map_rect=map_rect, world_scale=world_scale, zone_w=zone_w, zone_h=zone_h
            )

        # Visible span in world tiles covered by on-screen map rect.
        world_scale = max(1e-6, float(world_scale))
        view_span_wx = float(rect_w) / world_scale
        view_span_wy = float(rect_h) / world_scale
        view_min_wx = abs_wx - view_span_wx * 0.5
        view_min_wy = abs_wy - view_span_wy * 0.5

        cell_w_tiles = max(1, int(cell_w_tiles))
        cell_h_tiles = max(1, int(cell_h_tiles))

        # Snap view_min to cell boundaries so the grid is rigid/stable.
        #
        # IMPORTANT: when we cross-fade between two adjacent LODs (e.g. 4-tiles and 8-tiles),
        # we must *anchor* both layers to the same snapped origin, otherwise their grids can
        # appear to drift relative to each other while blending.
        snap_w = float(max(1, int(snap_base_tiles or cell_w_tiles)))
        snap_h = float(max(1, int(snap_base_tiles or cell_h_tiles)))
        snap_min_wx = math.floor(view_min_wx / snap_w) * snap_w
        snap_min_wy = math.floor(view_min_wy / snap_h) * snap_h

        # Fractional scroll within the snapped cell grid (in pixels).
        # This makes panning/zooming move the grid smoothly while keeping it rigidly aligned
        # to world-tile cell boundaries (no 'stuck' grid / glyph swimming).
        offset_px_x = (view_min_wx - snap_min_wx) * world_scale
        offset_px_y = (view_min_wy - snap_min_wy) * world_scale

        # How many cells cover the viewport (+padding to avoid "island in black").
        cols = max(1, int(math.ceil(view_span_wx / float(cell_w_tiles))) + 3)
        rows = max(1, int(math.ceil(view_span_wy / float(cell_h_tiles))) + 3)

        # Cell size in pixels.
        cell_px_w_f = max(1e-6, float(cell_w_tiles) * world_scale)
        cell_px_h_f = max(1e-6, float(cell_h_tiles) * world_scale)
        # For font sizing and some bounds checks we still use integer pixels.
        cell_px_w = max(1, int(round(cell_px_w_f)))
        cell_px_h = max(1, int(round(cell_px_h_f)))

        # Determine total world size (in tiles) for overmap_accel.
        cfg = getattr(game, "cfg", None)
        total_w = total_h = 0
        if cfg is not None:
            try:
                total_w = int(cfg.world_map_screens * cfg.world_width)
                total_h = int(cfg.world_map_screens * cfg.world_height)
            except Exception:
                total_w = total_h = 0

        p = getattr(game, "overmap_params", None)
        if not p or total_w <= 1 or total_h <= 1:
            return out

        # Per-cell cache: (lod_id, cx, cy, overmap_sig) -> (ch, (r,g,b))
        sig = self._overmap_signature(game)
        cache = getattr(self, "_lod_cell_cache", None)
        if cache is None:
            cache = {}
            self._lod_cell_cache = cache

        base_cx = int(math.floor(snap_min_wx / float(cell_w_tiles)))
        base_cy = int(math.floor(snap_min_wy / float(cell_h_tiles)))

        # Quick check: if the top-left cell isn't cached, we need to sample.
        k0 = (lod_id, cell_w_tiles, cell_h_tiles, base_cx, base_cy, sig)
        need_sample = k0 not in cache

        if need_sample:
            try:
                from edgecaster.overmap_accel import render_overmap_buffers_numpy
                import numpy as np

                span_wx = float(cols * cell_w_tiles)
                span_wy = float(rows * cell_h_tiles)

                # Iter budget: interactive, with more detail when zoomed in.
                zoom_factor = max(1.0, float(total_w) / max(1e-9, span_wx))
                iters = 48 + int(24 * math.log(max(1.0, zoom_factor)))
                iters = int(max(24, min(iters, 320)))

                rgb_main, _rgb_corr, _peak2 = render_overmap_buffers_numpy(
                    px_w=int(cols),
                    px_h=int(rows),
                    total_w=total_w,
                    total_h=total_h,
                    j_min_x=float(p["view_min_jx"]),
                    j_max_x=float(p["view_max_jx"]),
                    j_min_y=float(p["view_min_jy"]),
                    j_max_y=float(p["view_max_jy"]),
                    view_min_wx=float(snap_min_wx),
                    view_min_wy=float(snap_min_wy),
                    view_span_wx=float(span_wx),
                    view_span_wy=float(span_wy),
                    visual_c=p["visual_c"],
                    iters=iters,
                    corruption_level=float(p.get("corruption_level", 0.0) or 0.0),
                    corruption_seed=int(p.get("corruption_seed", 1337) or 1337),
                    spline_weight=float(p.get("corruption_spline_weight", 0.0) or 0.0),
                    hotspots=p.get("corruption_hotspots", None),
                    anchors=p.get("corruption_anchors", None),
                )

                from edgecaster.biome import ANCHORS_RGB, BIOME_GLYPHS_NP
                anchors_rgb = np.asarray(ANCHORS_RGB, dtype=np.int16)
                glyph_lut = BIOME_GLYPHS_NP.astype(np.int16, copy=False)
                rgb16 = rgb_main.astype(np.int16)
                dif = rgb16[:, :, None, :] - anchors_rgb[None, None, :, :]
                dist2 = (dif * dif).sum(axis=3)
                idx = dist2.argmin(axis=2).astype(np.int16)

                glyph_codes = glyph_lut[idx]  # (rows, cols)
                col = anchors_rgb[idx].astype(np.int16)
                col = np.clip(col + 28, 0, 255).astype(np.uint8)

                for rr in range(rows):
                    cy = base_cy + rr
                    for cc in range(cols):
                        cx = base_cx + cc
                        k = (lod_id, cell_w_tiles, cell_h_tiles, cx, cy, sig)
                        if k in cache:
                            continue
                        g = int(glyph_codes[rr, cc])
                        ch = chr(g)
                        color = (int(col[rr, cc, 0]), int(col[rr, cc, 1]), int(col[rr, cc, 2]))
                        cache[k] = (ch, color)

                max_cells = int(getattr(self, "lod_cache_max_cells", 250_000) or 250_000)
                if len(cache) > max_cells:
                    drop_n = max(10_000, len(cache) - max_cells)
                    for _ in range(drop_n):
                        try:
                            cache.pop(next(iter(cache)))
                        except Exception:
                            break

            except Exception:
                return out

        font_px = max(8, min(256, int(min(cell_px_w, cell_px_h) * 0.90)))
        font = self._get_cached_map_font(font_px)

        for rr in range(rows):
            cy = base_cy + rr
            py_f = float(rect_y) + float(rr) * float(cell_h_tiles) * float(world_scale) - float(offset_px_y)
            # Skip rows wholly above/below the viewport (with 1-cell padding).
            if py_f + float(cell_px_h_f) < float(rect_y - cell_px_h) or py_f > float(rect_y + rect_h + cell_px_h):
                continue

            for cc in range(cols):
                cx = base_cx + cc
                px_f = float(rect_x) + float(cc) * float(cell_w_tiles) * float(world_scale) - float(offset_px_x)
                if px_f + float(cell_px_w_f) < float(rect_x - cell_px_w) or px_f > float(rect_x + rect_w + cell_px_w):
                    continue

                px = int(round(px_f))
                py = int(round(py_f))

                k = (lod_id, cell_w_tiles, cell_h_tiles, cx, cy, sig)
                val = cache.get(k)
                if not val:
                    continue
                ch, color = val

                surf = font.render(ch, True, color)
                gx = px + (cell_px_w - surf.get_width()) // 2
                gy = py + (cell_px_h - surf.get_height()) // 2
                out.blit(surf, (gx, gy))

        return out



    
    def _render_zone_grid(self, game, map_rect, zone_w: int, zone_h: int, world_scale: float) -> pygame.Surface:
        """
        Render a coarse ASCII glyph grid for zoomed-out views.

        IMPORTANT: returns a pygame.Surface (SRCALPHA), not a Python list.
        This keeps draw_world_zoomed() logic unchanged (it expects .set_alpha()).

        Option B: Prefer Schwab's accelerated numpy overmap renderer as a *sampler*:
          - render a very low-res rgb buffer covering the visible world window
          - quantize each sample to (glyph, color)
          - draw into the zone-grid surface

        Fallback: if accel isn't available/ready, draw a simple placeholder grid.
        """
        import math

        # map_rect is a pygame.Rect in your code, but tuple-unpacking works too.
        rect_x, rect_y, rect_w, rect_h = int(map_rect.x), int(map_rect.y), int(map_rect.w), int(map_rect.h)

        grid_surf = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        if rect_w <= 0 or rect_h <= 0:
            return grid_surf

        cam = getattr(game, "camera", None)

        if cam is not None:
            abs_wx = float(getattr(cam, "world_x", 0.0))
            abs_wy = float(getattr(cam, "world_y", 0.0))
        else:
            abs_wx, abs_wy = self._infer_world_center_from_renderer(
                game,
                map_rect=map_rect,
                world_scale=world_scale,
                zone_w=zone_w,
                zone_h=zone_h,
            )



        # ---- LOD step: how many world-tiles each drawn glyph cell represents ----
        # Instead of tying to an arbitrary pixel threshold (world_scale < 6),
        # tie LOD to the same crossfade regime used in draw_world_zoomed.

        if world_scale <= 0:
            world_scale = 1e-6

        # We want the grid to start "chunking" once the global layer is becoming dominant.
        # blend isn't passed in, but we can reconstruct something similar:
        # derive zoom from world_scale / base_tile.
        zoom = float(world_scale) / float(max(1, self.base_tile))

        # Recompute zoom_start from map_rect + zone dimensions (same as draw_world_zoomed)
        tile_fit_px = min(map_rect.w / float(zone_w), map_rect.h / float(zone_h))
        zoom_start = float(tile_fit_px) / float(max(1, self.base_tile))

        # ratio > 1 when zoomed out past the "zone fits" point
        ratio = max(1.0, zoom_start / max(1e-6, zoom))

        # Power-of-two steps: 1,2,4,8,... as we zoom out past zoom_start
        lod_step = int(2 ** int(math.floor(math.log(ratio, 2))))
        lod_step = max(1, min(lod_step, 256))


        cell_px_w = max(1, int(round(float(lod_step) * float(world_scale))))
        cell_px_h = max(1, int(round(float(lod_step) * float(world_scale))))


        cols = max(1, int(math.ceil(rect_w / float(cell_px_w))))
        rows = max(1, int(math.ceil(rect_h / float(cell_px_h))))

        # Subslots: increase sampling slightly when near the blend boundary, but keep cheap.
        slots_x = 4
        slots_y = 4
        if lod_step == 1:
            if world_scale >= 10.0:
                slots_x = slots_y = 4
            elif world_scale >= 7.0:
                slots_x = slots_y = 4
            else:
                slots_x = slots_y = 4

        # Absolute world size in tiles (global coordinates)
        cfg = getattr(game, "cfg", None)
        total_w = total_h = 0
        if cfg is not None:
            try:
                total_w = int(cfg.world_map_screens * cfg.world_width)
                total_h = int(cfg.world_map_screens * cfg.world_height)
            except Exception:
                total_w = total_h = 0


        # Visible span in world tiles covered by the on-screen map rect.
        # This keeps sampling *locked* to camera/world coordinates (no drifting with LOD).
        view_span_wx = float(rect_w) / float(world_scale)
        view_span_wy = float(rect_h) / float(world_scale)
        view_min_wx = abs_wx - view_span_wx * 0.5
        view_min_wy = abs_wy - view_span_wy * 0.5

        # ---------------------------------------------------------------------
        # Accel path: render tiny rgb buffer for the visible window and quantize
        # ---------------------------------------------------------------------
        accel_ok = False
        rgb_main = None

        p = getattr(game, "overmap_params", None)
        if p and total_w > 1 and total_h > 1:
            try:
                import numpy as np
                from edgecaster.overmap_accel import render_overmap_buffers_numpy

                px_w = int(cols * slots_x)
                px_h = int(rows * slots_y)

                # Cache the expensive numpy call when the view hasn't changed meaningfully.
                # (This cache is intentionally simple; we can refine later.)
                key = (
                    px_w, px_h,
                    int(round(view_min_wx * 10)), int(round(view_min_wy * 10)),
                    int(round(view_span_wx * 10)), int(round(view_span_wy * 10)),
                    float(p.get("corruption_level", 0.0) or 0.0),
                    int(p.get("corruption_seed", 1337) or 1337),
                )
                cached = getattr(self, "_zone_grid_accel_cache", None)
                if cached is None:
                    cached = {}
                    self._zone_grid_accel_cache = cached

                if key in cached:
                    rgb_main = cached[key]
                    accel_ok = True
                else:
                    # Iter budget: keep it interactive. If you want, we can tie this to lod_step.
                    zoom_factor = max(1.0, float(total_w) / max(1e-9, view_span_wx))
                    iters = 48 + int(24 * math.log(max(1.0, zoom_factor)))
                    iters = int(max(24, min(iters, 320)))

                    rgb_main, _rgb_corr, _peak2 = render_overmap_buffers_numpy(
                        px_w=px_w,
                        px_h=px_h,
                        total_w=total_w,
                        total_h=total_h,
                        j_min_x=float(p["view_min_jx"]),
                        j_max_x=float(p["view_max_jx"]),
                        j_min_y=float(p["view_min_jy"]),
                        j_max_y=float(p["view_max_jy"]),
                        view_min_wx=float(view_min_wx),
                        view_min_wy=float(view_min_wy),
                        view_span_wx=float(view_span_wx),
                        view_span_wy=float(view_span_wy),
                        visual_c=p["visual_c"],
                        iters=iters,
                        corruption_level=float(p.get("corruption_level", 0.0) or 0.0),
                        corruption_seed=int(p.get("corruption_seed", 1337) or 1337),
                        spline_weight=float(p.get("corruption_spline_weight", 0.0) or 0.0),
                        hotspots=p.get("corruption_hotspots", None),
                        anchors=p.get("corruption_anchors", None),
                    )

                    # Store only rgb_main in cache (small enough at this resolution).
                    cached[key] = rgb_main
                    # Keep cache from growing forever
                    if len(cached) > 32:
                        # drop an arbitrary old item
                        cached.pop(next(iter(cached)))

                    accel_ok = True

            except Exception:
                accel_ok = False
                rgb_main = None

        # ---------------------------------------------------------------------
        # Quantize RGB -> glyph + color (cheap + stable)
        # ---------------------------------------------------------------------
        if accel_ok and rgb_main is not None:
            import numpy as np

            from edgecaster.biome import ANCHORS_RGB, BIOME_GLYPHS_NP
            anchors_rgb = np.asarray(ANCHORS_RGB, dtype=np.int16)
            glyph_lut = BIOME_GLYPHS_NP.astype(np.int16, copy=False)
            rgb16 = rgb_main.astype(np.int16)
            dif = rgb16[:, :, None, :] - anchors_rgb[None, None, :, :]
            dist2 = (dif * dif).sum(axis=3)
            idx = dist2.argmin(axis=2).astype(np.int16)

            glyph_codes = glyph_lut[idx]  # (px_h, px_w)
            col = anchors_rgb[idx].astype(np.int16)
            col = np.clip(col + 28, 0, 255).astype(np.uint8)

            # Draw: one glyph per *cell* (aggregate subslots by majority vote)
            font_px = max(8, min(64, int(min(cell_px_w, cell_px_h) * 0.90)))
            font = self._get_cached_map_font(font_px)

            for r in range(rows):
                for c in range(cols):
                    # aggregate subslots
                    x0 = c * slots_x
                    y0 = r * slots_y
                    block = glyph_codes[y0:y0 + slots_y, x0:x0 + slots_x].reshape(-1)
                    if block.size == 0:
                        continue
                    # majority vote glyph
                    # (np.bincount wants non-negative ints)
                    bc = np.bincount(block.astype(np.int32))
                    g = int(bc.argmax())
                    ch = chr(g)

                    # color: average of subslots’ anchor colors
                    col_block = col[y0:y0 + slots_y, x0:x0 + slots_x, :].reshape(-1, 3)
                    if col_block.size == 0:
                        continue
                    rr = int(col_block[:, 0].mean())
                    gg = int(col_block[:, 1].mean())
                    bb = int(col_block[:, 2].mean())
                    color = (rr, gg, bb)

                    # cell rect in screen space
                    px = rect_x + c * cell_px_w
                    py = rect_y + r * cell_px_h

                    # quick reject
                    if px >= rect_x + rect_w or py >= rect_y + rect_h:
                        continue

                    # render centered
                    surf = font.render(ch, True, color)
                    gx = px + (cell_px_w - surf.get_width()) // 2
                    gy = py + (cell_px_h - surf.get_height()) // 2
                    grid_surf.blit(surf, (gx, gy))

            return grid_surf

        # ---------------------------------------------------------------------
        # Fallback: cheap placeholder grid (keeps engine running if accel not ready)
        # ---------------------------------------------------------------------
        font_px = max(8, int(min(cell_px_w, cell_px_h) * 0.90))
        font = self._get_cached_map_font(font_px)
        dim = (140, 140, 150)

        for r in range(rows):
            for c in range(cols):
                px = rect_x + c * cell_px_w
                py = rect_y + r * cell_px_h
                if px >= rect_x + rect_w or py >= rect_y + rect_h:
                    continue
                surf = font.render("·", True, dim)
                gx = px + (cell_px_w - surf.get_width()) // 2
                gy = py + (cell_px_h - surf.get_height()) // 2
                grid_surf.blit(surf, (gx, gy))

        return grid_surf


    def _get_cached_font(self, px: int) -> pygame.font.Font:
        if not hasattr(self, "_font_cache"):
            self._font_cache = {}
        cache = self._font_cache
        if px not in cache:
            cache[px] = pygame.font.SysFont("consolas", px, bold=True)
        return cache[px]

    def _get_cached_map_font(self, px: int) -> pygame.font.Font:
        """Font cache for map glyphs (local + global layers).

        Critical: keep styling consistent with `self.map_font` (non-bold). If
        we use a bold font here, the zoomed-out / global glyphs look like a
        different tileset even when the glyph char is identical.
        """
        px = int(px)
        if px <= 0:
            px = 1
        f = self._map_font_cache.get(px)
        if f is None:
            # Match `self.map_font` styling: Consolas, NOT bold.
            f = pygame.font.SysFont("consolas", px, bold=False)
            self._map_font_cache[px] = f
        return f



    def draw_lorenz_overlay(self, game: Game) -> None:
        """Draw Lorenz 'butterflies' with short, tapered afterimage trails."""
        # refactor: replace renderer-managed Lorenz state with a LorenzView from lorenz.py; renderer should
        # only consume immutable positions/traits.
        
        
        # If the game just re-seeded the storm (stairs / teleport), wipe trails.
        if getattr(game, "lorenz_reset_trails", False):
            self.lorenz_surface.fill((0, 0, 0, 0))
            self.lorenz_trail_frames.clear()
            self.lorenz_last_tick = None
        # OPTIONAL: only Strange Attractors get the storm
        if not getattr(game, "has_lorenz_aura", False):
            # Clear trails if we swap to a non-storm character
            self.lorenz_surface.fill((0, 0, 0, 0))
            self.lorenz_trail_frames.clear()
            return

        # Get points from the game, not from the renderer.
        points = getattr(game, "lorenz_points", None)
        if not points:
            # No new points; clear any old trails
            self.lorenz_surface.fill((0, 0, 0, 0))
            self.lorenz_trail_frames.clear()
            return

        # Clear previous frame; we redraw trails from history each time
        self.lorenz_surface.fill((0, 0, 0, 0))

        # Fallback to the player position if the game didn't set a center yet.
        player = game.actors[game.player_id]
        px_tile, py_tile = player.pos
        center_x = getattr(game, "lorenz_center_x", float(px_tile))
        center_y = getattr(game, "lorenz_center_y", float(py_tile))

        # --- “camera” for the attractor: project (x, z) with a rotation ---
        angle = math.radians(30.0)  # tweak until it looks nice
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        # Keep (ux, uy, z) so we can color by z
        points_2d: List[Tuple[float, float, float]] = []
        z_min = float("inf")
        z_max = float("-inf")

        for (x, y, z) in points:
            # Take the (x, z) plane
            u = x
            v = z

            # Rotate
            ux = cos_a * u - sin_a * v
            uy = sin_a * u + cos_a * v

            points_2d.append((ux, uy, z))
            if z < z_min:
                z_min = z
            if z > z_max:
                z_max = z

        if not points_2d:
            return

        # Avoid divide-by-zero if z-range collapses
        if z_max <= z_min:
            z_max = z_min + 1e-6

        # --- “natural” Lorenz center: between the wings ---
        # Use x0 = 0 and z_mid as the middle of the z-band.
        z_mid = 0.5 * (z_min + z_max)
        x0 = 0.0

        # Project this (x0, z_mid) point into (ux, uy) space
        natural_ux = cos_a * x0 - sin_a * z_mid
        natural_uy = sin_a * x0 + cos_a * z_mid

        base_radius_px = max(2, int(self.tile * 0.3))

        def color_for_z(z: float) -> Tuple[int, int, int]:
            """Interpolate red -> amber -> yellow along z (RGB only)."""
            t = (z - z_min) / (z_max - z_min)
            t = max(0.0, min(1.0, t))

            low = (255, 80, 60)      # reddish
            mid = (255, 180, 80)     # amber
            high = (255, 255, 120)   # yellow

            if t < 0.5:
                u = t / 0.5
                r = int(low[0] + (mid[0] - low[0]) * u)
                g = int(low[1] + (mid[1] - low[1]) * u)
                b = int(low[2] + (mid[2] - low[2]) * u)
            else:
                u = (t - 0.5) / 0.5
                r = int(mid[0] + (high[0] - mid[0]) * u)
                g = int(mid[1] + (high[1] - mid[1]) * u)
                b = int(mid[2] + (high[2] - mid[2]) * u)
            return (r, g, b)

        # Project current butterflies into screen pixel space
        frame_points: List[Tuple[int, int, float]] = []

        for (ux, uy, z) in points_2d:
            # Offset relative to the fixed natural center in phase space
            rel_x = ux - natural_ux
            rel_y = uy - natural_uy

            dx = rel_x * self.lorenz_scale
            dy = rel_y * self.lorenz_scale

            if abs(dx) > self.lorenz_radius_tiles or abs(dy) > self.lorenz_radius_tiles:
                continue

            tx = int(round(center_x + dx))
            ty = int(round(center_y + dy))
            if not game.world.in_bounds(tx, ty):
                continue

            px = tx * self.tile + self.origin_x
            py = ty * self.tile + self.origin_y
            if px < 0 or py < 0 or px >= self.width or py >= self.height:
                continue

            frame_points.append((px, py, z))

        if not frame_points:
            self.lorenz_trail_frames.clear()
            return

        # Push the newest frame; keep only a handful for a short trail
        max_trail = 5
        self.lorenz_trail_frames.append(frame_points)
        if len(self.lorenz_trail_frames) > max_trail:
            self.lorenz_trail_frames.pop(0)

        # Draw from oldest (faintest, smallest) to newest (brightest, biggest)
        for i, frame in enumerate(self.lorenz_trail_frames):
            age = (i + 1) / len(self.lorenz_trail_frames)  # older → smaller / dimmer
            radius_px = max(1, int(base_radius_px * (1.0 - 0.6 * age)))
            alpha = int(255 * (1.0 - 0.7 * age))  # fade out with age
            for (px, py, z) in frame:
                r, g, b = color_for_z(z)
                color = (r, g, b, alpha)
                pygame.draw.circle(self.lorenz_surface, color, (px, py), radius_px)

        # Finally, blit the Lorenz layer over the main surface
        self.surface.blit(self.lorenz_surface, (0, 0))







    def _entity_visual(self, ent) -> Tuple[str, Tuple[int, int, int]]:
        """Return (glyph, color) for any renderable entity.

        - Actors are detected by having a 'faction' attribute.
        - Generic entities (items/features) use their own glyph/color.
        """
        # Actors (player, monsters, NPCs, etc.)
        if hasattr(ent, "faction"):
            # Always respect the entity's own glyph so body-swaps look right.
            glyph = getattr(ent, "glyph", "@")

            # You can still give factions default colors, but don't clobber explicit ones.
            base_color = getattr(ent, "color", None)
            if base_color is not None:
                color = base_color
            elif ent.faction == "player":
                color = self.player_color
            elif ent.faction == "npc":
                color = self.fg
            else:
                color = self.monster_color

            return glyph, color

        # Generic entities: items, features, etc.
        glyph = getattr(ent, "glyph", "?")
        color = getattr(ent, "color", self.fg)
        return glyph, color




    def draw_entities(self, world: World, entities) -> None:
        """Draw all renderable entities (actors, items, features...) on the map.

        This wrapper supports LOD fading: when the map is cross-fading to the
        global grid, entities fade with the local layer.
        """
        alpha = int(getattr(self, "_local_fade_alpha", 255))
        if alpha <= 0:
            return
        if alpha >= 255:
            self._draw_entities_impl(world, entities)
            return

        # Draw entities onto a temporary surface, then alpha-blend onto the main surface.
        temp = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        prev_surface = self.surface
        self.surface = temp
        try:
            self._draw_entities_impl(world, entities)
        finally:
            self.surface = prev_surface

        temp.set_alpha(alpha)
        self.surface.blit(temp, (0, 0))


    def _draw_entities_impl(self, world: World, entities) -> None:
        """Draw all renderable entities (actors, items, features...) on the map.

        Ordering is controlled by an optional 'render_layer' attribute:
        higher layers are drawn later (on top).
        """
        # Sort by render_layer; actors get default layer 2, others 1.
        def layer(ent) -> int:
            if hasattr(ent, "faction"):
                # treat actors as a higher layer by default
                return getattr(ent, "render_layer", 2)
            return getattr(ent, "render_layer", 1)

        entities_sorted = sorted(entities, key=layer)

        for ent in entities_sorted:
            pos = getattr(ent, "pos", None)
            if pos is None:
                continue
            x, y = pos
            if not world.in_bounds(x, y):
                continue
            tile = world.get_tile(x, y)
            if not tile or not tile.visible:
                continue

            px = x * self.tile + self.origin_x
            py = y * self.tile + self.origin_y
            if px >= self.width or py >= self.height:
                continue

            tags = getattr(ent, "tags", {}) or {}
            if tags.get("currency") == "bismuth" and getattr(self, "bismuth_icon_map", None) is not None:
                icon = self.bismuth_icon_map
                self.surface.blit(icon, (px, py))
            else:
                glyph, base_color = self._entity_visual(ent)

                scene_eff = getattr(self, "active_visual_effects", None) or []
                ent_eff = effect_names_from_obj(ent)
                eff = concat_effect_names(scene_eff, ent_eff)

                # Color lane
                color = apply_entity_color_effects(ent, base_color, eff)

                # Base tile rect in LOCAL coords
                base_rect = pygame.Rect(0, 0, self.tile, self.tile)

                # Compute union overlay bounds (may extend beyond base_rect)
                union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)

                # Allocate a canvas large enough for overlays (aura space)
                canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)

                # Offset that maps base_rect space into canvas space
                ox, oy = -union_rect.left, -union_rect.top

                # Render glyph centered inside the base tile region (shifted onto canvas)
                glyph_surf = self.map_font.render(glyph, True, color)
                gx = ox + (self.tile - glyph_surf.get_width()) // 2
                gy = oy + (self.tile - glyph_surf.get_height()) // 2
                canvas.blit(glyph_surf, (gx, gy))

                # Overlay lane: per-effect rects, shifted into canvas coords
                if eff:
                    shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
                    apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

                # Geometry lane: apply transforms to the whole canvas when blitting
                dest = pygame.Rect(px + union_rect.left, py + union_rect.top, union_rect.w, union_rect.h)
                if eff:
                    visual = build_visual_profile(VisualProfile(), eff)
                    apply_visual_panel(self.surface, canvas, dest, visual)
                else:
                    self.surface.blit(canvas, dest.topleft)

    def draw_pattern_overlay(self, game: Game) -> None:
        self.edges_surface.fill((0, 0, 0, 0))
        self.verts_surface.fill((0, 0, 0, 0))
        if not game.pattern.vertices:
            return
        origin = game.pattern_anchor
        if origin is None:
            return
        verts = project_vertices(game.pattern, origin)
        # density-based sizing
        count = len(verts)
        if count > 400:
            v_radius = max(1, int(self.vertex_base_radius * self.zoom * 0.5))
        elif count > 150:
            v_radius = max(1, int(self.vertex_base_radius * self.zoom * 0.75))
        else:
            v_radius = max(1, int(self.vertex_base_radius * self.zoom))
        v_radius = max(1, v_radius)

        # edges with gradient (or colored), thicker AA line (no halo)
        edge_colors = getattr(game.pattern, "edge_colors", {}) or {}
        for e in game.pattern.edges:
            try:
                a = verts[e.a]
                b = verts[e.b]
            except IndexError:
                continue
            ax = a[0] * self.tile + self.tile * 0.5 + self.origin_x
            ay = a[1] * self.tile + self.tile * 0.5 + self.origin_y
            bx = b[0] * self.tile + self.tile * 0.5 + self.origin_x
            by = b[1] * self.tile + self.tile * 0.5 + self.origin_y
            dx = bx - ax
            dy = by - ay
            dist = max(1.0, math.hypot(dx, dy))
            steps = max(4, int(dist / (self.tile * 0.75)))
            for i in range(steps):
                t0 = i / steps
                t1 = (i + 1) / steps
                x0 = ax + dx * t0
                y0 = ay + dy * t0
                x1 = ax + dx * t1
                y1 = ay + dy * t1
                edge_key = (min(e.a, e.b), max(e.a, e.b))
                col = edge_colors.get(edge_key)
                if col and isinstance(col, (list, tuple)) and len(col) == 2 and all(
                    isinstance(c, (list, tuple)) and len(c) >= 3 for c in col
                ):
                    c0 = tuple(int(v) for v in col[0][:3])
                    c1 = tuple(int(v) for v in col[1][:3])
                    col = self._lerp_color(c0, c1, (t0 + t1) * 0.5)
                if not col:
                    col = self._lerp_color(self.pattern_color, self.pattern_color_end, (t0 + t1) * 0.5)
                core_col = (*col, 220)
                pygame.draw.line(self.edges_surface, core_col, (x0, y0), (x1, y1), width=self.edge_width_base)
                pygame.draw.aaline(self.edges_surface, core_col, (x0, y0), (x1, y1))

        # vertices with glow sprites
        base_sprite = self._get_glow_sprite(v_radius, self.pattern_color)
        for vx, vy in verts:
            px = int(vx * self.tile + self.tile * 0.5 + self.origin_x)
            py = int(vy * self.tile + self.tile * 0.5 + self.origin_y)
            rect = base_sprite.get_rect(center=(px, py))
            self.verts_surface.blit(base_sprite, rect)

        # composite layers
        self.surface.blit(self.edges_surface, (0, 0))
        self.surface.blit(self.verts_surface, (0, 0))

    def draw_aim_overlay(self, game: Game) -> None:
        pred = self._ui_attr("aim_prediction", None)
        aim_action = getattr(pred, "action", None) or self._ui_attr("aim_action", None)
        if aim_action not in ("activate_all", "activate_seed"):
            return
        origin = game.pattern_anchor
        if origin is None or not game.pattern.vertices:
            return
        verts = project_vertices(game.pattern, origin)
        hover_vertex = getattr(pred, "hover_vertex", None) or self._ui_attr("hover_vertex", None)
        if hover_vertex is None or hover_vertex >= len(verts):
            return
        dmg_map: Dict[Tuple[int, int], int] = getattr(pred, "dmg_map", {}) if pred else {}
        fail_text: str | None = getattr(pred, "fail_text", None) if pred else None

        if aim_action == "activate_all":
            radius = getattr(pred, "radius", None)
            if radius is None:
                try:
                    radius = game.get_param_value("activate_all", "radius")
                except Exception:
                    radius = game.cfg.pattern_damage_radius if hasattr(game, "cfg") else 1.25
            center = verts[hover_vertex]
            cx = int(center[0] * self.tile + self.tile * 0.5 + self.origin_x)
            cy = int(center[1] * self.tile + self.tile * 0.5 + self.origin_y)
            pygame.draw.circle(self.surface, (120, 200, 255), (cx, cy), int(radius * self.tile), width=1)
            r2 = radius * radius
            target_vertices = getattr(pred, "target_vertices", None)
            if target_vertices is None:
                target_vertices = [i for i, v in enumerate(verts) if (v[0] - center[0]) ** 2 + (v[1] - center[1]) ** 2 <= r2]
            for idx in target_vertices:
                if idx < 0 or idx >= len(verts):
                    continue
                vx, vy = verts[idx]
                px = int(vx * self.tile + self.tile * 0.5 + self.origin_x)
                py = int(vy * self.tile + self.tile * 0.5 + self.origin_y)
                pygame.draw.circle(self.surface, (200, 240, 255), (px, py), max(3, self.tile // 5))
        else:  # activate_seed
            center = verts[hover_vertex]
            px = int(center[0] * self.tile + self.tile * 0.5 + self.origin_x)
            py = int(center[1] * self.tile + self.tile * 0.5 + self.origin_y)
            pygame.draw.circle(self.surface, (255, 230, 120), (px, py), max(5, self.tile // 3))
            target_vertices = getattr(pred, "target_vertices", None) or []
            seen = set()
            ordered_targets = []
            for idx in target_vertices:
                if idx is None or idx in seen:
                    continue
                seen.add(idx)
                ordered_targets.append(idx)
            for idx in ordered_targets:
                if idx < 0 or idx >= len(verts):
                    continue
                vx, vy = verts[idx]
                px = int(vx * self.tile + self.tile * 0.5 + self.origin_x)
                py = int(vy * self.tile + self.tile * 0.5 + self.origin_y)
                color = (200, 220, 255) if idx != hover_vertex else (255, 230, 120)
                pygame.draw.circle(self.surface, color, (px, py), max(3, self.tile // 5))
                tx = int(round(vx))
                ty = int(round(vy))
                rect = pygame.Rect(tx * self.tile + self.origin_x, ty * self.tile + self.origin_y, self.tile, self.tile)
                pygame.draw.rect(self.surface, color, rect, 1)

        # render previews with 2s triangle-wave fade
        t = pygame.time.get_ticks()
        phase = (t % 2000) / 2000.0
        fade = 1.0 - abs(phase * 2 - 1)  # triangle 0..1..0 over 2s
        alpha = int(80 + 120 * fade)
        dmg_font = pygame.font.SysFont("consolas", max(16, int(self.tile * 0.7)))

        # One number per actor (if visible); avoids per-tile spam.
        per_actor_damage: Dict[str, int] = getattr(pred, "per_actor_damage", {}) if pred else {}
        if per_actor_damage:
            try:
                level = game._level()
                for aid, dmg in per_actor_damage.items():
                    actor = level.actors.get(aid)
                    if actor is None:
                        continue
                    ax, ay = actor.pos
                    tile = level.world.get_tile(ax, ay) if hasattr(level, "world") else None
                    if tile is not None and hasattr(tile, "visible") and not tile.visible:
                        continue
                    px = ax * self.tile + self.origin_x + self.tile // 2
                    py = ay * self.tile + self.origin_y + self.tile // 2
                    dmg_surf = dmg_font.render(str(dmg), True, (255, 160, 160))
                    surf = pygame.Surface((dmg_surf.get_width(), dmg_surf.get_height()), pygame.SRCALPHA)
                    surf.blit(dmg_surf, (0, 0))
                    surf.set_alpha(alpha)
                    self.surface.blit(surf, (px - dmg_surf.get_width() // 2, py - self.tile // 2 - dmg_surf.get_height()))
            except Exception:
                # If we can't resolve actors (e.g., in rare init states), fallback could be added later.
                pass

        # Fail text: place just above the circle. Use fail_pct if available.
        fail_pct = getattr(pred, "fail_pct", None) if pred else None
        text_val = None
        if fail_pct is not None:
            text_val = f"Fail~{int(round(fail_pct))}%"
        elif fail_text:
            text_val = fail_text
        if text_val:
            center = verts[hover_vertex]
            radius = getattr(pred, "radius", None) or 0
            cx = int(center[0] * self.tile + self.tile * 0.5 + self.origin_x)
            cy = int(center[1] * self.tile + self.tile * 0.5 + self.origin_y)
            txt = self.small_font.render(text_val, True, (255, 180, 140))
            surf = pygame.Surface((txt.get_width(), txt.get_height()), pygame.SRCALPHA)
            surf.blit(txt, (0, 0))
            surf.set_alpha(alpha)
            # position above circle: just outside the top edge
            offset = int(radius * self.tile) if radius else int(1.0 * self.tile)
            self.surface.blit(surf, (cx - txt.get_width() // 2, cy - offset - txt.get_height() - 4))

    def draw_activation_overlay(self, game: Game) -> None:
        """Post-activation visuals only (no text)."""
        if not game.activation_points or game.activation_ttl <= 0:
            return
        world = game.world
        for vx, vy in game.activation_points:
            tx = int(round(vx))
            ty = int(round(vy))
            if not world.in_bounds(tx, ty):
                continue
            tile = world.get_tile(tx, ty)
            if tile is None or not tile.visible:
                continue
            px = int(vx * self.tile + self.tile * 0.5 + self.origin_x)
            py = int(vy * self.tile + self.tile * 0.5 + self.origin_y)
            sprite = self._get_glow_sprite(max(3, self.tile // 8), self.rune_color)
            self.surface.blit(sprite, sprite.get_rect(center=(px, py)))

    def draw_target_cursor(self, game: Game) -> None:
        """
        Draw the tile highlight for unified TargetMode.

        - For legacy rune placement, we still gate on game.awaiting_terminus.
        - For look-style targeting, we draw whenever ui_state.target.kind == "look".
        """
        t = self._ui_attr("target", None)
        tc = self._ui_attr("target_cursor", None)

        if not tc:
            return

        # Draw for:
        #  - legacy terminus mode (awaiting_terminus flag)
        #  - look-style TargetMode (kind == "look")
        kind = getattr(t, "kind", None) if t is not None else None
        draw_for_terminus = bool(game.awaiting_terminus)
        draw_for_look = (kind == "look")

        if not (draw_for_terminus or draw_for_look):
            return

        tx, ty = tc
        if not game.world.in_bounds(tx, ty):
            return

        px = tx * self.tile + self.origin_x
        py = ty * self.tile + self.origin_y

        rect = pygame.Rect(px, py, self.tile, self.tile)
        pygame.draw.rect(self.surface, (255, 255, 120), rect, 2)
        
        
    def draw_look_overlay(self, game: Game) -> None:
        """
        When in look-style TargetMode, show the name of any actor under
        the cursor in a yellow label just beneath the tile.
        """
        t = self._ui_attr("target", None)
        if not t or getattr(t, "kind", None) != "look":
            return

        cursor_tile = getattr(t, "cursor_tile", None) or self._ui_attr("target_cursor", None)
        if not cursor_tile:
            return

        tx, ty = cursor_tile
        world = game.world
        if not world.in_bounds(tx, ty):
            return

        tile = world.get_tile(tx, ty)
        if tile is None or (hasattr(tile, "visible") and not tile.visible):
            return

        # Find any renderable entities (actors, items, features...) at this tile.
        try:
            renderables = game.renderables_current()
        except Exception:
            renderables = []

        entities_here = [
            e for e in renderables
            if getattr(e, "pos", None) == (tx, ty)
        ]
        if not entities_here:
            return

        # For now, just show the "topmost" entity's name.
        ent = entities_here[-1]
        name = getattr(ent, "name", None) or getattr(ent, "label", None) or "something"


        # Convert tile → pixel; center text under the tile
        px = tx * self.tile + self.origin_x + self.tile // 2
        py = ty * self.tile + self.origin_y + self.tile  # just below the tile

        text = self.small_font.render(name, True, self.sel)  # self.sel is your yellow-ish color
        self.surface.blit(text, (px - text.get_width() // 2, py))



    def draw_place_overlay(self, game: Game) -> None:
        """Subtle pulsing circle showing placement range when selecting a terminus."""
        if not game.awaiting_terminus:
            return
        player = game.actors[game.player_id]
        cx = player.pos[0] * self.tile + self.tile * 0.5 + self.origin_x
        cy = player.pos[1] * self.tile + self.tile * 0.5 + self.origin_y
        radius = game.place_range * self.tile
        pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 350.0)
        alpha = int(25 + 30 * pulse)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        color = (120, 200, 255, alpha)
        pygame.draw.circle(overlay, color, (int(cx), int(cy)), int(radius), width=2)
        self.surface.blit(overlay, (0, 0))

    def draw_push_preview(self, game: Game) -> None:
        """Draw source/target boxes and ghost pattern for push_pattern targeting."""
        preview = self._ui_attr("push_preview", None)
        if not preview:
            return
        source = preview.get("source_com")
        target = preview.get("target_com")
        half = preview.get("half_size", 1.0)
        if source is None or target is None:
            return

        def _to_px(pt: Tuple[float, float]) -> Tuple[int, int]:
            # Align to tile centers so ghost matches where the pattern will land.
            return (
                int(pt[0] * self.tile + self.tile * 0.5 + self.origin_x),
                int(pt[1] * self.tile + self.tile * 0.5 + self.origin_y),
            )

        def _draw_box(center: Tuple[int, int], color_top, color_bottom) -> None:
            cx, cy = center
            size = int(half * self.tile)
            left = cx - size
            right = cx + size
            top = cy - size
            bottom = cy + size
            pygame.draw.line(self.surface, color_top, (left, top), (right, top), 2)
            pygame.draw.line(self.surface, color_bottom, (left, bottom), (right, bottom), 2)
            steps = max(2, bottom - top)
            for i in range(steps + 1):
                t = i / max(1, steps)
                col = (
                    int(color_top[0] + (color_bottom[0] - color_top[0]) * t),
                    int(color_top[1] + (color_bottom[1] - color_top[1]) * t),
                    int(color_top[2] + (color_bottom[2] - color_top[2]) * t),
                )
                y = top + i
                pygame.draw.line(self.surface, col, (left, y), (left + 1, y))
                pygame.draw.line(self.surface, col, (right, y), (right - 1, y))

        _draw_box(_to_px(source), (0, 0, 0), (255, 255, 255))
        _draw_box(_to_px(target), (30, 30, 30), (240, 240, 240))

        tgt_verts = preview.get("target_verts") or []
        edges = preview.get("edges") or []
        if tgt_verts and edges:
            tval = pygame.time.get_ticks() / 1000.0
            alpha = int(80 + 80 * (0.5 + 0.5 * math.sin(tval * 2 * math.pi)))
            ghost = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            for a, b in edges:
                if a >= len(tgt_verts) or b >= len(tgt_verts):
                    continue
                ax, ay = tgt_verts[a]
                bx, by = tgt_verts[b]
                pygame.draw.aaline(
                    ghost,
                    (180, 255, 200, alpha),
                    _to_px((ax, ay)),
                    _to_px((bx, by)),
                )
            self.surface.blit(ghost, (0, 0))

    def draw_status(self, game: Game) -> None:
        """Draw the top header HUD (delegates to StatusHeaderWidget)."""
        if not hasattr(self, "status_widget") or self.status_widget is None:
            self.status_widget = StatusHeaderWidget()

        ctx = WidgetContext(surface=self.surface, game=game, scene=None, renderer=self)

        # Reserve the top bar region; widget draws inside this.
        self.status_widget.rect = pygame.Rect(0, 0, self.width, self.top_bar_height)
        self.status_widget.layout(ctx)
        self.status_widget.draw(ctx)

    def draw_log(self, game: Game) -> None:
        """Scrollable log on the left side."""
        panel_w = self.log_panel_width
        panel_h = self.height - self.top_bar_height - self.ability_bar_height
        panel_rect = pygame.Rect(self.width - panel_w, self.top_bar_height, panel_w, panel_h)
        pygame.draw.rect(self.surface, (14, 14, 24), panel_rect)
        pygame.draw.rect(self.surface, (60, 60, 80), panel_rect, 1)

        panel_surface = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)

        # Keep a deeper history so scrolling can see older messages.
        # Ensure log capacity is high enough for long sessions.
        try:
            game.log.capacity = max(getattr(game.log, "capacity", 0), 100000)
        except Exception:
            pass
        max_lines = max(1000, getattr(game.log, "capacity", 1000))
        lines = game.log.tail(max_lines)
        y = panel_h - 8
        y += self.log_scroll_offset
        for line in reversed(lines[-max_lines:]):
            wrapped = self._wrap_text(line, self.small_font, panel_w - 12)
            for wline in reversed(wrapped):
                text = self.small_font.render(wline, True, self.fg)
                y -= text.get_height() + 2
                if y < 4:
                    break
                panel_surface.blit(text, (6, y))
            if y < 4:
                break

        self.surface.blit(panel_surface, panel_rect.topleft)

    def scroll_log(self, game: Game, delta_lines: int) -> None:
        """
        Adjust log scroll offset when hovering log panel.
        """
        line_h = self.small_font.get_height() + 2
        self.log_scroll_offset += delta_lines * line_h
        # Clamp to available content
        panel_h = self.height - self.top_bar_height - self.ability_bar_height
        max_lines = max(1000, getattr(game.log, "capacity", 1000))
        lines = game.log.tail(max_lines)
        total_h = 0
        for line in lines:
            wrapped = self._wrap_text(line, self.small_font, self.log_panel_width - 12)
            for _ in wrapped:
                total_h += line_h
        max_offset = max(0, total_h - panel_h + 16)
        if self.log_scroll_offset < 0:
            self.log_scroll_offset = 0
        if self.log_scroll_offset > max_offset:
            self.log_scroll_offset = max_offset
        # Optional debug
        try:
            from edgecaster.scenes.keybinds_scene import _dbg  # reuse simple logger if available

            _dbg(f"log_scroll offset={self.log_scroll_offset} total_h={total_h} max_offset={max_offset} lines={len(lines)}")
        except Exception:
            pass

    def draw_ignite_overlay(self, game: Game) -> None:
        """
        Visual overlay for Ignite: glow direct tiles red/orange, indirect amber, with flicker.
        """
        level = getattr(game, "_level", lambda: None)()
        if level is None:
            return
        state = getattr(level, "ignite_state", None)
        if not state:
            return
        direct = state.get("direct_tiles") or []
        indirect = state.get("indirect_tiles") or []
        remaining = float(state.get("remaining", 0))
        duration = float(state.get("duration", 1)) or 1.0
        tval = pygame.time.get_ticks() / 1000.0
        flicker = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(tval * 6.0))
        alpha_scale = (remaining / duration) * flicker

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        def draw_tile(tile_pos, color):
            tx, ty = tile_pos
            px = int(tx * self.tile + self.origin_x)
            py = int(ty * self.tile + self.origin_y)
            rect = pygame.Rect(px, py, self.tile, self.tile)
            pygame.draw.rect(overlay, color, rect)

        direct_col = (255, 80, 40, int(160 * alpha_scale))
        indirect_col = (255, 180, 80, int(90 * alpha_scale))
        for tile in direct:
            draw_tile(tile, direct_col)
        for tile in indirect:
            draw_tile(tile, indirect_col)

        # Subtle pulse outline on pattern anchor area if present
        anchor = getattr(level, "pattern_anchor", None)
        if anchor:
            ax, ay = anchor
            cx = ax * self.tile + self.origin_x + self.tile * 0.5
            cy = ay * self.tile + self.origin_y + self.tile * 0.5
            radius = self.tile * 1.0
            col = (255, 120, 60, int(80 * alpha_scale))
            pygame.draw.circle(overlay, col, (int(cx), int(cy)), int(radius), width=2)

        self.surface.blit(overlay, (0, 0))

    def draw_regrow_overlay(self, game: Game) -> None:
        """
        Visual overlay for Regrow: glow direct tiles green, indirect teal, with pulse.
        """
        level = getattr(game, "_level", lambda: None)()
        if level is None:
            return
        state = getattr(level, "regrow_state", None)
        if not state:
            return
        direct = state.get("direct_tiles") or []
        indirect = state.get("indirect_tiles") or []
        remaining = float(state.get("remaining", 0))
        duration = float(state.get("duration", 1)) or 1.0
        tval = pygame.time.get_ticks() / 1000.0
        flicker = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(tval * 5.5))
        alpha_scale = (remaining / duration) * flicker

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        def draw_tile(tile_pos, color):
            tx, ty = tile_pos
            px = int(tx * self.tile + self.origin_x)
            py = int(ty * self.tile + self.origin_y)
            rect = pygame.Rect(px, py, self.tile, self.tile)
            pygame.draw.rect(overlay, color, rect)

        direct_col = (80, 255, 120, int(160 * alpha_scale))
        indirect_col = (80, 220, 200, int(90 * alpha_scale))
        for tile in direct:
            draw_tile(tile, direct_col)
        for tile in indirect:
            draw_tile(tile, indirect_col)

        anchor = getattr(level, "pattern_anchor", None)
        if anchor:
            ax, ay = anchor
            cx = ax * self.tile + self.origin_x + self.tile * 0.5
            cy = ay * self.tile + self.origin_y + self.tile * 0.5
            radius = self.tile * 1.0
            col = (120, 255, 180, int(70 * alpha_scale))
            pygame.draw.circle(overlay, col, (int(cx), int(cy)), int(radius), width=2)

        self.surface.blit(overlay, (0, 0))

    def _wrap_text(self, text: str, font: pygame.font.Font, max_width: int) -> List[str]:
        """Simple word-wrap that fits text within max_width."""
        words = text.split()
        lines: List[str] = []
        cur: List[str] = []
        for w in words:
            test = " ".join(cur + [w]) if cur else w
            if font.size(test)[0] <= max_width:
                cur.append(w)
            else:
                if cur:
                    lines.append(" ".join(cur))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
        return lines or [text]

    def draw_ability_bar(self, game: Game) -> None:
        """Draw the bottom ability bar (delegates to AbilityBarWidget)."""
        if not hasattr(self, "ability_bar_widget") or self.ability_bar_widget is None:
            self.ability_bar_widget = AbilityBarWidget()

        ctx = WidgetContext(surface=self.surface, game=game, scene=None, renderer=self)
        self.ability_bar_widget.rect = pygame.Rect(
            0,
            self.height - self.ability_bar_height,
            self.width,
            self.ability_bar_height,
        )
        self.ability_bar_widget.layout(ctx)
        self.ability_bar_widget.draw(ctx)


    def _draw_ability_icon(self, rect: pygame.Rect, ability_or_action, game: Game) -> None:
        """Draw an ability icon from either an Ability or an action name."""
        action = getattr(ability_or_action, "action", ability_or_action)
        preview = getattr(ability_or_action, "preview_geom", None)
        surf = self._render_action_icon(action, game, (rect.w, rect.h), preview_geom=preview)
        self.surface.blit(surf, rect.topleft)

    def _draw_ability_icon_for_bar(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        ability,
        game: Game,
    ) -> None:
        """Adapter so AbilityBarRenderer can remain decoupled from this renderer.

        The bar passes us a target surface and rect; we re-use the same
        icon rendering logic but blit onto the provided surface instead of
        assuming self.surface.
        """
        action = getattr(ability, "action", ability)
        preview = getattr(ability, "preview_geom", None)
        surf = self._render_action_icon(action, game, (rect.w, rect.h), preview_geom=preview)
        surface.blit(surf, rect.topleft)




    def start_dungeon(self, game: Game) -> None:
        """
        Prepare renderer state for entering the dungeon loop.
        Called once per DungeonScene.run() before the frame loop.
        """
        self.quit_requested = False
        # Some callers already set pause_requested; make sure it's consistent.
        if not hasattr(self, "pause_requested"):
            self.pause_requested = False
        else:
            self.pause_requested = False

        # Reset camera each time we enter the dungeon loop (new game / reload).
        self.reset_camera(game)

        # Start target cursor at player position (scene/ui_state drives it)
        player = game.actors[game.player_id]
        if getattr(self, "ui_state", None) is not None:
            self.ui_state.target_cursor = player.pos  # type: ignore[attr-defined]




    # ------------------------------------------------------------------ #
    # Per-frame drawing
    # ------------------------------------------------------------------ #

    def draw_dungeon_frame(self, game: Game) -> None:
        """
        Draw one dungeon frame (no event polling, no main loop).
        Scenes call this once per tick.
        """
        # refactor: renderer should consume scene-provided state; drop ability/hover logic here.

        self.draw_world_zoomed(game)
        self.draw_lorenz_overlay(game)

        self.draw_pattern_overlay(game)
        self.draw_push_preview(game)
        self.draw_place_overlay(game)
        self.draw_activation_overlay(game)
        self.draw_aim_overlay(game)
        # Unified entity rendering: items + actors together.
        renderables = game.renderables_current()
        self.draw_entities(game.world, renderables)
        self.draw_target_cursor(game)
        self.draw_look_overlay(game)

        # HUD (status header, log panel, ability bar) is now routed
        # through a generic widget. For the moment this just forwards to the
        # existing draw_status/draw_log/draw_ability_bar methods, so the
        # visual result is unchanged; it simply gives us a composable entry
        # point for future layout work.
        ctx = WidgetContext(
            surface=self.surface,
            game=game,
            scene=None,
            renderer=self,
        )
        self.hud_widget.layout(ctx)
        self.hud_widget.draw(ctx)

        # HUD-adjacent overlays still live directly on the renderer for now.
        self.draw_ignite_overlay(game)
        self.draw_regrow_overlay(game)
        # Ability bar is drawn by the HUD widget.
        config_open = self._ui_attr("config_open", None)
        config_action = self._ui_attr("config_action", None)
        if config_open and config_action:
            self.draw_config_overlay(game)

        # Urgent overlay is now handled by UrgentMessageScene.
        self._present()







    def render(self, game: Game) -> None:
        """
        Legacy entry point kept for compatibility. The renderer no longer owns
        an event loop; scenes should drive input and call draw_dungeon_frame().
        """
        # Ensure fonts/surfaces are ready, then draw a single frame.
        self.start_dungeon(game)
        self.draw_dungeon_frame(game)















    def _change_zoom(self, delta_steps: int, pos: Tuple[int, int]) -> None:
        # delta_steps: mouse wheel y (positive zoom in), pos in surface coords
        mx, my = pos

        # We treat zoom as a continuous *world-scale* factor (pixels per world-tile).
        # Local rendering still clamps to a minimum readable font size, but the global
        # zone-grid renderer uses the full float scale so we can zoom out to the
        # full world map extent.
        #
        # Keep the world point under the cursor stable while zooming.
        base_x, base_y = self._map_origin_base()
        self.origin_x = base_x + self.pan_x
        self.origin_y = base_y + self.pan_y

        cur_scale = float(self.base_tile) * float(self.zoom)
        cur_scale = max(1e-6, cur_scale)

        wx = (mx - self.origin_x) / cur_scale
        wy = (my - self.origin_y) / cur_scale

        # Tunables
        ZOOM_RATE = 0.15          # base wheel sensitivity
        ZOOM_DAMP_EXP = 0.6      # >0 slows zoom more at small zoom values

        # Compute a damped zoom multiplier
        damp = max(0.05, self.zoom ** ZOOM_DAMP_EXP)
        zoom_factor = 1.0 + delta_steps * ZOOM_RATE * damp

        new_zoom = float(self.zoom) * zoom_factor

        # Allow *very* deep zoom-out so the whole overworld can fit on screen.
        # (Local layer will fade away long before this becomes illegible.)
        new_zoom = max(0.002, min(6.0, new_zoom))
        if abs(new_zoom - float(self.zoom)) < 1e-6:
            return

        new_scale = float(self.base_tile) * float(new_zoom)
        new_scale = max(1e-6, new_scale)

        self.zoom = float(new_zoom)

        # Local-map glyph tile size: clamp for readability.
        self.tile = max(8, int(round(new_scale)))

        # refresh helper surfaces (fonts stay constant size)
        self.edges_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self.verts_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        # map font scales with tile size; UI fonts remain constant
        self.map_font = pygame.font.SysFont("consolas", max(8, int(self.tile)))

        # rescale currency icon for map tiles when zoom changes
        if getattr(self, "bismuth_icon", None) is not None:
            try:
                self.bismuth_icon_map = pygame.transform.smoothscale(self.bismuth_icon, (self.tile, self.tile))
            except Exception:
                self.bismuth_icon_map = None

        # adjust pan so world point under cursor stays under cursor
        target_origin_x = mx - wx * new_scale
        target_origin_y = my - wy * new_scale
        self.pan_x = float(target_origin_x - base_x)
        self.pan_y = float(target_origin_y - base_y)


    def _get_glow_sprite(self, radius: int, color: Tuple[int, int, int]) -> pygame.Surface:
        key = (radius, color)
        cached = self.glow_cache.get(key)
        if cached is not None:
            return cached
        size = max(4, radius * 4)
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        cx = cy = size // 2
        # softer, smaller halo
        for r, alpha in (
            (int(radius * 1.4), 40),
            (radius, 130),
            (int(radius * 0.65), 220),
        ):
            if r <= 0:
                continue
            col = (*color, alpha)
            pygame.draw.circle(surf, col, (cx, cy), r)
        self.glow_cache[key] = surf
        return surf

    def _lerp_color(self, c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
        t = max(0.0, min(1.0, t))
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    def _log_debug(self, msg: str) -> None:
        """No-op placeholder retained for call-site compatibility."""
        return

    def _render_action_icon(
        self,
        action: str,
        game: Game,
        size: Tuple[int, int],
        overrides: Dict[str, object] | None = None,
        preview_geom: Dict[str, object] | None = None,
    ) -> pygame.Surface:
        """Render a tiny illustrative icon for an action, respecting current params and optional overrides."""
        pad = 2
        surf = pygame.Surface((max(8, size[0] - 2 * pad), max(8, size[1] - 2 * pad)), pygame.SRCALPHA)
        w, h = surf.get_size()

        def to_px(x: float, y: float) -> Tuple[int, int]:
            return (int(x * w), int(y * h))

        def draw_vertices(points, strong_idx=None):
            for i, (x, y) in enumerate(points):
                pos = to_px(x, y)
                if strong_idx is not None and i == strong_idx:
                    pygame.draw.circle(surf, (255, 230, 120), pos, 3)
                else:
                    pygame.draw.circle(surf, (200, 240, 255), pos, 2)

        def draw_lines(points, segs, color=(180, 230, 255)):
            for a, b in segs:
                pygame.draw.aaline(surf, color, to_px(*points[a]), to_px(*points[b]))

        geom = preview_geom or action_preview_geometry(action, game, overrides)
        extra = geom or {}
        verts = geom.get("verts", []) if geom else []
        segs = geom.get("segs", []) if geom else []

        if action == "reset":
            pygame.draw.line(surf, (200, 140, 140), (4, h // 2), (w - 4, h // 2), 2)
            pygame.draw.line(surf, (200, 140, 140), (4, h // 2 + 6), (w - 4, h // 2 + 6), 2)
        elif action == "meditate":
            pygame.draw.circle(surf, (180, 200, 255), (w // 2, h // 2), max(4, w // 3), width=2)
            pygame.draw.circle(surf, (120, 180, 255), (w // 2, h // 2), max(2, w // 6))

        if verts:
            # dotted segments if requested
            dotted = extra.get("dotted", []) if extra else []
            for a, b in segs:
                if (a, b) in dotted or (b, a) in dotted:
                    ax, ay = verts[a]
                    bx, by = verts[b]
                    steps = 6
                    for i in range(steps):
                        if i % 2 == 1:
                            continue
                        t0 = i / steps
                        t1 = (i + 1) / steps
                        px0 = ax + (bx - ax) * t0
                        py0 = ay + (by - ay) * t0
                        px1 = ax + (bx - ax) * t1
                        py1 = ay + (by - ay) * t1
                        pygame.draw.aaline(surf, (180, 230, 255), to_px(px0, py0), to_px(px1, py1))
                    continue
                color = (180, 230, 255)
                if extra and extra.get("rainbow"):
                    # Map along-segment gradient based on index
                    try:
                        idx = segs.index((a, b))
                    except ValueError:
                        idx = 0
                    colors = [
                        (255, 0, 0),
                        (255, 165, 0),
                        (255, 255, 0),
                        (0, 128, 0),
                        (0, 0, 255),
                        (75, 0, 130),
                        (238, 130, 238),
                    ]
                    color = colors[idx % len(colors)]
                elif extra and extra.get("green_levels"):
                    try:
                        idx = segs.index((a, b))
                    except ValueError:
                        idx = 0
                    max_idx = max(1, len(segs) - 1)
                    t = idx / max_idx
                    gcol = 255
                    val = int(255 * (1 - t))
                    color = (val, gcol, val)
                pygame.draw.aaline(surf, color, to_px(*verts[a]), to_px(*verts[b]))
            if extra and extra.get("circle"):
                rad_norm = extra.get("radius", 1.5) if extra else 1.5
                max_rad_norm = 4.0
                rfrac = min(1.0, rad_norm / max_rad_norm)
                radius_px = int(min(w, h) * (0.15 + 0.25 * rfrac))
                pygame.draw.circle(surf, (120, 200, 255), (w // 2, h // 2), radius_px, width=2)
            strong = extra.get("strong") if extra else []
            draw_vertices(verts, strong_idx=strong[0] if strong else None)
            if extra and extra.get("boxes"):
                for idx in extra["boxes"]:
                    if 0 <= idx < len(verts):
                        p = to_px(*verts[idx])
                        box_size = max(6, min(w, h) // 4)
                        rect_box = pygame.Rect(p[0] - box_size // 2, p[1] - box_size // 2, box_size, box_size)
                        pygame.draw.rect(surf, (180, 220, 255), rect_box, 1)

        return surf

    def teardown(self) -> None:
        pygame.quit()

    def draw_config_overlay(self, game: Game) -> None:
        # UI state now lives on DungeonUIState; renderer has no local fallback.
        action_name = self._ui_attr("config_action", None)
        if not action_name:
            return
        params = game.param_view(action_name)
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        row_heights = []
        for p in params:
            if action_name == "koch" and p["key"] == "flip":
                row_heights.append(80)
            else:
                row_heights.append(36)
        panel_w = int(self.width * 0.5)
        panel_h = min(int(self.height * 0.9), int(80 + sum(row_heights) + 20))
        panel_x = (self.width - panel_w) // 2
        panel_y = (self.height - panel_h) // 2
        pygame.draw.rect(overlay, (20, 20, 40, 230), pygame.Rect(panel_x, panel_y, panel_w, panel_h))
        pygame.draw.rect(overlay, (200, 200, 240, 240), pygame.Rect(panel_x, panel_y, panel_w, panel_h), 2)
        title = self.big_label(f"Configure {action_name}")
        overlay.blit(title, (panel_x + 16, panel_y + 12))
        y = panel_y + 50

        sel_idx = self._ui_attr("config_selection", 0)
        for i, p in enumerate(params):
            sel = (i == sel_idx)
            col = self.sel if sel else self.fg
            # custom render for Koch mirror flag
            if action_name == "koch" and p["key"] == "flip":
                label = self.font.render("Mirror", True, col)
                overlay.blit(label, (panel_x + 20, y))
                icon_size = 48
                icon_gap = 14
                icon_y = y + label.get_height() + 6
                height_val = game.get_param_value("koch", "height")
                allowed_idx = p.get("allowed_idx", 0)
                cur_idx = p.get("current_idx", 0)
                total_icons_w = 2 * icon_size + icon_gap
                start_x = panel_x + 20
                for idx_val, flip_val in enumerate((False, True)):
                    icon = self._render_action_icon(
                        "koch", game, (icon_size, icon_size), overrides={"height": height_val, "flip": flip_val}
                    )
                    box = pygame.Rect(start_x + idx_val * (icon_size + icon_gap), icon_y, icon_size, icon_size)
                    overlay.blit(icon, box.topleft)
                    border = self.sel if idx_val == cur_idx else (160, 170, 200)
                    if allowed_idx < idx_val:
                        border = (140, 90, 90)
                    pygame.draw.rect(overlay, border, box, 2)
                if p["next_req"]:
                    req_text = self.small_font.render(f"Next: {p['next_req']}", True, (255, 120, 120))
                    overlay.blit(
                        req_text,
                        (start_x + total_icons_w + 18, icon_y + icon_size // 2 - req_text.get_height() // 2),
                    )
                y += icon_size + label.get_height() + 20
                continue

            line = f"{p['label']}: {p['value']}"
            text = self.font.render(line, True, col)
            overlay.blit(text, (panel_x + 20, y))
            if p["next_req"]:
                req_text = self.small_font.render(f"Next: {p['next_req']}", True, (255, 120, 120))
                overlay.blit(req_text, (panel_x + panel_w - req_text.get_width() - 20, y + 4))
            y += 36
        hint = self.small_font.render("Left/Right adjust, Up/Down select, Enter/Esc close", True, self.fg)
        overlay.blit(hint, (panel_x + 16, panel_y + panel_h - 30))
        self.surface.blit(overlay, (0, 0))

    def big_label(self, text: str) -> pygame.Surface:
        return pygame.font.SysFont("consolas", 24, bold=True).render(text, True, self.fg)



    def _present(self) -> None:
        """Blit render surface to display with letterboxing (no stretch, aspect preserved).

        If a global VisualProfile is set (e.g. from a curse), use it to
        transform the entire game view (flip, rotate, etc.) as a final step.
        """
        dw, dh = self.display.get_size()
        sw, sh = self.surface.get_size()
        scale = min(dw / sw, dh / sh)
        new_w = int(sw * scale)
        new_h = int(sh * scale)
        ox = max(0, (dw - new_w) // 2)
        oy = max(0, (dh - new_h) // 2)

        # Keep letterbox info for mouse unprojection and other helpers.
        self.lb_off = (ox, oy)
        self.lb_scale = scale

        self.display.fill((0, 0, 0))

        # Pre-scale the logical game surface into the letterboxed panel.
        if scale != 1.0:
            panel = pygame.transform.smoothscale(self.surface, (new_w, new_h))
        else:
            # Copy to avoid surprising mutations if we transform.
            panel = self.surface.copy()

        panel_rect = pygame.Rect(ox, oy, new_w, new_h)
        self.visual_fx.update()
        panel_rect = self.visual_fx.apply_present_rect(panel_rect)

        # Global named effects (e.g. mirror curse) are applied to the entire panel here.
        global_names = getattr(self.visual_fx, "global_effects", []) or []
        if not global_names:
            self.display.blit(panel, panel_rect.topleft)
        # Apply overlay lane to the whole screen (global curses/blessings)


        else:
            global_visual = build_visual_profile(VisualProfile(), global_names)
            # Overlay lane (particles, scanlines, etc.)
            apply_surface_overlays(self, panel, panel.get_rect(), global_names)
            # Tint lane (for effects that only define a color modifier, e.g. syrupy)
            apply_screen_tint_overlays(self, panel, global_names)

            apply_visual_panel(
                base_surface=self.display,
                logical_surface=panel,
                window_rect=panel_rect,
                visual=global_visual,
            )



        pygame.display.flip()


    def _set_flash(self, text: str, color: Tuple[int, int, int] = (255, 120, 120), duration_ms: int = 2000) -> None:
        self.flash_text = text
        self.flash_color = color
        self.flash_until_ms = pygame.time.get_ticks() + duration_ms
        
        
        
    def apply_shake(self, amplitude: float = 12.0, duration_ms: int = 250):
        self.visual_fx.trigger_shake(amplitude_px=float(amplitude), duration_ms=int(duration_ms))
