"""Entity/Actor query and status helper operations.

This module extracts frequently used query/status helpers from Game to keep
`game.py` focused on orchestration.
"""

from __future__ import annotations

from typing import Optional, Tuple, List, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state.actors import Actor
    from edgecaster.state.entities import Entity


def actor_at(level: "LevelState", pos: Tuple[int, int]) -> Optional["Actor"]:
    for actor in level.actors.values():
        if actor.pos == pos and actor.alive:
            return actor
    return None


def all_actors(level: "LevelState") -> List["Actor"]:
    return [a for a in level.actors.values() if a.alive]


def entity_at(level: "LevelState", pos: Tuple[int, int]) -> Optional["Entity"]:
    """Return primary entity at tile, preferring non-actor items/features."""
    from edgecaster.state.actors import Actor

    item_candidate: Optional["Entity"] = None
    actor_candidate: Optional["Entity"] = None

    for ent in level.entities.values():
        if ent.pos != pos:
            continue
        if isinstance(ent, Actor):
            if actor_candidate is None:
                actor_candidate = ent
        else:
            if item_candidate is None:
                item_candidate = ent
    return item_candidate or actor_candidate


def items_at(level: "LevelState", pos: Tuple[int, int]) -> List["Entity"]:
    return [
        e for e in level.entities.values()
        if getattr(e, "pos", None) == pos
        and getattr(e, "kind", None) == "item"
    ]


def all_entities(level: "LevelState") -> List["Entity"]:
    return list(level.entities.values())


def blocking_entity_at(level: "LevelState", pos: Tuple[int, int]) -> Optional["Entity"]:
    ent = entity_at(level, pos)
    if ent and getattr(ent, "blocks_movement", False):
        return ent
    return None


def toggle_door(game: "Game", ent: "Entity", level: "LevelState", notify: bool = False) -> None:
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

