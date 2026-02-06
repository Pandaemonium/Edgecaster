from dataclasses import dataclass, field
from itertools import islice
from typing import Dict, Tuple, List, Optional, Callable
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
#       - ✓ spawning_system.spawn_enemies() now sets abs_pos on each spawn
#       - ✓ spawning_system.spawn_entity_from_template() sets abs_pos
#       - ✓ spawn_imps_near, spawn_echoes_near, spawn_enemies_for_biome set abs_pos
#       - ✓ _spawn_poi_contents() fixed: abs_pos for all actor spawns (bug: was using wrong var)
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

@dataclass
class _YogaStagedEntity:
    """Minimal entity-like object for attention-staged renderables (e.g., structure walls).

    We use this when we don't yet have a full prototype/spec for an entity, but we still
    want the renderer + LoD system to treat it as a real object. It intentionally mirrors
    the small surface area that renderables_in_abs_rect() relies on: id, pos, abs_pos,
    kind, tags, glyph, color/base_size.
    """
    id: str
    pos: Tuple[int, int]
    abs_pos: Tuple[int, int]
    kind: str = "structure"
    glyph: str = "#"
    color: Tuple[int, int, int] = (140, 120, 100)
    base_size: float = 1.0
    tags: Dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.tags.get("name", self.id)


class AttentionCellStore:
    """Unified cache of instantiated entities keyed by ABS-space bins (not zones).

    This is a Phase-1.8 stepping stone toward a full quadtree-backed attention field.
    For now it's a simple ABS bin grid so we can:
      - stage/unstage derived entities anywhere (god-vision), without creating zones
      - keep derived entities "real" objects that can be rendered and later interacted with
    """

    def __init__(self, *, bin_size: int = 32) -> None:
        self.bin_size = max(1, int(bin_size))
        self.entities: Dict[str, object] = {}
        self.eid_to_bin: Dict[str, Tuple[int, int, int]] = {}
        self.bins: Dict[Tuple[int, int, int], List[str]] = {}
        self.lineage_to_eid: Dict[str, str] = {}

    def _bin_for_abs(self, ax: float, ay: float, zz: int) -> Tuple[int, int, int]:
        bs = self.bin_size
        return (int(math.floor(float(ax) / bs)), int(math.floor(float(ay) / bs)), int(zz))

    def stage(self, obj: object, *, abs_x: float, abs_y: float, zz: int, lineage_id: str | None = None) -> str:
        eid = str(getattr(obj, "id", "") or "")
        if not eid:
            raise ValueError("staged object missing id")

        if lineage_id:
            prev = self.lineage_to_eid.get(lineage_id)
            if prev and prev != eid:
                # Prefer stable lineage mapping; remove old eid if present.
                self.despawn(prev)
            self.lineage_to_eid[lineage_id] = eid

        b = self._bin_for_abs(abs_x, abs_y, zz)

        prevb = self.eid_to_bin.get(eid)
        if prevb and prevb != b:
            # Move bins
            try:
                lst = self.bins.get(prevb)
                if lst and eid in lst:
                    lst.remove(eid)
            except Exception:
                pass

        self.entities[eid] = obj
        self.eid_to_bin[eid] = b
        self.bins.setdefault(b, []).append(eid)
        return eid

    def despawn(self, eid: str) -> None:
        eid = str(eid)
        b = self.eid_to_bin.pop(eid, None)
        if b is not None:
            try:
                lst = self.bins.get(b)
                if lst and eid in lst:
                    lst.remove(eid)
            except Exception:
                pass
        self.entities.pop(eid, None)

        # Remove from lineage mapping if present
        try:
            for k, v in list(self.lineage_to_eid.items()):
                if v == eid:
                    del self.lineage_to_eid[k]
        except Exception:
            pass

    def query_abs_rect(self, abs_rect: Tuple[float, float, float, float], *, zz: int) -> List[Tuple[object, float, float]]:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
        if ax1 < ax0:
            ax0, ax1 = ax1, ax0
        if ay1 < ay0:
            ay0, ay1 = ay1, ay0
        if ax1 == ax0 or ay1 == ay0:
            return []

        bs = self.bin_size
        bx0 = int(math.floor(ax0 / bs))
        by0 = int(math.floor(ay0 / bs))
        bx1 = int(math.floor((ax1 - 1e-6) / bs))
        by1 = int(math.floor((ay1 - 1e-6) / bs))

        out: List[Tuple[object, float, float]] = []
        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                ids = self.bins.get((bx, by, int(zz)))
                if not ids:
                    continue
                for eid in ids:
                    obj = self.entities.get(eid)
                    if obj is None:
                        continue
                    ap = getattr(obj, "abs_pos", None)
                    if not ap:
                        continue
                    ax, ay = ap
                    ax = float(ax)
                    ay = float(ay)
                    if ax0 <= ax < ax1 and ay0 <= ay < ay1:
                        out.append((obj, ax, ay))
        return out



from edgecaster import config
from edgecaster.state.world import World
from edgecaster.state.actors import Actor, Stats, Human
from edgecaster.state.entities import Entity
from edgecaster.enemies import factory as enemy_factory
from edgecaster.systems.world_entity_index import WorldEntityIndex
from edgecaster.systems import aggregate_resolution as aggregate_system

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.systems.sites import load_site_types

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
from edgecaster.systems import pattern_ops
from edgecaster.systems import scheduling
from edgecaster.systems import combat as combat_system
from edgecaster.systems import zones as zones_system
from edgecaster.systems import overmap as overmap_system
from edgecaster.systems import difficulty as difficulty_system
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
        self.camera_needs_recenter = False  # Set by zone transitions to signal camera update


        # debug flags
        self.debug_no_fog: bool = False
        self.debug_spawn_inventories: bool = False
        # Active-zone radius for seamless adjacency (zones are caches, not walls).
        # Radius=1 means a 3x3 window around the player is live.
        self.active_zone_radius: int = 1

        # zones keyed by (x, y, depth)
        self.levels: Dict[Tuple[int, int, int], LevelState] = {}
        # Attention-staged entities (Route 2: no rectangular zones as ontology)
        self.attn_store: AttentionCellStore = AttentionCellStore(bin_size=int(getattr(cfg, 'attn_bin_size', 32) or 32))
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

        # Realize aggregate details into this loaded zone (simulation allowed).
        # This replaces the old global berry scattering test.
        if coord[2] == 0:  # depth == 0
            if not getattr(world, "is_lair", False):
                self._realize_aggregate_details_in_zone(lvl, coord)

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
        """Spawn NPCs defined by any POIs attached to this level."""
        poi_ids = getattr(level.world, "poi_ids", [])

        # Debug logging for POI spawning
        try:
            with open("C:/Games/Edgecaster/debug.log", "a") as f:
                f.write(f"[_spawn_poi_contents] coord={coord}, poi_ids={poi_ids}\n")
        except Exception:
            pass

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
            # Registry is authoritative for POI definitions.
            poi_spec = self.poi_registry.get(pid)
            if not poi_spec:
                continue

            # Handle structures - use v2 structure_specs or legacy structures
            structures_to_process: List[dict] = []
            # Convert v2 structure_specs to dict format for compatibility
            for ss in poi_spec.structure_specs:
                struct_dict = {"kind": ss.kind}
                struct_dict.update(ss.tags)
                structures_to_process.append(struct_dict)

            for struct in structures_to_process:
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
                            # YOGA: Use correct variable (pos, not spawn_pos)
                            mob = enemy_factory.spawn_enemy(enemy_id, pos, abs_pos=self.abs_from_zone_local(coord, pos))
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
                        # YOGA: Set abs_pos for legendary boss
                        actor = enemy_factory.spawn_enemy(base_proto, spot, abs_pos=self.abs_from_zone_local(coord, spot))
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
                            # YOGA: Use correct variable ((tx, ty), not spawn_pos)
                            mob = enemy_factory.spawn_enemy(base_proto, (tx, ty), abs_pos=self.abs_from_zone_local(coord, (tx, ty)))
                            level.actors[mob.id] = mob
                            level.entities[mob.id] = mob
                            self._schedule(
                                level,
                                self.cfg.action_time_fast,
                                lambda aid=mob.id, lvl=level: self._monster_act(lvl, aid),
                            )
                            spawned += 1
                elif struct.get("kind") == "colosseum_arena":
                    # Build oval arena structure using wall ENTITIES (not terrain)
                    # This allows walls to be visible in WorldEntityIndex when zooming around
                    if poi_spec is None:
                        continue
                    fp = poi_spec.footprint
                    zx, zy, _ = coord
                    zone_w = self.cfg.world_width
                    zone_h = self.cfg.world_height

                    # Calculate ellipse center and radii from footprint
                    arena_cx = (fp.x0 + fp.x1) / 2.0
                    arena_cy = (fp.y0 + fp.y1) / 2.0
                    arena_rx = (fp.x1 - fp.x0) / 2.0  # horizontal radius
                    arena_ry = (fp.y1 - fp.y0) / 2.0  # vertical radius

                    # Get wall thickness from struct tags (default 3)
                    wall_thickness = int(struct.get("wall_thickness", 3))

                    # Colors for arena
                    wall_color = [140, 120, 100]    # Stone walls (list for override)
                    floor_color = (180, 160, 120)   # Sand/dirt floor

                    # Zone bounds in ABS space
                    zone_abs_x0 = zx * zone_w
                    zone_abs_y0 = zy * zone_h

                    world = level.world

                    # Pre-compute entrance positions to skip wall spawning there
                    entrance_width = 4
                    entrance_positions: set = set()
                    entrances = [
                        (int(arena_cx), fp.y0 + wall_thickness // 2),  # North
                        (int(arena_cx), fp.y1 - wall_thickness // 2 - 1),  # South
                        (fp.x0 + wall_thickness // 2, int(arena_cy)),  # West
                        (fp.x1 - wall_thickness // 2 - 1, int(arena_cy)),  # East
                    ]
                    for ent_abs_x, ent_abs_y in entrances:
                        for offset in range(-entrance_width // 2, entrance_width // 2 + 1):
                            for ex, ey in [(ent_abs_x + offset, ent_abs_y), (ent_abs_x, ent_abs_y + offset)]:
                                entrance_positions.add((ex, ey))

                    walls_spawned = 0

                    # Iterate through all tiles in this zone
                    for ty in range(zone_h):
                        for tx in range(zone_w):
                            abs_x = zone_abs_x0 + tx
                            abs_y = zone_abs_y0 + ty

                            dx = abs_x - arena_cx
                            dy = abs_y - arena_cy
                            if arena_rx <= 0 or arena_ry <= 0:
                                continue

                            # Normalized distance: <1 inside, =1 on boundary, >1 outside
                            dist_norm = (dx * dx) / (arena_rx * arena_rx) + (dy * dy) / (arena_ry * arena_ry)

                            # Inner ellipse boundary
                            inner_rx = arena_rx - wall_thickness
                            inner_ry = arena_ry - wall_thickness
                            if inner_rx > 0 and inner_ry > 0:
                                inner_dist = (dx * dx) / (inner_rx * inner_rx) + (dy * dy) / (inner_ry * inner_ry)
                            else:
                                inner_dist = 0

                            tile = world.get_tile(tx, ty)
                            if tile is None:
                                continue

                            if dist_norm <= 1.0:
                                # Inside or on the ellipse boundary
                                if inner_dist >= 1.0 and (abs_x, abs_y) not in entrance_positions:
                                    # Wall position - spawn wall entity
                                    pos = (tx, ty)
                                    # Use deterministic ID based on ABS position to avoid duplicates
                                    wall_eid = f"colosseum_wall:{abs_x},{abs_y}"
                                    if wall_eid not in level.entities and not self._entity_at(level, pos):
                                        try:
                                            ent = self._spawn_entity_from_template(
                                                "wall",
                                                pos,
                                                overrides={
                                                    "id": wall_eid,
                                                    "glyph": "█",
                                                    "color": wall_color,
                                                    "name": "Arena Wall",
                                                    "description": "Ancient stone walls of the colosseum.",
                                                },
                                            )
                                            # Override the ID to our deterministic one
                                            ent.id = wall_eid
                                            level.entities[wall_eid] = ent
                                            walls_spawned += 1
                                        except Exception:
                                            pass
                                    # Terrain should be walkable (entity handles blocking)
                                    tile.walkable = True
                                    tile.glyph = "."
                                    tile.color = floor_color
                                else:
                                    # Arena floor (including entrances)
                                    tile.walkable = True
                                    tile.glyph = "."
                                    tile.color = floor_color

                    # Debug logging
                    try:
                        with open("C:/Games/Edgecaster/debug.log", "a") as f:
                            f.write(f"[colosseum_arena] Zone ({zx}, {zy}): spawned {walls_spawned} wall entities\n")
                    except Exception:
                        pass

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
            # Get NPC specs from v2 or legacy format
            npc_specs_to_process = []
            if poi_spec:
                npc_specs_to_process = poi_spec.npc_specs
            elif legacy_poi:
                npc_specs_to_process = getattr(legacy_poi, "npcs", []) or []

            for spec_index, spec in enumerate(npc_specs_to_process):
                # Generate a stable spec key for this NPC to track across zones
                spec_key = f"npc:{spec.npc_id}:{spec_index}"

                # Check if this NPC was already spawned (prevents duplicates in multi-zone POIs)
                if self.poi_registry.is_npc_spawned(pid, spec_key):
                    continue

                # Check if this NPC was killed (don't respawn dead NPCs)
                if self.poi_registry.is_npc_dead(pid, spec_key):
                    continue

                npc_def = npcs.NPC_DEFS.get(spec.npc_id, {})
                name = spec.name or npc_def.get("name", spec.npc_id.title())
                glyph = spec.glyph or npc_def.get("glyph", "@")
                color = spec.color or tuple(npc_def.get("color", (255, 255, 255)))

                # V2 format supports abs_positions; convert to zone-local for spawning
                abs_positions = getattr(spec, "abs_positions", []) or []
                offsets = spec.offsets or [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]

                spawn_pos = None

                # Try ABS positions first (v2 feature)
                # Use FIRST position as canonical spawn - only spawn in that zone
                if abs_positions:
                    zx, zy, _ = coord
                    zone_w = self.cfg.world_width
                    zone_h = self.cfg.world_height
                    # Use only the first abs_position as the canonical spawn point
                    abs_x, abs_y = abs_positions[0]
                    # Convert ABS to zone-local
                    local_x = abs_x - (zx * zone_w)
                    local_y = abs_y - (zy * zone_h)
                    # Only spawn if canonical position is in THIS zone
                    if 0 <= local_x < zone_w and 0 <= local_y < zone_h:
                        spot = nearest_walkable((local_x, local_y))
                        if spot:
                            spawn_pos = spot
                    else:
                        # Canonical position is not in this zone - skip
                        continue

                # Fall back to offsets from entry (legacy behavior)
                if spawn_pos is None and not abs_positions:
                    for dx, dy in offsets:
                        candidate = (entry[0] + dx, entry[1] + dy)
                        spot = nearest_walkable(candidate)
                        if spot:
                            spawn_pos = spot
                            break

                if spawn_pos is None and not abs_positions:
                    spawn_pos = nearest_walkable(entry)
                if spawn_pos is None:
                    continue

                actor = None
                if spec.npc_id == "caged_demon":
                    actor = enemy_factory.spawn_enemy("caged_demon", spawn_pos,abs_pos=self.abs_from_zone_local(self.zone_coord, spawn_pos))
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
                    actor = enemy_factory.spawn_enemy("merchant", spawn_pos,abs_pos=self.abs_from_zone_local(self.zone_coord, spawn_pos))
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
                        abs_pos=self.abs_from_zone_local(self.zone_coord, spawn_pos),
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

                if actor:
                    # Track POI association for content state
                    actor.tags = getattr(actor, "tags", {}) or {}
                    actor.tags["poi_id"] = pid
                    actor.tags["poi_spec_key"] = spec_key

                    level.actors[actor.id] = actor
                    level.entities[actor.id] = actor

                    # Mark as spawned in registry (prevents duplicates)
                    self.poi_registry.mark_npc_spawned(pid, spec_key, actor.id)

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
        """Advance time by delta ticks across the active zone window."""
        try:
            delta = int(delta)
        except Exception:
            delta = int(delta or 0)
        if delta <= 0:
            return

        # Ensure adjacent zones are loaded so movement and AI can cross boundaries.
        active_levels = self._ensure_active_zones_loaded()
        if not active_levels:
            active_levels = [level]

        current_level = self._level()
        for lvl in active_levels:
            apply_player_systems = (lvl is current_level)
            scheduling.advance_time(self, lvl, delta, apply_player_systems=apply_player_systems)

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
        """Heuristic absolute size for rendering.

        We prefer explicit numeric fields (abs_size/base_size/size). If absent, we apply
        a conservative heuristic so that micro-items (berries, crystals, etc.) don't
        persist at macro zoom.
        """
        # Explicit numeric attributes win.
        for attr in ("abs_size", "base_size", "size"):
            v = getattr(obj, attr, None)
            if isinstance(v, (int, float)) and float(v) > 0:
                return float(v)

        # Heuristic fallback by kind / tags.
        kind = getattr(obj, "kind", "") or ""
        if kind == "item":
            return 0.25
        if kind in ("feature", "structure"):
            return 2.0

        tags = getattr(obj, "tags", {}) or {}
        if isinstance(tags, dict):
            if "item_type" in tags:
                return 0.25

        return 1.0

    def _rebuild_spatial_bins(self, level: LevelState) -> None:
        """Rebuild per-level spatial bins for fast camera-rect queries."""
        bs = int(getattr(level, "spatial_bin_size", 16) or 16)
        bs = max(1, bs)
        bins: Dict[Tuple[int, int], List[str]] = {}

        # Include actors (alive) and entities. Some actors may also be in entities; dedupe by id.
        seen: set[str] = set()

        for a in level.actors.values():
            try:
                if not a.alive:
                    continue
            except Exception:
                pass
            if a.id in seen:
                continue
            seen.add(a.id)
            x, y = a.pos
            key = (int(x) // bs, int(y) // bs)
            bins.setdefault(key, []).append(a.id)

        for e in level.entities.values():
            if e.id in seen:
                continue
            seen.add(e.id)
            x, y = e.pos
            key = (int(x) // bs, int(y) // bs)
            bins.setdefault(key, []).append(e.id)

        level.spatial_bins = bins
        level.spatial_dirty = False


    def _clamp_zone_window(
        self,
        zx0: int, zx1: int, zy0: int, zy1: int,
        *,
        zone_span_cap: int | None,
        ccx: float, ccy: float,
        zone_w: int, zone_h: int,
    ) -> tuple[int, int, int, int, bool]:
        """Clamp a zone-window around the camera center to at most zone_span_cap in each dimension.

        This is a **spatial hash convenience only** (NOT ontology). It prevents camera panning / god-vision
        from trying to stage the entire continent in one frame.

        Returns (zx0, zx1, zy0, zy1, was_clamped).
        """
        if not zone_span_cap or zone_span_cap <= 0:
            return zx0, zx1, zy0, zy1, False

        span_x = (zx1 - zx0 + 1)
        span_y = (zy1 - zy0 + 1)
        if span_x <= zone_span_cap and span_y <= zone_span_cap:
            return zx0, zx1, zy0, zy1, False

        try:
            czx = int(float(ccx) // float(zone_w))
            czy = int(float(ccy) // float(zone_h))
        except Exception:
            czx, czy = 0, 0

        half = zone_span_cap // 2

        nzx0 = czx - half
        nzx1 = nzx0 + zone_span_cap - 1
        nzy0 = czy - half
        nzy1 = nzy0 + zone_span_cap - 1

        return int(nzx0), int(nzx1), int(nzy0), int(nzy1), True

    def sync_attention_instantiation(self, abs_rect: tuple[float, float, float, float], *, cam_lod: float) -> None:
        """
        Phase-1 LifecycleManager (Route 2): stage/unstage real entities based on attention.

        - May instantiate/de-instantiate entities as camera pans/zooms (even while paused).
        - Must NOT advance time or apply simulation deltas.
        - Deterministic layout for berry_patch -> berries (cluster mode).
        - Derived children are staged into self.attn_store (ABS-binned), not into zones.
        - Basic persistence (Phase 1): if a spawned berry disappears from the *loaded* zone (picked up),
          we record its slot as harvested on the aggregate so it won't respawn.
        """
        # Remember latest view (used as a safety net before time advances)
        try:
            self._last_view_abs_rect = tuple(map(float, abs_rect))
            self._last_view_cam_lod = float(cam_lod)
        except Exception:
            pass

        try:
            ax0, ay0, ax1, ay1 = map(float, abs_rect)
        except Exception:
            return
        if ax1 < ax0:
            ax0, ax1 = ax1, ax0
        if ay1 < ay0:
            ay0, ay1 = ay1, ay0
        if ax1 == ax0 or ay1 == ay0:
            return

        cfg = getattr(self, "cfg", None)

        # Zone dimensions are still used as a spatial hash (NOT ontology).
        zone_w = int(getattr(cfg, "world_width", 0) or 0)
        zone_h = int(getattr(cfg, "world_height", 0) or 0)
        if zone_w <= 0 or zone_h <= 0:
            try:
                lvl0 = self._level()
                zone_w = int(getattr(lvl0.world, "width", 60) or 60)
                zone_h = int(getattr(lvl0.world, "height", 40) or 40)
            except Exception:
                zone_w = 60
                zone_h = 40

        # Current depth only in Phase 1
        _czx, _czy, zz = getattr(self, "zone_coord", (0, 0, 0))
        zz = int(zz)

        # Warm rect (reuse same idea as renderables_in_abs_rect)
        pad_tiles = float(getattr(cfg, "entity_render_pad_tiles", 0.0) or 0.0)
        if pad_tiles <= 0.0:
            pad_tiles = 0.5 * max((ax1 - ax0), (ay1 - ay0))

        wx0 = ax0 - pad_tiles
        wy0 = ay0 - pad_tiles
        wx1 = ax1 + pad_tiles
        wy1 = ay1 + pad_tiles

        # Camera center (ABS)
        ccx = (ax0 + ax1) * 0.5
        ccy = (ay0 + ay1) * 0.5

        # Union with player abs_pos ONLY if camera is reasonably close to the player.
        # (God-vision / remote panning must not create a giant "bridge" warm rect.)
        try:
            pax, pay = self._get_player_abs()
            dx = float(pax) - float(ccx)
            dy = float(pay) - float(ccy)
            near_thresh = 2.0 * float(pad_tiles)
            if (dx * dx + dy * dy) <= (near_thresh * near_thresh):
                wx0 = min(wx0, float(pax) - pad_tiles)
                wy0 = min(wy0, float(pay) - pad_tiles)
                wx1 = max(wx1, float(pax) + pad_tiles)
                wy1 = max(wy1, float(pay) + pad_tiles)
        except Exception:
            pass

        world_index = getattr(self, "world_entity_index", None)
        if world_index is None:
            return

        # Query macro entities in warm rect (clamped around camera so god-vision doesn't stage the universe)
        max_zone_span = int(getattr(cfg, "render_max_zone_span", 9) or 9)
        max_zone_span = max(1, int(max_zone_span))

        # Compute warm-rect zone window
        zx0 = int(math.floor(wx0 / float(zone_w)))
        zy0 = int(math.floor(wy0 / float(zone_h)))
        zx1 = int(math.floor((wx1 - 1e-9) / float(zone_w)))
        zy1 = int(math.floor((wy1 - 1e-9) / float(zone_h)))

        zx0c, zx1c, zy0c, zy1c, was_clamped = self._clamp_zone_window(
            zx0, zx1, zy0, zy1,
            zone_span_cap=max_zone_span,
            ccx=ccx, ccy=ccy,
            zone_w=zone_w, zone_h=zone_h,
        )

        # Ensure aggregate proxies exist at least for the clamped camera window.
        # (This avoids "only a few patches near player" when panning far away.)
        try:
            self._ensure_world_aggregate_entities(
                zone_w=zone_w,
                zone_h=zone_h,
                zx0=zx0c,
                zx1=zx1c,
                zy0=zy0c,
                zy1=zy1c,
                zz=zz,
                kinds=("berry_patch",),
            )
        except Exception:
            pass

        try:
            refs = world_index.query_abs_rect((wx0, wy0, wx1, wy1), z=zz, zone_span_cap=max_zone_span)
        except Exception:
            return

        partial_knowledge = bool(was_clamped)

        attn_store: AttentionCellStore = getattr(self, "attn_store", None)
        if attn_store is None:
            return

        # -----------------------------
        # A) BERRY PATCH -> BERRIES
        # -----------------------------
        in_scope_aggs: set[str] = set()

        for r in refs:
            ent = getattr(r, "ent", None)
            if ent is None:
                continue

            tags = getattr(ent, "tags", {}) or {}
            kind = str(tags.get("aggregate_kind", "") or getattr(ent, "kind", "") or "")
            if kind != "berry_patch":
                continue

            # Only refine when zoomed in enough
            thresh = float(tags.get("detail_lod_threshold", -1.25))
            if float(cam_lod) > thresh:
                continue

            zc = tuple(getattr(r, "zone_coord", (0, 0, zz)))
            if len(zc) != 3:
                continue

            agg_id = str(getattr(ent, "id", "") or "")
            if not agg_id:
                continue

            in_scope_aggs.add(agg_id)

            slot_to_eid = self._attn_active_agg_children.get(agg_id)
            if not isinstance(slot_to_eid, dict):
                slot_to_eid = {}
                self._attn_active_agg_children[agg_id] = slot_to_eid

            # Harvest persistence stored on the aggregate itself (truthy macro object)
            harvested = getattr(ent, "_agg_harvested_slots", None)
            if not isinstance(harvested, set):
                harvested = set()
                try:
                    setattr(ent, "_agg_harvested_slots", harvested)
                except Exception:
                    pass

            # If something removed a berry from a *loaded zone* (pickup), record its slot harvested.
            # (This keeps existing gameplay interactions working while zones still exist locally.)
            try:
                level = self.get_zone_for_render(zc)
            except Exception:
                level = None
            if level is not None:
                try:
                    for s, eid in list(slot_to_eid.items()):
                        if eid not in level.entities and eid in attn_store.entities:
                            harvested.add(int(s))
                            attn_store.despawn(eid)
                            del slot_to_eid[s]
                except Exception:
                    pass

            # Deterministic child layout (local coords)
            try:
                child_id, pts = aggregate_system.compute_cluster_children_layout(
                    self,
                    aggregate_ent=ent,
                    zone_coord=zc,
                    local_pos=tuple(getattr(r, "local_pos", (0, 0))),
                    zone_w=zone_w,
                    zone_h=zone_h,
                )
            except Exception:
                continue
            if not child_id or not pts:
                continue

            # Resolve child prototype once
            try:
                child_proto = prototypes.resolve_proto(str(child_id))
            except Exception:
                continue

            zx, zy, _z = map(int, zc)

            for slot, (lx, ly) in enumerate(pts):
                if slot in harvested:
                    continue

                ax = int(zx * int(zone_w) + int(lx))
                ay = int(zy * int(zone_h) + int(ly))

                # Keep within warm rect (prevents weird edge-instantiation)
                if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                    continue

                eid = f"{agg_id}:{child_id}:{slot}"
                if eid in attn_store.entities:
                    slot_to_eid[slot] = eid
                    # Mirror into loaded zone if present (enables pickup/look)
                    if level is not None and eid not in level.entities:
                        try:
                            level.entities[eid] = attn_store.entities[eid]
                            level.spatial_dirty = True
                        except Exception:
                            pass
                    continue

                # Spawn real entity (native local coords for its zone coord; ABS for rendering)
                try:
                    bent = spawn_factory.build_entity_from_spec(
                        spec=child_proto,
                        eid=eid,
                        pos=(int(lx), int(ly)),
                        abs_pos=(int(ax), int(ay)),
                        overrides={
                            "tags": {
                                "from_aggregate": agg_id,
                                "aggregate_slot": int(slot),
                                "aggregate_kind": "berry_patch",
                            }
                        },
                    )
                except Exception:
                    continue

                # Stage into attention store (primary)
                try:
                    attn_store.stage(bent, abs_x=ax, abs_y=ay, zz=zz, lineage_id=f"{agg_id}:{child_id}:{slot}")
                except Exception:
                    continue

                # Mirror into loaded zone if present
                if level is not None:
                    try:
                        level.entities[eid] = bent
                        level.spatial_dirty = True
                    except Exception:
                        pass

                slot_to_eid[slot] = eid

        # Evict berries for aggregates no longer in scope.
        # IMPORTANT: only evict when our macro query is complete. If the query was clamped/capped,
        # eviction causes flicker (entities appear for a tick then vanish).
        if not partial_knowledge:
            try:
                for agg_id, slot_map in list(self._attn_active_agg_children.items()):
                    if agg_id in in_scope_aggs:
                        continue
                    if isinstance(slot_map, dict):
                        for _slot, eid in list(slot_map.items()):
                            try:
                                attn_store.despawn(eid)
                            except Exception:
                                pass
                    del self._attn_active_agg_children[agg_id]
            except Exception:
                pass

        
        # -----------------------------
        # B) POI DETAILS (structures + NPC markers)
        #
        # Yoga invariant: POIs exist in ABS space independent of zone loading.
        # Therefore: do NOT rely on a POI's world-index marker being on-screen to
        # resolve its walls/NPCs. Query the POI registry directly by ABS overlap.
        # -----------------------------
        poi_reg = getattr(self, "poi_registry", None)
        if poi_reg is None:
            try:
                poi_reg = get_poi_registry(zone_w=zone_w, zone_h=zone_h)
            except Exception:
                poi_reg = None

        if poi_reg is not None:
            # Query POIs intersecting our warm rect. Prefer registry helper if present.
            try:
                poi_hits = poi_reg.get_in_abs_rect((wx0, wy0, wx1, wy1), z=zz)
            except Exception:
                try:
                    poi_hits = poi_reg.get_in_abs_rect((wx0, wy0, wx1, wy1), depth=zz)
                except Exception:
                    try:
                        poi_hits = poi_reg.get_in_abs_rect((wx0, wy0, wx1, wy1))
                    except Exception:
                        poi_hits = []

            in_scope_pois: set[str] = set()

            # Track active staged children per POI (walls + NPC markers)
            if not hasattr(self, "_attn_active_poi_children"):
                self._attn_active_poi_children = {}  # poi_id -> set[eid]

            for poi_spec in (poi_hits or []):
                try:
                    poi_id = str(getattr(poi_spec, "id", "") or "")
                    if not poi_id:
                        continue
                    in_scope_pois.add(poi_id)

                    poi_tags = getattr(poi_spec, "tags", None) or {}
                    # Only refine when zoomed in enough
                    thresh = float(poi_tags.get("detail_lod_threshold", -0.75))
                    if float(cam_lod) > thresh:
                        continue

                    fp = getattr(poi_spec, "footprint", None)
                    # Some POIs may not have a footprint (legacy); skip cleanly
                    if fp is None:
                        continue

                    active: set[str] = self._attn_active_poi_children.get(poi_id)
                    if not isinstance(active, set):
                        active = set()
                        self._attn_active_poi_children[poi_id] = active
                    desired: set[str] = set()

                    # -----------------------------
                    # B1) Colosseum / arena walls (structure markers)
                    # -----------------------------
                    poi_kind = str(getattr(poi_spec, "kind", "") or "")
                    if "colosseum" in poi_kind.lower() or "arena" in poi_kind.lower():
                        arena_cx = (float(fp.x0) + float(fp.x1)) / 2.0
                        arena_cy = (float(fp.y0) + float(fp.y1)) / 2.0
                        arena_rx = max(1.0, (float(fp.x1) - float(fp.x0)) / 2.0)
                        arena_ry = max(1.0, (float(fp.y1) - float(fp.y0)) / 2.0)

                        wall_thickness = int(poi_tags.get("wall_thickness", 3))
                        wall_thickness = max(1, int(wall_thickness))

                        # Ring thickness in normalized ellipse-space
                        tnorm = float(wall_thickness) / max(arena_rx, arena_ry)
                        inner = max(0.0, 1.0 - 2.0 * tnorm)
                        outer = 1.0 + 0.25 * tnorm

                        # Clip to warm rect ∩ footprint
                        ix0 = max(int(math.floor(wx0)), int(fp.x0))
                        iy0 = max(int(math.floor(wy0)), int(fp.y0))
                        ix1 = min(int(math.ceil(wx1)), int(fp.x1))
                        iy1 = min(int(math.ceil(wy1)), int(fp.y1))

                        for ax in range(ix0, ix1):
                            nx = ((float(ax) + 0.5) - arena_cx) / arena_rx
                            nx2 = nx * nx
                            if nx2 > outer:
                                continue

                            ny2_outer = outer - nx2
                            ny2_inner = inner - nx2
                            if ny2_outer <= 0.0:
                                continue

                            ny_outer = math.sqrt(max(0.0, ny2_outer))
                            ny_inner = math.sqrt(max(0.0, ny2_inner)) if ny2_inner > 0.0 else 0.0

                            y_outer = ny_outer * arena_ry
                            y_inner = ny_inner * arena_ry

                            top0 = int(math.floor(arena_cy + y_inner))
                            top1 = int(math.ceil(arena_cy + y_outer))
                            bot0 = int(math.floor(arena_cy - y_outer))
                            bot1 = int(math.ceil(arena_cy - y_inner))

                            top0 = max(top0, iy0)
                            top1 = min(top1, iy1)
                            bot0 = max(bot0, iy0)
                            bot1 = min(bot1, iy1)

                            for ay in range(top0, top1):
                                eid = f"poi:{poi_id}:wall:{ax}:{ay}"
                                desired.add(eid)
                                if eid not in attn_store.entities:
                                    w = _YogaStagedEntity(
                                        id=eid,
                                        pos=(int(ax), int(ay)),
                                        abs_pos=(int(ax), int(ay)),
                                        kind="structure",
                                        glyph="#",
                                        color=(160, 160, 160),
                                        name="Wall",
                                        tags={"poi": True, "poi_id": poi_id, "structure": "wall"},
                                    )
                                    attn_store.stage(w, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)

                            for ay in range(bot0, bot1):
                                eid = f"poi:{poi_id}:wall:{ax}:{ay}"
                                desired.add(eid)
                                if eid not in attn_store.entities:
                                    w = _YogaStagedEntity(
                                        id=eid,
                                        pos=(int(ax), int(ay)),
                                        abs_pos=(int(ax), int(ay)),
                                        kind="structure",
                                        glyph="#",
                                        color=(160, 160, 160),
                                        name="Wall",
                                        tags={"poi": True, "poi_id": poi_id, "structure": "wall"},
                                    )
                                    attn_store.stage(w, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)

                    # -----------------------------
                    # B2) POI NPCs (REAL actors, staged like berry children)
                    # -----------------------------
                    def _build_poi_actor(
                        *,
                        eid: str,
                        npc_id: str,
                        name: str,
                        glyph: str,
                        color,
                        abs_pos: tuple[int, int],
                        local_pos: tuple[int, int],
                        poi_id: str,
                        ns,
                    ):
                        """Build a real Actor instance for a POI child.

                        Yoga rule: this is a *resolution* product (like berries), not a zone-stamp.
                        """
                        npc_def = npcs.NPC_DEFS.get(npc_id, {}) if "npcs" in globals() else {}

                        # Special cases (matching legacy POI spawning behavior)
                        if npc_id == "caged_demon":
                            try:
                                a = enemy_factory.spawn_enemy("caged_demon", local_pos, abs_pos=abs_pos)
                            except TypeError:
                                a = enemy_factory.spawn_enemy("caged_demon", local_pos)
                                try:
                                    a.abs_pos = abs_pos
                                except Exception:
                                    pass
                            a.id = eid
                            a.pos = local_pos
                            a.faction = "neutral"
                            a.actions = ()
                            a.ai = "idle"
                            a.tags = getattr(a, "tags", {}) or {}
                            a.tags.update({"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id})
                            a.tags["show_exact_hp"] = True
                            try:
                                a.show_exact_hp = True
                            except Exception:
                                pass
                            desc = getattr(ns, "description", None) or npc_def.get("description") or getattr(a, "description", None)
                            if desc:
                                a.description = desc
                            try:
                                a.regen_per_tick = (1, 10)
                                self._start_regen(self.get_zone_for_render((abs_pos[0] // zone_w, abs_pos[1] // zone_h, zz)) or self._level(), a.id, amount=1, interval=10)
                            except Exception:
                                pass
                            # Appearance overrides
                            a.name = name
                            try:
                                a.glyph = glyph
                                a.color = color  # type: ignore[assignment]
                            except Exception:
                                pass
                            return a

                        if npc_id == "merchant":
                            try:
                                a = enemy_factory.spawn_enemy("merchant", local_pos, abs_pos=abs_pos)
                            except TypeError:
                                a = enemy_factory.spawn_enemy("merchant", local_pos)
                                try:
                                    a.abs_pos = abs_pos
                                except Exception:
                                    pass
                            a.id = eid
                            a.pos = local_pos
                            a.faction = "npc"
                            a.actions = ()
                            a.ai = "idle"
                            a.tags = getattr(a, "tags", {}) or {}
                            a.tags.update({"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id})
                            a.tags["merchant_id"] = npc_def.get("merchant_id", "general_store")

                            # Appearance overrides
                            a.name = name
                            try:
                                a.glyph = glyph
                                a.color = color  # type: ignore[assignment]
                            except Exception:
                                pass
                            desc = getattr(ns, "description", None) or npc_def.get("description") or getattr(a, "description", None)
                            if desc:
                                a.description = desc

                            # Ensure merchant system initialized when possible
                            try:
                                from edgecaster.systems import trade as trade_system
                                lvl = self.get_zone_for_render((abs_pos[0] // zone_w, abs_pos[1] // zone_h, zz))
                                if lvl is not None:
                                    trade_system.ensure_merchant_initialized(self, lvl, a)
                            except Exception:
                                pass
                            return a

                        # Default: Human NPC
                        try:
                            a = Human(
                                id=eid,
                                name=name,
                                pos=local_pos,
                                abs_pos=abs_pos,
                                faction="npc",
                                stats=Stats(hp=50, max_hp=50),
                                tags={"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id},
                                disposition=int(npc_def.get("base_disposition", 0) or 0),
                                affiliations=tuple(npc_def.get("factions", [])),
                                glyph=glyph,
                                color=color,  # type: ignore[arg-type]
                            )
                        except TypeError:
                            # In case Human signature differs in some branches
                            a = Human(
                                id=eid,
                                name=name,
                                pos=local_pos,
                                faction="npc",
                                stats=Stats(hp=50, max_hp=50),
                                tags={"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id},
                                disposition=int(npc_def.get("base_disposition", 0) or 0),
                                affiliations=tuple(npc_def.get("factions", [])),
                                glyph=glyph,
                                color=color,  # type: ignore[arg-type]
                            )
                            try:
                                a.abs_pos = abs_pos
                            except Exception:
                                pass

                        desc = getattr(ns, "description", None) or npc_def.get("description")
                        if desc:
                            a.description = desc
                        return a

                    def _mirror_actor_into_loaded_zone(eid: str, actor_obj, abs_pos: tuple[int, int]) -> None:
                        """If the actor's zone is loaded, mirror it into level.actors/entities."""
                        try:
                            zc = (int(abs_pos[0]) // int(zone_w), int(abs_pos[1]) // int(zone_h), int(zz))
                            lvl = self.get_zone_for_render(zc)
                        except Exception:
                            lvl = None
                        if lvl is None:
                            return
                        try:
                            if eid not in lvl.entities:
                                lvl.entities[eid] = actor_obj
                                lvl.spatial_dirty = True
                            if eid not in lvl.actors:
                                lvl.actors[eid] = actor_obj
                                lvl.spatial_dirty = True
                        except Exception:
                            pass

                    try:
                        npc_specs = getattr(poi_spec, "npc_specs", None) or []
                    except Exception:
                        npc_specs = []

                    for ns_i, ns in enumerate(npc_specs):
                        try:
                            npc_id = str(getattr(ns, "npc_id", "") or "")
                            if not npc_id:
                                continue

                            # Prefer explicit abs_positions (v2). If absent, fall back to legacy offsets.
                            abs_positions = list(getattr(ns, "abs_positions", []) or [])
                            if not abs_positions:
                                offsets = list(getattr(ns, "offsets", []) or [])
                                coord = getattr(poi_spec, "coord", None)
                                if offsets and coord is not None:
                                    try:
                                        zx = int(coord[0]); zy = int(coord[1])
                                        base_x = zx * int(zone_w)
                                        base_y = zy * int(zone_h)
                                        abs_positions = [(base_x + int(ox), base_y + int(oy)) for (ox, oy) in offsets]
                                    except Exception:
                                        abs_positions = []

                            if not abs_positions:
                                continue

                            npc_def = npcs.NPC_DEFS.get(npc_id, {}) if "npcs" in globals() else {}
                            glyph = getattr(ns, "glyph", None) or npc_def.get("glyph", "@")
                            color = getattr(ns, "color", None) or npc_def.get("color", (255, 255, 255))
                            name = getattr(ns, "name", None) or npc_def.get("name", npc_id.title())

                            for j, (ax, ay) in enumerate(abs_positions):
                                ax = int(ax); ay = int(ay)

                                # Only instantiate within warm rect
                                if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                                    continue

                                eid = f"poi:{poi_id}:npc:{npc_id}:{j}"
                                desired.add(eid)

                                # If already staged, just ensure it's mirrored into a loaded zone (enables talk/look/etc)
                                if eid in attn_store.entities:
                                    try:
                                        obj = attn_store.entities[eid]
                                        _mirror_actor_into_loaded_zone(eid, obj, (ax, ay))
                                    except Exception:
                                        pass
                                    continue

                                # Compute local pos for the actor's own zone
                                lzx = ax // int(zone_w)
                                lzy = ay // int(zone_h)
                                lx = ax - (lzx * int(zone_w))
                                ly = ay - (lzy * int(zone_h))

                                # Build a real Actor and stage it (like berries)
                                try:
                                    a = _build_poi_actor(
                                        eid=eid,
                                        npc_id=npc_id,
                                        name=str(name),
                                        glyph=str(glyph)[0] if glyph else "@",
                                        color=tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
                                        abs_pos=(ax, ay),
                                        local_pos=(int(lx), int(ly)),
                                        poi_id=poi_id,
                                        ns=ns,
                                    )
                                except Exception:
                                    continue

                                try:
                                    attn_store.stage(a, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)
                                except Exception:
                                    continue

                                _mirror_actor_into_loaded_zone(eid, a, (ax, ay))

                        except Exception:
                            continue

                    # Evict POI children that are no longer desired (for this poi_id)
                    try:
                        # (When query is clamped elsewhere, POI query itself is still complete for this rect.)
                        for eid in list(active):
                            if eid not in desired:
                                try:
                                    obj = attn_store.entities.get(eid)
                                    ap = getattr(obj, "abs_pos", None) if obj is not None else None
                                    if ap:
                                        try:
                                            zc = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                                            lvl = self.get_zone_for_render(zc)
                                        except Exception:
                                            lvl = None
                                        if lvl is not None:
                                            try:
                                                if eid in lvl.entities:
                                                    del lvl.entities[eid]
                                                    lvl.spatial_dirty = True
                                            except Exception:
                                                pass
                                            try:
                                                if eid in lvl.actors:
                                                    del lvl.actors[eid]
                                                    lvl.spatial_dirty = True
                                            except Exception:
                                                pass
                                    attn_store.despawn(eid)
                                except Exception:
                                    pass
                                active.discard(eid)

                    except Exception:
                        pass
                except Exception:
                    continue


            # Evict POI children for POIs no longer in scope (when camera moves away).
            try:
                for poi_id, active in list(self._attn_active_poi_children.items()):
                    if poi_id in in_scope_pois:
                        continue
                    if isinstance(active, set):
                        for eid in list(active):
                            try:
                                obj = attn_store.entities.get(eid)
                                ap = getattr(obj, "abs_pos", None) if obj is not None else None
                                if ap:
                                    try:
                                        zc = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                                        lvl = self.get_zone_for_render(zc)
                                    except Exception:
                                        lvl = None
                                    if lvl is not None:
                                        try:
                                            if eid in lvl.entities:
                                                del lvl.entities[eid]
                                                lvl.spatial_dirty = True
                                        except Exception:
                                            pass
                                        try:
                                            if eid in lvl.actors:
                                                del lvl.actors[eid]
                                                lvl.spatial_dirty = True
                                        except Exception:
                                            pass
                                attn_store.despawn(eid)
                            except Exception:
                                pass

                    del self._attn_active_poi_children[poi_id]
            except Exception:
                pass

        # -----------------------------
        # C) SITE DETAILS (NPCs from SiteRegistry; yoga-style resolution)
        #
        # Procedural "sites" (fishing_village, spriggan_grove, etc.) currently stamp
        # content on zone visit via mapgen_sites. That breaks yoga (camera can't resolve).
        #
        # This block resolves *site NPC children* like berry children:
        # - deterministic from site.seed + npc_pool
        # - staged into attn_store (primary truth)
        # - mirrored into loaded zone if present (enables talk/look)
        # -----------------------------
        try:
            site_reg = getattr(self, "site_registry", None)
        except Exception:
            site_reg = None

        if site_reg is not None:
            # Track active staged children per site_id
            if not hasattr(self, "_attn_active_site_children"):
                self._attn_active_site_children = {}  # site_id -> set[eid]

            # Determine which site zones intersect our warm rect
            szx0 = int(wx0) // int(zone_w)
            szx1 = (int(wx1) - 1) // int(zone_w)
            szy0 = int(wy0) // int(zone_h)
            szy1 = (int(wy1) - 1) // int(zone_h)

            # Player zone (for "in-person even if hidden")
            try:
                pz = getattr(self, "zone_coord", (None, None, zz))
                player_zx, player_zy, _ = (int(pz[0]), int(pz[1]), int(pz[2]))
            except Exception:
                player_zx, player_zy = (None, None)

            in_scope_sites: set[str] = set()

            import random as _random  # local import to avoid file-level churn

            for zxi in range(szx0, szx1 + 1):
                for zyi in range(szy0, szy1 + 1):
                    try:
                        site_specs = list(site_reg.get_at_zone(int(zxi), int(zyi)) or [])
                    except Exception:
                        site_specs = []

                    for site in site_specs:
                        try:
                            site_id = str(getattr(site, "id", "") or "")
                            if not site_id:
                                continue

                            # Gate refinement by zoom, like POIs do
                            # (You can tune this; this is a sane default band.)
                            thresh = -0.75
                            if float(cam_lod) > float(thresh):
                                continue

                            # Yoga: camera observation is sufficient to resolve site details.
                            # Do NOT gate resolution on discovery/zone-visit visibility.
                            # (If we want "hidden sites" later, that should be a *content* rule, not a render gate.)
                            in_scope_sites.add(site_id)


                            active: set[str] = self._attn_active_site_children.get(site_id)
                            if not isinstance(active, set):
                                active = set()
                                self._attn_active_site_children[site_id] = active
                            desired: set[str] = set()

                            # Deterministic RNG from site seed
                            try:
                                seed = int(getattr(site, "seed", 0) or 0)
                            except Exception:
                                seed = 0
                            rng = _random.Random(seed)

                            tags = getattr(site, "tags", {}) or {}
                            npc_pool = list(tags.get("npc_pool", []) or [])
                            if not npc_pool:
                                # Nothing to resolve for this site type (yet)
                                continue

                            # Pick 1-3 NPCs deterministically (bounded by pool size)
                            k = min(len(npc_pool), max(1, rng.randint(1, 3)))
                            chosen = [npc_pool[rng.randrange(0, len(npc_pool))] for _ in range(k)]

                            # Deterministic offsets near zone center (keeps them stable)
                            offsets = [(0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (3, 1), (-3, 1), (1, 3), (-1, -3)]
                            rng.shuffle(offsets)

                            # Zone anchor in ABS
                            zx, zy, zdepth = map(int, getattr(site, "coord", (zxi, zyi, zz)))
                            base_ax = zx * int(zone_w) + (int(zone_w) // 2)
                            base_ay = zy * int(zone_h) + (int(zone_h) // 2)

                            for i, npc_id in enumerate(chosen):
                                try:
                                    npc_id = str(npc_id)
                                    if not npc_id:
                                        continue

                                    ox, oy = offsets[i % len(offsets)]
                                    ax = int(base_ax + ox)
                                    ay = int(base_ay + oy)

                                    # Only instantiate within warm rect
                                    if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                                        continue

                                    eid = f"site:{site_id}:npc:{npc_id}:{i}"
                                    desired.add(eid)

                                    # Already staged? just mirror into loaded zone if available
                                    if eid in attn_store.entities:
                                        try:
                                            obj = attn_store.entities[eid]
                                            _mirror_actor_into_loaded_zone(eid, obj, (ax, ay))
                                        except Exception:
                                            pass
                                        continue

                                    # Compute local coords for the actor's own zone
                                    lzx = ax // int(zone_w)
                                    lzy = ay // int(zone_h)
                                    lx = ax - (lzx * int(zone_w))
                                    ly = ay - (lzy * int(zone_h))

                                    # Reuse the POI actor builder for now (keeps NPC defs consistent)
                                    npc_def = npcs.NPC_DEFS.get(npc_id, {}) if "npcs" in globals() else {}
                                    glyph = npc_def.get("glyph", "@")
                                    color = npc_def.get("color", (255, 255, 255))
                                    name = npc_def.get("name", npc_id.title())

                                    a = _build_poi_actor(
                                        eid=eid,
                                        npc_id=npc_id,
                                        name=str(name),
                                        glyph=str(glyph)[0] if glyph else "@",
                                        color=tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
                                        abs_pos=(ax, ay),
                                        local_pos=(int(lx), int(ly)),
                                        poi_id=f"site:{site_id}",   # piggyback field; tags below disambiguate
                                        ns=None,
                                    )
                                    # Patch tags so downstream can tell it's a site-child, not a POI-child
                                    try:
                                        a.tags = getattr(a, "tags", {}) or {}
                                        a.tags.update({"site": True, "site_id": site_id, "site_npc": True})
                                    except Exception:
                                        pass

                                    attn_store.stage(a, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)
                                    _mirror_actor_into_loaded_zone(eid, a, (ax, ay))

                                except Exception:
                                    continue

                            # Evict site children no longer desired
                            try:
                                for eid in list(active):
                                    if eid not in desired:
                                        try:
                                            obj = attn_store.entities.get(eid)
                                            ap = getattr(obj, "abs_pos", None) if obj is not None else None
                                            if ap:
                                                try:
                                                    zc = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                                                    lvl = self.get_zone_for_render(zc)
                                                except Exception:
                                                    lvl = None
                                                if lvl is not None:
                                                    try:
                                                        if eid in lvl.entities:
                                                            del lvl.entities[eid]
                                                            lvl.spatial_dirty = True
                                                    except Exception:
                                                        pass
                                                    try:
                                                        if eid in lvl.actors:
                                                            del lvl.actors[eid]
                                                            lvl.spatial_dirty = True
                                                    except Exception:
                                                        pass
                                            attn_store.despawn(eid)
                                        except Exception:
                                            pass
                                        active.discard(eid)

                                for eid in desired:
                                    active.add(eid)
                            except Exception:
                                pass

                        except Exception:
                            continue

            # Evict entire site sets that left scope
            try:
                for site_id, active in list(self._attn_active_site_children.items()):
                    if site_id in in_scope_sites:
                        continue
                    if isinstance(active, set):
                        for eid in list(active):
                            try:
                                attn_store.despawn(eid)
                            except Exception:
                                pass
                    del self._attn_active_site_children[site_id]
            except Exception:
                pass



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
        """Return renderable objects intersecting an absolute-world tile rect.

        abs_rect = (x0, y0, x1, y1) in absolute world-tile coordinates.
        Rect is half-open: [x0,x1) × [y0,y1).

        Camera-centric query for god-vision / scry / macro-view.

        Critical invariants:
        - Rendering must not instantiate gameplay state.
        - get_zone_for_render() may return None for unloaded zones (and that's OK).
        - World-index entities (sites, later forests/cities/etc.) must still be queryable
          even when the camera spans many zones.
        """
        try:
            ax0, ay0, ax1, ay1 = map(float, abs_rect)
        except Exception:
            return []

        if ax1 < ax0:
            ax0, ax1 = ax1, ax0
        if ay1 < ay0:
            ay0, ay1 = ay1, ay0
        if ax1 == ax0 or ay1 == ay0:
            return []

        cfg = getattr(self, "cfg", None)

        # Zone dimensions MUST match actual zone tile dims.
        zone_w = int(getattr(cfg, "world_width", 0) or 0)
        zone_h = int(getattr(cfg, "world_height", 0) or 0)
        if zone_w <= 0 or zone_h <= 0:
            try:
                lvl0 = self._level()
                zone_w = int(getattr(lvl0.world, "width", 60) or 60)
                zone_h = int(getattr(lvl0.world, "height", 40) or 40)
            except Exception:
                zone_w = 60
                zone_h = 40

        # Which depth are we on?
        _czx, _czy, zz = getattr(self, "zone_coord", (0, 0, 0))
        zz = int(zz)

        # ------------------------------------------------------------
        # YOGA: attention-driven resolution must run when the camera changes,
        # regardless of *how* the change happened (pan vs zoom vs look).
        #
        # The renderer calls renderables_in_abs_rect every frame; if the camera
        # rect / lod band changed since last frame, sync our attention store now.
        # ------------------------------------------------------------
        try:
            sig = (
                round(float(ax0), 3), round(float(ay0), 3), round(float(ax1), 3), round(float(ay1), 3),
                round(float(cam_lod), 4), int(zz),
            )
            last = getattr(self, "_attn_last_sig", None)
            if sig != last:
                self._attn_last_sig = sig
                try:
                    self.sync_attention_instantiation((ax0, ay0, ax1, ay1), cam_lod=float(cam_lod))
                except Exception:
                    # Never fail rendering because attention sync hiccuped.
                    pass
        except Exception:
            pass


        # ------------------------------------------------------------
        # WARM RECT (IMPORTANT): drive *candidate gathering* from warmth,
        # not from the razor-thin camera rect. This prevents "player disappears
        # when panning a few pixels" due to float/rounding/tight rect edges.
        # ------------------------------------------------------------
        # Default padding in ABS tiles around camera rect (offscreen pursuit / warmth)
        pad_tiles = float(getattr(cfg, "entity_render_pad_tiles", 0.0) or 0.0)
        if pad_tiles <= 0.0:
            try:
                pad_tiles = 0.5 * max((ax1 - ax0), (ay1 - ay0))
            except Exception:
                pad_tiles = 0.0

        wx0 = ax0 - pad_tiles
        wy0 = ay0 - pad_tiles
        wx1 = ax1 + pad_tiles
        wy1 = ay1 + pad_tiles

        # Union with inhabited actor position (keeps the fovea "warm" even if camera pans slightly)
        pax = pay = None
        try:
            pax, pay = self._get_player_abs()
            wx0 = min(wx0, float(pax) - pad_tiles)
            wy0 = min(wy0, float(pay) - pad_tiles)
            wx1 = max(wx1, float(pax) + pad_tiles)
            wy1 = max(wy1, float(pay) + pad_tiles)
        except Exception:
            pax = pay = None

        # ------------------------------------------------------------
        # Zone span covered by *warm rect* (NOT raw camera rect)
        # ------------------------------------------------------------
        zx0 = int(math.floor(wx0 / float(zone_w)))
        zy0 = int(math.floor(wy0 / float(zone_h)))
        zx1 = int(math.floor((wx1 - 1e-9) / float(zone_w)))
        zy1 = int(math.floor((wy1 - 1e-9) / float(zone_h)))

        max_zone_span = int(getattr(cfg, "render_max_zone_span", 9) or 9)
        max_zone_span = max(1, max_zone_span)
        span_x = (zx1 - zx0 + 1)
        span_y = (zy1 - zy0 + 1)
        too_many_zones = (span_x > max_zone_span) or (span_y > max_zone_span)

        out: List[RenderProxy] = []
        candidates: List[Tuple[object, float, float, Tuple[int, int, int], Tuple[int, int], float]] = []

        # Camera center for scoring (raw camera center, not warm center)
        ccx = 0.5 * (ax0 + ax1)
        ccy = 0.5 * (ay0 + ay1)

        def _score(abs_size: float, abs_x: float, abs_y: float) -> float:
            dx = abs_x - ccx
            dy = abs_y - ccy
            return abs_size * 1000.0 - (dx * dx + dy * dy)

        # ------------------------------------------------------------
        # 1) WORLD INDEX ENTITIES (sites, POIs, later forests/cities/etc.)
        # ------------------------------------------------------------
        self._ensure_world_site_entities(zone_w=zone_w, zone_h=zone_h)
        self._ensure_world_poi_entities(zone_w=zone_w, zone_h=zone_h)

        # Always ensure aggregate proxies for a **clamped camera window**.
        # This keeps god-vision panning responsive while still allowing distant areas
        # to resolve aggregates when the camera is there.
        try:
            zx0c, zx1c, zy0c, zy1c, _clamped = self._clamp_zone_window(
                zx0, zx1, zy0, zy1,
                zone_span_cap=max_zone_span,
                ccx=ccx, ccy=ccy,
                zone_w=zone_w, zone_h=zone_h,
            )
            self._ensure_world_aggregate_entities(
                zone_w=zone_w,
                zone_h=zone_h,
                zx0=zx0c,
                zx1=zx1c,
                zy0=zy0c,
                zy1=zy1c,
                zz=zz,
                kinds=("berry_patch",),
            )
        except Exception:
            pass


        # Query world index using WARM rect so panning doesn't "drop" things on the edge.
        try:
            if getattr(self, "world_entity_index", None) is not None:
                for ref in self.world_entity_index.query_abs_rect((wx0, wy0, wx1, wy1), z=zz, zone_span_cap=None):
                    obj = ref.ent
                    zx, zy, _z = ref.zone_coord
                    ox, oy = ref.local_pos

                    abs_x = float(zx * zone_w + ox)
                    abs_y = float(zy * zone_h + oy)

                    abs_size = self._size_for_render(obj)
                    ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                    delta = float(cam_lod) - float(ent_lod)

                    if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                        continue

                    # Intersection test against WARM rect (gather candidates), not camera rect.
                    half = 0.5 * float(abs_size)
                    ex0 = abs_x - half
                    ey0 = abs_y - half
                    ex1 = abs_x + half
                    ey1 = abs_y + half
                    if ex1 <= wx0 or ex0 >= wx1 or ey1 <= wy0 or ey0 >= wy1:
                        continue

                    sc = _score(abs_size, abs_x, abs_y)

                    # Render-only detail proxies for aggregates when zoomed in.
                    # NOTE (Yoga): detail proxies are disabled.
                    # If berries/soldiers/etc. are visible at a given band, they must be real entities
                    # staged into LevelState.entities by the attention lifecycle (not invented here).
                    pass

                    candidates.append((obj, abs_x, abs_y, (int(zx), int(zy), int(zz)), (int(ox), int(oy)), sc))
        except Exception:
            pass

        
        # ------------------------------------------------------------
        # 1.5) ATTENTION-STAGED ENTITIES (Route 2)
        # ------------------------------------------------------------
        try:
            attn_store = getattr(self, "attn_store", None)
            if attn_store is not None and include_entities:
                for obj, abs_x, abs_y in attn_store.query_abs_rect((wx0, wy0, wx1, wy1), zz=zz):
                    abs_size = self._size_for_render(obj)
                    ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                    delta = float(cam_lod) - float(ent_lod)

                    if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                        continue

                    sc = _score(abs_size, abs_x, abs_y)

                    # Derive zone/local purely for renderer convenience.
                    zx = int(math.floor(abs_x / float(zone_w)))
                    zy = int(math.floor(abs_y / float(zone_h)))
                    ox = int(abs_x - zx * zone_w)
                    oy = int(abs_y - zy * zone_h)

                    candidates.append((obj, float(abs_x), float(abs_y), (int(zx), int(zy), int(zz)), (int(ox), int(oy)), sc))
        except Exception:
            pass


        # ------------------------------------------------------------
        # 2) LOADED ZONES ONLY (never instantiate zones here)
        # ------------------------------------------------------------
        coords_to_check: List[Tuple[int, int, int]] = []

        if too_many_zones:
            # Camera spans many zones; do NOT iterate them all.
            # But do render entities from zones already in memory that intersect WARM rect.
            try:
                for coord in getattr(self, "levels", {}).keys():
                    try:
                        zx, zy, zdepth = coord
                    except Exception:
                        continue
                    if int(zdepth) != int(zz):
                        continue

                    zax0 = float(zx * zone_w)
                    zay0 = float(zy * zone_h)
                    zax1 = zax0 + float(zone_w)
                    zay1 = zay0 + float(zone_h)
                    if zax1 <= wx0 or zax0 >= wx1 or zay1 <= wy0 or zay0 >= wy1:
                        continue

                    coords_to_check.append((int(zx), int(zy), int(zz)))
            except Exception:
                coords_to_check = []
        else:
            for zx in range(zx0, zx1 + 1):
                for zy in range(zy0, zy1 + 1):
                    coords_to_check.append((int(zx), int(zy), int(zz)))

        for coord in coords_to_check:
            zx, zy, _ = coord
            try:
                level = self.get_zone_for_render(coord)
            except Exception:
                continue
            if level is None:
                continue

            if getattr(level, "spatial_dirty", True) or not getattr(level, "spatial_bins", None):
                self._rebuild_spatial_bins(level)

            # Local rect within this zone for WARM rect.
            lx0 = wx0 - float(zx * zone_w)
            ly0 = wy0 - float(zy * zone_h)
            lx1 = wx1 - float(zx * zone_w)
            ly1 = wy1 - float(zy * zone_h)

            if lx1 <= 0 or ly1 <= 0 or lx0 >= zone_w or ly0 >= zone_h:
                continue
            lx0 = max(0.0, min(float(zone_w), lx0))
            ly0 = max(0.0, min(float(zone_h), ly0))
            lx1 = max(0.0, min(float(zone_w), lx1))
            ly1 = max(0.0, min(float(zone_h), ly1))
            if lx1 <= lx0 or ly1 <= ly0:
                continue

            bs = int(getattr(level, "spatial_bin_size", 16) or 16)
            bs = max(1, bs)
            bx0 = int(math.floor(lx0 / bs))
            by0 = int(math.floor(ly0 / bs))
            bx1 = int(math.floor((lx1 - 1e-6) / bs))
            by1 = int(math.floor((ly1 - 1e-6) / bs))

            bins = level.spatial_bins
            seen_ids: set[str] = set()

            for by in range(by0, by1 + 1):
                for bx in range(bx0, bx1 + 1):
                    ids = bins.get((bx, by))
                    if not ids:
                        continue
                    for obj_id in ids:
                        if obj_id in seen_ids:
                            continue
                        seen_ids.add(obj_id)

                        obj = None
                        if include_actors:
                            obj = level.actors.get(obj_id)
                        if obj is None and include_entities:
                            obj = level.entities.get(obj_id)
                        if obj is None:
                            continue

                        try:
                            ox, oy = obj.pos
                        except Exception:
                            continue
                        if not (lx0 <= ox < lx1 and ly0 <= oy < ly1):
                            continue

                        abs_x = float(zx * zone_w + ox)
                        abs_y = float(zy * zone_h + oy)

                        abs_size = self._size_for_render(obj)
                        ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                        delta = float(cam_lod) - float(ent_lod)
                        if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                            continue

                        sc = _score(abs_size, abs_x, abs_y)
                        candidates.append((obj, abs_x, abs_y, coord, (int(ox), int(oy)), sc))

        if not candidates:
            return []

        # ------------------------------------------------------------
        # Banded multi-scale attention selection
        # ------------------------------------------------------------
        band_width = int(getattr(cfg, "entity_band_width", 4) or 4)
        band_width = max(1, band_width)

        bucket_slack = int(getattr(cfg, "entity_bucket_slack", 3) or 3)

        k_cell = int(getattr(cfg, "entity_render_k_cell", 12) or 12)
        k_cell = max(1, k_cell)

        k_layer = int(getattr(cfg, "entity_render_k_layer", max_count) or max_count)
        if k_layer <= 0:
            k_layer = max_count

        # Band overlap allows adjacent bands to still render (prevents "sites vanish entirely")
        band_overlap = int(getattr(cfg, "entity_band_overlap", 1) or 1)
        band_overlap = max(0, band_overlap)

        cam_lod_f = float(cam_lod)

        # Bias lets you zoom in closer before band transitions.
        zoom_in_bias = float(getattr(cfg, "entity_zoom_in_bias_lod", 3.0) or 3.0)
        cam_lod_f += zoom_in_bias

        # Optional additional band bias (simple constant nudge).
        band_bias = float(getattr(cfg, "entity_band_bias", 0.0) or 0.0)
        cam_lod_f += band_bias

        if not hasattr(self, "_entity_active_band"):
            self._entity_active_band = int(math.floor(cam_lod_f / float(band_width)))

        b = int(getattr(self, "_entity_active_band", 0) or 0)

        # Less sticky hysteresis by default.
        h = float(getattr(cfg, "entity_band_hysteresis", 0.05) or 0.05)

        if cam_lod_f > ((b + 1) * band_width + h):
            b = int(math.floor(cam_lod_f / float(band_width)))
        elif cam_lod_f < (b * band_width - h):
            b = int(math.floor(cam_lod_f / float(band_width)))

        self._entity_active_band = b

        lod_min = b * band_width
        lod_max = lod_min + (band_width - 1)

        cell_lod_min = lod_min + bucket_slack
        cell_lod_max = lod_max + bucket_slack

        def _ent_lod(sz: float) -> float:
            return math.log2(sz) if sz and sz > 0.0 else -30.0

        # Bucket candidates by native cell.
        cell_map = {}
        for obj, ex, ey, coord, local_pos, _sc in candidates:
            try:
                abs_size = float(self._size_for_render(obj))
            except Exception:
                abs_size = 1.0

            e_lod = _ent_lod(abs_size)
            e_band = int(math.floor(e_lod / float(band_width)))

            # Allow overlap bands around active band.
            if abs(e_band - b) > band_overlap:
                continue

            try:
                native_cell_lod = int(math.floor(e_lod)) + int(bucket_slack)
            except Exception:
                native_cell_lod = cell_lod_min

            if native_cell_lod < cell_lod_min:
                native_cell_lod = cell_lod_min
            elif native_cell_lod > cell_lod_max:
                native_cell_lod = cell_lod_max

            cell_size = float(2 ** int(native_cell_lod))
            cx = int(math.floor(float(ex) / cell_size))
            cy = int(math.floor(float(ey) / cell_size))

            key = (int(native_cell_lod), cx, cy)
            cell_map.setdefault(key, []).append((obj, float(ex), float(ey), coord, local_pos, abs_size))

        if not cell_map:
            return []

        selected = []

        # Iterate cell LODs for this band
        for cell_lod in range(int(cell_lod_min), int(cell_lod_max) + 1):
            cell_size = float(2 ** int(cell_lod))

            # Hot cells from WARM rect
            cx0 = int(math.floor(wx0 / cell_size))
            cy0 = int(math.floor(wy0 / cell_size))
            cx1 = int(math.floor((wx1 - 1e-9) / cell_size))
            cy1 = int(math.floor((wy1 - 1e-9) / cell_size))

            hot_keys = []
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    key = (int(cell_lod), cx, cy)
                    if key not in cell_map:
                        continue
                    ccx_cell = (float(cx) + 0.5) * cell_size
                    ccy_cell = (float(cy) + 0.5) * cell_size
                    dx = ccx_cell - ccx
                    dy = ccy_cell - ccy
                    hot_keys.append((dx * dx + dy * dy, key))

            hot_keys.sort(key=lambda t: t[0])

            for _d2, key in hot_keys:
                ents = cell_map.get(key, [])
                if not ents:
                    continue

                def _rank(rec):
                    _obj, _x, _y, _coord, _lp, _sz = rec
                    dx = _x - ccx
                    dy = _y - ccy
                    return (dx * dx + dy * dy, id(_obj))

                ents.sort(key=_rank)
                for rec in ents[:k_cell]:
                    selected.append(rec)

                if k_layer > 0 and len(selected) >= k_layer:
                    break

            if k_layer > 0 and len(selected) >= k_layer:
                break

        # ------------------------------------------------------------
        # YOGA INVARIANT: EVERYTHING ONSCREEN MUST RENDER.
        #
        # k_cell / k_layer / max_count are *attention budgets* and may
        # cull OFFSCREEN (warm padding) candidates, but they must never
        # hide entities that are actually inside the raw camera rect.
        #
        # Otherwise dense piles (e.g. item_depot) get arbitrarily clipped
        # by k_cell because many items share the same native bucket cell.
        # ------------------------------------------------------------
        onscreen: list[tuple[object, float, float, tuple[int, int, int], tuple[int, int], float]] = []
        onscreen_ids: set[object] = set()

        def _obj_key(o: object) -> object:
            # Prefer stable entity ids if present; fallback to object identity.
            oid = getattr(o, "id", None)
            return oid if oid is not None else id(o)

        # NOTE: candidates are already LoD-filtered (delta vs dmin/dmax/fade_w),
        # so we only enforce geometric inclusion here.
        for obj, ex, ey, coord, local_pos, _sc in candidates:
            if ax0 <= float(ex) < ax1 and ay0 <= float(ey) < ay1:
                try:
                    abs_size = float(self._size_for_render(obj))
                except Exception:
                    abs_size = 1.0
                rec = (obj, float(ex), float(ey), coord, local_pos, abs_size)
                key = _obj_key(obj)
                if key in onscreen_ids:
                    continue
                onscreen_ids.add(key)
                onscreen.append(rec)

        # Merge: onscreen first, then the budget-selected offscreen/warm set.
        merged: list[tuple[object, float, float, tuple[int, int, int], tuple[int, int], float]] = []
        merged.extend(onscreen)

        for rec in selected:
            obj = rec[0]
            if _obj_key(obj) in onscreen_ids:
                continue
            merged.append(rec)

        # Apply max_count ONLY to the warm/offscreen remainder.
        # Never truncate onscreen.
        if max_count > 0 and len(merged) > max_count:
            keep_n = max_count - len(onscreen)
            if keep_n <= 0:
                merged = onscreen
            else:
                merged = onscreen + merged[len(onscreen) : len(onscreen) + keep_n]

        selected = merged


        # Hard guarantee: include player if we can find it.
        # (Not a "render special-case"; it's an observer invariant.)
        try:
            p = getattr(self, "player", None)
            if p is not None:
                pid = getattr(p, "id", None)
                if pid is not None:
                    has_player = any(getattr(rec[0], "id", None) == pid for rec in selected)
                    if not has_player and pax is not None and pay is not None:
                        # If player was in candidates, it would've been selected; but if not,
                        # include it anyway to satisfy "fovea never disappears".
                        # Compute correct local_pos (not 0,0!) for tile lookup in rendering.
                        try:
                            p_zone_coord, p_local = self.zone_local_from_abs((int(pax), int(pay)), depth=zz)
                            p_local_pos = (int(p_local[0]), int(p_local[1]))
                        except Exception:
                            # Fallback: use player.pos if abs conversion fails
                            p_zone_coord = getattr(self, "zone_coord", (0, 0, zz))
                            p_local_pos = getattr(p, "pos", (0, 0))
                        # Debug: log when player fallback is triggered
                        with open("C:/Games/Edgecaster/debug.log", "a") as f:
                            f.write(f"[RenderSelect] PLAYER FALLBACK: abs=({pax},{pay}), zone={p_zone_coord}, local={p_local_pos}, not in {len(selected)} candidates\n")
                        selected.append((p, float(pax), float(pay), p_zone_coord, p_local_pos, 1.0))
        except Exception:
            pass

        for obj, abs_x, abs_y, coord, local_pos, _abs_size in selected:
            out.append(
                RenderProxy(
                    obj=obj,
                    abs_x=float(abs_x),
                    abs_y=float(abs_y),
                    zone_coord=coord,
                    local_pos=local_pos,
                )
            )

        return out


    
    def _ensure_world_site_entities(self, *, zone_w: int, zone_h: int) -> None:
        """
        Incrementally build world-level site entities (macro renderables) from the SiteRegistry.

        Important properties:
        - No gameplay side effects. Does NOT stamp walls/NPCs/etc.
        - Does NOT wait for async placement completion.
        - Not "build once": it incrementally adds newly-placed or newly-revealed sites.
        """

        # ---------------------------------------------------------------------
        # Leviathan (single world-scale test entity)
        # Declarative, idempotent, no flags
        # ---------------------------------------------------------------------

        # Hard-wired "spec" (exactly like a site spec, just inline for now)
        leviathan_spec = {
            "id": "world:leviathan",
            "hotspot_index": 0,  # east corruption patch
        }

        try:
            # Authoritative data
            hotspots = list(getattr(self, "corruption_hotspots", []) or [])
            grid = getattr(self, "tile_julia_grid", None)

            if hotspots and isinstance(grid, dict):
                jx, jy, *_ = hotspots[leviathan_spec["hotspot_index"]]

                # Julia → ABS tile conversion (linear grid inversion)
                view_min_jx = float(grid["view_min_jx"])
                view_min_jy = float(grid["view_min_jy"])
                step_x = float(grid["step_x"])
                step_y = float(grid["step_y"])
                total_x = int(grid["total_x"])
                total_y = int(grid["total_y"])

                ax = int(round((float(jx) - view_min_jx) / step_x))
                ay = int(round((float(jy) - view_min_jy) / step_y))
                ax = max(0, min(total_x - 1, ax))
                ay = max(0, min(total_y - 1, ay))

                # ABS → zone + local
                zx = ax // zone_w
                zy = ay // zone_h
                zz = 0
                ox = ax % zone_w
                oy = ay % zone_h

                # Build entity from existing prototype
                levi_proto = prototypes.resolve_proto("leviathan")

                ent = spawn_factory.build_entity_from_spec(
                    spec=levi_proto,
                    eid=leviathan_spec["id"],
                    pos=(ox, oy),
                    overrides={
                        "kind": "feature",  # world-scale renderable, like sites
                        "tags": {
                            "world_entity": True,
                            "leviathan": True,
                            "corruption_source": True,
                        },
                    },
                )

                # Idempotent add (WorldEntityIndex de-dupes by ID)
                self.world_entity_index.add(
                    ent,
                    zone_coord=(zx, zy, zz),
                    local_pos=(ox, oy),
                )

        except Exception:
            pass


        # Initialize tracking
        if not hasattr(self, "_world_site_ids_built"):
            self._world_site_ids_built = set()

        # If zone dims changed, rebuild the index (safe: we can re-add incrementally)
        prev_wh = getattr(self, "_world_entity_index_wh", None)
        wh = (int(zone_w), int(zone_h))
        if prev_wh != wh or getattr(self, "world_entity_index", None) is None:
            try:
                self.world_entity_index = WorldEntityIndex(zone_w=wh[0], zone_h=wh[1])
                self._world_entity_index_wh = wh
                self._world_site_ids_built.clear()
                # If the index is rebuilt, POI/world proxies must be re-added too.
                try:
                    if hasattr(self, "_world_poi_ids_built"):
                        self._world_poi_ids_built.clear()
                except Exception:
                    pass
            except Exception:
                return

        # Grab site specs. In god vision: all sites that exist so far.
        # In normal: only visible/discovered sites.
        try:
            if bool(getattr(self, "god_vision", False)):
                specs = list(getattr(self.site_registry, "_sites", {}).values())
            else:
                specs = list(self.site_registry.get_visible())
        except Exception:
            return

        if not specs:
            return

        site_types = load_site_types()

        # Resolve settlement prototype once
        try:
            settlement_proto = prototypes.resolve_proto("settlement")
        except Exception:
            settlement_proto = {
                "id": "settlement",
                "name": "Settlement",
                "glyph": "§",
                "color": [240, 220, 160],
                "kind": "feature",
                "base_size": 64,
                "tags": {},
            }

        new_count = 0

        for s in specs:
            try:
                sid = getattr(s, "id", None) or f"{getattr(s,'coord',(0,0,0))}"
                eid = f"site:{sid}"
                if eid in self._world_site_ids_built:
                    continue

                zx, zy, zz = map(int, getattr(s, "coord", (0, 0, 0)))
                cfg = site_types.get(getattr(s, "kind", ""), None)

                name = getattr(cfg, "name", None) or getattr(s, "kind", "site")
                glyph = getattr(cfg, "map_glyph", "?") if cfg else "?"
                color = list(getattr(cfg, "map_color", (255, 255, 255))) if cfg else [255, 255, 255]

                # Zone-center for now (later: intra-zone location from spec if you add it)
                ox = int(zone_w // 2)
                oy = int(zone_h // 2)

                overrides = {
                    "name": name,
                    "glyph": glyph,
                    "color": color,
                    "kind": "feature",
                    "base_size": 64,
                    "tags": {
                        "world_entity": True,
                        "site": True,
                        "site_id": getattr(s, "id", ""),
                        "site_kind": getattr(s, "kind", ""),
                        "site_seed": int(getattr(s, "seed", 0) or 0),
                        "site_biome": str(getattr(s, "biome", "")),
                        **(getattr(s, "tags", {}) or {}),
                    },
                }

                ent = spawn_factory.build_entity_from_spec(
                    spec=settlement_proto,
                    eid=eid,
                    pos=(ox, oy),
                    overrides=overrides,
                )

                self.world_entity_index.add(ent, zone_coord=(zx, zy, zz), local_pos=(ox, oy))
                self._world_site_ids_built.add(eid)
                new_count += 1

            except Exception:
                continue

        if new_count and hasattr(self, "_debug"):
            self._debug(f"[world_entities] added {new_count} site proxies (total={len(self._world_site_ids_built)})")

    def _ensure_world_poi_entities(self, *, zone_w: int, zone_h: int) -> None:
        """Incrementally build world-level POI entities (macro renderables) from the POIRegistry.

        This creates visible markers for v2-style POIs (like ancient_colosseum) that have
        show_on_map=True in their tags, allowing them to appear on the normal dungeon map
        at appropriate zoom levels.

        Important properties:
        - No gameplay side effects. Does NOT stamp walls/NPCs/etc.
        - Incrementally adds newly-placed or newly-discovered POIs.
        """
        # Initialize tracking
        if not hasattr(self, "_world_poi_ids_built"):
            self._world_poi_ids_built = set()

        # Get POI registry
        poi_reg = getattr(self, "poi_registry", None)
        if poi_reg is None:
            return

        # If zone dims changed, clear tracking
        prev_wh = getattr(self, "_world_poi_entity_wh", None)
        wh = (int(zone_w), int(zone_h))
        if prev_wh != wh:
            self._world_poi_ids_built.clear()
            self._world_poi_entity_wh = wh

        # Ensure world_entity_index exists
        if getattr(self, "world_entity_index", None) is None:
            return

        # Resolve settlement prototype for POI markers
        try:
            settlement_proto = prototypes.resolve_proto("settlement")
        except Exception:
            settlement_proto = {
                "id": "settlement",
                "name": "Settlement",
                "glyph": "§",
                "color": [240, 220, 160],
                "kind": "feature",
                "base_size": 64,
                "tags": {},
            }

        new_count = 0

        for poi in poi_reg:
            try:
                poi_id = poi.id
                eid = f"poi:{poi_id}"
                if eid in self._world_poi_ids_built:
                    continue

                # Only show POIs with show_on_map=True
                tags = poi.tags or {}
                if not tags.get("show_on_map", False):
                    continue

                # Get POI display properties
                name = poi.name or poi.kind or "Unknown"
                glyph = tags.get("map_glyph", "?")
                color_raw = tags.get("map_color", [200, 200, 200])
                if isinstance(color_raw, (list, tuple)) and len(color_raw) >= 3:
                    color = [int(color_raw[0]), int(color_raw[1]), int(color_raw[2])]
                else:
                    color = [200, 200, 200]

                # Get anchor position in ABS coordinates
                anchor_x, anchor_y = poi.anchor_abs
                depth = poi.depth

                # Convert to zone + local position
                zx = anchor_x // zone_w
                zy = anchor_y // zone_h
                ox = anchor_x % zone_w
                oy = anchor_y % zone_h

                # Calculate a reasonable base_size based on footprint
                footprint = poi.footprint
                poi_width = footprint.x1 - footprint.x0
                poi_height = footprint.y1 - footprint.y0
                base_size = max(poi_width, poi_height, 64)

                overrides = {
                    "name": name,
                    "glyph": glyph,
                    "color": color,
                    "kind": "feature",
                    "base_size": base_size,
                    "tags": {
                        "world_entity": True,
                        "poi": True,
                        "poi_id": poi_id,
                        "poi_kind": poi.kind,
                        **tags,
                    },
                }

                ent = spawn_factory.build_entity_from_spec(
                    spec=settlement_proto,
                    eid=eid,
                    pos=(ox, oy),
                    overrides=overrides,
                )

                self.world_entity_index.add(ent, zone_coord=(zx, zy, depth), local_pos=(ox, oy))
                self._world_poi_ids_built.add(eid)
                new_count += 1

            except Exception:
                continue

        if new_count and hasattr(self, "_debug"):
            self._debug(f"[world_entities] added {new_count} POI proxies (total={len(self._world_poi_ids_built)})")


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
        """Ensure aggregate world entities exist in WorldEntityIndex (no gameplay side effects).

        General-purpose mechanism used for berry patches today; goblin bands / forests / armies tomorrow.
        """
        # If the world_entity_index was rebuilt (e.g. zone dims changed), aggregates must be re-added.
        wh = (int(zone_w), int(zone_h))
        prev = getattr(self, "_agg_world_entity_index_wh", None)
        if prev != wh:
            try:
                # Clear incremental generation tracking so we can repopulate the fresh index.
                if hasattr(self, "_agg_worldgen_done"):
                    self._agg_worldgen_done.clear()
            except Exception:
                pass
            self._agg_world_entity_index_wh = wh

        aggregate_system.ensure_world_aggregates(
            self,
            zone_w=int(zone_w),
            zone_h=int(zone_h),
            zx0=int(zx0),
            zx1=int(zx1),
            zy0=int(zy0),
            zy1=int(zy1),
            zz=int(zz),
            kinds=kinds,
        )

    def _realize_aggregate_details_in_zone(self, level: "LevelState", coord: Tuple[int, int, int], kinds=None) -> None:
        """When a zone is created/entered (simulation allowed), realize aggregate details into real entities."""
        cfg = getattr(self, "cfg", None)
        zone_w = int(getattr(cfg, "world_width", 60) or 60)
        zone_h = int(getattr(cfg, "world_height", 40) or 40)

        # Ensure aggregates for this bucket exist in the world index first.
        self._ensure_world_aggregate_entities(
            zone_w=zone_w,
            zone_h=zone_h,
            zx0=int(coord[0]),
            zx1=int(coord[0]),
            zy0=int(coord[1]),
            zy1=int(coord[1]),
            zz=int(coord[2]),
            kinds=kinds,
        )

        aggregate_system.realize_details_for_loaded_zone(
            self,
            level,
            zone_coord=(int(coord[0]), int(coord[1]), int(coord[2])),
            zone_w=zone_w,
            zone_h=zone_h,
            kinds=kinds,
        )



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


    def describe_abs_tile_at(self, abs_pos: Tuple[int, int], *, cam_lod: float | None = None) -> str:
        """
        Describe an ABS tile that may be outside the currently loaded zone.

        For now:
        - If it's in a loaded zone, delegate to describe_tile_at(local).
        - Otherwise, fall back to world-index entities or a distant-terrain line.
        """
        try:
            zone, local = self.zone_local_from_abs(abs_pos, depth=getattr(self, "zone_coord", (0, 0, 0))[2], clamp_to_world=True)
        except Exception:
            zone, local = None, None

        if zone is not None and local is not None:
            if zone == getattr(self, "zone_coord", None):
                return self.describe_tile_at((int(local[0]), int(local[1])))

            # If this zone is already loaded, describe the local tile there.
            try:
                lvl = self.get_zone_for_render(zone)
            except Exception:
                lvl = None
            if lvl is not None:
                return self.describe_tile_at((int(local[0]), int(local[1])), level=lvl, zone_coord=zone)

        zx, zy, _zz = zone if zone is not None else ("?", "?", "?")
        ax, ay = int(abs_pos[0]), int(abs_pos[1])

        # If we have macro entities (POIs, walls, etc.) in the world index,
        # try to describe the best-matching one by LOD.
        try:
            if cam_lod is None:
                cam_lod = 0.0
            dmin = float(getattr(self, "entity_lod_delta_min", -5.0))
            dmax = float(getattr(self, "entity_lod_delta_max", 3.0))
            fade_w = float(getattr(self, "entity_lod_fade_width", 0.45))

            def _lod_delta(abs_size: float) -> float | None:
                ent_lod = math.log2(max(1e-12, abs_size))
                delta = float(cam_lod) - ent_lod
                if delta < dmin - fade_w or delta > dmax + fade_w:
                    return None
                return delta

            def _intersects_tile(abs_x: float, abs_y: float, abs_size: float) -> bool:
                half = 0.5 * float(abs_size)
                ex0 = abs_x - half
                ey0 = abs_y - half
                ex1 = abs_x + half
                ey1 = abs_y + half
                return not (ex1 <= ax or ex0 >= ax + 1 or ey1 <= ay or ey0 >= ay + 1)

            candidates: list[tuple[object, float]] = []
            zz = int(getattr(self, "zone_coord", (0, 0, 0))[2])

            world_index = getattr(self, "world_entity_index", None)
            if world_index is not None:
                for ref in world_index.query_abs_rect((ax, ay, ax + 1, ay + 1), z=zz, zone_span_cap=1):
                    obj = ref.ent
                    zx0, zy0, _z = ref.zone_coord
                    ox, oy = ref.local_pos
                    abs_x = float(zx0) * float(world_index.zone_w) + float(ox)
                    abs_y = float(zy0) * float(world_index.zone_h) + float(oy)
                    abs_size = self._size_for_render(obj)
                    if not _intersects_tile(abs_x, abs_y, abs_size):
                        continue
                    delta = _lod_delta(abs_size)
                    if delta is None:
                        continue
                    candidates.append((obj, float(delta)))

            attn_store = getattr(self, "attn_store", None)
            if attn_store is not None:
                for obj, abs_x, abs_y in attn_store.query_abs_rect((ax, ay, ax + 1, ay + 1), zz=zz):
                    abs_size = self._size_for_render(obj)
                    if not _intersects_tile(float(abs_x), float(abs_y), abs_size):
                        continue
                    delta = _lod_delta(abs_size)
                    if delta is None:
                        continue
                    candidates.append((obj, float(delta)))

            if candidates:
                # Prefer entities whose LOD best matches the camera.
                tol = float(getattr(self, "look_lod_tolerance", 0.75))
                min_delta = min(abs(d) for _, d in candidates)
                filtered = [obj for obj, d in candidates if abs(d) <= min_delta + tol]

                if filtered:
                    try:
                        from edgecaster.systems.actions import describe_entity_for_look
                    except Exception:
                        describe_entity_for_look = None  # type: ignore[assignment]
                    ent = filtered[0]
                    if describe_entity_for_look is not None:
                        info = describe_entity_for_look(ent)
                        glyph = info.get("glyph", "?")
                        desc = info.get("description", "") or "You see nothing remarkable about it."
                        lines = [str(glyph), "", str(desc)] if glyph else [str(desc)]
                        hp_txt = info.get("hp_text")
                        if hp_txt:
                            lines.extend(["", str(hp_txt)])
                        return "\n".join(lines)
        except Exception:
            pass

        return f"You peer into the distance at ({ax}, {ay}) in zone ({zx}, {zy}). The terrain is too far to make out."


    def describe_tile_at(
        self,
        pos: Tuple[int, int],
        *,
        level: Optional[LevelState] = None,
        zone_coord: Optional[Tuple[int, int, int]] = None,
    ) -> str:
        if level is None:
            level = self._level()
        if zone_coord is None:
            zone_coord = getattr(level, "coord", None) or getattr(self, "zone_coord", (0, 0, 0))
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

            # IMPORTANT: renderer uses the tile's zone_coord, not necessarily game.zone_coord.
            zx, zy, _zz = zone_coord
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
                blend = smoothstep_range(0.0, 1.0, frac)
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
        return int(self.cfg.world_width), int(self.cfg.world_height)

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
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                nx = max(0, min(max_screen, int(zx + dx)))
                ny = max(0, min(max_screen, int(zy + dy)))
                coords.append((nx, ny, int(zz)))
        return coords

    def _ensure_active_zones_loaded(self) -> list[LevelState]:
        """Ensure all active zones around the player are loaded."""
        levels: list[LevelState] = []
        for coord in self._active_zone_coords():
            try:
                lvl = zones_system.get_zone(self, coord, up_pos=None)
            except Exception:
                continue
            if lvl is not None:
                levels.append(lvl)
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
        """
        Like divmod, but explicitly guarantees:
          a = q*b + r with r in [0, b-1] even for negative a.
        """
        # Python's // already floors for negatives, but make it explicit + readable.
        q = a // b
        r = a - q * b
        if r < 0:
            q -= 1
            r += b
        return q, r

    def abs_from_zone_local(
        self,
        zone_coord: tuple[int, int, int],
        local_pos: tuple[int, int],
    ) -> tuple[int, int]:
        zx, zy, _zz = zone_coord
        lx, ly = int(local_pos[0]), int(local_pos[1])
        zw, zh = self._zone_dims()
        return int(zx) * zw + lx, int(zy) * zh + ly

    def zone_local_from_abs(
        self,
        abs_pos: tuple[int, int],
        *,
        depth: int | None = None,
        clamp_to_world: bool = True,
    ) -> tuple[tuple[int, int, int], tuple[int, int]]:
        ax, ay = int(abs_pos[0]), int(abs_pos[1])
        zw, zh = self._zone_dims()

        zx, lx = self._floor_divmod(ax, zw)
        zy, ly = self._floor_divmod(ay, zh)

        if clamp_to_world:
            max_screen = max(0, int(self.cfg.world_map_screens) - 1)
            zx = max(0, min(max_screen, zx))
            zy = max(0, min(max_screen, zy))
            # Re-clamp locals too, just in case rounding/edge weirdness occurs
            lx = max(0, min(zw - 1, lx))
            ly = max(0, min(zh - 1, ly))

        zz = int(depth if depth is not None else self.zone_coord[2])
        return (int(zx), int(zy), int(zz)), (int(lx), int(ly))

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

        dest_coord, dest_local = self.zone_local_from_abs(abs_pos, depth=self.zone_coord[2], clamp_to_world=True)
        (dzx, dzy, dzz) = dest_coord

        # Debug logging - always log when called (boundary crossing)
        try:
            with open("C:/Games/Edgecaster/debug.log", "a") as f:
                f.write(f"[_move_player_to_abs] Called: abs_pos={abs_pos}, old_coord={old_coord}, old_level_coord={old_level_coord}, dest_coord={dest_coord}, dest_local={dest_local}\n")
        except Exception:
            pass

        # Ensure destination chunk exists (boring cache behavior)
        dest_level = zones_system.get_zone(self, dest_coord, up_pos=None)

        # Commit rune scalar state from the current zone view back into canonical storage.
        try:
            self._commit_pattern_state_from_level(self._level())
        except Exception:
            pass


        # Move between levels if membership changes
        level_changed = getattr(old_level, "coord", None) != dest_coord
        try:
            with open("C:/Games/Edgecaster/debug.log", "a") as f:
                f.write(f"[_move_player_to_abs] level_changed={level_changed}, old_level.coord={getattr(old_level, 'coord', None)}, dest_coord={dest_coord}\n")
        except Exception:
            pass

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
            try:
                with open("C:/Games/Edgecaster/debug.log", "a") as f:
                    f.write(f"[_move_player_to_abs] Set camera_needs_recenter=True\n")
            except Exception:
                pass
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

        # Keep continuity: update FOV and Lorenz storm
        try:
            dest_level.need_fov = True
        except Exception:
            pass
        self._update_fov(dest_level)
        self._reset_lorenz_on_zone_change(player)
        # Ensure the new zone views canonical pattern state
        self._sync_level_pattern_view(dest_level)


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

        # Create the polygon pattern and anchor it on the player (CANONICAL ABS).
        pat = builder.regular_polygon_pattern(num_sides, radius)

        # Compute canonical ABS anchor at the player's current position.
        anchor_abs = getattr(player, "abs_pos", None)
        if anchor_abs is None:
            anchor_abs = self.abs_from_zone_local(self.zone_coord, player.pos)

        st = self._pattern_state(depth=self.zone_coord[2])
        st["pattern"] = pat
        st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1]))
        st["activation_points"] = []
        st["activation_ttl"] = 0
        st["pattern_motion"] = None

        # Sync the current zone view to canonical state immediately.
        self._sync_level_pattern_view(level)

        # Clear per-level auxiliaries tied to the current pattern.
        level.pattern_motion = None
        level.acidic_pattern = False
        level.fern_active = False
        level.fern_growth_tips = []
        level.fern_accum = 0.0

        self._commit_pattern_state_from_level(level)

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

        # Create the star pattern and anchor it on the player (CANONICAL ABS).
        pat = builder.star_pattern(num_points, outer_radius, inner_radius)

        anchor_abs = getattr(player, "abs_pos", None)
        if anchor_abs is None:
            anchor_abs = self.abs_from_zone_local(self.zone_coord, player.pos)

        st = self._pattern_state(depth=self.zone_coord[2])
        st["pattern"] = pat
        st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1]))
        st["activation_points"] = []
        st["activation_ttl"] = 0
        st["pattern_motion"] = None

        self._sync_level_pattern_view(level)

        level.pattern_motion = None
        level.acidic_pattern = False
        level.fern_active = False
        level.fern_growth_tips = []
        level.fern_accum = 0.0

        self._commit_pattern_state_from_level(level)

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
            from edgecaster.systems.chakras import (
                chakras_to_seed_pattern,
                get_all_chakra_positions_recursive,
            )
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

        # If the seed pattern carries a node-id ordering, keep it in sync
        # as we prune/reindex vertices. This lets us resolve the chosen root
        # by exact node id instead of guessing by position.
        node_order = None
        try:
            import json
            raw = chakra_pattern.meta.get("chakra_nodes") if getattr(chakra_pattern, "meta", None) else None
            if raw:
                node_order = json.loads(raw)
                if not isinstance(node_order, list) or len(node_order) != len(verts):
                    node_order = None
        except Exception:
            node_order = None

        # Debug logging: show active chakras + their seed positions.
        try:
            active_sorted = sorted(chakra_state.active)
            self._debug(f"[chakra_gen] active={active_sorted}")
            if node_order:
                for idx, nid in enumerate(node_order):
                    if idx < len(verts):
                        vx, vy = verts[idx]
                        flag = "ACTIVE" if nid in chakra_state.active else "inactive"
                        self._debug(f"[chakra_gen] seed node[{idx}] {nid} {flag} pos=({vx:.4f},{vy:.4f})")
            if edges:
                self._debug(f"[chakra_gen] seed edges={edges}")
        except Exception:
            pass

        # Remember the chosen pattern root early so we can preserve it if
        # multiple chakras share the exact same position (e.g., arm + shoulder).
        root_id = getattr(chakra_state, "pattern_root", None)

        # Collapse duplicate vertices (same position) so we don't accidentally
        # drop the chosen root when pruning degenerate edges. This can happen
        # when a branch root overlaps its sub-root (arm == arm.shoulder).
        if len(verts) >= 2:
            # Group vertices by position key (rounded for stability).
            key_for: List[tuple[float, float]] = [
                (round(vx, 6), round(vy, 6)) for vx, vy in verts
            ]
            groups: Dict[tuple[float, float], List[int]] = {}
            for idx, key in enumerate(key_for):
                groups.setdefault(key, []).append(idx)

            if any(len(g) > 1 for g in groups.values()):
                # Pick a representative for each group. Prefer the selected root
                # if it is part of this group.
                rep_for_key: Dict[tuple[float, float], int] = {}
                for key, idxs in groups.items():
                    rep = idxs[0]
                    if node_order is not None and root_id:
                        for i in idxs:
                            if node_order[i] == root_id:
                                rep = i
                                break
                    rep_for_key[key] = rep

                # Build old->new mapping, preserving a stable order.
                rep_list: List[int] = []
                old_to_new: Dict[int, int] = {}
                for i, key in enumerate(key_for):
                    rep = rep_for_key[key]
                    if rep not in old_to_new:
                        old_to_new[rep] = len(rep_list)
                        rep_list.append(rep)
                    old_to_new[i] = old_to_new[rep]

                verts = [verts[i] for i in rep_list]
                if node_order is not None:
                    node_order = [node_order[i] for i in rep_list]

                # Remap edges and drop any that collapse onto a single point.
                new_edges: List[tuple[int, int]] = []
                seen_edges: set[tuple[int, int]] = set()
                for a, b in edges:
                    na = old_to_new.get(a)
                    nb = old_to_new.get(b)
                    if na is None or nb is None or na == nb:
                        continue
                    key = (na, nb) if na <= nb else (nb, na)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    new_edges.append((na, nb))
                edges = new_edges

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
                if node_order is not None:
                    node_order = [node_order[i] for i in used_list]

        if len(verts) < 2 or not edges:
            self.log.add("Need at least 2 connected chakras to form a generator.")
            return

        try:
            if node_order:
                self._debug(f"[chakra_gen] after dedupe node_order={node_order}")
            self._debug(f"[chakra_gen] after dedupe verts={[(round(x,4), round(y,4)) for x,y in verts]}")
            self._debug(f"[chakra_gen] after dedupe edges={edges}")
        except Exception:
            pass

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
                if node_order is not None:
                    node_order = [node_order[i] for i in used_list]
        if len(verts) < 2 or not edges:
            self.log.add("Need at least 2 connected chakras to form a generator.")
            return

        try:
            if node_order:
                self._debug(f"[chakra_gen] after prune node_order={node_order}")
            self._debug(f"[chakra_gen] after prune verts={[(round(x,4), round(y,4)) for x,y in verts]}")
            self._debug(f"[chakra_gen] after prune edges={edges}")
        except Exception:
            pass

        # Reorder vertices so root is first and furthest point is last.
        # The CustomGraphGenerator uses vertices[0] and vertices[-1] as the
        # baseline for scaling - if these aren't the pattern endpoints,
        # the pattern will explode in size after iterations.
        #
        # Root must be explicitly chosen and active (no implicit body fallback).
        if not root_id or root_id not in chakra_state.active:
            self.log.add("Select an active chakra as the pattern root first.")
            return

        if node_order is not None and root_id in node_order:
            root_idx = node_order.index(root_id)
        else:
            # If we can't resolve the root by id, abort to avoid unexpected fallback.
            self.log.add("Pattern root not found in chakra pattern.")
            return

        # Find terminus as the farthest ACTIVE node by Euclidean distance.
        root_pos = verts[root_idx]

        def dist_sq_from_root(idx: int) -> float:
            dx = verts[idx][0] - root_pos[0]
            dy = verts[idx][1] - root_pos[1]
            return dx * dx + dy * dy

        if node_order is not None:
            candidates = [i for i, nid in enumerate(node_order) if nid in chakra_state.active]
        else:
            candidates = list(range(len(verts)))

        # Avoid choosing the root itself if we have other options.
        if len(candidates) > 1 and root_idx in candidates:
            candidates = [i for i in candidates if i != root_idx]

        if not candidates:
            self.log.add("Need at least 2 connected chakras to form a generator.")
            return

        furthest_idx = max(candidates, key=dist_sq_from_root)

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
                # Normalize by the baseline length so short roots don't
                # blow up the generator scale when mapped onto segments.
                nx /= base_len
                ny /= base_len
                norm_verts.append((nx, ny))
            verts = norm_verts
            # Pin the chosen root/terminus exactly to the baseline endpoints.
            if 0 <= root_idx < len(verts):
                verts[root_idx] = (0.0, 0.0)
            if 0 <= furthest_idx < len(verts):
                verts[furthest_idx] = (1.0, 0.0)

        try:
            self._debug(
                f"[chakra_gen] root_idx={root_idx} furthest_idx={furthest_idx} base_len={base_len:.4f}"
            )
        except Exception:
            pass

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
        if node_order is not None:
            new_node_order: List[str] = ["" for _ in range(len(new_verts))]
            for old_idx, new_idx in old_to_new.items():
                if old_idx < len(node_order):
                    new_node_order[new_idx] = node_order[old_idx]
            node_order = new_node_order

        try:
            if node_order:
                for idx, nid in enumerate(node_order):
                    vx, vy = verts[idx]
                    self._debug(f"[chakra_gen] final node[{idx}] {nid} pos=({vx:.4f},{vy:.4f})")
            self._debug(f"[chakra_gen] final edges={edges}")
        except Exception:
            pass

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
        # Preserve chakra seed metadata for future ability targeting.
        try:
            import json
            if node_order is not None:
                level.pattern.meta["chakra_seed_nodes"] = json.dumps(node_order)
            level.pattern.meta["chakra_seed_verts"] = json.dumps(verts)
            level.pattern.meta["chakra_seed_edges"] = json.dumps(edges)
            level.pattern.meta["chakra_seed_root"] = str(root_id)
            if node_order:
                level.pattern.meta["chakra_seed_terminus"] = str(node_order[-1])
        except Exception:
            pass
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
        self._commit_pattern_state_from_level(lvl)


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
        """Update visibility/exploration flags using ABS-space as the source of truth.

        This MUST be continuous across chunk boundaries.
        Zones/chunks are cache buckets only: FoV is computed in ABS coordinates and
        projected into whatever chunks are touched.
        """
        if self.player_id not in level.actors:
            return

        # Apply view bonus from equipment
        view_bonus = self.effective_character_stats().get("view", 0)
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
    def _debug(self, msg: str) -> None:
        try:
            with open(self.debug_log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
