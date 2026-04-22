"""LevelState accessor glue and session tracking."""
from __future__ import annotations

from typing import Any
from edgecaster.state.actors import Actor
from edgecaster.systems import entity_ops as entity_ops_system


def current_level(game: Any) -> Any:
    try:
        return game.levels[game.zone_coord]
    except Exception:
        return None


def ensure_player_level_binding(game: Any) -> Any:
    """Recover the current host actor when zone membership drifts."""
    level = current_level(game)
    try:
        player_id = str(getattr(game, "player_id", "") or "").strip()
    except Exception:
        player_id = ""
    if not player_id:
        return level

    if entity_ops_system.get_actor(level, player_id) is not None:
        return level

    maybe_ent = entity_ops_system.get_entity(level, player_id)
    if isinstance(maybe_ent, Actor):
        level.actors[player_id] = maybe_ent
        return level

    for coord, other_level in game.levels.items():
        if coord == game.zone_coord:
            continue
        actor = entity_ops_system.get_actor(other_level, player_id)
        if actor is None:
            maybe_ent = entity_ops_system.get_entity(other_level, player_id)
            if isinstance(maybe_ent, Actor):
                actor = maybe_ent
                other_level.actors[player_id] = actor
        if actor is None:
            continue
        game.zone_coord = coord
        try:
            other_level.entities[player_id] = actor
        except Exception:
            pass
        try:
            other_level.need_fov = True
        except Exception:
            pass
        return other_level

    return level



def is_player_alive(game: Any) -> bool:
    lvl = current_level(game)
    if lvl is None:
        return False
    try:
        player = entity_ops_system.get_actor(lvl, game.player_id)
        if player is None:
            return False
        return player.stats.hp > 0
    except Exception:
        return False
