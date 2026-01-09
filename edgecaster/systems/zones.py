"""
Zone management system.

Handles:
- Zone creation and initialization
- Zone retrieval with lazy creation
- Stair usage (up/down)
- Zone boundary transitions
- Fast travel

All functions accept (game, ...) as parameters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState
    from edgecaster.state.actors import Actor


def get_zone(
    game: "Game",
    coord: Tuple[int, int, int],
    up_pos: Optional[Tuple[int, int]] = None,
) -> "LevelState":
    """
    Get (and lazily create) a zone at the given coordinate.

    This resets pattern state and coherence when entering an existing zone.
    For rendering purposes without side effects, use get_zone_for_render.
    """
    from edgecaster.patterns import builder

    if coord not in game.levels:
        game.levels[coord] = game._make_zone(coord, up_pos=up_pos)
        # Default random spawns for most zones, but skip special "site" zones
        try:
            w = game.levels[coord].world
            is_special = bool(getattr(w, "is_lab", False) or getattr(w, "is_lair", False))
        except Exception:
            is_special = False
        if not is_special:
            game._spawn_enemies(game.levels[coord], count=4)

    # Entering any zone clears pattern and resets coherence
    lvl = game.levels[coord]
    lvl.pattern = builder.Pattern()
    lvl.pattern_anchor = None
    lvl.activation_points = []
    lvl.activation_ttl = 0
    lvl.acidic_pattern = False  # Clear corrosive melt
    # Stop any lingering motion when the pattern is cleared.
    lvl.pattern_motion = None

    # Reset coherence to max on zone change
    player = game._player() if hasattr(game, "_player") else None
    if player:
        player.stats.coherence = player.stats.max_coherence

    # POI discovery: entering a zone reveals its POI markers on the world map.
    try:
        game._discover_pois_for_level(lvl)
    except Exception:
        pass

    return game.levels[coord]


def get_zone_for_render(game: "Game", coord: Tuple[int, int, int]) -> "LevelState":
    """
    Get (and lazily create) a zone for *rendering* purposes.

    Unlike get_zone(), this does **not** reset pattern state, coherence, or
    any other per-zone gameplay state. It's intended for 'peek' rendering of
    adjacent zones while zooming out.
    """
    if coord not in game.levels:
        game.levels[coord] = game._make_zone(coord, up_pos=None)
    return game.levels[coord]


def use_stairs_down(game: "Game") -> None:
    """Use downward stairs to descend to a lower level."""
    lvl = game._level()
    player = game._player()
    tile = lvl.world.get_tile(*player.pos)
    if tile is None:
        return

    cx, cy, cz = game.zone_coord
    if tile.glyph == ">":
        target_coord = (cx, cy, cz + 1)
        up_pos = player.pos
        dest_level = get_zone(game, target_coord, up_pos=up_pos)

        # Move player
        del lvl.actors[game.player_id]
        dest_pos = dest_level.up_stairs or dest_level.world.entry
        player.pos = dest_pos
        dest_level.actors[game.player_id] = player
        game.zone_coord = target_coord
        game.log.add(f"You descend to depth {game.zone_coord[2]}.")
        game._update_fov(dest_level)

        # Snap the Lorenz storm to the new floor
        game._reset_lorenz_on_zone_change(player)


def use_stairs_up(game: "Game") -> None:
    """Use upward stairs to ascend to a higher level."""
    lvl = game._level()
    player = game._player()
    tile = lvl.world.get_tile(*player.pos)
    if tile is None:
        return

    cx, cy, cz = game.zone_coord
    # Surface: if no upstairs, request world map
    if cz == 0 and tile.glyph != "<":
        game.map_requested = True
        return

    if tile.glyph == "<" and cz > 0:
        target_coord = (cx, cy, cz - 1)
        dest_level = get_zone(game, target_coord, up_pos=None)
        del lvl.actors[game.player_id]
        dest_pos = dest_level.down_stairs or dest_level.world.entry
        player.pos = dest_pos
        dest_level.actors[game.player_id] = player
        game.zone_coord = target_coord
        game.log.add(f"You ascend to depth {game.zone_coord[2]}.")
        game._update_fov(dest_level)

        # Snap the Lorenz storm to the new floor
        game._reset_lorenz_on_zone_change(player)


def transition_edge(game: "Game", actor: "Actor", dx: int, dy: int) -> None:
    """Move the player across zone boundaries."""
    from edgecaster import events

    level = game._level()
    w, h = level.world.width, level.world.height
    x, y = actor.pos
    nx = x + dx
    ny = y + dy
    zx, zy, zz = game.zone_coord
    dzx = 1 if nx >= w else -1 if nx < 0 else 0
    dzy = 1 if ny >= h else -1 if ny < 0 else 0
    if dzx == 0 and dzy == 0:
        return

    dest_coord = (zx + dzx, zy + dzy, zz)
    dest_x = 0 if nx >= w else (w - 1 if nx < 0 else nx)
    dest_y = 0 if ny >= h else (h - 1 if ny < 0 else ny)
    dest_level = get_zone(game, dest_coord, up_pos=None)

    # Move actor
    del level.actors[game.player_id]
    actor.pos = (dest_x, dest_y)
    dest_level.actors[game.player_id] = actor
    game.zone_coord = dest_coord
    game.log.add(f"You travel to zone {dest_coord[0]},{dest_coord[1]} (depth {dest_coord[2]}).")
    game._update_fov(dest_level)

    # Hard-snap Lorenz storm when wrapping zones
    game._reset_lorenz_on_zone_change(actor)

    # Random overworld events (testing)
    if game.zone_coord[2] == 0 and game.rng.random() < 0.5:
        ev = events.pick_random_event(game)
        if ev is not None:
            game.set_urgent(
                ev.body,
                title=ev.title,
                choices=ev.choices,
                on_choice_effect=ev.effect,
            )


def fast_travel_to_zone(game: "Game", zx: int, zy: int) -> None:
    """Instantly move the player to the given overworld zone (depth 0)."""
    # Clamp to world bounds
    zx = max(0, min(game.cfg.world_map_screens - 1, zx))
    zy = max(0, min(game.cfg.world_map_screens - 1, zy))
    dest_coord = (zx, zy, 0)

    level = game._level()
    actor = level.actors.get(game.player_id)
    if actor is None:
        return

    dest_level = get_zone(game, dest_coord, up_pos=None)

    # Move actor between levels
    if game.player_id in level.actors:
        del level.actors[game.player_id]
    actor.pos = dest_level.world.entry
    dest_level.actors[game.player_id] = actor
    game.zone_coord = dest_coord
    dest_level.need_fov = True
    game._update_fov(dest_level)
    game._reset_lorenz_on_zone_change(actor)
    game.log.add(f"You fast-travel to zone {zx},{zy}.")

    # Debug: spawn inventory on arrival
    game.debug_spawn_inventory_near_player(count=1)
