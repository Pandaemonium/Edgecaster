"""
Ambient spawn maintenance for active zones.

Option 2 behavior:
- Keep nearby (active-window) zones populated over time.
- Use zone difficulty tiers to set a target hostile count.
- Top up slowly on a tick interval to avoid bursty spawns.

This keeps the world feeling alive while preserving the yoga model:
zones are cache windows, and active neighboring zones continue simulating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from edgecaster.systems import spawning as spawning_system
from edgecaster.systems import entity_ops as entity_ops_system

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState


def maintain_population(game: "Game", active_levels: Iterable["LevelState"], delta: int) -> None:
    """Top up hostile populations in active zones.

    Tunables come from ``game.cfg`` with conservative defaults:
    - ``ambient_spawn_enabled``
    - ``ambient_spawn_interval_ticks``
    - ``ambient_spawn_target_base``
    - ``ambient_spawn_target_per_tier``
    - ``ambient_spawn_target_max``
    - ``ambient_spawn_max_per_cycle``
    - ``ambient_spawn_player_safe_radius``
    """
    if delta <= 0:
        return

    cfg = game.cfg

    if not bool(getattr(cfg, "ambient_spawn_enabled", True)):
        return

    interval = max(1, int(getattr(cfg, "ambient_spawn_interval_ticks", 120) or 120))
    base_target = max(0, int(getattr(cfg, "ambient_spawn_target_base", 2) or 2))
    per_tier = float(getattr(cfg, "ambient_spawn_target_per_tier", 0.5) or 0.5)
    target_max = max(base_target, int(getattr(cfg, "ambient_spawn_target_max", 8) or 8))
    max_per_cycle = max(1, int(getattr(cfg, "ambient_spawn_max_per_cycle", 1) or 1))
    safe_radius = max(0, int(getattr(cfg, "ambient_spawn_player_safe_radius", 8) or 8))

    player_abs = None
    player_depth = None
    try:
        player_abs = game._get_player_abs()
        player_depth = int(game.zone_coord[2])
    except Exception:
        pass

    for level in active_levels:
        # Keep ambient top-up focused on the overworld-like roaming band.
        if not _is_spawn_eligible(level):
            continue

        # Per-level spawn clock (stored on LevelState to persist with zone cache).
        accum = float(getattr(level, "ambient_spawn_accum", 0.0))
        accum += float(delta)
        if accum < interval:
            level.ambient_spawn_accum = accum
            continue

        # Do at most one maintenance pass per call (prevents catch-up bursts).
        accum = accum % interval
        level.ambient_spawn_accum = accum

        tier = int(getattr(level, "danger_tier", 1) or 1)
        target = int(round(base_target + per_tier * max(0, tier - 1)))
        target = max(base_target, min(target, target_max))

        hostiles = _hostile_count(level)
        deficit = max(0, target - hostiles)
        if deficit <= 0:
            continue

        spawn_count = min(deficit, max_per_cycle)

        # Keep immediate player vicinity clear in the current player zone.
        avoid_near = None
        if (
            player_abs is not None
            and player_depth is not None
            and int(level.coord[2]) == player_depth
        ):
            zc, local = game.zone_local_from_abs(player_abs, depth=player_depth, clamp_to_world=False)
            if tuple(zc) == tuple(level.coord):
                avoid_near = (int(local[0]), int(local[1]))

        spawning_system.spawn_enemies(
            game,
            level,
            spawn_count,
            use_biome_spawning=True,
            include_neutral_factions=False,
            avoid_near=avoid_near,
            avoid_distance=safe_radius,
        )


def _is_spawn_eligible(level: "LevelState") -> bool:
    """Return True if this level should receive ambient hostile top-ups."""
    try:
        if int(level.coord[2]) != 0:
            return False
    except Exception:
        return False

    world = getattr(level, "world", None)
    if world is None:
        return False

    # Keep handcrafted/special arenas under explicit control.
    if bool(getattr(world, "is_lab", False)):
        return False
    if bool(getattr(world, "is_lair", False)):
        return False

    return True


def _hostile_count(level: "LevelState") -> int:
    n = 0
    for actor in entity_ops_system.iter_actors(level):
        if not getattr(actor, "alive", True):
            continue
        if getattr(actor, "faction", None) == "hostile":
            n += 1
    return n
