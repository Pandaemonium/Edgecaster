"""
Spawning System - Entity factories and spawn orchestration.

This module manages:
- Template loading and caching for enemies and entities
- Position finding for valid spawn locations
- Entity instantiation from prototypes
- Actor registration and AI scheduling

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
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


# ---------------------------------------------------------------------------
# Template Loading (Cached)
# ---------------------------------------------------------------------------

_enemy_ids_cache: Optional[List[str]] = None
_entity_templates_cache: Optional[Dict[str, dict]] = None


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
    max_attempts: int = 100,
) -> Optional[Tuple[int, int]]:
    """Find a valid spawn position in the level.

    Args:
        game: The Game instance
        level: The LevelState to spawn in
        near: If provided, search near this position within radius
        radius: Search radius when 'near' is provided
        avoid_actors: Skip tiles with actors
        avoid_entities: Skip tiles with entities
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

        if not world.in_bounds(x, y):
            continue
        if not world.is_walkable(x, y):
            continue
        if avoid_actors and game._actor_at(level, (x, y)):
            continue
        if avoid_entities and game._entity_at(level, (x, y)):
            continue

        return (x, y)

    return None


def find_nearest_walkable(
    game: "Game",
    level: "LevelState",
    origin: Tuple[int, int],
    max_radius: int = 12,
) -> Optional[Tuple[int, int]]:
    """Find the nearest walkable, unoccupied tile to origin.

    Searches in expanding rings from origin.
    """
    ox, oy = origin
    world = level.world

    # Check origin first
    if (world.in_bounds(ox, oy) and
        world.is_walkable(ox, oy) and
        not game._actor_at(level, (ox, oy))):
        return origin

    # Search expanding rings
    for r in range(1, max_radius + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                tx, ty = ox + dx, oy + dy
                if not world.in_bounds(tx, ty):
                    continue
                if not world.is_walkable(tx, ty):
                    continue
                if game._actor_at(level, (tx, ty)):
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
) -> "Entity":
    """Instantiate a plain Entity from the unified prototype bucket.

    Uses prototypes.resolve_proto() so inheritance works across entities.yaml/enemies.yaml/etc.
    `overrides` merges on top; `tags` merge dict-wise (entity-style).
    """
    spec = prototypes.resolve_proto(template_id)
    if not spec:
        raise KeyError(f"Unknown prototype id {template_id!r}")

    eid = game._new_id()
    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid=eid,
        pos=pos,
        overrides=overrides,
    )

    # Initialize per-instance item charges
    _init_entity_charges(game, ent)

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
    level.actors[actor.id] = actor
    level.entities[actor.id] = actor

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

        if not level.world.in_bounds(x, y):
            continue
        if not level.world.is_walkable(x, y):
            continue
        if game._actor_at(level, (x, y)):
            continue
        if game._entity_at(level, (x, y)):
            continue

        place_entity((x, y))
        spawned += 1

    return spawned


# ---------------------------------------------------------------------------
# Specific Spawn Functions
# ---------------------------------------------------------------------------

def spawn_enemies(
    game: "Game",
    level: "LevelState",
    count: int,
) -> int:
    """Spawn random enemies using the data-driven enemy factory.

    Returns the number of enemies spawned.
    """
    spawned = 0
    attempts = 0

    while spawned < count and attempts < 200:
        attempts += 1
        pos = find_spawn_position(game, level, avoid_entities=False)
        if pos is None:
            continue

        # Pick a random enemy template id from enemies.yaml
        enemy_ids = get_enemy_template_ids(game)
        tmpl_id = game.rng.choice(enemy_ids)

        mob = enemy_factory.spawn_enemy(tmpl_id, pos)

        # 20% bismuth imps
        if tmpl_id == "imp" and game.rng.random() < 0.2:
            mob.tags = getattr(mob, "tags", None) or {}
            mob.tags["visual_effects"] = ["bismuth"]
            if not mob.name.lower().startswith("bismuth "):
                mob.name = "bismuth imp"

        # Slaver packs: a slaver arrives chained to two brutes
        if tmpl_id == "slaver":
            _spawn_slaver_pack(game, level, mob, pos)

        register_actor(game, level, mob, schedule_ai=True)
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

        if not level.world.in_bounds(tx, ty):
            continue
        if not level.world.is_walkable(tx, ty):
            continue
        if game._actor_at(level, (tx, ty)):
            continue
        if game._blocking_entity_at(level, (tx, ty)):
            continue
        if game._entity_at(level, (tx, ty)):
            continue

        brute = enemy_factory.spawn_enemy("shackled_brute", (tx, ty))
        brute.tags = getattr(brute, "tags", None) or {}
        brute.tags["slaver_master_id"] = slaver.id
        brute.tags["slaver_group_id"] = group_id

        register_actor(game, level, brute, schedule_ai=True)
        brute_ids.append(brute.id)

    if brute_ids:
        slaver.tags["slaver_minion_ids"] = brute_ids


def spawn_imps_near(
    game: "Game",
    level: "LevelState",
    center: Tuple[int, int],
    count: int,
    radius: int = 3,
) -> int:
    """Spawn up to `count` imps within `radius` tiles of center."""

    def place_imp(pos: Tuple[int, int]) -> None:
        imp = enemy_factory.spawn_enemy("imp", pos)

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
        echo = enemy_factory.spawn_enemy("fractal_echo", pos)
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

        if not world.in_bounds(x, y):
            continue
        if not world.is_walkable(x, y):
            continue
        if game._actor_at(level, (x, y)):
            continue
        if game._entity_at(level, (x, y)):
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

            if not world.in_bounds(x, y):
                continue
            if not world.is_walkable(x, y):
                continue
            if game._actor_at(level, (x, y)):
                continue
            if game._entity_at(level, (x, y)):
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
        if not level.world.in_bounds(tx, ty):
            continue
        if not level.world.is_walkable(tx, ty):
            continue
        if game._actor_at(level, (tx, ty)):
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

        level.actors[aid] = mentor
        level.entities[aid] = mentor
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
            if not level.world.in_bounds(tx, ty):
                continue
            if not level.world.is_walkable(tx, ty):
                continue
            if game._actor_at(level, (tx, ty)):
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

            level.actors[aid] = npc
            level.entities[aid] = npc
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

        if not level.world.is_walkable(x, y) or game._actor_at(level, (x, y)):
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
        level.actors[aid] = actor
        level.entities[aid] = actor
        placed += 1

    return placed
