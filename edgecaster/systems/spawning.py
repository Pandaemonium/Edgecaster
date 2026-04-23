"""
Spawning System - Entity factories and spawn orchestration.

This module manages:
- Template loading and caching for enemies and entities
- Position finding for valid spawn locations
- Entity instantiation from prototypes
- Actor registration and AI scheduling

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.

YOGA COMPLIANCE NOTE:
- spawn_entity_from_template() sets abs_pos on creation via abs_from_zone_local().
- register_actor() backfills abs_pos if not already set.
- spawn_enemies() relies on register_actor() for abs_pos backfill.
- All spawn paths should produce entities with valid abs_pos.
- See vision_documents/the_yoga.txt Stage 1 for coordinate authority requirements.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING
import yaml

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state import Actor
    from edgecaster.state.entities import Entity

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.enemies import factory as enemy_factory
from edgecaster.state.actors import Human, Stats
from edgecaster.content import npcs
from edgecaster.systems import difficulty as difficulty_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_identity as entity_identity_system
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import footprints as footprints_system


# ---------------------------------------------------------------------------
# Template Loading (Cached)
# ---------------------------------------------------------------------------

_enemy_ids_cache: Optional[List[str]] = None
_entity_templates_cache: Optional[Dict[str, dict]] = None


# ---------------------------------------------------------------------------
# Biome Enemy Pool Caches
# ---------------------------------------------------------------------------

_biome_enemy_data_cache: Optional[list] = None
_biome_enemy_pool_cache: Dict[tuple[int, bool], List[str]] = {}



def get_enemy_template_ids(game: "Game") -> List[str]:
    """Get list of valid enemy template IDs for random spawning.

    Filters out player-only, no_random_spawn, and training_dummy templates.
    Results are cached for performance.
    """
    global _enemy_ids_cache

    # Check game's cache first (for backwards compatibility)
    cached = getattr(game, "_enemy_ids_cache", None)
    if cached is not None:
        return cached

    if _enemy_ids_cache is not None:
        return _enemy_ids_cache

    content_dir = Path(__file__).resolve().parent.parent / "content"
    yaml_path = content_dir / "enemies.yaml"

    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except FileNotFoundError:
        data = []

    enemy_ids: List[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id")
        if not tid:
            continue

        faction = entry.get("faction", "hostile")
        tags_raw = entry.get("tags", None)
        if isinstance(tags_raw, dict):
            tags = set(tags_raw.keys())
        elif isinstance(tags_raw, list):
            tags = set(tags_raw)
        else:
            tags = set()

        # Only randomize true enemies
        if faction != "hostile":
            continue
        if "player_only" in tags or "no_random_spawn" in tags or "training_dummy" in tags:
            continue

        enemy_ids.append(tid)

    if not enemy_ids:
        enemy_ids = ["imp"]

    _enemy_ids_cache = enemy_ids
    game._enemy_ids_cache = enemy_ids
    return enemy_ids


def get_entity_templates(game: "Game") -> Dict[str, dict]:
    """Load non-actor entity templates from content/entities.yaml (cached)."""
    global _entity_templates_cache

    # Check game's cache first
    cached = getattr(game, "_entity_templates_cache", None)
    if cached is not None:
        return cached

    if _entity_templates_cache is not None:
        return _entity_templates_cache

    content_dir = Path(__file__).resolve().parent.parent / "content"
    yaml_path = content_dir / "entities.yaml"

    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except FileNotFoundError:
        data = []

    templates: Dict[str, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id")
        if not tid:
            continue
        templates[tid] = entry

    _entity_templates_cache = templates
    game._entity_templates_cache = templates
    return templates


def clear_template_caches() -> None:
    """Clear the module-level template caches. Used for testing."""
    global _enemy_ids_cache, _entity_templates_cache
    _enemy_ids_cache = None
    _entity_templates_cache = None


# ---------------------------------------------------------------------------
# Placement Helpers (footprint-aware)
# ---------------------------------------------------------------------------


def _spawn_rect_from_spec(
    spec: Optional[Dict[str, object]],
    pos: Tuple[int, int],
) -> tuple[float, float, float, float]:
    return footprints_system.footprint_local_rect_from_spec(spec, (int(pos[0]), int(pos[1])))


def _spawn_rect_from_template_id(
    template_id: str,
    pos: Tuple[int, int],
) -> tuple[float, float, float, float]:
    try:
        spec = prototypes.resolve_proto(template_id)
    except Exception:
        spec = None
    return _spawn_rect_from_spec(spec, pos)


def _is_tile_spawnable(
    game: "Game",
    level: "LevelState",
    pos: Tuple[int, int],
    *,
    avoid_actors: bool = True,
    avoid_entities: bool = True,
    avoid_blocking: bool = False,
    footprint_rect: Optional[tuple[float, float, float, float]] = None,
) -> bool:
    x, y = int(pos[0]), int(pos[1])
    world = level.world
    rect = footprints_system.normalize_rect(footprint_rect) if footprint_rect is not None else footprints_system.tile_rect((x, y))

    # Point tile + footprint path are equivalent for 1x1 spawns, but this keeps
    # one geometry-aware path for future non-point placement policies.
    try:
        if not footprints_system.world_walkable_for_rect(world, rect):
            return False
    except Exception:
        try:
            if not world.in_bounds(x, y):
                return False
            if not world.is_walkable(x, y):
                return False
        except Exception:
            return False

    if avoid_actors:
        actor = None
        try:
            actor = entity_ops_system.first_actor_overlapping_rect(level, rect)
        except Exception:
            actor = None
        if actor is not None:
            return False

    if avoid_blocking:
        blocker = None
        try:
            blocker = entity_ops_system.blocking_entity_overlapping_rect(level, rect)
        except Exception:
            blocker = None
        if blocker is not None:
            return False

    if avoid_entities:
        ent = None
        try:
            ent = entity_ops_system.first_entity_overlapping_rect(level, rect)
        except Exception:
            ent = None
        if ent is not None:
            return False

    return True


# ---------------------------------------------------------------------------
# Position Finding
# ---------------------------------------------------------------------------

def find_spawn_position(
    game: "Game",
    level: "LevelState",
    *,
    near: Optional[Tuple[int, int]] = None,
    radius: int = 3,
    avoid_actors: bool = True,
    avoid_entities: bool = True,
    avoid_near: Optional[Tuple[int, int]] = None,
    avoid_distance: int = 0,
    max_attempts: int = 100,
    spawn_spec: Optional[Dict[str, object]] = None,
) -> Optional[Tuple[int, int]]:
    """Find a valid spawn position in the level.

    Args:
        game: The Game instance
        level: The LevelState to spawn in
        near: If provided, search near this position within radius
        radius: Search radius when 'near' is provided
        avoid_actors: Skip tiles with actors
        avoid_entities: Skip tiles with entities
        avoid_near: Optional local position to keep clear of spawned entities
        avoid_distance: Euclidean distance radius around `avoid_near` to avoid
        max_attempts: Maximum random attempts

    Returns:
        Valid (x, y) position or None if not found
    """
    world = level.world

    for _ in range(max_attempts):
        if near:
            cx, cy = near
            x = cx + game.rng.randint(-radius, radius)
            y = cy + game.rng.randint(-radius, radius)
        else:
            x = game.rng.randint(1, world.width - 2)
            y = game.rng.randint(1, world.height - 2)

        candidate_rect = _spawn_rect_from_spec(spawn_spec, (x, y)) if spawn_spec is not None else None
        if not _is_tile_spawnable(
            game,
            level,
            (x, y),
            avoid_actors=avoid_actors,
            avoid_entities=avoid_entities,
            avoid_blocking=False,
            footprint_rect=candidate_rect,
        ):
            continue
        if avoid_near is not None and avoid_distance > 0:
            dx = int(x) - int(avoid_near[0])
            dy = int(y) - int(avoid_near[1])
            if dx * dx + dy * dy < int(avoid_distance) * int(avoid_distance):
                continue

        return (x, y)

    return None


def find_nearest_walkable(
    game: "Game",
    level: "LevelState",
    origin: Tuple[int, int],
    max_radius: int = 12,
    spawn_spec: Optional[Dict[str, object]] = None,
) -> Optional[Tuple[int, int]]:
    """Find the nearest walkable, unoccupied tile to origin.

    Searches in expanding rings from origin.
    """
    ox, oy = origin

    # Check origin first
    origin_rect = _spawn_rect_from_spec(spawn_spec, (ox, oy)) if spawn_spec is not None else None
    if _is_tile_spawnable(
        game,
        level,
        (ox, oy),
        avoid_actors=True,
        avoid_entities=False,
        avoid_blocking=False,
        footprint_rect=origin_rect,
    ):
        return origin

    # Search expanding rings
    for r in range(1, max_radius + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                tx, ty = ox + dx, oy + dy
                candidate_rect = _spawn_rect_from_spec(spawn_spec, (tx, ty)) if spawn_spec is not None else None
                if not _is_tile_spawnable(
                    game,
                    level,
                    (tx, ty),
                    avoid_actors=True,
                    avoid_entities=False,
                    avoid_blocking=False,
                    footprint_rect=candidate_rect,
                ):
                    continue
                return (tx, ty)

    return None


# ---------------------------------------------------------------------------
# Entity Instantiation
# ---------------------------------------------------------------------------

def spawn_entity_from_template(
    game: "Game",
    template_id: str,
    pos: Tuple[int, int],
    overrides: Optional[Dict[str, object]] = None,
    *,
    deterministic: bool = False,
    deterministic_recipe: Optional[str] = None,
    deterministic_namespace: str = "ent",
    deterministic_salt: Optional[str] = None,
    zone_coord: Optional[Tuple[int, int, int]] = None,
) -> "Entity":
    """Instantiate a plain Entity from the unified prototype bucket.

    Uses prototypes.resolve_proto() so inheritance works across entities.yaml/enemies.yaml/etc.
    `overrides` merges on top; `tags` merge dict-wise (entity-style).
    """
    # resolve_proto uses the global master bucket (loaded lazily from content/*.yaml)
    spec = prototypes.resolve_proto(template_id)
    if not spec:
        raise KeyError(f"Unknown prototype id {template_id!r}")

    spawn_zone = tuple(zone_coord) if zone_coord is not None else tuple(getattr(game, "zone_coord", (0, 0, 0)))
    abs_pos = game.abs_from_zone_local(spawn_zone, pos)
    if deterministic:
        rid = str(deterministic_recipe or template_id)
        eid = entity_identity_system.deterministic_runtime_id(
            game,
            recipe_id=rid,
            abs_pos=abs_pos,
            namespace=str(deterministic_namespace or "ent"),
            salt=deterministic_salt,
        )
    else:
        eid = game._new_id()

    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid=eid,
        pos=pos,
        abs_pos=abs_pos,
        overrides=overrides,
    )

    # Initialize per-instance item charges
    _init_entity_charges(game, ent)
    _patch_spawned_entity_state(game, ent, owner_id=None, in_inventory=False)
    entity_graph_ops_system.register_entity(game, ent, lod_state="expanded")

    return ent



def _init_entity_charges(game: "Game", ent: "Entity") -> None:
    """Initialize charges for items that have charge-based abilities."""
    try:
        tags = getattr(ent, "tags", None) or {}

        if "charges" not in tags:
            raw_min = tags.get("charges_min")
            raw_max = tags.get("charges_max")
            if raw_min is not None or raw_max is not None:
                lo = int(raw_min if raw_min is not None else raw_max)
                hi = int(raw_max if raw_max is not None else raw_min)
                if hi < lo:
                    lo, hi = hi, lo
                lo = max(0, lo)
                hi = max(0, hi)
                charges = int(game.rng.randint(lo, hi))
                tags["charges"] = charges
                tags.setdefault("max_charges", charges)
        else:
            try:
                tags.setdefault("max_charges", int(tags.get("charges")))
            except Exception:
                pass

        ent.tags = tags
    except Exception:
        pass


def _patch_spawned_entity_state(
    game: "Game",
    ent: object,
    *,
    owner_id: Optional[str] = None,
    in_inventory: bool = False,
) -> None:
    """Best-effort entity_state initialization for new runtime entities."""
    try:
        patch = getattr(game, "patch_entity_state", None)
        if not callable(patch):
            return
        patch(
            ent,
            removed=False,
            dead=False,
            owner_id=owner_id,
            parent_entity_id=owner_id if in_inventory else None,
            socket_id="inventory" if in_inventory else None,
            in_inventory=bool(in_inventory),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Actor Registration Helpers
# ---------------------------------------------------------------------------

def register_actor(
    game: "Game",
    level: "LevelState",
    actor: "Actor",
    schedule_ai: bool = True,
) -> None:
    """Register an actor in the level and optionally schedule AI.

    Args:
        game: The Game instance
        level: The LevelState
        actor: The actor to register
        schedule_ai: If True, schedule the AI turn for this actor
    """
    # Ensure canonical ABS truth exists at birth.
    # This prevents UI anchoring / look-scene transitions from "flying in"
    # until the entity has moved once.
    try:
        if getattr(actor, "abs_pos", None) is None:
            actor.abs_pos = game.abs_from_zone_local(level.coord, actor.pos)
    except Exception:
        pass

    level.actors[actor.id] = actor
    level.entities[actor.id] = actor
    _patch_spawned_entity_state(game, actor, owner_id=None, in_inventory=False)
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")

    # Body expansion at birth: materialize body-node child entities now so that
    # chakra-scene queries and geometry lookups find them without triggering lazy
    # realization themselves.  Mirrors the realize_policy="require" queue in
    # query_geometry; uses lazy imports to avoid the entity_lifecycle→spawning
    # circular import that would occur at module level.
    try:
        from edgecaster.systems import entity_body as _eb_sys
        from edgecaster.systems import entity_lifecycle as _elc_sys
        if _eb_sys.can_expand_entity(actor):
            _expand_queue = [actor.id]
            _expand_seen: set = set()
            while _expand_queue:
                _cid = _expand_queue.pop(0)
                if _cid in _expand_seen:
                    continue
                _expand_seen.add(_cid)
                _child_ids = _elc_sys.expand_entity(game, _cid, reason="spawn")
                for _child_id in _child_ids:
                    _child_obj = _elc_sys.find_runtime_entity(game, str(_child_id))
                    if _child_obj is not None and _eb_sys.can_expand_entity(_child_obj):
                        _expand_queue.append(str(_child_id))
    except Exception:
        pass

    if schedule_ai:
        game._schedule(
            level,
            game.cfg.action_time_fast,
            lambda aid=actor.id, lvl=level: game._monster_act(lvl, aid),
        )


# ---------------------------------------------------------------------------
# Generic Spawn Helpers
# ---------------------------------------------------------------------------

def spawn_entities_near(
    game: "Game",
    level: "LevelState",
    center: Tuple[int, int],
    count: int,
    place_entity: Callable[[Tuple[int, int]], None],
    radius: int = 3,
) -> int:
    """Generic helper to spawn up to `count` entities within `radius` of center.

    Args:
        game: The Game instance
        level: The LevelState
        center: Center position to spawn around
        count: Number of entities to spawn
        place_entity: Callback to create and register the entity at a position
        radius: Spawn radius around center

    Returns:
        Number of entities actually spawned
    """
    cx, cy = center
    spawned = 0
    attempts = 0
    max_attempts = count * 20

    while spawned < count and attempts < max_attempts:
        attempts += 1
        x = cx + game.rng.randint(-radius, radius)
        y = cy + game.rng.randint(-radius, radius)

        if not _is_tile_spawnable(
            game,
            level,
            (x, y),
            avoid_actors=True,
            avoid_entities=True,
            avoid_blocking=False,
        ):
            continue

        place_entity((x, y))
        spawned += 1

    return spawned


# ---------------------------------------------------------------------------
# Specific Spawn Functions
# ---------------------------------------------------------------------------

def _get_biome_at_position(level: "LevelState", pos: Tuple[int, int]) -> Optional[int]:
    """Get the biome ID at a given position, if available."""
    x, y = pos
    world = getattr(level, "world", None)
    if world is None:
        return None

    # Try to get biome from tile
    try:
        tile = world.get_tile(x, y)
        if tile is not None:
            biome_id = getattr(tile, "biome_id", None)
            if isinstance(biome_id, (int, float)):
                return int(biome_id)
    except Exception:
        pass

    return None


def _nearest_tier_fallback_pool(
    pool: List[str],
    bounds: Dict[str, Tuple[int, int]],
    zone_tier: int,
) -> List[str]:
    """Pick a best-effort fallback pool when strict tier filtering returns empty.

    Why this exists:
    - Falling back to only ``imp`` makes late-game zones feel undertuned.
    - We instead choose enemies whose tier windows are closest to ``zone_tier``.
    """
    if not pool:
        return ["imp"]

    ranked = sorted(
        pool,
        key=lambda eid: abs((((bounds.get(eid, (1, 1))[0] + bounds.get(eid, (1, 1))[1]) * 0.5) - zone_tier)),
    )
    if not ranked:
        return ["imp"]

    # Keep a small variety band around the best match.
    best_mid = (bounds.get(ranked[0], (1, 1))[0] + bounds.get(ranked[0], (1, 1))[1]) * 0.5
    best_dist = abs(best_mid - zone_tier)
    out: List[str] = []
    for eid in ranked:
        tmin, tmax = bounds.get(eid, (1, 1))
        mid = (tmin + tmax) * 0.5
        if abs(mid - zone_tier) <= best_dist + 1.0:
            out.append(eid)
        if len(out) >= 6:
            break
    return out or [ranked[0]]


def _apply_enemy_zone_scaling(
    level: "LevelState",
    mob: "Actor",
    *,
    zone_tier: int,
    enemy_bounds: Optional[Tuple[int, int]] = None,
) -> None:
    """Scale spawned enemy durability/damage by zone pressure.

    Design goals for the balancing sweep:
    - Keep tier-1/2 near current feel.
    - Make tier 7+ meaningfully dangerous without touching every YAML row.
    """
    try:
        stats = getattr(mob, "stats", None)
        if stats is None:
            return

        tags = getattr(mob, "tags", None) or {}
        # Midpoint of the enemy's intended tier window. If unknown, assume zone tier.
        if enemy_bounds is None:
            enemy_mid = float(zone_tier)
        else:
            enemy_mid = (float(enemy_bounds[0]) + float(enemy_bounds[1])) * 0.5

        # Pressure is how far this spawn context exceeds the enemy's nominal tier.
        zone_floor = max(0.0, float(zone_tier) - 1.0)
        zone_pressure = max(0.0, float(zone_tier) - enemy_mid)

        # HP grows a bit faster than damage so fights last longer at high tiers.
        hp_mult = 1.0 + 0.09 * zone_floor + 0.08 * zone_pressure
        atk_mult = 1.0 + 0.07 * zone_floor + 0.07 * zone_pressure
        hp_mult = min(hp_mult, 2.60)
        atk_mult = min(atk_mult, 2.20)

        old_max_hp = int(getattr(stats, "max_hp", 1) or 1)
        old_hp = int(getattr(stats, "hp", old_max_hp) or old_max_hp)
        new_max_hp = max(1, int(round(old_max_hp * hp_mult)))
        new_hp = max(1, int(round(old_hp * hp_mult)))
        stats.max_hp = new_max_hp
        stats.hp = min(new_hp, new_max_hp)

        base_attack = int(tags.get("base_attack", 1) or 1)
        tags["base_attack"] = max(1, int(round(base_attack * atk_mult)))
        mob.tags = tags
    except Exception:
        return


def spawn_enemies(
    game: "Game",
    level: "LevelState",
    count: int,
    use_biome_spawning: bool = True,
    include_neutral_factions: bool = True,
    avoid_near: Optional[Tuple[int, int]] = None,
    avoid_distance: int = 0,
) -> int:
    """Spawn random enemies using the data-driven enemy factory.

    Args:
        game: The Game instance
        level: The LevelState to spawn in
        count: Number of enemies to spawn
        use_biome_spawning: If True, use biome-aware enemy selection
        include_neutral_factions: If True, include neutral faction creatures

    Returns the number of enemies spawned.

    Args:
        avoid_near: Optional local position to avoid spawning too close to.
        avoid_distance: Euclidean distance radius around `avoid_near`.
    """
    spawned = 0
    attempts = 0

    # Cache biome pools to avoid re-reading YAML for each spawn
    biome_pool_cache: Dict[int, List[str]] = {}

    # Fallback pool for when biome isn't available
    fallback_pool: Optional[List[str]] = None

    while spawned < count and attempts < 200:
        attempts += 1
        pos = find_spawn_position(
            game,
            level,
            avoid_entities=False,
            avoid_near=avoid_near,
            avoid_distance=avoid_distance,
        )
        if pos is None and (avoid_near is not None and avoid_distance > 0):
            # Fallback for very dense zones: relax the distance gate only.
            pos = find_spawn_position(game, level, avoid_entities=False)
        if pos is None:
            continue

        # Determine which enemy pool to use
        enemy_ids: List[str]

        if use_biome_spawning:
            biome_id = _get_biome_at_position(level, pos)

            if biome_id is not None:
                # Use cached pool or build new one
                if biome_id not in biome_pool_cache:
                    biome_pool_cache[biome_id] = get_biome_enemy_pool(
                        biome_id, include_neutral_factions=include_neutral_factions
                    )
                enemy_ids = biome_pool_cache[biome_id]
            else:
                # No biome info, use fallback
                if fallback_pool is None:
                    fallback_pool = get_enemy_template_ids(game)
                enemy_ids = fallback_pool
        else:
            # Biome spawning disabled, use classic behavior
            enemy_ids = get_enemy_template_ids(game)

        if not enemy_ids:
            enemy_ids = ["imp"]

        # Filter by zone difficulty tier (10-tier system).
        raw_zone_tier = getattr(level, "danger_tier", 1)
        try:
            zone_tier = int(raw_zone_tier)
        except Exception:
            zone_tier = 1
        zone_tier = max(1, zone_tier)
        filtered_pool, bounds = difficulty_system.filter_enemy_pool(game, enemy_ids, zone_tier)
        if filtered_pool:
            enemy_ids = filtered_pool
        else:
            # Prefer nearest-tier matches over a hard "imp only" fallback.
            enemy_ids = _nearest_tier_fallback_pool(enemy_ids, bounds, zone_tier)

        selected_id = None
        selected_pos = None
        sampled_ids: List[str] = []
        if enemy_ids:
            sampled_ids.append(str(game.rng.choice(enemy_ids)))
        while len(sampled_ids) < min(len(enemy_ids), 6):
            candidate_id = str(game.rng.choice(enemy_ids))
            if candidate_id in sampled_ids:
                continue
            sampled_ids.append(candidate_id)

        for candidate_id in sampled_ids:
            try:
                candidate_spec = prototypes.resolve_proto(candidate_id)
            except Exception:
                candidate_spec = None

            if _is_tile_spawnable(
                game,
                level,
                pos,
                avoid_actors=True,
                avoid_entities=True,
                avoid_blocking=True,
                footprint_rect=_spawn_rect_from_spec(candidate_spec, pos),
            ):
                selected_id = candidate_id
                selected_pos = pos
                break

            probe = find_spawn_position(
                game,
                level,
                avoid_entities=False,
                avoid_near=avoid_near,
                avoid_distance=avoid_distance,
                max_attempts=36,
                spawn_spec=candidate_spec,
            )
            if probe is None and (avoid_near is not None and avoid_distance > 0):
                probe = find_spawn_position(
                    game,
                    level,
                    avoid_entities=False,
                    avoid_near=avoid_near,
                    avoid_distance=0,
                    max_attempts=24,
                    spawn_spec=candidate_spec,
                )
            if probe is None:
                continue
            if not _is_tile_spawnable(
                game,
                level,
                probe,
                avoid_actors=True,
                avoid_entities=True,
                avoid_blocking=True,
                footprint_rect=_spawn_rect_from_spec(candidate_spec, probe),
            ):
                continue

            selected_id = candidate_id
            selected_pos = probe
            break

        if selected_id is None or selected_pos is None:
            continue
        tmpl_id = selected_id
        pos = selected_pos

        # YOGA: Compute ABS position for canonical storage
        abs_pos = game.abs_from_zone_local(level.coord, pos)
        mob = enemy_factory.spawn_enemy(tmpl_id, pos, abs_pos=abs_pos, game=game)

        # 20% bismuth imps
        if tmpl_id == "imp" and game.rng.random() < 0.2:
            mob.tags = getattr(mob, "tags", None) or {}
            mob.tags["visual_effects"] = ["bismuth"]
            if not mob.name.lower().startswith("bismuth "):
                mob.name = "bismuth imp"

        _apply_enemy_zone_scaling(
            level,
            mob,
            zone_tier=zone_tier,
            enemy_bounds=bounds.get(tmpl_id),
        )

        # Register the leader first so sidecar spawns can always reference
        # a live actor in `level.actors` (important for ringmaster troupe tags).
        register_actor(game, level, mob, schedule_ai=True)

        # Slaver packs: a slaver arrives chained to two brutes.
        if tmpl_id == "slaver":
            _spawn_slaver_pack(game, level, mob, pos)
        # Angry circus: ringmaster arrives with troupe members.
        if tmpl_id == "furious_ringmaster":
            _spawn_angry_circus(game, level, mob, pos)

        spawned += 1

    return spawned


def _spawn_slaver_pack(
    game: "Game",
    level: "LevelState",
    slaver: "Actor",
    pos: Tuple[int, int],
) -> None:
    """Spawn shackled brutes accompanying a slaver."""
    slaver.tags = getattr(slaver, "tags", None) or {}
    group_id = f"slaver_chain_{slaver.id}"
    slaver.tags["slaver_group_id"] = group_id

    brute_ids: List[str] = []
    x, y = pos
    offsets = [
        (-1, 0), (1, 0), (0, -1), (0, 1),
        (-1, -1), (1, -1), (-1, 1), (1, 1),
        (-2, 0), (2, 0), (0, -2), (0, 2),
    ]
    try:
        game.rng.shuffle(offsets)
    except Exception:
        pass

    for dx, dy in offsets:
        if len(brute_ids) >= 2:
            break
        tx, ty = x + dx, y + dy
        brute_rect = _spawn_rect_from_template_id("shackled_brute", (tx, ty))

        if not _is_tile_spawnable(
            game,
            level,
            (tx, ty),
            avoid_actors=True,
            avoid_entities=True,
            avoid_blocking=True,
            footprint_rect=brute_rect,
        ):
            continue

        # YOGA: Compute ABS position for canonical storage
        brute_abs_pos = game.abs_from_zone_local(level.coord, (tx, ty))
        brute = enemy_factory.spawn_enemy("shackled_brute", (tx, ty), abs_pos=brute_abs_pos, game=game)
        brute.tags = getattr(brute, "tags", None) or {}
        brute.tags["slaver_master_id"] = slaver.id
        brute.tags["slaver_group_id"] = group_id

        zone_tier = getattr(level, "danger_tier", 1) or 1
        _apply_enemy_zone_scaling(
            level,
            brute,
            zone_tier=zone_tier,
            enemy_bounds=(3, 8),
        )
        register_actor(game, level, brute, schedule_ai=True)
        brute_ids.append(brute.id)

    if brute_ids:
        slaver.tags["slaver_minion_ids"] = brute_ids


def _spawn_angry_circus(
    game: "Game",
    level: "LevelState",
    ringmaster: "Actor",
    pos: Tuple[int, int],
) -> None:
    """Spawn a coordinated circus pack centered on a furious ringmaster.

    Group rules are encoded in tags and enforced in two places:
    1) AI intent (`circus_member` / `furious_ringmaster`)
    2) movement dispatcher hard-leash in `Game._handle_move_or_attack`
    """
    ringmaster.tags = getattr(ringmaster, "tags", None) or {}
    group_id = f"angry_circus_{ringmaster.id}"
    leash = int(ringmaster.tags.get("circus_leash_range", 15) or 15)
    ringmaster.tags["circus_group_id"] = group_id
    ringmaster.tags["circus_leash_range"] = leash

    member_templates = [
        "angry_elephant",
        "angry_clown",
        "angry_acrobat",
        "angry_human_cannonball",
        "angry_bearded_lady",
    ]
    # Start close to ringmaster so the group reads as a single encounter.
    offsets = [
        (-2, 0), (2, 0), (0, -2), (0, 2),
        (-2, -2), (2, -2), (-2, 2), (2, 2),
        (-3, 0), (3, 0), (0, -3), (0, 3),
        (-1, -2), (1, -2), (-1, 2), (1, 2),
    ]
    try:
        game.rng.shuffle(offsets)
    except Exception:
        pass

    x, y = pos
    member_ids: List[str] = []
    used_positions: set[tuple[int, int]] = {pos}

    def _find_member_spawn(template_id: str) -> Optional[Tuple[int, int]]:
        # Pass 1: try curated offsets so the group appears "together".
        for dx, dy in offsets:
            tx, ty = x + dx, y + dy
            if (tx, ty) in used_positions:
                continue
            spawn_rect = _spawn_rect_from_template_id(template_id, (tx, ty))
            if not _is_tile_spawnable(
                game,
                level,
                (tx, ty),
                avoid_actors=True,
                avoid_entities=True,
                avoid_blocking=True,
                footprint_rect=spawn_rect,
            ):
                continue
            return (tx, ty)

        # Pass 2: if local offsets are blocked, expand search radius so
        # ringmaster still gets a troupe in dense/ruined zones.
        try:
            spawn_spec = prototypes.resolve_proto(template_id)
        except Exception:
            spawn_spec = None
        for radius in (4, 6, 8, 10, 12, 15):
            probe = find_spawn_position(
                game,
                level,
                near=pos,
                radius=radius,
                avoid_actors=True,
                avoid_entities=True,
                max_attempts=60,
                spawn_spec=spawn_spec,
            )
            if probe is None:
                continue
            if probe in used_positions:
                continue
            return probe

        # Pass 3: guaranteed fallback - if local space is crowded, place troupe
        # members anywhere on the current level so ringmasters do not appear solo.
        for _ in range(4):
            probe = find_spawn_position(
                game,
                level,
                near=None,
                radius=0,
                avoid_actors=True,
                avoid_entities=True,
                max_attempts=120,
                spawn_spec=spawn_spec,
            )
            if probe is None:
                continue
            if probe in used_positions:
                continue
            return probe
        return None

    for tmpl in member_templates:
        spawn_xy = _find_member_spawn(tmpl)
        if spawn_xy is None:
            continue
        used_positions.add(spawn_xy)

        m_abs = game.abs_from_zone_local(level.coord, spawn_xy)
        mate = enemy_factory.spawn_enemy(tmpl, spawn_xy, abs_pos=m_abs, game=game)
        mate.tags = getattr(mate, "tags", None) or {}
        mate.tags["circus_group_id"] = group_id
        mate.tags["circus_ringmaster_id"] = ringmaster.id
        mate.tags["circus_leash_range"] = leash

        zone_tier = getattr(level, "danger_tier", 1) or 1
        _apply_enemy_zone_scaling(
            level,
            mate,
            zone_tier=zone_tier,
            enemy_bounds=(5, 9),
        )
        register_actor(game, level, mate, schedule_ai=True)
        member_ids.append(mate.id)

    ringmaster.tags["circus_member_ids"] = member_ids
    try:
        game._debug(
            f"[circus] ringmaster={ringmaster.id} spawned_members={len(member_ids)} "
            f"at_zone={level.coord}"
        )
    except Exception:
        pass


def spawn_enemy_with_pack(
    game: "Game",
    level: "LevelState",
    enemy_id: str,
    pos: Tuple[int, int],
    *,
    zone_tier: Optional[int] = None,
    enemy_bounds: Optional[Tuple[int, int]] = None,
    schedule_ai: bool = True,
) -> "Actor":
    """Spawn an enemy and attach known sidecar packs when needed.

    This is a shared path for non-random spawners (sites/POIs/events) so
    `furious_ringmaster` and `slaver` behave consistently everywhere.
    """
    if zone_tier is None:
        zone_tier = int(getattr(level, "danger_tier", 1) or 1)
    abs_pos = game.abs_from_zone_local(level.coord, pos)
    mob = enemy_factory.spawn_enemy(enemy_id, pos, abs_pos=abs_pos, game=game)
    _apply_enemy_zone_scaling(level, mob, zone_tier=zone_tier, enemy_bounds=enemy_bounds)
    register_actor(game, level, mob, schedule_ai=schedule_ai)
    if enemy_id == "slaver":
        _spawn_slaver_pack(game, level, mob, pos)
    elif enemy_id == "furious_ringmaster":
        _spawn_angry_circus(game, level, mob, pos)
    return mob


def spawn_imps_near(
    game: "Game",
    level: "LevelState",
    center: Tuple[int, int],
    count: int,
    radius: int = 3,
) -> int:
    """Spawn up to `count` imps within `radius` tiles of center."""

    def place_imp(pos: Tuple[int, int]) -> None:
        # YOGA: Compute ABS position for canonical storage
        abs_pos = game.abs_from_zone_local(level.coord, pos)
        imp = enemy_factory.spawn_enemy("imp", pos, abs_pos=abs_pos, game=game)

        # 20% chance bismuth
        if game.rng.random() < 0.2:
            imp.tags = getattr(imp, "tags", None) or {}
            imp.tags["visual_effects"] = ["bismuth"]
            imp.name = "bismuth imp"

        register_actor(game, level, imp, schedule_ai=True)

    return spawn_entities_near(game, level, center, count, place_imp, radius)


def spawn_echoes_near(
    game: "Game",
    level: "LevelState",
    center: Tuple[int, int],
    count: int,
    radius: int = 3,
) -> int:
    """Spawn hostile fractal echoes within `radius` of center."""

    def place_echo(pos: Tuple[int, int]) -> None:
        # YOGA: Compute ABS position for canonical storage
        abs_pos = game.abs_from_zone_local(level.coord, pos)
        echo = enemy_factory.spawn_enemy("fractal_echo", pos, abs_pos=abs_pos, game=game)
        register_actor(game, level, echo, schedule_ai=True)

    return spawn_entities_near(game, level, center, count, place_echo, radius)


def spawn_berries_near(
    game: "Game",
    level: "LevelState",
    center: Tuple[int, int],
    count: int,
    radius: int = 3,
) -> int:
    """Spawn up to `count` test berries within `radius` tiles of center."""
    templates = get_entity_templates(game)
    berry_ids: List[str] = []

    for tid, tmpl in templates.items():
        tags = tmpl.get("tags", {}) or {}
        if tags.get("test_berry"):
            berry_ids.append(tid)

    if not berry_ids:
        return 0

    def place_berry(pos: Tuple[int, int]) -> None:
        template_id = game.rng.choice(berry_ids)
        ent = spawn_entity_from_template(game, template_id, pos)
        level.entities[ent.id] = ent

    return spawn_entities_near(game, level, center, count, place_berry, radius)


def scatter_test_berries(
    game: "Game",
    level: "LevelState",
    count: int = 30,
) -> int:
    """Scatter colored berry entities across the map for testing.

    Also spawns some bismuth piles alongside.
    """
    if count <= 0:
        return 0

    templates = get_entity_templates(game)
    berry_ids: List[str] = []

    for tid, tmpl in templates.items():
        tags = tmpl.get("tags", {}) or {}
        if tags.get("test_berry"):
            berry_ids.append(tid)

    if not berry_ids:
        return 0

    placed = 0
    attempts = 0
    max_attempts = count * 50
    world = level.world

    while placed < count and attempts < max_attempts:
        attempts += 1
        x = game.rng.randint(0, world.width - 1)
        y = game.rng.randint(0, world.height - 1)

        if not _is_tile_spawnable(
            game,
            level,
            (x, y),
            avoid_actors=True,
            avoid_entities=True,
            avoid_blocking=False,
        ):
            continue

        template_id = game.rng.choice(berry_ids)
        ent = spawn_entity_from_template(game, template_id, (x, y))
        level.entities[ent.id] = ent
        placed += 1

    # Sprinkle some bismuth piles
    bismuth_count = max(1, count // 10)
    for _ in range(bismuth_count):
        bis_attempts = 0
        while bis_attempts < 50:
            bis_attempts += 1
            x = game.rng.randint(0, world.width - 1)
            y = game.rng.randint(0, world.height - 1)

            if not _is_tile_spawnable(
                game,
                level,
                (x, y),
                avoid_actors=True,
                avoid_entities=True,
                avoid_blocking=False,
            ):
                continue

            try:
                ent = spawn_entity_from_template(
                    game,
                    "bismuth_pile",
                    (x, y),
                    overrides={"tags": {"amount": game.rng.randint(3, 15)}},
                )
                level.entities[ent.id] = ent
                placed += 1
                break
            except Exception:
                continue

    return placed


def debug_spawn_inventory_near_player(
    game: "Game",
    radius: int = 3,
    *,
    count: int | None = None,
) -> None:
    """Debug helper: conjure a curated batch of meta-Inventories near player.

    Spawns:
      - one of each functional adjective inventory (visual effect probes)
      - plus 3 non-functional inventories (flavor/junk bags)

    Notes:
      - `count` is intentionally accepted for backwards compatibility with
        existing call sites; this helper currently uses the curated batch plan.
      - This function remains in spawning because it is pure spawn orchestration
        and keeps Game slimmer.
    """
    level = game._level()
    if entity_ops_system.get_actor(level, game.player_id) is None:
        return
    player = entity_ops_system.get_actor(level, game.player_id)
    if player is None:
        return

    # Functional adjectives -> effect names from visual_effects registry.
    # "mirrored" resolves to mirror_x currently.
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

    # Flavor-only pool (no special effects).
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

    # Avoid duplicates where a word is in both pools.
    functional_set = {k.lower() for k in functional_map.keys()}
    nonfunctional_adjectives = [a for a in nonfunctional_adjectives if a.lower() not in functional_set]

    nonfunc_pool = list(nonfunctional_adjectives)
    game.rng.shuffle(nonfunc_pool)

    def next_nonfunc_adj() -> str:
        nonlocal nonfunc_pool
        if not nonfunc_pool:
            nonfunc_pool = list(nonfunctional_adjectives)
            game.rng.shuffle(nonfunc_pool)
        return nonfunc_pool.pop()

    def find_spot_near(center: tuple[int, int], r: int, max_attempts: int = 200) -> tuple[int, int] | None:
        cx, cy = center
        for _ in range(max_attempts):
            x = cx + game.rng.randint(-r, r)
            y = cy + game.rng.randint(-r, r)
            if not _is_tile_spawnable(
                game,
                level,
                (x, y),
                avoid_actors=True,
                avoid_entities=True,
                avoid_blocking=False,
            ):
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
        color = (
            game.rng.randint(80, 255),
            game.rng.randint(80, 255),
            game.rng.randint(80, 255),
        )
        overrides: dict[str, object] = {
            "name": display_name,
            "color": color,
        }
        if tags:
            overrides["tags"] = tags

        ent = game._spawn_entity_from_template(
            "debug_inventory",
            pos,
            overrides=overrides,
        )
        ent.description = "Definitely NOT a bag, it's much more Platonic than that."
        level.entities[ent.id] = ent
        game.get_inventory(ent.id)  # ensure inventory slot allocated

    desired: list[tuple[str, bool]] = []
    for adj in functional_map.keys():
        desired.append((adj, True))
    for _ in range(3):
        desired.append((next_nonfunc_adj(), False))

    spawned = 0
    center = player.pos
    for adj, is_func in desired:
        spot = find_spot_near(center, r=radius, max_attempts=250)
        if spot is None:
            continue
        spawn_inventory_at(spot, adj, functional=is_func)
        spawned += 1

    if spawned > 0:
        game.log.add(f"Inventory drop! ({spawned} conjured.)")
    else:
        game.log.add("This is no place for an inventory.")


# ---------------------------------------------------------------------------
# NPC Spawning
# ---------------------------------------------------------------------------

def spawn_mentor(game: "Game", level: "LevelState") -> Optional["Actor"]:
    """Place mentor NPC near entry if available."""
    entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
    x, y = entry
    offsets = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (-2, 0), (0, 2), (0, -2)]

    for dx, dy in offsets:
        tx, ty = x + dx, y + dy
        if not _is_tile_spawnable(
            game,
            level,
            (tx, ty),
            avoid_actors=True,
            avoid_entities=False,
            avoid_blocking=False,
        ):
            continue

        aid = game._new_id()
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
        mentor.description = "Old, one-eyed, and syphilitic, yet unerringly optimistic."

        register_actor(game, level, mentor, schedule_ai=False)
        return mentor


    return None


def spawn_intro_npcs(game: "Game", level: "LevelState") -> int:
    """Place the Hexmage and Cartographer near the entry if space allows.

    Returns the number of NPCs placed.
    """
    entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
    x, y = entry
    offsets = [
        (1, 1), (-1, 1), (2, 1), (-2, 1),
        (1, -1), (-1, -1), (2, -1), (-2, -1),
    ]
    npc_specs = [
        ("hexmage", "The Hexmage", "This runecaster is swarming with bees."),
        ("cartographer", "The Cartographer", "This chick is WAY too hot to be a cartographer."),
    ]
    placed = 0

    for npc_id, name, description in npc_specs:
        for dx, dy in offsets:
            tx, ty = x + dx + placed, y + dy
            if not _is_tile_spawnable(
                game,
                level,
                (tx, ty),
                avoid_actors=True,
                avoid_entities=False,
                avoid_blocking=False,
            ):
                continue

            aid = game._new_id()
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
            npc.description = description

            register_actor(game, level, npc, schedule_ai=False)
            placed += 1
            break


    return placed


def spawn_npcs(
    game: "Game",
    level: "LevelState",
    count: int = 1,
) -> int:
    """Spawn generic NPCs from NPC_DEFS near the entry.

    Returns the number placed.
    """
    if count <= 0:
        return 0

    placed = 0
    attempts = 0
    defs = list(npcs.NPC_DEFS.items())

    while placed < count and attempts < 100 and defs:
        attempts += 1
        npc_id, npc_data = defs[min(placed, len(defs) - 1)]

        ex, ey = level.world.entry
        x = max(1, min(level.world.width - 2, ex + (placed * 2)))
        y = max(1, min(level.world.height - 2, ey + 1))

        if not _is_tile_spawnable(
            game,
            level,
            (x, y),
            avoid_actors=True,
            avoid_entities=False,
            avoid_blocking=False,
        ):
            continue

        aid = game._new_id()
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
        register_actor(game, level, actor, schedule_ai=False)
        placed += 1


    return placed


# ---------------------------------------------------------------------------
# Biome-Aware Spawning
# ---------------------------------------------------------------------------

def get_biome_enemy_pool(biome_id: int, include_neutral_factions: bool = True) -> List[str]:
    """Get enemy template IDs appropriate for a specific biome.

    Reads enemies.yaml and filters by tags.biome_spawn.
    For ecology controllers we prefer: biome-specific + generic (if available).
    Results are cached for performance.
    """
    from edgecaster.climate import Biome
    from pathlib import Path

    global _biome_enemy_data_cache, _biome_enemy_pool_cache

    key = (int(biome_id), bool(include_neutral_factions))
    cached = _biome_enemy_pool_cache.get(key)
    if cached is not None:
        return list(cached)

    content_dir = Path(__file__).resolve().parent.parent / "content"
    yaml_path = content_dir / "enemies.yaml"

    # Load + parse YAML once per run
    if _biome_enemy_data_cache is None:
        try:
            with yaml_path.open("r", encoding="utf-8") as f:
                _biome_enemy_data_cache = yaml.safe_load(f) or []
        except FileNotFoundError:
            _biome_enemy_data_cache = []

    data = _biome_enemy_data_cache or []

    # Get biome name (string like "TEMPERATE_FOREST")
    try:
        biome_name = Biome(int(biome_id)).name
    except Exception:
        biome_name = None

    hostile_factions = {
        "hostile",
        "demon_cult_flame",
        "demon_cult_void",
        "demon_cult_flesh",
    }

    neutral_factions = {
        "natures_chosen",
        "high_society",
        "patternkeepers",
    }

    allowed_factions = set(hostile_factions)
    if include_neutral_factions:
        allowed_factions.update(neutral_factions)

    biome_enemies: List[str] = []
    generic_enemies: List[str] = []

    for entry in data:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("id")
        if not tid:
            continue

        faction = entry.get("faction", "hostile")

        # Skip templates, NPCs, player faction
        if faction in ("template", "npc", "player", "neutral"):
            continue

        if faction not in allowed_factions:
            continue

        tags = entry.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}

        if tags.get("no_random_spawn") or tags.get("template") or tags.get("training_dummy"):
            continue

        biome_spawn = tags.get("biome_spawn", [])
        # Be forgiving: allow biome_spawn to be a single string
        if isinstance(biome_spawn, str):
            biome_spawn = [biome_spawn]
        if not isinstance(biome_spawn, list):
            biome_spawn = []

        if biome_spawn and biome_name:
            if biome_name in biome_spawn:
                biome_enemies.append(str(tid))
        elif not biome_spawn:
            generic_enemies.append(str(tid))

    # NEW behavior: prefer biome-specific, but keep generic variety too
    out: List[str] = []
    seen: set[str] = set()

    for tid in biome_enemies + generic_enemies:
        if tid not in seen:
            seen.add(tid)
            out.append(tid)

    if not out:
        out = ["imp"]

    _biome_enemy_pool_cache[key] = list(out)
    return list(out)


def spawn_enemies_for_biome(
    game: "Game",
    level: "LevelState",
    biome_id: int,
    count: int,
    include_neutral_factions: bool = True,
) -> int:
    """Spawn enemies appropriate for the local biome.

    Args:
        game: The Game instance
        level: The LevelState to spawn in
        biome_id: The biome ID at this location
        count: Number of enemies to spawn
        include_neutral_factions: If True, include neutral faction creatures

    Returns:
        Number of enemies actually spawned
    """
    pool = get_biome_enemy_pool(biome_id, include_neutral_factions=include_neutral_factions)
    if not pool:
        return 0

    # Filter by zone difficulty tier (10-tier system).
    zone_tier = getattr(level, "danger_tier", 1) or 1
    filtered_pool, bounds = difficulty_system.filter_enemy_pool(game, pool, zone_tier)
    if filtered_pool:
        pool = filtered_pool
    else:
        pool = _nearest_tier_fallback_pool(pool, bounds, zone_tier)

    spawned = 0
    attempts = 0

    while spawned < count and attempts < 200:
        attempts += 1
        tmpl_id = game.rng.choice(pool)
        try:
            spawn_spec = prototypes.resolve_proto(tmpl_id)
        except Exception:
            spawn_spec = None

        pos = find_spawn_position(game, level, avoid_entities=False, spawn_spec=spawn_spec)
        if pos is None:
            continue

        try:
            # Use the sidecar-aware path so special leaders (e.g. ringmaster,
            # slaver) always bring their coordinated packs in biome spawns too.
            spawn_enemy_with_pack(
                game,
                level,
                tmpl_id,
                pos,
                zone_tier=zone_tier,
                enemy_bounds=bounds.get(tmpl_id),
                schedule_ai=True,
            )
            spawned += 1
        except Exception:
            continue

    return spawned
