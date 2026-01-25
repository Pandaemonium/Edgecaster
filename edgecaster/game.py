from dataclasses import dataclass, field
from itertools import islice
from typing import Dict, Tuple, List, Optional, Callable
from pathlib import Path
from collections import deque


from edgecaster import config, events
from edgecaster.state.world import World
from edgecaster.state.actors import Actor, Stats, Human
from edgecaster.state.entities import Entity
from edgecaster.enemies import factory as enemy_factory


from edgecaster import mapgen
from edgecaster import mapgen_sites
from edgecaster.content import pois as poi_content
from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns import builder
from edgecaster.character import Character, default_character
from edgecaster.content import npcs
from edgecaster.systems.actions import get_action, action_delay
from edgecaster.systems import equipment as equipment_system
from edgecaster.systems import item_grants
from edgecaster.systems import ai
from edgecaster.systems.params import ParamManager
from edgecaster.systems import legendaries as legendaries_system
from edgecaster.systems import lorenz_aura
from edgecaster.systems import action_runner
from edgecaster.systems import spawning as spawning_system
from edgecaster.systems import inventory as inventory_system
from edgecaster.systems import pattern_ops
from edgecaster.systems import scheduling
from edgecaster.systems import combat as combat_system
from edgecaster.systems import zones as zones_system
from edgecaster.systems import overmap as overmap_system
from edgecaster.systems import difficulty as difficulty_system
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


def _los(
    world: World,
    a: Tuple[int, int],
    b: Tuple[int, int],
    *,
    opaque: set[Tuple[int, int]] | None = None,
) -> bool:
    """
    Line-of-sight test from a -> b.

    Blocks LOS on:
      - any position in `opaque` (typically entities with blocks_vision=True)
      - terrain tiles with tile.blocks_vision == True (NOT tile.walkable)

    Always allows seeing the target square itself.
    """
    for (x, y) in _line_points(a[0], a[1], b[0], b[1]):
        if not world.in_bounds(x, y):
            return False
        tile = world.get_tile(x, y)
        if tile is None:
            return False

        # Always allow seeing the target square itself.
        if (x, y) == b:
            return True

        # Entity occluders (walls, closed doors, etc.)
        if opaque is not None and (x, y) in opaque:
            return False

        # Terrain occluders (cliffs, opaque fog tiles, etc.)
        if getattr(tile, "blocks_vision", False):
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
        items = list(islice(reversed(self.messages), 0, n))
        items.reverse()
        return items


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
    acidic_pattern: bool = False  # True when Corrosive Melt is active
    # Fern growth state (Barnsley fern auto-growth system)
    fern_active: bool = False  # Is fern growth enabled?
    fern_growth_tips: List[int] = field(default_factory=list)  # Vertex indices that can spawn growth
    fern_accum: float = 0.0  # Fractional tick accumulator for growth timing
    seal_trial: Optional["SealTrialState"] = None  # Sealing rune trial state (if any)
    # Zone difficulty metadata (computed on zone creation).
    danger_value: float = 0.0
    danger_tier: int = 1
    danger_sources: Dict[str, float] = field(default_factory=dict)



class Game:
    def __init__(
        self,
        cfg: config.GameConfig | None = None,
        rng=None,
        *,
        character: Character | None = None,
        seed: int | None = None,
    ) -> None:
        if cfg is None:
            cfg = config.GameConfig()
        if rng is None:
            from edgecaster.rng import new_rng

            rng = new_rng(seed if seed is not None else getattr(cfg, "seed", None))

        self.cfg = cfg
        self.rng = rng
        self._init_debug_log()
        self.log = MessageLog()
        self.place_range = cfg.place_range
        # Enemy/NPC prototypes are loaded lazily via prototypes.resolve_proto().
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

        # Factions/Reputation: per-character standing with each faction.
        # This is used for AI hostility checks and future quest/merchant logic.
        if not isinstance(getattr(self.character, "reputation", None), dict):
            self.character.reputation = {}
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
        # Use ParamManager for stat-gated action parameters
        self._param_manager = ParamManager()
        # Deferred: set stat getter after effective_character_stats is available
        self._param_manager.set_stat_getter(lambda: self.effective_character_stats())
        # Expose param_defs and param_state for backwards compatibility
        self.param_defs = self._param_manager.param_defs
        self.param_state = self._param_manager.param_state
        # generators the player "knows" for NPC rewards etc.
        self.unlocked_generators: List[str] = [self.character.generator]
        # start with params auto-maxed given current stats
        self._param_manager.recalc_max()
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
        # Corruption: Julia distortion intensity (phase 1: visuals-only in terrain generation).
        # Must be shared between world-map rendering and local zone generation.
        # Default: make corruption clearly visible on the overmap so it's easy to tune.
        # The World Map scene also includes a temporary slider to adjust this.
        self.corruption_level: float = 1.4
        self.corruption_seed: int = int(self.fractal_seed) + 9001
        self.corruption_version: int = 0
        self.corruption_hotspots: List[Tuple[float, float, float, float]] = []
        # Rune anchors suppress corruption locally in the Julia z-plane.
        # Stored as (jx, jy, sigma, strength) in Julia-plane coords.
        self.corruption_anchors: List[Tuple[float, float, float, float]] = []
        # Optional prototype parity: random spline-based distortion field.
        # Keep disabled by default so the landscape field remains canonical until tuned.
        self.corruption_spline_weight: float = 0.0
        # Developer mode: God Vision reveals entire map (no FOV restrictions)
        self.god_vision: bool = False
        # Climate configuration for biome generation.
        # Controls land_boost, sea_level, and temperature/moisture parameters.
        from edgecaster.climate import ClimateConfig
        self.climate_config: ClimateConfig = ClimateConfig()
        # world map render cache (surface + view window)
        self.world_map_cache = None
        self.world_map_c: complex | None = None
        self.world_map_rendering = False
        self.world_map_ready = False
        self.world_map_thread_started = False
        self.world_map_version: int = 0
        # What is currently driving a (re)render of the world map.
        # "loading" = initial generation; "corruption" = corruption forcing rerender.
        self.world_map_render_reason: str = "loading"
        # Infinite-zoom view window for the overmap render:
        # (min_wx, min_wy, span_wx, span_wy) in continuous world-tile coords.
        # None means "full world".
        self.world_map_view: Optional[Tuple[float, float, float, float]] = None
        self.world_map_view_token: int = 0
        # Last requested render size (used by background threads + cache keys).
        self.world_map_render_width: int = int(getattr(self.cfg, "view_width", 0) or 0)
        self.world_map_render_height: int = int(getattr(self.cfg, "view_height", 0) or 0)
        self.world_map_render_span: int = 16
        # If a new view is requested while rendering, queue the latest request and
        # render it immediately after the current thread completes.
        self._world_map_pending_request: Optional[dict] = None
        # per-tile julia grid (x coords, y coords) derived from overmap view
        self.tile_julia_grid: dict[str, list[float]] | None = None
        # flags
        self.map_requested = False
        self.fractal_editor_requested = False
        self.fractal_editor_state = None


        # debug flags
        self.debug_no_fog: bool = False
        self.debug_spawn_inventories: bool = False

        # zones keyed by (x, y, depth)
        self.levels: Dict[Tuple[int, int, int], LevelState] = {}
        # start roughly at world center so Julia coords near (0,0)
        center_zx = self.cfg.world_map_screens // 2
        center_zy = self.cfg.world_map_screens // 2
        self.zone_coord: Tuple[int, int, int] = (center_zx, center_zy, 0)
        self._next_id = 0
        # initialize overmap parameters/grid eagerly (fixed bounds) and kick off async render
        self._init_overmap_params_and_grid()

        # Site registry for biome-based POI placement.
        # Populated after overmap_params/tile_julia_grid are set up.
        from edgecaster.systems.sites import SiteRegistry
        from edgecaster.systems.site_placement import place_all_sites
        self.site_registry: SiteRegistry = place_all_sites(self)

        # Difficulty field configuration (tunable, decoupled from biomes).
        # Adjust this in one place to change zone difficulty behavior.
        self.difficulty_config = difficulty_system.DifficultyConfig()
        # Optional per-zone overrides (quest/scripts can set these).
        self.zone_difficulty_overrides: Dict[Tuple[int, int, int], float] = {}

        # Inventories: mapping from owner id to a list of carried Entities.
        # Initially empty; per-owner lists are created lazily via get_inventory().
        self.inventories: Dict[str, List[Entity]] = {}
        # Simple SFX cache for lightweight sounds
        self._sfx_cache: Dict[str, object] = {}

        # Quest tracking
        self.active_quests: Dict[str, "Quest"] = {}  # type: ignore[name-defined]
        self.completed_quests: List[str] = []
        self.failed_quests: List[str] = []

        # POI discovery/rumors:
        # - discovered POIs are visible on the world map
        # - rumored POIs are visible but "unconfirmed" until you enter their zone
        self.discovered_pois: set[str] = set()
        self.rumored_pois: set[str] = set()

        # create starting zone
        self.levels[self.zone_coord] = self._make_zone(coord=self.zone_coord, up_pos=None)
        # Apply sealing trial grants if the starting zone contains one.
        try:
            from edgecaster.systems import seal_trials
            seal_trials.sync_zone_trial(self, self._level(), self.zone_coord)
        except Exception:
            pass
        # Starting zone is immediately discovered.
        try:
            self._discover_pois_for_level(self.levels[self.zone_coord])
        except Exception:
            pass

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
        player.tags["icon_path"] = "assets/icons/bismuth_wizard.png"

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
                "polygon",
                "star",
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
            actions.append("corrosive_melt")
            actions.append("start_fern")
            actions.append("winter_hue")
            actions.append("freeze")
            actions.append("ignite")
            actions.append("regrow")
            actions.append("push_pattern")
            actions.append("corruption_cone")
            actions.append("place_rune_anchor")
            actions.append("lightning")

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

        # --- Starting wands -------------------------------------------------
        # Wands grant actions only while equipped, and have per-item charges.
        # Start the player with two different random wands so the system is easy to test.
        try:
            wand_defs = [
                ("wand_koch", "koch"),
                ("wand_branch", "branch"),
                ("wand_zigzag", "zigzag"),
                ("wand_activate_n", "activate_seed"),
                ("wand_sparkle", "sparkle"),
            ]
            intrinsic_set = {str(x) for x in (getattr(player, "actions", ()) or []) if x}

            # Prefer wands that grant something the player doesn't already have intrinsically.
            candidates = [wid for wid, act in wand_defs if act not in intrinsic_set]
            pool = candidates if len(candidates) >= 2 else [wid for wid, _ in wand_defs]

            first = self.rng.choice(pool)
            pool2 = [x for x in pool if x != first]
            second = self.rng.choice(pool2) if pool2 else first
            for wid in (first, second):
                try:
                    wand = self._spawn_entity_from_template(wid, player.pos)
                    self.player_inventory.append(wand)
                except Exception:
                    continue
        except Exception:
            pass






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

        # Choose nearby quest POIs (inventor tower + failing rune) for this run.
        # These are placed close to the starting zone so the early quest is reachable.
        start_zx, start_zy, _ = self.zone_coord
        max_screen = max(0, int(self.cfg.world_map_screens) - 1)

        reserved_coords = {
            tuple(poi.coord)
            for poi in poi_content.POIS.values()
            if tuple(poi.coord) != (0, 0, 0)
        }
        reserved_coords.add(tuple(self.zone_coord))
        reserved_coords.add((self.lab_zone[0], self.lab_zone[1], 0))

        def pick_near(max_radius: int, *, avoid: set[tuple[int, int, int]]) -> tuple[int, int, int]:
            for _ in range(400):
                dx = int(self.rng.randint(-max_radius, max_radius))
                dy = int(self.rng.randint(-max_radius, max_radius))
                if dx == 0 and dy == 0:
                    continue
                if dx * dx + dy * dy > max_radius * max_radius:
                    continue
                zx = max(0, min(max_screen, start_zx + dx))
                zy = max(0, min(max_screen, start_zy + dy))
                coord = (zx, zy, 0)
                if coord in avoid:
                    continue
                return coord
            # Fallback: clamp to bounds near start (may overlap if RNG is unlucky).
            return (max(0, min(max_screen, start_zx + max_radius)), start_zy, 0)

        self.inventor_zone = pick_near(5, avoid=reserved_coords)
        reserved_coords.add(tuple(self.inventor_zone))
        self.failing_rune_zone = pick_near(8, avoid=reserved_coords)
        reserved_coords.add(tuple(self.failing_rune_zone))
        self.destabilizer_ruin_zone = pick_near(6, avoid=reserved_coords)
        reserved_coords.add(tuple(self.destabilizer_ruin_zone))

        inventor_poi = poi_content.POIS.get("inventor_workshop")
        if inventor_poi:
            poi_content.POIS["inventor_workshop"] = poi_content.POI(
                id=inventor_poi.id,
                coord=tuple(self.inventor_zone),
                npcs=inventor_poi.npcs,
                structures=inventor_poi.structures,
            )

        failing_poi = poi_content.POIS.get("failing_rune")
        if failing_poi:
            poi_content.POIS["failing_rune"] = poi_content.POI(
                id=failing_poi.id,
                coord=tuple(self.failing_rune_zone),
                npcs=failing_poi.npcs,
                structures=failing_poi.structures,
            )

        ruin_poi = poi_content.POIS.get("destabilizer_ruin")
        if ruin_poi:
            poi_content.POIS["destabilizer_ruin"] = poi_content.POI(
                id=ruin_poi.id,
                coord=tuple(self.destabilizer_ruin_zone),
                npcs=ruin_poi.npcs,
                structures=ruin_poi.structures,
            )

        # Keep the guide's quest location in sync with the inventor placement.
        try:
            guide_def = npcs.NPC_DEFS.get("guide_npc", {})
            guide_def["quest_location"] = [self.inventor_zone[0], self.inventor_zone[1]]
        except Exception:
            pass

        # Lab is a known rumor from the start of the run.
        self.add_poi_rumor("lab", log=False)

        # Generate a batch of legendary lairs for this run (hidden until discovered/rumored).
        self._init_legendaries(count=50)
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
            import pygame

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

    # =========================================================================
    # PHASE 10: PARAM SYSTEM -> systems/params.py
    # These methods now delegate to self._param_manager
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _recalc_param_state_max(self) -> None:
        """Set all params to the highest tier allowed by current stats."""
        self._param_manager.recalc_max()

    def _xp_needed_for_level(self, level: int) -> int:
        """XP needed to go from this level to the next."""
        return max(1, self.cfg.xp_base + self.cfg.xp_per_level * (level - 1))

    def _coherence_limit(self) -> int:
        """How many vertices before coherence drain starts (INT*4)."""
        intellect = self.effective_character_stats().get("int", 0)
        return intellect * 4

    def _strength_limit(self) -> int:
        """How many activated vertices can be driven at once; scales with RES."""
        res = self.effective_character_stats().get("res", 0)
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

    def _init_debug_log(self):
        import logging
        import sys
        import time
        self.debug_log_path = "C:\\Games\\Edgecaster\\debug.log"
        try:
            with open(self.debug_log_path, "w", encoding="utf-8") as f:
                f.write(f"--- Log started at {time.asctime()} ---\n")
        except Exception as e:
            print(f"Error initializing debug log: {e}", file=sys.stderr)

        logger = logging.getLogger("edgecaster_debug")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            try:
                handler = logging.FileHandler(self.debug_log_path, encoding="utf-8")
                formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
                handler.setFormatter(formatter)
                logger.addHandler(handler)
            except Exception as e:
                print(f"Error adding handler to debug log: {e}", file=sys.stderr)
        self._logger = logger
        self._debug("Debug logging initialized.")

    def _debug(self, msg: str):
        if hasattr(self, '_logger') and self._logger:
            self._logger.debug(str(msg))
        else:
            import sys
            print(f"DEBUG: {msg}", file=sys.stderr)
        
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




    # =========================================================================
    # PHASE 0: ACTION EXECUTION -> systems/action_runner.py
    # Unify player (queue_actor_action) and AI (_monster_act) paths
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    # =========================================================================
    # PHASE 0: ACTION EXECUTION -> systems/action_runner.py
    # These methods delegate to action_runner module
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def queue_actor_action(self, actor_id: str, action_name: str, **kwargs) -> None:
        """
        Perform a generic action for the given actor and advance time based
        on the action's speed.

        Delegates to action_runner.run_action for unified player/AI action handling.
        """
        # Pop internal confirmation-skip flags for backwards compatibility
        skip_confirm = bool(kwargs.pop("__skip_confirm", False))
        skip_wand_confirm = bool(kwargs.pop("__skip_wand_confirm", False))

        result = action_runner.run_action(
            self,
            actor_id,
            action_name,
            skip_confirm=skip_confirm,
            skip_wand_confirm=skip_wand_confirm,
            is_ai=False,
            **kwargs,
        )

        # If action executed (not blocked or pending confirmation), advance time
        if result.executed:
            self._advance_time(self._level(), result.delay)

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

        tags = getattr(player, "tags", {}) or {}
        intrinsic = tags.get("intrinsic_actions")
        if not isinstance(intrinsic, list):
            intrinsic = list(getattr(player, "actions", ()) or [])
        if action_name in intrinsic:
            return False
        intrinsic.append(action_name)
        tags["intrinsic_actions"] = list(intrinsic)
        try:
            player.tags = tags
        except Exception:
            pass
        self.refresh_actor_actions(player.id)
        return True

    def refresh_actor_actions(self, actor_id: str) -> None:
        """Recompute an actor's actions from intrinsic + item-granted actions.

        Intrinsic actions are stored on the actor as `tags.intrinsic_actions` and are
        initialized lazily from the actor's current actions.

        Item-granted actions come from inventory items:
        - Held grants: item is in the actor's inventory (e.g. destabilizer)
        - Equipped grants: item is equipped (e.g. future wands)
        """
        actor_id = str(actor_id)
        try:
            lvl = self._level()
            actor = lvl.actors.get(actor_id)
        except Exception:
            actor = None
        if actor is None:
            return

        tags = getattr(actor, "tags", {}) or {}
        intrinsic = tags.get("intrinsic_actions")
        if not isinstance(intrinsic, list):
            intrinsic = list(getattr(actor, "actions", ()) or [])
            tags["intrinsic_actions"] = list(intrinsic)
            try:
                actor.tags = tags
            except Exception:
                pass

        try:
            inv = list(self.get_inventory(actor_id))
        except Exception:
            inv = []
        granted = item_grants.collect_active_granted_actions(inv)

        merged: List[str] = []
        seen: set[str] = set()
        for name in list(intrinsic) + list(granted):
            if not name:
                continue
            n = str(name)
            if n in seen:
                continue
            seen.add(n)
            merged.append(n)

        try:
            actor.actions = tuple(merged)
        except Exception:
            pass

        if actor_id == str(getattr(self, "player_id", "")) and hasattr(self, "ability_bar_state"):
            try:
                self.ability_bar_state.invalidate()
            except Exception:
                pass

    def effective_character_stats(self, owner_id: str | None = None) -> Dict[str, int]:
        """Return base CON/AGI/INT/RES with equipped item modifiers applied.

        - Base stats live on `self.character.stats`.
        - Equipped items are any inventory entities with `tags.equipped_slot`
          (or legacy `tags.equipped`).
        - Stat bonuses are declared per item via:

            tags.equip_mods: {con: +1, res: +2, ...}
        """
        base = dict(getattr(self.character, "stats", {}) or {})
        oid = str(owner_id) if owner_id is not None else str(getattr(self, "player_id", ""))
        try:
            inv = list(self.get_inventory(oid))
        except Exception:
            inv = []
        mods = equipment_system.collect_equip_mods(inv)
        return equipment_system.apply_mods(base, mods)





    # =========================================================================
    # PHASE 2: INVENTORY & EQUIPMENT -> systems/inventory.py
    # Container logic, pickup/drop, equip/unequip
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def get_inventory(self, owner_id: str) -> List[Entity]:
        return inventory_system.get_inventory(self, owner_id)

    @property
    def player_inventory(self) -> List[Entity]:
        return inventory_system.get_player_inventory(self)

    @property
    def messages(self) -> MessageLog:
        """Convenience accessor for the message log (quest system compatibility)."""
        return self.log





    # =========================================================================
    # PHASE 4: OVERMAP + CORRUPTION -> systems/overmap.py
    # World map threading, Julia grid, corruption anchors/hotspots
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def build_tile_julia_grid(self) -> None:
        """Precompute per-tile Julia coordinates across the whole world grid."""
        overmap_system.build_tile_julia_grid(self)

    def _init_overmap_params_and_grid(self) -> None:
        """Set fixed overmap params from curated c-path bounds and start background render."""
        overmap_system.init_overmap_params_and_grid(self)

    def _init_rune_anchors(self) -> None:
        """Seed rune-anchor POIs and corresponding corruption suppressors."""
        overmap_system.init_rune_anchors(self)

    def _start_world_map_thread(
        self,
        *,
        reason: str = "loading",
        width: Optional[int] = None,
        height: Optional[int] = None,
        span: Optional[int] = None,
        view: Optional[Tuple[float, float, float, float]] = None,
        view_token: Optional[int] = None,
        corruption_version: Optional[int] = None,
    ) -> None:
        """Start background thread to render the world map."""
        overmap_system.start_world_map_thread(
            self,
            reason=reason,
            width=width,
            height=height,
            span=span,
            view=view,
            view_token=view_token,
            corruption_version=corruption_version,
        )

    def _ensure_overmap_ready(self) -> None:
        """Ensure overmap params/grid exist."""
        overmap_system.ensure_overmap_ready(self)

    def _jx_jy_slices_for_zone(self, coord: Tuple[int, int, int]) -> tuple[Optional[List[float]], Optional[List[float]]]:
        """Return (jx_slice, jy_slice) for this zone coord using the global tile_julia_grid."""
        return overmap_system.jx_jy_slices_for_zone(self, coord)

    def set_corruption_level(self, level: float) -> None:
        """Set global corruption intensity (phase 1: visuals-only morphing)."""
        overmap_system.set_corruption_level(self, level)

    def set_corruption_spline_weight(self, weight: float) -> None:
        """Set weight for the optional spline-based distortion field (0 disables)."""
        overmap_system.set_corruption_spline_weight(self, weight)

    def add_corruption_hotspot(self, jx: float, jy: float, strength: float, sigma: float) -> None:
        """Add a localized corruption 'cone' (Gaussian bump) in Julia-plane coordinates."""
        overmap_system.add_corruption_hotspot(self, jx, jy, strength, sigma)

    def _alloc_rune_anchor_poi_id(self) -> str:
        """Return a unique POI id for a newly-created rune anchor."""
        return overmap_system.alloc_rune_anchor_poi_id(self)

    # =========================================================================
    # PHASE 5: LEGENDARIES & POI DISCOVERY -> systems/legendaries.py
    # These methods now delegate to legendaries_system
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _alloc_legendary_lair_poi_id(self) -> str:
        """Return a unique POI id for a newly-generated legendary lair."""
        return legendaries_system.alloc_legendary_lair_poi_id()

    def _init_legendaries(self, count: int = 50) -> None:
        """Generate legendary creatures and inject their lair POIs."""
        if getattr(self, "legendary_registry", None) is not None:
            return
        self.legendary_registry = legendaries_system.init_legendaries(self, count=count)

    def add_poi_rumor(self, poi_id: str, *, log: bool = True) -> None:
        """Mark a POI as rumored so it appears on the world map before discovery."""
        legendaries_system.add_poi_rumor(self, poi_id, log=log)

    def get_nearest_legendary_lairs(self, *args, **kwargs) -> List[Tuple[str, Tuple[int, int, int]]]:
        """Return nearby legendary lair POIs.

        Supports two call styles:
        - New: ``get_nearest_legendary_lairs(n=5, from_coord=(zx, zy, 0))``
        - Back-compat: ``get_nearest_legendary_lairs(zx, zy, max_dist=10, n=5)``
        """
        max_dist = kwargs.pop("max_dist", None)

        if len(args) >= 2:
            # Back-compat signature: (zx, zy, max_dist=?)
            try:
                zx = int(args[0])
                zy = int(args[1])
            except Exception:
                zx, zy = 0, 0
            from_coord = (zx, zy, 0)
            try:
                n = int(kwargs.pop("n", 5))
            except Exception:
                n = 5
        else:
            # New signature: (n=?, from_coord=?)
            try:
                n = int(args[0]) if args else int(kwargs.pop("n", 5))
            except Exception:
                n = 5
            from_coord = kwargs.pop("from_coord", None)

        results = legendaries_system.get_nearest_legendary_lairs(self, n, from_coord=from_coord)

        # Optional distance filter for the old signature (zone distance on the overworld).
        if max_dist is not None and from_coord is not None:
            try:
                ox, oy = int(from_coord[0]), int(from_coord[1])
                md = int(max_dist)
            except Exception:
                return results
            md2 = md * md
            results = [(pid, coord) for (pid, coord) in results if (coord[0] - ox) ** 2 + (coord[1] - oy) ** 2 <= md2]

        return results

    def _discover_pois_for_level(self, level: LevelState) -> None:
        """Record POIs attached to this level as discovered."""
        legendaries_system.discover_pois_for_level(self, level)

    def add_corruption_anchor(
        self,
        jx: float,
        jy: float,
        *,
        sigma: float,
        strength: float = 1.0,
        coord: Optional[Tuple[int, int, int]] = None,
        spawn_pos: Optional[Tuple[int, int]] = None,
    ) -> Optional[str]:
        """Add a rune anchor (corruption suppressor) and optionally create a POI marker."""
        return overmap_system.add_corruption_anchor(
            self, jx, jy, sigma=sigma, strength=strength, coord=coord, spawn_pos=spawn_pos
        )

    def _refresh_corruption_visuals(self) -> None:
        """Refresh already-instantiated overworld visuals and kick off overmap rerender."""
        overmap_system.refresh_corruption_visuals(self)

    # =========================================================================
    # PHASE 8: ZONE MANAGEMENT -> systems/zones.py
    # Zone creation, transitions, stairs, fast travel
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _make_zone(self, coord: Tuple[int, int, int], up_pos: Optional[Tuple[int, int]]) -> LevelState:
        x, y, depth = coord
        world = World(width=self.cfg.world_width, height=self.cfg.world_height)
        # Determine any POIs that hit this coord (used for lab/structures).
        poi_hits = [pid for pid, poi in poi_content.POIS.items() if tuple(poi.coord) == tuple(coord)]
        is_lab_zone = False
        is_lair_zone = False
        lair_layout = "multi_room"
        for pid in poi_hits:
            poi = poi_content.POIS.get(pid)
            if not poi:
                continue
            for struct in getattr(poi, "structures", []) or []:
                if struct.get("kind") == "lab":
                    is_lab_zone = True
                    break
                if struct.get("kind") == "legendary_lair":
                    is_lair_zone = True
                    lair_layout = str(struct.get("layout") or lair_layout)
            if is_lab_zone:
                break
            if is_lair_zone:
                # Only one lair layout exists today, but keep this selector so
                # adding arena/maze/fortress variants doesn't touch glue code.
                break

        if depth == 0 and is_lab_zone:
            mapgen.generate_lab(world, self.rng)
            lab_state = LabState()
        elif depth == 0 and is_lair_zone:
            mapgen_sites.generate_legendary_lair(world, self.rng, layout=lair_layout)
            lab_state = None
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
            # Default fast-travel spawn is the middle of the bottom edge so arriving
            # in a new overworld zone feels directional. The starting zone is the
            # exception: it should spawn in the center.
            if up_pos is None and "starting_zone" not in poi_hits:
                ex = world.width // 2
                ey = max(0, world.height - 2)
                if world.in_bounds(ex, ey) and world.is_walkable(ex, ey):
                    world.entry = (ex, ey)
            lab_state = None
        else:
            mapgen.generate_basic(world, self.rng, up_pos=up_pos, coord=coord)
            lab_state = None
        # Apply POIs (records ids on world)
        poi_hits = mapgen.apply_pois(world, coord)
        # Build starting structures (e.g., depotdepot)
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
        # Compute difficulty metadata for this zone (tier + sources).
        difficulty_system.apply_zone_difficulty(self, lvl, coord)
        # Spawn NPCs/entities from any POIs for this level (e.g., starting NPCs)
        self._spawn_poi_contents(lvl, coord)

        # Realize biome-based sites (if any) for this zone
        if depth == 0 and not is_lab_zone and not is_lair_zone:
            try:
                # Check if site placement is complete
                placement_complete = getattr(self, "site_placement_complete", False)
                self._debug(f"[mapgen] Zone ({x}, {y}): site_placement_complete={placement_complete}")

                count, discovered_site = mapgen_sites.realize_sites_in_zone(self, lvl, x, y, depth)
                self._debug(f"[mapgen] Zone ({x}, {y}): realize_sites_in_zone returned count={count}, site={discovered_site}")

                if count > 0 and discovered_site is not None:
                    self._debug(f"[mapgen] Realized {count} site(s) at zone ({x}, {y}): {discovered_site.kind}")
                    # Show discovery message for newly discovered sites
                    from edgecaster.systems.sites import load_site_types
                    site_types = load_site_types()
                    site_config = site_types.get(discovered_site.kind)
                    site_name = site_config.name if site_config else discovered_site.kind.replace("_", " ").title()
                    self.set_urgent(
                        f"You have found a {site_name}!",
                        title="Discovery!",
                        choices=["Continue..."]
                    )
            except Exception as e:
                self._debug(f"[mapgen] Error realizing sites at ({x}, {y}): {e!r}")

        if coord == (0, 0, 0) and not getattr(self, "_academy_hint_shown", False):
            self._academy_hint_shown = True
            academy = self.poi_locations.get("academy")
            if academy:
                ax, ay, _ = academy
                self.log.add(f"You hear of an Academy at ({ax},{ay}).")

        # scatter some test berries on overworld levels
        if coord[2] == 0:  # depth == 0
            # Don't scatter berries in lairs (they manage their own content).
            if not getattr(world, "is_lair", False):
                self._scatter_test_berries(lvl, count=10)

        return lvl



    # =========================================================================
    # PHASE 1: SPAWNING & ENTITY FACTORIES -> systems/spawning.py
    # These methods now delegate to spawning_system module
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _enemy_template_ids(self) -> List[str]:
        """Get valid enemy template IDs. Delegates to spawning_system."""
        return spawning_system.get_enemy_template_ids(self)





    def _spawn_enemies(self, level: LevelState, count: int) -> None:
        """Spawn enemies. Delegates to spawning_system."""
        spawning_system.spawn_enemies(self, level, count)

    def _entity_templates(self) -> Dict[str, dict]:
        """Get entity templates. Delegates to spawning_system."""
        return spawning_system.get_entity_templates(self)

    def _spawn_entity_from_template(
        self,
        template_id: str,
        pos: Tuple[int, int],
        overrides: Optional[Dict[str, object]] = None,
    ) -> Entity:
        """Spawn entity from template. Delegates to spawning_system."""
        return spawning_system.spawn_entity_from_template(self, template_id, pos, overrides)

    def _spawn_mentor(self, level: LevelState) -> None:
        """Place mentor NPC near entry. Delegates to spawning_system."""
        spawning_system.spawn_mentor(self, level)

    def _spawn_intro_npcs(self, level: LevelState) -> None:
        """Place intro NPCs. Delegates to spawning_system."""
        spawning_system.spawn_intro_npcs(self, level)

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

        def build_ruin_structure(*, layout: str = "multi_room") -> Optional[Dict[str, object]]:
            world = level.world
            wall_color = (110, 90, 80)
            floor_color = (70, 60, 50)

            for _ in range(80):
                w = int(self.rng.randint(9, 13))
                h = int(self.rng.randint(7, 11))
                x0 = int(self.rng.randint(1, max(1, world.width - w - 1)))
                y0 = int(self.rng.randint(1, max(1, world.height - h - 1)))

                walkable = 0
                total = w * h
                for dy in range(h):
                    for dx in range(w):
                        if world.is_walkable(x0 + dx, y0 + dy):
                            walkable += 1
                if walkable < int(total * 0.7):
                    continue

                interior: set[Tuple[int, int]] = set()
                for dy in range(h):
                    for dx in range(w):
                        x = x0 + dx
                        y = y0 + dy
                        tile = world.get_tile(x, y)
                        if tile is None:
                            continue
                        border = (dx == 0 or dx == w - 1 or dy == 0 or dy == h - 1)
                        if border:
                            tile.walkable = False
                            tile.glyph = "█"
                            tile.color = wall_color
                        else:
                            tile.walkable = True
                            tile.glyph = "."
                            tile.color = floor_color
                            interior.add((x, y))

                door_pos = (x0 + w // 2, y0 + h - 1)

                if layout == "multi_room" and w >= 10 and h >= 8:
                    if self.rng.random() < 0.5:
                        wall_x = x0 + w // 2
                        gap_y = y0 + h // 2
                        for yy in range(y0 + 1, y0 + h - 1):
                            tile = world.get_tile(wall_x, yy)
                            if tile is None:
                                continue
                            if yy == gap_y:
                                tile.walkable = True
                                tile.glyph = "."
                                tile.color = floor_color
                                interior.add((wall_x, yy))
                            else:
                                tile.walkable = False
                                tile.glyph = "█"
                                tile.color = wall_color
                                interior.discard((wall_x, yy))
                    else:
                        wall_y = y0 + h // 2
                        gap_x = x0 + w // 2
                        for xx in range(x0 + 1, x0 + w - 1):
                            tile = world.get_tile(xx, wall_y)
                            if tile is None:
                                continue
                            if xx == gap_x:
                                tile.walkable = True
                                tile.glyph = "."
                                tile.color = floor_color
                                interior.add((xx, wall_y))
                            else:
                                tile.walkable = False
                                tile.glyph = "█"
                                tile.color = wall_color
                                interior.discard((xx, wall_y))

                for _ in range(int(self.rng.randint(2, 5))):
                    if self.rng.random() < 0.5:
                        bx = x0 + self.rng.randint(1, w - 2)
                        by = y0 if self.rng.random() < 0.5 else (y0 + h - 1)
                    else:
                        bx = x0 if self.rng.random() < 0.5 else (x0 + w - 1)
                        by = y0 + self.rng.randint(1, h - 2)
                    if (bx, by) == door_pos:
                        continue
                    tile = world.get_tile(bx, by)
                    if tile:
                        tile.walkable = True
                        tile.glyph = "."
                        tile.color = floor_color

                center = (x0 + w // 2, y0 + h // 2)
                return {
                    "rect": (x0, y0, w, h),
                    "door_pos": door_pos,
                    "interior": list(interior),
                    "center": center,
                }

            return None

        for pid in poi_ids:
            poi = poi_content.POIS.get(pid)
            if not poi:
                continue
            # Handle structures
            for struct in getattr(poi, "structures", []) or []:
                if struct.get("kind") == "item_depot" and depot_info:
                    # --- Walls (entities) ---
                    for pos in (depot_info.get("wall_positions") or []):
                        try:
                            # Use the Game helper; Level doesn't implement get_entity_at in this codebase.
                            if not self._entity_at(level, pos):
                                ent = self._spawn_entity_from_template("wall", pos)
                                level.entities[ent.id] = ent

                            # Underlying terrain should remain "normal" walkability; the wall entity blocks.
                            tile = level.world.get_tile(*pos)
                            if tile:
                                tile.walkable = True
                        except Exception:
                            pass

                    # --- Door (entity) ---
                    door_pos = depot_info.get("door")
                    if door_pos:
                        try:
                            if not self._entity_at(level, door_pos):
                                ent = self._spawn_entity_from_template("door", door_pos)
                                level.entities[ent.id] = ent

                            # Do not overwrite glyph here; door entity renders as '+'.
                            tile = level.world.get_tile(*door_pos)
                            if tile:
                                tile.walkable = True
                        except Exception:
                            pass

                    # --- Sign (entity) ---
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



                    # Place items in interior - one of each type first, then random
                    interior = depot_info.get("interior") or []
                    # All spawnable items (excludes base templates, features, currency)
                    item_ids = [
                        # Consumables
                        "blueberry",
                        "raspberry",
                        "strawberry",
                        "healing_kit",
                        # Utility items
                        "destabilizer",
                        "debug_inventory",
                        "koch_knife",
                        "whip",
                        "energy_flask",
                        # Equipment (stat mods)
                        "resonant_ring",
                        "coherence_crystal",
                        "sage_cap",
                        "fleet_boots",
                        "vital_belt",
                        "glowing_band",
                        # Wands (equip-grant + charges)
                        "wand_koch",
                        "wand_branch",
                        "wand_zigzag",
                        "wand_activate_n",
                        "wand_sparkle",
                    ]
                    # Shuffle to get one of each, then fill remaining slots
                    shuffled = list(item_ids)
                    self.rng.shuffle(shuffled)
                    spawn_queue = list(shuffled)  # One of each first
                    # If more interior spots than items, cycle through again
                    while len(spawn_queue) < len(interior):
                        extra = list(item_ids)
                        self.rng.shuffle(extra)
                        spawn_queue.extend(extra)
                    for i, pos in enumerate(interior):
                        try:
                            template_id = spawn_queue[i]
                            ent = self._spawn_entity_from_template(template_id, pos)
                            level.entities[ent.id] = ent
                        except Exception:
                            continue
                elif struct.get("kind") == "rune_anchor":
                    # A simple local representation of the rune anchor.
                    # The *corruption suppression* is global and handled by the
                    # corruption system; this entity is just a visible POI.
                    center = (level.world.width // 2, level.world.height // 2)
                    spot = nearest_walkable(center)
                    if spot and not self._entity_at(level, spot):
                        try:
                            ent = self._spawn_entity_from_template("rune_anchor", spot)
                            level.entities[ent.id] = ent
                        except Exception:
                            pass
                elif struct.get("kind") == "sealing_rune_trial":
                    # Attach a sealing rune trial to this level (visual overlay + logic).
                    try:
                        from edgecaster.systems import seal_trials
                        trial_id = str(struct.get("trial_id") or "starter_seal")
                        seal_trials.attach_trial_to_level(self, level, trial_id)
                    except Exception as e:
                        self._debug(f"[seal_trials] Failed to attach trial: {e!r}")
                elif struct.get("kind") == "destabilizer_ruin":
                    layout = str(struct.get("layout") or "multi_room")
                    ruin_info = build_ruin_structure(layout=layout)
                    if ruin_info is None:
                        ruin_info = {"center": entry, "interior": []}

                    door_pos = ruin_info.get("door_pos")
                    if isinstance(door_pos, tuple):
                        try:
                            ent = self._spawn_entity_from_template("door", door_pos)
                            level.entities[ent.id] = ent
                            tile = level.world.get_tile(*door_pos)
                            if tile:
                                tile.walkable = False
                                tile.glyph = "+"
                        except Exception:
                            pass

                    interior_tiles = list(ruin_info.get("interior") or [])
                    self.rng.shuffle(interior_tiles)
                    dest_pos = None
                    for pos in interior_tiles:
                        if self._actor_at(level, pos) or self._entity_at(level, pos):
                            continue
                        dest_pos = pos
                        break
                    if dest_pos is None:
                        dest_pos = nearest_walkable(tuple(ruin_info.get("center", entry)))
                    if dest_pos:
                        try:
                            ent = self._spawn_entity_from_template("destabilizer", dest_pos)
                            level.entities[ent.id] = ent
                        except Exception:
                            pass

                    enemy_pool = struct.get("enemy_pool") or [
                        "corrupted_thug",
                        "mana_viper",
                        "shadow",
                        "raving_lunatic",
                        "goblin",
                    ]
                    if not isinstance(enemy_pool, list):
                        enemy_pool = list(enemy_pool)
                    try:
                        enemy_count = int(struct.get("enemy_count", 5))
                    except Exception:
                        enemy_count = 5
                    enemy_count = max(0, enemy_count)
                    boss_id = struct.get("boss_id")
                    spawn_ids: List[str] = []
                    if boss_id:
                        spawn_ids.append(str(boss_id))
                    for _ in range(max(0, enemy_count - len(spawn_ids))):
                        try:
                            spawn_ids.append(str(self.rng.choice(enemy_pool)))
                        except Exception:
                            pass

                    center = tuple(ruin_info.get("center", entry))
                    for enemy_id in spawn_ids:
                        pos = spawning_system.find_spawn_position(
                            self,
                            level,
                            near=center,
                            radius=6,
                            avoid_actors=True,
                            avoid_entities=True,
                        )
                        if pos is None:
                            continue
                        try:
                            mob = enemy_factory.spawn_enemy(enemy_id, pos)
                            mob.tags = getattr(mob, "tags", None) or {}
                            mob.tags["poi_id"] = pid
                            spawning_system.register_actor(self, level, mob, schedule_ai=True)
                        except Exception:
                            continue
                elif struct.get("kind") == "legendary_lair":
                    base_proto = str(struct.get("template_id") or struct.get("proto_id") or "imp")
                    legend_name = struct.get("name")
                    try:
                        hp_mult = float(struct.get("hp_mult", 3.0) or 3.0)
                    except Exception:
                        hp_mult = 3.0

                    # Put the legendary at the lair's suggested boss position, falling
                    # back to the zone center if the lair generator didn't provide one.
                    boss_hint = None
                    try:
                        li = getattr(level.world, "lair_info", None)
                        if isinstance(li, dict):
                            boss_hint = li.get("boss_pos")
                    except Exception:
                        boss_hint = None
                    center = boss_hint if boss_hint else (level.world.width // 2, level.world.height // 2)
                    spot = nearest_walkable(center)
                    if spot:
                        actor = enemy_factory.spawn_enemy(base_proto, spot)
                        if legend_name:
                            actor.name = str(legend_name)
                        actor.tags = getattr(actor, "tags", {}) or {}
                        actor.tags["legendary"] = True
                        if "legendary_id" in struct:
                            actor.tags["legendary_id"] = struct.get("legendary_id")
                        actor.tags["lair_poi_id"] = pid
                        # Generate procedural faction standings for this legendary
                        try:
                            from edgecaster.systems.reputation import generate_procedural_standings
                            actor.tags["faction_standings"] = generate_procedural_standings(
                                self.rng, bias_positive=0.5
                            )
                        except Exception:
                            pass
                        try:
                            base_hp = int(getattr(actor.stats, "max_hp", 1) or 1)
                            boosted = max(base_hp + 1, int(round(base_hp * hp_mult)))
                            actor.stats.max_hp = boosted
                            actor.stats.hp = boosted
                        except Exception:
                            pass

                        level.actors[actor.id] = actor
                        level.entities[actor.id] = actor
                        # Schedule AI for this legendary (same as normal enemies).
                        self._schedule(
                            level,
                            self.cfg.action_time_fast,
                            lambda aid=actor.id, lvl=level: self._monster_act(lvl, aid),
                        )

                        # Spawn a few minions of the same base creature near the boss.
                        try:
                            minions = int(self.rng.randint(3, 5))
                        except Exception:
                            minions = 3
                        spawned = 0
                        attempts = 0
                        while spawned < minions and attempts < 200:
                            attempts += 1
                            dx = int(self.rng.randint(-6, 6))
                            dy = int(self.rng.randint(-4, 4))
                            if dx == 0 and dy == 0:
                                continue
                            tx = int(spot[0] + dx)
                            ty = int(spot[1] + dy)
                            if not level.world.in_bounds(tx, ty):
                                continue
                            if not level.world.is_walkable(tx, ty):
                                continue
                            if self._actor_at(level, (tx, ty)):
                                continue
                            if self._blocking_entity_at(level, (tx, ty)):
                                continue
                            mob = enemy_factory.spawn_enemy(base_proto, (tx, ty))
                            level.actors[mob.id] = mob
                            level.entities[mob.id] = mob
                            self._schedule(
                                level,
                                self.cfg.action_time_fast,
                                lambda aid=mob.id, lvl=level: self._monster_act(lvl, aid),
                            )
                            spawned += 1
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
                elif spec.npc_id == "merchant":
                    actor = enemy_factory.spawn_enemy("merchant", spawn_pos)
                    actor.faction = "npc"
                    actor.actions = ()
                    actor.ai = "idle"
                    actor.tags = getattr(actor, "tags", {}) or {}
                    actor.tags["npc_id"] = spec.npc_id
                    actor.tags["merchant_id"] = npc_def.get("merchant_id", "general_store")
                    # Dev convenience: the starting-zone merchant stocks one of every
                    # item prototype and refreshes each time you talk to them.
                    if pid == "starting_zone":
                        actor.tags["merchant_all_items"] = True
                        actor.tags["merchant_refresh_on_talk"] = True

                    # Apply POI overrides for look/feel.
                    actor.name = name
                    actor.glyph = glyph
                    actor.color = color  # type: ignore[assignment]
                    desc = getattr(spec, "description", None) or npc_def.get("description") or actor.description
                    if desc:
                        actor.description = desc

                    # Ensure the merchant has a stock + restock schedule.
                    try:
                        from edgecaster.systems import trade as trade_system

                        trade_system.ensure_merchant_initialized(self, level, actor)
                    except Exception:
                        pass
                else:
                    aid = self._new_id()
                    actor = Human(
                        id=aid,
                        name=name,
                        pos=spawn_pos,
                        faction="npc",
                        stats=Stats(hp=50, max_hp=50),
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
        spawning_system.spawn_npcs(self, level, count)

    def _spawn_entities_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        place_entity: Callable[[Tuple[int, int]], None],
        radius: int = 3,
    ) -> int:
        return spawning_system.spawn_entities_near(self, level, center, count, place_entity, radius)

    def _spawn_imps_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        radius: int = 3,
    ) -> int:
        return spawning_system.spawn_imps_near(self, level, center, count, radius)


    def _spawn_echoes_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        radius: int = 3,
    ) -> int:
        return spawning_system.spawn_echoes_near(self, level, center, count, radius)


    def _spawn_berries_near(
        self,
        level: LevelState,
        center: Tuple[int, int],
        count: int,
        radius: int = 3,
    ) -> int:
        return spawning_system.spawn_berries_near(self, level, center, count, radius)

    def _scatter_test_berries(self, level: LevelState, count: int = 30) -> None:
        spawning_system.scatter_test_berries(self, level, count)

    def debug_spawn_inventory_near_player(self, radius: int = 3, *, count: int | None = None) -> None:
        """Debug helper: conjure a curated batch of meta-Inventories near the player.

        Spawns:
          - one of EACH functional adjective inventory (debugging visual effects)
          - plus 3 non-functional inventories (common "junk" bags for lulz)

        Functional adjectives are guaranteed to appear exactly once per call.
        """
        level = self._level()
        if self.player_id not in level.actors:
            return
        player = level.actors[self.player_id]

        # Functional adjectives -> effect name(s) (from visual_effects.py registry).
        # NOTE: "mirrored" resolves to either mirror_x or mirror_y per spawn.
        functional_map: dict[str, list[str]] = {
            "clockwise": ["clockwise"],
            "counter-clockwise": ["counter-clockwise"],
            "ghostly": ["ghostly"],
            "mirrored": ["mirror_x"],
            "fiery": ["fiery"],
            "bismuth": ["bismuth"],
            "jittery": ["jittery"],
            "colossal": ["colossal"],
            "smoky": ["smoky"],
            "malfunctioning": ["malfunctioning"],
            "carbonated": ["carbonated"],
            "toasty": ["toasty"],
            "arctic": ["arctic"],
            "syrupy": ["syrupy"],
            "candlelit": ["candlelit"],
            "octonionic": ["octonionic"],
            "celestial": ["celestial"],
            "extropic": ["extropic"],
            "entropic": ["entropic"],
            "underwhelming": ["underwhelming"],
            "revolving": ["revolving"],
            "orbital": ["orbital"],

        }

        # Non-functional pool (pure flavor; no effects)
        nonfunctional_adjectives = [
            "fetid", "dubious", "spectacular", "outrageous", "sensible",
            "colossal", "lightly-aged", "unfortunate", "malicious",
            "courageous", "flavorful", "salty", "magnanimous",
            "pernicious", "persuasive", "cartoonish", "trapezoidal",
            "bovine", "spectral", "capitalized", "automatic",
            "recursive", "stout",
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
            "malfunctioning", "twisted", "eldritch", "malted",
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
        nonfunctional_adjectives = [a for a in nonfunctional_adjectives if a.lower() not in functional_set]

        # Shuffle non-functional pool so the 3 "common" bags tend not to repeat.
        nonfunc_pool = list(nonfunctional_adjectives)
        self.rng.shuffle(nonfunc_pool)

        def next_nonfunc_adj() -> str:
            nonlocal nonfunc_pool
            if not nonfunc_pool:
                nonfunc_pool = list(nonfunctional_adjectives)
                self.rng.shuffle(nonfunc_pool)
            return nonfunc_pool.pop()

        def find_spot_near(center: tuple[int, int], radius: int, max_attempts: int = 200) -> tuple[int, int] | None:
            cx, cy = center
            for _ in range(max_attempts):
                x = cx + self.rng.randint(-radius, radius)
                y = cy + self.rng.randint(-radius, radius)
                if not level.world.in_bounds(x, y):
                    continue
                if not level.world.is_walkable(x, y):
                    continue
                if self._actor_at(level, (x, y)):
                    continue
                if self._entity_at(level, (x, y)):
                    continue
                return (x, y)
            return None

        def spawn_inventory_at(pos: tuple[int, int], adjective: str, *, functional: bool) -> None:
            tags: dict[str, object] = {}

            if functional:
                effects = list(functional_map.get(adjective, []))
                if effects:
                    tags["visual_effects"] = effects

            display_name = f"{adjective} Inventory"

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
                pos,
                overrides=overrides,
            )
            ent.description = "Definitely NOT a bag, it's much more Platonic than that."

            level.entities[ent.id] = ent
            # Ensure it has an inventory slot allocated
            self.get_inventory(ent.id)

        # --- Spawn plan: all functional + 3 non-functional ---
        desired: list[tuple[str, bool]] = []
        for adj in functional_map.keys():
            desired.append((adj, True))
        for _ in range(3):
            desired.append((next_nonfunc_adj(), False))

        spawned = 0
        center = player.pos
        for adj, is_func in desired:
            spot = find_spot_near(center, radius=radius, max_attempts=250)
            if spot is None:
                continue
            spawn_inventory_at(spot, adj, functional=is_func)
            spawned += 1

        if spawned > 0:
            self.log.add(f"Inventory drop! ({spawned} conjured.)")
        else:
            self.log.add("This is no place for an inventory.")

    # =========================================================================
    # PHASE 9: SCHEDULING & TIME -> systems/scheduling.py
    # Tick scheduling, time advancement, cooldowns, regen
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _schedule(self, level: LevelState, delay: int, action: Callable[[], None]) -> None:
        """Schedule an action to run after `delay` ticks."""
        scheduling.schedule(self, level, delay, action)

    def _advance_time(self, level: LevelState, delta: int) -> None:
        """Advance time by delta ticks. Delegates to scheduling module."""
        scheduling.advance_time(self, level, delta)

    def _start_regen(self, level: LevelState, actor_id: str, amount: int, interval: int) -> None:
        """Start periodic regen for an actor. Delegates to scheduling module."""
        scheduling.start_regen(self, level, actor_id, amount, interval)

    def _coherence_tick(self, level: LevelState, delta: int) -> None:
        """Drain coherence each tick. Delegates to scheduling module."""
        scheduling.coherence_tick(self, level, delta)

    def _cooldown_tick(self, level: LevelState, delta: int) -> None:
        """Tick down cooldowns. Delegates to scheduling module."""
        scheduling.cooldown_tick(self, level, delta)

    def _slow_mult(self, actor: Actor) -> float:
        """Get slow multiplier for an actor. Delegates to scheduling module."""
        return scheduling.slow_mult(actor)

    def _apply_action_tick_offset(self, actor: Actor, delay: int) -> int:
        """Apply additive tick offset. Delegates to scheduling module."""
        return scheduling.apply_action_tick_offset(actor, delay)


    # =========================================================================
    # PHASE 3: LORENZ / STRANGE ATTRACTOR -> systems/lorenz_aura.py
    # These methods now delegate to lorenz_aura module
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _init_lorenz_points(self) -> None:
        """Initialize Lorenz points via lorenz module."""
        lorenz.init_lorenz_points(self)

    def _step_lorenz(self, steps: int) -> None:
        """Step Lorenz points via lorenz module."""
        lorenz.step_lorenz(self, steps)

    def _advance_lorenz(self, level: LevelState, delta: int) -> None:
        """Advance the Lorenz aura for Strange Attractors."""
        lorenz_aura.advance_lorenz(self, level, delta)

    def _lorenz_contact_damage(self, level: LevelState) -> None:
        """Apply butterfly contact damage to nearby hostiles."""
        lorenz_aura.apply_contact_damage(self, level)



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

    def _items_at(self, level: LevelState, pos: Tuple[int, int]) -> List[Entity]:
        """Return all non-actor items at the given position."""
        return [
            e for e in level.entities.values()
            if getattr(e, "pos", None) == pos
            and getattr(e, "kind", None) == "item"
        ]

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
            ent.blocks_vision = ent.blocks_movement

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
                tile.glyph = "+"
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
        """Get (and lazily create) a zone. Delegates to zones_system."""
        return zones_system.get_zone(self, coord, up_pos)

    def get_zone_for_render(self, coord: Tuple[int, int, int]) -> LevelState:
        """Get zone for rendering without side effects. Delegates to zones_system."""
        return zones_system.get_zone_for_render(self, coord)

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


    def describe_tile_at(self, pos: Tuple[int, int]) -> str:
        level = self._level()
        tile = level.world.get_tile(*pos)
        if tile is None:
            return "You see nothing but void."

        god_vision = bool(getattr(self, "god_vision", False))
        explored = bool(getattr(tile, "explored", True))

        # --- Biome label (default: realized tile biome_id) ---
        biome_label = "Unknown"
        stored_biome_id = None
        try:
            stored_biome_id = int(getattr(tile, "biome_id", 0) or 0)
            from edgecaster.climate import Biome, BIOME_SHORT_NAMES
            b = Biome(stored_biome_id)
            biome_label = BIOME_SHORT_NAMES.get(b, b.name.replace("_", " ").title())
        except Exception:
            biome_label = "Unknown"

        passable = "Yes" if getattr(tile, "walkable", True) else "No (Blocked)"

        relief_label_map = {
            "≈": "Deep Ocean",
            "~": "Shallow Water",
            ",": "Coast",
            ".": "Plains",
            '"': "Hills",
            "#": "Mountains",
            "^": "Peak",
        }

        # Canonical relief ladder used by ascii.py's overmap LOD glyph ladder.
        relief_glyphs_by_cat = ["≈", "~", ",", ".", '"', "#", "^"]

        relief_glyph = "?"
        relief_label = "Unknown"

        # ---------------------------------------------------------------------
        # Preferred: ask the renderer what it drew (LOD cache).
        # This is the only correct answer when zoomed-out (LOD cells != tiles).
        # ---------------------------------------------------------------------
        try:
            cfg = getattr(self, "cfg", None)
            zone_w = int(getattr(cfg, "world_width", getattr(level.world, "width", 60) or 60))
            zone_h = int(getattr(cfg, "world_height", getattr(level.world, "height", 40) or 40))

            # IMPORTANT: renderer uses game.zone_coord (not level.coord)
            zx, zy, _zz = getattr(self, "zone_coord", (0, 0, 0))
            tx, ty = int(pos[0]), int(pos[1])

            abs_wx = float(int(zx) * zone_w + tx)
            abs_wy = float(int(zy) * zone_h + ty)

            # Find the Ascii renderer instance (best-effort; depends on wiring)
            ascii_renderer = None
            mgr = getattr(self, "scene_manager", None)
            candidates = []

            if mgr is not None:
                candidates.extend([
                    getattr(mgr, "renderer", None),
                    getattr(mgr, "ascii_renderer", None),
                    getattr(mgr, "ascii", None),
                ])
                r = getattr(mgr, "renderer", None)
                if r is not None:
                    candidates.extend([
                        getattr(r, "ascii_renderer", None),
                        getattr(r, "ascii", None),
                        getattr(r, "ascii_render", None),
                    ])

            for c in candidates:
                if c is None:
                    continue
                if hasattr(c, "_lod_cell_cache") and hasattr(c, "_overmap_signature") and hasattr(c, "_render_lod_grid"):
                    ascii_renderer = c
                    break

            cache = getattr(ascii_renderer, "_lod_cell_cache", None) if ascii_renderer is not None else None
            if isinstance(cache, dict) and ascii_renderer is not None:
                # Recompute LOD the same way ascii.py draw_world_zoomed() does.
                world_scale = float(getattr(ascii_renderer, "tile_px", float(getattr(ascii_renderer, "base_tile", 18)) * float(getattr(ascii_renderer, "zoom", 1.0))))
                world_scale = max(1e-6, world_scale)

                target_glyph_px = float(getattr(ascii_renderer, "lod_target_glyph_px", getattr(ascii_renderer, "base_tile", 18)) or getattr(ascii_renderer, "base_tile", 18))
                raw = target_glyph_px / world_scale

                LOD_RADIX = getattr(ascii_renderer, "lod_radix", 2)
                lod_f = math.log(max(1e-12, raw), LOD_RADIX)

                lod0 = int(math.floor(lod_f))
                lod1 = lod0 + 1
                frac = float(lod_f - lod0)

                lod0 = max(-12, min(lod0, 12))
                lod1 = max(-12, min(lod1, 12))
                if lod1 == lod0:
                    frac = 0.0

                cell0 = float(LOD_RADIX ** lod0)
                cell1 = float(LOD_RADIX ** lod1)

                # Same smoothstep + deadband as ascii.py
                def _smoothstep(a: float, b: float, t: float) -> float:
                    if t <= a:
                        return 0.0
                    if t >= b:
                        return 1.0
                    x = (t - a) / (b - a)
                    return x * x * (3.0 - 2.0 * x)

                blend = _smoothstep(0.0, 1.0, frac)
                eps = 0.06
                if blend <= eps:
                    a0, a1 = 1.0, 0.0
                elif blend >= 1.0 - eps:
                    a0, a1 = 0.0, 1.0
                else:
                    a0, a1 = 1.0 - blend, blend

                # Choose the dominant layer (what you mostly see)
                if a1 > a0 and abs(cell1 - cell0) > 1e-12:
                    lod_id = int(lod1)
                    cell_tiles = float(cell1)
                else:
                    lod_id = int(lod0)
                    cell_tiles = float(cell0)

                cell_tiles = max(1e-6, cell_tiles)

                # Cache keys use absolute cell coords (cx,cy) in world tile space.
                cx = int(math.floor(abs_wx / cell_tiles))
                cy = int(math.floor(abs_wy / cell_tiles))

                sig = ascii_renderer._overmap_signature(self)
                key = (lod_id, cell_tiles, cell_tiles, cx, cy, sig)
                val = cache.get(key, None)

                if val is not None:
                    # Cache entries can be either:
                    #   (ch, rgb)  [older]
                    #   (ch, rgb, biome_id, elev_cat)  [new]
                    ch = val[0] if len(val) > 0 else ""
                    cached_biome_id = None
                    cached_elev_cat = None

                    try:
                        if len(val) > 2:
                            cached_biome_id = int(val[2])
                    except Exception:
                        cached_biome_id = None

                    try:
                        if len(val) > 3:
                            cached_elev_cat = int(val[3])
                    except Exception:
                        cached_elev_cat = None

                    # Relief: prefer cached elev category (most stable), else use cached glyph char.
                    if cached_elev_cat is not None and 0 <= cached_elev_cat < len(relief_glyphs_by_cat):
                        relief_glyph = relief_glyphs_by_cat[cached_elev_cat]
                        relief_label = relief_label_map.get(relief_glyph, "Unknown")
                    elif isinstance(ch, str) and ch:
                        relief_glyph = ch[0]
                        relief_label = relief_label_map.get(relief_glyph, "Unknown")

                    # Prefer cached biome_id if available.
                    if cached_biome_id is not None:
                        try:
                            stored_biome_id = int(cached_biome_id)
                            from edgecaster.climate import Biome, BIOME_SHORT_NAMES
                            b = Biome(stored_biome_id)
                            biome_label = BIOME_SHORT_NAMES.get(b, b.name.replace("_", " ").title())
                        except Exception:
                            pass

        except Exception:
            # If cache plumbing fails, fall through to fallback.
            pass

        # ---------------------------------------------------------------------
        # Fallback: only trust tile.glyph if it is one of the NEW relief glyphs.
        # (Never resurrect legacy '%' etc.)
        # ---------------------------------------------------------------------
        if relief_glyph == "?":
            try:
                g = str(getattr(tile, "glyph", "") or "")
                if g and g[0] in relief_label_map:
                    relief_glyph = g[0]
                    relief_label = relief_label_map.get(relief_glyph, "Unknown")
            except Exception:
                pass

        if passable != "Yes" and relief_glyph == "█":
            relief_label = "Wall"

        return "\n".join([
            f"Biome: {biome_label} [tile:{stored_biome_id}]",
            f"Relief: {relief_label} ({relief_glyph})",
            f"Passable: {passable}",
        ])








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
        from edgecaster.systems.inventory import get_quantity

        # Get all items at this position
        items = self._items_at(level, pos)
        if not items:
            return

        # Filter out observer from auto-observe
        if auto and observer_id is not None:
            items = [i for i in items if getattr(i, "id", None) != observer_id]
            if not items:
                return

        # Check for bismuth currency first (special handling)
        bismuth_items = []
        regular_items = []
        for item in items:
            tags = getattr(item, "tags", {}) or {}
            if tags.get("currency") == "bismuth":
                bismuth_items.append(item)
            else:
                regular_items.append(item)

        # Describe bismuth separately
        for bitem in bismuth_items:
            tags = getattr(bitem, "tags", {}) or {}
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

        # Describe regular items
        if not regular_items:
            return

        if len(regular_items) == 1:
            # Single item - original behavior
            item = regular_items[0]
            name = getattr(item, "name", None) or "thing"
            qty = get_quantity(item)
            if qty > 1:
                self.log.add(f"You see here {qty} {name.lower()}s.")
            else:
                article = "an" if name and name[0].lower() in "aeiou" else "a"
                self.log.add(f"You see here {article} {name.lower()}.")
        else:
            # Multiple items - list them
            self.log.add(f"You see here {len(regular_items)} items:")
            for item in regular_items[:5]:
                name = getattr(item, "name", "something")
                qty = get_quantity(item)
                qty_suffix = f" ({qty})" if qty > 1 else ""
                self.log.add(f"  - {name}{qty_suffix}")
            if len(regular_items) > 5:
                self.log.add(f"  ... and {len(regular_items) - 5} more.")

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

    # --- PHASE 2: inventory manipulation methods -> systems/inventory.py ---

    def player_pick_up(self) -> None:
        inventory_system.player_pick_up(self)

    def drop_inventory_item(self, index: int) -> None:
        inventory_system.drop_inventory_item(self, index)

    def eat_item_from_inventory(self, owner_id: str, index: int) -> None:
        inventory_system.eat_item_from_inventory(self, owner_id, index)

    def eat_inventory_item(self, index: int) -> None:
        inventory_system.eat_inventory_item(self, index)

    def take_from_container(self, container_id: str, index: int) -> None:
        inventory_system.take_from_container(self, container_id, index)

    def move_item_between_inventories(
        self,
        src_owner_id: str,
        index: int,
        dest_owner_id: str,
    ) -> None:
        inventory_system.move_item_between_inventories(self, src_owner_id, index, dest_owner_id)

    def get_equipped_in_slot(self, owner_id: str, slot_id: str):
        return inventory_system.get_equipped_in_slot(self, owner_id, slot_id)

    def unequip_slot(self, owner_id: str, slot_id: str) -> None:
        inventory_system.unequip_slot(self, owner_id, slot_id)

    def unequip_item(self, owner_id: str, item_id: str) -> None:
        inventory_system.unequip_item(self, owner_id, item_id)

    def equip_item_to_slot(self, owner_id: str, item_id: str, slot_id: str) -> None:
        inventory_system.equip_item_to_slot(self, owner_id, item_id, slot_id)

    def equip_item_to_slot_qty(
        self,
        owner_id: str,
        item_id: str,
        slot_id: str,
        qty: int = 1,
    ) -> None:
        """Equip a specific quantity from a stacked item to a slot."""
        inventory_system.equip_item_to_slot_qty(self, owner_id, item_id, slot_id, qty)

    def move_item_between_inventories_qty(
        self,
        src_owner_id: str,
        index: int,
        dest_owner_id: str,
        qty: Optional[int] = None,
    ) -> None:
        """Move a specific quantity between inventories."""
        inventory_system.move_item_between_inventories_qty(
            self, src_owner_id, index, dest_owner_id, qty
        )


    @property
    def has_lorenz_aura(self) -> bool:
        """True if the current character should have the Lorenz storm aura."""
        return lorenz_aura.has_lorenz_aura(self)

    def _reset_lorenz_on_zone_change(self, player: Actor) -> None:
        """Hard-snap the Lorenz storm to the player when changing zones."""
        lorenz_aura.reset_on_zone_change(self, player)



    # =========================================================================
    # PHASE 6: PATTERN OPERATIONS -> systems/pattern_ops.py
    # Pattern projection, vertex queries, activation, ignite/regrow/freeze
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def projected_vertices(self) -> List[Tuple[float, float]]:
        return pattern_ops.projected_vertices(self)

    def nearest_vertex(self, world_pos: Tuple[float, float]) -> Optional[int]:
        return pattern_ops.nearest_vertex(self, world_pos)

    def neighbors_of(self, idx: int) -> List[int]:
        return pattern_ops.neighbors_of(self, idx)

    def neighbor_set_depth(self, seed: int, depth: int) -> List[int]:
        return pattern_ops.neighbor_set_depth(self, seed, depth)

    # --- PHASE 10 (continued): param helpers -> delegating to _param_manager ---

    def _stat_value(self, stat: str) -> int:
        """Get current value of a stat (delegates to _param_manager)."""
        return self._param_manager._get_stat_value(stat)

    def _allowed_index(self, action: str, key: str) -> int:
        """Return max tier index allowed by current stats."""
        return self._param_manager.allowed_index(action, key)

    def _param_value(self, action: str, key: str):
        """Get current param value respecting stat caps."""
        return self._param_manager._get_value_internal(action, key)

    def adjust_param(self, action: str, key: str, delta: int) -> Tuple[bool, str]:
        """Adjust a param by delta steps. Returns (success, message)."""
        return self._param_manager.adjust(action, key, delta)

    def param_view(self, action: str) -> List[dict]:
        """Return UI-friendly view of params for an action."""
        return self._param_manager.view(action)

    def get_param_value(self, action: str, key: str):
        """Get the current value for a param (with special cases)."""
        return self._param_manager.get_value(action, key)

    # --- placement -> pattern_ops ---

    def begin_place_mode(self) -> None:
        pattern_ops.begin_place_mode(self)

    def try_place_terminus(self, target: Tuple[int, int]) -> None:
        pattern_ops.try_place_terminus(self, target)

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
        pattern_ops.reset_pattern(self)


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



    def use_stairs_down(self) -> None:
        """Use downward stairs. Delegates to zones_system."""
        zones_system.use_stairs_down(self)

    def use_stairs_up(self) -> None:
        """Use upward stairs. Delegates to zones_system."""
        zones_system.use_stairs_up(self)


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

        # Recompute available actions for the new host (e.g. equipped/held item-grants).
        self.refresh_actor_actions(self.player_id)

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



    # =========================================================================
    # PHASE 7: COMBAT & DAMAGE -> systems/combat.py
    # Hostility checks, attack resolution, death handling
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def is_hostile(self, attacker: Actor, target: Actor) -> bool:
        """Reputation-driven hostility check. Delegates to combat_system."""
        return combat_system.is_hostile(self, attacker, target)

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
        if target and target.id != id:
            if self.is_hostile(actor, target) or self.is_hostile(target, actor):
                self._attack(level, actor, target)
                return
            # Friendly/neutral actors block movement.
            if id == self.player_id:
                self.log.add(f"You bump into {target.name}.")
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

        # Slaver packs: enforce that chains never exceed their leash length.
        # This is implemented at the movement dispatcher level so it applies
        # universally (AI, possession, scripted moves), not just in the AI layer.
        try:
            CHAIN_RANGE = 4  # tiles, using Chebyshev distance (same convention as adjacency checks)
            tags = getattr(actor, "tags", None) or {}
            proto_id = str(getattr(actor, "proto_id", "") or tags.get("template_id") or "")

            # Brutes: cannot step farther than CHAIN_RANGE from their slaver.
            master_id = tags.get("slaver_master_id")
            if master_id:
                master = level.actors.get(str(master_id))
                if master is None or not getattr(master, "alive", False):
                    # If the master no longer exists, treat the brute as freed.
                    tags.pop("slaver_master_id", None)
                    actor.tags = tags
                else:
                    mx, my = master.pos
                    cur_d = max(abs(x - mx), abs(y - my))
                    new_d = max(abs(nx - mx), abs(ny - my))
                    if cur_d <= CHAIN_RANGE:
                        # Normal case: prevent exceeding the leash.
                        if new_d > CHAIN_RANGE:
                            return
                    else:
                        # Recovery case (e.g. after a teleport): allow only moves that don't stretch further.
                        if new_d > cur_d:
                            return

            # Slavers: cannot step farther than CHAIN_RANGE from any still-chained brutes.
            if proto_id == "slaver":
                for other in level.actors.values():
                    otags = getattr(other, "tags", None) or {}
                    if otags.get("slaver_master_id") != actor.id:
                        continue
                    bx, by = other.pos
                    cur_d = max(abs(x - bx), abs(y - by))
                    new_d = max(abs(nx - bx), abs(ny - by))
                    if cur_d <= CHAIN_RANGE:
                        if new_d > CHAIN_RANGE:
                            return
                    else:
                        if new_d > cur_d:
                            return
        except Exception:
            pass

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
        """Resolve an attack. Delegates to combat_system."""
        combat_system.attack(self, level, attacker, defender)




    def _transition_edge(self, actor: Actor, dx: int, dy: int) -> None:
        """Move the player across zone boundaries. Delegates to zones_system."""
        zones_system.transition_edge(self, actor, dx, dy)

    def fast_travel_to_zone(self, zx: int, zy: int) -> None:
        """Fast travel to zone. Delegates to zones_system."""
        zones_system.fast_travel_to_zone(self, zx, zy)

    def _monster_act(self, level: LevelState, id: str) -> None:
        """AI actor turn execution.

        Uses action_runner for unified action handling (cooldowns, delays).
        """
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
            # Use unified action runner for AI actions
            result = action_runner.run_ai_action(
                self,
                actor.id,
                action_name,
                **(params or {}),
            )
            if result.executed:
                delay = result.delay
            else:
                # Action blocked (cooldown, etc.) - fall back to wait
                delay = self.cfg.action_time_fast

        # --- Schedule next turn -----------------------------------------
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

    def act_throw_flask(
        self,
        actor_id: str,
        target_pos: Optional[Tuple[int, int]]
    ) -> None:
        """Throw an energy flask to activate nearby vertices with high damage.

        Args:
            actor_id: ID of the actor throwing the flask (usually player)
            target_pos: (x, y) tile coordinates where the flask lands
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return

        # Validate target position
        if target_pos is None or not level.world.in_bounds(*target_pos):
            self.log.add("Invalid target location.")
            return

        # Find the equipped flask
        from edgecaster.systems.item_grants import find_grant_origin

        inv = self.get_inventory(actor_id)
        flask = find_grant_origin(inv, "throw_flask")

        if flask is None:
            self.log.add("No energy flask equipped.")
            return

        # Get pattern origin and vertices
        origin = self._activation_origin(level)
        if origin is None or not level.pattern.vertices:
            self.log.add("No rune pattern active to energize.")
            self._consume_flask(actor_id, flask)  # Still consume the flask
            return

        # Project vertices into world space
        from edgecaster.patterns.activation import project_vertices
        world_vertices = project_vertices(level.pattern, origin)

        # Find vertices within flask radius
        FLASK_RADIUS = 3.0  # 3-tile radius impact zone
        PER_VERTEX_DAMAGE = 5
        DAMAGE_CAP = 100

        tx, ty = target_pos
        center_x = tx + 0.5  # Tile center
        center_y = ty + 0.5

        active_verts = []
        r2 = FLASK_RADIUS * FLASK_RADIUS

        for vx, vy in world_vertices:
            dx = vx - center_x
            dy = vy - center_y
            if dx*dx + dy*dy <= r2:
                active_verts.append((vx, vy))

        if not active_verts:
            self.log.add("The flask shatters, but no vertices were in range.")
            self._consume_flask(actor_id, flask)
            return

        # Apply damage to nearby enemies
        from edgecaster.patterns.activation import damage_from_vertices

        hit_count = 0
        # Convert to list to avoid "dictionary changed size during iteration"
        for enemy in list(level.actors.values()):
            if not enemy.alive or enemy.id == actor_id:
                continue
            if enemy.faction == "player":
                continue

            # Calculate damage based on vertices near enemy
            dmg = damage_from_vertices(
                active_verts,
                enemy.pos,
                FLASK_RADIUS,
                PER_VERTEX_DAMAGE,
                cap=DAMAGE_CAP,
            )

            if dmg > 0:
                enemy.stats.hp -= dmg
                hit_count += 1
                self.log.add(f"Arcane energy sears {enemy.name} for {dmg} damage!")

                if enemy.stats.hp <= 0:
                    self._kill_actor(level, enemy, killer_id=actor_id)

        # Log result
        if hit_count == 0:
            self.log.add(f"The flask energizes {len(active_verts)} vertices, but no enemies are nearby.")
        else:
            self.log.add(f"Flask impact: {len(active_verts)} vertices activated!")

        # Consume one flask from the stack
        self._consume_flask(actor_id, flask)

    def _consume_flask(self, actor_id: str, flask_item: Any) -> None:
        """Consume one flask from the equipped stack.

        Args:
            actor_id: Owner of the flask
            flask_item: The flask item entity
        """
        from edgecaster.systems.inventory import get_quantity, set_quantity
        from edgecaster.systems import equipment as equipment_system

        qty = get_quantity(flask_item)

        if qty > 1:
            # Reduce stack by 1
            set_quantity(flask_item, qty - 1)
            self.log.add(f"Flask thrown. {qty - 1} remaining.")
        else:
            # Last flask - remove from inventory and unequip
            inv = self.get_inventory(actor_id)
            try:
                inv.remove(flask_item)
            except ValueError:
                pass  # Already removed

            # Unequip if it was equipped
            if equipment_system.is_equipped(flask_item):
                try:
                    self.unequip_item(actor_id, str(flask_item.id))
                except Exception:
                    pass

            self.log.add("Last flask consumed.")

        # Refresh actions (flask action may disappear if stack depleted)
        self.refresh_actor_actions(actor_id)

    def act_push_pattern(self, actor_id: str, target_pos=None, rotation_deg: float = 0) -> None:
        level = self._level()
        pattern_ops.push_pattern(self, level, target_pos, rotation_deg)

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
                    self._kill_actor(level, actor, killer_id=actor_id)

    def act_ignite(self, actor_id: str) -> None:
        """
        Ignite red edges for 30 ticks with decaying direct/indirect damage.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        pattern = getattr(level, "pattern", None)
        if actor is None or pattern is None or not pattern.edges:
            return

        caster_is_player = actor_id == self.player_id

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
                            self._kill_actor(
                                level,
                                obj,
                                killer_id=actor_id,
                                killer_is_player=caster_is_player,
                            )
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

        caster_is_player = actor_id == self.player_id

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
                                self._kill_actor(
                                    level,
                                    target,
                                    killer_id=actor_id,
                                    killer_is_player=caster_is_player,
                                )
                tags = getattr(target, "tags", {}) or {}
                current = float(tags.get("frozen_slow", 1.0))
                if slow_mult > current:
                    tags["frozen_slow"] = slow_mult
                    tags["frozen_slow_timer"] = 0.0
                    target.tags = tags

    def act_corruption_cone(self, actor_id: str) -> None:
        """Create a localized 'cone' of corruption centered on the actor's current position.

        Phase 1: this only affects fractal-derived visuals/biomes, not walkability.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return

        # Ensure we have a stable world->Julia mapping.
        try:
            self._ensure_overmap_ready()
        except Exception:
            pass
        if getattr(self, "tile_julia_grid", None) is None:
            try:
                self.build_tile_julia_grid()
            except Exception:
                return
        if getattr(self, "tile_julia_grid", None) is None:
            return

        zx, zy, depth = self.zone_coord
        if depth != 0:
            # Only overworld zones participate in the overmap-Julia correspondence right now.
            if actor_id == self.player_id:
                self.log.add("The seals resist corruption beneath the surface.")
            return

        wx = zx * self.cfg.world_width + int(actor.pos[0])
        wy = zy * self.cfg.world_height + int(actor.pos[1])

        try:
            jx = float(self.tile_julia_grid["x"][wx])  # type: ignore[index]
            jy = float(self.tile_julia_grid["y"][wy])  # type: ignore[index]
        except Exception:
            return

        # Julia-plane units: sigma is measured in the same coordinate system as (jx, jy).
        # A useful conversion for humans is "sigma in tiles", using the world->Julia step size.
        mean_step = None
        try:
            step_x = float(self.tile_julia_grid.get("step_x", 0.0))  # type: ignore[union-attr]
            step_y = float(self.tile_julia_grid.get("step_y", 0.0))  # type: ignore[union-attr]
            if abs(step_x) > 1e-12 and abs(step_y) > 1e-12:
                mean_step = 0.5 * (abs(step_x) + abs(step_y))
        except Exception:
            mean_step = None

        def sigma_from_tiles(sigma_tiles: float) -> float:
            step = mean_step or 0.01
            return max(0.01, float(sigma_tiles) * step)

        # NPC/AI callers: fall back to gear params.
        if actor_id != self.player_id:
            height = float(self.get_param_value("corruption_cone", "height"))
            slope = float(self.get_param_value("corruption_cone", "slope"))
            sigma = max(0.01, slope)
            self.add_corruption_hotspot(jx, jy, strength=height, sigma=sigma)
            return

        # Player: prompt for a very visible, high-impact cone.
        strength_choices: list[tuple[str, Optional[float]]] = [
            ("Whisper (strength 3)", 3.0),
            ("Ritual (strength 8)", 8.0),
            ("Cataclysm (strength 18)", 18.0),
            ("Cancel", None),
        ]

        def after_strength(idx: int, game: "Game") -> None:
            chosen = strength_choices[idx][1] if 0 <= idx < len(strength_choices) else None
            if chosen is None:
                game.log.add("You let the land remain unwarped, for now.")
                return

            sigma_tiles_choices: list[tuple[str, Optional[float]]] = [
                ("Steep (sigma ~25 tiles)", 25.0),
                ("Medium (sigma ~60 tiles)", 60.0),
                ("Wide (sigma ~140 tiles)", 140.0),
                ("Apocalyptic (sigma ~260 tiles)", 260.0),
                ("Cancel", None),
            ]

            def after_sigma(idx2: int, game2: "Game") -> None:
                sigma_tiles = sigma_tiles_choices[idx2][1] if 0 <= idx2 < len(sigma_tiles_choices) else None
                if sigma_tiles is None:
                    game2.log.add("The cone collapses back into possibility.")
                    return

                sigma = sigma_from_tiles(sigma_tiles)
                game2.add_corruption_hotspot(jx, jy, strength=float(chosen), sigma=float(sigma))

                approx_tiles = sigma_tiles
                if mean_step:
                    approx_tiles = sigma / mean_step

                game2.log.add("You twist the land into a cone of corruption.")
                game2.log.add(
                    f"(Slope uses sigma in Julia-plane units; here sigma={sigma:.3f} (~{approx_tiles:.0f} tiles).)"
                )

            slope_body = (
                "Choose the slope/spread of the cone.\n\n"
                "Notes:\n"
                "- 'Slope' is represented as Gaussian sigma.\n"
                "- Smaller sigma = steeper (sharper) cone.\n"
                "- Sigma is measured in Julia-plane units; the options below are expressed in tiles.\n"
            )
            game.set_urgent(
                slope_body,
                title="Corruption Cone: Slope",
                choices=[t for t, _v in sigma_tiles_choices],
                on_choice_effect=after_sigma,
            )

        strength_body = (
            "Choose the strength (height) of the cone.\n\n"
            "This will visibly warp the world map and any overworld zones you have already visited."
        )
        self.set_urgent(
            strength_body,
            title="Corruption Cone: Strength",
            choices=[t for t, _v in strength_choices],
            on_choice_effect=after_strength,
        )

    def act_place_rune_anchor(self, actor_id: str) -> None:
        """Place a rune anchor at the actor's current overworld position.

        Rune anchors suppress *all* corruption contributions locally (mountains/spots/hotspots),
        because they scale the effective corruption_level at z before any distortion math runs.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return

        # Ensure we have a stable world->Julia mapping.
        try:
            self._ensure_overmap_ready()
        except Exception:
            pass
        if getattr(self, "tile_julia_grid", None) is None:
            try:
                self.build_tile_julia_grid()
            except Exception:
                return
        if getattr(self, "tile_julia_grid", None) is None:
            return

        zx, zy, depth = self.zone_coord
        if depth != 0:
            if actor_id == self.player_id:
                self.log.add("Your anchor finds no purchase beneath the surface.")
            return

        wx = zx * self.cfg.world_width + int(actor.pos[0])
        wy = zy * self.cfg.world_height + int(actor.pos[1])

        try:
            jx = float(self.tile_julia_grid["x"][wx])  # type: ignore[index]
            jy = float(self.tile_julia_grid["y"][wy])  # type: ignore[index]
        except Exception:
            return

        mean_step = None
        try:
            step_x = float(self.tile_julia_grid.get("step_x", 0.0))  # type: ignore[union-attr]
            step_y = float(self.tile_julia_grid.get("step_y", 0.0))  # type: ignore[union-attr]
            if abs(step_x) > 1e-12 and abs(step_y) > 1e-12:
                mean_step = 0.5 * (abs(step_x) + abs(step_y))
        except Exception:
            mean_step = None

        range_tiles = float(self.get_param_value("place_rune_anchor", "range"))
        strength = float(self.get_param_value("place_rune_anchor", "strength"))
        step = mean_step or 0.01
        sigma = max(0.01, range_tiles * step)

        pid = self.add_corruption_anchor(
            jx,
            jy,
            sigma=sigma,
            strength=strength,
            coord=(zx, zy, 0),
            spawn_pos=tuple(actor.pos),
        )

        if actor_id == self.player_id:
            approx_tiles = range_tiles
            if mean_step and mean_step > 1e-9:
                approx_tiles = sigma / mean_step
            self.log.add("You drive a rune anchor into the land.")
            self.log.add(f"(sigma={sigma:.3f} ~ {approx_tiles:.0f} tiles; strength={strength:.2f})")
            if pid:
                self.log.add(f"Anchor marked on the world map as {pid}.")

    def act_seal_rune(self, actor_id: str) -> None:
        """Bind a sealing rune using a coherence crystal (trial zones only)."""
        try:
            from edgecaster.systems import seal_trials
            seal_trials.seal_rune(self, actor_id)
        except Exception:
            self.log.add("The seal refuses to bind.")

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

    def act_polygon(self, actor_id: str) -> None:
        """Place a regular polygon pattern centered on the player.

        Clears any existing pattern and creates a new polygon with the
        configured number of sides and radius. The root/terminus vertex
        is directly north of center, and vertices proceed clockwise.
        """
        level = self._level()
        player = self._player()

        # Get parameters from the param system
        num_sides = self._param_value("polygon", "sides")
        radius = self._param_value("polygon", "radius")

        # Default fallbacks if params not found
        if num_sides is None:
            num_sides = 6
        if radius is None:
            radius = 4

        # Create the polygon pattern and anchor it on the player
        level.pattern = builder.regular_polygon_pattern(num_sides, radius)
        level.pattern_anchor = player.pos
        level.pattern_motion = None
        level.activation_points = []
        level.activation_ttl = 0
        level.acidic_pattern = False  # Clear corrosive melt on new pattern
        # Clear fern growth state on new pattern
        level.fern_active = False
        level.fern_growth_tips = []
        level.fern_accum = 0.0

        self.log.add(f"Polygon ({num_sides} sides, radius {radius}) placed.")

    def act_star(self, actor_id: str) -> None:
        """Place a star pattern centered on the player.

        Clears any existing pattern and creates a new star with the
        configured number of points, outer radius, and inner radius.
        The first point (root/terminus) is directly north.
        """
        level = self._level()
        player = self._player()

        # Get parameters from the param system
        num_points = self._param_value("star", "points")
        outer_radius = self._param_value("star", "outer_radius")
        inner_radius = self._param_value("star", "inner_radius")

        # Default fallbacks if params not found
        if num_points is None:
            num_points = 5
        if outer_radius is None:
            outer_radius = 5
        if inner_radius is None:
            inner_radius = 2

        # Create the star pattern and anchor it on the player
        level.pattern = builder.star_pattern(num_points, outer_radius, inner_radius)
        level.pattern_anchor = player.pos
        level.pattern_motion = None
        level.activation_points = []
        level.activation_ttl = 0
        level.acidic_pattern = False  # Clear corrosive melt on new pattern
        # Clear fern growth state on new pattern
        level.fern_active = False
        level.fern_growth_tips = []
        level.fern_accum = 0.0

        self.log.add(f"Star ({num_points} points, outer {outer_radius}, inner {inner_radius}) placed.")

    # --- chakra modifiers / charge helpers ---

    def _chakra_modifiers(self, actor_id: str):
        """Return ChakraModifiers for the given actor (resonance + charge)."""
        try:
            actor = self._level().actors.get(actor_id)
        except Exception:
            actor = None
        if actor is None:
            return None

        chakra_state = getattr(actor, "chakra_state", None)
        if chakra_state is None:
            return None

        try:
            from edgecaster.prototypes import resolve_body_schema
            from edgecaster.systems import chakras as chakra_system
        except Exception:
            return None

        body_schema = resolve_body_schema(actor) or {}
        bonuses = chakra_system.check_resonance_bonuses(body_schema, chakra_state)
        mods = chakra_system.get_resonance_modifiers(bonuses)
        avg_charge = chakra_system.get_average_charge(chakra_state)
        mods = chakra_system.apply_charge_to_modifiers(mods, avg_charge)
        return mods

    def _consume_chakra_charge(self, actor_id: str, amount: float) -> None:
        """Consume chakra charge from the actor's active chakras."""
        if amount <= 0:
            return
        try:
            actor = self._level().actors.get(actor_id)
        except Exception:
            actor = None
        if actor is None:
            return
        chakra_state = getattr(actor, "chakra_state", None)
        if chakra_state is None:
            return
        try:
            from edgecaster.systems import chakras as chakra_system
        except Exception:
            return
        chakra_system.consume_chakra_charge(chakra_state, amount)

    def act_chakra(self, actor_id: str) -> None:
        """Apply chakra pattern as a fractal generator to existing edges.

        Each active chakra becomes a vertex positioned according to body
        geometry. This chakra shape is then used to transform each edge
        in the current pattern, just like Koch, Branch, or Custom generators.
        """
        level = self._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return

        # Need an existing pattern to transform
        if not level.pattern.vertices:
            self.log.add("No pattern to modify. Place a terminus first.")
            return

        # Get chakra state
        chakra_state = getattr(actor, "chakra_state", None)
        if chakra_state is None:
            self.log.add("No chakra state found.")
            return

        # Get body schema
        try:
            from edgecaster.prototypes import resolve_body_schema
            from edgecaster.systems.chakras import chakras_to_seed_pattern, get_chakra_world_positions
            body_schema = resolve_body_schema(actor)
        except Exception:
            self.log.add("Could not resolve body schema.")
            return

        if not body_schema or not body_schema.get("nodes"):
            self.log.add("No body schema to generate pattern from.")
            return

        # Generate the chakra seed pattern
        try:
            chakra_pattern = chakras_to_seed_pattern(body_schema, chakra_state, base_scale=1.0)
        except Exception as e:
            self.log.add(f"Chakra pattern generation failed: {e}")
            return

        if not chakra_pattern.vertices or len(chakra_pattern.vertices) < 2:
            self.log.add("Need at least 2 active chakras to form a generator.")
            return

        # Extract vertices as (x, y) tuples
        verts = [(v.pos[0], v.pos[1]) for v in chakra_pattern.vertices]

        # Extract edges as (a, b) index tuples
        edges = []
        if chakra_pattern.edges:
            edges = [(e.a, e.b) for e in chakra_pattern.edges]

        # Drop orphan vertices (not referenced by any edge). These can
        # otherwise show up as stray points when applying the generator.
        if edges:
            used = set()
            for a, b in edges:
                used.add(a)
                used.add(b)
            if len(used) >= 2:
                # Preserve original vertex order for stability
                used_list = [i for i in range(len(verts)) if i in used]
                old_to_new = {old: new for new, old in enumerate(used_list)}
                verts = [verts[i] for i in used_list]
                edges = [(old_to_new[a], old_to_new[b]) for a, b in edges if a in old_to_new and b in old_to_new]

        if len(verts) < 2 or not edges:
            self.log.add("Need at least 2 connected chakras to form a generator.")
            return

        # Drop degenerate edges where endpoints collapse to the same spot.
        # These create tiny "orphan dots" when the generator is applied.
        def edge_len_sq(a_idx: int, b_idx: int) -> float:
            ax, ay = verts[a_idx]
            bx, by = verts[b_idx]
            dx = ax - bx
            dy = ay - by
            return dx * dx + dy * dy

        eps_sq = 1e-8
        edges = [(a, b) for a, b in edges if edge_len_sq(a, b) > eps_sq]

        if edges:
            used = set()
            for a, b in edges:
                used.add(a)
                used.add(b)
            if len(used) >= 2:
                used_list = [i for i in range(len(verts)) if i in used]
                old_to_new = {old: new for new, old in enumerate(used_list)}
                verts = [verts[i] for i in used_list]
                edges = [(old_to_new[a], old_to_new[b]) for a, b in edges if a in old_to_new and b in old_to_new]
        if len(verts) < 2 or not edges:
            self.log.add("Need at least 2 connected chakras to form a generator.")
            return

        # Reorder vertices so root is first and furthest point is last.
        # The CustomGraphGenerator uses vertices[0] and vertices[-1] as the
        # baseline for scaling - if these aren't the pattern endpoints,
        # the pattern will explode in size after iterations.
        #
        # Root is typically the "body" node; fall back to closest-to-origin.
        root_hint = None
        try:
            root_id = body_schema.get("root")
            if root_id:
                positions = get_chakra_world_positions(body_schema, chakra_state, base_scale=1.0)
                root_hint = positions.get(root_id)
        except Exception:
            root_hint = None

        def dist_sq(p: tuple) -> float:
            return p[0] ** 2 + p[1] ** 2
        if root_hint is not None:
            rx, ry = root_hint
            def dist_to_hint(idx: int) -> float:
                dx = verts[idx][0] - rx
                dy = verts[idx][1] - ry
                return dx * dx + dy * dy
            root_idx = min(range(len(verts)), key=dist_to_hint)
        else:
            root_idx = min(range(len(verts)), key=lambda i: dist_sq(verts[i]))

        # Find furthest vertex from root
        root_pos = verts[root_idx]

        def dist_from_root(p: tuple) -> float:
            dx = p[0] - root_pos[0]
            dy = p[1] - root_pos[1]
            return dx * dx + dy * dy

        furthest_idx = max(range(len(verts)), key=lambda i: dist_from_root(verts[i]))

        # Normalize the chakra shape so the baseline lies on +X axis.
        # This prevents unintended rotation and keeps the terminus aligned
        # with the existing pattern's segment endpoints.
        rx, ry = root_pos
        tx, ty = verts[furthest_idx]
        bx = tx - rx
        by = ty - ry
        base_len = math.hypot(bx, by)
        if base_len > 1e-6:
            ang = math.atan2(by, bx)
            cos_a = math.cos(-ang)
            sin_a = math.sin(-ang)

            norm_verts = []
            for vx, vy in verts:
                # Translate so root is origin
                dx = vx - rx
                dy = vy - ry
                # Rotate so baseline aligns to +X
                nx = dx * cos_a - dy * sin_a
                ny = dx * sin_a + dy * cos_a
                norm_verts.append((nx, ny))
            verts = norm_verts

            # Update root/terminus indices after normalization
            root_idx = 0
            # Terminus is now the furthest point along +X
            furthest_idx = max(range(len(verts)), key=lambda i: verts[i][0])

        # Build mapping from old indices to new indices
        # New order: root first, furthest last, everything else in between
        old_to_new = {}
        new_verts = []

        # Add root first
        old_to_new[root_idx] = 0
        new_verts.append(verts[root_idx])

        # Add all middle vertices
        for i, v in enumerate(verts):
            if i != root_idx and i != furthest_idx:
                old_to_new[i] = len(new_verts)
                new_verts.append(v)

        # Add furthest last
        old_to_new[furthest_idx] = len(new_verts)
        new_verts.append(verts[furthest_idx])

        # Remap edge indices
        new_edges = [(old_to_new[a], old_to_new[b]) for a, b in edges]

        verts = new_verts
        edges = new_edges

        # Get amplitude from param system (like custom generator)
        amp = self._param_value("chakra", "amplitude")
        if amp is None:
            amp = 1.0
        # Apply chakra resonance/charge amp multiplier if available.
        mods = self._chakra_modifiers(actor_id)
        if mods is not None:
            amp *= mods.chakra_amp_mult

        # Create a CustomGraphGenerator with the chakra shape
        gen = builder.CustomGraphGenerator(verts, edges, amplitude=amp)

        # Apply to current pattern (same flow as _apply_fractal_op)
        segs = level.pattern.to_segments()
        level.pattern_motion = None  # Cancel any ongoing motion

        segs = gen.apply_segments(segs, max_segments=self.cfg.max_vertices)
        segs = builder.cleanup_duplicates(segs)
        if len(segs) > self.cfg.max_vertices:
            segs = segs[: self.cfg.max_vertices]
            self.log.add("Pattern capped at max vertices.")

        level.pattern = builder.Pattern.from_segments(segs)
        self.log.add(f"Chakra generator applied ({len(verts)} chakra vertices).")

        # Spend a bit of chakra charge when applying the generator.
        if mods is not None:
            try:
                from edgecaster.systems import chakras as chakra_system
                self._consume_chakra_charge(
                    actor_id,
                    chakra_system.CHARGE_CONSUME_GENERATOR * mods.charge_consume_mult,
                )
            except Exception:
                pass

    def act_corrosive_melt(self, actor_id: str) -> None:
        """Activate acidic mode on the current pattern.

        When active, edges that touch enemy tiles dissolve and deal damage
        based on their green intensity. Lasts until pattern reset.
        """
        level = self._level()
        player = self._player()

        # Check if already acidic
        if level.acidic_pattern:
            self.log.add("Pattern is already acidic.")
            return

        # Check if there's a pattern to make acidic
        if not level.pattern.vertices:
            self.log.add("No pattern to corrode. Place a terminus first.")
            return

        # Get mana cost from params
        mana_cost = self._param_value("corrosive_melt", "mana_cost")
        if mana_cost is None:
            mana_cost = 30

        # Check mana
        if player.stats.mana < mana_cost:
            self.log.add(f"Not enough mana ({int(player.stats.mana)}/{mana_cost}).")
            return

        # Spend mana and activate
        player.stats.mana -= mana_cost
        player.stats.clamp()
        level.acidic_pattern = True

        self.log.add("Pattern becomes acidic! Edges will dissolve on enemy contact.")

    def act_start_fern(self, actor_id: str) -> None:
        """Toggle Barnsley fern auto-growth on the current pattern.

        When active, the fern grows as a connected tree using Barnsley affine
        transforms. Growth consumes coherence and oldest vertices are pruned
        when over capacity.
        """
        from edgecaster.systems import fern_growth

        level = self._level()

        # Check if there's a pattern anchor
        if not level.pattern_anchor:
            self.log.add("Need a pattern anchor to grow the fern from.")
            return

        # Toggle fern growth
        if level.fern_active:
            level.fern_active = False
            level.fern_accum = 0.0
            # Reset fern state for next activation
            fern_growth._reset_fern_state(level)
            if hasattr(level, "_fern_node_to_vertex"):
                del level._fern_node_to_vertex
            self.log.add("Fern growth stopped.")
            return

        # Activate fern growth
        level.fern_active = True
        level.fern_accum = 0.0
        # Reset fern state to start fresh
        fern_growth._reset_fern_state(level)
        if hasattr(level, "_fern_node_to_vertex"):
            del level._fern_node_to_vertex
        self.log.add("Fern begins to grow...")

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
        pattern_ops.reset_pattern(self)


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

        # Apply chakra resonance/charge modifiers (player only for now).
        mods = self._chakra_modifiers(self.player_id)
        if mods is not None:
            dmg_radius = max(0.1, float(dmg_radius) + float(mods.radius_bonus))
            per_vertex = int(math.ceil(float(per_vertex) * float(mods.damage_mult)))

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
        if mods is not None:
            mana_cost = int(math.ceil(float(mana_cost) * float(mods.mana_cost_mult)))
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
                self._kill_actor(level, actor, killer_id=self.player_id, killer_is_player=True)

        if hits == 0:
            self.log.add("Your rune fizzles; no foes in its reach.")

        # Spend chakra charge on activation.
        if mods is not None:
            try:
                from edgecaster.systems import chakras as chakra_system
                self._consume_chakra_charge(
                    self.player_id,
                    chakra_system.CHARGE_CONSUME_ACTIVATE * mods.charge_consume_mult,
                )
            except Exception:
                pass

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
        mods = self._chakra_modifiers(self.player_id)
        if mods is not None:
            depth = int(depth) + int(mods.neighbor_depth_bonus)
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
        if mods is not None:
            mana_cost = int(math.ceil(float(mana_cost) * float(mods.mana_cost_mult)))
        if player.stats.mana < mana_cost:
            self.log.add(f"Not enough mana ({player.stats.mana}/{mana_cost}).")
            return
        player.stats.mana -= mana_cost
        player.stats.clamp()

        per_vertex = self._param_value("activate_seed", "damage")
        if mods is not None:
            per_vertex = int(math.ceil(float(per_vertex) * float(mods.damage_mult)))
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
                    self._kill_actor(level, target_actor, killer_id=self.player_id, killer_is_player=True)
        if hits == 0:
            self.log.add("Your focus fizzles; no foes in reach.")

        if mods is not None:
            try:
                from edgecaster.systems import chakras as chakra_system
                self._consume_chakra_charge(
                    self.player_id,
                    chakra_system.CHARGE_CONSUME_ACTIVATE * mods.charge_consume_mult,
                )
            except Exception:
                pass

    # --- FOV ---

    def _on_enemy_killed(self, enemy: Actor) -> None:
        if enemy.faction != "hostile":
            return
        if enemy.tags.get("_xp_awarded"):
            return
        enemy.tags["_xp_awarded"] = 1
        xp_gain = enemy.tags.get("xp", self.cfg.xp_per_imp) if enemy.tags else self.cfg.xp_per_imp
        self._grant_xp(xp_gain)

    def _spawn_legendary_reward(self, level: LevelState, pos: Tuple[int, int], actor: Actor) -> None:
        """Drop a guaranteed reward when a legendary creature dies.

        This is intentionally centralized here (not in the renderer or POI code)
        so we can later add other special kill rewards without scattering logic.
        """
        tags = getattr(actor, "tags", {}) or {}
        if not tags.get("legendary"):
            return

        legendary_id = tags.get("legendary_id")
        if legendary_id:
            try:
                reg = getattr(self, "legendary_registry", None)
                if isinstance(reg, dict):
                    rec = reg.get(str(legendary_id))
                    if isinstance(rec, dict):
                        if rec.get("reward_spawned"):
                            return
                        rec["reward_spawned"] = True
            except Exception:
                pass

        world = level.world
        px, py = int(pos[0]), int(pos[1])
        reward_pos: Optional[Tuple[int, int]] = None
        for r in range(0, 6):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    x = px + dx
                    y = py + dy
                    if not world.in_bounds(x, y):
                        continue
                    if not world.is_walkable(x, y):
                        continue
                    if self._actor_at(level, (x, y)):
                        continue
                    if self._entity_at(level, (x, y)):
                        continue
                    reward_pos = (x, y)
                    break
                if reward_pos is not None:
                    break
            if reward_pos is not None:
                break
        if reward_pos is None:
            return

        roll = float(self.rng.random()) if getattr(self, "rng", None) else 0.0

        # Reward pool: bismuth, a wand, or a stat-boosting item.
        if roll < 0.40:
            try:
                amt = int(self.rng.randint(40, 140))
            except Exception:
                amt = 60
            ent = self._spawn_entity_from_template(
                "bismuth_pile",
                reward_pos,
                overrides={"tags": {"amount": amt}},
            )
            level.entities[ent.id] = ent
            self.log.add(f"A bismuth hoard spills out ({amt}).")
            return

        if roll < 0.70:
            wand_id = self.rng.choice(["wand_koch", "wand_branch", "wand_zigzag", "wand_activate_n", "wand_sparkle"])
            ent = self._spawn_entity_from_template(wand_id, reward_pos)
            level.entities[ent.id] = ent
            self.log.add(f"You find a {ent.name}.")
            return

        item_id = self.rng.choice(["resonant_ring", "sage_cap", "fleet_boots", "vital_belt"])
        ent = self._spawn_entity_from_template(item_id, reward_pos)
        level.entities[ent.id] = ent
        self.log.add(f"You find a {ent.name}.")

    def _kill_actor(
        self,
        level: LevelState,
        actor: Actor,
        *,
        killer_id: Optional[str] = None,
        killer_is_player: bool = False,
    ) -> None:
        """Handle removing a dead actor. Delegates to combat_system."""
        combat_system.kill_actor(
            self,
            level,
            actor,
            killer_id=killer_id,
            killer_is_player=killer_is_player,
        )


    def _update_fov(self, level: LevelState, radius: int = 8) -> None:
        if self.player_id not in level.actors:
            return

        # Apply view bonus from equipment
        view_bonus = self.effective_character_stats().get("view", 0)
        radius = radius + view_bonus

        px, py = level.actors[self.player_id].pos
        level.world.clear_visibility()

        # God Vision mode: reveal entire map, no FOV restrictions
        if getattr(self, "god_vision", False):
            for y in range(level.world.height):
                for x in range(level.world.width):
                    tile = level.world.get_tile(x, y)
                    if tile:
                        tile.visible = True
                        tile.explored = True
                    actor = self._actor_at(level, (x, y))
                    if actor and actor.id not in level.spotted:
                        level.spotted.add(actor.id)
            # Apply lighting after visibility changes (consistent behavior)
            from edgecaster.systems import lighting
            lighting.update_level_lighting(self, level, (px, py))
            level.need_fov = False
            return

        # Build set of opaque positions from entities (walls, closed doors, etc.)
        opaque: set[Tuple[int, int]] = set()
        for ent in level.entities.values():
            if getattr(ent, "blocks_vision", False):
                opaque.add(ent.pos)



        # Normal FOV calculation
        r2 = radius * radius
        for y in range(py - radius, py + radius + 1):
            for x in range(px - radius, px + radius + 1):
                if not level.world.in_bounds(x, y):
                    continue
                dx = x - px
                dy = y - py
                if dx * dx + dy * dy > r2:
                    continue

                if _los(level.world, (px, py), (x, y), opaque=opaque):
                    tile = level.world.get_tile(x, y)
                    if tile:
                        tile.visible = True
                        tile.explored = True
                    actor = self._actor_at(level, (x, y))
                    if actor and actor.id not in level.spotted:
                        level.spotted.add(actor.id)
                        if actor.id != self.player_id:
                            self.log.add(f"You spot a {actor.name}.")

        # Apply lighting from light-emitting entities (e.g., dropped Glowing Band)
        #from edgecaster.systems import lighting
        #lighting.update_level_lighting(self, level, (px, py))

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
