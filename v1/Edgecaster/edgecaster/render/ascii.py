"""Pygame-based ASCII-style renderer with ability bar and targeting."""
import pygame
from typing import Tuple, List

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
        self.tile = tile
        self.surface = pygame.display.set_mode((width, height))
        pygame.display.set_caption("Edgecaster (ASCII prototype)")
        self.font = pygame.font.SysFont("consolas", tile)
        self.small_font = pygame.font.SysFont("consolas", 16)
        self.bg = (10, 10, 20)
        self.fg = (220, 230, 240)
        self.dim = (120, 130, 150)
        self.player_color = (255, 210, 80)
        self.monster_color = (255, 120, 120)
        self.rune_color = (90, 200, 255)
        self.pattern_color = (80, 180, 240)
        self.hp_color = (200, 80, 80)
        self.mana_color = (90, 160, 255)
        self.bar_bg = (40, 40, 60)
        self.ability_bar_height = 72
        self.abilities: List[Ability] = [
            Ability("Place", 1, "place"),
            Ability("Subdivide", 2, "subdivide"),
            Ability("Koch", 3, "koch"),
            Ability("Branch", 4, "branch"),
            Ability("Extend", 5, "extend"),
            Ability("Activate R", 6, "activate_all"),
            Ability("Activate N", 7, "activate_seed"),
            Ability("Reset", 8, "reset"),
            Ability("Meditate", 9, "meditate"),
        ]
        self.current_ability_index = 0
        self.target_cursor = (0, 0)

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
                px = x * self.tile
                py = y * self.tile
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
            px = x * self.tile
            py = y * self.tile
            if px >= self.width or py >= self.height:
                continue
            text = self.font.render(glyph, True, color)
            self.surface.blit(text, (px, py))

    def draw_pattern_overlay(self, game: Game) -> None:
        if not game.pattern.vertices:
            return
        origin = game.pattern_anchor
        if origin is None:
            return
        verts = project_vertices(game.pattern, origin)
        for e in game.pattern.edges:
            try:
                a = verts[e.a]
                b = verts[e.b]
            except IndexError:
                continue
            ax = int(a[0] * self.tile + self.tile * 0.5)
            ay = int(a[1] * self.tile + self.tile * 0.5)
            bx = int(b[0] * self.tile + self.tile * 0.5)
            by = int(b[1] * self.tile + self.tile * 0.5)
            pygame.draw.line(self.surface, self.pattern_color, (ax, ay), (bx, by), 1)
        for vx, vy in verts:
            px = int(vx * self.tile + self.tile * 0.5)
            py = int(vy * self.tile + self.tile * 0.5)
            pygame.draw.circle(self.surface, self.pattern_color, (px, py), max(2, self.tile // 6))

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
            px = int(vx * self.tile + self.tile * 0.5)
            py = int(vy * self.tile + self.tile * 0.5)
            pygame.draw.circle(self.surface, self.rune_color, (px, py), max(2, self.tile // 5))

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
        y = 6
        bar_w = 200
        bar_h = 12
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        hp_ratio = 0 if player.stats.max_hp == 0 else player.stats.hp / player.stats.max_hp
        pygame.draw.rect(self.surface, self.hp_color, pygame.Rect(x, y, int(bar_w * hp_ratio), bar_h))
        hp_text = self.small_font.render(f"HP {player.stats.hp}/{player.stats.max_hp}", True, self.fg)
        self.surface.blit(hp_text, (x + 4, y - 14))
        y += bar_h + 10
        pygame.draw.rect(self.surface, self.bar_bg, pygame.Rect(x, y, bar_w, bar_h))
        mp_ratio = 0 if player.stats.max_mana == 0 else player.stats.mana / player.stats.max_mana
        pygame.draw.rect(self.surface, self.mana_color, pygame.Rect(x, y, int(bar_w * mp_ratio), bar_h))
        mp_text = self.small_font.render(f"Mana {player.stats.mana}/{player.stats.max_mana}", True, self.fg)
        self.surface.blit(mp_text, (x + 4, y - 14))

    def draw_log(self, game: Game) -> None:
        start_y = game.world.height * self.tile + 8
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
            self.surface.blit(text, (rect.x + (rect.w - text.get_width()) // 2, rect.y + (rect.h - text.get_height()) // 2))
            x += box_w + gap

    def render(self, game: Game) -> None:
        clock = pygame.time.Clock()
        running = True
        self.target_cursor = game.actors[game.player_id].pos
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

            self.draw_world(game.world)
            self.draw_pattern_overlay(game)
            self.draw_activation_overlay(game)
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

        if pygame.K_1 <= key <= pygame.K_9:
            hk = key - pygame.K_0
            for idx, ability in enumerate(self.abilities):
                if ability.hotkey == hk:
                    self.current_ability_index = idx
                    if ability.action == "place":
                        self.target_cursor = game.actors[game.player_id].pos
                        game.begin_place_mode()
                    else:
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
                else:
                    self._trigger_action(game, ability.action)
                return

        tx = mx // self.tile
        ty = my // self.tile
        if not game.world.in_bounds(tx, ty):
            return
        if game.awaiting_terminus:
            self.target_cursor = (tx, ty)
            game.try_place_terminus((tx, ty))
        else:
            px, py = game.actors[game.player_id].pos
            dx = tx - px
            dy = ty - py
            if tx == px and ty == py:
                game.use_stairs()
            elif abs(dx) + abs(dy) == 1:
                game.queue_player_move((dx, dy))

    def _trigger_current(self, game: Game) -> None:
        ability = self.abilities[self.current_ability_index]
        self._trigger_action(game, ability.action)

    def _trigger_action(self, game: Game, action: str) -> None:
        if action == "place":
            self.target_cursor = game.actors[game.player_id].pos
            game.begin_place_mode()
        elif action == "subdivide":
            game.queue_player_fractal("subdivide")
        elif action == "koch":
            game.queue_player_fractal("koch")
        elif action == "branch":
            game.queue_player_fractal("branch")
        elif action == "extend":
            game.queue_player_fractal("extend")
        elif action == "activate_all":
            game.queue_player_activate()
        elif action == "activate_seed":
            game.queue_player_activate_seed()
        elif action == "reset":
            game.reset_pattern()
        elif action == "meditate":
            game.queue_meditate()

    def teardown(self) -> None:
        pygame.quit()
