"""Pygame-based ASCII-style renderer with ability bar and targeting."""
import pygame
import math
from typing import Tuple, List, Dict


from edgecaster.game import Game
from edgecaster.state.actors import Actor
from edgecaster.state.world import World
from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns.library import action_preview_geometry
from edgecaster.ui.ability_bar import AbilityBarRenderer
from edgecaster.visuals import VisualProfile, apply_visual_panel





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
        self.surface_flags = pygame.RESIZABLE
        # render surface at native resolution; display may be larger in fullscreen
        self.surface = pygame.Surface((width, height))
        self.fullscreen = False
        self.display = pygame.display.set_mode((width, height), self.surface_flags)
        self.lb_off = (0, 0)  # letterbox offset when centering
        self.lb_scale = 1.0   # letterbox scale factor
        self.lb_scale = 1.0   # letterbox scale factor
        pygame.display.set_caption("Edgecaster (ASCII prototype)")
        self.font = pygame.font.SysFont("consolas", self.base_tile)  # UI font (fixed)
        self.map_font = pygame.font.SysFont("consolas", self.tile)  # map glyphs, scales with zoom
        self.small_font = pygame.font.SysFont("consolas", 16)
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



    # ----        # Optional world-level visual profile (e.g. curses/blessings).
        # SceneManager can set this via set_global_visual_profile().
        self.global_visual_profile: VisualProfile | None = None

    # UI state access (scene-owned view-model preferred)                 #
    # ------------------------------------------------------------------ #

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

    def present(self) -> None:
        self._present()

    def toggle_fullscreen(self) -> None:
        flags = self.display.get_flags()
        if flags & pygame.FULLSCREEN:
            self.display = pygame.display.set_mode((self.width, self.height), self.surface_flags)
            self.fullscreen = False
        else:
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



    def draw_world(self, world: World) -> None:
        # clear main surface
        self.surface.fill(self.bg)
        # compute map origin offset to account for top bar and right log
        # Push the map down by ~4 rows of tiles to keep HUD from overlapping.
        map_origin_x = 8
        map_origin_y = self.top_bar_height + 8 + int(self.tile * 4)
        self.origin_x = map_origin_x
        self.origin_y = map_origin_y
        palette = {
            "~": (90, 130, 255),   # water
            ",": (190, 170, 120),  # shore
            ".": (140, 200, 140),  # grass
            "T": (60, 140, 80),    # trees
            "^": (170, 140, 100),  # hills
            "#": (180, 180, 190),  # mountains/walls
        }
        for y in range(world.height):
            for x in range(world.width):
                tile = world.tiles[y][x]
                if not tile.explored and not tile.visible:
                    continue
                base_col = tile.tint if getattr(tile, "tint", None) else palette.get(tile.glyph, self.fg)
                # sanitize color: ensure length 3 and ints 0-255
                if base_col is None:
                    base_col = self.fg
                if len(base_col) >= 3:
                    base_col = tuple(max(0, min(255, int(base_col[i]))) for i in range(3))
                else:
                    base_col = self.fg
                if tile.visible:
                    color = base_col
                else:
                    # dim the color for explored but not visible
                    color = tuple(max(0, int(c * 0.5)) for c in base_col)
                ch = tile.glyph
                text = self.map_font.render(ch, True, color)
                px = x * self.tile + self.origin_x
                py = y * self.tile + self.origin_y
                if px >= self.width - self.log_panel_width or py >= self.height - self.ability_bar_height:
                    continue
                self.surface.blit(text, (px, py))

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

            glyph, color = self._entity_visual(ent)
            text = self.map_font.render(glyph, True, color)
            self.surface.blit(text, (px, py))



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
        player = game.actors[game.player_id]
        x = 12
        y = 12

        bar_w = 220
        bar_h = 12

        # --- Header: name, class, level ---
        char = getattr(game, "character", None)
        if char:
            name = char.name or "Edgecaster"
            char_class = getattr(char, "char_class", None)
        else:
            name = getattr(player, "name", "Edgecaster")
            char_class = None

        lvl = player.stats.level if player else 1

        label = getattr(game, "current_host_label", None)
        if label:
            header_line = f"{name} the {label}"
        else:
            header_line = name
        header_line += f" (Lv {lvl})"

        header_text = self.small_font.render(header_line, True, self.fg)

        # Raise header slightly (was y - 24)
        header_y = y
        self.surface.blit(header_text, (x, header_y))
        y += header_text.get_height() + 10

        # --- HP bar ---
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        hp_ratio = 0 if player.stats.max_hp == 0 else player.stats.hp / player.stats.max_hp
        pygame.draw.rect(
            self.surface,
            self.hp_color,
            pygame.Rect(x, y, int(bar_w * hp_ratio), bar_h),
        )
        hp_text = self.small_font.render(
            f"HP {player.stats.hp}/{player.stats.max_hp}", True, self.fg
        )
        # Put the HP label just above its bar
        self.surface.blit(hp_text, (x + 4, y - 18))

        # --- XP bar (to the right of HP) ---
        xp_needed = max(1, getattr(player.stats, "xp_to_next", 1))
        xp_cur = getattr(player.stats, "xp", 0)
        lvl = getattr(player.stats, "level", 1)
        xp_text = self.small_font.render(f"XP {xp_cur}/{xp_needed}   (Lv {lvl})", True, self.fg)
        self.surface.blit(xp_text, (x + bar_w + 20, y - 18))
        xp_x = x + bar_w + 20
        xp_y = y
        xp_ratio = max(0, min(1, xp_cur / xp_needed))
        xp_w = 180
        pygame.draw.rect(
            self.surface,
            self.bar_bg,
            pygame.Rect(xp_x, xp_y, xp_w, bar_h),
        )
        pygame.draw.rect(
            self.surface,
            (120, 200, 120),
            pygame.Rect(xp_x, xp_y, int(xp_w * xp_ratio), bar_h),
        )
        # remove redundant Lv in bar; already shown above
        # (label rendered before the bar)

        # --- Mana bar below HP ---
        y += bar_h + 16
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        mp_ratio = 0 if player.stats.max_mana == 0 else player.stats.mana / player.stats.max_mana
        pygame.draw.rect(
            self.surface,
            self.mana_color,
            pygame.Rect(x, y, int(bar_w * mp_ratio), bar_h),
        )
        mp_text = self.small_font.render(
            f"Mana {player.stats.mana}/{player.stats.max_mana}", True, self.fg
        )
        self.surface.blit(mp_text, (x + 4, y - 18))

        # --- Coherence bar to the right of mana ---
        coh_x = x + bar_w + 20
        coh_y = y
        coh = getattr(player.stats, "coherence", 0)
        coh_max = max(1, getattr(player.stats, "max_coherence", 1))
        coh_ratio = max(0, min(1, coh / coh_max))
        coh_w = 200
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(coh_x, coh_y, coh_w, bar_h))
        pygame.draw.rect(
            self.surface,
            (200, 180, 100),
            pygame.Rect(coh_x, coh_y, int(coh_w * coh_ratio), bar_h),
        )
        coh_text = self.small_font.render(f"Coherence {coh}/{coh_max}", True, self.fg)
        self.surface.blit(coh_text, (coh_x + 4, coh_y - 18))

        # --- Character stats & coherence under bars ---
        y += bar_h + 12
        if hasattr(game, "character") and game.character:
            stats = game.character.stats
            line = (
                f"CON {stats.get('con',0)}  "
                f"AGI {stats.get('agi',0)}  "
                f"INT {stats.get('int',0)}  "
                f"RES {stats.get('res',0)}"
            )
            stats_text = self.small_font.render(line, True, self.fg)
            self.surface.blit(stats_text, (x, y))

            # coherence info
            verts_count = len(game.pattern.vertices) if hasattr(game, "pattern") else 0
            coh_limit = game._coherence_limit()
            y += 18
            label = f"Vertices {verts_count}/{coh_limit}"
            coh_text = self.small_font.render(label, True, self.fg)
            self.surface.blit(coh_text, (x, y))

        # tick / zone info on top-right
        tick_text = self.small_font.render(f"Tick: {game.current_tick}", True, self.fg)
        zx, zy, zz = getattr(game, "zone", (0, 0, game.level_index))
        level_text = self.small_font.render(f"Zone ({zx},{zy}) Depth {zz}", True, self.fg)
        self.surface.blit(tick_text, (self.width - tick_text.get_width() - 8, 12))
        self.surface.blit(level_text, (self.width - level_text.get_width() - 8, 30))


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
        bar_rect = pygame.Rect(0, self.height - self.ability_bar_height, self.width, self.ability_bar_height)
        pygame.draw.rect(self.surface, (15, 15, 28), bar_rect)

        bar_state = getattr(game, "ability_bar_state", None)
        if not bar_state:
            return

        # Dynamic hotkeys: renumber visible abilities 1..N every frame
        visible = bar_state.visible_abilities()
        for idx, ability in enumerate(visible):
            if hasattr(ability, "hotkey"):
                ability.hotkey = idx + 1

        # Model is maintained by DungeonScene/AbilityBarState; renderer is view-only.
        self.ability_bar_view.draw(
            surface=self.surface,
            game=game,
            bar_state=bar_state,
            bar_rect=bar_rect,
            small_font=self.small_font,
            fg=self.fg,
            width=self.width,
            icon_drawer=self._draw_ability_icon_for_bar,
        )


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

        self.draw_world(game.world)
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

        self.draw_status(game)
        self.draw_log(game)
        self.draw_ignite_overlay(game)
        self.draw_regrow_overlay(game)
        self.draw_ability_bar(game)
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
        # world position under cursor before zoom
        wx = (mx - self.origin_x) / self.tile
        wy = (my - self.origin_y) / self.tile

        new_zoom = self.zoom + delta_steps * 0.1
        new_zoom = max(0.3, min(6.0, new_zoom))
        if abs(new_zoom - self.zoom) < 1e-3:
            return
        self.zoom = new_zoom
        self.tile = max(8, int(self.base_tile * self.zoom))
        # refresh surfaces (fonts stay constant size)
        self.edges_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self.verts_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        # map font scales with zoom; UI fonts remain constant
        self.map_font = pygame.font.SysFont("consolas", max(8, int(self.tile)))
        # adjust origin so world point under cursor stays under cursor
        self.origin_x = mx - wx * self.tile
        self.origin_y = my - wy * self.tile

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

        global_visual = getattr(self, "global_visual_profile", None)

        if global_visual is None:
            # Normal path: just blit the panel as-is.
            self.display.blit(panel, panel_rect.topleft)
        else:
            # Cursed/blessed path: apply the visual profile to the whole panel.
            # We hand the letterboxed region to apply_visual_panel; it will
            # flip/rotate/offset within that rectangle.
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
