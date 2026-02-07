"""Pygame-based ASCII-style renderer with ability bar and targeting.

YOGA REFACTOR NOTES (see vision_documents/the_yoga.txt):
------------------------------------------------------------------------
This renderer is transitioning from zone-local to ABS-based coordinates.

Current Status:
- Player position uses abs_pos for camera centering (see draw(), ~line 370)
- Entity rendering derives screen position from ABS via camera transform
- Pattern/rune rendering still uses zone-local anchor in some paths

YOGA Stage 2 - Centralize Coordinate Transforms (IN PROGRESS):
- [DONE] Created helper APIs: screen_to_abs_tile(), screen_to_abs_tile_int(), abs_tile_to_screen_px()
- [TODO] Consolidate all tile->screen conversions to use these helpers
- [TODO] Eliminate ad-hoc coordinate math scattered across draw methods

Key Invariant (Yoga Compliant):
- Renderer should ONLY project from ABS world state to screen pixels
- It must not "repair" or "invent" positions from zone-local data
- If something draws wrong, the bug is upstream (game state), not here

See vision_documents/spring_cleaning.txt SLICE 4 for related map/camera refactor.
------------------------------------------------------------------------
"""
import pygame
import math
import random
import time
from pathlib import Path
from typing import Tuple, List, Dict
from edgecaster.render.overmap_lod import OvermapLodRenderer
from edgecaster.math_utils import smoothstep_range, lerp_rgb

from edgecaster.game import Game
from edgecaster.state.world import World
from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns.library import action_preview_geometry
from edgecaster.systems.tooltips import resolve_action_tooltip
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
        # Continuous world scale (pixels per world-tile). This may be very large when zooming into micro-realms.
        self.tile_px = float(tile)
        # Clamped glyph/icon size used for fonts/icons (keeps rendering sane at extreme zoom).
        self.glyph_px = int(tile)
        self.origin_x = 0
        self.origin_y = 0
        self.pan_x = 0.0
        self.pan_y = 0.0
        # Camera interaction timestamp (used to defer expensive sampling while actively panning/zooming)
        self._last_camera_input_ms = 0
        # Follow mode: keep player anchored at a screen offset (0,0 == center).
        # Right-drag updates this offset so the player stays at a chosen on-screen location.
        self.camera_follow = True
        self.camera_follow_offset_px = (0.0, 0.0)

        # Cache rendered glyph surfaces so we don"t call font.render() per cell per frame.
        # Key: (font_px, ch, r, g, b, alpha)
        self._glyph_surf_cache = {}
        self._glyph_surf_cache_max = 4096
        # Sprite/icon caches (tiles mode + UI previews)
        # Quantize icon sizes to reduce smoothscale churn during zoom.
        self._icon_size_bin_px = 1  # 2px bins keeps zoom smooth with bounded caches, but gives a weird jitter so I'm putting it back to 1
        self._icon_idle_ms = 80     # ms; defer expensive sprite cache fills while camera is moving

        # Icon caches:
        #   raw:      path -> Surface (convert_alpha once at load)
        #   scaled:   (path, size_bin) -> Surface
        #   composed: (path, size_bin, eff_sig) -> Surface  (icon + overlays + visual panel)
        self._entity_icon_cache_raw = {}
        self._entity_icon_cache_scaled = {}
        self._entity_icon_cache_composed = {}
        self._entity_icon_missing_paths = set()
        self._entity_icon_cache_raw_max = 512
        self._entity_icon_cache_scaled_max = 1024
        self._entity_icon_cache_composed_max = 1024

        # Reused surface for entity fade blending (avoid per-frame allocations)
        self._entities_fade_surface = None

        # Cache scaled bismuth icons by glyph_px (map icon scaling is surprisingly expensive).
        self._bismuth_icon_map_cache = {}
        self._last_zoom_glyph_px = int(self.glyph_px)
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
        # sealing rune overlay
        self.seal_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        # Lorenz attractor overlay
        self.lorenz_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        # Lorenz view defaults; simulation state now lives in game/lorenz.py
        self.lorenz_scale = 0.18          # maps x,y to tile offsets
        self.lorenz_radius_tiles = 7      # max aura radius in tiles
        # cached glow sprites: key = (radius_px, col

        # cached glow sprites: key = (radius_px, color_tuple)
        self.glow_cache: Dict[Tuple[int, Tuple[int, int, int]], pygame.Surface] = {}
        # cached sparkle VFX sprites
        self._radial_cache: Dict[Tuple[int, Tuple[int, int, int]], pygame.Surface] = {}
        self._starburst_cache: Dict[Tuple[int, int, Tuple[int, int, int]], pygame.Surface] = {}
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






        # Zoomed-out overmap LOD renderer (terrain only).
        # This keeps heavy sampling/caching logic out of ascii.py.
        try:
            sx, sy = self.zone_grid_slots
        except Exception:
            sx, sy = (3, 2)
        self.overmap_lod = OvermapLodRenderer(
            get_font=self._get_cached_map_font,
            slots_x=int(sx),
            slots_y=int(sy),
        )

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


    def _zone_abs_offset(self, game: Game) -> Tuple[int, int]:
        """Return the ABS tile offset of the current loaded zone.

        Renderer math after the FoV/abs_pos refactor treats world coordinates as ABS.
        Many gameplay systems (including targeting + rune patterns) still store tile/world
        positions in the current LevelState's LOCAL frame. This helper bridges LOCAL→ABS
        purely for drawing overlays.
        """
        try:
            zx, zy, _zz = getattr(game, "zone_coord", (0, 0, 0))
            zx = int(zx)
            zy = int(zy)
        except Exception:
            zx = zy = 0
        try:
            zw = int(getattr(game.cfg, "world_width", 0) or 0)
            zh = int(getattr(game.cfg, "world_height", 0) or 0)
        except Exception:
            zw = zh = 0
        if zw <= 0 or zh <= 0:
            return (0, 0)
        return (zx * zw, zy * zh)

    def _local_tile_to_abs(self, game: Game, tile_xy: Tuple[int, int]) -> Tuple[int, int]:
        ox, oy = self._zone_abs_offset(game)
        return (int(tile_xy[0] + ox), int(tile_xy[1] + oy))

    def _local_world_to_abs(self, game: Game, world_xy: Tuple[float, float]) -> Tuple[float, float]:
        ox, oy = self._zone_abs_offset(game)
        return (float(world_xy[0] + ox), float(world_xy[1] + oy))

    # -------------------------------------------------------------------------
    # YOGA Stage 2: Centralized Coordinate Transforms
    # -------------------------------------------------------------------------
    # These helpers replace ad-hoc coordinate math scattered across dungeon.py
    # and other input-handling code. Use these instead of manual calculations.


    def get_camera_abs_rect_and_lod(self, game) -> tuple[tuple[float, float, float, float], float]:
        """
        Return ((ax0,ay0,ax1,ay1), cam_lod) using renderer camera state, without drawing.
        ax/ay are in absolute world-tile coordinates.
        """
        map_origin_x, map_origin_y = self._map_origin_base()

        map_w = self.width - map_origin_x
        map_h = self.height - self.ability_bar_height - map_origin_y
        map_rect = pygame.Rect(map_origin_x, map_origin_y, max(1, map_w), max(1, map_h))

        world_scale = float(getattr(self, "tile_px", float(self.base_tile) * float(getattr(self, "zoom", 1.0))))
        world_scale = max(1e-6, world_scale)

        x0 = (float(map_rect.left) - float(getattr(self, "origin_x", map_origin_x))) / world_scale
        y0 = (float(map_rect.top) - float(getattr(self, "origin_y", map_origin_y))) / world_scale
        x1 = (float(map_rect.right) - float(getattr(self, "origin_x", map_origin_x))) / world_scale
        y1 = (float(map_rect.bottom) - float(getattr(self, "origin_y", map_origin_y))) / world_scale

        abs_rect = (float(x0), float(y0), float(x1), float(y1))

        try:
            cam_lod = math.log2(float(self.base_tile) / world_scale)
        except Exception:
            cam_lod = 0.0

        return abs_rect, float(cam_lod)




    def screen_to_abs_tile(self, screen_px: Tuple[float, float]) -> Tuple[float, float]:
        """Convert screen pixels to ABS world-tile coordinates (floating-point).

        Use this for sub-tile precision (e.g., vertex targeting, smooth positioning).
        """
        sx, sy = screen_px
        t = max(1, self.tile)
        return ((sx - self.origin_x) / t, (sy - self.origin_y) / t)

    def screen_to_abs_tile_int(self, screen_px: Tuple[float, float]) -> Tuple[int, int]:
        """Convert screen pixels to ABS world-tile coordinates (integer, floored).

        Use this for tile-based targeting (e.g., terminus placement, rune casting).
        """
        sx, sy = screen_px
        t = max(1, self.tile)
        return (int((sx - self.origin_x) // t), int((sy - self.origin_y) // t))

    def abs_tile_to_screen_px(self, abs_x: float, abs_y: float) -> Tuple[float, float]:
        """Convert ABS world-tile coordinates to screen pixels.

        Use this for positioning UI elements at world locations.
        """
        t = max(1, self.tile)
        return (abs_x * t + self.origin_x, abs_y * t + self.origin_y)

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
        # Base origin before pan.
        # IMPORTANT: this must NOT depend on self.tile (zoom), or the map will "slide"
        # down/up as you zoom, creating huge black bars at high zoom.
        map_origin_x = 8

        # Keep any extra padding based on the *base* tile size (or make it a constant),
        # so it stays stable across zoom levels.
        stable_pad = int(self.base_tile * 4)   # was int(self.tile * 4)

        map_origin_y = self.top_bar_height + 8 + stable_pad
        return map_origin_x, map_origin_y



    def pan_by_px(self, dx: float, dy: float) -> None:
        """Pan the camera by a pixel delta (logical surface pixels).

        Used for right-mouse drag panning. Positive dx/dy shifts the map origin
        right/down (i.e., the world appears to move with the mouse).
        """
        self.pan_x += float(dx)
        self.pan_y += float(dy)
        # If we're following the player, treat the pan as an offset update so
        # the player remains at a fixed screen position.
        if getattr(self, "camera_follow", False):
            try:
                ox, oy = self.camera_follow_offset_px
            except Exception:
                ox, oy = 0.0, 0.0
            self.camera_follow_offset_px = (float(ox) + float(dx), float(oy) + float(dy))

        try:
            self._last_camera_input_ms = pygame.time.get_ticks()
        except Exception:
            self._last_camera_input_ms = 0

    def center_camera_on_player(
        self,
        game: Game,
        *,
        snap_zoom: bool = True,
        target_offset_px: tuple[float, float] | None = None,
    ) -> None:
        """Center the camera on the player.

        IMPORTANT:
        - World positioning must use the *true* world scale (tile_px / camera.tile_px),
          not the clamped glyph/font size. Otherwise at extreme zoom (micro realms),
          recenter + targeting overlays drift toward the upper-left.

        glyph_px stays clamped strictly for font/icon legibility.
        """
        try:
            player = game.actors[game.player_id]
            ap = getattr(player, "abs_pos", None)
            if ap is not None:
                px, py = int(ap[0]), int(ap[1])
            else:
                # Legacy fallback: derive abs from current zone/local if possible
                try:
                    px, py = game.abs_from_zone_local(game.zone_coord, player.pos)
                except Exception:
                    px, py = player.pos
        except Exception:
            return


        cam = getattr(self, "camera", None)

        if snap_zoom:
            # Snap to LoD 0 zoom immediately.
            self.zoom = 1.0
            if cam is not None:
                try:
                    cam.zoom = 1.0
                except Exception:
                    pass

            new_scale = float(self.base_tile) * float(self.zoom)

            # True world scale
            self.tile_px = float(new_scale)

            # Glyph/font scale (clamped)
            self.glyph_px = int(max(8, min(96, round(new_scale))))

            # CRITICAL: self.tile is used broadly for positioning; keep it world-scale.
            self.tile = float(self.tile_px)

            self.map_font = pygame.font.SysFont("consolas", int(self.glyph_px), bold=False)

            if getattr(self, "bismuth_icon", None) is not None:
                try:
                    self.bismuth_icon_map = pygame.transform.smoothscale(
                        self.bismuth_icon, (int(self.glyph_px), int(self.glyph_px))
                    )
                except Exception:
                    self.bismuth_icon_map = None

        map_origin_x, map_origin_y = self._map_origin_base()
        map_w = self.width - self.log_panel_width
        map_h = self.height - self.ability_bar_height - map_origin_y

        offset_x = offset_y = 0.0
        if target_offset_px is not None:
            try:
                offset_x = float(target_offset_px[0])
                offset_y = float(target_offset_px[1])
            except Exception:
                offset_x = offset_y = 0.0

        cx = map_origin_x + map_w // 2 + offset_x
        cy = map_origin_y + map_h // 2 + offset_y

        world_scale = float(getattr(self, "tile_px", getattr(cam, "tile_px", self.base_tile)))
        world_scale = max(1.0, world_scale)

        self.pan_x = float(cx - map_origin_x - float(px) * world_scale)
        self.pan_y = float(cy - map_origin_y - float(py) * world_scale)

        # Keep TileCamera in sync if you're using it elsewhere.
        if cam is not None:
            try:
                cam.pan_x = float(self.pan_x)
                cam.pan_y = float(self.pan_y)
            except Exception:
                pass


    def reset_camera(self, game: Game) -> None:
        """Hard-reset pan/zoom to sane defaults and center on player."""
        self.pan_x = 0.0
        self.pan_y = 0.0
        # Reset follow offset to center.
        self.camera_follow_offset_px = (0.0, 0.0)
        # Camera interaction timestamp (used to defer expensive sampling while actively panning/zooming)
        self._last_camera_input_ms = 0

        # Cache rendered glyph surfaces so we don"t call font.render() per cell per frame.
        # Key: (font_px, ch, r, g, b, alpha)
        self._glyph_surf_cache = {}
        self._glyph_surf_cache_max = 4096

        # Cache scaled bismuth icons by glyph_px (map icon scaling is surprisingly expensive).
        self._bismuth_icon_map_cache = {}
        self._last_zoom_glyph_px = int(self.glyph_px)

        cam = getattr(self, "camera", None)
        if cam is not None:
            try:
                cam.pan_x = 0.0
                cam.pan_y = 0.0
            except Exception:
                pass

        self.zoom = 1.0
        if cam is not None:
            try:
                cam.zoom = 1.0
            except Exception:
                pass

        new_scale = float(self.base_tile) * float(self.zoom)

        # True world scale
        self.tile_px = float(new_scale)

        # Glyph/font scale (clamped)
        self.glyph_px = int(max(8, min(96, round(new_scale))))

        # CRITICAL: positioning uses world-scale
        self.tile = float(self.tile_px)

        self.map_font = pygame.font.SysFont("consolas", int(self.glyph_px), bold=False)

        if getattr(self, "bismuth_icon", None) is not None:
            try:
                self.bismuth_icon_map = pygame.transform.smoothscale(
                    self.bismuth_icon, (int(self.glyph_px), int(self.glyph_px))
                )
            except Exception:
                self.bismuth_icon_map = None

        self.center_camera_on_player(game, snap_zoom=False)

        
    def _sync_zoom_dependent_resources(self) -> None:
        """Update any cached resources that depend on current camera zoom.

        Kept as a single call-site so camera refactors don't require chasing
        down tile/font/icon updates throughout the renderer.
        """
        # World-tile -> screen pixels (true world scale).
        try:
            tpx = float(getattr(self.camera, "tile_px", self.base_tile))
        except Exception:
            tpx = float(self.base_tile)

        self.tile_px = float(tpx)
        self.tile = float(tpx)  # keep legacy alias world-scale

        # Clamp glyph/font size for legibility (NOT used for world placement).
        glyph_px = int(round(self.tile_px))
        glyph_px = max(8, min(96, glyph_px))
        self.glyph_px = glyph_px

        # Map glyph font scales with zoom; keep style consistent (NOT bold).
        try:
            self.map_font = pygame.font.SysFont("consolas", int(glyph_px), bold=False)
        except Exception:
            self.map_font = pygame.font.SysFont("consolas", max(1, int(glyph_px)))

        # Rescale map-sized icons when zoom changes (use clamped glyph size).
        if getattr(self, "bismuth_icon", None) is not None:
            try:
                self.bismuth_icon_map = pygame.transform.smoothscale(
                    self.bismuth_icon, (int(glyph_px), int(glyph_px))
                )
            except Exception:
                self.bismuth_icon_map = None





    def draw_world_zoomed(self, game: Game) -> None:
        """Draw geography using an arbitrary power-of-two LOD stack.

        Philosophical rule: LoD 0 is *not special*.
        Every LOD renders the same way: a rigid grid aligned to world-tile coordinates,
        sampled via the fast numpy overmap renderer and cached per cell.

        At any moment we render at most two adjacent LOD layers and cross-fade between them.
        Entities are drawn separately (and can later pick their own native LOD).
        """
        import math

        # Localize optional imports for speed inside inner loops.
        try:
            from edgecaster.systems.zones import get_zone_for_render as _get_zone_for_render
        except Exception:
            _get_zone_for_render = None

        # Always clear first to avoid trails while blending.
        self.surface.fill(self.bg)

        # In tiles mode we want terrain tiles to be authoritative (no ASCII '.' fallbacks in virgin space).
        # If the tiles pipeline isn't available, ASCII glyph LOD remains the fallback.
        try:
            from edgecaster.render.terrain_tiles import get_terrain_tile_surface  # noqa: F401
            self.prefer_terrain_tiles = True
        except Exception:
            self.prefer_terrain_tiles = False


        # Use the SAME map rect / origin logic as draw_world_local so the camera/pan math
        # stays consistent at every LOD.
        map_origin_x, map_origin_y = self._map_origin_base()
        map_w = self.width - self.log_panel_width
        map_h = self.height - self.ability_bar_height - map_origin_y
        map_rect = pygame.Rect(map_origin_x, map_origin_y, max(1, map_w), max(1, map_h))

        # Apply pan/zoom from the authoritative TileCamera (post-abs_pos refactor).
        # origin_x/origin_y are the screen position of ABSOLUTE world tile (0,0).
        cam = getattr(game, "camera", None)
        if cam is not None and hasattr(cam, "map_origin_px") and hasattr(cam, "tile_px"):
            try:
                self.origin_x, self.origin_y = cam.map_origin_px((float(map_origin_x), float(map_origin_y)))
                # Keep renderer pan mirrors in sync for any legacy callers.
                try:
                    self.pan_x = float(getattr(cam, "pan_x", self.pan_x))
                    self.pan_y = float(getattr(cam, "pan_y", self.pan_y))
                    self.zoom = float(getattr(cam, "zoom", self.zoom))
                except Exception:
                    pass
                world_scale = float(max(1, int(getattr(cam, "tile_px", int(self.base_tile)))))
            except Exception:
                self.origin_x = map_origin_x + self.pan_x
                self.origin_y = map_origin_y + self.pan_y
                world_scale = float(getattr(self, "tile_px", float(self.base_tile) * float(getattr(self, "zoom", 1.0))))
        else:
            self.origin_x = map_origin_x + self.pan_x
            self.origin_y = map_origin_y + self.pan_y
            world_scale = float(getattr(self, "tile_px", float(self.base_tile) * float(getattr(self, "zoom", 1.0))))

        world_scale = max(1e-6, float(world_scale))


        # ---------------------------------------------------------------------
        # Choose the two adjacent LODs to render.
        #
        # Each LOD is a cell size in world tiles: ..., 1/4, 1/2, 1, 2, 4, 8, ...
        # We want cell_px ~= target_glyph_px, so cell_tiles ~= target / world_scale.
        # ---------------------------------------------------------------------
        target_glyph_px = float(getattr(self, "lod_target_glyph_px", self.base_tile) or self.base_tile)
        raw = target_glyph_px / world_scale  # desired cell size in tiles (can be < 1 for micro LODs)

        LOD_RADIX = getattr(self, "lod_radix", 2)
        lod_f = math.log(max(1e-12, raw), LOD_RADIX)
        # Cache a continuous camera LoD scalar for entity rendering.
        # Convention: LoD 0 corresponds to 1 world-tile per glyph. Positive => zoomed out.
        self._camera_lod_cont = float(lod_f)
        self._camera_lod_radix = int(LOD_RADIX)


        lod0 = int(math.floor(lod_f))
        lod1 = lod0 + 1
        frac = float(lod_f - lod0)  # 0..1

        # Allow negative LODs (micro realms) and positive LODs (macro realms).
        lod0 = max(-12, min(lod0, 12))
        lod1 = max(-12, min(lod1, 12))
        if lod1 == lod0:
            frac = 0.0

        cell0 = float(LOD_RADIX ** lod0)
        cell1 = float(LOD_RADIX ** lod1)

        # Anchor both blended layers to the same snapped origin to prevent drift.
        snap_base = float(max(cell0, cell1))

        # Smooth the blend so it's not too "linear" around the midpoint.
        blend = smoothstep_range(0.0, 1.0, frac)

        # Small deadband: when we're effectively at one LOD, don't spend time on the other.
        eps = 0.06
        if blend <= eps:
            a0, a1 = 1.0, 0.0
        elif blend >= 1.0 - eps:
            a0, a1 = 0.0, 1.0
        else:
            a0, a1 = 1.0 - blend, blend


        # -------------------------------------------------------------
        # PERF / DEBUG TOGGLE: disable terrain LOD crossfade
        #
        # When enabled, render EXACTLY ONE LOD layer (no alpha blits).
        # You can toggle this from anywhere by setting:
        #   game.debug_disable_lod_crossfade = True
        # or:
        #   renderer.debug_disable_lod_crossfade = True
        # -------------------------------------------------------------
        if bool(getattr(game, "debug_disable_lod_crossfade", True) or getattr(self, "debug_disable_lod_crossfade", True)):
            # Pick the "nearest" LOD so snapping feels sensible.
            # blend is 0..1 where 0 is lod0, 1 is lod1.
            if blend >= 0.5:
                cell0, lod0 = cell1, lod1
            # Force single-layer render
            a0, a1 = 1.0, 0.0
            cell1, lod1 = cell0, lod0
            snap_base = float(cell0)


        # -----------------------------------------------------------------
        # Entity visibility / fading
        #
        # Goal:
        # - When zooming OUT (macro LODs), entities should fade with the 1-tile
        #   layer so they disappear as the terrain becomes "global".
        # - When zooming IN (micro LODs / negative LoD), entities should NOT
        #   immediately disappear just because the 1-tile layer stopped participating
        #   in the terrain blend. Instead, keep them visible until a single tile
        #   becomes *huge* on screen, then fade out gracefully.
        # -----------------------------------------------------------------
        raw = target_glyph_px / world_scale  # desired cell size in tiles

        if raw > 1.0:
            # Zoomed out: do NOT globally fade entities with the terrain blend.
            # Size-based LOD visibility is handled per-entity in _compute_entity_visibility_alpha().
            ent_a = 1.0
        else:
            # Zoomed in: only fade out when a single tile starts to fill most of the map viewport.
            min_dim = float(min(map_rect.w, map_rect.h))
            # ratio ~= 1 when one tile ~= entire map height/width.
            ratio = float(world_scale) / max(1.0, min_dim)
            start = float(getattr(self, "entity_micro_fade_start", 0.55))
            end = float(getattr(self, "entity_micro_fade_end", 0.90))
            ent_a = 1.0 - smoothstep_range(start, end, ratio)

        ent_a = max(0.0, min(1.0, ent_a))
        self._local_fade_alpha = int(round(255 * ent_a))

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

        if a1 > 0.0 and abs(cell1 - cell0) > 1e-12:
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
        Infer ABSOLUTE world tile coordinates (global) from renderer pan/origin.

        Post-abs_pos refactor invariant:
        - origin_x/origin_y are the screen-space origin for ABSOLUTE world tile (0,0).
        - Therefore we must NOT add any zone_coord offsets here.

        This is purely the inverse of the current screen transform:
            abs = (screen_center - origin) / world_scale
        """
        cx_px = float(map_rect.centerx)
        cy_px = float(map_rect.centery)

        dx_px = cx_px - float(self.origin_x)
        dy_px = cy_px - float(self.origin_y)

        s = max(1e-6, float(world_scale))
        abs_wx = dx_px / s
        abs_wy = dy_px / s
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
        cell_w_tiles: float,
        cell_h_tiles: float,
        lod_id,
        snap_base_tiles: float | None = None,
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
        # IMPORTANT: game.camera is pixel-pan based and does not store canonical world_x/world_y.
        # We infer center from the camera transform (screen center -> local tiles) + zone offset.
        cam = getattr(game, "camera", None)
        if cam is not None and hasattr(cam, "world_from_screen_px"):
            try:
                # TileCamera.world_from_screen_px returns ABSOLUTE world-tile coords
                # (post-abs_pos refactor). Do NOT add zone offsets here.
                abs_wx, abs_wy = cam.world_from_screen_px(
                    (float(map_rect.centerx), float(map_rect.centery)),
                    base_origin_px=(float(map_rect.x), float(map_rect.y)),
                )
                abs_wx = float(abs_wx)
                abs_wy = float(abs_wy)
            except Exception:
                abs_wx, abs_wy = self._infer_world_center_from_renderer(
                    game, map_rect=map_rect, world_scale=world_scale, zone_w=zone_w, zone_h=zone_h
                )
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


        # Absolute world size in tiles (global coordinates). Used for clamping at edges to avoid
        # sampling out-of-bounds (persistent black bars at max pan / max zoom).
        cfg = getattr(game, "cfg", None)
        total_w = total_h = 0
        if cfg is not None:
            try:
                total_w = int(cfg.world_map_screens * cfg.world_width)
                total_h = int(cfg.world_map_screens * cfg.world_height)
            except Exception:
                total_w = total_h = 0

        # Clamp the visible window to world bounds.
        
        CLAMP_VIEW_TO_WORLD = False

        if CLAMP_VIEW_TO_WORLD:
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

        cell_w_tiles = max(1e-6, float(cell_w_tiles))
        cell_h_tiles = max(1e-6, float(cell_h_tiles))

        # Snap view_min to cell boundaries so the grid is rigid/stable.
        #
        # IMPORTANT: when we cross-fade between two adjacent LODs (e.g. 4-tiles and 8-tiles),
        # we must *anchor* both layers to the same snapped origin, otherwise their grids can
        # appear to drift relative to each other while blending.
        snap_w = max(1e-6, float(snap_base_tiles if snap_base_tiles is not None else cell_w_tiles))
        snap_h = max(1e-6, float(snap_base_tiles if snap_base_tiles is not None else cell_h_tiles))
        
        snap_min_wx = math.floor(view_min_wx / snap_w) * snap_w
        snap_min_wy = math.floor(view_min_wy / snap_h) * snap_h


        # Clamp snap_min as well: floor-snapping can push the sampled window out of bounds at
        # the far edge even when view_min is clamped. This is the classic "bars at max X/Y" case.

        if CLAMP_VIEW_TO_WORLD:

            if total_w > 0:
                max_snap_min_wx = max(0.0, float(total_w) - view_span_wx)
                snap_min_wx = max(0.0, min(snap_min_wx, max_snap_min_wx))
            if total_h > 0:
                max_snap_min_wy = max(0.0, float(total_h) - view_span_wy)
                snap_min_wy = max(0.0, min(snap_min_wy, max_snap_min_wy))

        # Fractional scroll within the snapped cell grid (in pixels).
        # This makes panning/zooming move the grid smoothly while keeping it rigidly aligned
        # to world-tile cell boundaries (no 'stuck' grid / glyph swimming).
        offset_px_x = (view_min_wx - snap_min_wx) * world_scale
        offset_px_y = (view_min_wy - snap_min_wy) * world_scale

        # How many cells cover the viewport (+padding to avoid "island in black").
        PAD_CELLS = 2

        cols = max(1, int(math.ceil(view_span_wx / float(cell_w_tiles))) + 3 + 2 * PAD_CELLS)
        rows = max(1, int(math.ceil(view_span_wy / float(cell_h_tiles))) + 3 + 2 * PAD_CELLS)

        base_cx = int(math.floor(snap_min_wx / float(cell_w_tiles))) - PAD_CELLS
        base_cy = int(math.floor(snap_min_wy / float(cell_h_tiles))) - PAD_CELLS

        pad_px_x = float(PAD_CELLS) * float(cell_w_tiles) * float(world_scale)
        pad_px_y = float(PAD_CELLS) * float(cell_h_tiles) * float(world_scale)

        # IMPORTANT: sampling and drawing must agree on the same padded origin.
        # We draw PAD_CELLS worth of extra cells around the viewport to avoid
        # "island in black" artifacts when panning/zooming. That means the
        # sampled overmap window must also start at the padded origin.
        sample_min_wx = float(base_cx) * float(cell_w_tiles)
        sample_min_wy = float(base_cy) * float(cell_h_tiles)


        # Cell size in pixels.
        cell_px_w_f = max(1e-6, float(cell_w_tiles) * world_scale)
        cell_px_h_f = max(1e-6, float(cell_h_tiles) * world_scale)
        # For font sizing and some bounds checks we still use integer pixels.
        cell_px_w = max(1, int(round(cell_px_w_f)))
        cell_px_h = max(1, int(round(cell_px_h_f)))

        p = getattr(game, "overmap_params", None)
        if not p or total_w <= 1 or total_h <= 1:
            return out

        # Per-cell cache: (lod_id, cx, cy, overmap_sig) -> (ch, (r,g,b))
        sig = self._overmap_signature(game)
        cache = getattr(self, "_lod_cell_cache", None)
        if cache is None:
            cache = {}
            self._lod_cell_cache = cache


        # Consider the camera 'interactive' for a short window after pan/zoom input.
        # While interactive, we try hard to avoid expensive overmap sampling; we will
        # still render 1:1 local dungeon tiles correctly, and fill gaps with cheap
        # placeholders until the cache warms.
        try:
            now_ms = pygame.time.get_ticks()
            interactive = (now_ms - int(getattr(self, '_last_camera_input_ms', 0))) < 120
        except Exception:
            interactive = False
        # Decide whether we must sample this viewport into the per-cell cache.
        #
        # IMPORTANT: we must not only check the top-left cell. When panning slightly,
        # base_cx/base_cy can stay the same while new columns/rows enter on the far edge.
        # If we only sample when (base_cx,base_cy) is missing, we'll get "black bars"
        # until the camera moves far enough to change base_cx/base_cy.
        need_sample = False
        if cache:
            # Check the perimeter (O(cols+rows)) which is cheap and catches new edge cells.
            last_c = cols - 1
            last_r = rows - 1
            for cc in range(cols):
                if (lod_id, cell_w_tiles, cell_h_tiles, base_cx + cc, base_cy + 0, sig) not in cache:
                    need_sample = True
                    break
                if (lod_id, cell_w_tiles, cell_h_tiles, base_cx + cc, base_cy + last_r, sig) not in cache:
                    need_sample = True
                    break
            if not need_sample:
                for rr in range(rows):
                    if (lod_id, cell_w_tiles, cell_h_tiles, base_cx + 0, base_cy + rr, sig) not in cache:
                        need_sample = True
                        break
                    if (lod_id, cell_w_tiles, cell_h_tiles, base_cx + last_c, base_cy + rr, sig) not in cache:
                        need_sample = True
                        break
        else:
            need_sample = True

        if need_sample and (not interactive):
            try:
                from edgecaster.overmap_accel import render_overmap_fields_climate
                import numpy as np

                span_wx = float(cols * cell_w_tiles)
                span_wy = float(rows * cell_h_tiles)

                # Iter budget: more detail when zoomed in.
                zoom_factor = max(1.0, float(total_w) / max(1e-9, span_wx))
                iters = 48 + int(24 * math.log(max(1.0, zoom_factor)))
                iters = int(max(24, min(iters, 320)))

                elev2, biome2, _peak2 = render_overmap_fields_climate(
                    px_w=int(cols),
                    px_h=int(rows),
                    total_w=total_w,
                    total_h=total_h,
                    j_min_x=float(p["view_min_jx"]),
                    j_max_x=float(p["view_max_jx"]),
                    j_min_y=float(p["view_min_jy"]),
                    j_max_y=float(p["view_max_jy"]),
                    view_min_wx=float(sample_min_wx),
                    view_min_wy=float(sample_min_wy),
                    view_span_wx=float(span_wx),
                    view_span_wy=float(span_wy),
                    visual_c=p["visual_c"],
                    iters=iters,
                    corruption_level=float(p.get("corruption_level", 0.0) or 0.0),
                    corruption_seed=int(p.get("corruption_seed", 1337) or 1337),
                    spline_weight=float(p.get("corruption_spline_weight", 0.0) or 0.0),
                    hotspots=p.get("corruption_hotspots", None),
                    anchors=p.get("corruption_anchors", None),
                    climate_config=p.get("climate_config", None),
                )

                # --- Elevation glyph ladder (Phase 1: simple thresholds; tweak later) ---
                # bins define the *upper* threshold of each rung (digitize -> 0..6)
                bins = np.asarray([0.12, 0.22, 0.28, 0.56, 0.72, 0.86], dtype=np.float32)
                glyphs = np.asarray(
                    [ord("≈"), ord("~"), ord(","), ord("."), ord('"'), ord("#"), ord("^")],
                    dtype=np.int16,
                )
                e_idx = np.digitize(elev2.astype(np.float32, copy=False), bins, right=False).astype(np.int16, copy=False)
                glyph_codes = glyphs[e_idx]

                # --- Biome fg color (Phase 1: biome palette only; no extra tint) ---
                from edgecaster.biome import BIOME_COLORS as _BIOME_COLORS

                lut = np.empty((256, 3), dtype=np.uint8)
                lut[:, :] = (200, 200, 200)
                for k, v in _BIOME_COLORS.items():
                    kk = int(k)
                    if 0 <= kk < 256:
                        lut[kk, 0] = int(v[0])
                        lut[kk, 1] = int(v[1])
                        lut[kk, 2] = int(v[2])

                bi = biome2.astype(np.int16, copy=False)
                bi = np.clip(bi, 0, 255).astype(np.uint8, copy=False)
                col = lut[bi]


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

                        # Cache biome + relief category too (used by look/inspect, especially in god vision)
                        biome_id = int(bi[rr, cc])
                        elev_cat = int(e_idx[rr, cc])
                        # Cache corruption intensity too (quantized 0..255).
                        # _peak2 is a float field; we clamp and scale so the cache stays compact and overlay-friendly.
                        # Cache corruption intensity quantized to 0..15 (what terrain_tiles expects).
                        # Cache corruption intensity quantized to 0..15 (what terrain_tiles expects).
                        # IMPORTANT: threshold small background noise to 0 so overlays don't appear everywhere.
                        try:
                            c = float(_peak2[rr, cc])
                        except Exception:
                            c = 0.0

                        if c < 0.0:
                            c = 0.0
                        elif c > 1.0:
                            c = 1.0

                        CORR_VIS_THRESHOLD = 0.18  # tweak 0.12..0.30 depending on how localized you want it

                        if c <= CORR_VIS_THRESHOLD:
                            corr_q = 0
                        else:
                            # Remap so "just above threshold" starts at low intensities
                            c2 = (c - CORR_VIS_THRESHOLD) / (1.0 - CORR_VIS_THRESHOLD)
                            corr_q = int(c2 * 15.0 + 0.5)
                            if corr_q < 1:
                                corr_q = 1
                            elif corr_q > 15:
                                corr_q = 15


                        cache[k] = (ch, color, biome_id, elev_cat, corr_q)



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

        # Get player position, world, and zone offset for FOV-based dimming
        player_pos = None
        world = None
        zone_offset_x = 0
        zone_offset_y = 0
        fov_radius = 8  # Match the FOV radius from _update_fov
        god_vision = bool(getattr(game, "god_vision", False) or getattr(game, "debug_god_vision", False) or getattr(game, "dev_god_vision", False) or getattr(world, "god_vision", False))  # Developer mode: show all tiles

        # IMPORTANT: game state has shifted a bit over time (actors dict vs game.player,
        # world map vs zone). We support both so FOV/visibility stays correct.
        try:
            # Newer/most common path
            player = game.actors[game.player_id]
            player_pos = getattr(player, 'pos', None)
            lvl = game._level()
            world = getattr(lvl, 'world', None)
        except Exception:
            player = None

        if player_pos is None:
            try:
                player = getattr(game, 'player', None)
                player_pos = getattr(player, 'xy', None) or getattr(player, 'pos', None)
            except Exception:
                player_pos = None

        if world is None:
            try:
                # Some builds expose a current zone on game.world
                wobj = getattr(game, 'world', None)
                world = getattr(wobj, 'zone', None) or getattr(game, 'zone', None) or wobj
            except Exception:
                world = None

        try:
            zone_x, zone_y, _zone_z = getattr(game, 'zone_coord', (0, 0, 0))
            zone_offset_x = int(zone_x) * int(zone_w)
            zone_offset_y = int(zone_y) * int(zone_h)
        except Exception:
            zone_offset_x = 0
            zone_offset_y = 0

        # When rendering at 1 world-tile per cell, prefer the *actual* local-map glyphs
        # (walls, stairs, site structures, etc.) over the overmap/biome sampler output.
        #
        # This keeps the pretty zoomed LoD terrain while ensuring authored/placed
        # features are still visible.
        biome_glyph_to_color = None
        special_glyph_to_color = None

        from edgecaster import biome as _biome

        biome_id_to_color = None
        structure_glyph_to_color = {
            "█": (160, 160, 160),  # walls: fixed gray (Phase 1)
            "+": (200, 170, 120),  # door-ish (leave as a visible special for now)
            ">": (220, 220, 255),  # stairs down
            "<": (220, 220, 255),  # stairs up
            "=": (255, 240, 160),  # console/platform
        }

        try:
            from edgecaster.biome import BIOME_COLORS as _BC
            # Make a simple 0..255 LUT for speed.
            lut = [(200, 200, 200)] * 256
            for k, v in _BC.items():
                kk = int(k)
                if 0 <= kk < 256:
                    lut[kk] = (int(v[0]), int(v[1]), int(v[2]))
            biome_id_to_color = lut
        except Exception:
            biome_id_to_color = None


        for rr in range(rows):
            cy = base_cy + rr
            py_f = map_rect.y + rr * cell_h_tiles * world_scale - offset_px_y - pad_px_y
            # Skip rows wholly above/below the viewport (with 1-cell padding).
            if py_f + float(cell_px_h_f) < float(rect_y - cell_px_h) or py_f > float(rect_y + rect_h + cell_px_h):
                continue

            for cc in range(cols):
                cx = base_cx + cc
                px_f = map_rect.x + cc * cell_w_tiles * world_scale - offset_px_x - pad_px_x
                if px_f + float(cell_px_w_f) < float(rect_x - cell_px_w) or px_f > float(rect_x + rect_w + cell_px_w):
                    continue

                px = int(round(px_f))
                py = int(round(py_f))

                k = (lod_id, cell_w_tiles, cell_h_tiles, cx, cy, sig)
                val = cache.get(k)
                if val:
                    ch = val[0]
                    orig_color = val[1]
                    # Optional metadata (newer cache entries):
                    #   val[2] = biome_id, val[3] = elev_cat
                    # We don't need them for drawing, but other systems may query.
                else:
                    # Cache miss (often during active panning/zooming before sampling runs).
                    # In tiles mode, do NOT draw ASCII placeholder glyphs into virgin/unrendered space.
                    # Leave it transparent so the base surface (black/fog) shows until the cache warms.
                    if bool(getattr(self, "prefer_terrain_tiles", False)):
                        ch = None
                        orig_color = getattr(self, 'dim', (140, 140, 150))
                    else:
                        ch = '·'
                        orig_color = getattr(self, 'dim', (140, 140, 150))



                # Apply FOV-based dimming using the same tile.visible flags as entities.
                # This works at all zoom levels:
                # - cell_w_tiles < 1: multiple cells per tile (zoomed in)
                # - cell_w_tiles == 1: 1:1 mapping (normal zoom)
                # - cell_w_tiles > 1: one cell covers multiple tiles (zoomed out)

                # Start with cached color (sampler truth)
                color = orig_color

                # If available, prefer the cached biome_id (sampler truth) over realized tile.biome_id
                cached_biome_id = None
                if val and len(val) > 2:
                    try:
                        cached_biome_id = int(val[2])
                    except Exception:
                        cached_biome_id = None

                if player_pos is not None and world is not None:
                    # For zoomed out (cell > 1 tile), sample center of cell
                    # For zoomed in (cell < 1 tile), sample the tile containing this cell
                    if cell_w_tiles >= 1.0:
                        # Center of the cell in global tile coords
                        global_x = int(cx * cell_w_tiles + cell_w_tiles * 0.5)
                        global_y = int(cy * cell_h_tiles + cell_h_tiles * 0.5)
                    else:
                        # Sub-tile cell: just get the tile this cell is within
                        global_x = int(cx * cell_w_tiles)
                        global_y = int(cy * cell_h_tiles)

                    # Resolve absolute tile -> (zone coord, local) without assuming the current zone owns terrain.
                    # This is the core "quantum observer" invariant: abs-space is canonical; zones are just caches.
                    tile = None
                    try:
                        zx0, zy0, zz0 = getattr(game, "zone_coord", (0, 0, 0))
                        zz0 = int(zz0)
                    except Exception:
                        zx0 = zy0 = 0
                        zz0 = 0

                    # Compute the zone coordinate that contains (global_x, global_y). Works for negatives.
                    try:
                        zxt = int(math.floor(float(global_x) / float(zone_w)))
                        zyt = int(math.floor(float(global_y) / float(zone_h)))
                    except Exception:
                        zxt = int(global_x // zone_w)
                        zyt = int(global_y // zone_h)

                    local_x = int(global_x - zxt * int(zone_w))
                    local_y = int(global_y - zyt * int(zone_h))

                    # Try to peek realized tile (for structure overrides only). Do NOT treat missing tiles as "world ends".
                    if zxt == int(zx0) and zyt == int(zy0):
                        # Fast path: current loaded world.
                        if world is not None and getattr(world, "in_bounds", None) and world.in_bounds(local_x, local_y):
                            tile = world.get_tile(local_x, local_y)
                    else:
                        # Render-peek other loaded chunks only (never instantiate).
                        try:
                            lvl2 = _get_zone_for_render(game, (int(zxt), int(zyt), int(zz0))) if _get_zone_for_render else None
                        except Exception:
                            lvl2 = None
                        if lvl2 is not None:
                            w2 = getattr(lvl2, "world", None)
                            if w2 is not None and getattr(w2, "in_bounds", None) and w2.in_bounds(local_x, local_y):
                                tile = w2.get_tile(local_x, local_y)

                    # --- Fog/visibility truth comes from ABS-space, not zone tile flags ---
                    abs_tx = int(global_x)
                    abs_ty = int(global_y)
                    try:
                        explored = bool(god_vision) or bool(game.is_abs_explored(abs_tx, abs_ty, depth=zz0))
                    except Exception:
                        explored = bool(god_vision)
                    if (not explored) and (not god_vision):
                        continue

                    # Base terrain color from sampler (biome_id), regardless of whether a realized tile exists.
                    base_color = orig_color
                    if biome_id_to_color is not None:
                        try:
                            bid = cached_biome_id
                            if bid is None and tile is not None:
                                bid = int(getattr(tile, "biome_id", 0) or 0)
                            if bid is not None and 0 <= int(bid) < 256:
                                base_color = biome_id_to_color[int(bid)]
                        except Exception:
                            pass

                    # At 1:1 scale, override glyph/color for realized STRUCTURES only.
                    if tile is not None and abs(cell_w_tiles - 1.0) < 1e-6:
                        try:
                            tg = str(tile.glyph or "")
                            if tg:
                                tg = tg[0]
                            if tg in structure_glyph_to_color:
                                ch = tg
                                base_color = structure_glyph_to_color[tg]
                        except Exception:
                            pass

                    # Visibility (dimming) from ABS-space FoV truth
                    try:
                        visible_now = bool(god_vision) or bool(game.is_abs_visible(abs_tx, abs_ty, depth=zz0))
                    except Exception:
                        visible_now = bool(god_vision)

                    # Alias used by the terrain-tiles underlay path below.
                    abs_visible = bool(visible_now)

                    if visible_now:
                        color = base_color
                    else:
                        r, g, b = base_color
                        color = (int(r * 0.4), int(g * 0.4), int(b * 0.4))
                else:
                    # If we have no world/player context, just draw the cached sampler result.
                    # (This keeps renderer stable even in menus or transitional scenes.)
                    pass


                # --- Terrain tiles: base tile underlay, glyphs only for structures ---
                # Tie terrain-tiles to the same "graphics mode" switch as entity sprites.
                # If the user is in ASCII mode (prefer_sprite_icons=False), we must NOT draw tile underlays.
                prefer_terrain_tiles = bool(getattr(self, "prefer_terrain_tiles", False)) and bool(
                    getattr(self, "prefer_sprite_icons", True)
                )

                drawn_tile = False
                if prefer_terrain_tiles and val:
                    try:
                        from edgecaster.render.terrain_tiles import get_terrain_tile_surface

                        # Newer cache entries include explicit biome/elev/corr metadata:
                        #   (ch, color, biome_id, elev_cat, corr_q)
                        # Older entries (e.g. OvermapLodRenderer) only store:
                        #   (ch, color)
                        # In that case, infer biome/elev from the glyph + biome anchor color so tiles-mode
                        # still previews correctly while the more expensive sampler cache warms up.
                        if len(val) > 3:
                            cached_biome_id = int(val[2])
                            cached_elev_cat = int(val[3])
                            cached_corr_q = int(val[4]) if len(val) > 4 else 0
                        else:
                            # Elev category ladder matches edgecaster.biome BIOME_GLYPHS order.
                            glyph_to_elev_cat = {
                                "≈": 0,
                                "~": 1,
                                ",": 2,
                                ".": 3,
                                '"': 4,
                                "#": 5,
                                "^": 6,
                            }
                            cached_elev_cat = int(glyph_to_elev_cat.get(ch, 3))
                            cached_corr_q = 0

                            cached_biome_id = 0
                            try:
                                # Fast path: exact reverse map from anchor color -> biome_id
                                rev = getattr(self, "_biome_color_to_id", None)
                                if rev is None:
                                    rev = {}
                                    if biome_id_to_color is not None:
                                        for i, col_t in enumerate(biome_id_to_color):
                                            # Many LUT entries may duplicate; that's fine.
                                            if col_t not in rev:
                                                rev[col_t] = int(i)
                                    self._biome_color_to_id = rev

                                if orig_color in rev:
                                    cached_biome_id = int(rev[orig_color])
                                else:
                                    # Fallback: nearest BIOME_COLORS entry (rare)
                                    from edgecaster.biome import BIOME_COLORS as _BC

                                    r0, g0, b0 = int(orig_color[0]), int(orig_color[1]), int(orig_color[2])
                                    best_id = 0
                                    best_d = 10**18
                                    for k, v in _BC.items():
                                        rr, gg, bb = int(v[0]), int(v[1]), int(v[2])
                                        d = (rr - r0) * (rr - r0) + (gg - g0) * (gg - g0) + (bb - b0) * (bb - b0)
                                        if d < best_d:
                                            best_d = d
                                            best_id = int(k)
                                    cached_biome_id = int(best_id)
                            except Exception:
                                cached_biome_id = 0

                        tile_surf = get_terrain_tile_surface(
                            elev_cat=cached_elev_cat,
                            biome_id=cached_biome_id,
                            w_px=cell_px_w,
                            h_px=cell_px_h,
                            cx=cx,
                            cy=cy,
                            corruption_level=cached_corr_q,
                        )

                        # Apply the SAME fog-of-war dim vibe to terrain tiles.
                        # If the ABS tile is not visible this tick (but *is* explored), dim it just like glyphs.
                        if not god_vision:
                            try:
                                if not abs_visible:
                                    tile_surf = tile_surf.copy()
                                    tile_surf.fill((102, 102, 102, 255), special_flags=pygame.BLEND_RGBA_MULT)  # ~0.4
                            except Exception:
                                pass

                        out.blit(tile_surf, (px, py))
                        drawn_tile = True
                    except Exception:
                        drawn_tile = False


                # In tiles mode: draw glyphs only for structure overrides.
                # In ASCII mode: draw glyphs for everything (original behavior).
                if (ch is not None) and ((not prefer_terrain_tiles) or (ch in structure_glyph_to_color) or (not drawn_tile)):
                    surf = self._get_cached_glyph_surface(font=font, font_px=font_px, ch=ch, color=color, alpha_u8=255)
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


        # In terrain-tiles mode, the zoomed-out terrain underlay is drawn via
        # the terrain tile renderer; this ASCII LOD grid should not draw any
        # placeholder glyphs (which look like '.' in tiles mode).
        if bool(getattr(self, 'prefer_terrain_tiles', False)):
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


        # Absolute world size in tiles (global coordinates). Used for clamping at edges to avoid
        # sampling out-of-bounds (persistent black bars at max pan / max zoom).
        cfg = getattr(game, "cfg", None)
        total_w = total_h = 0
        if cfg is not None:
            try:
                total_w = int(cfg.world_map_screens * cfg.world_width)
                total_h = int(cfg.world_map_screens * cfg.world_height)
            except Exception:
                total_w = total_h = 0

        # Clamp the visible window to world bounds.
        CLAMP_VIEW_TO_WORLD = False

        if CLAMP_VIEW_TO_WORLD:

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
                    surf = self._get_cached_glyph_surface(font=font, font_px=font_px, ch=ch, color=color, alpha_u8=255)
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

        if bool(getattr(self, 'prefer_terrain_tiles', False)):
            # In tiles mode, leave this layer empty; the terrain underlay handles
            # unknown/virgin regions as black.
            return grid_surf

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


    def _get_cached_glyph_surface(
        self,
        *,
        font: 'pygame.font.Font',
        font_px: int,
        ch: str,
        color: tuple[int, int, int],
        alpha_u8: int = 255,
    ) -> 'pygame.Surface':
        """Return a cached glyph surface.

        This is a major performance win for zoomed-out LOD grids where we may
        draw thousands of glyphs per frame.

        Key includes alpha so blended layers can reuse surfaces too.
        """
        try:
            r, g, b = int(color[0]), int(color[1]), int(color[2])
        except Exception:
            r, g, b = 255, 255, 255
        key = (int(font_px), str(ch), r, g, b, int(alpha_u8))
        surf = self._glyph_surf_cache.get(key)
        if surf is not None:
            return surf

        surf = font.render(ch, True, (r, g, b))
        if int(alpha_u8) < 255:
            surf = surf.copy()
            surf.set_alpha(int(alpha_u8))

        self._glyph_surf_cache[key] = surf
        if len(self._glyph_surf_cache) > int(self._glyph_surf_cache_max):
            drop_n = max(64, len(self._glyph_surf_cache) - int(self._glyph_surf_cache_max))
            for _ in range(drop_n):
                try:
                    self._glyph_surf_cache.pop(next(iter(self._glyph_surf_cache)))
                except Exception:
                    break

        return surf



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



    # ------------------------------------------------------------------
    # Entity icon / sprite rendering (renderer-agnostic hook)
    # ------------------------------------------------------------------

    def _is_camera_interactive(self, now_ms: int) -> bool:
        """True if we're within the 'recent camera input' window."""
        try:
            last = int(getattr(self, "_last_camera_input_ms", 0))
            idle_ms = int(getattr(self, "_icon_idle_ms", 80))
            return (int(now_ms) - last) < idle_ms
        except Exception:
            return False

    def _quantize_icon_px(self, size_px: int) -> int:
        """Quantize icon size to reduce cache churn during smooth zoom."""
        try:
            size_px = int(size_px)
        except Exception:
            size_px = 1
        size_px = max(1, size_px)
        bin_px = int(getattr(self, "_icon_size_bin_px", 2) or 1)
        if bin_px <= 1:
            return size_px
        # nearest bin
        return max(1, int(round(size_px / float(bin_px))) * bin_px)

    def _trim_cache_dict(self, d: dict, max_n: int) -> None:
        """Bound a dict cache by dropping oldest insertion-order entries."""
        try:
            max_n = int(max_n)
        except Exception:
            return
        if max_n <= 0:
            return
        if len(d) <= max_n:
            return
        drop_n = max(32, len(d) - max_n)
        for _ in range(drop_n):
            try:
                d.pop(next(iter(d)))
            except Exception:
                break
    def _get_nearest_cached_scaled_icon(self, path: str, size_bin: int) -> pygame.Surface | None:
        """During interactive zoom, prefer a nearby cached scaled sprite over glyph fallback.

        This prevents sprite<->glyph flicker while still avoiding scale-on-miss.
        """
        try:
            size_bin = max(1, int(size_bin))
        except Exception:
            size_bin = 1

        bin_px = int(getattr(self, "_icon_size_bin_px", 2) or 1)
        if bin_px <= 0:
            bin_px = 1

        cache = getattr(self, "_entity_icon_cache_scaled", None) or {}
        # Exact match first (cheap)
        s = cache.get((path, int(size_bin)))
        if s is not None:
            return s

        # Search outward by bins: -1,+1,-2,+2,... (bounded constant work)
        # NOTE: we keep this small so it stays cheap in the hot loop.
        MAX_STEPS = 10
        for i in range(1, MAX_STEPS + 1):
            lo = size_bin - i * bin_px
            hi = size_bin + i * bin_px
            if lo >= 1:
                s = cache.get((path, int(lo)))
                if s is not None:
                    return s
            s = cache.get((path, int(hi)))
            if s is not None:
                return s

        return None

    def _resolve_entity_icon_path(self, ent) -> str | None:
        """Resolve sprite/icon path by convention."""
        # Priority order:
        #  1) ent.sprite_path / ent.sprite / ent.icon_path
        #  2) ent.tags['icon_path'] or ent.tags['icon']
        #  3) ent.tags['currency'] -> assets/icons/<currency>.png
        for attr in ("sprite_path", "sprite", "icon_path"):
            v = getattr(ent, attr, None)
            if isinstance(v, str) and v.strip():
                return v.strip()

        tags = getattr(ent, "tags", {}) or {}
        v = tags.get("icon_path") or tags.get("icon")
        if isinstance(v, str) and v.strip():
            return v.strip()

        # Convention fallback for NPCs spawned from NPC defs.
        # This allows npc_id-driven actors (e.g. Chakra Sage) to pick up
        # `assets/icons/<npc_id>.png` without requiring per-actor icon tags.
        npc_id = tags.get("npc_id")
        if isinstance(npc_id, str) and npc_id.strip():
            return f"assets/icons/{npc_id.strip()}.png"

        cur = tags.get("currency")
        if isinstance(cur, str) and cur.strip():
            return f"assets/icons/{cur.strip()}.png"

        return None

    def get_entity_icon_surface(
        self,
        ent,
        *,
        size_px: int,
        scene_effects: list[str] | None = None,
        prefer_sprite: bool = True,
        allow_idle_gating: bool = False,
    ) -> pygame.Surface:
        """Return an RGBA surface representing an entity at a given pixel size.

        If allow_idle_gating=True (used by dungeon map draw), we avoid filling missing
        scaled/composed sprite caches while the camera is actively moving.
        """
        size_px = max(1, int(size_px))
        size_bin = self._quantize_icon_px(size_px)

        eff = concat_effect_names(scene_effects or [], effect_names_from_obj(ent))
        now_ms = pygame.time.get_ticks()
        interactive = bool(allow_idle_gating) and self._is_camera_interactive(int(now_ms))

        path = None
        if prefer_sprite and bool(getattr(self, "prefer_sprite_icons", True)):
            path = self._resolve_entity_icon_path(ent)

        if path:
            # (A) If effects requested, try composed cache first
            if eff:
                ckey = (path, int(size_bin), str(eff))
                comp = self._entity_icon_cache_composed.get(ckey)
                if comp is not None:
                    return comp

            # (B) Try scaled cache
            skey = (path, int(size_bin))
            scaled = self._entity_icon_cache_scaled.get(skey)

            # While interactive: do NOT scale-on-miss (prevents hitching)
            if scaled is None and interactive:
                # IMPORTANT: keep sprites as sprites (no glyph flicker).
                # Reuse a nearby cached scaled sprite if possible.
                near = self._get_nearest_cached_scaled_icon(path, size_bin)
                if near is not None:
                    # If effects are active, still skip compose-on-miss while moving.
                    return near
                # If we have nothing cached yet, fall through to the normal behavior:
                # we still avoid composing effects while interactive (handled below),
                # but we allow the first scale so the entity doesn't flicker into glyphs.

            if scaled is None:
                scaled = self._load_and_scale_icon(path, size_px=size_bin)

            if scaled is not None:
                # While interactive: do NOT compose-on-miss (avoid per-entity surface work)
                if eff and interactive:
                    return scaled

                if eff:
                    out = self._apply_entity_effects_to_icon(ent, scaled, size_px=size_bin, eff=eff)
                    try:
                        self._entity_icon_cache_composed[ckey] = out
                        if len(self._entity_icon_cache_composed) > int(self._entity_icon_cache_composed_max):
                            self._trim_cache_dict(self._entity_icon_cache_composed, int(self._entity_icon_cache_composed_max))
                    except Exception:
                        pass
                    return out

                return scaled

        # Fallback: render a glyph (ASCII)
        return self._render_entity_glyph_icon(ent, size_px=size_px, eff=eff)

    def _load_and_scale_icon(self, path: str, *, size_px: int) -> pygame.Surface | None:
        """Load an icon (cached) and return a smoothly scaled copy (cached by size bin)."""
        try:
            size_px = max(1, int(size_px))
            size_bin = self._quantize_icon_px(size_px)

            key = (path, int(size_bin))
            cached = self._entity_icon_cache_scaled.get(key)
            if cached is not None:
                return cached

            if path in self._entity_icon_missing_paths:
                return None

            raw = self._entity_icon_cache_raw.get(path)
            if raw is None:
                p = Path(path)
                if not p.is_absolute():
                    p = (self._project_root / p).resolve()
                raw = pygame.image.load(str(p)).convert_alpha()
                self._entity_icon_cache_raw[path] = raw
                if len(self._entity_icon_cache_raw) > int(self._entity_icon_cache_raw_max):
                    self._trim_cache_dict(self._entity_icon_cache_raw, int(self._entity_icon_cache_raw_max))

            scaled = pygame.transform.smoothscale(raw, (int(size_bin), int(size_bin)))
            self._entity_icon_cache_scaled[key] = scaled
            if len(self._entity_icon_cache_scaled) > int(self._entity_icon_cache_scaled_max):
                self._trim_cache_dict(self._entity_icon_cache_scaled, int(self._entity_icon_cache_scaled_max))
            return scaled
        except Exception:
            try:
                self._entity_icon_missing_paths.add(path)
            except Exception:
                pass
            return None

    def _render_entity_glyph_icon(self, ent, *, size_px: int, eff: str) -> pygame.Surface:
        """Glyph fallback for get_entity_icon_surface().

        Optimized: uses cached fonts and cached glyph surfaces (no per-call font.render()).
        """
        glyph = str(getattr(ent, "glyph", "@"))[:1]

        base_color = getattr(self, "fg", (240, 240, 255))
        try:
            _, base_color = self._entity_visual(ent)
        except Exception:
            pass
        color = apply_entity_color_effects(ent, base_color, eff)

        font_px = max(10, int(round(size_px * 0.90)))
        font = self._get_cached_font(font_px)

        # Fast path: no overlays/transforms
        if not eff:
            canvas = pygame.Surface((size_px, size_px), pygame.SRCALPHA)
            gsurf = self._get_cached_glyph_surface(
                font=font, font_px=font_px, ch=glyph, color=color, alpha_u8=255
            )
            gx = (size_px - gsurf.get_width()) // 2
            gy = (size_px - gsurf.get_height()) // 2
            canvas.blit(gsurf, (gx, gy))
            return canvas

        base_rect = pygame.Rect(0, 0, size_px, size_px)
        union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)

        canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
        ox, oy = -union_rect.left, -union_rect.top

        gsurf = self._get_cached_glyph_surface(
            font=font, font_px=font_px, ch=glyph, color=color, alpha_u8=255
        )
        gx = ox + (size_px - gsurf.get_width()) // 2
        gy = oy + (size_px - gsurf.get_height()) // 2
        canvas.blit(gsurf, (gx, gy))

        shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
        apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

        try:
            visual = build_visual_profile(VisualProfile(), eff)
            out = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
            apply_visual_panel(out, canvas, out.get_rect(), visual)
            return out
        except Exception:
            return canvas

    def _apply_entity_effects_to_icon(self, ent, icon: pygame.Surface, *, size_px: int, eff: str) -> pygame.Surface:
        """Apply overlay + geometric effects to a sprite/icon surface.

        Callers should cache this result in hot paths.
        """
        base_rect = pygame.Rect(0, 0, size_px, size_px)
        union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)

        canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
        ox, oy = -union_rect.left, -union_rect.top

        ix = ox + (size_px - icon.get_width()) // 2
        iy = oy + (size_px - icon.get_height()) // 2
        canvas.blit(icon, (ix, iy))

        if eff:
            shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
            apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

        if eff:
            try:
                visual = build_visual_profile(VisualProfile(), eff)
                out = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
                apply_visual_panel(out, canvas, out.get_rect(), visual)
                return out
            except Exception:
                pass

        return canvas


    def _draw_slaver_chains(self, world: World, entities, *, tile_px_f: float) -> None:
        """Draw visual-only chains between slavers and their chained brutes.

        This intentionally does NOT enforce gameplay rules; the chain leash is enforced in `Game._handle_move_or_attack`.

        Tuning knobs are grouped below:
        - `base_w` / `core_w`: chain thickness (pixels)
        - `base_col` / `core_col`: chain color + opacity
        - `sag`: how much the chain droops (pixels)
        - `pip_*`: link "pips" that make it read as a chain
        """
        if tile_px_f <= 0:
            return

        # Build a quick id->actor map from renderables (we only chain actors).
        actor_by_id = {}
        for ent in entities:
            if not hasattr(ent, "faction"):
                continue
            if not getattr(ent, "alive", True):
                continue
            ent_id = getattr(ent, "id", None)
            if isinstance(ent_id, str) and ent_id:
                actor_by_id[ent_id] = ent

        if not actor_by_id:
            return

        screen_rect = pygame.Rect(0, 0, self.width, self.height)
        now = time.monotonic()

        for brute in actor_by_id.values():
            tags = getattr(brute, "tags", None) or {}
            master_id = tags.get("slaver_master_id")
            if not master_id:
                continue

            master = actor_by_id.get(str(master_id))
            if master is None:
                continue

            ax, ay = getattr(master, "pos", (None, None))
            bx, by = getattr(brute, "pos", (None, None))
            if ax is None or ay is None or bx is None or by is None:
                continue

            # Match entity visibility: only draw chains when both endpoints are visible.
            tile_a = world.get_tile(int(ax), int(ay))
            tile_b = world.get_tile(int(bx), int(by))
            if not tile_a or not tile_b or not tile_a.visible or not tile_b.visible:
                continue

            # Tile centers in screen pixels (same mapping used by entity glyph/sprite rendering).
            a_px = (
                float(ax) * tile_px_f + float(self.origin_x) + tile_px_f * 0.5,
                float(ay) * tile_px_f + float(self.origin_y) + tile_px_f * 0.5,
            )
            b_px = (
                float(bx) * tile_px_f + float(self.origin_x) + tile_px_f * 0.5,
                float(by) * tile_px_f + float(self.origin_y) + tile_px_f * 0.5,
            )

            # Quick reject: if the entire chain is off-screen, skip.
            if not screen_rect.inflate(40, 40).collidepoint(int(a_px[0]), int(a_px[1])) and not screen_rect.inflate(40, 40).collidepoint(int(b_px[0]), int(b_px[1])):
                continue

            # Local overlay for alpha (keeps the chain subtle without tinting the whole screen).
            pad = int(max(6, min(18, tile_px_f * 0.35)))
            left = int(min(a_px[0], b_px[0]) - pad)
            top = int(min(a_px[1], b_px[1]) - pad)
            w = int(abs(a_px[0] - b_px[0]) + pad * 2)
            h = int(abs(a_px[1] - b_px[1]) + pad * 2)
            fx_rect = pygame.Rect(left, top, max(2, w), max(2, h)).clip(screen_rect)
            if fx_rect.w <= 1 or fx_rect.h <= 1:
                continue

            overlay = pygame.Surface((fx_rect.w, fx_rect.h), pygame.SRCALPHA)

            axp, ayp = a_px
            bxp, byp = b_px
            dx = bxp - axp
            dy = byp - ayp
            length = math.hypot(dx, dy)
            if length <= 1e-6:
                continue

            # Perpendicular unit for droop.
            px = -dy / length
            py = dx / length

            # TUNING: sag in pixels (droop). We keep it small so it reads as a chain, not a big curve.
            dist_tiles = max(abs(int(bx) - int(ax)), abs(int(by) - int(ay)))
            sag = min(tile_px_f * 0.45, float(dist_tiles) * tile_px_f * 0.18)
            phase = (hash((str(master_id), getattr(brute, "id", ""))) & 0xFFFF) / 65536.0 * math.tau
            sag *= 0.85 + 0.15 * math.sin(now * 2.0 + phase)

            cxp = (axp + bxp) * 0.5 + px * sag
            cyp = (ayp + byp) * 0.5 + py * sag

            # Sample a quadratic Bezier (A -> C -> B).
            steps = int(max(10, min(28, length / 12.0)))
            pts: list[tuple[int, int]] = []
            for i in range(steps + 1):
                t = i / float(steps)
                omt = 1.0 - t
                x = omt * omt * axp + 2.0 * omt * t * cxp + t * t * bxp
                y = omt * omt * ayp + 2.0 * omt * t * cyp + t * t * byp
                pts.append((int(x - fx_rect.x), int(y - fx_rect.y)))

            if len(pts) < 2:
                continue

            # TUNING: thickness/colors (keep thin so it hugs tiles).
            base_w = 2
            core_w = 1
            base_col = (18, 18, 28, 150)   # darker = more subtle
            core_col = (185, 185, 210, 210)

            pygame.draw.lines(overlay, base_col, False, pts, base_w)
            pygame.draw.lines(overlay, core_col, False, pts, core_w)

            # TUNING: chain "links" along the curve.
            pip_r = 1
            pip_col = (210, 210, 235, 200)
            pip_step = max(3, int(len(pts) / max(6, dist_tiles * 3)))
            for j in range(0, len(pts), pip_step):
                pygame.draw.circle(overlay, pip_col, pts[j], pip_r)

            # Endpoint shackles.
            shackle_r = 2
            pygame.draw.circle(overlay, (235, 235, 250, 220), pts[0], shackle_r, 1)
            pygame.draw.circle(overlay, (235, 235, 250, 220), pts[-1], shackle_r, 1)

            self.surface.blit(overlay, fx_rect.topleft)


    def draw_entities(self, game, world: World, entities) -> None:
        """Draw all renderable entities (actors, items, features...) on the map.

        Entity visibility is decided per-entity (size vs camera LoD) and per-tile (visibility / explored),
        not by terrain LOD cross-fade. We keep this function allocation-free and rely on fast culls.
        """
        self._draw_entities_impl(game, world, entities)


    def _draw_entities_impl(self, game, world: World, entities) -> None:
        """Draw all renderable entities (actors, items, features...) on the map.

        Ordering is controlled by an optional 'render_layer' attribute:
        higher layers are drawn later (on top).

        Phase 1 fog-of-war rule:
        - If tile.visible: draw normally
        - Else if tile.explored: draw ONLY entities with tags.remember_in_fog (dimmed)
        - Else: draw nothing
        """
        def layer(ent) -> int:
            e = getattr(ent, "obj", ent)
            if hasattr(e, "faction"):
                return getattr(e, "render_layer", 2)
            return getattr(e, "render_layer", 1)

        entities_sorted = sorted(entities, key=layer)

        tile_px_f = float(getattr(self, "tile_px", float(self.tile)))
        tile_px_i = max(1, int(round(tile_px_f)))

        god_vision = bool(getattr(game, "god_vision", False) or getattr(game, "debug_god_vision", False) or getattr(game, "dev_god_vision", False) or getattr(world, "god_vision", False))

        # Visual-only overlay: slaver chains (drawn underneath actor glyphs/sprites).
        # Note: this currently only draws when both endpoints are visible.
        self._draw_slaver_chains(world, entities_sorted, tile_px_f=tile_px_f)

        # Approx "map viewport" bounds, used as a sane cap for huge zoom-in.
        map_w = max(1, int(self.width - self.log_panel_width))
        map_h = max(1, int(self.height - self.ability_bar_height - self._map_origin_base()[1]))
        min_dim = max(1, min(map_w, map_h))

        # Allow entities to get VERY large, but cap to roughly the visible map area.
        size_cap = int(min_dim * 1.05)

        # Effects active at the scene level (if any)
        scene_eff = getattr(self, "active_visual_effects", None) or []

        for ent in entities_sorted:
            # Camera queries may provide RenderProxy objects spanning zones.
            # A RenderProxy is expected to expose:
            #   - obj: the underlying entity/actor
            #   - abs_x, abs_y: absolute-world tile coords
            #   - zone_coord: (zx,zy,zz) of the owning zone
            #   - local_pos: optional (x,y) within the owning zone (fallback to obj.pos)
            if hasattr(ent, "obj") and hasattr(ent, "abs_x") and hasattr(ent, "abs_y"):
                obj = ent.obj
                try:
                    abs_x = float(ent.abs_x)
                    abs_y = float(ent.abs_y)
                except Exception:
                    continue
                ent_zone = getattr(ent, "zone_coord", getattr(game, "zone_coord", (0, 0, 0)))
                local_pos = getattr(ent, "local_pos", None) or getattr(obj, "pos", None)
                if local_pos is None:
                    continue
                try:
                    lx, ly = int(local_pos[0]), int(local_pos[1])
                except Exception:
                    continue
            else:
                obj = ent

                # Prefer true absolute position if present.
                ap = getattr(obj, "abs_pos", None)
                if ap is not None:
                    try:
                        abs_x = float(ap[0])
                        abs_y = float(ap[1])
                    except Exception:
                        continue

                    # Derive owning chunk + local for fog-of-war tile lookup.
                    try:
                        ent_zone, local_pos = game.zone_local_from_abs((int(round(abs_x)), int(round(abs_y))))
                        lx, ly = int(local_pos[0]), int(local_pos[1])
                    except Exception:
                        # Fallback if conversion isn't available for some reason
                        ent_zone = getattr(game, "zone_coord", (0, 0, 0))
                        # Best-effort local guess
                        pos = getattr(obj, "pos", None)
                        if pos is None:
                            continue
                        lx, ly = int(pos[0]), int(pos[1])

                else:
                    # Legacy fallback: treat as local to current loaded chunk
                    pos = getattr(obj, "pos", None)
                    if pos is None:
                        continue
                    try:
                        lx, ly = int(pos[0]), int(pos[1])
                    except Exception:
                        continue
                    ent_zone = getattr(game, "zone_coord", (0, 0, 0))

                    # Derive abs from current chunk membership
                    try:
                        abs_x, abs_y = game.abs_from_zone_local(ent_zone, (lx, ly))
                        abs_x = float(abs_x)
                        abs_y = float(abs_y)
                    except Exception:
                        # Last resort: assume local==abs (not ideal, but avoids crashing)
                        abs_x = float(lx)
                        abs_y = float(ly)

            # ABS-space camera: abs_x/abs_y already live in the renderer's world coordinate frame.
            x = float(abs_x)
            y = float(abs_y)


            # Fog-of-war tile rules require a tile lookup in the entity's owning zone.
            tile = None
            if hasattr(game, "get_zone_for_render"):
                try:
                    lvl = game.get_zone_for_render(ent_zone)
                except Exception:
                    lvl = None
            else:
                lvl = None

            if lvl is None:
                # Fall back to current world (single-zone).
                if not world.in_bounds(lx, ly):
                    continue
                tile = world.get_tile(lx, ly)
            else:
                wld = getattr(lvl, "world", None)
                if wld is None:
                    continue
                if not wld.in_bounds(lx, ly):
                    continue
                tile = wld.get_tile(lx, ly)

            if not tile:
                continue

            # From here down, treat 'ent' as the underlying object.
            ent = obj

            # In normal vision, never draw anything on unexplored tiles.
            if (not god_vision) and (not getattr(tile, "explored", False)):
                continue

            # Determine whether we are allowed to draw this entity when not currently visible.
            visible_now = bool(getattr(tile, "visible", False)) or god_vision
            draw_in_fog = False

            if not visible_now:
                tags = getattr(ent, "tags", {}) or {}
                if not isinstance(tags, dict):
                    tags = {}
                draw_in_fog = bool(tags.get("remember_in_fog", False))
                if not draw_in_fog:
                    continue

            # If we're drawing from explored fog memory, dim the entity.
            dim_in_fog = (not visible_now) and draw_in_fog
            fog_alpha_u8 = 110  # ~0.43 of 255, matches your terrain 0.4 dim vibe
            base_alpha_u8 = fog_alpha_u8 if dim_in_fog else 255

            px_f = x * tile_px_f + float(self.origin_x)
            py_f = y * tile_px_f + float(self.origin_y)

            # Quick reject (coarse)
            if px_f > float(self.width) or py_f > float(self.height):
                continue
            if px_f + tile_px_f < 0 or py_f + tile_px_f < 0:
                continue

            # -----------------------------
            # 1) Try sprite/icon (general)
            # -----------------------------
            # Phase 0: size-aware LoD visibility + render-only scaling.
            # base_size is the absolute world size (in LoD0 tiles) for the entity root.
            base_size = getattr(ent, "base_size", None)
            if not isinstance(base_size, (int, float)):
                base_size = getattr(ent, "size", 1.0)

            abs_size = getattr(ent, "abs_size", base_size)
            try:
                abs_size = float(abs_size)
            except Exception:
                abs_size = 1.0
            if not (abs_size > 0.0):
                abs_size = 1e-6

            # Continuous camera LoD derived from zoom (radix-2). LoD 0 at zoom==1.
            radix = 2
            try:
                z = float(getattr(self, "zoom", 1.0) or 1.0)
            except Exception:
                z = 1.0
            z = max(1e-12, z)
            cam_lod = float(math.log(1.0 / z, radix))
            try:
                ent_lod = float(math.log(max(1e-12, abs_size), radix))
            except Exception:
                ent_lod = 0.0

            delta = cam_lod - ent_lod

            # Tunables (override per scene if desired).
            dmin = float(getattr(self, "entity_lod_delta_min", -5.0))
            dmax = float(getattr(self, "entity_lod_delta_max", 3.0))
            fade_w = float(getattr(self, "entity_lod_fade_width", 0.45))

            # Visibility alpha in [0..1]
            if delta < dmin - fade_w or delta > dmax + fade_w:
                continue
            if delta < dmin:
                vis_a = smoothstep_range(dmin - fade_w, dmin, delta)
            elif delta > dmax:
                vis_a = 1.0 - smoothstep_range(dmax, dmax + fade_w, delta)
            else:
                vis_a = 1.0

            # Combine LoD visibility fade with fog-memory dimming.
            draw_alpha_u8 = int(round(float(base_alpha_u8) * float(vis_a)))
            if draw_alpha_u8 <= 0:
                continue
            if draw_alpha_u8 > 255:
                draw_alpha_u8 = 255

            # Compute desired pixel size (quantized/cached later by get_entity_icon_surface).
            want_px_f = float(tile_px_f) * float(abs_size)

            # If it would be a ~1-2px speck, skip entirely (performance + avoids subpixel drift).
            min_px_hide = float(getattr(self, "entity_min_px_hide", 2.0))
            min_px_full = float(getattr(self, "entity_min_px_full", 6.0))
            if want_px_f <= min_px_hide:
                continue

            # Smoothly fade small on-screen sprites as we zoom out.
            if want_px_f >= min_px_full:
                pix_a = 1.0
            else:
                pix_a = smoothstep_range(min_px_hide, min_px_full, want_px_f)

            # Apply pixel-size fade to alpha (still before any sprite cache lookups).
            draw_alpha_u8 = int(round(float(draw_alpha_u8) * float(pix_a)))
            if draw_alpha_u8 <= 0:
                continue
            if draw_alpha_u8 > 255:
                draw_alpha_u8 = 255

            want_px = int(round(want_px_f))
            want_px = max(1, min(want_px, size_cap))

            # Centerpoint for the tile
            cx = int(round(px_f + tile_px_f * 0.5))
            cy = int(round(py_f + tile_px_f * 0.5))

            has_sprite_hint = self._resolve_entity_icon_path(ent) is not None

            if has_sprite_hint and bool(getattr(self, "prefer_sprite_icons", True)):
                ent_eff = effect_names_from_obj(ent)
                eff = concat_effect_names(scene_eff, ent_eff)

                # IMPORTANT:
                # Pull a *stable* base sprite from cache (no time-varying scene effects baked in).
                icon = self.get_entity_icon_surface(
                    ent,
                    size_px=want_px,
                    scene_effects=[],          # <- do NOT bake animated effects into the cached icon
                    prefer_sprite=True,
                    allow_idle_gating=True,
                )

                if icon is not None:
                    # If there are no effects at all, keep the fast path.
                    if not eff:
                        if draw_alpha_u8 < 255:
                            try:
                                icon = icon.copy()
                                icon.set_alpha(int(draw_alpha_u8))
                            except Exception:
                                pass
                        rect = icon.get_rect(center=(cx, cy))
                        self.surface.blit(icon, rect.topleft)
                        continue

                    # Otherwise, compose into a per-frame canvas so we can tint + overlay.
                    base_rect = pygame.Rect(0, 0, tile_px_i, tile_px_i)
                    union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)
                    canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
                    ox, oy = -union_rect.left, -union_rect.top

                    # Center sprite on the *tile* rect (not union rect)
                    ix = ox + (tile_px_i - icon.get_width()) // 2
                    iy = oy + (tile_px_i - icon.get_height()) // 2
                    canvas.blit(icon, (ix, iy))

                    now_ms = pygame.time.get_ticks()

                    # 1) Dynamic color effects (bismuth hue drift etc.) as a MULTIPLY tint
                    try:
                        tint = apply_entity_color_effects(ent, (255, 255, 255), eff, now_ms=now_ms)
                        if tint != (255, 255, 255):
                            canvas.fill((tint[0], tint[1], tint[2], 255), special_flags=pygame.BLEND_RGBA_MULT)
                    except Exception:
                        pass

                    # 2) Dynamic overlay lane (sparkles/shimmer/etc.), masked by sprite alpha
                    try:
                        shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
                        overlay = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
                        apply_surface_overlays(ent, overlay, overlay.get_rect(), eff, now_ms=now_ms, rect_by_name=shifted)

                        # Mask overlay alpha by the sprite silhouette alpha so it doesn't become a square.
                        try:
                            import numpy as np
                            oa = pygame.surfarray.pixels_alpha(overlay)
                            ca = pygame.surfarray.pixels_alpha(canvas)
                            oa[:] = ((oa.astype(np.uint16) * ca.astype(np.uint16)) // 255).astype(np.uint8)
                            del oa, ca
                        except Exception:
                            pass

                        canvas.blit(overlay, (0, 0))
                    except Exception:
                        pass

                    if dim_in_fog:
                        try:
                            canvas.set_alpha(int(draw_alpha_u8))
                        except Exception:
                            pass

                    dest = pygame.Rect(
                        int(round(px_f + union_rect.left)),
                        int(round(py_f + union_rect.top)),
                        union_rect.w,
                        union_rect.h,
                    )
                    if draw_alpha_u8 < 255:
                        try:
                            canvas.set_alpha(int(draw_alpha_u8))
                        except Exception:
                            pass
                    self.surface.blit(canvas, dest.topleft)
                    continue


            # -----------------------------
            # 2) Glyph fallback (big!)
            # -----------------------------
            glyph, base_color = self._entity_visual(ent)

            ent_eff = effect_names_from_obj(ent)
            eff = concat_effect_names(scene_eff, ent_eff)
            color = apply_entity_color_effects(ent, base_color, eff)

            if dim_in_fog:
                try:
                    r, g, b = color
                    color = (int(r * 0.4), int(g * 0.4), int(b * 0.4))
                except Exception:
                    pass

            # Let glyph font scale with zoom, but cap it.
            font_px = max(8, min(want_px, 1024))
            font = self._get_cached_map_font(font_px)

            base_rect = pygame.Rect(0, 0, tile_px_i, tile_px_i)
            union_rect, rect_by_name = compute_overlay_union_rect(ent, base_rect, eff)
            canvas = pygame.Surface((union_rect.w, union_rect.h), pygame.SRCALPHA)
            ox, oy = -union_rect.left, -union_rect.top

            # Fast path: no overlays/transforms -> no per-entity canvas allocation
            if not eff:
                gsurf = self._get_cached_glyph_surface(
                    font=font,
                    font_px=font_px,
                    ch=glyph,
                    color=color,
                    alpha_u8=int(draw_alpha_u8),
                )
                gx = int(round(px_f + (tile_px_i - gsurf.get_width()) * 0.5))
                gy = int(round(py_f + (tile_px_i - gsurf.get_height()) * 0.5))
                self.surface.blit(gsurf, (gx, gy))
                continue

            if eff:
                shifted = {name: r.move(ox, oy) for name, r in rect_by_name.items()}
                apply_surface_overlays(ent, canvas, canvas.get_rect(), eff, rect_by_name=shifted)

            if dim_in_fog:
                try:
                    canvas.set_alpha(int(draw_alpha_u8))
                except Exception:
                    pass

            dest = pygame.Rect(
                int(round(px_f + union_rect.left)),
                int(round(py_f + union_rect.top)),
                union_rect.w,
                union_rect.h,
            )
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
        ox, oy = self._zone_abs_offset(game)
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
            ax = (a[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x
            ay = (a[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y
            bx = (b[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x
            by = (b[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y
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
                    col = lerp_rgb(c0, c1, (t0 + t1) * 0.5)
                if not col:
                    col = lerp_rgb(self.pattern_color, self.pattern_color_end, (t0 + t1) * 0.5)
                core_col = (*col, 220)
                pygame.draw.line(self.edges_surface, core_col, (x0, y0), (x1, y1), width=self.edge_width_base)
                pygame.draw.aaline(self.edges_surface, core_col, (x0, y0), (x1, y1))

        # vertices with glow sprites
        base_sprite = self._get_glow_sprite(v_radius, self.pattern_color)
        for vx, vy in verts:
            px = int((vx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
            py = int((vy + oy) * self.tile + self.tile * 0.5 + self.origin_y)
            rect = base_sprite.get_rect(center=(px, py))
            self.verts_surface.blit(base_sprite, rect)

        # composite layers
        self.surface.blit(self.edges_surface, (0, 0))
        self.surface.blit(self.verts_surface, (0, 0))

    def draw_seal_trial_overlay(self, game: Game) -> None:
        """Draw the target seal pattern + highlight the missing chunk.

        Intact edges are rendered dimly so players can see the full target.
        Missing edges pulse brighter to call attention to the damaged segment.
        """
        self.seal_surface.fill((0, 0, 0, 0))
        level = game._level()
        trial = getattr(level, "seal_trial", None)
        if trial is None:
            return
        sealed = bool(getattr(trial, "sealed", False))

        ox, oy = self._zone_abs_offset(game)
        origin = trial.target_anchor
        try:
            verts = project_vertices(trial.target_pattern, origin)
        except Exception:
            verts = []

        if not verts:
            # Throttle debug output so we only log once per second.
            now = pygame.time.get_ticks()
            last = getattr(self, "_seal_overlay_debug_tick", 0)
            if now - last > 1000 and hasattr(game, "_debug"):
                self._seal_overlay_debug_tick = now
                game._debug(
                    "[seal_trials] overlay skipped "
                    f"verts=0 edges={len(getattr(trial.target_pattern, 'edges', []) or [])} "
                    f"root={trial.root_tile} term={trial.terminus_tile}"
                )
            return

        # Pulse speed/strength for the missing edges (skip pulsing if sealed).
        t = pygame.time.get_ticks() / 1000.0
        pulse = 1.0 if sealed else (0.5 + 0.5 * math.sin(t * (math.tau / 1.4)))

        if sealed:
            intact_col = (120, 190, 255, 140)
            missing_dim = intact_col
            missing_bright = intact_col
        else:
            intact_col = (80, 120, 180, 70)
            missing_dim = (40, 40, 60, 140)
            missing_bright = (255, 210, 140, int(120 + 120 * pulse))

        for edge in trial.target_pattern.edges:
            try:
                a = verts[edge.a]
                b = verts[edge.b]
            except Exception:
                continue
            ax = (a[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x
            ay = (a[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y
            bx = (b[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x
            by = (b[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y

            edge_key = (min(edge.a, edge.b), max(edge.a, edge.b))
            if edge_key in trial.missing_edge_keys:
                # Missing chunk: draw a dark baseline + a pulsing highlight.
                pygame.draw.aaline(self.seal_surface, missing_dim, (ax, ay), (bx, by))
                pygame.draw.aaline(self.seal_surface, missing_bright, (ax, ay), (bx, by))
            else:
                pygame.draw.aaline(self.seal_surface, intact_col, (ax, ay), (bx, by))

        self.surface.blit(self.seal_surface, (0, 0))

    def draw_seal_root_hint(self, game: Game) -> None:
        """Draw a pulsing border on the root tile while snapped to the terminus."""
        if not self._ui_attr("seal_snap_active", False):
            return
        root = self._ui_attr("seal_root_hint", None)
        if not root:
            return

        ox, oy = self._zone_abs_offset(game)
        rx, ry = root
        tile_px = max(1, int(round(self.tile)))
        x = int((rx + ox) * self.tile + self.origin_x)
        y = int((ry + oy) * self.tile + self.origin_y)

        t = pygame.time.get_ticks() / 1000.0
        pulse = 0.5 + 0.5 * math.sin(t * (math.tau / 1.6))
        alpha = int(80 + 140 * pulse)
        col = (255, 220, 140, alpha)

        # Two-pass border for a readable "stand here" target.
        pygame.draw.rect(self.surface, col, pygame.Rect(x, y, tile_px, tile_px), width=2)
        inner = tile_px - 4
        if inner > 0:
            pygame.draw.rect(self.surface, col, pygame.Rect(x + 2, y + 2, inner, inner), width=1)

    def draw_seal_trial_status(self, game: Game) -> None:
        """Draw a small status panel describing current seal-trial readiness."""
        level = game._level()
        trial = getattr(level, "seal_trial", None)
        if trial is None:
            return

        try:
            from edgecaster.systems import seal_trials

            lines = seal_trials.build_trial_status_lines(game, level, trial)
        except Exception:
            lines = []

        if not lines:
            return

        # Keep this panel on the map side (left of log) and below the top HUD strip.
        max_w = max(220, self.width - self.log_panel_width - 24)
        line_surfs = [self.small_font.render(line, True, self.fg) for line in lines]
        text_w = max(s.get_width() for s in line_surfs)
        text_h = sum(s.get_height() + 2 for s in line_surfs)

        panel_w = min(max_w, text_w + 16)
        panel_h = text_h + 12
        panel_x = 8
        panel_y = self.top_bar_height + 8

        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        if getattr(trial, "sealed", False):
            border = (80, 180, 120, 210)
        elif getattr(trial, "ready_to_seal", False):
            border = (220, 200, 120, 210)
        else:
            border = (120, 150, 220, 200)
        panel.fill((12, 14, 24, 205))
        pygame.draw.rect(panel, border, pygame.Rect(0, 0, panel_w, panel_h), 1)

        y = 6
        for i, surf in enumerate(line_surfs):
            if i == 0:
                header = self.small_font.render(lines[i], True, self.sel)
                panel.blit(header, (8, y))
            else:
                panel.blit(surf, (8, y))
            y += surf.get_height() + 2

        self.surface.blit(panel, (panel_x, panel_y))

    def draw_action_preview_underlay(self, game: Game) -> None:
        """Draw a scene-provided action preview under entities (pure view)."""
        preview = self._ui_attr("action_preview", None)
        if preview is None:
            return

        ox, oy = self._zone_abs_offset(game)

        overlay = getattr(self, "_action_preview_surface", None)
        if overlay is None or overlay.get_size() != (self.width, self.height):
            overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            setattr(self, "_action_preview_surface", overlay)
        overlay.fill((0, 0, 0, 0))

        drew_any = False

        # --- Tile highlights -------------------------------------------------
        direct = getattr(preview, "direct_tiles", None) or ()
        indirect = getattr(preview, "indirect_tiles", None) or ()
        direct_col = getattr(preview, "direct_color", None)
        indirect_col = getattr(preview, "indirect_color", None)
        tile_px = max(1, int(round(self.tile)))

        if direct_col or indirect_col:
            if direct_col is None:
                direct_col = (255, 255, 255, 90)
            if indirect_col is None:
                indirect_col = (255, 255, 255, 45)

            for (tx, ty) in direct:
                px = int((tx + ox) * self.tile + self.origin_x)
                py = int((ty + oy) * self.tile + self.origin_y)
                pygame.draw.rect(overlay, direct_col, pygame.Rect(px, py, tile_px, tile_px))
                drew_any = True

            for (tx, ty) in indirect:
                px = int((tx + ox) * self.tile + self.origin_x)
                py = int((ty + oy) * self.tile + self.origin_y)
                pygame.draw.rect(overlay, indirect_col, pygame.Rect(px, py, tile_px, tile_px))
                drew_any = True

        # --- Circular highlights --------------------------------------------
        # Used by chakra-driven previews (e.g. Energy Kick) to show radial
        # effect zones around specific rune vertices.
        circles = getattr(preview, "circles", None) or ()
        if circles:
            t_ms = pygame.time.get_ticks()

            for c in circles:
                try:
                    wx, wy = c.pos
                    rad_tiles = float(c.radius)
                except Exception:
                    continue
                if rad_tiles <= 0:
                    continue

                try:
                    base_rgba = tuple(int(v) for v in c.color[:4])
                    if len(base_rgba) < 4:
                        raise ValueError
                except Exception:
                    base_rgba = (140, 220, 255, 130)

                # Per-circle pulse selection. This keeps ability previews visually distinct
                # while sharing the same drawing path.
                pulse = str(getattr(c, "pulse", "sawtooth") or "sawtooth").lower()
                try:
                    period_ms = max(1, int(getattr(c, "period_ms", 1500) or 1500))
                except Exception:
                    period_ms = 1500

                phase = (t_ms % period_ms) / float(period_ms)  # 0..1
                if pulse == "sine":
                    # Smooth fade-in/fade-out.
                    mult = 0.5 + 0.5 * math.sin(phase * math.tau)
                else:
                    # Default sawtooth: jump high then linearly taper.
                    mult = 1.0 - phase

                cx = int((wx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
                cy = int((wy + oy) * self.tile + self.tile * 0.5 + self.origin_y)
                pr = max(1, int(round(rad_tiles * self.tile)))

                alpha = max(0, min(255, int(base_rgba[3] * mult)))
                if alpha <= 0:
                    continue

                fill_col = (base_rgba[0], base_rgba[1], base_rgba[2], alpha)
                edge_alpha = max(40, min(255, int(alpha * 1.15)))
                edge_col = (base_rgba[0], base_rgba[1], base_rgba[2], edge_alpha)

                # Fill + crisp border keeps the area readable over mixed terrain.
                pygame.draw.circle(overlay, fill_col, (cx, cy), pr, width=0)
                pygame.draw.circle(overlay, edge_col, (cx, cy), pr, width=1)
                drew_any = True

        # --- Ghost pattern overlay ------------------------------------------
        pat = getattr(preview, "pattern", None)
        anchor = getattr(preview, "pattern_anchor", None)
        if pat is not None and anchor is not None and getattr(pat, "vertices", None):
            edge_alpha = int(getattr(preview, "pattern_edge_alpha", 150) or 150)
            edge_alpha = max(20, min(255, edge_alpha))

            base0 = getattr(preview, "pattern_color", None) or self.pattern_color
            base1 = getattr(preview, "pattern_color_end", None) or self.pattern_color_end

            try:
                verts = project_vertices(pat, anchor)
            except Exception:
                verts = []

            edge_colors = getattr(pat, "edge_colors", {}) or {}

            for e in getattr(pat, "edges", []) or []:
                try:
                    a = verts[e.a]
                    b = verts[e.b]
                except Exception:
                    continue

                ax = (a[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x
                ay = (a[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y
                bx = (b[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x
                by = (b[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y
                dx = bx - ax
                dy = by - ay
                dist = max(1.0, math.hypot(dx, dy))
                steps = max(4, int(dist / (self.tile * 0.75)))

                edge_key = (min(e.a, e.b), max(e.a, e.b))
                col = edge_colors.get(edge_key)

                for i in range(steps):
                    t0 = i / steps
                    t1 = (i + 1) / steps
                    x0 = ax + dx * t0
                    y0 = ay + dy * t0
                    x1 = ax + dx * t1
                    y1 = ay + dy * t1

                    seg_col = col
                    if seg_col and isinstance(seg_col, (list, tuple)) and len(seg_col) == 2 and all(
                        isinstance(c, (list, tuple)) and len(c) >= 3 for c in seg_col
                    ):
                        c0 = tuple(int(v) for v in seg_col[0][:3])
                        c1 = tuple(int(v) for v in seg_col[1][:3])
                        seg_col = lerp_rgb(c0, c1, (t0 + t1) * 0.5)

                    if not seg_col:
                        seg_col = lerp_rgb(base0, base1, (t0 + t1) * 0.5)

                    try:
                        rgb = tuple(int(v) for v in seg_col[:3])
                    except Exception:
                        rgb = base0

                    core_col = (*rgb, edge_alpha)
                    pygame.draw.line(
                        overlay,
                        core_col,
                        (x0, y0),
                        (x1, y1),
                        width=max(1, int(getattr(self, "edge_width_base", 2))),
                    )
                    pygame.draw.aaline(overlay, core_col, (x0, y0), (x1, y1))
                    drew_any = True

        if drew_any:
            self.surface.blit(overlay, (0, 0))

    def draw_action_preview_overlay(self, _game: Game) -> None:
        """Draw a scene-provided action preview above entities (pure view)."""
        preview = self._ui_attr("action_preview", None)
        if preview is None:
            return

        ox, oy = self._zone_abs_offset(_game)

        texts = getattr(preview, "texts", None) or ()
        if not texts:
            return

        # Gentle fade so the user can tell it's a preview.
        t = pygame.time.get_ticks()
        phase = (t % 2000) / 2000.0
        fade = 1.0 - abs(phase * 2 - 1)  # triangle 0..1..0
        alpha = int(90 + 140 * fade)

        font_px = max(14, int(self.tile * 0.55))
        font = pygame.font.SysFont("consolas", font_px)

        for item in texts:
            try:
                tx, ty = item.pos
            except Exception:
                continue

            px = int((tx + ox) * self.tile + self.origin_x + self.tile * 0.5)
            py = int((ty + oy) * self.tile + self.origin_y + self.tile * 0.5)

            try:
                color = tuple(int(v) for v in item.color[:3])
            except Exception:
                color = (255, 255, 255)

            surf = font.render(str(item.text), True, color)
            tmp = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
            tmp.blit(surf, (0, 0))
            tmp.set_alpha(alpha)
            self.surface.blit(tmp, (px - surf.get_width() // 2, py - int(self.tile * 0.5) - surf.get_height()))

    def draw_aim_overlay(self, game: Game) -> None:
        pred = self._ui_attr("aim_prediction", None)
        ox, oy = self._zone_abs_offset(game)
        aim_action = getattr(pred, "action", None) or self._ui_attr("aim_action", None)
        if aim_action not in ("activate_all", "activate_seed", "throw_flask"):
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

        if aim_action in ("activate_all", "throw_flask"):
            radius = getattr(pred, "radius", None)
            if radius is None:
                if aim_action == "throw_flask":
                    radius = 3.0
                else:
                    try:
                        radius = game.get_param_value("activate_all", "radius")
                    except Exception:
                        radius = game.cfg.pattern_damage_radius if hasattr(game, "cfg") else 1.25
            if aim_action == "throw_flask":
                center = getattr(pred, "center", None)
                if center is None:
                    hover = verts[hover_vertex]
                    tx = int(round(hover[0]))
                    ty = int(round(hover[1]))
                    center = (tx + 0.5, ty + 0.5)
            else:
                center = verts[hover_vertex]
                cx = int((center[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x)
                cy = int((center[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y)
            pygame.draw.circle(self.surface, (120, 200, 255), (cx, cy), int(radius * self.tile), width=1)
            r2 = radius * radius
            target_vertices = getattr(pred, "target_vertices", None)
            if target_vertices is None:
                target_vertices = [i for i, v in enumerate(verts) if (v[0] - center[0]) ** 2 + (v[1] - center[1]) ** 2 <= r2]
            for idx in target_vertices:
                if idx < 0 or idx >= len(verts):
                    continue
                vx, vy = verts[idx]
                px = int((vx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
                py = int((vy + oy) * self.tile + self.tile * 0.5 + self.origin_y)
                pygame.draw.circle(self.surface, (200, 240, 255), (px, py), max(3, self.tile // 5))
        else:  # activate_seed
            center = verts[hover_vertex]
            px = int((vx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
            py = int((vy + oy) * self.tile + self.tile * 0.5 + self.origin_y)
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
                px = int((vx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
                py = int((vy + oy) * self.tile + self.tile * 0.5 + self.origin_y)
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
                    px = (ax + ox) * self.tile + self.origin_x + self.tile // 2
                    py = (ay + oy) * self.tile + self.origin_y + self.tile // 2
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
            cx = int((center[0] + ox) * self.tile + self.tile * 0.5 + self.origin_x)
            cy = int((center[1] + oy) * self.tile + self.tile * 0.5 + self.origin_y)

            txt = self.small_font.render(text_val, True, (255, 180, 140))
            surf = pygame.Surface((txt.get_width(), txt.get_height()), pygame.SRCALPHA)
            surf.blit(txt, (0, 0))
            surf.set_alpha(alpha)
            # position above circle: just outside the top edge
            offset = int(radius * self.tile) if radius else int(1.0 * self.tile)
            self.surface.blit(surf, (cx - txt.get_width() // 2, cy - offset - txt.get_height() - 4))


    def draw_activation_overlay(self, game: Game) -> None:
        """Post-activation visuals only (no text).

        Activation points are currently stored in LOCAL level coordinates.
        Convert to ABS for rendering, but keep visibility gating in LOCAL.
        """
        if not game.activation_points or game.activation_ttl <= 0:
            return
        world = game.world
        ox, oy = self._zone_abs_offset(game)
        for vx, vy in game.activation_points:
            tx = int(round(vx))
            ty = int(round(vy))
            if not world.in_bounds(tx, ty):
                continue
            tile = world.get_tile(tx, ty)
            if tile is None or not getattr(tile, "visible", True):
                continue

            avx = vx + ox
            avy = vy + oy
            px = int(avx * self.tile + self.tile * 0.5 + self.origin_x)
            py = int(avy * self.tile + self.tile * 0.5 + self.origin_y)
            sprite = self._get_glow_sprite(max(3, self.tile // 8), self.rune_color)
            self.surface.blit(sprite, sprite.get_rect(center=(px, py)))


    def draw_target_cursor(self, game: Game) -> None:
        """
        Draw the tile highlight for unified TargetMode.

        ui_state.target_cursor is LOCAL (current zone) but renderer origins are ABS.
        Convert LOCAL→ABS for drawing so the yellow cursor stays visible.
        """
        t = self._ui_attr("target", None)
        tc = self._ui_attr("target_cursor", None)
        tc_abs = self._ui_attr("target_cursor_abs", None)

        kind = getattr(t, "kind", None) if t is not None else None
        mode = getattr(t, "mode", None) if t is not None else None

        draw_for_terminus = (kind == "tile" and mode == "terminus")
        draw_for_look = (kind == "look")


        if not (draw_for_terminus or draw_for_look):
            return

        # Prefer ABS cursor for look-mode and terminus-mode when available.
        if (draw_for_look or draw_for_terminus) and tc_abs is not None:
            ax, ay = int(tc_abs[0]), int(tc_abs[1])
        else:
            if not tc:
                return
            tx, ty = tc
            if hasattr(game, "world") and hasattr(game.world, "in_bounds"):
                if not game.world.in_bounds(tx, ty):
                    return
            ax, ay = self._local_tile_to_abs(game, (tx, ty))



        tile_px = float(self.tile)
        size = max(1, int(round(tile_px)))

        px = int(round(ax * tile_px + self.origin_x))
        py = int(round(ay * tile_px + self.origin_y))

        rect = pygame.Rect(px, py, size, size)
        pygame.draw.rect(self.surface, (255, 255, 120), rect, 2)


    def draw_look_overlay(self, game: Game) -> None:
        """
        When in look-style TargetMode, show the name of any actor under
        the cursor in a yellow label just beneath the tile.

        Cursor tile is LOCAL, renderer pixels are ABS → convert for placement.
        """
        t = self._ui_attr("target", None)
        if not t or getattr(t, "kind", None) != "look":
            return

        cursor_tile = getattr(t, "cursor_tile", None) or self._ui_attr("target_cursor", None)
        if not cursor_tile:
            return

        tx, ty = cursor_tile
        world = getattr(game, "world", None)
        if world is None or not hasattr(world, "in_bounds") or not world.in_bounds(tx, ty):
            return

        tile = world.get_tile(tx, ty)
        if tile is None or (hasattr(tile, "visible") and not tile.visible):
            return

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

        ent = entities_here[-1]
        name = getattr(ent, "name", None) or getattr(ent, "label", None) or "something"

        ax, ay = self._local_tile_to_abs(game, (tx, ty))

        px = ax * self.tile + self.origin_x + self.tile // 2
        py = ay * self.tile + self.origin_y + self.tile

        text = self.small_font.render(name, True, self.sel)
        self.surface.blit(text, (px - text.get_width() // 2, py))


    def draw_place_overlay(self, game: Game) -> None:
        """Subtle pulsing circle showing placement range when selecting a terminus."""
        if not getattr(game, "awaiting_terminus", False):
            return
        player = game.actors[game.player_id]
        ax, ay = self._local_tile_to_abs(game, player.pos)
        cx = ax * self.tile + self.tile * 0.5 + self.origin_x
        cy = ay * self.tile + self.tile * 0.5 + self.origin_y
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

        ox, oy = self._zone_abs_offset(game)

        def _to_px(pt: Tuple[float, float]) -> Tuple[int, int]:
            ax = pt[0] + ox
            ay = pt[1] + oy
            return (
                int(ax * self.tile + self.tile * 0.5 + self.origin_x),
                int(ay * self.tile + self.tile * 0.5 + self.origin_y),
            )

        sx, sy = _to_px(source)
        tx, ty = _to_px(target)

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        box = int(max(1, half * self.tile * 2))
        pygame.draw.rect(overlay, (120, 220, 255, 70), pygame.Rect(sx - box // 2, sy - box // 2, box, box), 2)
        pygame.draw.rect(overlay, (255, 220, 120, 80), pygame.Rect(tx - box // 2, ty - box // 2, box, box), 2)

        pygame.draw.line(overlay, (220, 220, 220, 90), (sx, sy), (tx, ty), 2)


        # --- Ghost pattern (push) -----------------------------------------
        verts = preview.get("target_verts")
        edges = preview.get("edges")
        if verts and edges:
            # Draw ghost edges (green-ish) from target verts.
            for a, b in edges:
                try:
                    p0 = _to_px(verts[a])
                    p1 = _to_px(verts[b])
                except Exception:
                    continue
                pygame.draw.line(overlay, (180, 255, 180, 110), p0, p1, 2)
                pygame.draw.aaline(overlay, (180, 255, 180, 110), p0, p1)

            # Optional: vertex pips (helps readability)
            r = max(2, int(self.tile * 0.12))
            for vx, vy in verts:
                px, py = _to_px((vx, vy))
                pygame.draw.circle(overlay, (200, 255, 200, 130), (px, py), r)


        tiles = preview.get("ghost_tiles") or []
        for gx, gy in tiles:
            agx = gx + ox
            agy = gy + oy
            px = int(agx * self.tile + self.origin_x)
            py = int(agy * self.tile + self.origin_y)
            rect = pygame.Rect(px, py, int(self.tile), int(self.tile))
            pygame.draw.rect(overlay, (180, 255, 180, 55), rect, 1)

        self.surface.blit(overlay, (0, 0))

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
        ox, oy = self._zone_abs_offset(game)
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
            px = int((tx + ox) * self.tile + self.origin_x)
            py = int((ty + oy) * self.tile + self.origin_y)
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
            cx = (ax + ox) * self.tile + self.origin_x + self.tile * 0.5
            cy = (ay + oy) * self.tile + self.origin_y + self.tile * 0.5
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
        ox, oy = self._zone_abs_offset(game)
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
            px = int((tx + ox) * self.tile + self.origin_x)
            py = int((ty + oy) * self.tile + self.origin_y)
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
            cx = (ax + ox) * self.tile + self.origin_x + self.tile * 0.5
            cy = (ay + oy) * self.tile + self.origin_y + self.tile * 0.5
            radius = self.tile * 1.0
            col = (120, 255, 180, int(70 * alpha_scale))
            pygame.draw.circle(overlay, col, (int(cx), int(cy)), int(radius), width=2)

        self.surface.blit(overlay, (0, 0))

    def draw_choking_vines_overlay(self, game: Game) -> None:
        """
        Visual overlay for Choking Vines.

        Choking-vine simulation stores geometry in ABS tile coordinates:
        - segments: (x0, y0, x1, y1)
        - tips: {"x": float, "y": float}

        We draw the tendrils directly from that ABS geometry so they remain stable
        when crossing zone boundaries.
        """
        level = getattr(game, "_level", lambda: None)()
        if level is None:
            return
        state = getattr(level, "choking_vines_state", None)
        if not state:
            return

        segments = list(state.get("segments", []) or [])
        tips = list(state.get("tips", []) or [])
        if not segments and not tips:
            return

        remaining = float(state.get("remaining", 0))
        duration = float(state.get("duration", 1)) or 1.0
        pulse = 0.72 + 0.28 * (0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 175.0))
        alpha_scale = max(0.0, min(1.0, (remaining / duration) * pulse))
        if alpha_scale <= 1e-4:
            return

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)

        base_col = (48, 210, 110, int(86 * alpha_scale))
        core_col = (120, 255, 170, int(165 * alpha_scale))
        tip_col = (180, 255, 210, int(230 * alpha_scale))
        base_w = max(1, int(round(self.tile * 0.11)))
        core_w = max(1, int(round(self.tile * 0.06)))
        tip_r = max(2, int(round(self.tile * 0.14)))

        # Draw tendril segments first.
        for seg in segments:
            try:
                x0, y0, x1, y1 = seg
            except Exception:
                continue
            p0 = (
                int(float(x0) * self.tile + self.tile * 0.5 + self.origin_x),
                int(float(y0) * self.tile + self.tile * 0.5 + self.origin_y),
            )
            p1 = (
                int(float(x1) * self.tile + self.tile * 0.5 + self.origin_x),
                int(float(y1) * self.tile + self.tile * 0.5 + self.origin_y),
            )
            pygame.draw.line(overlay, base_col, p0, p1, base_w)
            pygame.draw.line(overlay, core_col, p0, p1, core_w)

        # Highlight active growth tips.
        for tip in tips:
            try:
                tx = float(tip.get("x", 0.0))
                ty = float(tip.get("y", 0.0))
            except Exception:
                continue
            px = int(tx * self.tile + self.tile * 0.5 + self.origin_x)
            py = int(ty * self.tile + self.tile * 0.5 + self.origin_y)
            pygame.draw.circle(overlay, tip_col, (px, py), tip_r)

        self.surface.blit(overlay, (0, 0))

    def draw_sparkle_overlay(self, game: Game) -> None:
        """
        Visual overlay for Sparkle:
        - Pattern edges briefly black out and fade back in (~1s).
        - Vertices crackle with bright "sequin"/"firecracker" sparkles.

        This is intentionally render-time based (monotonic clock) so it animates
        even while waiting for player input.
        """
        level = getattr(game, "_level", lambda: None)()
        if level is None:
            return

        state = getattr(level, "sparkle_state", None)
        if not state:
            return
        ox, oy = self._zone_abs_offset(game)

        try:
            t0 = float(state.get("t0", 0.0))
            duration = float(state.get("duration_s", 1.0) or 1.0)
        except Exception:
            setattr(level, "sparkle_state", None)
            return

        now = float(time.monotonic())
        if duration <= 1e-6:
            setattr(level, "sparkle_state", None)
            return

        t = (now - t0) / duration
        if t >= 1.0:
            setattr(level, "sparkle_state", None)
            return

        overlay = getattr(self, "_sparkle_overlay_surface", None)
        if overlay is None or overlay.get_size() != (self.width, self.height):
            overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            setattr(self, "_sparkle_overlay_surface", overlay)
        overlay.fill((0, 0, 0, 0))

        glow = getattr(self, "_sparkle_glow_surface", None)
        if glow is None or glow.get_size() != (self.width, self.height):
            glow = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
            setattr(self, "_sparkle_glow_surface", glow)
        glow.fill((0, 0, 0, 0))

        verts = state.get("verts") or []
        edges = state.get("edges") or []

        # Fallback for older states: derive geometry from the current pattern.
        if (not verts) and game.pattern.vertices and game.pattern_anchor is not None:
            try:
                verts = project_vertices(game.pattern, game.pattern_anchor)
                edges = [(int(e.a), int(e.b)) for e in game.pattern.edges]
            except Exception:
                verts = []
                edges = []

        if not verts:
            return

        # Edge blackout fades out over time (ease-out).
        fade = max(0.0, min(1.0, t))
        blackout_alpha = int(255 * (1.0 - fade) ** 1.7)
        blackout_alpha = max(0, min(255, blackout_alpha))

        if blackout_alpha > 0 and edges:
            col = (0, 0, 0, blackout_alpha)
            width = max(1, int(self.edge_width_base + 2))
            for a_idx, b_idx in edges:
                if a_idx < 0 or b_idx < 0 or a_idx >= len(verts) or b_idx >= len(verts):
                    continue
                ax, ay = verts[a_idx]
                bx, by = verts[b_idx]
                x0 = (ax + ox) * self.tile + self.tile * 0.5 + self.origin_x
                y0 = (ay + oy) * self.tile + self.tile * 0.5 + self.origin_y
                x1 = (bx + ox) * self.tile + self.tile * 0.5 + self.origin_x
                y1 = (by + oy) * self.tile + self.tile * 0.5 + self.origin_y
                pygame.draw.line(overlay, col, (x0, y0), (x1, y1), width=width)
                pygame.draw.aaline(overlay, col, (x0, y0), (x1, y1))

        # Crackling sparkles: time-quantized for stability and performance.
        try:
            seed = int(state.get("seed", 0))
        except Exception:
            seed = 0
        frame = int((now - t0) / 0.045)  # ~22 Hz
        rng = random.Random((seed ^ (frame * 0x9E3779B1)) & 0xFFFFFFFF)

        # More sparkles early, fewer as it settles (use the shorter crackle window).
        try:
            spark_dur = float(state.get("spark_duration_s", 1.0) or 1.0)
        except Exception:
            spark_dur = 1.0
        spark_t = 1.0
        if spark_dur > 1e-6:
            spark_t = max(0.0, min(1.0, (now - t0) / spark_dur))
        settle = (1.0 - spark_t) ** 0.5
        max_sparkles = 90
        base_sparkles = max(14, min(max_sparkles, len(verts) // 18))
        sparkle_count = max(8, int(base_sparkles * (0.35 + 0.65 * settle)))

        # Radius scales with zoom/tile size, but keep readable.
        base_r = max(1, int(self.tile * 0.08))
        jitter = max(0, int(self.tile * 0.10))

        palette = [
            (255, 255, 235),
            (255, 240, 200),
            (255, 220, 170),
            (235, 245, 255),
        ]

        for _ in range(min(sparkle_count, 200)):
            idx = rng.randrange(0, len(verts))
            vx, vy = verts[idx]
            px = int((vx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
            py = int((vy + oy) * self.tile + self.tile * 0.5 + self.origin_y)
            if jitter:
                px += rng.randint(-jitter, jitter)
                py += rng.randint(-jitter, jitter)

            # Firecracker: mostly small flashes with occasional larger "pops".
            pop = rng.random()
            r = base_r + (1 if pop > 0.85 else 0) + (1 if pop > 0.96 else 0)
            a = int((70 + 185 * (pop ** 0.35)) * (0.25 + 0.75 * settle))
            a = max(0, min(255, a))

            rgb = rng.choice(palette)
            pygame.draw.circle(glow, (*rgb, a), (px, py), r)
            if r >= 2 and a >= 120:
                # Tiny cross "sequin" glint.
                glint = (*rgb, int(a * 0.85))
                pygame.draw.aaline(glow, glint, (px - r, py), (px + r, py))
                pygame.draw.aaline(glow, glint, (px, py - r), (px, py + r))

        # Lens flare / starburst: one bright pulse near the rune COM.
        # This is purely visual, and uses additive blending.
        if spark_t < 1.0 and len(verts) >= 2:
            sx = sum(float(v[0]) for v in verts) / max(1, len(verts))
            sy = sum(float(v[1]) for v in verts) / max(1, len(verts))
            cx = int((sx + ox) * self.tile + self.tile * 0.5 + self.origin_x)
            cy = int((sy + oy) * self.tile + self.tile * 0.5 + self.origin_y)

            # Fast attack + exponential-ish decay (matches the inspiration).
            attack = 0.05
            decay = max(0.10, spark_dur - attack)
            elapsed = max(0.0, now - t0)
            if elapsed <= attack:
                u = elapsed / max(1e-6, attack)
                intensity = 1.0 - (1.0 - u) * (1.0 - u)  # ease-out
            else:
                d = (elapsed - attack) / max(1e-6, decay)
                intensity = math.exp(-4.0 * d)

            intensity = max(0.0, min(1.0, intensity))
            if intensity > 0.02:
                radius = max(10, int(self.tile * 1.35))
                glow_sprite = self._get_radial_sprite(max(6, radius // 2), (255, 255, 255))
                star_sprite = self._get_starburst_sprite(radius, spikes=8, color=(255, 255, 255))

                # Rotate faster near the peak, slower later.
                spin_speed = 720.0
                spin = spin_speed * (0.25 + 0.75 * intensity)
                angle = (spin * elapsed) % 360.0

                punch = 1.0 + 0.15 * math.exp(-40.0 * elapsed)
                sprite = pygame.transform.rotozoom(star_sprite, angle, punch)
                sprite.set_alpha(int(255 * intensity))

                g = glow_sprite.copy()
                g.set_alpha(int(200 * intensity))
                glow.blit(g, g.get_rect(center=(cx, cy)).topleft, special_flags=pygame.BLEND_RGBA_ADD)
                glow.blit(sprite, sprite.get_rect(center=(cx, cy)).topleft, special_flags=pygame.BLEND_RGBA_ADD)

        # Darken edges first, then add glow/sparkles on top.
        self.surface.blit(overlay, (0, 0))
        self.surface.blit(glow, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

    def draw_lightning_overlay(self, game: Game) -> None:
        """Visual overlay for Lightning (pure VFX; no rules/simulation here)."""
        level = getattr(game, "_level", lambda: None)()
        if level is None:
            return

        state = getattr(level, "lightning_state", None)
        if not state:
            return
        ox, oy = self._zone_abs_offset(game)

        try:
            t0 = float(state.get("t0", 0.0))
            duration = float(state.get("duration_s", 0.26) or 0.26)
            flash_s = float(state.get("flash_s", 0.08) or 0.08)
        except Exception:
            setattr(level, "lightning_state", None)
            return

        now = float(time.monotonic())
        if duration <= 1e-6:
            setattr(level, "lightning_state", None)
            return

        u = (now - t0) / duration
        if u >= 1.0:
            setattr(level, "lightning_state", None)
            return

        try:
            seed = int(state.get("seed", 0))
        except Exception:
            seed = 0

        verts = state.get("verts") or []
        edges = state.get("edges") or []
        if not verts or not edges:
            setattr(level, "lightning_state", None)
            return

        # Cache generated edge-constrained paths so the effect stays thin and stable.
        paths_world = state.get("_edge_paths_world")
        if not paths_world:
            rng = random.Random(seed & 0xFFFFFFFF)

            def edge_polyline(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
                """Return a tiny jagged polyline that stays close to the edge segment."""
                ax, ay = a
                bx, by = b
                dx = bx - ax
                dy = by - ay
                length = math.hypot(dx, dy)
                if length <= 1e-6:
                    return [(ax, ay), (bx, by)]

                # Perpendicular unit vector (for a small jitter that keeps the bolt on-edge).
                px = -dy / length
                py = dx / length

                # TUNING: segments per edge (higher => more jagged).
                steps = int(max(4, min(18, length * 9.0)))
                out: list[tuple[float, float]] = [(ax, ay)]

                for i in range(1, steps):
                    t = i / float(steps)
                    base_x = ax + dx * t
                    base_y = ay + dy * t

                    # TUNING: jaggedness amplitude in TILE units (keep small to stay on-edge).
                    amp = 0.06 * (0.35 + 0.65 * (1.0 - abs(2.0 * t - 1.0)))
                    offset = rng.uniform(-amp, amp)
                    out.append((base_x + px * offset, base_y + py * offset))

                out.append((bx, by))
                return out

            paths_world = []
            for a_idx, b_idx in edges:
                if a_idx < 0 or b_idx < 0 or a_idx >= len(verts) or b_idx >= len(verts):
                    continue
                paths_world.append(edge_polyline(verts[a_idx], verts[b_idx]))

            state["_edge_paths_world"] = paths_world

        # Compute a tight bounding box around the rune footprint (in tile coords).
        tile_bbox = state.get("_tile_bbox")
        if tile_bbox is None:
            xs = [float(v[0]) for v in verts]
            ys = [float(v[1]) for v in verts]
            if not xs or not ys:
                return
            tile_bbox = (math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))
            state["_tile_bbox"] = tile_bbox

        min_x, min_y, max_x, max_y = tile_bbox
        pad_px = int(max(10, self.tile * 2.0))
        left = int((min_x + ox) * self.tile + self.origin_x) - pad_px
        top = int((min_y + oy) * self.tile + self.origin_y) - pad_px
        width = int((max_x - min_x + 1) * self.tile) + pad_px * 2
        height = int((max_y - min_y + 1) * self.tile) + pad_px * 2

        screen_rect = pygame.Rect(0, 0, self.width, self.height)
        fx_rect = pygame.Rect(left, top, width, height).clip(screen_rect)
        if fx_rect.w <= 1 or fx_rect.h <= 1:
            return

        # Local surface for bolt rendering + bloom.
        bolt = pygame.Surface((fx_rect.w, fx_rect.h), pygame.SRCALPHA)

        def to_local_px(wx: float, wy: float) -> tuple[int, int]:
            # World-space (tile units) -> local pixel coords (centered on tile centers).
            px = (wx + ox) * self.tile + self.origin_x + self.tile * 0.5 - fx_rect.x
            py = (wy + oy) * self.tile + self.origin_y + self.tile * 0.5 - fx_rect.y
            return (int(px), int(py))

        # TUNING: reveal speed (how quickly the bolt "runs" along the path).
        reveal = min(1.0, max(0.0, u) * 1.35)

        # TUNING: flicker frequency + decay curve.
        phase = (seed & 0xFFFF) / 65536.0 * math.tau
        flick = 0.75 + 0.25 * math.sin(60.0 * (now - t0) + phase)
        intensity = math.exp(-5.0 * max(0.0, u)) * flick
        max_alpha = 240
        alpha = int(max(0, min(255, max_alpha * intensity)))
        if alpha <= 0:
            return

        # TUNING: bolt thickness (in pixels). Keep these small so lightning hugs rune edges.
        core_width = 1
        glow_width = 2
        color = (255, 255, 255)

        def draw_polyline(points_world: list[tuple[float, float]]) -> None:
            if len(points_world) < 2:
                return
            pts = [to_local_px(x, y) for (x, y) in points_world]
            n = max(2, int(len(pts) * reveal))
            pts = pts[:n]
            if len(pts) < 2:
                return
            pygame.draw.lines(bolt, (*color, int(alpha * 0.12)), False, pts, glow_width)
            pygame.draw.lines(bolt, (*color, alpha), False, pts, core_width)
            # Add a few aaliners for extra crispness.
            for a, b in zip(pts, pts[1:]):
                pygame.draw.aaline(bolt, (*color, alpha), a, b)

        for path in paths_world:
            draw_polyline(path)

        # TUNING: bloom (blur) size/intensity. Smaller downsample => tighter bloom.
        def blur_surface(surf: pygame.Surface, downsample: int = 5) -> pygame.Surface:
            w, h = surf.get_size()
            ds = max(1, int(downsample))
            small = pygame.transform.smoothscale(surf, (max(1, w // ds), max(1, h // ds)))
            return pygame.transform.smoothscale(small, (w, h))

        bloom = blur_surface(bolt, downsample=2)
        bloom.set_alpha(int(alpha * 0.22))

        # Optional early flash: briefly over-brightens rune edges (stays thin/on-edge).
        if flash_s > 1e-6 and (now - t0) < flash_s:
            flash_u = 1.0 - ((now - t0) / flash_s)
            flash_a = int(220 * max(0.0, min(1.0, flash_u)))
            if flash_a > 0:
                for path in paths_world:
                    pts = [to_local_px(x, y) for (x, y) in path]
                    n = max(2, int(len(pts) * reveal))
                    pts = pts[:n]
                    if len(pts) >= 2:
                        pygame.draw.lines(bolt, (255, 255, 255, flash_a), False, pts, 2)

        # Composite additively onto the main surface.
        self.surface.blit(bloom, fx_rect.topleft, special_flags=pygame.BLEND_RGBA_ADD)
        self.surface.blit(bolt, fx_rect.topleft, special_flags=pygame.BLEND_RGBA_ADD)

        # Extra punch: starbursts on struck targets.
        target_tiles = state.get("target_tiles") or []
        if target_tiles:
            # TUNING: hit marker size/intensity. Keep this small so it reads as a "spark"
            # on the creature, not a large white disk.
            star_radius = max(6, int(self.tile * 0.055))
            star = self._get_starburst_sprite(star_radius, spikes=8, color=(220, 240, 255))
            star_a = int(200 * min(1.0, intensity * 1.2))
            if star_a > 0:
                for tx, ty in target_tiles:
                    cx, cy = to_local_px(float(tx), float(ty))
                    sprite = star.copy()
                    sprite.set_alpha(star_a)
                    self.surface.blit(sprite, sprite.get_rect(center=(fx_rect.x + cx, fx_rect.y + cy)).topleft, special_flags=pygame.BLEND_RGBA_ADD)

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

    def draw_action_tooltip(self, game: Game) -> None:
        """Draw a lightweight tooltip for the currently hovered ability action."""
        action_name = self._ui_attr("hovered_action", None)
        if not action_name:
            return

        tooltip = resolve_action_tooltip(game, str(action_name), getattr(game, "player_id", None))
        if tooltip is None:
            return

        title_font = self.font
        body_font = self.small_font

        max_w = min(520, max(260, self.width - self.log_panel_width - 24))
        content_w = max_w - 16

        rendered_lines: List[Tuple[str, pygame.Surface]] = []
        rendered_lines.append((tooltip.title, title_font.render(tooltip.title, True, (240, 245, 255))))

        for ln in self._wrap_text(tooltip.summary, body_font, content_w):
            rendered_lines.append((ln, body_font.render(ln, True, (215, 225, 245))))

        for line in tooltip.lines:
            for wrapped in self._wrap_text(f"- {line}", body_font, content_w):
                rendered_lines.append((wrapped, body_font.render(wrapped, True, (170, 230, 210))))

        line_gap = 3
        panel_h = 10
        panel_w = 0
        for txt, surf in rendered_lines:
            panel_h += surf.get_height() + line_gap
            panel_w = max(panel_w, surf.get_width())
        panel_h += 8
        panel_w = min(max_w, panel_w + 16)

        # Anchor near mouse, but keep above ability bar and out of the log panel.
        mx, my = self._to_surface(pygame.mouse.get_pos())
        x = int(mx + 18)
        y = int(self.height - self.ability_bar_height - panel_h - 8)

        max_x = self.width - self.log_panel_width - panel_w - 6
        if x > max_x:
            x = max(6, max_x)
        if x < 6:
            x = 6

        if y < self.top_bar_height + 6:
            y = self.top_bar_height + 6

        rect = pygame.Rect(x, y, panel_w, panel_h)
        pygame.draw.rect(self.surface, (10, 14, 26, 230), rect, border_radius=6)
        pygame.draw.rect(self.surface, (90, 118, 170), rect, 1, border_radius=6)

        ty = rect.y + 7
        for _txt, surf in rendered_lines:
            self.surface.blit(surf, (rect.x + 8, ty))
            ty += surf.get_height() + line_gap


    def _draw_ability_icon(self, rect: pygame.Rect, ability_or_action, game: Game) -> None:
        """Draw an ability icon from either an Ability or an action name."""
        action = getattr(ability_or_action, "action", ability_or_action)
        preview = getattr(ability_or_action, "preview_geom", None)
        surf = self._render_action_icon(action, game, (rect.w, rect.h), preview_geom=preview)
        dx = rect.x + (rect.w - surf.get_width()) // 2
        dy = rect.y + (rect.h - surf.get_height()) // 2
        self.surface.blit(surf, (dx, dy))

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
        dx = rect.x + (rect.w - surf.get_width()) // 2
        dy = rect.y + (rect.h - surf.get_height()) // 2
        surface.blit(surf, (dx, dy))




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
        # YOGA: Set ABS cursor as canonical, local is derived
        player = game.actors[game.player_id]
        if getattr(self, "ui_state", None) is not None:
            abs_pos = getattr(player, "abs_pos", None)
            if abs_pos is not None:
                self.ui_state.target_cursor_abs = abs_pos  # type: ignore[attr-defined]
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

        self.draw_seal_trial_overlay(game)
        self.draw_pattern_overlay(game)
        self.draw_choking_vines_overlay(game)
        self.draw_sparkle_overlay(game)
        self.draw_lightning_overlay(game)
        self.draw_action_preview_underlay(game)
        self.draw_push_preview(game)
        self.draw_place_overlay(game)
        self.draw_activation_overlay(game)
        self.draw_aim_overlay(game)
        # Unified entity rendering: items + actors together.
        # Camera-centric query (god-vision / macro-view):
        # - compute the visible world rect in tile coords
        # - translate local-zone coords -> absolute world coords
        # - query only entities intersecting that rect, with an LoD/size band cull.
        try:
            map_origin_x, map_origin_y = self._map_origin_base()
            map_w = self.width - self.log_panel_width
            map_h = self.height - self.ability_bar_height - map_origin_y
            map_rect = pygame.Rect(map_origin_x, map_origin_y, max(1, map_w), max(1, map_h))

            world_scale = float(getattr(self, "tile_px", float(self.base_tile) * float(getattr(self, "zoom", 1.0))))
            world_scale = max(1e-6, world_scale)

            # Visible rect in *local* tile coords.
            x0 = (float(map_rect.left) - float(getattr(self, "origin_x", map_origin_x))) / world_scale
            y0 = (float(map_rect.top) - float(getattr(self, "origin_y", map_origin_y))) / world_scale
            x1 = (float(map_rect.right) - float(getattr(self, "origin_x", map_origin_x))) / world_scale
            y1 = (float(map_rect.bottom) - float(getattr(self, "origin_y", map_origin_y))) / world_scale

            # Visible rect in ABSOLUTE world-tile coords.
            # NOTE: origin_x/origin_y are now assumed to be an abs-space mapping
            # (because pan is centered using player.abs_pos).
            abs_rect = (float(x0), float(y0), float(x1), float(y1))

            # cam_lod = log2(1 / zoom)
            cam_lod = 0.0
            try:
                cam_lod = math.log2(float(self.base_tile) / world_scale)
            except Exception:
                cam_lod = 0.0

            dmin = float(getattr(self, "entity_lod_delta_min", -5.0))
            dmax = float(getattr(self, "entity_lod_delta_max", 0.5))
            fade_w = float(getattr(self, "entity_lod_fade_width", 0.6))
            max_count = int(getattr(self, "entity_render_max_count", 2000))

            if hasattr(game, "renderables_in_abs_rect"):
                renderables = game.renderables_in_abs_rect(
                    abs_rect,
                    cam_lod=cam_lod,
                    dmin=dmin,
                    dmax=dmax,
                    fade_w=fade_w,
                    max_count=max_count,
                )
            else:
                renderables = game.renderables_current()
        except Exception:
            renderables = game.renderables_current()
        self.draw_entities(game, game.world, renderables)
        self.draw_action_preview_overlay(game)
        self.draw_target_cursor(game)
        self.draw_seal_root_hint(game)
        self.draw_look_overlay(game)
        self.draw_seal_trial_status(game)

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
        self.draw_action_tooltip(game)

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

        # Keep the world point under the cursor stable while zooming.
        base_x, base_y = self._map_origin_base()
        self.origin_x = base_x + self.pan_x
        self.origin_y = base_y + self.pan_y

        # Use the *true* continuous world scale (pixels per world-tile).
        cur_scale = float(self.base_tile) * float(self.zoom)
        cur_scale = max(1e-6, cur_scale)

        wx = (mx - self.origin_x) / cur_scale
        wy = (my - self.origin_y) / cur_scale

        # Tunables
        ZOOM_RATE = 0.15          # base wheel sensitivity
        ZOOM_DAMP_EXP = 0.6       # >0 slows zoom more at extremes

        # Symmetric damping: avoids zoom exploding at large zoom.
        z = float(self.zoom)
        if z < 1.0:
            damp = max(0.05, z ** ZOOM_DAMP_EXP)
        else:
            damp = max(0.05, z ** (-ZOOM_DAMP_EXP))

        zoom_factor = 1.0 + float(delta_steps) * ZOOM_RATE * damp
        new_zoom = float(self.zoom) * zoom_factor

        # Allow deep zoom-out AND deep zoom-in (micro realms).
        new_zoom = max(0.002, min(2048.0, new_zoom))
        if abs(new_zoom - float(self.zoom)) < 1e-6:
            return

        self.zoom = float(new_zoom)

        # Mark camera as 'interactive' for a short window so expensive sampling can be deferred.
        try:
            self._last_camera_input_ms = pygame.time.get_ticks()
        except Exception:
            self._last_camera_input_ms = 0

        # True world scale for ALL placement math (terrain + entities + targeting).
        new_scale = float(self.base_tile) * float(self.zoom)
        new_scale = max(1e-6, new_scale)
        self.tile_px = float(new_scale)

        # CRITICAL: self.tile must remain WORLD scale (not clamped glyph size).
        self.tile = float(self.tile_px)

        # Clamped render glyph size (fonts/icons) for legibility.
        new_glyph_px = int(max(8, min(96, round(new_scale))))
        self.glyph_px = new_glyph_px

        # Map font scales with clamped glyph size; UI fonts remain constant.
        if int(getattr(self, '_last_zoom_glyph_px', -1)) != int(new_glyph_px):
            self.map_font = self._get_cached_map_font(int(new_glyph_px))
            self._last_zoom_glyph_px = int(new_glyph_px)

            # Rescale currency icon for map tiles when zoom changes (use clamped glyph size).
            if getattr(self, 'bismuth_icon', None) is not None:
                try:
                    cached = self._bismuth_icon_map_cache.get(int(new_glyph_px))
                    if cached is None:
                        cached = pygame.transform.smoothscale(self.bismuth_icon, (int(new_glyph_px), int(new_glyph_px)))
                        self._bismuth_icon_map_cache[int(new_glyph_px)] = cached
                    self.bismuth_icon_map = cached
                except Exception:
                    self.bismuth_icon_map = None

        # Adjust pan so world point under cursor stays under cursor (use TRUE scale).
        target_origin_x = mx - wx * new_scale
        target_origin_y = my - wy * new_scale
        self.pan_x = float(target_origin_x - base_x)
        self.pan_y = float(target_origin_y - base_y)

        # Keep TileCamera in sync if present.
        cam = getattr(self, 'camera', None)
        if cam is not None:
            try:
                cam.zoom = float(self.zoom)
                cam.pan_x = float(self.pan_x)
                cam.pan_y = float(self.pan_y)
            except Exception:
                pass


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

    def _get_radial_sprite(self, radius: int, color: Tuple[int, int, int]) -> pygame.Surface:
        """Soft circular glow via concentric circles (cached)."""
        radius = int(max(2, min(512, radius)))
        key = (radius, color)
        cached = self._radial_cache.get(key)
        if cached is not None:
            return cached
        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        cx = cy = radius
        for r in range(radius, 0, -1):
            a = int(255 * (r / radius) ** 2)
            pygame.draw.circle(surf, (*color, a), (cx, cy), r)
        self._radial_cache[key] = surf
        return surf

    def _get_starburst_sprite(self, radius: int, *, spikes: int = 8, color: Tuple[int, int, int] = (255, 255, 255)) -> pygame.Surface:
        """Lens flare-ish starburst (cached)."""
        radius = int(max(6, min(768, radius)))
        spikes = int(max(4, min(24, spikes)))
        key = (radius, spikes, color)
        cached = self._starburst_cache.get(key)
        if cached is not None:
            return cached

        surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        cx = cy = radius

        # Halo (additive on self-surface)
        halo = self._get_radial_sprite(radius, color)
        surf.blit(halo, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        # Star spikes
        for i in range(spikes):
            ang = (i / spikes) * math.tau
            length = radius * (1.0 if i % 2 == 0 else 0.55)
            x2 = cx + math.cos(ang) * length
            y2 = cy + math.sin(ang) * length
            for w, alpha_scale in ((3, 0.20), (2, 0.35), (1, 0.60)):
                a = int(255 * alpha_scale)
                pygame.draw.line(surf, (*color, a), (cx, cy), (x2, y2), w)
                pygame.draw.aaline(surf, (*color, a), (cx, cy), (x2, y2))

        # Hot core
        pygame.draw.circle(surf, (*color, 255), (cx, cy), max(2, radius // 10))

        self._starburst_cache[key] = surf
        return surf

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
            base = min(w, h)
            pygame.draw.circle(surf, (180, 200, 255), (w // 2, h // 2), max(4, base // 3), width=2)
            pygame.draw.circle(surf, (120, 180, 255), (w // 2, h // 2), max(2, base // 6))

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
