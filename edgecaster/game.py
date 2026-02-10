from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Dict, Tuple, List, Optional, Callable
from pathlib import Path
from collections import deque

from edgecaster.math_utils import smoothstep_range

# =============================================================================
# YOGA REFACTOR NOTES (see vision_documents/the_yoga.txt)
# =============================================================================
#
# STAGE 1 (Active): Coordinate Authority Unification
#
# Current Status:
#   - abs_pos is defined on Entity but not consistently populated on spawn
#   - abs_from_zone_local / zone_local_from_abs helpers exist and are used
#   - _pattern_state_by_depth provides canonical ABS pattern storage
#   - Player movement uses _move_player_to_abs() which is yoga-compliant
#
# TODO (YOGA):
#   [1] Entity abs_pos population: DONE.
#       - âœ“ spawning_system.spawn_enemies() now sets abs_pos on each spawn
#       - âœ“ spawning_system.spawn_entity_from_template() sets abs_pos
#       - âœ“ spawn_imps_near, spawn_echoes_near, spawn_enemies_for_biome set abs_pos
#       - âœ“ _spawn_poi_contents() fixed: abs_pos for all actor spawns (bug: was using wrong var)
#
#   [2] LevelState.entities: Still stores entities by zone-local membership.
#       - Eventually should be an index/view, not authoritative storage
#       - Migration path: WorldEntityIndex is the precursor
#
#   [3] Targeting: DONE - target_cursor_abs is now canonical.
#       - DungeonScene._set_cursor_abs() sets ABS first, derives local
#       - set_target_cursor() stub removed (was never called)
#
#   [4] Pattern anchor: _pattern_state stores anchor_abs, but many code
#       paths still read level.pattern_anchor (zone-local).
#       - Audit callers of level.pattern_anchor and migrate to game.pattern_anchor_abs()
#
# STAGE 2 (Next): Centralize Coordinate Transforms
#   - abs_from_zone_local / zone_local_from_abs exist but are on Game
#   - Should be accessible from renderer without full game reference
#   - Consider creating camera.py or coords.py helper module
#
# =============================================================================

@dataclass
class RenderProxy:
    """Lightweight wrapper for rendering objects that may live in other zones.

    The renderer can treat this as 'an entity' but it also carries absolute-world
    coordinates so we can map it into the current camera frame.
    """
    obj: object
    abs_x: float
    abs_y: float
    zone_coord: Tuple[int, int, int]
    local_pos: Tuple[int, int]







# =============================================================================
# ATTENTION-CELL ENTITY STORE (Route 2: deprecate rectangular zones)
# =============================================================================

from edgecaster import config
from edgecaster.state.world import World
from edgecaster.state.actors import Actor, Stats, Human
from edgecaster.state.entities import Entity
from edgecaster.enemies import factory as enemy_factory
from edgecaster.systems.world_entity_index import WorldEntityIndex
from edgecaster.systems import aggregate_resolution as aggregate_system

from edgecaster import prototypes
from edgecaster import spawn_factory

from edgecaster import mapgen
from edgecaster import mapgen_sites
from edgecaster.content.pois import get_poi_registry
from edgecaster.systems.poi_registry import POIRegistry
from edgecaster.systems import poi_worldgen
from edgecaster.state.pois import ABSRect, POISpec
from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns import builder
from edgecaster.character import Character, default_character
from edgecaster.content import npcs
from edgecaster.systems import equipment as equipment_system
from edgecaster.systems import item_grants
from edgecaster.systems import ai
from edgecaster.systems.params import ParamManager
from edgecaster.systems import legendaries as legendaries_system
from edgecaster.systems import lorenz_aura
from edgecaster.systems import action_runner
from edgecaster.systems import spawning as spawning_system
from edgecaster.systems import inventory as inventory_system
from edgecaster.systems import inspection as inspection_system
from edgecaster.systems import pattern_ops
from edgecaster.systems import scheduling
from edgecaster.systems import combat as combat_system
from edgecaster.systems import coords as coords_system
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import render_query as render_query_system
from edgecaster.systems import attention as attention_system
from edgecaster.systems import poi_spawning as poi_spawning_system
from edgecaster.systems import zones as zones_system
from edgecaster.systems import overmap as overmap_system
from edgecaster.systems import difficulty as difficulty_system
from edgecaster.systems import ambient_spawns as ambient_spawns_system
from edgecaster.systems import damage_policy as damage_policy_system
from edgecaster.systems import combat_actions as combat_actions_system
from edgecaster.systems import pattern_runtime as pattern_runtime_system
from edgecaster.systems import blade_runtime as blade_runtime_system
from edgecaster.systems import chakras as chakras_system
from edgecaster.systems import chakra_effects as chakra_effects_system
from edgecaster.systems import chakra_items as chakra_items_system
from edgecaster.systems import perf_profiler
from edgecaster.systems import telemetry as telemetry_system
from . import lorenz
import math
import random


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
    # Choking Vines runtime state (ABS-space tendril segments + tips).
    choking_vines_state: Optional[Dict[str, Any]] = None
    # Rune-mutating choking vines runtime state (branches that become real edges).
    rune_choking_vines_state: Optional[Dict[str, Any]] = None
    # Visible thrown-knife projectiles (ABS-space center positions + rune-shape payload).
    thrown_knives_state: List[Dict[str, Any]] = field(default_factory=list)
    seal_trial: Optional["SealTrialState"] = None  # Sealing rune trial state (if any)
    # Zone difficulty metadata (computed on zone creation).
    danger_value: float = 0.0
    danger_tier: int = 1
    danger_sources: Dict[str, float] = field(default_factory=dict)
    # Active deferred (telegraphed) actions pending resolution.
    deferred_actions: List[Any] = field(default_factory=list)
    # Accumulator for ambient hostile top-up timing (Option 2 roaming spawns).
    ambient_spawn_accum: float = 0.0



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
        self._perf_profiler = perf_profiler.PerfProfiler(
            enabled=bool(getattr(self.cfg, "perf_profiler_enabled", True)),
            flush_seconds=float(getattr(self.cfg, "perf_profiler_flush_seconds", 2.0)),
            top_n=int(getattr(self.cfg, "perf_profiler_top_n", 8)),
            min_avg_ms=float(getattr(self.cfg, "perf_profiler_min_avg_ms", 0.05)),
        )
        self.telemetry_log_path = "C:\\Games\\Edgecaster\\telemetry.ndjson"
        self._telemetry = telemetry_system.TelemetryLogger.from_path(
            self.telemetry_log_path,
            enabled=True,
        )
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
        # Progression points (1 per level).
        # Even levels spend by player choice; odd levels auto-random.
        if getattr(self.character, "advancement_points", None) is None:
            self.character.advancement_points = 0
        try:
            self.character.advancement_points = int(self.character.advancement_points)
        except Exception:
            self.character.advancement_points = 0
        self._telemetry_emit(
            "session_start",
            seed=(getattr(self.character, "seed", None) or getattr(self.cfg, "seed", None)),
            class_name=getattr(self.character, "player_class", None),
            species=getattr(self.character, "species", None),
        )

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
        self.blade_editor_requested = False
        self.fractal_editor_state = None
        self.camera_needs_recenter = False  # Set by zone transitions to signal camera update


        # debug flags
        self.debug_no_fog: bool = False
        self.debug_spawn_inventories: bool = False
        # Active-zone radius for seamless adjacency (zones are caches, not walls).
        # Radius=1 means a 3x3 window around the player is live.
        self.active_zone_radius: int = 1
        # Zone prewarm queue: we avoid creating every neighbor zone in one frame.
        # Instead we incrementally pre-create nearby zones over subsequent ticks.
        self.zone_prewarm_budget_per_advance: int = 1
        self._zone_prewarm_queue: List[Tuple[int, int, int]] = []
        self._zone_prewarm_set: set[Tuple[int, int, int]] = set()
        # Direction hint (dx, dy in zone-space) used to prioritize forward neighbors.
        self._zone_prewarm_dir_hint: Optional[Tuple[int, int]] = None

        # zones keyed by (x, y, depth)
        self.levels: Dict[Tuple[int, int, int], LevelState] = {}
        # Attention-staged entities (Route 2: no rectangular zones as ontology)
        self.attn_store: attention_system.AttentionCellStore = attention_system.AttentionCellStore(bin_size=int(getattr(cfg, 'attn_bin_size', 32) or 32))
        # Track which child entities are active per aggregate (agg_id -> {slot:int -> eid:str})
        self._attn_active_agg_children: Dict[str, Dict[int, str]] = {}
        # Track which staged structure tiles are active per POI/site (parent_id -> set[eid])
        self._attn_active_struct_children: Dict[str, set[str]] = {}
        # start roughly at world center so Julia coords near (0,0)
        center_zx = self.cfg.world_map_screens // 2
        center_zy = self.cfg.world_map_screens // 2
        self.zone_coord: Tuple[int, int, int] = (center_zx, center_zy, 0)
        self._next_id = 0
        # initialize overmap parameters/grid eagerly (fixed bounds) and kick off async render
        self._init_overmap_params_and_grid()

        # ---------------------------------------------------------------------
        # Canonical rune pattern state (ABS-space, per-depth).
        # Zones/LevelStates are just caches; the pattern should not be.
        # ---------------------------------------------------------------------
        self._pattern_state_by_depth: dict[int, dict] = {}


        # Site registry for biome-based POI placement.
        # Populated after overmap_params/tile_julia_grid are set up.
        from edgecaster.systems.sites import SiteRegistry
        from edgecaster.systems.site_placement import place_all_sites
        self.site_registry: SiteRegistry = place_all_sites(self)
        # World-level entity index (macro-scale renderables).
        # Populated from site_registry once placement completes.
        zone_w_init = int(getattr(getattr(self, "cfg", None), "world_width", 60) or 60)
        zone_h_init = int(getattr(getattr(self, "cfg", None), "world_height", 40) or 40)
        self.world_entity_index: WorldEntityIndex = WorldEntityIndex(zone_w=zone_w_init, zone_h=zone_h_init)
        self._world_entity_index_wh = (zone_w_init, zone_h_init)  # Prevent recreation later
        self._world_entities_built: bool = False

        # POI registry for yoga-compliant POI management.
        # Supports multi-zone POIs, nesting, and ABS footprints.
        self.poi_registry: POIRegistry = get_poi_registry(zone_w=zone_w_init, zone_h=zone_h_init)

        # Create world entities for all POI contents (NPCs, structures, walls).
        # This makes POIs visible when zooming around in God Vision, same as berries.
        try:
            poi_worldgen.ensure_all_poi_world_entities(
                self, zone_w=zone_w_init, zone_h=zone_h_init
            )
        except Exception:
            pass

        # Difficulty field configuration (tunable, decoupled from biomes).
        # Adjust this in one place to change zone difficulty behavior.
        self.difficulty_config = difficulty_system.DifficultyConfig()
        # Optional per-zone overrides (quest/scripts can set these).
        self.zone_difficulty_overrides: Dict[Tuple[int, int, int], float] = {}

        # Inventories: mapping from owner id to a list of carried Entities.
        # Initially empty; per-owner lists are created lazily via get_inventory().
        self.inventories: Dict[str, List[Entity]] = {}
        # Per-actor fractal blade runtime state.
        self.blade_states: Dict[str, blade_runtime_system.BladeState] = {}
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
            actions.append("aggressive_vines")
            actions.append("choking_vines")
            actions.append("push_pattern")
            actions.append("corruption_cone")
            actions.append("place_rune_anchor")
            actions.append("lightning")

        elif player_class == "Monk":
            # Monk kit: core rune tools + chakra generator.
            actions += [
                "place",
                "subdivide",
                "extend",
                "activate_seed",   # Activate N
                "reset",
                "meditate",
                "push_pattern",
                "chakra",
                "wind_rush",
                "energy_kick",
                "palm_burst",
                "mirror_strike",
                "aggressive_vines",
                "choking_vines",
            ]
        elif player_class == "Gardener":
            # Gardener kit: branch-heavy rune shaping + vine toolkit.
            actions += [
                "place",
                "branch",
                "activate_all",      # Activate R
                "activate_seed",     # Activate N
                "verdant_edges",     # Verdant
                "start_fern",        # Fern Growth
                "regrow",
                "choking_vines",
                "aggressive_vines",
            ]
        elif player_class == "Blade":
            # Blade kit: melee verbs + core rune manipulation.
            # Starts with an intrinsic *empty* blade; slots scale with INT.
            actions += [
                "slash",
                "thrust",
                "cleave",
                "throwing_knife",
                "place",
                "subdivide",
                "extend",
                "activate_seed",
                "reset",
                "meditate",
                "push_pattern",
            ]

        # For now, all other classes keep only move/wait (empty ability bar).
        player.actions = tuple(actions)

        # Tag as 'the player'
        player.tags.setdefault("is_player", True)
        if player_class:
            player.tags.setdefault("class", player_class)

        # Apply any character-defined chakra initialization (e.g., Monk setup).
        chakra_init = getattr(self.character, "chakra_init", None)
        if chakra_init:
            try:
                from edgecaster.systems.chakras import ChakraState
                player.chakra_state = ChakraState.from_dict(chakra_init)
            except Exception:
                pass


        self.player_id = player.id
        lvl = self._level()
        lvl.actors[player.id] = player
        lvl.entities[player.id] = player
        # Blade class starts with an intrinsic empty blade profile.
        if player_class == "Blade":
            try:
                blade_runtime_system.ensure_actor_blade_state(self, player.id)
            except Exception:
                pass

        # Canonical absolute position (Phase 1.5 yoga)
        # This makes abs-space the source of truth for player movement/render queries later.
        player.abs_pos = self.abs_from_zone_local(self.zone_coord, player.pos)


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
        try:
            self.reanchor_poi("lab", (self.lab_zone[0], self.lab_zone[1], 0))
        except Exception:
            pass

        # Choose nearby quest POIs (inventor tower + failing rune) for this run.
        # These are placed close to the starting zone so the early quest is reachable.
        start_zx, start_zy, _ = self.zone_coord
        max_screen = max(0, int(self.cfg.world_map_screens) - 1)

        zone_w = int(getattr(self.cfg, "world_width", 60) or 60)
        zone_h = int(getattr(self.cfg, "world_height", 40) or 40)
        reserved_coords: set[tuple[int, int, int]] = set()
        try:
            for poi_spec in self.poi_registry:
                for zx, zy in poi_spec.get_zone_coords(zone_w, zone_h):
                    reserved_coords.add((int(zx), int(zy), int(poi_spec.depth)))
        except Exception:
            reserved_coords = set()
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

        try:
            self.reanchor_poi("inventor_workshop", tuple(self.inventor_zone))
            self.reanchor_poi("failing_rune", tuple(self.failing_rune_zone))
            self.reanchor_poi("destabilizer_ruin", tuple(self.destabilizer_ruin_zone))
        except Exception:
            pass

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
        self.refresh_poi_locations()


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

    def _auto_stat_roll(self) -> Optional[str]:
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
        if self._spend_advancement_point(chosen, source="odd_random"):
            self.log.add(f"Your {chosen.upper()} grows (+1).")
            return chosen
        return None

    def _choose_stat_upgrade(self) -> Optional[str]:
        """Even levels: choose a stat to upgrade. For now auto-picks highest weight."""
        options = ["con", "res", "int", "agi"]
        weights = getattr(self.character, "stat_weights", None)
        if not weights:
            weights = {k: 1.0 for k in options}
        chosen = max(options, key=lambda k: weights.get(k, 0))
        return chosen

    def _spend_advancement_point(self, stat: str, *, source: str) -> bool:
        """Spend one advancement point on a stat.

        Returns True if a point was spent and the stat was increased.
        """
        stat = str(stat or "").lower()
        if stat not in {"con", "res", "int", "agi"}:
            return False
        points = int(getattr(self.character, "advancement_points", 0) or 0)
        if points <= 0:
            return False
        self.character.stats[stat] = self.character.stats.get(stat, 0) + 1
        self.character.advancement_points = points - 1
        lvl = 0
        try:
            lvl = int(getattr(self._player().stats, "level", 0))
        except Exception:
            lvl = 0
        self._telemetry_emit(
            "advancement_spent",
            stat=stat,
            source=source,
            remaining_points=int(self.character.advancement_points),
            level=lvl,
        )
        return True

    def _is_monk_player(self) -> bool:
        cls = (
            getattr(self.character, "player_class", None)
            or getattr(self.character, "char_class", None)
            or ""
        )
        return str(cls).strip().lower() == "monk"

    def _unlockable_chakras_for_player(self) -> List[str]:
        """Return currently unlockable chakra ids for the active player."""
        try:
            player = self._player()
            chakra_state = getattr(player, "chakra_state", None)
            if chakra_state is None:
                return []
            body_schema = prototypes.resolve_body_schema(player)
            return chakras_system.list_unlockable_chakras(body_schema, chakra_state)
        except Exception:
            return []

    def _maybe_prompt_monk_chakra_unlock(self, level: int) -> None:
        """
        Every 3rd level, Monk can unlock one currently-gated chakra.

        Uses the same gating query as the Chakra Sage so both systems stay in sync.
        """
        if not self._is_monk_player():
            return
        if int(level) <= 0 or int(level) % 3 != 0:
            return

        unlockable = self._unlockable_chakras_for_player()
        if not unlockable:
            self.log.add("No additional chakras are currently available to awaken.")
            return

        labels = [chakras_system.chakra_display_name(node_id) for node_id in unlockable]
        choices = list(labels) + ["Not right now."]

        def _apply_choice(idx: int, g: "Game") -> None:
            i = int(idx)
            if i < 0 or i >= len(unlockable):
                g.log.add("You postpone your chakra awakening for now.")
                return
            node_id = unlockable[i]
            try:
                p = g._player()
                chakra_state = getattr(p, "chakra_state", None)
                if chakra_state is None:
                    return
                if chakras_system.unlock_chakra(chakra_state, node_id, auto_activate=True):
                    display_name = chakras_system.chakra_display_name(node_id)
                    g.log.add(f"You awaken your {display_name} chakra.")
                    g.grant_ability("chakra")
            except Exception:
                return

        self.set_urgent(
            f"Level {int(level)} insight: choose one chakra to awaken.",
            title="Monk Awakening",
            choices=choices,
            on_choice_effect=_apply_choice,
        )

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
        before_level = int(stats.level)
        stats.xp += amount
        self._telemetry_emit(
            "xp_gain",
            amount=int(amount),
            xp_after=int(stats.xp),
            level_before=before_level,
        )
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
        # One advancement point per level-up.
        self.character.advancement_points = int(getattr(self.character, "advancement_points", 0) or 0) + 1
        self._telemetry_emit(
            "level_up",
            new_level=int(player.stats.level),
            hp_gain=int(hp_gain),
            mana_gain=int(mana_gain),
            advancement_points=int(self.character.advancement_points),
        )
        # Stat upgrades: odd levels auto-roll by class weights; even levels choose.
        lvl = player.stats.level
        if lvl % 2 == 1:
            self._auto_stat_roll()

            def _after_odd_continue(_idx: int, g: "Game") -> None:
                g._maybe_prompt_monk_chakra_unlock(int(lvl))

            self.set_urgent(
                f"You reach level {player.stats.level}! (+{hp_gain} HP, +{mana_gain} MP)",
                title="Level Up!",
                choices=["Continue..."],
                on_choice_effect=_after_odd_continue,
            )
        else:
            options = ["CON", "RES", "INT", "AGI"]

            def _apply_even_choice(idx: int, g: "Game") -> None:
                opt = options[max(0, min(int(idx), len(options) - 1))]
                stat = opt.lower()
                if not g._spend_advancement_point(stat, source="even_choice"):
                    fallback = g._choose_stat_upgrade() or "res"
                    g._spend_advancement_point(fallback, source="even_choice_fallback")
                    stat = fallback
                g.log.add(f"You focus your training: {stat.upper()} +1.")
                g._recalc_param_state_max()
                g._maybe_prompt_monk_chakra_unlock(int(lvl))

            self.set_urgent(
                f"You reach level {player.stats.level}! (+{hp_gain} HP, +{mana_gain} MP)\nChoose a stat to improve.",
                title="Level Up!",
                choices=options,
                on_choice_effect=_apply_even_choice,
            )
        # refresh params after stat change
        self._recalc_param_state_max()

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

        # --- ABS-space fog-of-war caches (authoritative for terrain rendering) ---
        # Visible this tick: (abs_x, abs_y, depth)
        self.fov_visible_abs: set[tuple[int, int, int]] = set()
        # Explored memory: depth -> {(abs_x, abs_y), ...}
        self.fov_explored_abs: dict[int, set[tuple[int, int]]] = {}
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

        # Chakra-granted abilities: auto-granted based on active chakra tokens.
        chakra_granted: list[str] = []
        try:
            from edgecaster.systems import chakra_items as _ci
            active = _ci.effective_active_nodes(self, actor)
            tokens: set[str] = set()
            for nid in active:
                for tok in str(nid).lower().split("."):
                    t = tok.strip()
                    if t:
                        tokens.add(t)
                        if t.endswith("_m"):
                            tokens.add(t[:-2])
            _CHAKRA_ABILITY_MAP = {
                "chakra_pulse": lambda t: bool(t),  # any active chakra
                "iron_skin": lambda t: "chest" in t and "back" in t,
                "third_eye": lambda t: "eye" in t,
                "root_grasp": lambda t: "foot" in t or "ankle" in t or "sole" in t,
                "phantom_limb": lambda t: any(x in t for x in ("arm", "shoulder", "elbow", "forearm", "hand")),
                "spinal_surge": lambda t: "back" in t and len(active) >= 3,
            }
            for ability, check in _CHAKRA_ABILITY_MAP.items():
                if check(tokens):
                    chakra_granted.append(ability)
        except Exception:
            pass

        merged: List[str] = []
        seen: set[str] = set()
        for name in list(intrinsic) + list(granted) + chakra_granted:
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
        return legendaries_system.alloc_legendary_lair_poi_id(self)

    def refresh_poi_locations(self) -> None:
        """Rebuild cached POI marker locations from the POI registry."""
        poi_reg = getattr(self, "poi_registry", None)
        if poi_reg is None:
            self.poi_locations = {}
            return
        cfg = getattr(self, "cfg", None)
        zone_w = int(getattr(cfg, "world_width", 60) or 60)
        zone_h = int(getattr(cfg, "world_height", 40) or 40)
        locs: Dict[str, Tuple[int, int, int]] = {}
        for poi in poi_reg:
            ax, ay = poi.anchor_abs
            zx = int(ax) // zone_w
            zy = int(ay) // zone_h
            locs[str(poi.id)] = (zx, zy, int(poi.depth))
        self.poi_locations = locs

    def reanchor_poi(self, poi_id: str, coord: Tuple[int, int, int]) -> bool:
        """Move an existing POI to a new zone (registry-only, ABS truth)."""
        poi_reg = getattr(self, "poi_registry", None)
        if poi_reg is None:
            return False
        poi_spec = poi_reg.get(str(poi_id))
        if poi_spec is None:
            return False
        zx, zy, depth = coord
        cfg = getattr(self, "cfg", None)
        zone_w = int(getattr(cfg, "world_width", 60) or 60)
        zone_h = int(getattr(cfg, "world_height", 40) or 40)
        footprint = ABSRect.from_zone_coord(int(zx), int(zy), zone_w, zone_h)
        anchor_abs = footprint.center
        new_spec = POISpec(
            id=poi_spec.id,
            kind=poi_spec.kind,
            name=poi_spec.name,
            footprint=footprint,
            depth=int(depth),
            anchor_abs=anchor_abs,
            parent_id=poi_spec.parent_id,
            child_ids=list(poi_spec.child_ids),
            npc_specs=list(poi_spec.npc_specs),
            structure_specs=list(poi_spec.structure_specs),
            entity_specs=list(poi_spec.entity_specs),
            seed=int(getattr(poi_spec, "seed", 0) or 0),
            tags=dict(poi_spec.tags or {}),
        )
        poi_reg.add(new_spec)
        self.refresh_poi_locations()
        return True

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
        # Use registry spatial query for multi-zone POI support.
        poi_specs = self.poi_registry.get_at_zone(x, y, depth)
        poi_hits = [p.id for p in poi_specs]
        is_lab_zone = False
        is_lair_zone = False
        lair_layout = "multi_room"
        lair_seed: int | None = None
        for poi_spec in poi_specs:
            # Check v2 structure specs
            for struct_spec in poi_spec.structure_specs:
                if struct_spec.kind == "lab":
                    is_lab_zone = True
                    break
                if struct_spec.kind == "legendary_lair":
                    is_lair_zone = True
                    lair_layout = str(struct_spec.tags.get("layout") or lair_layout)
                    try:
                        lair_seed = int(struct_spec.tags.get("lair_seed", poi_spec.seed))
                    except Exception:
                        lair_seed = lair_seed
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
            lair_rng = self.rng
            if lair_seed is not None:
                try:
                    lair_rng = random.Random(int(lair_seed) & 0xFFFFFFFF)
                except Exception:
                    lair_rng = self.rng
            mapgen_sites.generate_legendary_lair(world, lair_rng, layout=lair_layout)
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
        # Use the registry for spatial queries (supports multi-zone POIs)
        poi_hits = mapgen.apply_pois(world, coord, poi_registry=self.poi_registry)
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


        # Ensure this zone views the canonical pattern state
        self._sync_level_pattern_view(lvl)
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
        """Spawn/realize POI runtime contents for this loaded level."""
        poi_spawning_system.spawn_poi_contents(self, level, coord)

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
        """Debug helper for conjuring inventories near player."""
        spawning_system.debug_spawn_inventory_near_player(self, radius=radius, count=count)

    # =========================================================================
    # PHASE 9: SCHEDULING & TIME -> systems/scheduling.py
    # Tick scheduling, time advancement, cooldowns, regen
    # See vision_documents/spring_cleaning.txt for refactor plan
    # =========================================================================

    def _schedule(self, level: LevelState, delay: int, action: Callable[[], None]) -> None:
        """Schedule an action to run after `delay` ticks."""
        scheduling.schedule(self, level, delay, action)

    def _advance_time(self, level: LevelState, delta: int) -> None:
        """Advance time by delta ticks across the active zone window."""
        with perf_profiler.measure(self, "game._advance_time"):
            try:
                delta = int(delta)
            except Exception:
                delta = int(delta or 0)
            if delta <= 0:
                return

            # Ensure adjacent zones are loaded so movement and AI can cross boundaries.
            if self.cfg.allow_zone_prewarm_during_tick:
                active_levels = self._ensure_active_zones_loaded()
                if not active_levels:
                    active_levels = [level]
            else:
                # Zones are caches, not ontology — never create them on the tick hot path
                active_levels = [level]


            current_level = self._level()
            for lvl in active_levels:
                apply_player_systems = (lvl is current_level)
                scheduling.advance_time(self, lvl, delta, apply_player_systems=apply_player_systems)

            # Option 2: maintain ambient hostile populations across active zones.
            # This keeps roaming areas populated over time without relying on
            # one-time zone-entry spawns.
            ambient_spawns_system.maintain_population(self, active_levels, delta)

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
        return entity_ops_system.actor_at(level, pos)

    def _all_actors(self, level: LevelState) -> List[Actor]:
        return entity_ops_system.all_actors(level)

    # --- entity queries (non-actor entities) ---

    def _entity_at(self, level: LevelState, pos: Tuple[int, int]) -> Optional[Entity]:
        """Return the primary entity at a tile, preferring non-actor items."""
        return entity_ops_system.entity_at(level, pos)

    def _items_at(self, level: LevelState, pos: Tuple[int, int]) -> List[Entity]:
        """Return all non-actor items at the given position."""
        return entity_ops_system.items_at(level, pos)

    def _all_entities(self, level: LevelState) -> List[Entity]:
        return entity_ops_system.all_entities(level)
        
    def _blocking_entity_at(self, level: LevelState, pos: Tuple[int, int]) -> Optional[Entity]:
        """Return a blocking entity at this position, if any."""
        return entity_ops_system.blocking_entity_at(level, pos)

    def _toggle_door(self, ent: Entity, level: LevelState, notify: bool = False) -> None:
        entity_ops_system.toggle_door(self, ent, level, notify=notify)


    # --- status helpers ---

    def _add_status(self, actor: Actor, name: str, duration: int, on_apply: Optional[str] = None) -> None:
        entity_ops_system.add_status(self, actor, name, duration, on_apply=on_apply)

    def _tick_status(self, actor: Actor, name: str) -> None:
        entity_ops_system.tick_status(actor, name)

    def _has_status(self, actor: Actor, name: str) -> bool:
        return entity_ops_system.has_status(actor, name)

    def _get_zone(self, coord: Tuple[int, int, int], up_pos: Optional[Tuple[int, int]] = None) -> LevelState:
        """Get (and lazily create) a zone. Delegates to zones_system."""
        return zones_system.get_zone(self, coord, up_pos)

    def get_zone_for_render(self, coord: Tuple[int, int, int]) -> Optional[LevelState]:
        """Render-peek only: returns already-loaded zones, else None."""
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




    def _size_for_render(self, obj: object) -> float:
        return render_query_system.size_for_render(obj)

    def _rebuild_spatial_bins(self, level: LevelState) -> None:
        render_query_system.rebuild_spatial_bins(level)


    def _clamp_zone_window(
        self,
        zx0: int, zx1: int, zy0: int, zy1: int,
        *,
        zone_span_cap: int | None,
        ccx: float, ccy: float,
        zone_w: int, zone_h: int,
    ) -> tuple[int, int, int, int, bool]:
        return render_query_system.clamp_zone_window(
            zx0, zx1, zy0, zy1,
            zone_span_cap=zone_span_cap,
            ccx=ccx, ccy=ccy,
            zone_w=zone_w, zone_h=zone_h,
        )

    def sync_attention_instantiation(self, abs_rect: tuple[float, float, float, float], *, cam_lod: float) -> None:
        """Delegate attention lifecycle staging to systems.attention."""
        
        attention_system.sync_attention_instantiation(self, abs_rect, cam_lod=cam_lod)

        



    def renderables_in_abs_rect(
        self,
        abs_rect: Tuple[float, float, float, float],
        *,
        include_actors: bool = True,
        include_entities: bool = True,
        cam_lod: float,
        dmin: float = -5.0,
        dmax: float = 0.75,
        fade_w: float = 0.6,
        max_count: int = 2000,
    ) -> List[RenderProxy]:
        """Delegate attention-driven render candidate assembly to systems.attention."""
        return attention_system.renderables_in_abs_rect(
            self,
            abs_rect,
            include_actors=include_actors,
            include_entities=include_entities,
            cam_lod=cam_lod,
            dmin=dmin,
            dmax=dmax,
            fade_w=fade_w,
            max_count=max_count,
            proxy_cls=RenderProxy,
        )



    
    def _ensure_world_site_entities(self, *, zone_w: int, zone_h: int) -> None:
        """Delegate world-level site proxy staging to systems.attention."""
        attention_system._ensure_world_site_entities(self, zone_w=zone_w, zone_h=zone_h)


    def _ensure_world_poi_entities(self, *, zone_w: int, zone_h: int) -> None:
        """Delegate world-level POI proxy staging to systems.attention."""
        attention_system._ensure_world_poi_entities(self, zone_w=zone_w, zone_h=zone_h)



    def _ensure_world_aggregate_entities(
        self,
        *,
        zone_w: int,
        zone_h: int,
        zx0: int,
        zx1: int,
        zy0: int,
        zy1: int,
        zz: int,
        kinds=None,
    ) -> None:
        """Delegate world aggregate proxy staging to systems.attention."""
        attention_system._ensure_world_aggregate_entities(
            self,
            zone_w=zone_w,
            zone_h=zone_h,
            zx0=zx0,
            zx1=zx1,
            zy0=zy0,
            zy1=zy1,
            zz=zz,
            kinds=kinds,
        )


    def _realize_aggregate_details_in_zone(self, level: "LevelState", coord: Tuple[int, int, int], kinds=None) -> None:
        """Delegate zone-local aggregate detail realization to systems.attention."""
        attention_system._realize_aggregate_details_in_zone(self, level, coord, kinds=kinds)




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
        """Describe entities underfoot when manually examining ('x')."""
        inspection_system.describe_current_tile(self, for_examine=for_examine)


    def describe_abs_tile_at(self, abs_pos: Tuple[int, int], *, cam_lod: float | None = None) -> str:
        """Describe an ABS tile that may be outside the current zone."""
        return inspection_system.describe_abs_tile_at(self, abs_pos, cam_lod=cam_lod)


    def describe_tile_at(
        self,
        pos: Tuple[int, int],
        *,
        level: Optional[LevelState] = None,
        zone_coord: Optional[Tuple[int, int, int]] = None,
    ) -> str:
        return inspection_system.describe_tile_at(self, pos, level=level, zone_coord=zone_coord)








    def _auto_look(self, level: LevelState) -> None:
        """Describe items at the player's feet after moving."""
        player = level.actors.get(self.player_id)
        if player is None:
            return
        self._describe_tile(level, player.pos, observer_id=self.player_id, auto=True)

    def _describe_tile(
        self,
        level: LevelState,
        pos: Tuple[int, int],
        observer_id: Optional[str] = None,
        auto: bool = False,
    ) -> None:
        """Log a description of entities at the given tile, if any."""
        inspection_system.describe_tile_log(
            self,
            level,
            pos,
            observer_id=observer_id,
            auto=auto,
        )

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
            # Phase 1.5: player movement is canonical in abs-space.
            # Crossing a chunk boundary is just membership/caching, not metaphysics.
            if id == self.player_id:
                ax, ay = self._get_player_abs()
                self._move_player_to_abs((ax + int(dx), ay + int(dy)))
            else:
                try:
                    cur_abs = getattr(actor, "abs_pos", None)
                    if cur_abs is None:
                        cur_abs = self.abs_from_zone_local(level.coord, actor.pos)
                    new_abs = (int(cur_abs[0]) + int(dx), int(cur_abs[1]) + int(dy))
                    self._move_actor_to_abs(actor, new_abs, from_level=level)
                except Exception:
                    pass
            return

        # stair use is explicit, so only move/attack here
        target = self._actor_at(level, (nx, ny))
        if target and target.id != id:
            if self.is_hostile(actor, target) or self.is_hostile(target, actor):
                # Blade-class hosts replace bump-attack with blade slash.
                if blade_runtime_system.actor_uses_blade_melee(self, id):
                    handled = blade_runtime_system.act_blade_attack(
                        self,
                        id,
                        "slash",
                        target_pos=(nx, ny),
                        from_bump=True,
                    )
                    if handled:
                        return
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
                    if new_d > CHAIN_RANGE and new_d > cur_d:
                        # Block the move if it would extend the chain.
                        if id == self.player_id:
                            self.log.add("The chain tugs you back.")
                        return

            # Angry circus pack leash: members remain near ringmaster and
            # ringmaster avoids straying too far from troupe members.
            if tags.get("circus_group_id"):
                leash = int(tags.get("circus_leash_range", 15) or 15)
                ringmaster_id = tags.get("circus_ringmaster_id")
                # Member rule: cannot increase distance past leash from ringmaster.
                if ringmaster_id:
                    ringmaster = level.actors.get(str(ringmaster_id))
                    if ringmaster is None or not getattr(ringmaster, "alive", False):
                        # Group leader gone: drop leash tags and continue.
                        tags.pop("circus_ringmaster_id", None)
                        tags.pop("circus_group_id", None)
                        tags.pop("circus_leash_range", None)
                        actor.tags = tags
                    else:
                        rx, ry = ringmaster.pos
                        cur_d = max(abs(x - rx), abs(y - ry))
                        new_d = max(abs(nx - rx), abs(ny - ry))
                        if new_d > leash and new_d > cur_d:
                            if id == self.player_id:
                                self.log.add("The ringmaster's whistle calls the troupe back.")
                            return
                else:
                    # Ringmaster rule: keep troupe members within leash.
                    member_ids = list(tags.get("circus_member_ids", []) or [])
                    for mid in member_ids:
                        mate = level.actors.get(str(mid))
                        if mate is None or not getattr(mate, "alive", False):
                            continue
                        mx, my = mate.pos
                        cur_d = max(abs(x - mx), abs(y - my))
                        new_d = max(abs(nx - mx), abs(ny - my))
                        if new_d > leash and new_d > cur_d:
                            if id == self.player_id:
                                self.log.add("You can't abandon your circus troupe.")
                            return
        except Exception:
            pass

        # Update local cached position (zone-relative)
        actor.pos = (nx, ny)
        # Yoga: spatial bins are a cache, so any move invalidates them.
        level.spatial_dirty = True

        # Canonical ABS update applies to *all* actors.
        # During migration, some actors may not yet have abs_pos; derive from
        # the current zone coord deterministically.
        try:
            cur_abs = getattr(actor, "abs_pos", None)
            if cur_abs is None:
                ax, ay = self.abs_from_zone_local(level.coord, (x, y))
            else:
                ax, ay = int(cur_abs[0]), int(cur_abs[1])
            new_abs = (ax + int(dx), ay + int(dy))
            setattr(actor, "abs_pos", new_abs)
            if id == self.player_id:
                # Keep legacy helpers consistent; player is not special-cased in truth,
                # but other subsystems may still consult _get/_set_player_abs during migration.
                self._set_player_abs(new_abs)
        except Exception:
            # If an unusual object lacks abs_pos support, fail soft (render bridge can still derive).
            pass

        if id == self.player_id:
            level.need_fov = True
            # Auto-look when the player steps onto a tile (but don't describe yourself)
            try:
                self._auto_look(level)
            except Exception:
                pass


    def _attack(self, level: LevelState, attacker: Actor, defender: Actor) -> None:
        """Resolve an attack. Delegates to combat_system."""
        combat_system.attack(self, level, attacker, defender)

    # --- Absolute position yoga (Phase 1.5) ---------------------------------
    # Canonical truth for the player (and later all actors) is absolute tile coords.
    # We keep zone/local as a *cache addressing / LevelState membership* detail.

    def _zone_dims(self) -> tuple[int, int]:
        return coords_system.zone_dims(self)

    def _active_zone_coords(
        self,
        *,
        center: tuple[int, int, int] | None = None,
        radius: int | None = None,
    ) -> list[tuple[int, int, int]]:
        """Return a list of zone coords within the active radius (Chebyshev)."""
        if center is None:
            center = self.zone_coord
        if radius is None:
            radius = int(getattr(self, "active_zone_radius", 1) or 1)
        radius = max(0, int(radius))

        zx, zy, zz = center
        max_screen = max(0, int(self.cfg.world_map_screens) - 1)
        coords: list[tuple[int, int, int]] = []
        seen: set[tuple[int, int, int]] = set()
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                nx = max(0, min(max_screen, int(zx + dx)))
                ny = max(0, min(max_screen, int(zy + dy)))
                c = (nx, ny, int(zz))
                if c in seen:
                    continue
                seen.add(c)
                coords.append(c)
        return coords

    def _active_zone_coords_prioritized(
        self,
        *,
        center: tuple[int, int, int] | None = None,
        radius: int | None = None,
        dir_hint: tuple[int, int] | None = None,
    ) -> list[tuple[int, int, int]]:
        """
        Return active-zone coords ordered by likely movement relevance.

        Ordering rules:
        - current zone first
        - then zones closest in Chebyshev distance
        - if dir_hint is provided, zones "ahead" of movement are preferred
        """
        if center is None:
            center = self.zone_coord
        cx, cy, _cz = center
        coords = self._active_zone_coords(center=center, radius=radius)

        dxh = dyh = 0
        if dir_hint is not None:
            try:
                dxh = int(dir_hint[0])
                dyh = int(dir_hint[1])
            except Exception:
                dxh = dyh = 0

        def score(c: tuple[int, int, int]) -> tuple[int, int, int]:
            zx, zy, _ = c
            ddx = int(zx) - int(cx)
            ddy = int(zy) - int(cy)
            cheb = max(abs(ddx), abs(ddy))
            # Higher dot means "more forward" in movement direction, so negate for sorting.
            dot = ddx * dxh + ddy * dyh
            man = abs(ddx) + abs(ddy)
            return (cheb, -dot, man)

        coords.sort(key=score)
        return coords

    def _queue_zone_prewarm(
        self,
        coord: tuple[int, int, int],
    ) -> None:
        """Add a zone to the incremental prewarm queue if it is not already loaded/queued."""
        if coord in self.levels:
            return
        if coord in self._zone_prewarm_set:
            return
        self._zone_prewarm_queue.append(coord)
        self._zone_prewarm_set.add(coord)

    def _seed_zone_prewarm_queue(self) -> None:
        """
        Seed the prewarm queue from the current active radius.

        This is cheap and idempotent; duplicates are filtered by _zone_prewarm_set.
        """
        self._prune_zone_prewarm_queue()
        coords = self._active_zone_coords_prioritized(dir_hint=self._zone_prewarm_dir_hint)
        for c in coords:
            self._queue_zone_prewarm(c)

    def _prune_zone_prewarm_queue(self) -> None:
        """
        Drop queued coords that are far from the current zone.

        This avoids wasting budget on stale prewarm requests after fast travel
        or large camera/player jumps.
        """
        cx, cy, cz = self.zone_coord
        keep_radius = max(2, int(getattr(self, "active_zone_radius", 1) or 1) + 1)
        if not self._zone_prewarm_queue:
            return
        kept: list[tuple[int, int, int]] = []
        kept_set: set[tuple[int, int, int]] = set()
        for c in self._zone_prewarm_queue:
            zx, zy, zz = c
            if int(zz) != int(cz):
                continue
            if max(abs(int(zx) - int(cx)), abs(int(zy) - int(cy))) > keep_radius:
                continue
            if c in kept_set:
                continue
            kept.append(c)
            kept_set.add(c)
        self._zone_prewarm_queue = kept
        self._zone_prewarm_set = kept_set

    def _drain_zone_prewarm_queue(self, budget: int) -> None:
        """
        Incrementally create queued zones.

        This intentionally limits new zone creation per tick to reduce hitching.
        """
        left = max(0, int(budget))
        while left > 0 and self._zone_prewarm_queue:
            coord = self._zone_prewarm_queue.pop(0)
            self._zone_prewarm_set.discard(coord)
            if coord in self.levels:
                left -= 1
                continue
            try:
                zones_system.get_zone(self, coord, up_pos=None)
            except Exception:
                # If creation fails, drop it for now; a later seed pass can retry.
                left -= 1
                continue
            left -= 1

    def _loaded_active_levels(self) -> list[LevelState]:
        """Return already-loaded active levels only (no creation)."""
        out: list[LevelState] = []
        for coord in self._active_zone_coords():
            lvl = self.levels.get(coord)
            if lvl is not None:
                out.append(lvl)
        return out

    def _ensure_active_zones_loaded(self) -> list[LevelState]:
        """
        Ensure the current zone is loaded and incrementally prewarm neighbors.

        We do *not* synchronously force-create the entire active radius each tick,
        because that causes large frame spikes when crossing chunk boundaries.
        """
        # Current zone is mandatory.
        try:
            if self.zone_coord not in self.levels:
                zones_system.get_zone(self, self.zone_coord, up_pos=None)
        except Exception:
            pass

        # Incremental neighbor prewarm.
        self._seed_zone_prewarm_queue()
        budget = int(getattr(self, "zone_prewarm_budget_per_advance", 1) or 1)
        self._drain_zone_prewarm_queue(budget)

        # Return currently-loaded active zones.
        levels = self._loaded_active_levels()
        if not levels:
            try:
                levels = [self._level()]
            except Exception:
                levels = []
        return levels

    def _is_zone_active(self, coord: tuple[int, int, int] | None) -> bool:
        """Return True if a zone coord is within the active-radius window."""
        if coord is None:
            return False
        zx, zy, zz = coord
        cx, cy, cz = self.zone_coord
        if int(zz) != int(cz):
            return False
        radius = int(getattr(self, "active_zone_radius", 1) or 1)
        radius = max(0, radius)
        return max(abs(int(zx) - int(cx)), abs(int(zy) - int(cy))) <= radius

    @staticmethod
    def _floor_divmod(a: int, b: int) -> tuple[int, int]:
        return coords_system.floor_divmod(a, b)

    def abs_from_zone_local(
        self,
        zone_coord: tuple[int, int, int],
        local_pos: tuple[int, int],
    ) -> tuple[int, int]:
        return coords_system.abs_from_zone_local(self, zone_coord, local_pos)

    def zone_local_from_abs(
        self,
        abs_pos: tuple[int, int],
        *,
        depth: int | None = None,
        clamp_to_world: bool = True,
    ) -> tuple[tuple[int, int, int], tuple[int, int]]:
        return coords_system.zone_local_from_abs(
            self,
            abs_pos,
            depth=depth,
            clamp_to_world=clamp_to_world,
        )

    def _get_player_abs(self) -> tuple[int, int]:
        p = self._player()
        ap = getattr(p, "abs_pos", None)
        if ap is None:
            # Backfill from current membership (legacy compatibility)
            ap = self.abs_from_zone_local(self.zone_coord, p.pos)
            setattr(p, "abs_pos", ap)
        return int(ap[0]), int(ap[1])

    def _set_player_abs(self, abs_pos: tuple[int, int]) -> None:
        p = self._player()
        setattr(p, "abs_pos", (int(abs_pos[0]), int(abs_pos[1])))

    def _move_actor_to_abs(
        self,
        actor: Actor,
        abs_pos: tuple[int, int],
        *,
        from_level: LevelState | None = None,
    ) -> None:
        """Move a non-player actor across zone boundaries using ABS coordinates."""
        if getattr(actor, "id", None) == self.player_id:
            self._move_player_to_abs(abs_pos)
            return

        if from_level is None:
            try:
                for lvl in self.levels.values():
                    if actor.id in getattr(lvl, "actors", {}):
                        from_level = lvl
                        break
            except Exception:
                from_level = None
        if from_level is None:
            return

        dest_coord, dest_local = self.zone_local_from_abs(
            abs_pos,
            depth=getattr(from_level, "coord", self.zone_coord)[2],
            clamp_to_world=True,
        )
        dest_level = zones_system.get_zone(self, dest_coord, up_pos=None)
        level_changed = getattr(from_level, "coord", None) != dest_coord

        if level_changed:
            try:
                del from_level.actors[actor.id]
            except Exception:
                pass
            try:
                del from_level.entities[actor.id]
            except Exception:
                pass
            try:
                from_level.spatial_dirty = True
            except Exception:
                pass

            actor.pos = dest_local
            dest_level.actors[actor.id] = actor
            try:
                dest_level.entities[actor.id] = actor
            except Exception:
                pass
            try:
                dest_level.spatial_dirty = True
            except Exception:
                pass

            # If this actor is AI-driven, schedule its next turn in the new level.
            try:
                tags = getattr(actor, "tags", None) or {}
                if tags.get("ai"):
                    self._schedule(
                        dest_level,
                        self.cfg.action_time_fast,
                        lambda aid=actor.id, lvl=dest_level: self._monster_act(lvl, aid),
                    )
            except Exception:
                pass
        else:
            actor.pos = dest_local
            try:
                dest_level.spatial_dirty = True
            except Exception:
                pass

        setattr(actor, "abs_pos", (int(abs_pos[0]), int(abs_pos[1])))

    def _move_player_to_abs(self, abs_pos: tuple[int, int]) -> None:
        """
        Canonical movement primitive for the player:
        - compute dest chunk membership from abs
        - move between LevelStates if needed (cache)
        - update local pos, zone_coord, abs_pos
        - update FOV
        """
        player = self._player()
        old_level = self._level()
        old_coord = self.zone_coord
        old_level_coord = getattr(old_level, "coord", None)
        old_abs = self._get_player_abs()

        dest_coord, dest_local = self.zone_local_from_abs(abs_pos, depth=self.zone_coord[2], clamp_to_world=True)
        (dzx, dzy, dzz) = dest_coord

        # Ensure destination chunk exists (boring cache behavior)
        dest_level = zones_system.get_zone(self, dest_coord, up_pos=None)

        # Commit rune scalar state from the current zone view back into canonical storage.
        try:
            self._commit_pattern_state_from_level(self._level())
        except Exception:
            pass


        # Move between levels if membership changes
        level_changed = getattr(old_level, "coord", None) != dest_coord

        if level_changed:



            # remove from old level
            try:
                del old_level.actors[self.player_id]
            except Exception:
                pass
            try:
                # Some code mirrors actors into entities
                del old_level.entities[self.player_id]
            except Exception:
                pass
            # Yoga: old zone cache is stale after removing the player.
            try:
                old_level.spatial_dirty = True
            except Exception:
                pass

            self.zone_coord = dest_coord
            player.pos = dest_local
            dest_level.actors[self.player_id] = player


            try:
                dest_level.entities[self.player_id] = player
            except Exception:
                pass
            # Yoga: dest zone cache must be rebuilt to include the player.
            try:
                dest_level.spatial_dirty = True
            except Exception:
                pass



            # Signal camera to recenter on player after zone change
            self.camera_needs_recenter = True
        else:
            # Same chunk, just update local pos
            player.pos = dest_local
            # Yoga: local move still invalidates the cached bins.
            try:
                dest_level.spatial_dirty = True
            except Exception:
                pass

        # Update canonical absolute
        self._set_player_abs(abs_pos)

        # Track movement direction in zone-space for forward prewarm prioritization.
        try:
            ddx = int(abs_pos[0]) - int(old_abs[0])
            ddy = int(abs_pos[1]) - int(old_abs[1])
        except Exception:
            ddx = ddy = 0
        if ddx != 0 or ddy != 0:
            zx0, zy0, _ = old_coord
            zx1, zy1, _ = self.zone_coord
            zdx = int(zx1) - int(zx0)
            zdy = int(zy1) - int(zy0)
            # Prefer explicit zone movement hint when available, else use tile direction.
            hx = zdx if zdx != 0 else (1 if ddx > 0 else -1 if ddx < 0 else 0)
            hy = zdy if zdy != 0 else (1 if ddy > 0 else -1 if ddy < 0 else 0)
            self._zone_prewarm_dir_hint = (hx, hy)

        # After movement, seed/drain a small prewarm slice so neighboring zones
        # tend to be ready before the next boundary crossing.
        # Phase 0: never prewarm zones on the movement hot path.
        # (Zones are caches, not ontology; this must not happen per-step.)
        if getattr(self.cfg, "allow_zone_prewarm_during_move", False):
            self._seed_zone_prewarm_queue()
            budget = int(getattr(self, "zone_prewarm_budget_per_advance", 0) or 0)
            if budget > 0:
                self._drain_zone_prewarm_queue(budget)


        # Keep continuity: update FOV and Lorenz storm
        try:
            dest_level.need_fov = True
        except Exception:
            pass
        self._update_fov(dest_level)
        self._reset_lorenz_on_zone_change(player)
        # Ensure the new zone views canonical pattern state
        self._sync_level_pattern_view(dest_level)
        # Auto-describe items at the player's new position.
        try:
            self._auto_look(dest_level)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Canonical rune pattern state (ABS-space, per-depth)
    # ---------------------------------------------------------------------
    def _pattern_state(self, depth: int | None = None) -> dict:
        d = int(self.zone_coord[2] if depth is None else depth)
        state = self._pattern_state_by_depth.get(d)
        if state is None:
            state = {
                "pattern": builder.Pattern(),
                "anchor_abs": None,            # (ax, ay) in ABS tiles
                "activation_points": [],
                "activation_ttl": 0,
                # Secondary / modifier state that MUST persist across zone views:
                "pattern_motion": None,         # motion dict (see motion.py)
                "acidic_pattern": False,
                "fern_active": False,
                "fern_growth_tips": [],
                "fern_accum": 0.0,
                "choking_vines_state": None,
                "rune_choking_vines_state": None,
            }
            self._pattern_state_by_depth[d] = state

        # Back-compat: earlier versions used "motion"
        if "pattern_motion" not in state and "motion" in state:
            state["pattern_motion"] = state.get("motion")
        return state

    def pattern_anchor_abs(self) -> tuple[int, int] | None:
        return self._pattern_state().get("anchor_abs")

    def _set_pattern_anchor_abs(self, anchor_abs: tuple[int, int] | None) -> None:
        st = self._pattern_state()
        st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1])) if anchor_abs is not None else None

    def _commit_pattern_state_from_level(self, level: "LevelState") -> None:
        """
        Write the *current zone view* (LevelState) back into canonical pattern state.

        This is the critical bridge: LevelState is a cache/view; Game is truth.
        Without this, crossing a zone boundary can resurrect older canonical state.
        """
        coord = getattr(level, "coord", self.zone_coord)
        zx, zy, d = coord
        st = self._pattern_state(depth=d)

        # Pattern object
        st["pattern"] = getattr(level, "pattern", builder.Pattern())

        # Anchor: level stores zone-local; canonical stores ABS
        anchor_local = getattr(level, "pattern_anchor", None)
        if anchor_local is None:
            st["anchor_abs"] = None
        else:
            zw, zh = self._zone_dims()
            ox = zx * zw
            oy = zy * zh
            # anchor_local can be float-ish in some code paths; canonical is int tiles
            ax = int(round(anchor_local[0] + ox))
            ay = int(round(anchor_local[1] + oy))
            st["anchor_abs"] = (ax, ay)

        # Activation preview
        st["activation_points"] = list(getattr(level, "activation_points", []) or [])
        st["activation_ttl"] = int(getattr(level, "activation_ttl", 0) or 0)

        # Motion + modifiers
        st["pattern_motion"] = getattr(level, "pattern_motion", None)
        st["acidic_pattern"] = bool(getattr(level, "acidic_pattern", False))
        st["fern_active"] = bool(getattr(level, "fern_active", False))
        st["fern_growth_tips"] = list(getattr(level, "fern_growth_tips", []) or [])
        st["fern_accum"] = float(getattr(level, "fern_accum", 0.0) or 0.0)
        st["choking_vines_state"] = getattr(level, "choking_vines_state", None)
        st["rune_choking_vines_state"] = getattr(level, "rune_choking_vines_state", None)

    def _sync_level_pattern_view(self, level: "LevelState") -> None:
        """
        Make the current LevelState view the canonical Game pattern state.
        Keeps legacy code working while we migrate systems.
        """
        st = self._pattern_state(depth=getattr(level, "coord", self.zone_coord)[2])

        # Core pattern + secondary state
        level.pattern = st["pattern"]
        level.pattern_motion = st.get("pattern_motion", None)
        level.acidic_pattern = bool(st.get("acidic_pattern", False))
        level.fern_active = bool(st.get("fern_active", False))
        level.fern_growth_tips = list(st.get("fern_growth_tips", []) or [])
        level.fern_accum = float(st.get("fern_accum", 0.0) or 0.0)
        level.choking_vines_state = st.get("choking_vines_state")
        level.rune_choking_vines_state = st.get("rune_choking_vines_state")

        # Activation preview
        level.activation_points = list(st.get("activation_points", []) or [])
        level.activation_ttl = int(st.get("activation_ttl", 0) or 0)

        # Derive a *zone-local* anchor from canonical ABS anchor.
        anchor_abs = st.get("anchor_abs")
        if anchor_abs is None:
            level.pattern_anchor = None
            return

        zx, zy, _ = getattr(level, "coord", self.zone_coord)
        zw, zh = self._zone_dims()
        ox = zx * zw
        oy = zy * zh
        level.pattern_anchor = (int(anchor_abs[0] - ox), int(anchor_abs[1] - oy))




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

        # If the player is not on this level, only act if this zone is in the
        # active adjacency window (seamless boundary behavior).
        if self.player_id not in level.actors:
            if not self._is_zone_active(getattr(level, "coord", None)):
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

        # Status: Rooted (cannot act, just tick down)
        if self._has_status(actor, "rooted"):
            self.log.add(f"{actor.name} is rooted in place!")
            self._tick_status(actor, "rooted")
            self._schedule(
                level,
                self.cfg.action_time_fast,
                lambda aid=id, lvl=level: self._monster_act(lvl, aid),
            )
            return

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
        dest_level = level
        if actor.id not in level.actors:
            # Actor crossed a zone boundary; schedule on the new level.
            try:
                abs_pos = getattr(actor, "abs_pos", None)
                if abs_pos is None:
                    abs_pos = self.abs_from_zone_local(level.coord, actor.pos)
                dest_coord, _ = self.zone_local_from_abs(abs_pos, depth=level.coord[2], clamp_to_world=True)
                dest_level = zones_system.get_zone(self, dest_coord, up_pos=None)
            except Exception:
                dest_level = level

        self._schedule(
            dest_level,
            delay,
            lambda aid=id, lvl=dest_level: self._monster_act(lvl, aid),
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
        return combat_actions_system.act_throw_flask(self, actor_id, target_pos)

    def _consume_flask(self, actor_id: str, flask_item: Any) -> None:
        return combat_actions_system._consume_flask(self, actor_id, flask_item)

    def act_push_pattern(self, actor_id: str, target_pos=None, rotation_deg: float = 0) -> None:
        level = self._level()
        pattern_ops.push_pattern(self, level, target_pos, rotation_deg)

    def act_destabilize(self, actor_id: str) -> None:
        return combat_actions_system.act_destabilize(self, actor_id)

    def act_ignite(self, actor_id: str) -> None:
        return combat_actions_system.act_ignite(self, actor_id)

    def act_regrow(self, actor_id: str) -> None:
        return combat_actions_system.act_regrow(self, actor_id)

    def act_freeze(self, actor_id: str) -> None:
        return combat_actions_system.act_freeze(self, actor_id)

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
        return pattern_runtime_system.act_polygon(self, actor_id)

    def act_star(self, actor_id: str) -> None:
        return pattern_runtime_system.act_star(self, actor_id)

    # --- chakra modifiers / charge helpers ---

    def _chakra_modifiers(self, actor_id: str):
        return pattern_runtime_system.chakra_modifiers(self, actor_id)

    def chakra_effects(self, actor_id: Optional[str] = None) -> chakra_effects_system.ChakraEffectSnapshot:
        """Return aggregated passive effects from the actor's active chakras.

        This is the canonical query point for chakra-conditioned passives.
        New systems should use this helper instead of inspecting chakra node ids
        directly, so condition logic stays centralized in chakra_effects.py.
        """
        aid = str(actor_id or getattr(self, "player_id", ""))
        actor: Optional[Actor] = None
        try:
            actor = self._level().actors.get(aid)
        except Exception:
            actor = None
        if actor is None:
            # Fallback: actor may not be in the current zone cache bucket.
            for lvl in getattr(self, "levels", {}).values():
                actor = lvl.actors.get(aid)
                if actor is not None:
                    break
        if actor is None:
            return chakra_effects_system.ChakraEffectSnapshot()

        # Active set includes explicit activations plus item-driven temporary
        # auto-activations, so passives can react to equipped chakra gear.
        active = chakra_items_system.effective_active_nodes(self, actor)
        return chakra_effects_system.evaluate_effects(active)

    def chakra_effect_value(
        self,
        key: str,
        *,
        actor_id: Optional[str] = None,
        default: float = 0.0,
    ) -> float:
        return self.chakra_effects(actor_id).value(key, default)

    def _consume_chakra_charge(self, actor_id: str, amount: float) -> None:
        return pattern_runtime_system.consume_chakra_charge(self, actor_id, amount)

    def act_chakra(self, actor_id: str) -> None:
        return pattern_runtime_system.act_chakra(self, actor_id)

    # --- Blade runtime delegates (systems/blade_runtime.py) ---
    def ensure_actor_blade_state(self, actor_id: str):
        return blade_runtime_system.ensure_actor_blade_state(self, actor_id)

    def set_actor_blade_generators(self, actor_id: str, generators: List[str]):
        return blade_runtime_system.set_actor_blade_generators(self, actor_id, generators)

    def act_blade_attack(
        self,
        actor_id: str,
        verb: str,
        *,
        target_pos: Optional[Tuple[int, int]] = None,
        from_bump: bool = False,
    ) -> bool:
        return blade_runtime_system.act_blade_attack(
            self,
            actor_id,
            verb,
            target_pos=target_pos,
            from_bump=from_bump,
        )

    def act_throwing_knife(
        self,
        actor_id: str,
        *,
        target_pos: Optional[Tuple[int, int]] = None,
    ) -> bool:
        return blade_runtime_system.act_throwing_knife(
            self,
            actor_id,
            target_pos=target_pos,
        )

    # --- Combat action delegates (systems/combat_actions.py) ---
    def _wind_rush_start_vertex_candidates(
        self,
        actor_tile: Tuple[int, int],
        world_vertices: List[Tuple[float, float]],
        pattern: builder.Pattern,
    ) -> set[int]:
        return combat_actions_system._wind_rush_start_vertex_candidates(
            self,
            actor_tile,
            world_vertices,
            pattern,
        )
    def _wind_rush_vertex_path(
        self,
        pattern: builder.Pattern,
        start_candidates: set[int],
        target_idx: int,
        num_vertices: int,
    ) -> Optional[List[int]]:
        return combat_actions_system._wind_rush_vertex_path(
            self,
            pattern,
            start_candidates,
            target_idx,
            num_vertices,
        )
    def _wind_rush_local_path_points(
        self,
        actor_tile: Tuple[int, int],
        path_indices: List[int],
        world_vertices: List[Tuple[float, float]],
    ) -> List[Tuple[int, int]]:
        return combat_actions_system._wind_rush_local_path_points(
            self,
            actor_tile,
            path_indices,
            world_vertices,
        )
    def wind_rush_preview(
        self,
        target_vertex: Optional[int],
        *,
        actor_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        return combat_actions_system.wind_rush_preview(
            self,
            target_vertex,
            actor_id=actor_id,
        )
    def act_wind_rush(self, actor_id: str, target_vertex: Optional[int]) -> None:
        return combat_actions_system.act_wind_rush(self, actor_id, target_vertex)
    def act_energy_kick(self, actor_id: str) -> None:
        return combat_actions_system.act_energy_kick(self, actor_id)
    def _chakra_nodes_for_vertex(self, v: Any) -> set[str]:
        return combat_actions_system._chakra_nodes_for_vertex(self, v)
    def _chakra_world_points(
        self,
        pattern: builder.Pattern,
        anchor: Tuple[int, int],
        include_predicate: Optional[Callable[[str], bool]] = None,
        *,
        predicate: Optional[Callable[[str], bool]] = None,
    ) -> List[Tuple[float, float]]:
        # Compatibility wrapper:
        # combat_actions uses keyword `predicate=...` while older call sites passed
        # the callable positionally as `include_predicate`. Accept either form.
        pred = predicate or include_predicate
        if pred is None:
            return []
        return combat_actions_system._chakra_world_points(
            self,
            pattern,
            anchor,
            predicate=pred,
        )
    def act_palm_burst(self, actor_id: str) -> None:
        return combat_actions_system.act_palm_burst(self, actor_id)
    def act_mirror_strike(self, actor_id: str) -> None:
        return combat_actions_system.act_mirror_strike(self, actor_id)
    def act_aggressive_vines(self, actor_id: str) -> None:
        return combat_actions_system.act_aggressive_vines(self, actor_id)
    def act_choking_vines(self, actor_id: str) -> None:
        return combat_actions_system.act_choking_vines(self, actor_id)
    def act_corrosive_melt(self, actor_id: str) -> None:
        return combat_actions_system.act_corrosive_melt(self, actor_id)
    def act_start_fern(self, actor_id: str) -> None:
        return combat_actions_system.act_start_fern(self, actor_id)

    def _apply_fractal_op(self, lvl: LevelState, kind: str) -> None:
        return pattern_runtime_system.apply_fractal_op(self, lvl, kind)


    def _reset_pattern_core(self, lvl: LevelState) -> None:
        pattern_ops.reset_pattern(self)


    def _meditate_core(self, lvl: LevelState, actor_id: str) -> None:
        # Currently only the player meditates; hook actor_id up properly later.
        player = self._player()
        before = player.stats.mana
        gain = 10
        # Chakra passive: back endurance grants bonus mana regen.
        try:
            gain += int(self.chakra_effect_value("mana_regen_bonus", actor_id=self.player_id))
        except Exception:
            pass
        player.stats.mana = min(player.stats.max_mana, player.stats.mana + gain)
        restored = player.stats.mana - before

        # Chakra passive: chest vigor grants HP on meditation.
        hp_bonus = 0
        try:
            hp_bonus = int(self.chakra_effect_value("hp_regen_per_rest", actor_id=self.player_id))
        except Exception:
            pass
        if hp_bonus > 0 and player.stats.hp < player.stats.max_hp:
            player.stats.hp = min(player.stats.max_hp, player.stats.hp + hp_bonus)
            player.stats.clamp()

        if restored > 0 or hp_bonus > 0:
            parts = []
            if restored > 0:
                parts.append(f"{restored} mana")
            if hp_bonus > 0:
                parts.append(f"{hp_bonus} HP")
            self.log.add(f"You meditate and restore {' and '.join(parts)}.")
        else:
            self.log.add("You meditate but feel already full of mana.")




    def _activation_origin(self, level: LevelState) -> Optional[Tuple[int, int]]:
        return pattern_runtime_system.activation_origin(self, level)

    def _activate_pattern_all(self, level: LevelState, target_vertex: Optional[int]) -> None:
        return pattern_runtime_system.activate_pattern_all(self, level, target_vertex)

    def _activate_pattern_seed_neighbors(self, level: LevelState, target_vertex: Optional[int]) -> None:
        return pattern_runtime_system.activate_pattern_seed_neighbors(
            self,
            level,
            target_vertex,
        )

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
        """Update visibility/exploration flags using ABS-space as the source of truth.

        This MUST be continuous across chunk boundaries.
        Zones/chunks are cache buckets only: FoV is computed in ABS coordinates and
        projected into whatever chunks are touched.
        """
        if self.player_id not in level.actors:
            return

        # Apply view bonus from equipment + chakra passives.
        view_bonus = self.effective_character_stats().get("view", 0)
        view_bonus += int(round(self.chakra_effect_value("fov_radius_bonus", actor_id=self.player_id)))
        # Status: third_eye grants a large temporary vision boost.
        try:
            player = level.actors.get(self.player_id)
            if player and self._has_status(player, "third_eye"):
                view_bonus += 10
        except Exception:
            pass
        radius = int(radius + view_bonus)
        if radius < 1:
            radius = 1

        depth = int(self.zone_coord[2])

        # Canonical player absolute position
        p_absx, p_absy = self._get_player_abs()

        # God Vision mode: keep existing behavior stable (zone-local reveal)
        if getattr(self, "god_vision", False):
            level.world.clear_visibility()
            for y in range(level.world.height):
                for x in range(level.world.width):
                    tile = level.world.get_tile(x, y)
                    if tile:
                        tile.visible = True
                        tile.explored = True
                    actor = self._actor_at(level, (x, y))
                    if actor and actor.id not in level.spotted:
                        level.spotted.add(actor.id)
            # Lighting is zone-local; apply to current chunk only.
            from edgecaster.systems import lighting
            px, py = level.actors[self.player_id].pos
            lighting.update_level_lighting(self, level, (px, py))
            level.need_fov = False
            return

        # Clear ABS-space visibility for this tick (explored is persistent)
        try:
            self.fov_visible_abs.clear()
        except Exception:
            self.fov_visible_abs = set()

        # --- Determine which chunks are observed by this FoV update ---
        zw, zh = self._zone_dims()
        min_ax = int(p_absx - radius)
        max_ax = int(p_absx + radius)
        min_ay = int(p_absy - radius)
        max_ay = int(p_absy + radius)

        zminx, _ = self._floor_divmod(min_ax, zw)
        zmaxx, _ = self._floor_divmod(max_ax, zw)
        zminy, _ = self._floor_divmod(min_ay, zh)
        zmaxy, _ = self._floor_divmod(max_ay, zh)

        observed: dict[tuple[int, int, int], LevelState] = {}
        for zy in range(int(zminy), int(zmaxy) + 1):
            for zx in range(int(zminx), int(zmaxx) + 1):
                zc = (int(zx), int(zy), int(depth))
                zl = zones_system.get_zone(self, zc)
                if zl is not None:
                    observed[zc] = zl

        # Clear visibility across all observed chunks so 'forgetting' works across boundaries
        for zl in observed.values():
            zl.world.clear_visibility()

        # Build ABS-space opaque set from vision-blocking entities across observed chunks
        opaque_abs: set[tuple[int, int]] = set()
        for zc, zl in observed.items():
            for ent in zl.entities.values():
                if getattr(ent, "blocks_vision", False):
                    ax, ay = self.abs_from_zone_local(zc, ent.pos)
                    opaque_abs.add((int(ax), int(ay)))

        # Status: third_eye bypasses all vision-blocking entities.
        try:
            player = level.actors.get(self.player_id)
            if player and self._has_status(player, "third_eye"):
                opaque_abs = set()
        except Exception:
            pass

        # ABS-space terrain occluder query (walls/cliffs/etc.)
        def _blocks_vision_abs(ax: int, ay: int) -> bool:
            # Resolve ABS -> (zone, local) without world clamping
            zc, local = self.zone_local_from_abs((ax, ay), depth=depth, clamp_to_world=False)
            zl = observed.get(zc)
            if zl is None:
                zl = zones_system.get_zone(self, zc)
                if zl is None:
                    return False  # unknown chunk: do not create a phantom wall
                observed[zc] = zl
            lx, ly = local
            tile = zl.world.get_tile(int(lx), int(ly))
            if tile is None:
                return False
            return bool(getattr(tile, "blocks_vision", False))

        # ABS-space LOS test from a -> b (allows seeing the target square itself)
        def _los_abs(a_abs: tuple[int, int], b_abs: tuple[int, int]) -> bool:
            for (tx, ty) in _line_points(a_abs[0], a_abs[1], b_abs[0], b_abs[1]):
                tx = int(tx); ty = int(ty)

                # Always allow seeing the target square itself.
                if (tx, ty) == (int(b_abs[0]), int(b_abs[1])):
                    return True

                # Skip the origin cell
                if (tx, ty) == (int(a_abs[0]), int(a_abs[1])):
                    continue

                # Entity occluders (doors, wall-entities, etc.)
                if (tx, ty) in opaque_abs:
                    return False

                # Terrain occluders
                if _blocks_vision_abs(tx, ty):
                    return False
            return True

        # Recompute 'spotted' from scratch for the observed area (prevents sticky visibility across chunks)
        prev_spotted = set(level.spotted)
        new_spotted: set[str] = set()
        new_spotted.add(self.player_id)

        r2 = radius * radius
        for ay in range(min_ay, max_ay + 1):
            dy = ay - p_absy
            for ax in range(min_ax, max_ax + 1):
                dx = ax - p_absx
                if dx * dx + dy * dy > r2:
                    continue

                # Resolve ABS -> (zone, local)
                zc, local = self.zone_local_from_abs((ax, ay), depth=depth, clamp_to_world=False)
                zl = observed.get(zc)
                if zl is None:
                    zl = zones_system.get_zone(self, zc)
                    if zl is None:
                        continue
                    observed[zc] = zl

                lx, ly = int(local[0]), int(local[1])

                if not _los_abs((p_absx, p_absy), (ax, ay)):
                    continue

                # ABS-space FOV truth (renderer consults this first)
                self.fov_visible_abs.add((int(ax), int(ay), int(depth)))
                self.fov_explored_abs.setdefault(int(depth), set()).add((int(ax), int(ay)))

                # Project into chunk-local tile flags as cache/persistence.
                tile = zl.world.get_tile(lx, ly)
                if tile is not None:
                    tile.visible = True
                    tile.explored = True

                actor = self._actor_at(zl, (lx, ly))
                if actor:
                    new_spotted.add(actor.id)
                    if actor.id != self.player_id and actor.id not in prev_spotted:
                        self.log.add(f"You spot a {actor.name}.")

        level.spotted = new_spotted
        level.need_fov = False


    # --- ABS-space fog queries (authoritative for terrain rendering) ---
    def is_abs_visible(self, abs_x: int, abs_y: int, depth: int | None = None) -> bool:
        """True if this ABS tile is visible THIS tick."""
        if depth is None:
            try:
                depth = int(self.zone_coord[2])
            except Exception:
                depth = 0
        try:
            return (int(abs_x), int(abs_y), int(depth)) in self.fov_visible_abs
        except Exception:
            return False

    def is_abs_explored(self, abs_x: int, abs_y: int, depth: int | None = None) -> bool:
        """True if this ABS tile has ever been seen (persistent exploration)."""
        if depth is None:
            try:
                depth = int(self.zone_coord[2])
            except Exception:
                depth = 0
        s = self.fov_explored_abs.get(int(depth))
        if not s:
            return False
        return (int(abs_x), int(abs_y)) in s



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

    # --- debug logging ---
    def _telemetry_emit(self, event: str, **payload: Any) -> None:
        """Emit one telemetry event to telemetry.ndjson (always-on, fail-soft)."""
        tel = getattr(self, "_telemetry", None)
        if tel is None:
            return
        safe_payload = dict(payload)
        try:
            # Some events (e.g. session_start) are emitted before the first level exists.
            lvl = None
            try:
                lvl = self._level()
            except Exception:
                lvl = None
            safe_payload.setdefault("tick", int(getattr(lvl, "current_tick", 0) if lvl is not None else 0))
            safe_payload.setdefault("zone", tuple(getattr(self, "zone_coord", (0, 0, 0))))
            tel.emit(event, **safe_payload)
        except Exception:
            return

    def _debug(self, msg: str) -> None:
        try:
            with open(self.debug_log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
