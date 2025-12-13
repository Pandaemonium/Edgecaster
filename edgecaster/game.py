from dataclasses import dataclass, field
import heapq
import threading
from typing import Dict, Tuple, List, Optional, Callable
from pathlib import Path
import yaml
from collections import deque
import pygame


from edgecaster import config, events
from edgecaster.state.world import World
from edgecaster.state.actors import Actor, Stats, Human
from edgecaster.state.entities import Entity
from edgecaster.enemies import factory as enemy_factory


from edgecaster import mapgen
from edgecaster.content import pois as poi_content
from edgecaster.patterns.activation import project_vertices, damage_from_vertices
from edgecaster.patterns import builder
from edgecaster.character import Character, default_character
from edgecaster.content import npcs
from edgecaster.systems.actions import get_action, action_delay
from edgecaster.patterns import colors as pattern_colors
from edgecaster.patterns import motion as pattern_motion
from edgecaster.systems import ai
import edgecaster.enemies.templates as enemy_templates
from . import lorenz
import math


Move = Tuple[int, int]

@dataclass
class LabState:
    chaos: float = 0.0
    chaos_threshold: float = 1.0

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
    capacity: int = 100000
    messages: deque[str] | None = None

    def __post_init__(self) -> None:
        if self.messages is None:
            # deque for O(1) append/pop with bounded history
            self.messages = deque(maxlen=self.capacity)
        # Bump capacity for older saves.
        if self.capacity < 1000:
            self.capacity = 100000
        if self.messages is not None:
            self.messages = deque(self.messages, maxlen=self.capacity)

    def add(self, text: str) -> None:
        self.messages.append(text)

    def tail(self, n: int) -> List[str]:
        if n <= 0:
            return []
        return list(self.messages)[-n:]


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
    spotted: set[str] = field(default_factory=set)  # seen actors
    coord: Tuple[int, int, int] = (0, 0, 0)  # (x, y, depth)
    lab_state: Optional["LabState"] = None  # lab-specific state if this is a lab zone



class Game:
    def __init__(self, cfg: config.GameConfig, rng, character: Character | None = None) -> None:
        self.cfg = cfg
        self.rng = rng
        self.log = MessageLog()
        self.place_range = cfg.place_range
        # debug log file
        self.debug_log_path = Path(__file__).resolve().parent.parent / "debug.log"
        # clear debug log each run
        try:
            self.debug_log_path.write_text("", encoding="utf-8")
        except Exception:
            pass
        # ensure enemy templates are loaded once up-front
        try:
            enemy_templates.load_enemy_templates(logger=self._debug)
        except Exception as e:
            self._debug(f"Enemy template load failed: {e!r}")
        # urgent message system (level-ups, death, important events)
        self.urgent_message: str | None = None
        self.urgent_resolved: bool = True
        self.urgent_callback: Optional[Callable[[str], None]] = None
        self.scene_manager = None  # type: ignore[assignment]

        # richer urgent metadata (title/body/choices) for the popup scene
        self.urgent_title: Optional[str] = None
        self.urgent_body: Optional[str] = None
        self.urgent_choices: Optional[List[str]] = None
        # Optional effect to run when a choice is selected
        self.urgent_choice_effect: Optional[Callable[[int, "Game"], None]] = None

        # character info
        self.character: Character = character or default_character()
        # Currency: bismuth wallet
        self.bismuth: int = 0

        # What the HUD should call the thing-you-are:
        # initially your class, later overwritten by body-hops.
        base_label = (
            getattr(self.character, "char_class", None)
            or getattr(self.character, "player_class", None)
        )
        self.current_host_label: Optional[str] = base_label

        # XP / parameter defs based on character stats
        self.param_defs = self._init_param_defs()
        self.param_state = self._init_param_state()
        # generators the player "knows" for NPC rewards etc.
        self.unlocked_generators: List[str] = [self.character.generator]
        # start with params auto-maxed given current stats
        self._recalc_param_state_max()
        # custom patterns (list of vertex lists)
        self.custom_patterns: List[list] = []
        if getattr(self.character, "custom_pattern", None):
            self.custom_patterns.append(self.character.custom_pattern)
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
        # per-tile julia grid (x coords, y coords) derived from overmap view
        self.tile_julia_grid: dict[str, list[float]] | None = None
        # flags
        self.map_requested = False
        self.fractal_editor_requested = False
        self.fractal_editor_state = None


        # zones keyed by (x, y, depth)
        self.levels: Dict[Tuple[int, int, int], LevelState] = {}
        # start roughly at world center so Julia coords near (0,0)
        center_zx = self.cfg.world_map_screens // 2
        center_zy = self.cfg.world_map_screens // 2
        self.zone_coord: Tuple[int, int, int] = (center_zx, center_zy, 0)
        self._next_id = 0
        # initialize overmap parameters/grid eagerly (fixed bounds) and kick off async render
        self._init_overmap_params_and_grid()

        # Inventories: mapping from owner id to a list of carried Entities.
        # Initially empty; per-owner lists are created lazily via get_inventory().
        self.inventories: Dict[str, List[Entity]] = {}
        # Simple SFX cache for lightweight sounds
        self._sfx_cache: Dict[str, object] = {}

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

        # Choose a template id for the player base body.
        # Later you can put this on Character (race/species field).
        player_tmpl_id = getattr(self.character, "template_id", None) or "human_base"

        # Build a base Actor from the data-driven factory
        player = enemy_factory.spawn_enemy(player_tmpl_id, (px, py))

        # Override template defaults with run-specific data
        player.id = self._new_id()
        player.name = player_name
        player.pos = (px, py)
        player.faction = "player"      # make sure this is canonical
        player.stats = player_stats    # use character-derived stats
        player.description = "You attempt to perceive yourself, but can do so only incompletely."

        # --- Class kit / action set -----------------------------------
        # Everyone gets the boring core verbs (never shown on the bar):
        actions = ["move", "wait"]

        # Determine the class as chosen in character creation.
        player_class = (
            getattr(self.character, "player_class", None)
            or getattr(self.character, "char_class", None)
        )

        # Fractal config from character creation
        generator_choice = getattr(self.character, "generator", "koch")
        illuminator_choice = getattr(self.character, "illuminator", "radius")

        if player_class == "Kochbender":
            # Kochbender standard 7-slot kit (old behaviour):
            #
            # 1. Place
            # 2. Subdivide
            # 3. Extend
            # 4. Generator (Koch / Branch / Zigzag / Custom)
            # 5. Activate (R or N depending on illuminator)
            # 6. Reset
            # 7. Meditate
            #
            # The bar will render these in order using the ActionDef labels.

            # Core rune operators
            actions += [
                "place",
                "subdivide",
                "extend",
                generator_choice,   # e.g. "koch", "branch", "zigzag", "custom"
            ]

            # Illuminator: choose *one* activator based on char creation
            if illuminator_choice == "radius":
                actions.append("activate_all")     # "Activate R"
            elif illuminator_choice == "neighbors":
                actions.append("activate_seed")    # "Activate N"
            else:
                # Fallback: default to radius-style activator
                actions.append("activate_all")

            # Meta slots
            actions.append("reset")
            actions.append("meditate")
            actions.append("rainbow_edges")
            actions.append("verdant_edges")
            actions.append("winter_hue")
            actions.append("freeze")
            actions.append("ignite")
            actions.append("regrow")
            actions.append("push_pattern")

        # For now, all other classes keep only move/wait (empty ability bar).
        player.actions = tuple(actions)

        # Tag as 'the player'
        player.tags.setdefault("is_player", True)
        if player_class:
            player.tags.setdefault("class", player_class)


        self.player_id = player.id
        lvl = self._level()
        lvl.actors[player.id] = player
        lvl.entities[player.id] = player

        # --- Give the player a recursive inventory test item -----------------
        #
        # This uses the normal debug_inventory template (a container item),
        # but we:
        #   1) Put it directly into the player's inventory
        #   2) Make *its own* inventory list contain itself.
        #
        # So when you open it, you'll see an item that is the same bag,
        # and opening that again just keeps nesting visually.
        try:
            recursive_item = self._spawn_entity_from_template(
                "debug_inventory",
                player.pos,
                overrides={
                    "name": "recursive Inventory",
                    "tags": {"recursive_inventory": True},
                },
            )
        except Exception:
            recursive_item = None

        if recursive_item is not None:
            # Put the bag into the player's starting inventory.
            self.player_inventory.append(recursive_item)

            # Now give *that bag* its own inventory, containing itself.
            rec_inv = self.get_inventory(recursive_item.id)
            rec_inv.append(recursive_item)

            recursive_item.description = (
                "A Platonic bag that appears to contain, among other things, itself."
            )

        # DEBUG: spawn a few Inventory entities near the starting position so we
        # can pick them up and test nested containers / recursion.
        self.debug_spawn_inventory_near_player(count=8)



 

        self._spawn_enemies(self._level(), count=4)

        # optional little intro flourish (you can tweak or remove)
        import datetime
        year = datetime.date.today().year
        leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        leap_msg = "It's a leap year. Be careful!" if leap else "It's not a leap year."
        self.log.add(f"Welcome, {player_name}. {leap_msg}")
        self.log.add("Imps lurk nearby. Press ? for help.")

        self._update_fov(self._level())

        # decide a lab zone for this run (one random overworld zone)
        self.lab_zone: Tuple[int, int] = (
            self.rng.randrange(0, self.cfg.world_map_screens),
            self.rng.randrange(0, self.cfg.world_map_screens),
        )
        self.log.add(f"A mysterious lab is rumored at overworld zone ({self.lab_zone[0]}, {self.lab_zone[1]}). Press < to view the world map.")
        # Inject the lab POI with the chosen coord so mapgen/POI system can build it.
        lab_poi = poi_content.POIS.get("lab")
        if lab_poi:
            poi_content.POIS["lab"] = poi_content.POI(
                id=lab_poi.id,
                coord=(self.lab_zone[0], self.lab_zone[1], 0),
                npcs=lab_poi.npcs,
                structures=lab_poi.structures,
            )
        # Known POI markers (zone coords) for world map rendering / hints (after lab injected)
        self.poi_locations: Dict[str, Tuple[int, int, int]] = {
            pid: tuple(poi.coord) for pid, poi in poi_content.POIS.items()
        }


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
    # --- currency helpers ---

    def adjust_currency(self, amount: int, *, log: bool = True) -> int:
        """Adjust bismuth wallet; clamps at 0. Returns new balance."""
        try:
            self.bismuth = max(0, int(self.bismuth + amount))
        except Exception:
            pass
        if log and amount != 0:
            if amount > 0:
                self.log.add(f"You gain {amount} bismuth.")
            else:
                self.log.add(f"You spend {-amount} bismuth.")
        return getattr(self, "bismuth", 0)

    def _play_sfx(self, rel_path: str, volume: float = 1.0) -> None:
        """Lightweight SFX helper with simple caching; best-effort (fails silently)."""
        try:
            root = Path(__file__).resolve().parent.parent
            path = root / rel_path
            if not path.exists():
                return
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            cache = getattr(self, "_sfx_cache", {})
            snd = cache.get(path)
            if snd is None:
                snd = pygame.mixer.Sound(str(path))
                cache[path] = snd
                self._sfx_cache = cache
            snd.set_volume(max(0.0, min(1.0, float(volume))))
            snd.play()
        except Exception:
            return

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
            "winter_hue": {
                "radius": {"values": [1, 2, 3, 4], "thresholds": [0, 2, 4, 6], "stat": "res", "label": "Radius"},
                "scale": {"values": [1.0, 1.5, 2.0, 3.0], "thresholds": [0, 5, 10, 15], "stat": "res", "label": "Intensity Scale"},
            },
            "freeze": {
                "damage_scale": {"values": [0.05, 0.1, 0.2, 0.3], "thresholds": [0, 2, 4, 6], "stat": "res", "label": "Dmg / Blue"},
                "slow_scale": {"values": [0.02, 0.04, 0.06, 0.08], "thresholds": [0, 2, 4, 6], "stat": "res", "label": "Slow / Blue"},
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

    # --- level-up stat logic ---

    def _auto_stat_roll(self) -> None:
        """Roll a stat increase based on class weights."""
        weights = getattr(self.character, "stat_weights", None)
        if not weights:
            weights = {"con": 0.25, "res": 0.25, "int": 0.25, "agi": 0.25}
        keys = list(weights.keys())
        vals = [max(0.0, float(weights[k])) for k in keys]
        total = sum(vals)
        if total <= 0:
            vals = [1.0 for _ in keys]
            total = len(keys)
        vals = [v / total for v in vals]
        r = self.rng.random()
        acc = 0.0
        chosen = keys[-1]
        for k, w in zip(keys, vals):
            acc += w
            if r <= acc:
                chosen = k
                break
        self.character.stats[chosen] = self.character.stats.get(chosen, 0) + 1
        self.log.add(f"Your {chosen.upper()} grows (+1).")

    def _choose_stat_upgrade(self) -> Optional[str]:
        """Even levels: choose a stat to upgrade. For now auto-picks highest weight."""
        options = ["con", "res", "int", "agi"]
        weights = getattr(self.character, "stat_weights", None)
        if not weights:
            weights = {k: 1.0 for k in options}
        chosen = max(options, key=lambda k: weights.get(k, 0))
        self.character.stats[chosen] = self.character.stats.get(chosen, 0) + 1
        self.log.add(f"You focus your training: {chosen.upper()} +1.")
        return chosen

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
        # Stat upgrades: odd levels auto-roll by class weights; even levels choose.
        lvl = player.stats.level
        if lvl % 2 == 1:
            self._auto_stat_roll()
        else:
            chosen = self._choose_stat_upgrade()
            if chosen is None:
                chosen = "res"  # fallback
            self.character.stats[chosen] = self.character.stats.get(chosen, 0) + 1
        # refresh params after stat change
        self._recalc_param_state_max()
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
        on_choice_effect: Optional[Callable[[int, "Game"], None]] = None,
    ) -> None:

        """Notify the UI of an urgent message.

        If a UI callback is installed, call it immediately; otherwise,
        fall back to the old flag-based behaviour.
        """

        # Remember structured fields so the UI can style the popup.
        self.urgent_title = title
        self.urgent_body = text
        self.urgent_choices = choices
        self.urgent_choice_effect = on_choice_effect

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




    def queue_actor_action(self, actor_id: str, action_name: str, **kwargs) -> None:
        """
        Perform a generic action for the given actor and advance time based
        on the action's speed.

        This uses the existing per-level event system (_advance_time)
        instead of introducing a separate scheduler.
        """
        lvl = self._level()
        action_def = get_action(action_name)
        delay = action_delay(self.cfg, action_def)  # use cfg, not 'config'

        # Determine cooldown origin: first item in inventory that grants this ability, else actor.
        origin = None
        actor = None
        try:
            actor = self._level().actors.get(actor_id)
        except Exception:
            actor = None
        if actor is not None:
            inv = self.inventories.get(actor_id, [])
            for item in inv:
                if getattr(item, "tags", {}).get("grants_ability") == action_name:
                    origin = item
                    break
        if origin is None and actor is not None:
            origin = actor

        # Cooldown gate
        if origin is not None:
            cd = getattr(origin, "cooldowns", {}).get(action_name, 0)
            if cd > 0:
                if actor_id == self.player_id:
                    self.log.add("That ability is recharging.")
                return

        # Do the actual action right now.
        action_def.func(self, actor_id, **kwargs)

        # Apply cooldown if defined
        if origin is not None and action_def.cooldown_ticks > 0:
            try:
                origin.cooldowns[action_name] = action_def.cooldown_ticks
            except Exception:
                pass

        # Slow effects: multiply delay by any active slow multiplier on the actor.
        if actor is not None:
            mult = self._slow_mult(actor)
            if mult > 1.0:
                delay = int(math.ceil(delay * mult))

        # Advance game time by the appropriate amount.
        self._advance_time(lvl, delay)

    def queue_player_action(self, action_name: str, **kwargs) -> None:
        """Convenience wrapper to queue an action for the current player."""
        self.queue_actor_action(self.player_id, action_name, **kwargs)

    # --- ability / actions helpers -------------------------------------------------

    def grant_ability(self, action_name: str) -> bool:
        """
        Add an action to the current player's action list if not already present.

        Returns True if added. Also invalidates the ability bar state when present.
        """
        try:
            lvl = self._level()
            player = lvl.actors.get(self.player_id)
        except Exception:
            player = None
        if player is None:
            return False

        current = list(getattr(player, "actions", ()) or [])
        if action_name in current:
            return False
        current.append(action_name)
        player.actions = tuple(current)

        if hasattr(self, "ability_bar_state"):
            try:
                self.ability_bar_state.invalidate()
            except Exception:
                pass
        return True





    def get_inventory(self, owner_id: str) -> List[Entity]:
        """Return the inventory list for a given owner id, creating it if needed.

        This keeps all inventories in a single registry on the Game object,
        while still conceptually treating them as per-entity state.
        """
        return self.inventories.setdefault(owner_id, [])

    @property
    def player_inventory(self) -> List[Entity]:
        """Convenience accessor for the current host's inventory.

        This automatically follows body-swaps by using the current player_id.
        """
        return self.get_inventory(self.player_id)





    def build_tile_julia_grid(self) -> None:
        """Precompute per-tile Julia coordinates across the whole world grid."""
        if not getattr(self, "overmap_params", None):
            return
        p = self.overmap_params
        # Require julia extents from overmap_params
        if not all(k in p for k in ("view_min_jx", "view_max_jx", "view_min_jy", "view_max_jy")):
            return
        total_x = self.cfg.world_map_screens * self.cfg.world_width
        total_y = self.cfg.world_map_screens * self.cfg.world_height
        if total_x <= 0 or total_y <= 0:
            return
        step_x = (p["view_max_jx"] - p["view_min_jx"]) / max(1, total_x - 1)
        step_y = (p["view_max_jy"] - p["view_min_jy"]) / max(1, total_y - 1)
        jx = [p["view_min_jx"] + i * step_x for i in range(total_x)]
        jy = [p["view_min_jy"] + i * step_y for i in range(total_y)]
        self.tile_julia_grid = {
            "x": jx,
            "y": jy,
            "total_x": total_x,
            "total_y": total_y,
            "step_x": step_x,
            "step_y": step_y,
            "view_min_jx": p["view_min_jx"],
            "view_max_jx": p["view_max_jx"],
            "view_min_jy": p["view_min_jy"],
            "view_max_jy": p["view_max_jy"],
        }

    def _init_overmap_params_and_grid(self) -> None:
        """Set fixed overmap params from curated c-path bounds and start background render."""
        # If already initialized, do nothing.
        if getattr(self, "overmap_params", None) and getattr(self, "tile_julia_grid", None):
            return
        try:
            from edgecaster.scenes.world_map_scene import WorldMapScene
            wm = WorldMapScene(self, span=16)
            entry = wm._pick_visual_entry()
        except Exception:
            return
        cfg = self.cfg
        total_w = cfg.world_map_screens * cfg.world_width
        total_h = cfg.world_map_screens * cfg.world_height
        min_wx = 0.0
        min_wy = 0.0
        max_wx = float(total_w)
        max_wy = float(total_h)
        # stash params without surface; render thread will fill it
        self.overmap_params = {
            "min_wx": min_wx,
            "min_wy": min_wy,
            "span_x": max_wx,
            "span_y": max_wy,
            "visual_c": entry["c"],
            "surface_size": (0, 0),
            "surface": None,
            "orig_min_wx": min_wx,
            "orig_min_wy": min_wy,
            "orig_max_wx": max_wx,
            "orig_max_wy": max_wy,
            "view_max_wx": max_wx,
            "view_max_wy": max_wy,
            "orig_min_jx": entry["x_min"],
            "orig_max_jx": entry["x_max"],
            "orig_min_jy": entry["y_min"],
            "orig_max_jy": entry["y_max"],
            "view_min_jx": entry["x_min"],
            "view_max_jx": entry["x_max"],
            "view_min_jy": entry["y_min"],
            "view_max_jy": entry["y_max"],
        }
        # build grid immediately so locals can generate
        self.build_tile_julia_grid()
        # kick off background render
        self._start_world_map_thread()

    def _start_world_map_thread(self) -> None:
        if self.world_map_thread_started:
            return
        self.world_map_thread_started = True
        self.world_map_rendering = True
        t = threading.Thread(target=self._background_render_map, daemon=True)
        t.start()

    def _background_render_map(self) -> None:
        """Render overmap in a background thread using fixed params."""
        try:
            from edgecaster.scenes.world_map_scene import WorldMapScene
            wm = WorldMapScene(self, span=16)
            class Stub:
                def __init__(self, w: int, h: int) -> None:
                    self.width = w
                    self.height = h
            stub = Stub(self.cfg.view_width, self.cfg.view_height)
            surf, view = wm._render_overmap(stub)
            self.world_map_cache = {"surface": surf, "view": view, "key": (stub.width, stub.height, wm.span)}
            self.world_map_ready = True
        finally:
            self.world_map_rendering = False

    def _ensure_overmap_ready(self) -> None:
        """Ensure overmap params/grid exist; kick off background render if needed."""
        if getattr(self, "overmap_params", None) and getattr(self, "tile_julia_grid", None):
            return
        # initialize params/grid
        self._init_overmap_params_and_grid()
        # If no render in progress/ready, fall back to synchronous render to avoid missing data
        if not self.world_map_ready and not self.world_map_rendering:
            try:
                from edgecaster.scenes.world_map_scene import WorldMapScene
                wm = WorldMapScene(self, span=16)
                class Stub:
                    def __init__(self, w, h) -> None:
                        self.width = w
                        self.height = h
                stub = Stub(self.cfg.view_width, self.cfg.view_height)
                surf, view = wm._render_overmap(stub)
                self.world_map_cache = {"surface": surf, "view": view, "key": (stub.width, stub.height, wm.span)}
                self.world_map_ready = True
            finally:
                self.world_map_rendering = False

    def _make_zone(self, coord: Tuple[int, int, int], up_pos: Optional[Tuple[int, int]]) -> LevelState:
        x, y, depth = coord
        world = World(width=self.cfg.world_width, height=self.cfg.world_height)
        # Determine any POIs that hit this coord (used for lab/structures).
        poi_hits = [pid for pid, poi in poi_content.POIS.items() if tuple(poi.coord) == tuple(coord)]
        is_lab_zone = False
        for pid in poi_hits:
            poi = poi_content.POIS.get(pid)
            if not poi:
                continue
            for struct in getattr(poi, "structures", []) or []:
                if struct.get("kind") == "lab":
                    is_lab_zone = True
                    break
            if is_lab_zone:
                break

        if depth == 0 and is_lab_zone:
            mapgen.generate_lab(world, self.rng)
            lab_state = LabState()
        elif depth == 0:
            self._ensure_overmap_ready()
            jx_slice = jy_slice = None
            if getattr(self, "tile_julia_grid", None):
                gx0 = x * world.width
                gx1 = gx0 + world.width
                gy0 = y * world.height
                gy1 = gy0 + world.height
                xgrid = self.tile_julia_grid.get("x", [])
                ygrid = self.tile_julia_grid.get("y", [])
                # fall back to None if out of bounds
                if gx0 < 0 or gy0 < 0 or gx1 > len(xgrid) or gy1 > len(ygrid):
                    jx_slice = jy_slice = None
                else:
                    jx_slice = xgrid[gx0:gx1]
                    jy_slice = ygrid[gy0:gy1]
            mapgen.generate_fractal_overworld(
                world,
                self.fractal_field,
                coord,
                self.rng,
                up_pos=up_pos,
                overmap_params=self.overmap_params,
                jx_slice=jx_slice,
                jy_slice=jy_slice,
            )
            lab_state = None
        else:
            mapgen.generate_basic(world, self.rng, up_pos=up_pos, coord=coord)
            lab_state = None
        # Apply POIs (records ids on world)
        poi_hits = mapgen.apply_pois(world, coord)
        # Build starting structures (e.g., depot)
        if "starting_zone" in poi_hits:
            try:
                depot_info = mapgen.build_item_depot(world, self.rng, world.entry)
                world.depot_info = depot_info  # type: ignore[attr-defined]
            except Exception:
                world.depot_info = None  # type: ignore[attr-defined]
        if "lab" in poi_hits:
            # Already generated as a lab layout above; nothing extra for now.
            pass
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
            lab_state=lab_state,
        )
        # Spawn NPCs/entities from any POIs for this level (e.g., starting NPCs)
        self._spawn_poi_contents(lvl, coord)

        if coord == (0, 0, 0) and not getattr(self, "_academy_hint_shown", False):
            self._academy_hint_shown = True
            academy = self.poi_locations.get("academy")
            if academy:
                ax, ay, _ = academy
                self.log.add(f"You hear of an Academy at ({ax},{ay}).")

        # scatter some test berries on overworld levels
        if coord[2] == 0:  # depth == 0
            self._scatter_test_berries(lvl, count=10)
            # Place a destabilizer near entry for testing/availability.
            try:
                ex, ey = lvl.world.entry
                offsets = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (0, 2), (-2, 0), (0, -2)]
                for dx, dy in offsets:
                    tx, ty = ex + dx, ey + dy
                    if not lvl.world.in_bounds(tx, ty):
                        continue
                    if not lvl.world.is_walkable(tx, ty):
                        continue
                    if self._entity_at(lvl, (tx, ty)):
                        continue
                    ent = self._spawn_entity_from_template("destabilizer", (tx, ty))
                    if ent:
                        lvl.entities[ent.id] = ent
                        break
            except Exception:
                pass

        return lvl



    def _enemy_template_ids(self) -> List[str]:
        cached = getattr(self, "_enemy_ids_cache", None)
        if cached is not None:
            return cached

        content_dir = Path(__file__).resolve().parent / "content"
        yaml_path = content_dir / "enemies.yaml"

        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []

        enemy_ids: List[str] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            tid = entry.get("id")
            if not tid:
                continue

            faction = entry.get("faction", "hostile")
            tags = set(entry.get("tags", []) or [])

            # Only randomize true enemies
            if faction != "hostile":
                continue
            if "player_only" in tags:
                continue

            enemy_ids.append(tid)

        if not enemy_ids:
            enemy_ids = ["imp"]

        self._enemy_ids_cache = enemy_ids
        return enemy_ids





    def _spawn_enemies(self, level: LevelState, count: int) -> None:
        """Spawn a handful of enemies using the data-driven enemy factory."""
        spawned = 0
        attempts = 0
        while spawned < count and attempts < 200:
            attempts += 1
            x = self.rng.randint(1, level.world.width - 2)
            y = self.rng.randint(1, level.world.height - 2)
            pos = (x, y)

            if not level.world.is_walkable(x, y):
                continue
            if self._actor_at(level, pos):
                continue
            if self._blocking_entity_at(level, pos):
                continue

            # Pick a random enemy template id from enemies.yaml
            enemy_ids = self._enemy_template_ids()
            tmpl_id = self.rng.choice(enemy_ids)

            mob = enemy_factory.spawn_enemy(tmpl_id, pos)
            # 50% bismuth imps (works for both direct "imp" spawns and YAML-driven pools)
            if tmpl_id == "imp" and self.rng.random() < 0.2:
                mob.tags = getattr(mob, "tags", None) or {}
                mob.tags["visual_effects"] = ["bismuth"]
                # Temporary naming convention (will be replaced by descriptor system later)
                if not mob.name.lower().startswith("bismuth "):
                    mob.name = "bismuth imp"
            level.actors[mob.id] = mob
            level.entities[mob.id] = mob  # mirror into entities

            # Schedule AI for this enemy.
            self._schedule(
                level,
                self.cfg.action_time_fast,
                lambda aid=mob.id, lvl=level: self._monster_act(lvl, aid),
            )
            spawned += 1

    def _entity_templates(self) -> Dict[str, dict]:
        """Load non-actor entity templates from content/entities.yaml (cached)."""
        cached = getattr(self, "_entity_templates_cache", None)
        if cached is not None:
            return cached

        content_dir = Path(__file__).resolve().parent / "content"
        yaml_path = content_dir / "entities.yaml"

        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or []
        except FileNotFoundError:
            self._debug(f"No entities.yaml found at {yaml_path}; using empty template set.")
            data = []

        templates: Dict[str, dict] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            tid = entry.get("id")
            if not tid:
                continue
            templates[tid] = entry

        self._entity_templates_cache = templates
        self._debug(f"Loaded {len(templates)} entity templates from {yaml_path}.")
        return templates

    def _spawn_entity_from_template(
        self,
        template_id: str,
        pos: Tuple[int, int],
        overrides: Optional[Dict[str, object]] = None,
    ) -> Entity:
        """Instantiate a plain Entity from entities.yaml at the given position.

        `overrides` can supply name/color/kind/etc. and will be merged on top
        of the template; `tags` are merged rather than replaced.
        """
        templates = self._entity_templates()
        tmpl = templates.get(template_id)
        if tmpl is None:
            raise KeyError(f"Unknown entity template id {template_id!r}")

        # Base fields from template
        name = tmpl.get("name", template_id)
        glyph = tmpl.get("glyph", "?")
        color = tmpl.get("color", (255, 255, 255))
        if isinstance(color, list):
            color = tuple(color)
        kind = tmpl.get("kind", "generic")
        render_layer = int(tmpl.get("render_layer", 1))
        blocks_movement = bool(tmpl.get("blocks_movement", False))
        tags = dict(tmpl.get("tags", {}) or {})
        statuses = dict(tmpl.get("statuses", {}) or {})

        # Apply overrides (tags merged)
        if overrides:
            o = dict(overrides)  # shallow copy
            override_tags = o.pop("tags", None)
            if override_tags:
                tags.update(override_tags)

            name = o.get("name", name)
            glyph = o.get("glyph", glyph)
            color = tuple(o.get("color", color))
            kind = o.get("kind", kind)
            render_layer = int(o.get("render_layer", render_layer))
            blocks_movement = bool(o.get("blocks_movement", blocks_movement))

        eid = self._new_id()
        return Entity(
            id=eid,
            name=name,
            pos=pos,
            glyph=glyph,
            color=color,            # type: ignore[arg-type]
            render_layer=render_layer,
            kind=kind,
            blocks_movement=blocks_movement,
            tags=tags,
            statuses=statuses,
        )



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
            mentor = Human(
                id=aid,
                name="Mentor",
                pos=(tx, ty),
                faction="npc",
                stats=Stats(hp=1, max_hp=1),
                tags={"npc_id": "mentor"},
                disposition=10,
                affiliations=("edgecasters",),
            )
            # Custom look/inspect description
            mentor.description = "Old, one-eyed, and syphilitic, yet unerringly optimistic."
            

            level.actors[aid] = mentor
            level.entities[aid] = mentor
            break


    def _spawn_intro_npcs(self, level: LevelState) -> None:
        """Place the Hexmage and Cartographer near the entry if space allows."""
        entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
        x, y = entry
        offsets = [
            (1, 1),
            (-1, 1),
            (2, 1),
            (-2, 1),
            (1, -1),
            (-1, -1),
            (2, -1),
            (-2, -1),
        ]
        npc_specs = [
            ("hexmage", "The Hexmage"),
            ("cartographer", "The Cartographer"),
        ]
        placed = 0
        for npc_id, name in npc_specs:
            for dx, dy in offsets:
                tx, ty = x + dx + placed, y + dy  # small shift per NPC to avoid collisions
                if not level.world.in_bounds(tx, ty):
                    continue
                if not level.world.is_walkable(tx, ty):
                    continue
                if self._actor_at(level, (tx, ty)):
                    continue
                aid = self._new_id()
                npc = Human(
                    id=aid,
                    name=name,
                    pos=(tx, ty),
                    faction="npc",
                    stats=Stats(hp=1, max_hp=1),
                    tags={"npc_id": npc_id},
                    disposition=5,
                    affiliations=("edgecasters",),
                    glyph="&",
                )

                # Custom per-NPC description for the look system
                if npc_id == "hexmage":
                    npc.description = "This runecaster is swarming with bees."
                elif npc_id == "cartographer":
                    npc.description = "This chick is WAY too hot to be a cartographer."
                    

                level.actors[aid] = npc
                level.entities[aid] = npc
                placed += 1
                break
            # continue loop to next npc even if not placed; failure to place is tolerated


    def _spawn_poi_contents(self, level: LevelState, coord: Tuple[int, int, int]) -> None:
        """Spawn NPCs defined by any POIs attached to this level."""
        poi_ids = getattr(level.world, "poi_ids", [])
        if not poi_ids:
            return
        entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
        depot_info = getattr(level.world, "depot_info", None)

        def nearest_walkable(origin: Tuple[int, int], max_radius: int = 12) -> Optional[Tuple[int, int]]:
            ox, oy = origin
            if level.world.in_bounds(ox, oy) and level.world.is_walkable(ox, oy) and not self._actor_at(level, (ox, oy)):
                return origin
            for r in range(1, max_radius + 1):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        tx, ty = ox + dx, oy + dy
                        if not level.world.in_bounds(tx, ty):
                            continue
                        if not level.world.is_walkable(tx, ty):
                            continue
                        if self._actor_at(level, (tx, ty)):
                            continue
                        return (tx, ty)
            return None

        for pid in poi_ids:
            poi = poi_content.POIS.get(pid)
            if not poi:
                continue
            # Handle structures
            for struct in getattr(poi, "structures", []) or []:
                if struct.get("kind") == "item_depot" and depot_info:
                    # Place door
                    door_pos = depot_info.get("door")
                    if door_pos:
                        try:
                            ent = self._spawn_entity_from_template("door", door_pos)
                            level.entities[ent.id] = ent
                            tile = level.world.get_tile(*door_pos)
                            if tile:
                                tile.walkable = False
                                tile.glyph = "#"
                        except Exception:
                            pass
                    # Place sign
                    sign_pos = depot_info.get("sign")
                    if sign_pos:
                        try:
                            ent = self._spawn_entity_from_template(
                                "sign",
                                sign_pos,
                                overrides={"tags": {"sign_text": "Item Depot"}, "name": "Item Depot"},
                            )
                            level.entities[ent.id] = ent
                        except Exception:
                            pass
                    # Place items in interior
                    interior = depot_info.get("interior") or []
                    item_ids = ["blueberry", "raspberry", "strawberry", "destabilizer", "debug_inventory", "healing_kit", "scrap_blade"]
                    for pos in interior:
                        try:
                            template_id = self.rng.choice(item_ids)
                            ent = self._spawn_entity_from_template(template_id, pos)
                            level.entities[ent.id] = ent
                        except Exception:
                            continue
            # Extra: drop some starting bismuth piles in the starting zone.
            if pid == "starting_zone":
                world = level.world
                dropped = 0
                attempts = 0
                max_attempts = 200
                while dropped < 5 and attempts < max_attempts:
                    attempts += 1
                    x = self.rng.randint(0, world.width - 1)
                    y = self.rng.randint(0, world.height - 1)
                    if not world.in_bounds(x, y):
                        continue
                    if not world.is_walkable(x, y):
                        continue
                    if self._actor_at(level, (x, y)):
                        continue
                    if self._entity_at(level, (x, y)):
                        continue
                    try:
                        ent = self._spawn_entity_from_template("bismuth_pile", (x, y))
                        level.entities[ent.id] = ent
                        dropped += 1
                    except Exception:
                        continue
            for spec in poi.npcs:
                npc_def = npcs.NPC_DEFS.get(spec.npc_id, {})
                name = spec.name or npc_def.get("name", spec.npc_id.title())
                glyph = spec.glyph or npc_def.get("glyph", "@")
                color = spec.color or tuple(npc_def.get("color", (255, 255, 255)))
                offsets = spec.offsets or [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
                # Try explicit offsets first
                spawn_pos = None
                for dx, dy in offsets:
                    candidate = (entry[0] + dx, entry[1] + dy)
                    spot = nearest_walkable(candidate)
                    if spot:
                        spawn_pos = spot
                        break
                if spawn_pos is None:
                    spawn_pos = nearest_walkable(entry)
                if spawn_pos is None:
                    continue
                if spec.npc_id == "caged_demon":
                    actor = enemy_factory.spawn_enemy("caged_demon", spawn_pos)
                    actor.faction = "neutral"
                    actor.actions = ()
                    actor.ai = "idle"
                    actor.tags = getattr(actor, "tags", {}) or {}
                    actor.tags["npc_id"] = spec.npc_id
                    actor.tags["show_exact_hp"] = True
                    actor.show_exact_hp = True
                    desc = getattr(spec, "description", None) or npc_def.get("description") or actor.description
                    if desc:
                        actor.description = desc
                    actor.regen_per_tick = (1, 10)
                    self._start_regen(level, actor.id, amount=1, interval=10)
                else:
                    aid = self._new_id()
                    actor = Human(
                        id=aid,
                        name=name,
                        pos=spawn_pos,
                        faction="npc",
                        stats=Stats(hp=1, max_hp=1),
                        tags={"npc_id": spec.npc_id},
                        disposition=npc_def.get("base_disposition", 0),
                        affiliations=tuple(npc_def.get("factions", [])),
                        glyph=glyph,
                        color=color,  # type: ignore[arg-type]
                    )
                    # NEW: wire through POI / NPC_DEF description for look/inspect
                    desc = getattr(spec, "description", None) or npc_def.get("description")
                    if desc:
                        actor.description = desc
                    actor.tags.setdefault("npc_id", spec.npc_id)
                    if npc_def.get("show_exact_hp", False):
                        actor.tags["show_exact_hp"] = True
                        actor.show_exact_hp = True
                level.actors[actor.id] = actor
                level.entities[actor.id] = actor

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
            actor = Human(
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

    def _spawn_entities_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        place_entity: Callable[[Tuple[int, int]], None],
        radius: int = 3,
    ) -> int:
        """Generic helper to spawn up to `count` entities within `radius` of center.

        `place_entity` is called with each chosen (x, y) and is responsible for
        inserting the new thing into `level.entities` (and `level.actors`, if
        applicable).
        """
        cx, cy = center
        spawned = 0
        attempts = 0
        max_attempts = count * 20

        while spawned < count and attempts < max_attempts:
            attempts += 1
            x = cx + self.rng.randint(-radius, radius)
            y = cy + self.rng.randint(-radius, radius)

            if not level.world.in_bounds(x, y):
                continue
            if not level.world.is_walkable(x, y):
                continue
            if self._actor_at(level, (x, y)):
                continue
            # Avoid stacking multiple entities on the same tile for now.
            if self._entity_at(level, (x, y)):
                continue

            place_entity((x, y))
            spawned += 1

        return spawned

    def _spawn_imps_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        radius: int = 3,
    ) -> int:
        """Spawn up to `count` imps within `radius` tiles of center using templates."""

        def place_imp(pos: Tuple[int, int]) -> None:
            imp = enemy_factory.spawn_enemy("imp", pos)

            # 50% chance this imp is bismuth (visual effect applies to its glyph/color everywhere)
            if self.rng.random() < 0.2:
                imp.tags = getattr(imp, "tags", None) or {}
                imp.tags["visual_effects"] = ["bismuth"]
                imp.name = "bismuth imp"

            level.actors[imp.id] = imp
            level.entities[imp.id] = imp

            self._schedule(
                level,
                self.cfg.action_time_fast,
                lambda aid=imp.id, lvl=level: self._monster_act(lvl, aid),
            )

        return self._spawn_entities_near(level, center, count, place_imp, radius)


    def _spawn_echoes_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        radius: int = 3,
    ) -> int:
        """Spawn hostile fractal echoes within `radius` of center."""
        def place_echo(pos: Tuple[int, int]) -> None:
            echo = enemy_factory.spawn_enemy("fractal_echo", pos)
            level.actors[echo.id] = echo
            level.entities[echo.id] = echo

            self._schedule(
                level,
                self.cfg.action_time_fast,
                lambda aid=echo.id, lvl=level: self._monster_act(lvl, aid),
            )

        return self._spawn_entities_near(level, center, count, place_echo, radius)


    def _spawn_berries_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        radius: int = 3,
    ) -> int:
        """Spawn up to `count` test berries within `radius` tiles of center
        using data-driven templates from entities.yaml.
        """
        templates = self._entity_templates()
        berry_ids: List[str] = []
        for tid, tmpl in templates.items():
            tags = tmpl.get("tags", {}) or {}
            # Any template tagged as a test berry is allowed.
            if tags.get("test_berry"):
                berry_ids.append(tid)

        if not berry_ids:
            # Fallback to legacy behaviour if no berries defined.
            self._debug("No test_berry templates found in entities.yaml.")
            return 0

        def place_berry(pos: Tuple[int, int]) -> None:
            template_id = self.rng.choice(berry_ids)
            ent = self._spawn_entity_from_template(template_id, pos)
            level.entities[ent.id] = ent

        return self._spawn_entities_near(level, center, count, place_berry, radius)






    def _scatter_test_berries(self, level: LevelState, count: int = 30) -> None:
        """Scatter some colored berry entities across the map as a test.

        Berries are defined in content/entities.yaml via the `test_berry` tag.
        """
        if count <= 0:
            return

        templates = self._entity_templates()
        berry_ids: List[str] = []
        for tid, tmpl in templates.items():
            tags = tmpl.get("tags", {}) or {}
            if tags.get("test_berry"):
                berry_ids.append(tid)

        if not berry_ids:
            self._debug("No test_berry templates in entities.yaml; skipping berry scatter.")
            return

        placed = 0
        attempts = 0
        max_attempts = count * 50
        world = level.world

        while placed < count and attempts < max_attempts:
            attempts += 1
            x = self.rng.randint(0, world.width - 1)
            y = self.rng.randint(0, world.height - 1)

            if not world.in_bounds(x, y):
                continue
            if not world.is_walkable(x, y):
                continue
            if self._actor_at(level, (x, y)):
                continue
            if self._entity_at(level, (x, y)):
                continue

            template_id = self.rng.choice(berry_ids)
            ent = self._spawn_entity_from_template(template_id, (x, y))
            level.entities[ent.id] = ent
            placed += 1

        # Sprinkle some bismuth piles alongside berries for testing.
        bismuth_count = max(1, count // 10)
        for _ in range(bismuth_count):
            attempts = 0
            while attempts < max_attempts and placed < count + bismuth_count:
                attempts += 1
                x = self.rng.randint(0, world.width - 1)
                y = self.rng.randint(0, world.height - 1)
                if not world.in_bounds(x, y):
                    continue
                if not world.is_walkable(x, y):
                    continue
                if self._actor_at(level, (x, y)):
                    continue
                if self._entity_at(level, (x, y)):
                    continue
                try:
                    ent = self._spawn_entity_from_template(
                        "bismuth_pile",
                        (x, y),
                        overrides={"tags": {"amount": self.rng.randint(3, 15)}},
                    )
                    level.entities[ent.id] = ent
                    placed += 1
                    break
                except Exception:
                    continue



    def debug_spawn_inventory_near_player(self, count: int = 1, radius: int = 3) -> None:
        """Debug helper: conjure one or more meta-Inventories near the player.

        Each Inventory is an item-entity with container=True and the glyph '∞',
        so it can be picked up with 'g' and opened from the inventory UI.

        Distribution (per inventory):
        - 80% functional adjective: picks one adjective that ALSO declares one or more
          VisualEffect names (tags['visual_effects']).
        - 20% non-functional adjective: purely cosmetic wordplay, no effects.

        In all cases, exactly ONE adjective appears in the name.
        """
        level = self._level()
        if self.player_id not in level.actors:
            return
        player = level.actors[self.player_id]

        # ------------------------------------------------------------------
        # Adjectives
        #
        # Keep these two pools mutually exclusive:
        # - functional adjectives have associated VisualEffects (tested pipeline)
        # - non-functional adjectives are just funny flavor (no effect yet)
        # ------------------------------------------------------------------

        # Functional adjectives → effect name(s) (from visual_effects.py registry).
        # NOTE: "mirrored" resolves to either mirror_x or mirror_y per spawn.
        functional_map: dict[str, list[str]] = {
            "clockwise": ["clockwise"],
            "ghostly": ["ghostly"],
            "mirrored": [],  # chosen dynamically: ["mirror_x"] or ["mirror_y"]
            "fiery": ["fiery"],
            "bismuth": ["bismuth"],
        }

        # Big goofy pool. We will remove any functional adjectives from this below.
        nonfunctional_adjectives = [
            "fetid", "dubious", "spectacular", "outrageous", "sensible",
            "colossal", "lightly-aged", "unfortunate", "malicious",
            "courageous", "flavorful", "salty", "magnanimous",
            "pernicious", "persuasive", "cartoonish", "trapezoidal",
            "bovine", "spectral", "capitalized", "automatic",
            "counter-clockwise", "recursive", "stout",
            "lean", "microscopic", "semipermeable", "blessed",
            "+1", "+2", "candlelit", "smoky", "smoked", "cozy",
            "uninhabitable", "nuclear", "deathly", "ferocious",
            "fractious", "queer", "rectilinear", "lavender-scented",
            "hopefully not racist", "erotic", "far-fetched", "amazing",
            "underwhelming", "carnivorous", "mysterious", "arctic",
            "celestial", "toasty", "room temperature",
            "unassuming", "subtle", "gaudy", "ornate", "gem-encrusted",
            "golden", "wooden", "marbled", "spiked", "luminescent",
            "electrified", "poisonous", "venomous", "mangled",
            "malfunctioning", "twisted", "octonionic", "eldritch", "malted",
            "syrupy", "tumultuous", "festooned", "inappropriate", "entropic",
            "extropic", "overpopulated", "arbitrary",
            "ecstatic", "carbon-based", "semifluid", "carbonated",
            "vitamin-rich", "emotionally vulnerable", "disgruntled",
            "vegan-friendly", "emphatic", "plain old",
            "cream-filled", "inexcusable", "historically accurate",
            "randomized", "lubricated", "grape-flavored", "excitable",
            "tasteless", "vintage", "incandescent", "steam-powered",
        ]

        # Ensure mutual exclusivity (remove any functional words if they appear).
        functional_set = {k.lower() for k in functional_map.keys()}
        nonfunctional_adjectives = [
            a for a in nonfunctional_adjectives
            if a.lower() not in functional_set
        ]

        # Shuffle pool so a batch of spawned inventories tends not to repeat.
        nonfunc_pool = list(nonfunctional_adjectives)
        self.rng.shuffle(nonfunc_pool)

        def next_nonfunc_adj() -> str:
            nonlocal nonfunc_pool
            if not nonfunc_pool:
                nonfunc_pool = list(nonfunctional_adjectives)
                self.rng.shuffle(nonfunc_pool)
            return nonfunc_pool.pop()

        def place_inventory(pos: Tuple[int, int]) -> None:
            x, y = pos

            # Roll: functional vs non-functional adjective
            tags: dict[str, object] = {}
            if self.rng.random() < 0.80:
                adj = self.rng.choice(list(functional_map.keys()))
                effects = list(functional_map[adj])

                if adj == "mirrored":
                    effects = [self.rng.choice(["mirror_x", "mirror_y"])]

                if effects:
                    tags["visual_effects"] = effects
            else:
                adj = next_nonfunc_adj()

            display_name = f"{adj} Inventory"

            # Random color, overriding the template's default
            color = (
                self.rng.randint(80, 255),
                self.rng.randint(80, 255),
                self.rng.randint(80, 255),
            )

            overrides: dict[str, object] = {
                "name": display_name,
                "color": color,
            }
            if tags:
                overrides["tags"] = tags

            ent = self._spawn_entity_from_template(
                "debug_inventory",
                (x, y),
                overrides=overrides,
            )
            ent.description = "Definitely NOT a bag, it's much more Platonic than that."

            level.entities[ent.id] = ent
            # Ensure it has an inventory slot allocated
            self.get_inventory(ent.id)

        spawned = self._spawn_entities_near(
            level,
            player.pos,
            count,
            place_inventory,
            radius=radius,
        )

        if spawned > 0:
            if spawned == 1:
                self.log.add("That's a nice looking inventory.")
            else:
                self.log.add("Inventory sale! Inventory inventory must go!")
        else:
            self.log.add("This is no place for an inventory.")





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
        # NEW: cooldowns tick down
        self._cooldown_tick(level, delta)
        # NEW: pattern motion tick
        pattern_motion.step_motion(self, level, delta)

    def _start_regen(self, level: LevelState, actor_id: str, amount: int, interval: int) -> None:
        """
        Start periodic regen for an actor: heals `amount` HP every `interval` ticks.
        """
        def tick() -> None:
            actor = level.actors.get(actor_id)
            if actor is None or getattr(actor, "alive", True) is False:
                return
            try:
                stats = actor.stats
                if stats.hp < stats.max_hp:
                    stats.hp = min(stats.max_hp, stats.hp + amount)
            except Exception:
                pass
            # reschedule if still alive
            if actor is not None:
                self._schedule(level, interval, tick)

        self._schedule(level, interval, tick)


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



    def _cooldown_tick(self, level: LevelState, delta: int) -> None:
        """Tick down cooldowns on actors, ground entities, and inventory items."""
        seen: set[str] = set()

        def tick_entity(ent) -> None:
            if not hasattr(ent, "cooldowns"):
                return
            ent_id = getattr(ent, "id", None)
            if ent_id and ent_id in seen:
                return
            if ent_id:
                seen.add(ent_id)
            cds = getattr(ent, "cooldowns", {})
            to_delete = []
            for name, val in list(cds.items()):
                new_val = max(0, val - delta)
                if new_val <= 0:
                    to_delete.append(name)
                else:
                    cds[name] = new_val
            for name in to_delete:
                del cds[name]

        for act in level.actors.values():
            tick_entity(act)
        for ent in level.entities.values():
            tick_entity(ent)
        for items in getattr(self, "inventories", {}).values():
            for ent in items:
                tick_entity(ent)

        # Tick down frozen/chilled slow effects (decay 0.1 every 10 ticks).
        for actor in level.actors.values():
            tags = getattr(actor, "tags", None) or {}
            mult = float(tags.get("frozen_slow", 1.0))
            if mult <= 1.0:
                continue
            acc = float(tags.get("frozen_slow_timer", 0.0))
            acc += delta
            if acc >= 10:
                steps = int(acc // 10)
                acc = acc % 10
                mult = max(1.0, mult - steps * 0.1)
            if mult <= 1.0 + 1e-6:
                tags.pop("frozen_slow", None)
                tags.pop("frozen_slow_timer", None)
            else:
                tags["frozen_slow"] = mult
                tags["frozen_slow_timer"] = acc
            actor.tags = tags

    def _slow_mult(self, actor: Actor) -> float:
        tags = getattr(actor, "tags", {}) or {}
        try:
            mult = float(tags.get("frozen_slow", 1.0))
        except Exception:
            mult = 1.0
        return max(1.0, mult)


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

        # Clear reset flag after consumers have had a chance to react.
        if self.lorenz_reset_trails:
            self.lorenz_reset_trails = False



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

            # Prefer actual Actors when present; otherwise treat as item/feature.
            if isinstance(ent, Actor):
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

    def _toggle_door(self, ent: Entity, level: LevelState, notify: bool = False) -> None:
        tags = getattr(ent, "tags", {}) or {}
        state = tags.get("door_state", "closed")
        tile = level.world.get_tile(*ent.pos) if hasattr(ent, "pos") else None
        if state == "closed":
            tags["door_state"] = "open"
            ent.blocks_movement = False
            ent.glyph = "/"
            ent.color = getattr(ent, "color", (180, 140, 80))
            if tile:
                tile.walkable = True
                tile.glyph = "."
            if notify:
                self.log.add("You open the door.")
        else:
            tags["door_state"] = "closed"
            ent.blocks_movement = True
            ent.glyph = "+"
            ent.color = getattr(ent, "color", (180, 140, 80))
            if tile:
                # Closed doors block movement and line-of-sight like walls.
                tile.walkable = False
                tile.glyph = "#"
            if notify:
                self.log.add("You close the door.")
        ent.tags = tags
        # Refresh visibility immediately so opening/closing updates FOV right away.
        level.need_fov = True
        self._update_fov(level)


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
        # Stop any lingering motion when the pattern is cleared.
        lvl.pattern_motion = None
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

    # --- lab console ---

    def request_fractal_editor(self) -> None:
        """Request opening the fractal editor (e.g., when on a lab console)."""
        self.fractal_editor_requested = True
        
        
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

        tags = getattr(ent, "tags", {}) or {}
        if tags.get("currency") == "bismuth":
            amt = 0
            try:
                amt = int(tags.get("amount", 0))
            except Exception:
                amt = 0
            if amt <= 0:
                size = "tiny"
            elif amt <= 3:
                size = "tiny"
            elif amt <= 10:
                size = "small"
            elif amt <= 25:
                size = "medium"
            elif amt <= 50:
                size = "large"
            elif amt <= 100:
                size = "huge"
            else:
                size = "enormous"
            article = "an" if size[0].lower() in "aeiou" else "a"
            self.log.add(f"You see {article} {size} bismuth crystal.")
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

        # Currency piles are auto-absorbed.
        tags = getattr(ent, "tags", {}) or {}
        if tags.get("currency") == "bismuth":
            amt = int(tags.get("amount", 0))
            if amt > 0:
                self.adjust_currency(amt, log=True)
                # Play cash pickup sound (best-effort).
                self._play_sfx("assets/sfx/chaching.mp3", volume=0.7)
            # remove entity from world
            for eid, e in list(level.entities.items()):
                if e is ent:
                    del level.entities[eid]
                    break
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

        # Append the item to the current host's inventory.
        inv = self.player_inventory
        inv.append(ent)

        name = getattr(ent, "name", None) or "item"
        article = "an" if name and name[0].lower() in "aeiou" else "a"
        self.log.add(f"You pick up {article} {name.lower()}.")

        # Grant abilities tagged on the item (general hook)
        grants = ent.tags.get("grants_ability") if hasattr(ent, "tags") else None
        if grants:
            added = self.grant_ability(grants)
            if added:
                self.log.add(f"You learned how to {grants.replace('_', ' ')}.")
    def drop_inventory_item(self, index: int) -> None:
        """Drop an item from the inventory onto the player's current tile."""
        inv = self.player_inventory
        if not (0 <= index < len(inv)):
            return

        level = self._level()
        if self.player_id not in level.actors:
            return
        player = level.actors[self.player_id]

        ent = inv.pop(index)

        # Place the entity at the player's current position in the world.
        ent.pos = player.pos
        level.entities[ent.id] = ent  # type: ignore[index]

        name = getattr(ent, "name", None) or "item"
        article = "an" if name and name[0].lower() in "aeiou" else "a"
        self.log.add(f"You drop {article} {name.lower()}.")


    def eat_item_from_inventory(self, owner_id: str, index: int) -> None:
        """Consume an item from the given owner's inventory, if edible.

        This is mainly used for test berries. The *player* always gets
        healed, regardless of where the item was stored.
        """
        inv = self.get_inventory(owner_id)
        if not inv:
            if owner_id == self.player_id:
                self.log.add("You have nothing to eat.")
            return

        if not (0 <= index < len(inv)):
            return

        ent = inv[index]
        tags = getattr(ent, "tags", {}) or {}

        is_berry = bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }
        if not is_berry:
            name = getattr(ent, "name", None) or "item"
            if owner_id == self.player_id:
                self.log.add(f"You can't eat the {name.lower()}.")
            else:
                # Slightly different flavour when rummaging in bags.
                self.log.add(f"You decide not to eat the {name.lower()}.")
            return

        # Actually consume the item from that inventory.
        inv.pop(index)

        # Heal the player a bit for eating a berry.
        player = self._player()
        before = player.stats.hp
        player.stats.hp = min(player.stats.max_hp, player.stats.hp + 1)
        after = player.stats.hp

        if after > before:
            self.log.add("That was tart!")
        else:
            self.log.add("That was really tart!")

    def eat_inventory_item(self, index: int) -> None:
        """Backward-compatible wrapper for older code paths.

        Eats from the current host's inventory (player).
        """
        self.eat_item_from_inventory(self.player_id, index)


    def take_from_container(self, container_id: str, index: int) -> None:
        """Move an item from another entity's inventory into the player's.

        Legacy helper used by some older UIs / AI. Newer code should prefer
        ``move_item_between_inventories`` directly so we have a single place
        to handle recursive containers and logging.
        """
        self.move_item_between_inventories(
            container_id,
            index,
            self.player_id,
        )



    def move_item_between_inventories(
        self,
        src_owner_id: str,
        index: int,
        dest_owner_id: str,
    ) -> None:
        """Move an item from one entity's inventory to another's.

        Used by the UI to 'bag' items into containers (or later,
        for trading, stealing, etc.).
        """
        # No-op if same inventory
        if src_owner_id == dest_owner_id:
            return

        src_inv = self.get_inventory(src_owner_id)
        if not (0 <= index < len(src_inv)):
            return

        # Peek at the item first so we can detect self-removal on recursive bags.
        ent = src_inv[index]
        if getattr(ent, "id", None) == src_owner_id:
            # Special case: a container trying to remove itself from its own inventory.
            # Do not mutate any inventories; just narrate the failure.
            name = getattr(ent, "name", None) or "item"
            self.log.add(
                f"You turn the {name.lower()} inside out, but it remains itself."
            )
            return

        # Normal case: actually move the item.
        ent = src_inv.pop(index)
        dst_inv = self.get_inventory(dest_owner_id)
        dst_inv.append(ent)

        name = getattr(ent, "name", None) or "item"
        article = "an" if name and name[0].lower() in "aeiou" else "a"

        # Friendly label for the destination
        if dest_owner_id == self.player_id:
            dest_label = "your inventory"
        else:
            dest_label = dest_owner_id
            level = self._level()

            # First try level entities / actors
            dest_ent = level.entities.get(dest_owner_id) or level.actors.get(dest_owner_id)

            # If not found there, search through all inventories for a matching entity id
            if dest_ent is None:
                for inv_owner, items in self.inventories.items():
                    for it in items:
                        if getattr(it, "id", None) == dest_owner_id:
                            dest_ent = it
                            break
                    if dest_ent is not None:
                        break

            if dest_ent is not None:
                dest_name = getattr(dest_ent, "name", None)
                if dest_name:
                    dest_label = dest_name

        self.log.add(f"You put {article} {name.lower()} into {dest_label}.")







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
            lvl.pattern_motion = None
            self.log.add(f"Terminus placed at {target}.")

        self._schedule(lvl, self.cfg.place_time_ticks, do_place)
        self._advance_time(lvl, self.cfg.place_time_ticks)
        lvl.awaiting_terminus = False

    # --- actions ---

    # --- actions ---

    def queue_player_move(self, delta: Move) -> None:
        """
        Legacy entry point used by the renderer for directional input.

        Under the hood we now route this through the generic Action
        system so that movement is just another Action, with its speed
        and energy cost defined in the registry.
        """
        dx, dy = delta
        self.queue_actor_action(self.player_id, "move", dx=dx, dy=dy)


    def queue_player_wait(self) -> None:
        """Spend a turn doing nothing (useful for letting effects tick or luring enemies)."""
        self.queue_actor_action(self.player_id, "wait")
    
    def queue_player_fractal(self, kind: str) -> None:
        lvl = self._level()
        self._apply_fractal_op(lvl, kind)
        self._advance_time(lvl, self.cfg.action_time_fast)


    def reset_pattern(self) -> None:
        lvl = self._level()
        self._reset_pattern_core(lvl)


    def queue_player_activate(self, target_vertex: Optional[int]) -> None:
        lvl = self._level()
        self._activate_pattern_all(lvl, target_vertex)
        self._advance_time(lvl, self.cfg.action_time_fast)


    def queue_player_activate_seed(self, target_vertex: Optional[int]) -> None:
        lvl = self._level()
        self._activate_pattern_seed_neighbors(lvl, target_vertex)
        self._advance_time(lvl, self.cfg.action_time_fast)


    def queue_meditate(self) -> None:
        lvl = self._level()
        # Reuse the same core logic but keep the old time cost
        self._meditate_core(lvl, self.player_id)
        self._advance_time(lvl, 100)


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
        if npc_id in ("hexmage", "cartographer"):
            choices = ["Let's draft", "Maybe later"]
            return {"npc_id": npc_id, "name": npc.name, "lines": lines, "choices": choices}
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
        if npc_id in ("hexmage", "cartographer"):
            if choice.lower().startswith("let"):
                from edgecaster.scenes.fractal_editor_scene import FractalEditorState

                if npc_id == "hexmage":
                    self.fractal_editor_state = FractalEditorState(
                        grid_kind="hex",
                        max_vertices=4,
                        max_edges=None,
                    )
                    note = "The Hexmage opens a hexagonal drafting grid."
                else:
                    self.fractal_editor_state = FractalEditorState(
                        grid_x_min=-5,
                        grid_x_max=15,
                        grid_y_min=-10,
                        grid_y_max=10,
                        grid_kind="rect",
                        max_edges=None,
                    )
                    note = "The Cartographer unrolls a wide rectangular grid."
                self.fractal_editor_requested = True
                return note
            return "Maybe another time."
        all_gens = {"koch", "branch", "zigzag"}
        if choice not in all_gens:
            return "That knowledge eludes you."
        if choice in self.unlocked_generators:
            return f"You already know {choice.title()}."
        # teach new generator (replaces primary generator selection)
        self.unlocked_generators.append(choice)
        self.character.generator = choice
        # Grant the corresponding ability so it appears on the bar.
        try:
            self.grant_ability(choice)
        except Exception:
            pass
        return f"{choice.title()} added to your repertoire."



    def use_stairs_down(self) -> None:
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

    def use_stairs_up(self) -> None:
        lvl = self._level()
        player = self._player()
        tile = lvl.world.get_tile(*player.pos)
        if tile is None:
            return
        cx, cy, cz = self.zone_coord
        # surface: if no upstairs, request world map
        if cz == 0 and tile.glyph != "<":
            self.map_requested = True
            return
        if tile.glyph == "<" and cz > 0:
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


    def possess_actor(self, target_id: str) -> None:
        """Epiphenomenal body-hop: switch which Actor is controlled as the player."""
        level = self._level()

        # Sanity checks
        if target_id == self.player_id:
            return
        target = level.actors.get(target_id)
        if target is None:
            # Fall back to any Actor tracked in entities (if it somehow wasn't in actors).
            maybe_ent = level.entities.get(target_id) if hasattr(level, "entities") else None
            if isinstance(maybe_ent, Actor):
                target = maybe_ent
        if target is None or not getattr(target, "alive", False):
            self.log.add("Your consciousness finds no purchase.")
            return

        # --- release old host (if still around) ---
        old_player = level.actors.get(self.player_id)
        if old_player is not None:
            old_tags = getattr(old_player, "tags", None)
            native_faction = None
            if isinstance(old_tags, dict):
                # Stop treating the old shell as 'the player'
                old_tags.pop("is_player", None)
                # If we previously recorded its original faction, use that
                native_faction = (
                    old_tags.get("native_faction")
                    or old_tags.get("original_faction")
                )
            # Fall back to hostile if we don't know better
            if getattr(old_player, "faction", None) != "dead":
                old_player.faction = native_faction or "hostile"

        # --- claim new host ---
        # Capture its current faction before we overwrite it
        prev_faction = getattr(target, "faction", None)

        tags = getattr(target, "tags", None)
        if tags is None:
            tags = {}
            target.tags = tags  # type: ignore[assignment]

        # Remember native faction so we can restore later if needed
        if prev_faction and "native_faction" not in tags:
            tags["native_faction"] = prev_faction

        # Mark as the player-controlled body
        tags["is_player"] = True
        target.faction = "player"

        # HUD label: prioritize a species/kind tag, fall back to its name.
        host_label = (
            tags.get("species")
            or tags.get("kind")
            or getattr(target, "name", None)
            or "???"
        )
        self.current_host_label = host_label

        # Switch control to the new body
        self.player_id = target.id

        # Recompute FOV from the new perspective
        level.need_fov = True
        self._update_fov(level)

        # Re-center Lorenz storm on the new host if this run has an aura
        if self.has_lorenz_aura:
            px, py = target.pos
            self.lorenz_center_x = float(px)
            self.lorenz_center_y = float(py)
            self._lorenz_prev_pos = target.pos
            self._lorenz_prev_zone = self.zone_coord
            self.lorenz_reset_trails = True

        self.log.add(f"You've always been a {host_label}, so long as you can remember.")



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
            # Auto-open doors on bump
            if getattr(blocking_ent, "tags", {}).get("door_state") == "closed":
                self._toggle_door(blocking_ent, level, notify=(id == self.player_id))
                # After opening, proceed if no longer blocking
                if not getattr(blocking_ent, "blocks_movement", False):
                    self._handle_move_or_attack(level, id, dx, dy)
                return
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
            # auto-trigger lab console if standing on it
            tile = level.world.get_tile(nx, ny)
            if tile and tile.glyph == "=":
                self.request_fractal_editor()

    def _attack(self, level: LevelState, attacker: Actor, defender: Actor) -> None:
        dmg = 1
        defender.stats.hp -= dmg
        defender.stats.clamp()
        if attacker.id == self.player_id:
            self.log.add(f"You hit {defender.name} for {dmg}.")
        elif defender.id == self.player_id:
            self.log.add(f"{attacker.name} hits you for {dmg}.")
        else:
            self.log.add(f"{attacker.name} hits {defender.name} for {dmg}.")
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
                # Currency drop if the defender carries bismuth loot tags.
                tags = getattr(defender, "tags", {}) or {}
                drop_min = int(tags.get("currency_min", 0))
                drop_max = int(tags.get("currency_max", 0))
                if drop_max < drop_min:
                    drop_max = drop_min
                if drop_max > 0 and level.world.is_walkable(*defender.pos):
                    amt = self.rng.randint(drop_min, drop_max) if drop_min < drop_max else drop_min
                    if amt > 0:
                        try:
                            ent = self._spawn_entity_from_template(
                                "bismuth_pile",
                                defender.pos,
                                overrides={"tags": {"amount": amt}},
                            )
                            level.entities[ent.id] = ent
                        except Exception:
                            pass
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
 
        # --- RANDOM OVERWORLD EVENTS (testing) ---
        # 50% chance to fire *some* event on overworld for now.
        if self.zone_coord[2] == 0 and self.rng.random() < 0.5:
            ev = events.pick_random_event(self)
            if ev is not None:
                self.set_urgent(
                    ev.body,
                    title=ev.title,
                    choices=ev.choices,
                    on_choice_effect=ev.effect,
                )

        # DEBUG: drop a test Inventory on each new screen to exercise nested containers.
        self.debug_spawn_inventory_near_player(count=1)


    def fast_travel_to_zone(self, zx: int, zy: int) -> None:
        """Instantly move the player to the given overworld zone (depth 0)."""
        # clamp to world bounds
        zx = max(0, min(self.cfg.world_map_screens - 1, zx))
        zy = max(0, min(self.cfg.world_map_screens - 1, zy))
        dest_coord = (zx, zy, 0)
        level = self._level()
        actor = level.actors.get(self.player_id)
        if actor is None:
            return
        dest_level = self._get_zone(dest_coord, up_pos=None)
        # move actor between levels
        if self.player_id in level.actors:
            del level.actors[self.player_id]
        actor.pos = dest_level.world.entry
        dest_level.actors[self.player_id] = actor
        self.zone_coord = dest_coord
        dest_level.need_fov = True
        self._update_fov(dest_level)
        self._reset_lorenz_on_zone_change(actor)
        self.log.add(f"You fast-travel to zone {zx},{zy}.")

        # DEBUG: same behaviour as edge-wrap: one Inventory per arrival.
        self.debug_spawn_inventory_near_player(count=1)


    def _monster_act(self, level: LevelState, id: str) -> None:
        actor = level.actors.get(id)
        if actor is None or not actor.alive:
            return

        # If the player is not on this level (e.g. moved away), just
        # reschedule a bit later and do nothing for now.
        if self.player_id not in level.actors:
            self._schedule(
                level,
                self.cfg.action_time_fast,
                lambda aid=id, lvl=level: self._monster_act(lvl, aid),
            )
            return

        # Status: Distracted (30% chance to lose turn)
        if self._has_status(actor, "distracted"):
            if self.rng.random() < 0.3:
                self.log.add(f"The distracted {actor.name} falters.")
                self._tick_status(actor, "distracted")
                # Lose a turn: just wait one 'fast' step.
                self._schedule(
                    level,
                    self.cfg.action_time_fast,
                    lambda aid=id, lvl=level: self._monster_act(lvl, aid),
                )
                return
            else:
                self._tick_status(actor, "distracted")

        # --- Decide + perform an Action via the AI layer -----------------
        try:
            action_name, params = ai.choose_action(self, level, actor)
        except Exception:
            # Extremely defensive: if AI explodes, just wait.
            action_name, params = "wait", {}

        delay = self.cfg.action_time_fast

        if action_name:
            from edgecaster.systems.actions import get_action, action_delay

            try:
                action_def = get_action(action_name)
            except KeyError:
                # Unknown action: fall back to a simple wait.
                delay = self.cfg.action_time_fast
            else:
                # Perform the action immediately; do NOT call _advance_time
                # here, because we are already executing inside the event
                # queue (_advance_time's loop).
                action_def.func(self, actor.id, **(params or {}))
                delay = action_delay(self.cfg, action_def)

        # --- Schedule next turn -----------------------------------------
        # Apply slow effects to monsters too.
        mult = self._slow_mult(actor)
        if mult > 1.0:
            delay = int(math.ceil(delay * mult))
        self._schedule(
            level,
            delay,
            lambda aid=id, lvl=level: self._monster_act(lvl, aid),
        )


    # --- pattern activation ---


    # --- pattern activation helpers for the Action system ---

    def act_activate_all(self, actor_id: str, target_vertex: Optional[int]) -> None:
        """Generic action entry point: activate the whole pattern at a vertex."""
        level = self._level()
        # For now we still assume the player is the caster; later we can
        # look up the actor by id and its level explicitly.
        self._activate_pattern_all(level, target_vertex)


    def act_activate_seed(self, actor_id: str, target_vertex: Optional[int]) -> None:
        """Generic action entry point: activate neighbors around a seed vertex."""
        level = self._level()
        self._activate_pattern_seed_neighbors(level, target_vertex)

    def act_push_pattern(self, actor_id: str, target_pos=None, rotation_deg: float = 0) -> None:
        """Begin repeated motion/rotation of the current pattern."""
        level = self._level()
        pattern = getattr(level, "pattern", None)
        anchor = getattr(level, "pattern_anchor", None)
        if pattern is None or anchor is None or not pattern.vertices:
            return
        com = pattern_motion.center_of_mass(pattern)
        com_world = (com[0] + anchor[0], com[1] + anchor[1])
        if target_pos is None:
            target_pos = com_world
        dx = target_pos[0] - com_world[0]
        dy = target_pos[1] - com_world[1]
        dist = (dx * dx + dy * dy) ** 0.5
        # Allow pure rotation if targeting the current tile (avoid half-tile drift).
        if dist < 0.51:
            dx = dy = 0.0
        max_range = 5.0
        if dist > max_range and dist > 0:
            scale = max_range / dist
            dx *= scale
            dy *= scale
        pattern_motion.start_motion(level, (dx, dy), rotation_deg, interval=10)

    def act_destabilize(self, actor_id: str) -> None:
        """Teleport randomly within 10 tiles; 50% chance to take 10% max HP."""
        level = self._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return
        px, py = actor.pos
        radius = 10
        rng = getattr(self, "rng", None)

        candidates = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) > radius:
                    continue
                tx, ty = px + dx, py + dy
                if not level.world.in_bounds(tx, ty):
                    continue
                if not level.world.is_walkable(tx, ty):
                    continue
                candidates.append((tx, ty))

        if candidates:
            dest = rng.choice(candidates) if rng else candidates[0]
            actor.pos = dest
            if actor_id == self.player_id:
                self.log.add(f"You destabilize and reappear at {dest[0]},{dest[1]}.")
            else:
                self.log.add(f"{actor.name} flickers and reappears elsewhere.")
            level.need_fov = True

        # Damage roll: 50% chance
        if (rng.random() < 0.5) if rng else True:
            dmg = max(1, int(actor.stats.max_hp * 0.1))
            actor.stats.hp -= dmg
            actor.stats.clamp()
            if actor_id == self.player_id:
                self.log.add(f"Chaos bites! You take {dmg} damage.")
                if actor.stats.hp <= 0:
                    self.set_urgent("by way of destabilization", title="You unravel...", choices=["Continue..."])
            else:
                self.log.add(f"{actor.name} shudders from the destabilization.")
                if actor.stats.hp <= 0:
                    self._kill_actor(level, actor)

    def act_ignite(self, actor_id: str) -> None:
        """
        Ignite red edges for 30 ticks with decaying direct/indirect damage.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        pattern = getattr(level, "pattern", None)
        if actor is None or pattern is None or not pattern.edges:
            return

        # High mana cost gate
        cost = 30
        try:
            if actor.stats.mana < cost:
                if actor_id == self.player_id:
                    self.log.add("Not enough mana to ignite.")
                return
            actor.stats.mana -= cost
            actor.stats.clamp()
        except Exception:
            pass

        duration = 30
        base_direct = 4.0
        base_indirect = 2.0

        state = {
            "remaining": duration,
            "duration": duration,
            "accum": {},  # target_id -> fractional dmg
            "direct_tiles": [],
            "indirect_tiles": [],
        }
        level.ignite_state = state
        if actor_id == self.player_id:
            self.log.add("You ignite the pattern!")

        def normalize_edge_key(a: int, b: int) -> tuple[int, int]:
            return (a, b) if a <= b else (b, a)

        def edge_color_map():
            edge_colors = getattr(pattern, "edge_colors", {}) or {}
            if edge_colors:
                return edge_colors
            return {}

        color_map = edge_color_map()

        def tiles_for_edge(a_idx: int, b_idx: int) -> list[tuple[int, int]]:
            try:
                anchor = getattr(level, "pattern_anchor", None)
                verts = project_vertices(pattern, anchor)
                ax, ay = verts[a_idx]
                bx, by = verts[b_idx]
            except Exception:
                return []
            return _line_points(int(round(ax)), int(round(ay)), int(round(bx)), int(round(by)))

        def apply_tick() -> None:
            if state.get("remaining", 0) <= 0:
                level.ignite_state = None
                return
            anchor = getattr(level, "pattern_anchor", None)
            if anchor is None:
                level.ignite_state = None
                return
            # Decay multiplier
            mult = state["remaining"] / duration

            # Collect direct tiles and redness values
            direct_tiles: dict[tuple[int, int], float] = {}
            for edge in pattern.edges:
                a = getattr(edge, "a", None)
                b = getattr(edge, "b", None)
                if a is None or b is None:
                    continue
                col = color_map.get(normalize_edge_key(a, b), None)
                if col is None:
                    if isinstance(edge.color, tuple) and len(edge.color) >= 3:
                        col = edge.color
                    else:
                        continue
                try:
                    r, g, bl = int(col[0]), int(col[1]), int(col[2])
                except Exception:
                    continue
                redness = max(0, r - max(g, bl))
                if redness <= 0:
                    continue
                for t in tiles_for_edge(a, b):
                    prev = direct_tiles.get(t, 0.0)
                    if redness > prev:
                        direct_tiles[t] = redness

            if not direct_tiles:
                state["remaining"] = 0
                level.ignite_state = None
                return

            # Indirect tiles: neighbors of direct
            indirect_tiles: dict[tuple[int, int], float] = {}
            for (dx, dy), red in direct_tiles.items():
                for ox in (-1, 0, 1):
                    for oy in (-1, 0, 1):
                        nx, ny = dx + ox, dy + oy
                        if (nx, ny) in direct_tiles:
                            continue
                        prev = indirect_tiles.get((nx, ny), 0.0)
                        if red > prev:
                            indirect_tiles[(nx, ny)] = red

            # Persist tiles for renderer
            state["direct_tiles"] = list(direct_tiles.keys())
            state["indirect_tiles"] = list(indirect_tiles.keys())

            # Damage application
            combined: dict[str, any] = {}
            for aid, act in level.actors.items():
                combined[aid] = act
            for eid, ent in level.entities.items():
                if eid not in combined:
                    combined[eid] = ent

            for tid, obj in combined.items():
                pos = getattr(obj, "pos", None)
                if not pos:
                    continue
                tx, ty = int(round(pos[0])), int(round(pos[1]))
                dmg_val = 0.0
                if (tx, ty) in direct_tiles:
                    redness = direct_tiles[(tx, ty)]
                    dmg_val = base_direct * (redness / 255.0) * mult
                elif (tx, ty) in indirect_tiles:
                    redness = indirect_tiles[(tx, ty)]
                    dmg_val = base_indirect * (redness / 255.0) * mult
                if dmg_val <= 0:
                    continue
                acc = state["accum"].get(tid, 0.0) + dmg_val
                dmg_int = int(acc)
                state["accum"][tid] = acc - dmg_int
                if dmg_int > 0:
                    try:
                        obj.stats.hp -= dmg_int
                        obj.stats.clamp()
                        if obj.stats.hp <= 0 and tid != self.player_id:
                            self._kill_actor(level, obj)
                    except Exception:
                        pass

            state["remaining"] -= 1
            if state["remaining"] > 0:
                self._schedule(level, 1, apply_tick)
            else:
                level.ignite_state = None

        # First tick immediately
        apply_tick()

    def act_regrow(self, actor_id: str) -> None:
        """
        Heal along green edges for 30 ticks with decaying strength.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        pattern = getattr(level, "pattern", None)
        if actor is None or pattern is None or not pattern.edges:
            return

        cost = 30
        try:
            if actor.stats.mana < cost:
                if actor_id == self.player_id:
                    self.log.add("Not enough mana to regrow.")
                return
            actor.stats.mana -= cost
            actor.stats.clamp()
        except Exception:
            pass

        duration = 30
        base_direct = 3.5
        base_indirect = 1.5

        state = {
            "remaining": duration,
            "duration": duration,
            "accum": {},  # target_id -> fractional heal
            "direct_tiles": [],
            "indirect_tiles": [],
        }
        level.regrow_state = state
        if actor_id == self.player_id:
            self.log.add("You flood the pattern with renewal.")

        def normalize_edge_key(a: int, b: int) -> tuple[int, int]:
            return (a, b) if a <= b else (b, a)

        def edge_color_map():
            edge_colors = getattr(pattern, "edge_colors", {}) or {}
            if edge_colors:
                return edge_colors
            return {}

        color_map = edge_color_map()

        def tiles_for_edge(a_idx: int, b_idx: int) -> list[tuple[int, int]]:
            try:
                anchor = getattr(level, "pattern_anchor", None)
                verts = project_vertices(pattern, anchor)
                ax, ay = verts[a_idx]
                bx, by = verts[b_idx]
            except Exception:
                return []
            return _line_points(int(round(ax)), int(round(ay)), int(round(bx)), int(round(by)))

        def apply_tick() -> None:
            if state.get("remaining", 0) <= 0:
                level.regrow_state = None
                return
            anchor = getattr(level, "pattern_anchor", None)
            if anchor is None:
                level.regrow_state = None
                return
            mult = state["remaining"] / duration

            direct_tiles: dict[tuple[int, int], float] = {}
            for edge in pattern.edges:
                a = getattr(edge, "a", None)
                b = getattr(edge, "b", None)
                if a is None or b is None:
                    continue
                col = color_map.get(normalize_edge_key(a, b), None)
                if col is None:
                    if isinstance(edge.color, tuple) and len(edge.color) >= 3:
                        col = edge.color
                    else:
                        continue
                try:
                    r, g, bl = int(col[0]), int(col[1]), int(col[2])
                except Exception:
                    continue
                greenness = max(0, g - max(r, bl))
                if greenness <= 0:
                    continue
                for t in tiles_for_edge(a, b):
                    prev = direct_tiles.get(t, 0.0)
                    if greenness > prev:
                        direct_tiles[t] = greenness

            if not direct_tiles:
                state["remaining"] = 0
                level.regrow_state = None
                return

            indirect_tiles: dict[tuple[int, int], float] = {}
            for (dx, dy), gval in direct_tiles.items():
                for ox in (-1, 0, 1):
                    for oy in (-1, 0, 1):
                        nx, ny = dx + ox, dy + oy
                        if (nx, ny) in direct_tiles:
                            continue
                        prev = indirect_tiles.get((nx, ny), 0.0)
                        if gval > prev:
                            indirect_tiles[(nx, ny)] = gval

            state["direct_tiles"] = list(direct_tiles.keys())
            state["indirect_tiles"] = list(indirect_tiles.keys())

            combined: dict[str, any] = {}
            for aid, act in level.actors.items():
                combined[aid] = act
            for eid, ent in level.entities.items():
                if eid not in combined:
                    combined[eid] = ent

            for tid, obj in combined.items():
                pos = getattr(obj, "pos", None)
                if not pos:
                    continue
                tx, ty = int(round(pos[0])), int(round(pos[1]))
                heal_val = 0.0
                if (tx, ty) in direct_tiles:
                    gval = direct_tiles[(tx, ty)]
                    heal_val = base_direct * (gval / 255.0) * mult
                elif (tx, ty) in indirect_tiles:
                    gval = indirect_tiles[(tx, ty)]
                    heal_val = base_indirect * (gval / 255.0) * mult
                if heal_val <= 0:
                    continue
                acc = state["accum"].get(tid, 0.0) + heal_val
                heal_int = int(acc)
                state["accum"][tid] = acc - heal_int
                if heal_int > 0:
                    try:
                        obj.stats.hp = min(obj.stats.max_hp, obj.stats.hp + heal_int)
                    except Exception:
                        pass

            state["remaining"] -= 1
            if state["remaining"] > 0:
                self._schedule(level, 1, apply_tick)
            else:
                level.regrow_state = None

        apply_tick()

    def act_freeze(self, actor_id: str) -> None:
        """
        Deal damage and apply slowing based on pattern blueness across all tiles the pattern occupies.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        pattern = getattr(level, "pattern", None)
        anchor = getattr(level, "pattern_anchor", None)
        if actor is None or pattern is None or anchor is None or not pattern.vertices:
            return

        # High mana cost gate (no cooldown)
        cost = 35
        try:
            if actor.stats.mana < cost:
                if actor_id == self.player_id:
                    self.log.add("Not enough mana to freeze.")
                return
            actor.stats.mana -= cost
            actor.stats.clamp()
        except Exception:
            pass

        dmg_scale = getattr(self, "get_param_value", lambda a, k: 0.1)("freeze", "damage_scale") or 0.1
        slow_scale = getattr(self, "get_param_value", lambda a, k: 0.04)("freeze", "slow_scale") or 0.04

        verts_world = project_vertices(pattern, anchor)
        vcolors = getattr(pattern, "vertex_colors", None) or []

        def blueness(idx: int) -> float:
            try:
                col = vcolors[idx]
            except Exception:
                col = None
            if not col or len(col) < 3:
                return 0.0
            r, g, b = col[0], col[1], col[2]
            return max(0.0, float(b) - max(float(r), float(g)))

        tile_blue: Dict[Tuple[int, int], float] = {}
        for i, (vx, vy) in enumerate(verts_world):
            tx = int(round(vx))
            ty = int(round(vy))
            blue = blueness(i)
            tile_blue[(tx, ty)] = tile_blue.get((tx, ty), 0.0) + blue

        if actor_id == self.player_id:
            self.log.add("You unleash a freezing wave through the pattern.")

        for (tx, ty), bsum in tile_blue.items():
            if bsum <= 0:
                continue
            dmg = bsum * float(dmg_scale)
            slow_mult = 1.0 + bsum * float(slow_scale)
            if slow_mult > 4.0:
                slow_mult = 4.0
            for target in list(level.actors.values()):
                if not target.alive:
                    continue
                if tuple(getattr(target, "pos", (None, None))) != (tx, ty):
                    continue
                if dmg > 0:
                    dmg_int = int(max(0, dmg))
                    if dmg_int > 0:
                        target.stats.hp -= dmg_int
                        target.stats.clamp()
                        if target.id == self.player_id:
                            self.log.add(f"The freeze bites you for {dmg_int} damage.")
                            if target.stats.hp <= 0:
                                self.set_urgent("by way of freezing", title="You unravel...", choices=["Continue..."])
                        else:
                            self.log.add(f"{target.name} is frozen for {dmg_int} damage.")
                            if target.stats.hp <= 0:
                                self._kill_actor(level, target)
                tags = getattr(target, "tags", {}) or {}
                current = float(tags.get("frozen_slow", 1.0))
                if slow_mult > current:
                    tags["frozen_slow"] = slow_mult
                    tags["frozen_slow_timer"] = 0.0
                    target.tags = tags

    def act_fractal(self, actor_id: str, kind: str) -> None:
        """Generic action entry point: apply a fractal generator to the current pattern."""
        level = self._level()
        self._apply_fractal_op(level, kind)


    def act_reset_rune(self, actor_id: str) -> None:
        """Generic action entry point: reset the current rune/pattern."""
        level = self._level()
        self._reset_pattern_core(level)


    def act_meditate(self, actor_id: str) -> None:
        """Generic action entry point: meditate to restore mana."""
        level = self._level()
        self._meditate_core(level, actor_id)


    def _apply_fractal_op(self, lvl: LevelState, kind: str) -> None:
        if not lvl.pattern.vertices:
            self.log.add("No pattern to modify. Place a terminus first.")
            return
        segs = lvl.pattern.to_segments()
        # Editing the pattern should cancel any ongoing motion.
        lvl.pattern_motion = None
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
        elif kind.startswith("custom"):
            idx = 0
            if kind != "custom":
                try:
                    idx = int(kind.split("_", 1)[1])
                except Exception:
                    idx = 0
            if not self.custom_patterns or idx >= len(self.custom_patterns):
                self.log.add("No custom pattern saved.")
                return
            pattern = self.custom_patterns[idx]
            verts = None
            edges = []
            if isinstance(pattern, dict):
                verts = pattern.get("vertices")
                edges = pattern.get("edges", [])
            else:
                verts = pattern
            if not verts or len(verts) < 2:
                self.log.add("No custom pattern saved.")
                return
            amp = self._param_value("custom", "amplitude")
            if edges:
                gen = builder.CustomGraphGenerator(verts, edges, amplitude=amp)
            else:
                gen = builder.CustomPolyGenerator(verts, amplitude=amp)
        else:
            self.log.add("Unknown fractal op.")
            return

        segs = gen.apply_segments(segs, max_segments=self.cfg.max_vertices)
        segs = builder.cleanup_duplicates(segs)
        if len(segs) > self.cfg.max_vertices:
            segs = segs[: self.cfg.max_vertices]
            self.log.add("Pattern capped at max vertices.")
        lvl.pattern = builder.Pattern.from_segments(segs)


    def _reset_pattern_core(self, lvl: LevelState) -> None:
        lvl.pattern = builder.Pattern()
        lvl.pattern_anchor = None
        lvl.activation_points = []
        lvl.activation_ttl = 0
        # restore coherence to max when manually resetting
        player = self._player()
        player.stats.coherence = player.stats.max_coherence
        self.log.add("Rune reset.")


    def _meditate_core(self, lvl: LevelState, actor_id: str) -> None:
        # Currently only the player meditates; hook actor_id up properly later.
        player = self._player()
        before = player.stats.mana
        gain = 10
        player.stats.mana = min(player.stats.max_mana, player.stats.mana + gain)
        restored = player.stats.mana - before
        if restored > 0:
            self.log.add(f"You meditate and restore {restored} mana.")
        else:
            self.log.add("You meditate but feel already full of mana.")




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
        aid = actor.id

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

    # --- debug logging ---
    def _debug(self, msg: str) -> None:
        try:
            with open(self.debug_log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
