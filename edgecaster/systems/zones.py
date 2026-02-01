"""
Zone management system.

PHASE 1 (Zone Metaphysics Removal)
---------------------------------
Zones are being demoted to *boring chunks*:
- They are a cache / storage container keyed by (zx, zy, depth).
- Crossing a chunk boundary must not, by itself, trigger gameplay rituals
  (pattern resets, coherence resets, spawns, discovery popups, etc.).

This module therefore provides:
- get_zone(): pure lazy creation + retrieval (no side effects beyond creation)
- get_zone_for_render(): strict render-peek (never creates)
- stair helpers (explicit transitions are allowed)
- transition_edge(): *mechanical* wrap to adjacent chunk only (no rituals)

Later phases will replace most "zone enter" semantics with:
- Attention/hotness based ticking
- Macro-entity refinement/coarsening
- Proximity-based discovery
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
    Get (and lazily create) a chunk/zone at the given coordinate.

    IMPORTANT (Phase 1):
    - This must not perform "zone-entry rituals".
    - No pattern/coherence resets.
    - No discovery, no spawns, no late realization.
    - Those belong to attention/macro systems later.

    Side effects allowed here:
    - Creating the LevelState if it doesn't exist yet.
    """
    if coord not in game.levels:
        game.levels[coord] = game._make_zone(coord, up_pos=up_pos)
    return game.levels[coord]


def get_zone_for_render(game: "Game", coord: Tuple[int, int, int]) -> Optional["LevelState"]:
    """
    Render-peek only.

    INVARIANT:
    - NEVER instantiate gameplay state.
    - MUST NOT call game._make_zone().
    - MUST NOT trigger any gameplay side effects.

    Returns already-loaded LevelState if present, else None.
    """
    lvl = game.levels.get(coord)
    if lvl is not None:
        return lvl

    # Escape hatch intentionally kept but *disabled by default*.
    # Treat as radioactive: only enable for experiments.
    if bool(getattr(game, "allow_render_zone_creation", False)):
        try:
            game.levels[coord] = game._make_zone(coord, up_pos=None)
            return game.levels[coord]
        except Exception:
            return None

    return None


def use_stairs_down(game: "Game") -> None:
    """Use downward stairs to descend to a lower level (explicit transition)."""
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

        # Move player between LevelStates
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
    """Use upward stairs to ascend to a higher level (explicit transition)."""
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
    """
    Mechanical movement across chunk boundaries.

    IMPORTANT (Phase 1):
    - NO random overworld events here.
    - NO pattern resets / coherence resets / discovery.
    - This is purely a coordinate + cache transition.

    NOTE:
    We still keep a single "current" LevelState for now (legacy), but we make
    the boundary crossing invisible and side-effect free.
    """
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

    # Move actor between levels
    if game.player_id in level.actors:
        del level.actors[game.player_id]
    actor.pos = (dest_x, dest_y)
    dest_level.actors[game.player_id] = actor
    game.zone_coord = dest_coord

    # Keep continuity: update FOV and keep Lorenz storm coherent.
    dest_level.need_fov = True
    game._update_fov(dest_level)
    game._reset_lorenz_on_zone_change(actor)


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
