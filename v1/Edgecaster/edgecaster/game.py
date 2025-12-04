from dataclasses import dataclass
import heapq
from typing import Dict, Tuple, List, Optional, Callable

from edgecaster import config
from edgecaster.state.world import World
from edgecaster.state.actors import Actor, Stats
from edgecaster.state.entities import Entity
from edgecaster import mapgen
from edgecaster.patterns.activation import project_vertices, damage_from_vertices
from edgecaster.patterns import builder
from edgecaster.character import Character, default_character
from edgecaster.content import npcs
from . import lorenz
import math


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
    entities: Dict[str, Entity]
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
    spotted: set = None  # seen actors
    coord: Tuple[int, int, int] = (0, 0, 0)  # (x, y, depth)



class Game:
    def __init__(self, cfg: config.GameConfig, rng, character: Character | None = None) -> None:
        self.cfg = cfg
        self.rng = rng
        self.log = MessageLog()
        self.place_range = cfg.place_range
        # urgent message system (level-ups, death, important events)
        # urgent message system (level-ups, death, important events)
        self.urgent_message: str | None = None
        self.urgent_resolved: bool = True
        self.urgent_callback: Optional[Callable[[str], None]] = None

        # richer urgent metadata (title/body/choices) for the popup scene
        self.urgent_title: Optional[str] = None
        self.urgent_body: Optional[str] = None
        self.urgent_choices: Optional[List[str]] = None


        # character info
        self.character: Character = character or default_character()
        # XP / parameter defs based on character stats
        self.param_defs = self._init_param_defs()
        self.param_state = self._init_param_state()
        # generators the player "knows" for NPC rewards etc.
        self.unlocked_generators: List[str] = [self.character.generator]
        # start with params auto-maxed given current stats
        self._recalc_param_state_max()
        # fractal field for overworld generation
        # seed: use character seed if provided, else derive from rng
        if getattr(self.character, "use_random_seed", False):
            self.fractal_seed = self.rng.randint(0, 10**9)
        else:
            self.fractal_seed = getattr(self.character, "seed", None) or getattr(cfg, "seed", None)
        self.fractal_field = mapgen.FractalField(seed=self.fractal_seed)
        # world map render cache (surface + view window)
        self.world_map_cache = None
        self.world_map_c: complex | None = None
        self.world_map_rendering = False
        self.world_map_ready = False
        self.world_map_thread_started = False
        # flags
        self.map_requested = False


        # zones keyed by (x, y, depth)
        self.levels: Dict[Tuple[int, int, int], LevelState] = {}
        self.zone_coord: Tuple[int, int, int] = (0, 0, 0)
        self._next_id = 0
        # Simple player inventory: list of Entity objects the player is carrying.
        # For now it's a flat list; later we can support containers/stacking.
        self.inventory: List[Entity] = []
        # create starting zone
        self.levels[self.zone_coord] = self._make_zone(coord=self.zone_coord, up_pos=None)

        # --- Strange Attractor / Lorenz aura state (game-time, not renderer-time) ---
        self.lorenz_points: List[Tuple[float, float, float]] = []
        self.lorenz_sigma = 10.0
        self.lorenz_rho = 28.0
        self.lorenz_beta = 8.0 / 3.0
        self.lorenz_dt = 0.01
        # how many small Euler steps per game-tick; tweak to taste
        self.lorenz_steps_per_tick = 1
        # small random perturbation each step to break perfect symmetry
        self.lorenz_noise = 0.0007
        # Renderer hint: when True, the Lorenz trails/afterimages should be cleared
        self.lorenz_reset_trails: bool = False
        # how many Lorenz 'butterflies' orbit the player
        if getattr(self.character, "player_class", None) == "Strange Attractor":
            # Start with two; one feels a bit lonely.
            self.lorenz_num_points = 2
        else:
            # other classes start with no personal storm; we can repurpose this later
            self.lorenz_num_points = 0


        # center of the storm (tile coords, floats for possible smoothing later)
        self.lorenz_center_x: float | None = None
        self.lorenz_center_y: float | None = None

        # bookkeeping to detect teleports / zone changes
        self._lorenz_prev_pos: Optional[Tuple[int, int]] = None
        self._lorenz_prev_zone: Tuple[int, int, int] = self.zone_coord


        # spawn player
        px, py = self._level().world.entry
        player_name = self.character.name or "Edgecaster"
        player_stats = self._build_player_stats()
        player = Actor(
            id=self._new_id(),
            name=player_name,
            pos=(px, py),
            faction="player",
            stats=player_stats,
        )

        self.player_id = player.id
        lvl = self._level()
        lvl.actors[player.id] = player
        lvl.entities[player.id] = player
   

        # enemies
        self._spawn_enemies(self._level(), count=4)

        # optional little intro flourish (you can tweak or remove)
        import datetime
        year = datetime.date.today().year
        leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        leap_msg = "It's a leap year. Be careful!" if leap else "It's not a leap year."
        self.log.add(f"Welcome, {player_name}. {leap_msg}")
        self.log.add("Imps lurk nearby. Press ? for help.")

        self._update_fov(self._level())


    def _build_player_stats(self) -> Stats:
        con = self.character.stats.get("con", 0)
        res = self.character.stats.get("res", 0)
        intel = self.character.stats.get("int", 0)
        base_hp = 20 + con * 6
        base_mana = 50 + res * 12
        base_coh = max(0, intel * 20)
        return Stats(
            hp=base_hp,
            max_hp=base_hp,
            mana=base_mana,
            max_mana=base_mana,
            xp=0,
            level=1,
            xp_to_next=self._xp_needed_for_level(1),
            coherence=base_coh,
            max_coherence=base_coh,
        )

    def _init_param_defs(self) -> Dict[str, Dict[str, dict]]:
        # thresholds correspond to minimum stat to unlock the value at same index
        return {
            "branch": {
                "angle": {
                    "values": [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90],
                    "thresholds": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
                    "stat": "int",
                    "label": "Angle",
                },
                "count": {"values": [2, 3, 4, 5], "thresholds": [0, 3, 5, 7], "stat": "int", "label": "Branches"},
            },
            "koch": {
                "height": {"values": [0.25, 0.4, 0.6], "thresholds": [0, 3, 6], "stat": "int", "label": "Amplitude"},
                "flip": {"values": [False, True], "thresholds": [0, 5], "stat": "int", "label": "Mirror"},
            },
            "subdivide": {
                "parts": {"values": [2, 3, 4, 5, 6], "thresholds": [0, 2, 4, 6, 8], "stat": "int", "label": "Segments"},
            },
            "zigzag": {
                "parts": {"values": [4, 6, 8, 10], "thresholds": [0, 2, 4, 6], "stat": "int", "label": "Segments"},
                "amp": {"values": [0.1, 0.2, 0.3], "thresholds": [0, 3, 6], "stat": "int", "label": "Amplitude"},
            },
            "activate_all": {
                "radius": {"values": [0.5, 1.0, 1.5, 2.0, 3.0, 4.0], "thresholds": [0, 1, 2, 3, 5, 8], "stat": "res", "label": "Radius"},
                "damage": {"values": [1, 2, 3], "thresholds": [0, 4, 8], "stat": "res", "label": "Damage"},
            },
            "activate_seed": {
                "neighbor_depth": {"values": [1, 2, 3], "thresholds": [0, 3, 6], "stat": "res", "label": "Depth"},
                "damage": {"values": [1, 2, 3], "thresholds": [0, 4, 8], "stat": "res", "label": "Damage"},
            },
            "custom": {
                "amplitude": {"values": [1.0, 0.9, 0.8, 0.7], "thresholds": [0, 1, 2, 3], "stat": "int", "label": "Scale"},
            },
        }

    def _init_param_state(self) -> Dict[Tuple[str, str], int]:
        state: Dict[Tuple[str, str], int] = {}
        for action, params in self.param_defs.items():
            for key in params:
                state[(action, key)] = 0
        return state

    def _recalc_param_state_max(self) -> None:
        """Set all params to the highest tier allowed by current stats (for auto-max radii/neighbor depth)."""
        for action, params in self.param_defs.items():
            for key in params:
                if action == "custom" and key == "amplitude":
                    # keep custom amplitude at user choice (default 1.0)
                    continue
                allowed = self._allowed_index(action, key)
                if allowed < 0:
                    allowed = 0
                self.param_state[(action, key)] = allowed

    def _xp_needed_for_level(self, level: int) -> int:
        """XP needed to go from this level to the next."""
        return max(1, self.cfg.xp_base + self.cfg.xp_per_level * (level - 1))

    def _coherence_limit(self) -> int:
        """How many vertices before coherence drain starts (INT*4)."""
        intellect = self.character.stats.get("int", 0)
        return intellect * 4

    def _strength_limit(self) -> int:
        """How many activated vertices can be driven at once; scales with RES."""
        res = self.character.stats.get("res", 0)
        return 40 + res * 40

    def _fizzle_roll(self, over: int, limit: int) -> bool:
        """Return True if activation should fizzle (probability increases with overage)."""
        if over <= 0:
            return False
        # success chance = limit / (limit + over); failure chance grows with overage
        fail_chance = over / (limit + over)
        return self.rng.random() < fail_chance

    def _grant_xp(self, amount: int) -> None:
        if amount <= 0:
            return
        player = self._player()
        stats = player.stats
        stats.xp += amount
        while stats.xp_to_next > 0 and stats.xp >= stats.xp_to_next:
            stats.xp -= stats.xp_to_next
            stats.level += 1
            self._on_level_up(player)
            # stats may have changed; refresh parameter caps
            self._recalc_param_state_max()
        stats.xp_to_next = self._xp_needed_for_level(stats.level)
        # Recompute coherence from int
        intel = self.character.stats.get("int", 0)
        base_coh = max(0, intel * 20)
        stats.max_coherence = base_coh
        stats.coherence = min(stats.coherence, stats.max_coherence)

    def _on_level_up(self, player: Actor) -> None:
        con = self.character.stats.get("con", 0)
        res = self.character.stats.get("res", 0)
        hp_gain = 5 + con * 2
        mana_gain = 5 + res * 2
        player.stats.max_hp += hp_gain
        player.stats.max_mana += mana_gain
        player.stats.hp = player.stats.max_hp
        player.stats.mana = player.stats.max_mana
        self.set_urgent(
            f"You reach level {player.stats.level}! (+{hp_gain} HP, +{mana_gain} MP)",
            title="Level Up!",
            choices=["Continue..."],
        )

        # Strange Attractors gain an extra Lorenz butterfly each level.
        if getattr(self.character, "player_class", None) == "Strange Attractor":
            current = getattr(self, "lorenz_num_points", 2)
            if current < 2:
                # Just in case something weird happened; enforce the baseline.
                current = 2
            self.lorenz_num_points = current + 1

            # Re-seed the storm so the new butterfly count is applied.
            # Next time advance_lorenz runs, init_lorenz_points will use the new count.
            self.lorenz_points = []

            # Flavor text in the normal log (non-urgent).
            self.log.add("Another butterfly is attracted to the storm...")

    # --- helpers ---

    def _new_id(self) -> str:
        aid = f"act{self._next_id}"
        self._next_id += 1
        return aid
        
    def set_urgent(
        self,
        text: str,
        *,
        title: Optional[str] = None,
        choices: Optional[List[str]] = None,
    ) -> None:
        """Notify the UI of an urgent message.

        If a UI callback is installed, call it immediately; otherwise,
        fall back to the old flag-based behaviour.
        """
        # Remember structured fields so the UI can style the popup.
        self.urgent_title = title
        self.urgent_body = text
        self.urgent_choices = choices

        if self.urgent_callback is not None:
            # Let the current scene/UI handle it (typically by pushing
            # an UrgentMessageScene).
            self.urgent_callback(text)
        else:
            # Legacy behaviour: store flags so something else can poll.
            self.urgent_message = text
            self.urgent_resolved = False

        # Urgent messages still go into the scrolling log for history.
        self.log.add(text)




    def _make_zone(self, coord: Tuple[int, int, int], up_pos: Optional[Tuple[int, int]]) -> LevelState:
        x, y, depth = coord
        world = World(width=self.cfg.world_width, height=self.cfg.world_height)
        if depth == 0:
            mapgen.generate_fractal_overworld(world, self.fractal_field, coord, self.rng, up_pos=up_pos)
        else:
            mapgen.generate_basic(world, self.rng, up_pos=up_pos)
        lvl = LevelState(
            world=world,
            actors={},
            entities={},   # NEW
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
            spotted=set(),
            coord=coord,
        )
        # mentor on starting overworld tile
        if coord == (0, 0, 0):
            self._spawn_mentor(lvl)

        # scatter some test berries on overworld levels
        if coord[2] == 0:  # depth == 0
            self._scatter_test_berries(lvl, count=10)

        return lvl


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
            if self._blocking_entity_at(level, (x, y)):
                continue
            aid = self._new_id()
            imp = Actor(
                aid,
                "Imp",
                (x, y),
                faction="hostile",
                stats=Stats(hp=5, max_hp=5),
                tags={"xp": self.cfg.xp_per_imp},
            )
            level.actors[aid] = imp
            level.entities[aid] = imp  # mirror into entities

            self._schedule(level, self.cfg.action_time_fast, lambda aid=aid, lvl=level: self._monster_act(lvl, aid))
            spawned += 1


    def _spawn_mentor(self, level: LevelState) -> None:
        """Place mentor NPC near entry if available."""
        entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
        x, y = entry
        offsets = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (-2, 0), (0, 2), (0, -2)]
        for dx, dy in offsets:
            tx, ty = x + dx, y + dy
            if not level.world.in_bounds(tx, ty):
                continue
            if not level.world.is_walkable(tx, ty):
                continue
            if self._actor_at(level, (tx, ty)):
                continue
            aid = self._new_id()
            mentor = Actor(
                id=aid,
                name="Mentor",
                pos=(tx, ty),
                faction="npc",
                stats=Stats(hp=1, max_hp=1),
                tags={"npc_id": "mentor"},
                disposition=10,
                affiliations=("edgecasters",),
            )
            level.actors[aid] = mentor
            level.entities[aid] = mentor
            break

    def _spawn_npcs(self, level: LevelState, count: int = 1) -> None:
        if count <= 0:
            return
        placed = 0
        attempts = 0
        defs = list(npcs.NPC_DEFS.items())
        while placed < count and attempts < 100 and defs:
            attempts += 1
            npc_id, npc_data = defs[min(placed, len(defs) - 1)]
            # place near entry if possible
            ex, ey = level.world.entry
            x = max(1, min(level.world.width - 2, ex + (placed * 2)))
            y = max(1, min(level.world.height - 2, ey + 1))
            if not level.world.is_walkable(x, y) or self._actor_at(level, (x, y)):
                continue
            aid = self._new_id()
            actor = Actor(
                id=aid,
                name=npc_data.get("name", "NPC"),
                pos=(x, y),
                faction="npc",
                stats=Stats(hp=1, max_hp=1),
                tags={"npc_id": npc_id},
                disposition=npc_data.get("base_disposition", 0),
                affiliations=tuple(npc_data.get("factions", [])),
            )
            level.actors[aid] = actor
            level.entities[aid] = actor  # now NPCs are entities too
            placed += 1


    def _scatter_test_berries(self, level: LevelState, count: int = 30) -> None:
        """Scatter some colored berry entities across the map as a test.

        Berries are simple non-blocking items with glyph 'b' and different colors.
        """
        if count <= 0:
            return

        berry_defs = [
            ("Blueberry",   "blueberry",   (80, 80, 200)),
            ("Raspberry",   "raspberry",   (200, 60, 120)),
            ("Strawberry",  "strawberry",  (230, 80, 80)),
        ]

        placed = 0
        attempts = 0
        max_attempts = count * 50

        world = level.world

        while placed < count and attempts < max_attempts:
            attempts += 1
            x = self.rng.randint(0, world.width - 1)
            y = self.rng.randint(0, world.height - 1)

            # must be a walkable, in-bounds tile
            if not world.in_bounds(x, y):
                continue
            if not world.is_walkable(x, y):
                continue

            # avoid stacking on actors or existing entities
            if self._actor_at(level, (x, y)):
                continue
            if self._entity_at(level, (x, y)):
                continue

            name, berry_id_tag, color = self.rng.choice(berry_defs)
            eid = self._new_id()

            ent = Entity(
                id=eid,
                name=name,
                pos=(x, y),
                glyph="b",
                color=color,
                kind="item",
                render_layer=1,
                blocks_movement=False,
                tags={"item_type": berry_id_tag, "test_berry": True},
            )

            level.entities[eid] = ent
            placed += 1



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

        # NEW: advance the Lorenz aura in game-time, not render-time
        self._advance_lorenz(level, delta)

        # NEW: coherence drain based on vertices
        self._coherence_tick(level, delta)


    def _coherence_tick(self, level: LevelState, delta: int) -> None:
        """Drain coherence each tick based on vertex count beyond INT*4."""
        player = self._player()
        stats = player.stats
        intel = self.character.stats.get("int", 0)
        discount = intel * 4
        verts = len(level.pattern.vertices) if level.pattern else 0
        over = max(0, verts - discount)
        if over <= 0:
            return
        # drain per tick: over/10 per requested design
        drain = over * delta / 10.0
        stats.coherence = int(max(0, stats.coherence - drain))
        if stats.coherence <= 0:
            # pattern unravels immediately
            level.pattern = builder.Pattern()
            level.pattern_anchor = None
            level.activation_points = []
            level.activation_ttl = 0
            self.log.add("Your pattern loses coherence and unravels.")
            stats.coherence = stats.max_coherence



    # --- Lorenz / strange-attractor aura, game-side ---

    def _init_lorenz_points(self) -> None:
        """Thin wrapper around lorenz.init_lorenz_points."""
        lorenz.init_lorenz_points(self)

    def _step_lorenz(self, steps: int) -> None:
        """Thin wrapper around lorenz.step_lorenz."""
        lorenz.step_lorenz(self, steps)

    def _advance_lorenz(self, level: LevelState, delta: int) -> None:
        """Advance the Lorenz aura only for Strange Attractors."""
        if not self.has_lorenz_aura:
            # Keep all Lorenz state dormant/cleared for other classes.
            self.lorenz_points = []
            self.lorenz_center_x = None
            self.lorenz_center_y = None
            self._lorenz_prev_pos = None
            self._lorenz_prev_zone = self.zone_coord
            return

        # First advance the continuous Lorenz dynamics
        lorenz.advance_lorenz(self, level, delta)

        # Then apply contact damage from butterflies to nearby hostiles
        self._lorenz_contact_damage(level)



    def _lorenz_contact_damage(self, level: LevelState) -> None:
        """Apply 'butterfly' contact damage to hostiles overlapping the Lorenz storm.

        We mirror the renderer's projection:
        - Take (x, z) from each Lorenz point
        - Rotate in the (x, z) plane by 30°
        - Subtract a fixed 'natural' Lorenz center between the wings
        - Scale to tile offsets, clamp to a radius
        - Map to world tiles around lorenz_center_x/lorenz_center_y
        """
        points = getattr(self, "lorenz_points", None)
        if not points:
            return

        # Only apply if we have a valid center; lorenz.advance_lorenz sets this to the player.
        center_x = self.lorenz_center_x
        center_y = self.lorenz_center_y
        if center_x is None or center_y is None:
            player = self._player()
            center_x, center_y = player.pos

        # Match the renderer's projection parameters
        angle = math.radians(30.0)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        lorenz_scale = 0.18          # same as AsciiRenderer.lorenz_scale
        lorenz_radius_tiles = 7      # same as AsciiRenderer.lorenz_radius_tiles

        # Project into a rotated 2D plane, track z band for the natural center
        points_2d: List[Tuple[float, float, float]] = []
        z_min = float("inf")
        z_max = float("-inf")

        for (x, y, z) in points:
            u = x
            v = z
            ux = cos_a * u - sin_a * v
            uy = sin_a * u + cos_a * v
            points_2d.append((ux, uy, z))
            if z < z_min:
                z_min = z
            if z > z_max:
                z_max = z

        if not points_2d:
            return

        if z_max <= z_min:
            z_max = z_min + 1e-6

        # Same 'natural' center as the renderer: between the wings
        z_mid = 0.5 * (z_min + z_max)
        x0 = 0.0
        natural_ux = cos_a * x0 - sin_a * z_mid
        natural_uy = sin_a * x0 + cos_a * z_mid

        # Map butterflies to tiles and count how many hit each tile
        tile_hits: Dict[Tuple[int, int], int] = {}
        r2_max = float(lorenz_radius_tiles * lorenz_radius_tiles)

        for (ux, uy, z) in points_2d:
            rel_x = ux - natural_ux
            rel_y = uy - natural_uy
            dx = rel_x * lorenz_scale
            dy = rel_y * lorenz_scale
            if dx * dx + dy * dy > r2_max:
                continue

            tx = int(round(center_x + dx))
            ty = int(round(center_y + dy))
            if not level.world.in_bounds(tx, ty):
                continue

            key = (tx, ty)
            tile_hits[key] = tile_hits.get(key, 0) + 1

        if not tile_hits:
            return

        verbs = ["cuts", "slices", "singes", "shocks", "jolts", "burns", "blinds", "chars", "sears"]
        base_damage = 1  # TODO: scale with stats later

        for actor in list(level.actors.values()):
            if not actor.alive:
                continue
            if actor.id == self.player_id:
                continue
            if actor.faction != "hostile":
                continue

            hits = tile_hits.get(actor.pos, 0)
            if hits <= 0:
                continue

            dmg = base_damage * hits
            if dmg <= 0:
                continue

            actor.stats.hp -= dmg
            actor.stats.clamp()

            verb = self.rng.choice(verbs)
            self.log.add(f"Your butterfly {verb} the {actor.name} for {dmg} damage.")

            # 50% chance to distract nearby foes until end of next turn
            if self.rng.random() < 0.5:
                if not self._has_status(actor, "distracted"):
                    self._add_status(actor, "distracted", duration=1, on_apply=f"The {actor.name} seems distracted by the butterflies.")
                else:
                    # refresh duration
                    actor.statuses["distracted"] = max(actor.statuses.get("distracted", 0), 1)

            if actor.stats.hp <= 0:
                self.log.add(f"{actor.name} dies.")
                self._kill_actor(level, actor)



    # --- actor queries ---

    def _actor_at(self, level: LevelState, pos: Tuple[int, int]) -> Optional[Actor]:
        for actor in level.actors.values():
            if actor.pos == pos and actor.alive:
                return actor
        return None

    def _all_actors(self, level: LevelState) -> List[Actor]:
        return [a for a in level.actors.values() if a.alive]

    # --- entity queries (non-actor entities) ---

    def _entity_at(self, level: LevelState, pos: Tuple[int, int]) -> Optional[Entity]:
        """Return the 'primary' entity at a tile, preferring non-actor items.

        If both an actor (e.g. the player) and an item occupy the same tile,
        we return the item first so that looking / picking up behaves
        intuitively.
        """
        item_candidate: Optional[Entity] = None
        actor_candidate: Optional[Entity] = None

        for ent in level.entities.values():
            if ent.pos != pos:
                continue

            # Heuristic: anything with a 'faction' attribute we treat as an actor.
            if hasattr(ent, "faction"):
                if actor_candidate is None:
                    actor_candidate = ent
            else:
                if item_candidate is None:
                    item_candidate = ent

        # Prefer items, but fall back to actors if no items present.
        return item_candidate or actor_candidate

    def _all_entities(self, level: LevelState) -> List[Entity]:
        return list(level.entities.values())
        
    def _blocking_entity_at(self, level: LevelState, pos: Tuple[int, int]) -> Optional[Entity]:
        """Return a blocking entity at this position, if any.

        Non-blocking entities (like berries/items) are ignored for movement.
        """
        ent = self._entity_at(level, pos)
        if ent and getattr(ent, "blocks_movement", False):
            return ent
        return None


    # --- status helpers ---

    def _add_status(self, actor: Actor, name: str, duration: int, on_apply: Optional[str] = None) -> None:
        actor.statuses[name] = max(duration, actor.statuses.get(name, 0))
        if on_apply:
            self.log.add(on_apply)

    def _tick_status(self, actor: Actor, name: str) -> None:
        if name not in actor.statuses:
            return
        actor.statuses[name] -= 1
        if actor.statuses[name] <= 0:
            del actor.statuses[name]

    def _has_status(self, actor: Actor, name: str) -> bool:
        return actor.statuses.get(name, 0) > 0

    def _get_zone(self, coord: Tuple[int, int, int], up_pos: Optional[Tuple[int, int]] = None) -> LevelState:
        if coord not in self.levels:
            self.levels[coord] = self._make_zone(coord, up_pos=up_pos)
            self._spawn_enemies(self.levels[coord], count=4)
        # entering any zone clears pattern and resets coherence
        lvl = self.levels[coord]
        lvl.pattern = builder.Pattern()
        lvl.pattern_anchor = None
        lvl.activation_points = []
        lvl.activation_ttl = 0
        # reset coherence to max on zone change
        player = self._player() if hasattr(self, "_player") else None
        if player:
            player.stats.coherence = player.stats.max_coherence
        return self.levels[coord]

    def all_actors_current(self) -> List[Actor]:
        """Alive actors on the current level."""
        return self._all_actors(self._level())

    def all_entities_current(self) -> List[Entity]:
        """All non-actor entities on the current level (items, features, etc.)."""
        return self._all_entities(self._level())

    def renderables_current(self) -> List[object]:
        """All things that should be rendered on the current level.

        This is a simple concatenation of non-actor entities (items, features)
        and living actors (player, monsters, NPCs).
        """
        return self.all_entities_current() + self.all_actors_current()



    # --- player helpers ---

    def _level(self) -> LevelState:
        return self.levels[self.zone_coord]

    def _player(self) -> Actor:
        return self._level().actors[self.player_id]
    @property
    def player_alive(self) -> bool:
        """True if the player is still present and has positive HP."""
        lvl = self._level()
        if self.player_id not in lvl.actors:
            return False
        return lvl.actors[self.player_id].stats.hp > 0
        
        
    def describe_current_tile(self, for_examine: bool = False) -> None:
        """Describe entities under the player when manually examining ('x').

        The for_examine flag is accepted for compatibility with the renderer,
        but the current behaviour is the same either way: this is only used
        for explicit 'look' commands, not auto-observe.
        """
        level = self._level()
        if self.player_id not in level.actors:
            return
        player = level.actors[self.player_id]
        pos = player.pos

        ent = self._entity_at(level, pos)

        # If there is no entity, or the only entity is the player themself,
        # show the cheeky message.
        if ent is None or getattr(ent, "id", None) == self.player_id:
            self.log.add("You see nothing here, save yourself.")
            return

        # Otherwise, describe whatever is here.
        self._describe_tile(level, pos, observer_id=self.player_id, auto=False)



    def _describe_tile(
        self,
        level: LevelState,
        pos: Tuple[int, int],
        observer_id: Optional[str] = None,
        auto: bool = False,
    ) -> None:
        """Log a description of entities at the given tile, if any.

        - auto=True: 'auto-observe' (e.g. stepping onto a tile)
        - auto=False: manual usage (normally routed through describe_current_tile)
        """
        ent = self._entity_at(level, pos)
        if not ent:
            return

        # If this is an auto-observe and the only thing here is the observer,
        # don't spam the log with "You see yourself" messages.
        if auto and observer_id is not None and getattr(ent, "id", None) == observer_id:
            return

        name = getattr(ent, "name", None) or "thing"
        article = "an" if name and name[0].lower() in "aeiou" else "a"
        self.log.add(f"You see here {article} {name.lower()}.")

    def show_help(self) -> None:
        """Show a brief help / keybind summary as an urgent popup."""
        lines = [
            "Core controls:",
            "  Movement: arrow keys / WASD / numpad",
            "  Activate rune: F",
            "  Examine tile underfoot: x",
            "  Pick up item: g",
            "  Inventory: i",
            "  Use stairs: > (down) / < (up)",
            "  World map: < from the overworld edge",
            "",
            "System / meta:",
            "  Toggle fullscreen: F11",
            "  Pause / menu: Esc",
            "",
            "Press any listed key in the dungeon to try it out.",
        ]
        body = "\n".join(lines)
        self.set_urgent(
            body,
            title="Help",
            choices=["Continue..."],
        )



    def player_pick_up(self) -> None:
        """Attempt to pick up an item under the player's feet."""
        level = self._level()
        if self.player_id not in level.actors:
            return
        player = level.actors[self.player_id]
        ent = self._entity_at(level, player.pos)
        if ent is None:
            self.log.add("There is nothing here to pick up.")
            return

        # Don't allow picking up actors or non-item entities (for now).
        if hasattr(ent, "faction") or getattr(ent, "kind", None) != "item":
            self.log.add("You can't pick that up.")
            return

        # Remove from the level's entity list.
        for eid, e in list(level.entities.items()):
            if e is ent:
                del level.entities[eid]
                break

        # Ensure inventory exists and append the item.
        if not hasattr(self, "inventory"):
            self.inventory = []  # type: ignore[assignment]
        self.inventory.append(ent)  # type: ignore[arg-type]

        name = getattr(ent, "name", None) or "item"
        article = "an" if name and name[0].lower() in "aeiou" else "a"
        self.log.add(f"You pick up {article} {name.lower()}.")
    def drop_inventory_item(self, index: int) -> None:
        """Drop an item from the inventory onto the player's current tile."""
        # Ensure inventory exists and index is in range
        if not hasattr(self, "inventory"):
            return
        inv = self.inventory  # type: ignore[assignment]
        if not (0 <= index < len(inv)):
            return

        level = self._level()
        if self.player_id not in level.actors:
            return
        player = level.actors[self.player_id]

        ent = inv.pop(index)

        # Place the entity at the player's current position in the world.
        ent.pos = player.pos
        # Reinsert into the level's entity dict using its existing id.
        level.entities[ent.id] = ent  # type: ignore[index]

        name = getattr(ent, "name", None) or "item"
        article = "an" if name and name[0].lower() in "aeiou" else "a"
        self.log.add(f"You drop {article} {name.lower()}.")

    def eat_inventory_item(self, index: int) -> None:
        """Consume an item from the inventory, if edible.

        For now, this is mainly used for test berries: they are removed
        from the inventory and we log a little flavour text.
        """
        if not hasattr(self, "inventory"):
            self.log.add("You have nothing to eat.")
            return
        inv = self.inventory  # type: ignore[assignment]
        if not (0 <= index < len(inv)):
            return

        ent = inv[index]
        tags = getattr(ent, "tags", {}) or {}

        # Basic edibility check: our test berries all carry a 'test_berry'
        # flag and an 'item_type' tag like 'blueberry', 'raspberry', etc.
        is_berry = bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }
        if not is_berry:
            name = getattr(ent, "name", None) or "item"
            self.log.add(f"You can't eat the {name.lower()}.")
            return

        # Actually consume the item.
        inv.pop(index)
        # Later we can hook in healing / buffs here; for now just flavour.
        self.log.add("That was tart!")


    @property
    def has_lorenz_aura(self) -> bool:
        """True if the current character should have the Lorenz storm aura."""
        return getattr(self.character, "player_class", None) == "Strange Attractor"

    def _reset_lorenz_on_zone_change(self, player: Actor) -> None:
        """Hard-snap the Lorenz storm to the player when changing zones."""
        if not self.has_lorenz_aura:
            return

        # Clear all continuous state so we don't smear across zones.
        self.lorenz_points = []
        self._lorenz_prev_pos = player.pos
        self._lorenz_prev_zone = self.zone_coord

        # Center the storm on the new player position immediately.
        px, py = player.pos
        self.lorenz_center_x = float(px)
        self.lorenz_center_y = float(py)

        # Tell the renderer to nuke any old afterimage frames.
        self.lorenz_reset_trails = True

        # Optionally seed fresh butterflies right away so you never get a "blank" frame.
        lorenz.init_lorenz_points(self)
        # Clear any existing pattern when entering a zone and reset coherence
        lvl = self._level()
        lvl.pattern = builder.Pattern()
        lvl.pattern_anchor = None
        lvl.activation_points = []
        lvl.activation_ttl = 0
        player.stats.coherence = player.stats.max_coherence



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

    # --- param helpers ---

    def _stat_value(self, stat: str) -> int:
        return int(self.character.stats.get(stat, 0))

    def _allowed_index(self, action: str, key: str) -> int:
        spec = self.param_defs[action][key]
        thresholds = spec["thresholds"]
        stat_val = self._stat_value(spec["stat"])
        allowed = -1
        for i, thr in enumerate(thresholds):
            if stat_val >= thr:
                allowed = i
        return allowed

    def _param_value(self, action: str, key: str):
        idx = self.param_state.get((action, key), 0)
        values = self.param_defs[action][key]["values"]
        idx = max(0, min(idx, len(values) - 1))
        return values[idx]

    def adjust_param(self, action: str, key: str, delta: int) -> Tuple[bool, str]:
        spec = self.param_defs.get(action, {}).get(key)
        if not spec:
            return False, "Unknown parameter"
        values = spec["values"]
        allowed = self._allowed_index(action, key)
        cur_idx = self.param_state.get((action, key), 0)
        new_idx = cur_idx + delta
        new_idx = max(0, min(new_idx, len(values) - 1))
        if new_idx > allowed:
            need = spec["thresholds"][new_idx]
            return False, f"Requires {spec['stat'].upper()} {need}"
        if new_idx == cur_idx:
            return False, ""
        self.param_state[(action, key)] = new_idx
        return True, ""

    def param_view(self, action: str) -> List[dict]:
        result = []
        params = self.param_defs.get(action, {})
        for key, spec in params.items():
            if action == "activate_all" and key == "damage":
                # damage scales automatically with resonance; hide from UI
                continue
            cur_idx = self.param_state.get((action, key), 0)
            allowed = self._allowed_index(action, key)
            value = spec["values"][cur_idx]
            label = spec.get("label", key)
            blocked = cur_idx >= allowed and cur_idx == len(spec["values"]) - 1
            # next requirement
            next_req = ""
            next_idx = cur_idx + 1
            if next_idx < len(spec["values"]):
                need = spec["thresholds"][next_idx]
                if self._stat_value(spec["stat"]) < need:
                    next_req = f"{spec['stat'].upper()} {need}"
            result.append(
                {
                    "key": key,
                    "label": label,
                    "value": value,
                    "allowed_idx": allowed,
                    "current_idx": cur_idx,
                    "next_req": next_req,
                }
            )
        return result

    def get_param_value(self, action: str, key: str):
        if action == "activate_all" and key == "damage":
            spec = self.param_defs.get(action, {}).get(key)
            if not spec:
                return self._param_value(action, key)
            allowed = self._allowed_index(action, key)
            values = spec["values"]
            allowed = max(0, min(allowed, len(values) - 1))
            return values[allowed]
        return self._param_value(action, key)

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

    def queue_player_wait(self) -> None:
        """Spend a turn doing nothing (useful for letting effects tick or luring enemies)."""
        lvl = self._level()
        self._advance_time(lvl, self.cfg.action_time_fast)
    
    def queue_player_activate(self, target_vertex: Optional[int]) -> None:
        lvl = self._level()
        self._activate_pattern_all(lvl, target_vertex)
        self._advance_time(lvl, self.cfg.action_time_fast)

    def queue_player_activate_seed(self, target_vertex: Optional[int]) -> None:
        lvl = self._level()
        self._activate_pattern_seed_neighbors(lvl, target_vertex)
        self._advance_time(lvl, self.cfg.action_time_fast)

    # --- interaction / NPCs ---

    def _adjacent_npc(self) -> Optional[Actor]:
        lvl = self._level()
        px, py = self._player().pos
        for actor in lvl.actors.values():
            if actor.faction == "npc" and actor.alive:
                ax, ay = actor.pos
                if max(abs(ax - px), abs(ay - py)) == 1:
                    return actor
        return None

    def talk_start(self):
        """Return dialogue info if an adjacent NPC exists."""
        npc = self._adjacent_npc()
        if not npc:
            self.log.add("No one nearby to talk to.")
            return None
        npc_id = npc.tags.get("npc_id")
        data = npcs.NPC_DEFS.get(npc_id, {})
        lines = data.get("dialogue", [f"{npc.name} waits patiently."])
        # Offer a generator you don't already have
        all_gens = ["koch", "branch", "zigzag"]
        owned = set(self.unlocked_generators)
        choices = [g for g in all_gens if g not in owned]
        if not choices:
            choices = []
            lines = lines + ["You already know every pattern I can teach."]
        return {"npc_id": npc_id, "name": npc.name, "lines": lines, "choices": choices}

    def talk_complete(self, npc_id: str | None, choice: Optional[str]) -> str:
        """Apply the selected reward, return a summary line."""
        if not choice:
            return "You end the conversation."
        all_gens = {"koch", "branch", "zigzag"}
        if choice not in all_gens:
            return "That knowledge eludes you."
        if choice in self.unlocked_generators:
            return f"You already know {choice.title()}."
        # teach new generator (replaces primary generator selection)
        self.unlocked_generators.append(choice)
        self.character.generator = choice
        return f"{choice.title()} added to your repertoire."

    def queue_player_fractal(self, kind: str) -> None:
        lvl = self._level()
        if not lvl.pattern.vertices:
            self.log.add("No pattern to modify. Place a terminus first.")
            return
        segs = lvl.pattern.to_segments()
        if kind == "subdivide":
            parts = self._param_value("subdivide", "parts")
            gen = builder.SubdivideGenerator(parts=parts)
        elif kind == "koch":
            height = self._param_value("koch", "height")
            flip = self._param_value("koch", "flip")
            gen = builder.KochGenerator(height_factor=height, flip=flip)
        elif kind == "branch":
            angle = self._param_value("branch", "angle")
            count = self._param_value("branch", "count")
            gen = builder.BranchGenerator(angle_deg=angle, length_factor=0.45, branch_count=count)
        elif kind == "extend":
            gen = builder.ExtendGenerator()
        elif kind == "zigzag":
            parts = self._param_value("zigzag", "parts")
            amp = self._param_value("zigzag", "amp")
            gen = builder.ZigzagGenerator(parts=parts, amplitude_factor=amp)
        elif kind == "custom":
            if not self.character.custom_pattern or len(self.character.custom_pattern) < 2:
                self.log.add("No custom pattern saved.")
                return
            amp = self._param_value("custom", "amplitude")
            gen = builder.CustomPolyGenerator(self.character.custom_pattern, amplitude=amp)
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
        # restore coherence to max when manually resetting
        player = self._player()
        player.stats.coherence = player.stats.max_coherence
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
        cx, cy, cz = self.zone_coord
        if tile.glyph == ">":
            target_coord = (cx, cy, cz + 1)
            up_pos = player.pos
            dest_level = self._get_zone(target_coord, up_pos=up_pos)
            # move player
            del lvl.actors[self.player_id]
            dest_pos = dest_level.up_stairs or dest_level.world.entry
            player.pos = dest_pos
            dest_level.actors[self.player_id] = player
            self.zone_coord = target_coord
            self.log.add(f"You descend to depth {self.zone_coord[2]}.")
            self._update_fov(dest_level)

            # NEW: snap the Lorenz storm to the new floor
            self._reset_lorenz_on_zone_change(player)

        elif tile.glyph == "<" and cz > 0:
            target_coord = (cx, cy, cz - 1)
            dest_level = self._get_zone(target_coord, up_pos=None)
            del lvl.actors[self.player_id]
            dest_pos = dest_level.down_stairs or dest_level.world.entry
            player.pos = dest_pos
            dest_level.actors[self.player_id] = player
            self.zone_coord = target_coord
            self.log.add(f"You ascend to depth {self.zone_coord[2]}.")
            self._update_fov(dest_level)

            # NEW: snap the Lorenz storm to the new floor
            self._reset_lorenz_on_zone_change(player)


    # --- movement & combat ---

    def _handle_move_or_attack(self, level: LevelState, id: str, dx: int, dy: int) -> None:
        actor = level.actors.get(id)
        if actor is None or not actor.alive:
            return

        x, y = actor.pos
        nx = x + dx
        ny = y + dy

        if not level.world.in_bounds(nx, ny):
            # only player can transition zones
            if id == self.player_id:
                self._transition_edge(actor, dx, dy)
            return

        # stair use is explicit, so only move/attack here
        target = self._actor_at(level, (nx, ny))
        if target and target.id != id and target.faction != actor.faction:
            self._attack(level, actor, target)
            return

        # treat blocking entities as solid, like walls
        blocking_ent = self._blocking_entity_at(level, (nx, ny))
        if blocking_ent:
            if id == self.player_id:
                self.log.add(f"You bump into the {blocking_ent.name}.")
            return

        if not level.world.is_walkable(nx, ny):
            if id == self.player_id:
                self.log.add("You bump into a wall.")
            return

        actor.pos = (nx, ny)
        if id == self.player_id:
            level.need_fov = True
            # Auto-look when the player steps onto a tile (but don't describe yourself)
            self._describe_tile(level, actor.pos, observer_id=actor.id, auto=True)




    def _attack(self, level: LevelState, attacker: Actor, defender: Actor) -> None:
        dmg = 1
        defender.stats.hp -= dmg
        defender.stats.clamp()
        if attacker.id == self.player_id:
            self.log.add(f"You hit {defender.name} for {dmg}.")
        else:
            self.log.add(f"{attacker.name} hits you for {dmg}.")
        if defender.stats.hp <= 0:
            # Player death uses the urgent popup; enemies die normally.
            if defender.id == self.player_id:
                cause = attacker.name
                self.set_urgent(
                    f"by way of {cause}",
                    title="You unravel...",
                    choices=["Continue..."],
                )
            else:
                self.log.add(f"{defender.name} dies.")
                self._kill_actor(level, defender)




    def _transition_edge(self, actor: Actor, dx: int, dy: int) -> None:
        """Move the player across zone boundaries."""
        level = self._level()
        w, h = level.world.width, level.world.height
        x, y = actor.pos
        nx = x + dx
        ny = y + dy
        zx, zy, zz = self.zone_coord
        dzx = 1 if nx >= w else -1 if nx < 0 else 0
        dzy = 1 if ny >= h else -1 if ny < 0 else 0
        if dzx == 0 and dzy == 0:
            return
        dest_coord = (zx + dzx, zy + dzy, zz)
        dest_x = 0 if nx >= w else (w - 1 if nx < 0 else nx)
        dest_y = 0 if ny >= h else (h - 1 if ny < 0 else ny)
        dest_level = self._get_zone(dest_coord, up_pos=None)
        # move actor
        del level.actors[self.player_id]
        actor.pos = (dest_x, dest_y)
        dest_level.actors[self.player_id] = actor
        self.zone_coord = dest_coord
        self.log.add(f"You travel to zone {dest_coord[0]},{dest_coord[1]} (depth {dest_coord[2]}).")
        self._update_fov(dest_level)
        # NEW: hard-snap Lorenz storm when wrapping zones
        self._reset_lorenz_on_zone_change(actor)


    def _monster_act(self, level: LevelState, id: str) -> None:
        actor = level.actors.get(id)
        if actor is None or not actor.alive:
            return
        if self.player_id not in level.actors:
            # player not on this level; reschedule later
            self._schedule(level, self.cfg.action_time_fast, lambda aid=id, lvl=level: self._monster_act(lvl, aid))
            return

        # status: Distracted (30% chance to lose turn)
        if self._has_status(actor, "distracted"):
            if self.rng.random() < 0.3:
                self.log.add(f"The distracted {actor.name} falters.")
                self._tick_status(actor, "distracted")
                self._schedule(level, self.cfg.action_time_fast, lambda aid=id, lvl=level: self._monster_act(lvl, aid))
                return
            else:
                self._tick_status(actor, "distracted")

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
        self._schedule(level, self.cfg.action_time_fast, lambda aid=id, lvl=level: self._monster_act(lvl, aid))

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
        # coherence check: overall pattern size vs INT
        coh_limit = self._coherence_limit()
        if len(world_vertices) > coh_limit and self._fizzle_roll(len(world_vertices) - coh_limit, coh_limit):
            self.log.add("This pattern strains your mind.")
            return
        if target_vertex is None or target_vertex < 0 or target_vertex >= len(world_vertices):
            self.log.add("Select a vertex to target the circle.")
            return
        center = world_vertices[target_vertex]
        dmg_radius = self.get_param_value("activate_all", "radius")
        per_vertex = self.get_param_value("activate_all", "damage")
        cap = self.cfg.pattern_damage_cap

        # pick vertices in radius
        active_vertices = []
        r2 = dmg_radius * dmg_radius
        for v in world_vertices:
            dx = v[0] - center[0]
            dy = v[1] - center[1]
            if dx * dx + dy * dy <= r2:
                active_vertices.append(v)
        str_limit = self._strength_limit()
        if len(active_vertices) > str_limit and self._fizzle_roll(len(active_vertices) - str_limit, str_limit):
            self.log.add("You strain to channel that many vertices at once and lose focus.")
            return

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
            if actor.id == self.player_id or actor.faction == "player":
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
                self._kill_actor(level, actor)

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
        # coherence check: overall pattern size vs INT
        coh_limit = self._coherence_limit()
        if len(world_vertices) > coh_limit and self._fizzle_roll(len(world_vertices) - coh_limit, coh_limit):
            self.log.add("Your pattern destabilizes; the activation slips away.")
            return
        depth = self._param_value("activate_seed", "neighbor_depth")
        active_indices = set(self.neighbor_set_depth(seed_idx, depth))
        active_vertices = [world_vertices[i] for i in active_indices if 0 <= i < len(world_vertices)]
        level.activation_points = active_vertices
        level.activation_ttl = self.cfg.pattern_overlay_ttl

        mana_cost = len(active_vertices)
        player = self._player()
        str_limit = self._strength_limit()
        if len(active_vertices) > str_limit and self._fizzle_roll(len(active_vertices) - str_limit, str_limit):
            self.log.add("This weave challenges your focus.")
            return
        if player.stats.mana < mana_cost:
            self.log.add(f"Not enough mana ({player.stats.mana}/{mana_cost}).")
            return
        player.stats.mana -= mana_cost
        player.stats.clamp()

        per_vertex = self._param_value("activate_seed", "damage")
        hits = 0
        # damage enemies in tiles containing active vertices
        for ax, ay in active_vertices:
            tile_x = int(round(ax))
            tile_y = int(round(ay))
            target_actor = self._actor_at(level, (tile_x, tile_y))
            if target_actor and target_actor.id != self.player_id and target_actor.faction != "player":
                target_actor.stats.hp -= per_vertex
                hits += 1
                self.log.add(f"Your focus bites {target_actor.name} for {per_vertex}.")
                if target_actor.stats.hp <= 0:
                    self.log.add(f"{target_actor.name} crumbles.")
                    self._on_enemy_killed(target_actor)
        if hits == 0:
            self.log.add("Your focus fizzles; no foes in reach.")

    # --- FOV ---

    def _on_enemy_killed(self, enemy: Actor) -> None:
        if enemy.faction != "hostile":
            return
        if enemy.tags.get("_xp_awarded"):
            return
        enemy.tags["_xp_awarded"] = 1
        xp_gain = enemy.tags.get("xp", self.cfg.xp_per_imp) if enemy.tags else self.cfg.xp_per_imp
        self._grant_xp(xp_gain)

    def _kill_actor(self, level: LevelState, actor: Actor) -> None:
        """Handle removing a dead actor from the world and awarding XP once."""
        # Award XP (handles faction check + duplicate protection)
        self._on_enemy_killed(actor)

        # Use the canonical id (actor_id is a property alias if you made Actor→Entity)
        aid = actor.actor_id

        # Remove from actors dict
        if aid in level.actors:
            del level.actors[aid]

        # Remove from entities dict (so it stops being rendered)
        if aid in level.entities:
            del level.entities[aid]


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
                    actor = self._actor_at(level, (x, y))
                    if actor and actor.id not in level.spotted:
                        level.spotted.add(actor.id)
                        if actor.id != self.player_id:
                            self.log.add(f"You spot a {actor.name}.")
        level.need_fov = False

    # --- exposed for renderer ---

    @property
    def world(self) -> World:
        return self._level().world

    @property
    def actors(self) -> Dict[str, Actor]:
        return self._level().actors

    @property
    def entities(self) -> Dict[str, Entity]:
        return self._level().entities

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
        return self.zone_coord[2]

    @property
    def zone(self) -> Tuple[int, int, int]:
        return self.zone_coord

    @property
    def awaiting_terminus(self) -> bool:
        return self._level().awaiting_terminus

    @awaiting_terminus.setter
    def awaiting_terminus(self, value: bool) -> None:
        self._level().awaiting_terminus = value

    def set_target_cursor(self, pos: Tuple[int, int]) -> None:
        # helper for renderer if needed
        pass
