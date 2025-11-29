"""Pygame-based ASCII-style renderer with ability bar and targeting."""
import pygame
import math
from typing import Tuple, List, Dict

from edgecaster.game import Game
from edgecaster.state.actors import Actor
from edgecaster.state.world import World
from edgecaster.patterns.activation import project_vertices


class Ability:
    def __init__(self, name: str, hotkey: int, action: str) -> None:
        self.name = name
        self.hotkey = hotkey  # 1-based numeric
        self.action = action
        self.rect: pygame.Rect | None = None


class AsciiRenderer:
    def __init__(self, width: int, height: int, tile: int) -> None:
        pygame.init()
        self.width = width
        self.height = height
        self.base_tile = tile
        self.zoom = 1.0
        self.tile = tile
        self.origin_x = 0
        self.origin_y = 0
        self.surface = pygame.display.set_mode((width, height))
        pygame.display.set_caption("Edgecaster (ASCII prototype)")
        self.font = pygame.font.SysFont("consolas", self.base_tile)
        self.small_font = pygame.font.SysFont("consolas", 16)
        self.bg = (10, 10, 20)
        self.fg = (220, 230, 240)
        self.dim = (120, 130, 150)
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
        self.abilities: List[Ability] = []
        self.current_ability_index = 0
        self.target_cursor = (0, 0)
        self.aim_action: str | None = None
        self.hover_vertex: int | None = None
        self.hover_neighbors: List[int] = []
        # pattern layers
        self.edges_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        self.verts_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        # cached glow sprites: key = (radius_px, color_tuple)
        self.glow_cache: Dict[Tuple[int, Tuple[int, int, int]], pygame.Surface] = {}

    def draw_world(self, world: World) -> None:
        self.surface.fill(self.bg)
        for y in range(world.height):
            for x in range(world.width):
                tile = world.tiles[y][x]
                if not tile.explored and not tile.visible:
                    continue
                color = self.fg if tile.visible else self.dim
                ch = tile.glyph
                text = self.font.render(ch, True, color)
                px = x * self.tile + self.origin_x
                py = y * self.tile + self.origin_y
                if px >= self.width or py >= self.height:
                    continue
                self.surface.blit(text, (px, py))

    def _actor_visual(self, actor: Actor) -> Tuple[str, Tuple[int, int, int]]:
        if actor.faction == "player":
            return "@", self.player_color
        if actor.faction == "hostile":
            return "m", self.monster_color
        return "?", self.fg

    def draw_actors(self, world: World, actors) -> None:
        for actor in actors:
            x, y = actor.pos
            if not world.in_bounds(x, y):
                continue
            tile = world.get_tile(x, y)
            if not tile or not tile.visible:
                continue
            glyph, color = self._actor_visual(actor)
            px = x * self.tile + self.origin_x
            py = y * self.tile + self.origin_y
            if px >= self.width or py >= self.height:
                continue
            text = self.font.render(glyph, True, color)
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

        # edges with gradient, thicker AA line (no halo)
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
        if self.aim_action not in ("activate_all", "activate_seed"):
            return
        origin = game.pattern_anchor
        if origin is None or not game.pattern.vertices:
            return
        verts = project_vertices(game.pattern, origin)
        if self.hover_vertex is None or self.hover_vertex >= len(verts):
            return
        if self.aim_action == "activate_all":
            radius = game.cfg.pattern_damage_radius if hasattr(game, "cfg") else 1.25
            center = verts[self.hover_vertex]
            cx = int(center[0] * self.tile + self.tile * 0.5 + self.origin_x)
            cy = int(center[1] * self.tile + self.tile * 0.5 + self.origin_y)
            pygame.draw.circle(self.surface, (120, 200, 255), (cx, cy), int(radius * self.tile), width=1)
            r2 = radius * radius
            for v in verts:
                dx = v[0] - center[0]
                dy = v[1] - center[1]
                if dx * dx + dy * dy <= r2:
                    px = int(v[0] * self.tile + self.tile * 0.5 + self.origin_x)
                    py = int(v[1] * self.tile + self.tile * 0.5 + self.origin_y)
                    pygame.draw.circle(self.surface, (200, 240, 255), (px, py), max(3, self.tile // 5))
        else:  # activate_seed
            center = verts[self.hover_vertex]
            px = int(center[0] * self.tile + self.tile * 0.5 + self.origin_x)
            py = int(center[1] * self.tile + self.tile * 0.5 + self.origin_y)
            pygame.draw.circle(self.surface, (255, 230, 120), (px, py), max(5, self.tile // 3))
            targets = [self.hover_vertex] + [idx for idx in self.hover_neighbors if idx is not None]
            seen = set()
            ordered_targets = []
            for idx in targets:
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
                color = (200, 220, 255) if idx != self.hover_vertex else (255, 230, 120)
                pygame.draw.circle(self.surface, color, (px, py), max(3, self.tile // 5))
                tx = int(round(vx))
                ty = int(round(vy))
                rect = pygame.Rect(tx * self.tile + self.origin_x, ty * self.tile + self.origin_y, self.tile, self.tile)
                pygame.draw.rect(self.surface, color, rect, 1)

    def draw_activation_overlay(self, game: Game) -> None:
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
            # small pop using glow sprite
            sprite = self._get_glow_sprite(max(3, self.tile // 8), self.rune_color)
            self.surface.blit(sprite, sprite.get_rect(center=(px, py)))

    def draw_target_cursor(self, game: Game) -> None:
        if not game.awaiting_terminus:
            return
        tx, ty = self.target_cursor
        if not game.world.in_bounds(tx, ty):
            return
        px = tx * self.tile
        py = ty * self.tile
        rect = pygame.Rect(px, py, self.tile, self.tile)
        pygame.draw.rect(self.surface, (255, 255, 120), rect, 2)

    def draw_status(self, game: Game) -> None:
        player = game.actors[game.player_id]
        x = 8
        y = 24
        bar_w = 200
        bar_h = 12
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        hp_ratio = 0 if player.stats.max_hp == 0 else player.stats.hp / player.stats.max_hp
        pygame.draw.rect(self.surface, self.hp_color, pygame.Rect(x, y, int(bar_w * hp_ratio), bar_h))
        hp_text = self.small_font.render(f"HP {player.stats.hp}/{player.stats.max_hp}", True, self.fg)
        self.surface.blit(hp_text, (x + 4, y - 18))
        y += bar_h + 16
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        mp_ratio = 0 if player.stats.max_mana == 0 else player.stats.mana / player.stats.max_mana
        pygame.draw.rect(self.surface, self.mana_color, pygame.Rect(x, y, int(bar_w * mp_ratio), bar_h))
        mp_text = self.small_font.render(f"Mana {player.stats.mana}/{player.stats.max_mana}", True, self.fg)
        self.surface.blit(mp_text, (x + 4, y - 18))
        # stats under bars
        y += bar_h + 12
        if hasattr(game, "character") and game.character:
            stats = game.character.stats
            line = f"CON {stats.get('con',0)}  AGI {stats.get('agi',0)}  INT {stats.get('int',0)}  RES {stats.get('res',0)}"
            stats_text = self.small_font.render(line, True, self.fg)
            self.surface.blit(stats_text, (x, y))

    def draw_log(self, game: Game) -> None:
        start_y = self.height - self.ability_bar_height - 120
        lines = game.log.tail(5)
        y = start_y
        for line in lines:
            text = self.small_font.render(line, True, self.fg)
            self.surface.blit(text, (8, y))
            y += text.get_height() + 2
        tick_text = self.small_font.render(f"Tick: {game.current_tick}", True, self.fg)
        level_text = self.small_font.render(f"Level: {game.level_index}", True, self.fg)
        self.surface.blit(tick_text, (self.width - tick_text.get_width() - 8, start_y))
        self.surface.blit(level_text, (self.width - level_text.get_width() - 8, start_y + 18))

    def draw_ability_bar(self) -> None:
        bar_rect = pygame.Rect(0, self.height - self.ability_bar_height, self.width, self.ability_bar_height)
        pygame.draw.rect(self.surface, (15, 15, 28), bar_rect)
        margin = 8
        gap = 6
        n = len(self.abilities)
        avail_w = self.width - 2 * margin
        box_w = (avail_w - gap * (n - 1)) / n
        x = margin
        for idx, ability in enumerate(self.abilities):
            rect = pygame.Rect(int(x), bar_rect.top + 8, int(box_w), bar_rect.height - 16)
            ability.rect = rect
            if idx == self.current_ability_index:
                border = (255, 255, 180)
                fill = (45, 45, 70)
            else:
                border = (120, 120, 160)
                fill = (25, 25, 45)
            pygame.draw.rect(self.surface, fill, rect)
            pygame.draw.rect(self.surface, border, rect, 2)
            label = ability.name
            if ability.hotkey:
                label = f"{ability.hotkey}:{label}"
            text = self.small_font.render(label, True, self.fg)
            text_x = rect.x + (rect.w - text.get_width()) // 2
            text_y = rect.y + 2
            self.surface.blit(text, (text_x, text_y))
            # icon area below text
            icon_top = text_y + text.get_height() + 4
            icon_height = rect.bottom - icon_top - 4
            icon_height = max(12, icon_height)
            icon_rect = pygame.Rect(rect.x + 6, icon_top, rect.w - 12, icon_height)
            self._draw_ability_icon(icon_rect, ability.action)
            x += box_w + gap

    def render(self, game: Game) -> None:
        clock = pygame.time.Clock()
        running = True
        self.target_cursor = game.actors[game.player_id].pos
        if not self.abilities:
            self._build_abilities(game)
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if game.awaiting_terminus:
                            game.awaiting_terminus = False
                        else:
                            running = False
                    else:
                        self._handle_input(game, event.key)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_click(game, event.pos)
                elif event.type == pygame.MOUSEMOTION:
                    self._update_hover(game, event.pos)
                elif event.type == pygame.MOUSEWHEEL:
                    self._change_zoom(event.y)

            # continuous hover update if no motion event (keep in sync)
            if self.aim_action:
                self._update_hover(game, pygame.mouse.get_pos())

            self.draw_world(game.world)
            self.draw_pattern_overlay(game)
            self.draw_activation_overlay(game)
            self.draw_aim_overlay(game)
            self.draw_actors(game.world, game.all_actors_current())
            self.draw_target_cursor(game)
            self.draw_status(game)
            self.draw_log(game)
            self.draw_ability_bar()
            pygame.display.flip()
            clock.tick(60)

    def _handle_input(self, game: Game, key: int) -> None:
        mapping = {
            pygame.K_UP: (0, -1),
            pygame.K_DOWN: (0, 1),
            pygame.K_LEFT: (-1, 0),
            pygame.K_RIGHT: (1, 0),
            pygame.K_w: (0, -1),
            pygame.K_s: (0, 1),
            pygame.K_a: (-1, 0),
            pygame.K_d: (1, 0),
            pygame.K_q: (-1, -1),
            pygame.K_e: (1, -1),
            pygame.K_z: (-1, 1),
            pygame.K_c: (1, 1),
            pygame.K_KP1: (-1, 1),
            pygame.K_KP2: (0, 1),
            pygame.K_KP3: (1, 1),
            pygame.K_KP4: (-1, 0),
            pygame.K_KP5: (0, 0),
            pygame.K_KP6: (1, 0),
            pygame.K_KP7: (-1, -1),
            pygame.K_KP8: (0, -1),
            pygame.K_KP9: (1, -1),
        }
        if game.awaiting_terminus:
            if key in mapping:
                tx, ty = self.target_cursor
                dx, dy = mapping[key]
                nt = (tx + dx, ty + dy)
                if game.world.in_bounds(*nt):
                    self.target_cursor = nt
            elif key in (pygame.K_RETURN, pygame.K_SPACE):
                game.try_place_terminus(self.target_cursor)
            return

        if self.aim_action in ("activate_all", "activate_seed") and key in (pygame.K_RETURN, pygame.K_SPACE):
            target_idx = self._current_hover_vertex(game)
            if self.aim_action == "activate_all":
                game.queue_player_activate(target_idx)
            else:
                game.queue_player_activate_seed(target_idx)
            self.aim_action = None
            return

        if pygame.K_1 <= key <= pygame.K_9:
            hk = key - pygame.K_0
            for idx, ability in enumerate(self.abilities):
                if ability.hotkey == hk:
                    self.current_ability_index = idx
                    if ability.action == "place":
                        self.target_cursor = game.actors[game.player_id].pos
                        game.begin_place_mode()
                        self.aim_action = None
                    else:
                        if ability.action in ("activate_all", "activate_seed"):
                            self.aim_action = ability.action
                        else:
                            self.aim_action = None
                        if ability.action not in ("activate_all", "activate_seed"):
                            self._trigger_action(game, ability.action)
                    return

        if key in mapping:
            game.queue_player_move(mapping[key])
            return
        if key == pygame.K_f:
            self._trigger_action(game, "activate_all")
            return
        if key in (pygame.K_PERIOD, pygame.K_GREATER, pygame.K_COMMA, pygame.K_LESS):
            game.use_stairs()
            return
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            self._trigger_current(game)

    def _handle_click(self, game: Game, pos) -> None:
        mx, my = pos
        for idx, ability in enumerate(self.abilities):
            if ability.rect and ability.rect.collidepoint(mx, my):
                self.current_ability_index = idx
                if ability.action == "place":
                    self.target_cursor = game.actors[game.player_id].pos
                    game.begin_place_mode()
                    self.aim_action = None
                else:
                    if ability.action in ("activate_all", "activate_seed"):
                        self.aim_action = ability.action
                    else:
                        self.aim_action = None
                    if ability.action not in ("activate_all", "activate_seed"):
                        self._trigger_action(game, ability.action)
                return

        tx = int((mx - self.origin_x) // self.tile)
        ty = int((my - self.origin_y) // self.tile)
        if not game.world.in_bounds(tx, ty):
            return
        if game.awaiting_terminus:
            self.target_cursor = (tx, ty)
            game.try_place_terminus((tx, ty))
        else:
            if self.aim_action in ("activate_all", "activate_seed"):
                target_idx = self._current_hover_vertex(game)
                if self.aim_action == "activate_all":
                    game.queue_player_activate(target_idx)
                else:
                    game.queue_player_activate_seed(target_idx)
                self.aim_action = None
            else:
                px, py = game.actors[game.player_id].pos
                dx = tx - px
                dy = ty - py
                if tx == px and ty == py:
                    game.use_stairs()
                elif max(abs(dx), abs(dy)) == 1:
                    game.queue_player_move((int(dx), int(dy)))

    def _trigger_current(self, game: Game) -> None:
        ability = self.abilities[self.current_ability_index]
        self._trigger_action(game, ability.action)

    def _trigger_action(self, game: Game, action: str) -> None:
        if action == "place":
            self.target_cursor = game.actors[game.player_id].pos
            game.begin_place_mode()
            self.aim_action = None
        elif action == "subdivide":
            self.aim_action = None
            game.queue_player_fractal("subdivide")
        elif action == "koch":
            self.aim_action = None
            game.queue_player_fractal("koch")
        elif action == "branch":
            self.aim_action = None
            game.queue_player_fractal("branch")
        elif action == "extend":
            self.aim_action = None
            game.queue_player_fractal("extend")
        elif action == "zigzag":
            self.aim_action = None
            game.queue_player_fractal("zigzag")
        elif action == "activate_all":
            self.aim_action = "activate_all"
            self._update_hover(game, pygame.mouse.get_pos())
        elif action == "activate_seed":
            self.aim_action = "activate_seed"
            self._update_hover(game, pygame.mouse.get_pos())
        elif action == "reset":
            self.aim_action = None
            game.reset_pattern()
        elif action == "meditate":
            self.aim_action = None
            game.queue_meditate()

    def _current_hover_vertex(self, game: Game) -> int | None:
        return self.hover_vertex

    def _update_hover(self, game: Game, mouse_pos: Tuple[int, int]) -> None:
        if self.aim_action not in ("activate_all", "activate_seed"):
            self.hover_vertex = None
            self.hover_neighbors = []
            return
        mx, my = mouse_pos
        wx = (mx - self.origin_x) / self.tile
        wy = (my - self.origin_y) / self.tile
        idx = game.nearest_vertex((wx, wy))
        self.hover_vertex = idx
        if idx is not None and self.aim_action == "activate_seed":
            self.hover_neighbors = game.neighbor_set_depth(idx, game.cfg.activate_neighbor_depth)
        else:
            self.hover_neighbors = []

    def _change_zoom(self, delta_steps: int) -> None:
        # delta_steps: mouse wheel y (positive zoom in)
        mouse_pos = pygame.mouse.get_pos()
        mx, my = mouse_pos
        # world position under cursor before zoom
        wx = (mx - self.origin_x) / self.tile
        wy = (my - self.origin_y) / self.tile

        new_zoom = self.zoom + delta_steps * 0.1
        new_zoom = max(0.6, min(2.0, new_zoom))
        if abs(new_zoom - self.zoom) < 1e-3:
            return
        self.zoom = new_zoom
        self.tile = max(8, int(self.base_tile * self.zoom))
        # refresh surfaces (fonts stay constant size)
        self.edges_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        self.verts_surface = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
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

    def _build_abilities(self, game: Game) -> None:
        # build ability list based on character choices
        char = getattr(game, "character", None)
        generator_choice = "koch"
        illuminator_choice = "radius"
        if char:
            generator_choice = char.generator
            illuminator_choice = char.illuminator

        abilities: List[Ability] = []
        hotkey = 1

        def add(name: str, action: str):
            nonlocal hotkey
            abilities.append(Ability(name, hotkey, action))
            hotkey += 1

        add("Place", "place")
        add("Subdivide", "subdivide")
        add("Extend", "extend")
        # generator-specific
        gen_label = {"koch": "Koch", "branch": "Branch", "zigzag": "Zigzag"}.get(generator_choice, generator_choice)
        gen_action = generator_choice if generator_choice in ("koch", "branch", "zigzag") else None
        if gen_action:
            add(gen_label, gen_action)

        # illuminator choice
        if illuminator_choice == "radius":
            add("Activate R", "activate_all")
        elif illuminator_choice == "neighbors":
            add("Activate N", "activate_seed")
        else:
            add("Activate R", "activate_all")
            add("Activate N", "activate_seed")

        add("Reset", "reset")
        add("Meditate", "meditate")

        self.abilities = abilities

    def _draw_ability_icon(self, rect: pygame.Rect, action: str) -> None:
        pad = 2
        surf = pygame.Surface((max(8, rect.w - 2 * pad), max(8, rect.h - 2 * pad)), pygame.SRCALPHA)
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

        verts = []
        segs = []
        extra = None

        if action == "place":
            verts = [(0.15, 0.5), (0.85, 0.5)]
            segs = [(0, 1)]
            extra = {"strong": [1]}
        elif action == "subdivide":
            verts = [(0.1, 0.5), (0.35, 0.5), (0.65, 0.5), (0.9, 0.5)]
            segs = [(0, 1), (1, 2), (2, 3)]
        elif action == "extend":
            verts = [(0.15, 0.55), (0.45, 0.55), (0.55, 0.45), (0.85, 0.45)]
            segs = [(0, 1), (2, 3)]
        elif action == "koch":
            verts = [(0.1, 0.7), (0.5, 0.2), (0.9, 0.7)]
            segs = [(0, 1), (1, 2)]
        elif action == "branch":
            verts = [(0.1, 0.6), (0.5, 0.6), (0.9, 0.35), (0.9, 0.85)]
            segs = [(0, 1), (1, 2), (1, 3)]
            extra = {"strong": [1]}
        elif action == "zigzag":
            verts = [(0.1, 0.65), (0.3, 0.35), (0.5, 0.65), (0.7, 0.35), (0.9, 0.65)]
            segs = [(0, 1), (1, 2), (2, 3), (3, 4)]
        elif action == "activate_all":
            verts = [(0.25, 0.5), (0.75, 0.5), (0.5, 0.25), (0.5, 0.75)]
            segs = [(0, 1), (2, 3)]
            extra = {"circle": True}
        elif action == "activate_seed":
            verts = [(0.5, 0.5), (0.2, 0.5), (0.8, 0.5), (0.5, 0.2), (0.5, 0.8)]
            segs = [(1, 0), (0, 2), (3, 0), (0, 4)]
            extra = {"strong": [0], "boxes": list(range(1, len(verts)))}
        elif action == "reset":
            pygame.draw.line(surf, (200, 140, 140), (4, h // 2), (w - 4, h // 2), 2)
            pygame.draw.line(surf, (200, 140, 140), (4, h // 2 + 6), (w - 4, h // 2 + 6), 2)
        elif action == "meditate":
            pygame.draw.circle(surf, (180, 200, 255), (w // 2, h // 2), max(4, w // 3), width=2)
            pygame.draw.circle(surf, (120, 180, 255), (w // 2, h // 2), max(2, w // 6))

        if verts:
            draw_lines(verts, segs)
            if extra and extra.get("circle"):
                pygame.draw.circle(surf, (120, 200, 255), (w // 2, h // 2), int(min(w, h) * 0.4), width=2)
            strong = extra.get("strong") if extra else []
            draw_vertices(verts, strong_idx=strong[0] if strong else None)
            if extra and extra.get("boxes"):
                for idx in extra["boxes"]:
                    if 0 <= idx < len(verts):
                        p = to_px(*verts[idx])
                        box_size = max(6, min(w, h) // 4)
                        rect_box = pygame.Rect(p[0] - box_size // 2, p[1] - box_size // 2, box_size, box_size)
                        pygame.draw.rect(surf, (180, 220, 255), rect_box, 1)

        self.surface.blit(surf, rect.topleft)
    def teardown(self) -> None:
        pygame.quit()
