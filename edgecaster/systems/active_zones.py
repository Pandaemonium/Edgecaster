"""Active-zone window and cross-zone movement management.

Treat zone adjacency as a cache window only.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from edgecaster.systems import zones as zones_system
from edgecaster.systems import entity_ops as entity_ops_system


def active_zone_coords(
    game: Any,
    *,
    center: Optional[Tuple[int, int, int]] = None,
    radius: Optional[int] = None,
) -> List[Tuple[int, int, int]]:
    """Return a list of zone coords within the active radius (Chebyshev)."""
    if center is None:
        center = game.zone_coord
    if radius is None:
        radius = int(getattr(game, "active_zone_radius", 1) or 1)
    radius = max(0, int(radius))

    zx, zy, zz = center
    max_screen = max(0, int(game.cfg.world_map_screens) - 1)
    coords: List[Tuple[int, int, int]] = []
    seen: set[Tuple[int, int, int]] = set()
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


def active_zone_coords_prioritized(
    game: Any,
    *,
    center: Optional[Tuple[int, int, int]] = None,
    radius: Optional[int] = None,
    dir_hint: Optional[Tuple[int, int]] = None,
) -> List[Tuple[int, int, int]]:
    """
    Return active-zone coords ordered by likely movement relevance.
    """
    if center is None:
        center = game.zone_coord
    cx, cy, _cz = center
    coords = active_zone_coords(game, center=center, radius=radius)

    dxh = dyh = 0
    if dir_hint is not None:
        try:
            dxh = int(dir_hint[0])
            dyh = int(dir_hint[1])
        except Exception:
            dxh = dyh = 0

    def score(c: Tuple[int, int, int]) -> Tuple[int, int, int]:
        zx, zy, _ = c
        ddx = int(zx) - int(cx)
        ddy = int(zy) - int(cy)
        cheb = max(abs(ddx), abs(ddy))
        dot = ddx * dxh + ddy * dyh
        man = abs(ddx) + abs(ddy)
        return (cheb, -dot, man)

    coords.sort(key=score)
    return coords


def ensure_active_zones_loaded(game: Any) -> List[Any]:
    """
    Ensure the current zone is loaded and incrementally prewarm neighbors.
    """
    try:
        if game.zone_coord not in game.levels:
            zones_system.get_zone(game, game.zone_coord, up_pos=None)
    except Exception:
        pass

    game._seed_zone_prewarm_queue()
    budget = int(getattr(game, "zone_prewarm_budget_per_advance", 1) or 1)
    game._drain_zone_prewarm_queue(budget)

    levels = game._loaded_active_levels()
    if not levels:
        try:
            levels = [game._level()]
        except Exception:
            levels = []
    return levels


def is_zone_active(game: Any, coord: Optional[Tuple[int, int, int]]) -> bool:
    """Return True if a zone coord is within the active-radius window."""
    if coord is None:
        return False
    zx, zy, zz = coord
    cx, cy, cz = game.zone_coord
    if int(zz) != int(cz):
        return False
    radius = int(getattr(game, "active_zone_radius", 1) or 1)
    radius = max(0, radius)
    return max(abs(int(zx) - int(cx)), abs(int(zy) - int(cy))) <= radius


def move_actor_to_abs(
    game: Any,
    actor: Any,
    abs_pos: Tuple[int, int],
    *,
    from_level: Optional[Any] = None,
) -> None:
    """Move a non-player actor across zone boundaries using ABS coordinates."""
    if getattr(actor, "id", None) == game.player_id:
        game._move_player_to_abs(abs_pos)
        return

    if from_level is None:
        try:
            for lvl in game.levels.values():
                if entity_ops_system.get_actor(lvl, actor.id) is not None:
                    from_level = lvl
                    break
        except Exception:
            from_level = None
    if from_level is None:
        return

    dest_coord, dest_local = game.zone_local_from_abs(
        abs_pos,
        depth=getattr(from_level, "coord", game.zone_coord)[2],
        clamp_to_world=True,
    )
    dest_level = zones_system.get_zone(game, dest_coord, up_pos=None)
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

        game._set_entity_local_pos(actor, dest_local)
        dest_level.actors[actor.id] = actor
        try:
            dest_level.entities[actor.id] = actor
        except Exception:
            pass
        try:
            dest_level.spatial_dirty = True
        except Exception:
            pass

        try:
            tags = getattr(actor, "tags", None) or {}
            if tags.get("ai"):
                game._schedule(
                    dest_level,
                    game.cfg.action_time_fast,
                    lambda aid=actor.id, lvl=dest_level: game._monster_act(lvl, aid),
                )
        except Exception:
            pass
    else:
        game._set_entity_local_pos(actor, dest_local)
        try:
            dest_level.spatial_dirty = True
        except Exception:
            pass

    game._set_entity_abs_pos(actor, (int(abs_pos[0]), int(abs_pos[1])))


def advance_time(game: Any, level: Any, delta: int) -> None:
    """Advance time by delta ticks across the active zone window."""
    from edgecaster.systems import perf_profiler
    from edgecaster.systems import scheduling
    from edgecaster.systems import ambient_spawns as ambient_spawns_system

    with perf_profiler.measure(game, "game._advance_time"):
        try:
            delta = int(delta)
        except Exception:
            delta = int(delta or 0)
        if delta <= 0:
            return

        if game.cfg.allow_zone_prewarm_during_tick:
            active_levels = ensure_active_zones_loaded(game)
            if not active_levels:
                active_levels = [level]
        else:
            active_levels = [level]

        current_level = game._level()
        for lvl in active_levels:
            apply_player_systems = (lvl is current_level)
            scheduling.advance_time(game, lvl, delta, apply_player_systems=apply_player_systems)

        ambient_spawns_system.maintain_population(game, active_levels, delta)