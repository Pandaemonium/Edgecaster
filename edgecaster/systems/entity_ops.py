"""Entity/Actor query and status helper operations.

This module extracts frequently used query/status helpers from Game to keep
`game.py` focused on orchestration.
"""

from __future__ import annotations

from typing import Optional, Tuple, List, TYPE_CHECKING, Iterable
from edgecaster.systems import footprints as footprints_system
from edgecaster.systems import entity_snapshots as entity_snapshots_system

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state.actors import Actor
    from edgecaster.state.entities import Entity


def _is_suppressed(game: "Game | None", entity: object) -> bool:
    """Return True if *entity* is marked removed/dead in entity_state."""
    if game is None:
        return False
    try:
        return game.entity_is_suppressed(entity)
    except Exception:
        return False


def actor_at(
    level: "LevelState",
    pos: Tuple[int, int],
    *,
    game: "Game | None" = None,
) -> Optional["Actor"]:
    for actor in level.actors.values():
        if not actor.alive:
            continue
        if _is_suppressed(game, actor):
            continue
        if footprints_system.entity_overlaps_tile(actor, pos):
            return actor
    return None


def actors_overlapping_rect(
    level: "LevelState",
    rect: tuple[float, float, float, float],
    *,
    exclude_id: str | None = None,
) -> List["Actor"]:
    out: List["Actor"] = []
    for actor in level.actors.values():
        if actor is None or not getattr(actor, "alive", False):
            continue
        if exclude_id is not None and getattr(actor, "id", None) == exclude_id:
            continue
        try:
            if footprints_system.rect_overlaps(
                footprints_system.entity_footprint_local(actor),
                rect,
            ):
                out.append(actor)
        except Exception:
            continue
    return out


def first_actor_overlapping_rect(
    level: "LevelState",
    rect: tuple[float, float, float, float],
    *,
    exclude_id: str | None = None,
) -> Optional["Actor"]:
    for actor in actors_overlapping_rect(level, rect, exclude_id=exclude_id):
        return actor
    return None


def entities_overlapping_rect(
    level: "LevelState",
    rect: tuple[float, float, float, float],
    *,
    exclude_ids: Iterable[str] | None = None,
    include_actor_entities: bool = True,
) -> List["Entity"]:
    out: List["Entity"] = []
    skip = set(str(eid) for eid in (exclude_ids or ()))
    actor_ids = set(getattr(level, "actors", {}).keys())
    for ent in level.entities.values():
        ent_id = str(getattr(ent, "id", "") or "")
        if ent_id in skip:
            continue
        if not include_actor_entities and ent_id in actor_ids:
            continue
        try:
            if footprints_system.rect_overlaps(
                footprints_system.entity_footprint_local(ent),
                rect,
            ):
                out.append(ent)
        except Exception:
            continue
    return out


def first_entity_overlapping_rect(
    level: "LevelState",
    rect: tuple[float, float, float, float],
    *,
    exclude_ids: Iterable[str] | None = None,
    include_actor_entities: bool = True,
) -> Optional["Entity"]:
    for ent in entities_overlapping_rect(
        level,
        rect,
        exclude_ids=exclude_ids,
        include_actor_entities=include_actor_entities,
    ):
        return ent
    return None


def all_actors(level: "LevelState") -> List["Actor"]:
    return [a for a in level.actors.values() if a.alive]


def entity_at(
    level: "LevelState",
    pos: Tuple[int, int],
    *,
    game: "Game | None" = None,
) -> Optional["Entity"]:
    """Return primary entity at tile, preferring non-actor items/features."""
    from edgecaster.state.actors import Actor

    item_candidate: Optional["Entity"] = None
    actor_candidate: Optional["Entity"] = None

    for ent in level.entities.values():
        if _is_suppressed(game, ent):
            continue
        if not footprints_system.entity_overlaps_tile(ent, pos):
            continue
        if isinstance(ent, Actor):
            if actor_candidate is None:
                actor_candidate = ent
        else:
            if item_candidate is None:
                item_candidate = ent
    return item_candidate or actor_candidate


def items_at(
    level: "LevelState",
    pos: Tuple[int, int],
    *,
    game: "Game | None" = None,
) -> List["Entity"]:
    return [
        e for e in level.entities.values()
        if footprints_system.entity_overlaps_tile(e, pos)
        and getattr(e, "kind", None) == "item"
        and not _is_suppressed(game, e)
    ]


def all_entities(level: "LevelState") -> List["Entity"]:
    return list(level.entities.values())


def blocking_entity_at(
    level: "LevelState",
    pos: Tuple[int, int],
    *,
    game: "Game | None" = None,
) -> Optional["Entity"]:
    for ent in level.entities.values():
        if _is_suppressed(game, ent):
            continue
        if footprints_system.entity_blocks_movement_at(ent, pos):
            return ent
    return None


def blocking_entity_overlapping_rect(
    level: "LevelState",
    rect: tuple[float, float, float, float],
    *,
    exclude_ids: Iterable[str] | None = None,
    ignore_actor_entities: bool = True,
) -> Optional["Entity"]:
    skip = set(exclude_ids or [])
    actor_ids = set(getattr(level, "actors", {}).keys())
    for ent in level.entities.values():
        ent_id = str(getattr(ent, "id", "") or "")
        if ent_id in skip:
            continue
        if ignore_actor_entities and ent_id in actor_ids:
            continue
        try:
            if footprints_system.entity_blocks_movement_in_rect(ent, rect):
                return ent
        except Exception:
            continue
    return None


def toggle_door(game: "Game", ent: "Entity", level: "LevelState", notify: bool = False) -> None:
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
            game.log.add("You open the door.")
    else:
        tags["door_state"] = "closed"
        ent.blocks_movement = True
        ent.glyph = "+"
        ent.color = getattr(ent, "color", (180, 140, 80))
        if tile:
            tile.walkable = False
            tile.glyph = "+"
        if notify:
            game.log.add("You close the door.")

    # Unification note: door toggles are one of the clearest examples of a
    # deterministic entity mutating while it may later collapse out of the
    # active zone. Persist through the shared snapshot bridge so this uses the
    # same patch shape as attention collapse/re-realization.
    entity_snapshots_system.persist_entity_snapshot(
        game,
        ent,
        entity_id=str(getattr(ent, "entity_id", "") or getattr(ent, "id", "") or ""),
        lineage_id=str(tags.get("lineage_id", "") or "") or None,
    )

    ent.tags = tags
    level.need_fov = True
    game._update_fov(level)


def add_status(game: "Game", actor: "Actor", name: str, duration: int, on_apply: str | None = None) -> None:
    actor.statuses[name] = max(duration, actor.statuses.get(name, 0))
    if on_apply:
        game.log.add(on_apply)


def tick_status(actor: "Actor", name: str) -> None:
    if name not in actor.statuses:
        return
    actor.statuses[name] -= 1
    if actor.statuses[name] <= 0:
        del actor.statuses[name]


def has_status(actor: "Actor", name: str) -> bool:
    return actor.statuses.get(name, 0) > 0
