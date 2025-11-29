from dataclasses import dataclass
import heapq
from typing import Dict, Tuple, List, Optional, Callable

from edgecaster import config
from edgecaster.state.world import World
from edgecaster.state.actors import Actor, Stats
from edgecaster import mapgen
from edgecaster.patterns.activation import project_vertices, damage_from_vertices
from edgecaster.patterns import builder

Move = Tuple[int, int]


def _line_points(x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
    points = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def _los(world: World, a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    for (x, y) in _line_points(a[0], a[1], b[0], b[1]):
        if not world.in_bounds(x, y):
            return False
        tile = world.get_tile(x, y)
        if tile is None:
            return False
        if (x, y) == b:
            return True
        if not tile.walkable:
            return False
    return True


@dataclass
class MessageLog:
    capacity: int = 50
    messages: List[str] | None = None

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []

    def add(self, text: str) -> None:
        self.messages.append(text)
        if len(self.messages) > self.capacity:
            self.messages.pop(0)

    def tail(self, n: int) -> List[str]:
        return self.messages[-n:]


@dataclass
class LevelState:
    world: World
    actors: Dict[str, Actor]
    events: List[Tuple[int, int, Callable[[], None]]]
    order: int
    current_tick: int
    pattern: builder.Pattern
    pattern_anchor: Optional[Tuple[int, int]]
    activation_points: List[Tuple[float, float]]
    activation_ttl: int
    awaiting_terminus: bool
    need_fov: bool
    up_stairs: Optional[Tuple[int, int]] = None
    down_stairs: Optional[Tuple[int, int]] = None
    hover_vertex: Optional[int] = None  # for renderer hinting


class Game:
    def __init__(self, cfg: config.GameConfig, rng) -> None:
        self.cfg = cfg
        self.rng = rng
        self.log = MessageLog()
        self.place_range = cfg.place_range

        self.levels: Dict[int, LevelState] = {}
        self.current_level = 0
        self._next_id = 0

        # create level 0
        self.levels[0] = self._make_level(level_idx=0, up_pos=None)

        # spawn player
        px, py = self.levels[0].world.entry
        player = Actor(
            actor_id=self._new_id(),
            name="Edgecaster",
            pos=(px, py),
            faction="player",
            stats=Stats(hp=30, max_hp=30, mana=100, max_mana=100),
        )
        self.player_id = player.actor_id
        self.levels[0].actors[player.actor_id] = player
        # enemies
        self._spawn_enemies(self.levels[0], count=4)
        self.log.add("Imps lurk nearby. Move with arrows/WASD. F: activate rune. ESC to exit.")

        self._update_fov(self.levels[0])

    # --- helpers ---

    def _new_id(self) -> str:
        aid = f"act{self._next_id}"
        self._next_id += 1
        return aid

    def _make_level(self, level_idx: int, up_pos: Optional[Tuple[int, int]]) -> LevelState:
        world = World(width=self.cfg.world_width, height=self.cfg.world_height)
        mapgen.generate_basic(world, self.rng, up_pos=up_pos)
        return LevelState(
            world=world,
            actors={},
            events=[],
            order=0,
            current_tick=0,
            pattern=builder.Pattern(),
            pattern_anchor=None,
            activation_points=[],
            activation_ttl=0,
            awaiting_terminus=False,
            need_fov=True,
            up_stairs=world.up_stairs,
            down_stairs=world.down_stairs,
        )

    def _spawn_enemies(self, level: LevelState, count: int) -> None:
        spawned = 0
        attempts = 0
        while spawned < count and attempts < 200:
            attempts += 1
            x = self.rng.randint(1, level.world.width - 2)
            y = self.rng.randint(1, level.world.height - 2)
            if not level.world.is_walkable(x, y):
                continue
            if self._actor_at(level, (x, y)):
                continue
            aid = self._new_id()
            level.actors[aid] = Actor(aid, "Imp", (x, y), faction="hostile", stats=Stats(hp=5, max_hp=5))
            self._schedule(level, self.cfg.action_time_fast, lambda aid=aid, lvl=level: self._monster_act(lvl, aid))
            spawned += 1

    # --- scheduling ---

    def _schedule(self, level: LevelState, delay: int, action: Callable[[], None]) -> None:
        level.order += 1
        heapq.heappush(level.events, (level.current_tick + delay, level.order, action))

    def _advance_time(self, level: LevelState, delta: int) -> None:
        target = level.current_tick + delta
        while level.events and level.events[0][0] <= target:
            tick, _, action = heapq.heappop(level.events)
            level.current_tick = tick
            action()
        level.current_tick = target
        if level.activation_ttl > 0:
            level.activation_ttl = max(0, level.activation_ttl - delta)
            if level.activation_ttl == 0:
                level.activation_points = []
        if level.need_fov:
            self._update_fov(level)

    # --- actor queries ---

    def _actor_at(self, level: LevelState, pos: Tuple[int, int]) -> Optional[Actor]:
        for actor in level.actors.values():
            if actor.pos == pos and actor.alive:
                return actor
        return None

    def _all_actors(self, level: LevelState) -> List[Actor]:
        return [a for a in level.actors.values() if a.alive]

    def all_actors_current(self) -> List[Actor]:
        """Alive actors on the current level."""
        return self._all_actors(self._level())

    # --- player helpers ---

    def _level(self) -> LevelState:
        return self.levels[self.current_level]

    def _player(self) -> Actor:
        return self._level().actors[self.player_id]

    def projected_vertices(self) -> List[Tuple[float, float]]:
        lvl = self._level()
        if lvl.pattern_anchor is None:
            return []
        return project_vertices(lvl.pattern, lvl.pattern_anchor)

    def nearest_vertex(self, world_pos: Tuple[float, float]) -> Optional[int]:
        verts = self.projected_vertices()
        if not verts:
            return None
        wx, wy = world_pos
        best_idx = None
        best_d2 = 1e18
        for i, (vx, vy) in enumerate(verts):
            dx = vx - wx
            dy = vy - wy
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_idx = i
        return best_idx

    def neighbors_of(self, idx: int) -> List[int]:
        lvl = self._level()
        adj: Dict[int, List[int]] = {}
        for e in lvl.pattern.edges:
            adj.setdefault(e.a, []).append(e.b)
            adj.setdefault(e.b, []).append(e.a)
        return adj.get(idx, [])

    def neighbor_set_depth(self, seed: int, depth: int) -> List[int]:
        """Return unique vertices within depth hops (including seed)."""
        if depth <= 0:
            return [seed]
        visited = {seed}
        frontier = {seed}
        lvl = self._level()
        adj: Dict[int, List[int]] = {}
        for e in lvl.pattern.edges:
            adj.setdefault(e.a, []).append(e.b)
            adj.setdefault(e.b, []).append(e.a)
        for _ in range(depth):
            new_frontier = set()
            for node in frontier:
                for n in adj.get(node, []):
                    if n not in visited:
                        visited.add(n)
                        new_frontier.add(n)
            if not new_frontier:
                break
            frontier = new_frontier
        return list(visited)

    # --- placement ---

    def begin_place_mode(self) -> None:
        lvl = self._level()
        lvl.awaiting_terminus = True
        self.log.add(f"Select terminus within {self.place_range} tiles (click or arrows+Enter).")

    def try_place_terminus(self, target: Tuple[int, int]) -> None:
        lvl = self._level()
        if not lvl.awaiting_terminus:
            return
        px, py = self._player().pos
        dx = target[0] - px
        dy = target[1] - py
        dist2 = dx * dx + dy * dy
        if dist2 > self.place_range * self.place_range:
            self.log.add("Out of range.")
            return

        def do_place() -> None:
            lvl.pattern = builder.line_pattern((0.0, 0.0), (dx, dy))
            lvl.pattern_anchor = (px, py)
            self.log.add(f"Terminus placed at {target}.")

        self._schedule(lvl, self.cfg.place_time_ticks, do_place)
        self._advance_time(lvl, self.cfg.place_time_ticks)
        lvl.awaiting_terminus = False

    # --- actions ---

    def queue_player_move(self, delta: Move) -> None:
        lvl = self._level()
        dx, dy = delta
        self._handle_move_or_attack(lvl, self.player_id, dx, dy)
        self._advance_time(lvl, self.cfg.action_time_fast)

    def queue_player_activate(self, target_vertex: Optional[int]) -> None:
        lvl = self._level()
        self._activate_pattern_all(lvl, target_vertex)
        self._advance_time(lvl, self.cfg.action_time_fast)

    def queue_player_activate_seed(self, target_vertex: Optional[int]) -> None:
        lvl = self._level()
        self._activate_pattern_seed_neighbors(lvl, target_vertex)
        self._advance_time(lvl, self.cfg.action_time_fast)

    def queue_player_fractal(self, kind: str) -> None:
        lvl = self._level()
        if not lvl.pattern.vertices:
            self.log.add("No pattern to modify. Place a terminus first.")
            return
        segs = lvl.pattern.to_segments()
        if kind == "subdivide":
            gen = builder.SubdivideGenerator(parts=3)
        elif kind == "koch":
            gen = builder.KochGenerator(height_factor=0.35)
        elif kind == "branch":
            gen = builder.BranchGenerator(angle_deg=22.0, length_factor=0.45)
        elif kind == "extend":
            gen = builder.ExtendGenerator()
        else:
            self.log.add("Unknown fractal op.")
            return
        segs = gen.apply_segments(segs, max_segments=self.cfg.max_vertices)
        segs = builder.cleanup_duplicates(segs)
        if len(segs) > self.cfg.max_vertices:
            segs = segs[: self.cfg.max_vertices]
            self.log.add("Pattern capped at max vertices.")
        lvl.pattern = builder.Pattern.from_segments(segs)
        self._advance_time(lvl, self.cfg.action_time_fast)

    def reset_pattern(self) -> None:
        lvl = self._level()
        lvl.pattern = builder.Pattern()
        lvl.pattern_anchor = None
        lvl.activation_points = []
        lvl.activation_ttl = 0
        self.log.add("Rune reset.")

    def queue_meditate(self) -> None:
        lvl = self._level()
        player = self._player()
        before = player.stats.mana
        gain = 10
        player.stats.mana = min(player.stats.max_mana, player.stats.mana + gain)
        restored = player.stats.mana - before
        if restored > 0:
            self.log.add(f"You meditate and restore {restored} mana.")
        else:
            self.log.add("You meditate but feel already full of mana.")
        self._advance_time(lvl, 100)

    def use_stairs(self) -> None:
        lvl = self._level()
        player = self._player()
        tile = lvl.world.get_tile(*player.pos)
        if tile is None:
            return
        if tile.glyph == ">":
            target_level = self.current_level + 1
            up_pos = player.pos
            if target_level not in self.levels:
                self.levels[target_level] = self._make_level(target_level, up_pos=up_pos)
                self._spawn_enemies(self.levels[target_level], count=4)
            # move player
            del lvl.actors[self.player_id]
            dest_level = self.levels[target_level]
            dest_pos = dest_level.up_stairs or dest_level.world.entry
            player.pos = dest_pos
            dest_level.actors[self.player_id] = player
            self.current_level = target_level
            self.log.add(f"You descend to level {self.current_level}.")
            self._update_fov(dest_level)
        elif tile.glyph == "<" and self.current_level > 0:
            target_level = self.current_level - 1
            del lvl.actors[self.player_id]
            dest_level = self.levels[target_level]
            dest_pos = dest_level.down_stairs or dest_level.world.entry
            player.pos = dest_pos
            dest_level.actors[self.player_id] = player
            self.current_level = target_level
            self.log.add(f"You ascend to level {self.current_level}.")
            self._update_fov(dest_level)

    # --- movement & combat ---

    def _handle_move_or_attack(self, level: LevelState, actor_id: str, dx: int, dy: int) -> None:
        actor = level.actors.get(actor_id)
        if actor is None or not actor.alive:
            return
        x, y = actor.pos
        nx = x + dx
        ny = y + dy
        if not level.world.in_bounds(nx, ny):
            return
        # stair use is explicit, so only move/attack here
        target = self._actor_at(level, (nx, ny))
        if target and target.actor_id != actor_id and target.faction != actor.faction:
            self._attack(level, actor, target)
            return
        if not level.world.is_walkable(nx, ny):
            if actor_id == self.player_id:
                self.log.add("You bump into a wall.")
            return
        actor.pos = (nx, ny)
        if actor_id == self.player_id:
            level.need_fov = True

    def _attack(self, level: LevelState, attacker: Actor, defender: Actor) -> None:
        dmg = 1
        defender.stats.hp -= dmg
        defender.stats.clamp()
        if attacker.actor_id == self.player_id:
            self.log.add(f"You hit {defender.name} for {dmg}.")
        else:
            self.log.add(f"{attacker.name} hits you for {dmg}.")
        if defender.stats.hp <= 0:
            self.log.add(f"{defender.name} dies.")

    def _monster_act(self, level: LevelState, actor_id: str) -> None:
        actor = level.actors.get(actor_id)
        if actor is None or not actor.alive:
            return
        if self.player_id not in level.actors:
            # player not on this level; reschedule later
            self._schedule(level, self.cfg.action_time_fast, lambda aid=actor_id, lvl=level: self._monster_act(lvl, aid))
            return
        px, py = level.actors[self.player_id].pos
        ax, ay = actor.pos
        if abs(px - ax) + abs(py - ay) == 1:
            self._attack(level, actor, level.actors[self.player_id])
        else:
            steps = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
            dx, dy = self.rng.choice(steps)
            target = (ax + dx, ay + dy)
            if level.world.in_bounds(*target) and level.world.is_walkable(*target) and not self._actor_at(level, target):
                actor.pos = target
                if level.world.get_tile(*target).visible:
                    self.log.add(f"{actor.name} shuffles to {actor.pos}.")
        self._schedule(level, self.cfg.action_time_fast, lambda aid=actor_id, lvl=level: self._monster_act(lvl, aid))

    # --- pattern activation ---

    def _activation_origin(self, level: LevelState) -> Optional[Tuple[int, int]]:
        return level.pattern_anchor

    def _activate_pattern_all(self, level: LevelState, target_vertex: Optional[int]) -> None:
        if not level.pattern.vertices:
            self.log.add("No pattern defined.")
            return
        origin = self._activation_origin(level)
        if origin is None:
            self.log.add("Pattern has no anchor.")
            return
        world_vertices = project_vertices(level.pattern, origin)
        if target_vertex is None or target_vertex < 0 or target_vertex >= len(world_vertices):
            self.log.add("Select a vertex to target the circle.")
            return
        center = world_vertices[target_vertex]
        dmg_radius = self.cfg.pattern_damage_radius
        per_vertex = self.cfg.pattern_damage_per_vertex
        cap = self.cfg.pattern_damage_cap

        # pick vertices in radius
        active_vertices = []
        r2 = dmg_radius * dmg_radius
        for v in world_vertices:
            dx = v[0] - center[0]
            dy = v[1] - center[1]
            if dx * dx + dy * dy <= r2:
                active_vertices.append(v)

        mana_cost = len(active_vertices)
        player = self._player()
        if mana_cost == 0:
            self.log.add("No vertices in range of the target.")
            return
        if player.stats.mana < mana_cost:
            self.log.add(f"Not enough mana ({player.stats.mana}/{mana_cost}).")
            return
        player.stats.mana -= mana_cost
        player.stats.clamp()

        level.activation_points = active_vertices
        level.activation_ttl = self.cfg.pattern_overlay_ttl

        total_vertices = len(active_vertices)
        hits = 0
        for actor in list(level.actors.values()):
            if not actor.alive:
                continue
            if actor.actor_id == self.player_id or actor.faction == "player":
                continue
            tile = level.world.get_tile(*actor.pos)
            if tile is None or not tile.visible:
                continue
            # tile square center distance to circle, approximate coverage factor
            ax = actor.pos[0] + 0.5
            ay = actor.pos[1] + 0.5
            dx = ax - center[0]
            dy = ay - center[1]
            dist = (dx * dx + dy * dy) ** 0.5
            half_diag = 0.7071
            if dist <= dmg_radius - half_diag:
                coverage = 1.0
            elif dist >= dmg_radius + half_diag:
                coverage = 0.0
            else:
                span = (dmg_radius + half_diag) - (dmg_radius - half_diag)
                coverage = max(0.0, min(1.0, 1 - (dist - (dmg_radius - half_diag)) / span))
            if coverage <= 0:
                continue
            dmg = int(per_vertex * total_vertices * coverage)
            if dmg <= 0:
                continue
            hits += 1
            actor.stats.hp -= dmg
            self.log.add(f"Your rune sears {actor.name} for {dmg}.")
            if actor.stats.hp <= 0:
                self.log.add(f"{actor.name} is annihilated.")
        if hits == 0:
            self.log.add("Your rune fizzles; no foes in its reach.")

    def _activate_pattern_seed_neighbors(self, level: LevelState, target_vertex: Optional[int]) -> None:
        if not level.pattern.vertices:
            self.log.add("No pattern defined.")
            return
        origin = self._activation_origin(level)
        if origin is None:
            self.log.add("Pattern has no anchor.")
            return
        world_vertices = project_vertices(level.pattern, origin)
        if not world_vertices:
            self.log.add("No vertices to activate.")
            return
        if target_vertex is None or target_vertex < 0 or target_vertex >= len(world_vertices):
            self.log.add("Select a vertex to target.")
            return

        seed_idx = target_vertex
        active_indices = set(self.neighbor_set_depth(seed_idx, self.cfg.activate_neighbor_depth))
        active_vertices = [world_vertices[i] for i in active_indices if 0 <= i < len(world_vertices)]
        level.activation_points = active_vertices
        level.activation_ttl = self.cfg.pattern_overlay_ttl

        mana_cost = len(active_vertices)
        player = self._player()
        if player.stats.mana < mana_cost:
            self.log.add(f"Not enough mana ({player.stats.mana}/{mana_cost}).")
            return
        player.stats.mana -= mana_cost
        player.stats.clamp()

        per_vertex = self.cfg.pattern_damage_per_vertex
        hits = 0
        # damage enemies in tiles containing active vertices
        for ax, ay in active_vertices:
            tile_x = int(round(ax))
            tile_y = int(round(ay))
            target_actor = self._actor_at(level, (tile_x, tile_y))
            if target_actor and target_actor.actor_id != self.player_id and target_actor.faction != "player":
                target_actor.stats.hp -= per_vertex
                hits += 1
                self.log.add(f"Your focus bites {target_actor.name} for {per_vertex}.")
                if target_actor.stats.hp <= 0:
                    self.log.add(f"{target_actor.name} crumbles.")
        if hits == 0:
            self.log.add("Your focus fizzles; no foes in reach.")

    # --- FOV ---

    def _update_fov(self, level: LevelState, radius: int = 8) -> None:
        if self.player_id not in level.actors:
            return
        px, py = level.actors[self.player_id].pos
        level.world.clear_visibility()
        r2 = radius * radius
        for y in range(py - radius, py + radius + 1):
            for x in range(px - radius, px + radius + 1):
                if not level.world.in_bounds(x, y):
                    continue
                dx = x - px
                dy = y - py
                if dx * dx + dy * dy > r2:
                    continue
                if _los(level.world, (px, py), (x, y)):
                    tile = level.world.get_tile(x, y)
                    if tile:
                        tile.visible = True
                        tile.explored = True
        level.need_fov = False

    # --- exposed for renderer ---

    @property
    def world(self) -> World:
        return self._level().world

    @property
    def actors(self) -> Dict[str, Actor]:
        return self._level().actors

    @property
    def activation_points(self) -> List[Tuple[float, float]]:
        return self._level().activation_points

    @property
    def activation_ttl(self) -> int:
        return self._level().activation_ttl

    @property
    def pattern(self) -> builder.Pattern:
        return self._level().pattern

    @property
    def pattern_anchor(self) -> Optional[Tuple[int, int]]:
        return self._level().pattern_anchor

    @property
    def current_tick(self) -> int:
        return self._level().current_tick

    @property
    def level_index(self) -> int:
        return self.current_level

    @property
    def awaiting_terminus(self) -> bool:
        return self._level().awaiting_terminus

    @awaiting_terminus.setter
    def awaiting_terminus(self, value: bool) -> None:
        self._level().awaiting_terminus = value

    def set_target_cursor(self, pos: Tuple[int, int]) -> None:
        # helper for renderer if needed
        pass
