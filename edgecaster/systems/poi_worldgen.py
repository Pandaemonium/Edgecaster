# edgecaster/systems/poi_worldgen.py
"""
POI World Entity Generation System

Creates world entities for POI contents (NPCs, structures, walls) and stores them
in WorldEntityIndex so they're visible when zooming around the map.

[LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
This module overlaps with `systems/poi_spawning.py` during migration.
End-state is a single resolver/entity-graph realization pipeline.

Follows the same pattern as aggregate_resolution.py for berry patches:
- World entities exist in ABS space independent of zone loading
- Deterministic IDs prevent duplicate spawning
- When a zone loads, world entities are "realized" into actual actors

Yoga principles:
- POI contents exist before observation (camera attention != existence)
- Querying WorldEntityIndex has no gameplay side effects
- Realization is separate from existence
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple, Any
import random

from edgecaster.state.pois import POISpec, NPCSpawnSpecV2, StructureSpec
from edgecaster import prototypes
from edgecaster import spawn_factory

if TYPE_CHECKING:
    from edgecaster.game import Game


# =============================================================================
# Deterministic ID Generation
# =============================================================================

def _stable_hash(*parts: object) -> int:
    """Generate a stable hash from parts (deterministic within run)."""
    s = "|".join(str(p) for p in parts)
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _poi_seed(game: "Game", *parts: object) -> int:
    """Get a deterministic seed based on game seed + parts."""
    base = 0
    try:
        base = int(getattr(game, "fractal_seed", 0) or 0)
    except Exception:
        base = 0
    if not base:
        try:
            base = int(getattr(getattr(game, "cfg", None), "seed", 0) or 0)
        except Exception:
            base = 0
    return (base ^ _stable_hash(*parts)) & 0xFFFFFFFF


def poi_npc_world_id(poi_id: str, npc_id: str, index: int) -> str:
    """Generate deterministic world entity ID for a POI NPC."""
    return f"poi:{poi_id}:npc:{npc_id}:{index}"


def poi_wall_world_id(poi_id: str, abs_x: int, abs_y: int) -> str:
    """Generate deterministic world entity ID for a POI wall."""
    return f"poi:{poi_id}:wall:{abs_x},{abs_y}"


def poi_structure_world_id(poi_id: str, kind: str, index: int) -> str:
    """Generate deterministic world entity ID for a POI structure."""
    return f"poi:{poi_id}:struct:{kind}:{index}"


# =============================================================================
# World Entity Creation
# =============================================================================

@dataclass
class POIWorldEntity:
    """Lightweight world entity for POI content (render-only until realized)."""
    id: str
    poi_id: str
    content_type: str  # "npc", "wall", "structure"

    # Display properties
    name: str
    glyph: str
    color: Tuple[int, int, int]
    description: str = ""

    # Position in ABS space
    abs_pos: Tuple[int, int] = (0, 0)

    # Zone info (derived from abs_pos)
    zone_coord: Tuple[int, int, int] = (0, 0, 0)
    pos: Tuple[int, int] = (0, 0)  # local pos within zone

    # Size for LoD rendering (larger = visible from further away)
    abs_size: float = 1.0

    # Original spec data for realization
    spec_data: Dict[str, Any] = None  # type: ignore

    # Tags for rendering/querying
    tags: Dict[str, Any] = None  # type: ignore

    def __post_init__(self):
        if self.spec_data is None:
            self.spec_data = {}
        if self.tags is None:
            self.tags = {
                "world_entity": True,
                "poi_content": True,
                "poi_id": self.poi_id,
                "content_type": self.content_type,
            }


def _create_npc_world_entity(
    game: "Game",
    poi_spec: POISpec,
    npc_spec: NPCSpawnSpecV2,
    spec_index: int,
    abs_pos: Tuple[int, int],
    zone_w: int,
    zone_h: int,
) -> POIWorldEntity:
    """Create a world entity for an NPC."""
    from edgecaster.content import npcs

    # Get NPC definition for display properties
    npc_def = npcs.NPC_DEFS.get(npc_spec.npc_id, {})

    name = npc_spec.name or npc_def.get("name", npc_spec.npc_id.title())
    glyph = npc_spec.glyph or npc_def.get("glyph", "@")
    color = npc_spec.color or tuple(npc_def.get("color", (255, 255, 255)))
    description = npc_spec.description or npc_def.get("description", "")

    # Calculate zone coord and local pos
    zx = abs_pos[0] // zone_w
    zy = abs_pos[1] // zone_h
    local_x = abs_pos[0] % zone_w
    local_y = abs_pos[1] % zone_h

    eid = poi_npc_world_id(poi_spec.id, npc_spec.npc_id, spec_index)

    return POIWorldEntity(
        id=eid,
        poi_id=poi_spec.id,
        content_type="npc",
        name=name,
        glyph=glyph,
        color=color,
        description=description,
        abs_pos=abs_pos,
        zone_coord=(zx, zy, poi_spec.depth),
        pos=(local_x, local_y),
        abs_size=1.5,  # NPCs visible at moderate zoom
        spec_data={
            "npc_id": npc_spec.npc_id,
            "spec_index": spec_index,
            "npc_spec": npc_spec,
        },
        tags={
            "world_entity": True,
            "poi_content": True,
            "poi_id": poi_spec.id,
            "content_type": "npc",
            "npc_id": npc_spec.npc_id,
        },
    )


def _create_wall_world_entity(
    poi_spec: POISpec,
    abs_x: int,
    abs_y: int,
    zone_w: int,
    zone_h: int,
    wall_color: Tuple[int, int, int] = (140, 120, 100),
) -> POIWorldEntity:
    """Create a world entity for a wall."""
    zx = abs_x // zone_w
    zy = abs_y // zone_h
    local_x = abs_x % zone_w
    local_y = abs_y % zone_h

    eid = poi_wall_world_id(poi_spec.id, abs_x, abs_y)

    return POIWorldEntity(
        id=eid,
        poi_id=poi_spec.id,
        content_type="wall",
        name="Arena Wall",
        glyph="█",
        color=wall_color,
        description="Ancient stone walls.",
        abs_pos=(abs_x, abs_y),
        zone_coord=(zx, zy, poi_spec.depth),
        pos=(local_x, local_y),
        abs_size=3.0,  # Walls visible from further away
        spec_data={
            "wall": True,
        },
        tags={
            "world_entity": True,
            "poi_content": True,
            "poi_id": poi_spec.id,
            "content_type": "wall",
            "blocks_movement": True,
            "blocks_vision": True,
        },
    )


def _create_lair_wall_world_entity(
    poi_spec: POISpec,
    abs_x: int,
    abs_y: int,
    zone_w: int,
    zone_h: int,
    *,
    wall_color: Tuple[int, int, int] = (120, 105, 140),
) -> POIWorldEntity:
    """Create a world entity for a legendary lair wall segment."""
    zx = abs_x // zone_w
    zy = abs_y // zone_h
    local_x = abs_x % zone_w
    local_y = abs_y % zone_h

    eid = poi_wall_world_id(poi_spec.id, abs_x, abs_y)

    return POIWorldEntity(
        id=eid,
        poi_id=poi_spec.id,
        content_type="wall",
        name="Lair Wall",
        glyph="#",
        color=wall_color,
        description="Ruin-scored stone walls.",
        abs_pos=(abs_x, abs_y),
        zone_coord=(zx, zy, poi_spec.depth),
        pos=(local_x, local_y),
        abs_size=2.5,  # Slightly smaller than colosseum walls
        spec_data={
            "wall": True,
        },
        tags={
            "world_entity": True,
            "poi_content": True,
            "poi_id": poi_spec.id,
            "content_type": "wall",
            "blocks_movement": True,
            "blocks_vision": True,
        },
    )


# =============================================================================
# POI World Entity Instantiation
# =============================================================================

def ensure_poi_world_entities(
    game: "Game",
    poi_spec: POISpec,
    *,
    zone_w: int = 60,
    zone_h: int = 40,
) -> int:
    """
    Create world entities for all contents of a POI and add to WorldEntityIndex.

    This function is idempotent - calling it multiple times won't create duplicates.
    Returns the number of new entities created.

    Args:
        game: Game instance
        poi_spec: The POI specification
        zone_w: Zone width in tiles
        zone_h: Zone height in tiles

    Returns:
        Number of world entities created
    """
    # Track which POIs we've processed
    if not hasattr(game, "_poi_worldgen_done"):
        game._poi_worldgen_done: Set[str] = set()  # type: ignore

    if poi_spec.id in game._poi_worldgen_done:  # type: ignore
        return 0

    tags = poi_spec.tags or {}
    show_on_map = bool(tags.get("show_on_map", False))
    worldgen_on_rumor = bool(tags.get("worldgen_on_rumor", False))

    # Only build world proxies for POIs intended for world-scale visibility.
    # Local POIs (e.g. starting_zone NPCs) should be realized by zone loading
    # only, otherwise they duplicate with local spawned actors.
    if not show_on_map and not worldgen_on_rumor:
        return 0

    # Defer rumor-gated POIs until they are known.
    if worldgen_on_rumor:
        rumored = set(getattr(game, "rumored_pois", set()) or set())
        discovered = set(getattr(game, "discovered_pois", set()) or set())
        if poi_spec.id not in rumored and poi_spec.id not in discovered:
            return 0

    # Ensure world_entity_index exists
    if getattr(game, "world_entity_index", None) is None:
        return 0

    created = 0

    # Create NPC world entities
    for spec_index, npc_spec in enumerate(poi_spec.npc_specs):
        # Pick the first ABS position for this NPC
        abs_positions = getattr(npc_spec, "abs_positions", []) or []
        if not abs_positions:
            # Fall back to anchor + offsets
            offsets = npc_spec.offsets or [(0, 0)]
            abs_positions = [
                (poi_spec.anchor_abs[0] + dx, poi_spec.anchor_abs[1] + dy)
                for dx, dy in offsets
            ]

        if abs_positions:
            # Use first position as canonical spawn point
            abs_pos = abs_positions[0]
            ent = _create_npc_world_entity(
                game, poi_spec, npc_spec, spec_index, abs_pos, zone_w, zone_h
            )

            try:
                game.world_entity_index.add(
                    ent,
                    zone_coord=ent.zone_coord,
                    local_pos=ent.pos,
                )
                created += 1
            except Exception:
                pass

    # Create structure world entities (walls, etc.)
    for struct_index, struct_spec in enumerate(poi_spec.structure_specs):
        if struct_spec.kind == "colosseum_arena":
            # Generate wall positions for the arena
            wall_count = _create_colosseum_wall_entities(
                game, poi_spec, struct_spec, zone_w, zone_h
            )
            created += wall_count
        elif struct_spec.kind == "legendary_lair":
            wall_count = _create_legendary_lair_world_entities(
                game, poi_spec, struct_spec, zone_w, zone_h
            )
            created += wall_count

    game._poi_worldgen_done.add(poi_spec.id)  # type: ignore

    # Debug logging
    try:
        with open("C:/Games/Edgecaster/debug.log", "a") as f:
            f.write(f"[poi_worldgen] Created {created} world entities for POI '{poi_spec.id}'\n")
    except Exception:
        pass

    return created


def _create_colosseum_wall_entities(
    game: "Game",
    poi_spec: POISpec,
    struct_spec: StructureSpec,
    zone_w: int,
    zone_h: int,
) -> int:
    """Create wall world entities for a colosseum arena structure."""
    fp = poi_spec.footprint

    # Calculate ellipse parameters
    arena_cx = (fp.x0 + fp.x1) / 2.0
    arena_cy = (fp.y0 + fp.y1) / 2.0
    arena_rx = (fp.x1 - fp.x0) / 2.0
    arena_ry = (fp.y1 - fp.y0) / 2.0

    wall_thickness = int(struct_spec.tags.get("wall_thickness", 3))
    wall_color = (140, 120, 100)

    # Pre-compute entrance positions
    entrance_width = 4
    entrance_positions: Set[Tuple[int, int]] = set()
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

    created = 0

    # Iterate through entire footprint and create wall entities
    for abs_y in range(fp.y0, fp.y1):
        for abs_x in range(fp.x0, fp.x1):
            dx = abs_x - arena_cx
            dy = abs_y - arena_cy

            if arena_rx <= 0 or arena_ry <= 0:
                continue

            # Normalized distance from center
            dist_norm = (dx * dx) / (arena_rx * arena_rx) + (dy * dy) / (arena_ry * arena_ry)

            # Inner ellipse boundary
            inner_rx = arena_rx - wall_thickness
            inner_ry = arena_ry - wall_thickness
            if inner_rx > 0 and inner_ry > 0:
                inner_dist = (dx * dx) / (inner_rx * inner_rx) + (dy * dy) / (inner_ry * inner_ry)
            else:
                inner_dist = 0

            # Wall: inside outer ellipse, outside inner ellipse, not at entrance
            if dist_norm <= 1.0 and inner_dist >= 1.0:
                if (abs_x, abs_y) not in entrance_positions:
                    ent = _create_wall_world_entity(
                        poi_spec, abs_x, abs_y, zone_w, zone_h, wall_color
                    )
                    try:
                        game.world_entity_index.add(
                            ent,
                            zone_coord=ent.zone_coord,
                            local_pos=ent.pos,
                        )
                        created += 1
                    except Exception:
                        pass

    return created


def _create_legendary_lair_world_entities(
    game: "Game",
    poi_spec: POISpec,
    struct_spec: StructureSpec,
    zone_w: int,
    zone_h: int,
) -> int:
    """Create wall world entities for a legendary lair structure.

    We generate the lair layout deterministically from the lair seed and
    then extract only boundary walls (walls adjacent to floor) so the
    ruin reads clearly in God Vision without flooding the index with
    every wall tile.
    """
    from edgecaster.state.world import World
    from edgecaster import mapgen_sites

    layout = str(struct_spec.tags.get("layout") or "multi_room")
    seed = struct_spec.tags.get("lair_seed", poi_spec.seed)
    try:
        seed = int(seed) & 0xFFFFFFFF
    except Exception:
        seed = int(poi_spec.seed) & 0xFFFFFFFF

    rng = random.Random(seed)
    world = World(width=zone_w, height=zone_h)
    try:
        mapgen_sites.generate_legendary_lair(world, rng, layout=layout)
    except Exception:
        return 0

    def has_adjacent_floor(x: int, y: int) -> bool:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not world.in_bounds(nx, ny):
                continue
            tile = world.get_tile(nx, ny)
            if tile and tile.walkable:
                return True
        return False

    created = 0
    fp = poi_spec.footprint
    for y in range(world.height):
        for x in range(world.width):
            tile = world.get_tile(x, y)
            if tile is None or tile.walkable:
                continue
            if not has_adjacent_floor(x, y):
                continue

            abs_x = fp.x0 + x
            abs_y = fp.y0 + y
            ent = _create_lair_wall_world_entity(
                poi_spec, abs_x, abs_y, zone_w, zone_h
            )
            try:
                game.world_entity_index.add(
                    ent,
                    zone_coord=ent.zone_coord,
                    local_pos=ent.pos,
                )
                created += 1
            except Exception:
                pass

    return created


def ensure_all_poi_world_entities(
    game: "Game",
    *,
    zone_w: int = 60,
    zone_h: int = 40,
) -> int:
    """
    Create world entities for all POIs in the registry.

    Call this during game initialization to make all known POIs
    visible when zooming around.
    """
    from edgecaster.content.pois import get_poi_registry

    registry = get_poi_registry(zone_w, zone_h)
    total = 0

    for poi_spec in registry:
        created = ensure_poi_world_entities(game, poi_spec, zone_w=zone_w, zone_h=zone_h)
        total += created

    return total


# =============================================================================
# Realization: Convert world POI content intents into runtime entities/actors
# =============================================================================

def realize_poi_npc_spec(
    game: "Game",
    level: Any,  # LevelState
    coord: Tuple[int, int, int],
    *,
    poi_id: str,
    npc_spec: NPCSpawnSpecV2,
    spec_index: int,
    spawn_pos: Tuple[int, int],
) -> Optional[Any]:
    """Realize one POI NPC spec at a chosen local position."""
    from edgecaster.content import npcs
    from edgecaster.enemies import factory as enemy_factory
    from edgecaster.state.actors import Human, Stats
    from edgecaster.systems import entity_ops as entity_ops_system
    from edgecaster.systems import spawning as spawning_system

    registry = getattr(game, "poi_registry", None)
    if registry is None:
        return None

    spec_key = f"npc:{npc_spec.npc_id}:{int(spec_index)}"
    if registry.is_npc_spawned(poi_id, spec_key):
        return None
    if registry.is_npc_dead(poi_id, spec_key):
        return None

    if entity_ops_system.actor_at(level, spawn_pos) is not None:
        return None

    npc_def = npcs.NPC_DEFS.get(npc_spec.npc_id, {})
    name = npc_spec.name or npc_def.get("name", npc_spec.npc_id.title())
    glyph = npc_spec.glyph or npc_def.get("glyph", "@")
    color = npc_spec.color or tuple(npc_def.get("color", (255, 255, 255)))

    actor = None
    if npc_spec.npc_id == "caged_demon":
        actor = enemy_factory.spawn_enemy(
            "caged_demon",
            spawn_pos,
            abs_pos=game.abs_from_zone_local(coord, spawn_pos),
        )
        actor.faction = "neutral"
        actor.actions = ()
        actor.ai = "idle"
        actor.tags = getattr(actor, "tags", {}) or {}
        actor.tags["npc_id"] = npc_spec.npc_id
        actor.tags["show_exact_hp"] = True
        actor.show_exact_hp = True
        desc = npc_spec.description or npc_def.get("description") or actor.description
        if desc:
            actor.description = desc
        actor.regen_per_tick = (1, 10)
        game._start_regen(level, actor.id, amount=1, interval=10)
    elif npc_spec.npc_id == "merchant":
        actor = enemy_factory.spawn_enemy(
            "merchant",
            spawn_pos,
            abs_pos=game.abs_from_zone_local(coord, spawn_pos),
        )
        actor.faction = "npc"
        actor.actions = ()
        actor.ai = "idle"
        actor.tags = getattr(actor, "tags", {}) or {}
        actor.tags["npc_id"] = npc_spec.npc_id
        actor.tags["merchant_id"] = npc_def.get("merchant_id", "general_store")
        if poi_id == "starting_zone":
            actor.tags["merchant_all_items"] = True
            actor.tags["merchant_refresh_on_talk"] = True
        actor.name = name
        actor.glyph = glyph
        actor.color = color
        desc = npc_spec.description or npc_def.get("description") or actor.description
        if desc:
            actor.description = desc
        try:
            from edgecaster.systems import trade as trade_system

            trade_system.ensure_merchant_initialized(game, level, actor)
        except Exception:
            pass
    else:
        aid = game._new_id()
        actor = Human(
            id=aid,
            name=name,
            pos=spawn_pos,
            abs_pos=game.abs_from_zone_local(coord, spawn_pos),
            faction="npc",
            stats=Stats(hp=50, max_hp=50),
            tags={"npc_id": npc_spec.npc_id},
            disposition=npc_def.get("base_disposition", 0),
            affiliations=tuple(npc_def.get("factions", [])),
            glyph=glyph,
            color=color,
        )
        desc = npc_spec.description or npc_def.get("description")
        if desc:
            actor.description = desc
        actor.tags.setdefault("npc_id", npc_spec.npc_id)
        if npc_def.get("show_exact_hp", False):
            actor.tags["show_exact_hp"] = True
            actor.show_exact_hp = True

    if actor is None:
        return None

    extra_tags = getattr(npc_spec, "tags", None) or {}
    if isinstance(extra_tags, dict):
        try:
            actor.tags.update(extra_tags)
        except Exception:
            pass
    actor.tags = getattr(actor, "tags", {}) or {}
    actor.tags["poi_id"] = poi_id
    actor.tags["poi_spec_key"] = spec_key

    spawning_system.register_actor(game, level, actor, schedule_ai=False)
    registry.mark_npc_spawned(poi_id, spec_key, actor.id)
    return actor


def realize_poi_wall_at(
    game: "Game",
    level: Any,  # LevelState
    *,
    pos: Tuple[int, int],
    wall_eid: str,
    glyph: str = "#",
    color: Tuple[int, int, int] = (140, 120, 100),
    name: str = "Arena Wall",
    description: str = "Ancient stone walls.",
    floor_color: Tuple[int, int, int] = (180, 160, 120),
    deterministic_recipe: Optional[str] = None,
) -> Optional[Any]:
    """Realize one wall entity into a loaded level."""
    from edgecaster.systems import entity_ops as entity_ops_system

    if str(wall_eid) in level.entities:
        return None
    if entity_ops_system.entity_at(level, pos) is not None:
        return None

    recipe = str(deterministic_recipe or wall_eid)
    try:
        ent = game._spawn_entity_from_template(
            "wall",
            pos,
            overrides={
                "id": str(wall_eid),
                "glyph": str(glyph or "#")[:1],
                "color": list(color),
                "name": str(name or "Wall"),
                "description": str(description or ""),
            },
            deterministic=True,
            deterministic_recipe=recipe,
            deterministic_namespace="poi",
            zone_coord=getattr(game, "zone_coord", (0, 0, 0)),
        )
    except TypeError:
        try:
            ent = game._spawn_entity_from_template(
                "wall",
                pos,
                overrides={
                    "id": str(wall_eid),
                    "glyph": str(glyph or "#")[:1],
                    "color": list(color),
                    "name": str(name or "Wall"),
                    "description": str(description or ""),
                },
            )
        except Exception:
            return None
    except Exception:
        return None

    try:
        ent.id = str(wall_eid)
    except Exception:
        pass
    level.entities[str(wall_eid)] = ent

    tile = level.world.get_tile(*pos)
    if tile:
        tile.walkable = True
        tile.glyph = "."
        tile.color = floor_color
    return ent


def spawn_poi_contents(
    game: "Game",
    level: Any,  # LevelState
    coord: Tuple[int, int, int],
) -> None:
    """Unified runtime POI realization entrypoint.

    [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_5]
    Temporary wrapper while `poi_spawning.py` logic is incrementally absorbed.
    """
    from edgecaster.systems import poi_spawning as poi_spawning_system

    poi_spawning_system.spawn_poi_contents(game, level, coord)
