"""
Lighting System - Light emission from entities.

This module manages:
- Collecting light sources from entities in a level
- Calculating per-tile illumination
- Optionally revealing distant lit tiles, respecting blocks_vision
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple, Set

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState
    from edgecaster.state.world import World


@dataclass
class LightSource:
    """A light-emitting entity."""
    pos: Tuple[int, int]
    radius: int
    intensity: float = 1.0
    color: Optional[Tuple[int, int, int]] = None


def _los_check(
    world: "World",
    a: Tuple[int, int],
    b: Tuple[int, int],
    *,
    opaque: Optional[Set[Tuple[int, int]]] = None,
) -> bool:
    """
    Check line-of-sight between two points (simplified Bresenham).

    Blocks LOS on:
      - any position in `opaque` (entities with blocks_vision=True)
      - terrain tiles with tile.blocks_vision == True (if present)

    Always allows seeing the target square itself.
    """
    x0, y0 = a
    x1, y1 = b
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0

    while True:
        if not world.in_bounds(x, y):
            return False

        tile = world.get_tile(x, y)
        if tile is None:
            return False

        # Allow seeing the target tile itself
        if (x, y) == (x1, y1):
            return True

        # Entity-based occlusion (walls, closed doors, etc.)
        if opaque is not None and (x, y) in opaque:
            return False

        # Terrain-based occlusion (if ever used)
        if getattr(tile, "blocks_vision", False):
            return False

        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def collect_light_sources(level: "LevelState") -> List[LightSource]:
    """Gather all light-emitting entities in the level.

    Light sources are entities with tags.light_radius > 0.
    """
    sources: List[LightSource] = []

    entities = getattr(level, "entities", {}) or {}
    for ent in entities.values():
        pos = getattr(ent, "pos", None)
        if pos is None:
            continue

        tags = getattr(ent, "tags", {}) or {}
        radius = tags.get("light_radius", 0)
        if not radius or radius <= 0:
            continue

        try:
            radius = int(radius)
        except (TypeError, ValueError):
            continue

        intensity = float(tags.get("light_intensity", 1.0))
        color = tags.get("light_color")
        if color and not isinstance(color, tuple):
            try:
                color = tuple(color)
            except Exception:
                color = None

        sources.append(
            LightSource(
                pos=pos,
                radius=radius,
                intensity=intensity,
                color=color,
            )
        )

    return sources


def _build_opaque_set(level: "LevelState") -> Set[Tuple[int, int]]:
    """Build a set of positions that block vision (entities with blocks_vision)."""
    opaque: Set[Tuple[int, int]] = set()
    entities = getattr(level, "entities", {}) or {}
    for ent in entities.values():
        if getattr(ent, "blocks_vision", False):
            pos = getattr(ent, "pos", None)
            if pos is not None:
                opaque.add(pos)
    return opaque


def calculate_illumination(level: "LevelState", sources: List[LightSource]) -> None:
    """Calculate and store illumination values for all tiles.

    For each light source, illuminates tiles within its radius
    with falloff based on distance. Light requires LOS respecting blocks_vision.
    """
    world = level.world
    world.clear_illumination()

    if not sources:
        return

    opaque = _build_opaque_set(level)

    for src in sources:
        lx, ly = src.pos
        r = src.radius
        r2 = r * r

        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                tx, ty = lx + dx, ly + dy
                if not world.in_bounds(tx, ty):
                    continue

                dist2 = dx * dx + dy * dy
                if dist2 > r2:
                    continue

                if not _los_check(world, (lx, ly), (tx, ty), opaque=opaque):
                    continue

                tile = world.get_tile(tx, ty)
                if tile is None:
                    continue

                # Distance falloff
                dist = (dist2 ** 0.5) if dist2 > 0 else 0.0
                falloff = 1.0 - (dist / (r + 1))
                illumination = src.intensity * falloff

                tile.illumination = max(tile.illumination, illumination)


def mark_lit_tiles_visible(
    game: "Game",
    level: "LevelState",
    player_pos: Tuple[int, int],
    illumination_threshold: float = 0.1,
) -> None:
    """Mark sufficiently lit tiles as visible if player has LOS to them.

    This allows seeing distant light sources (e.g. glowing items)
    *without* violating walls/doors that block vision.
    """
    world = level.world
    px, py = player_pos

    opaque = _build_opaque_set(level)

    for y in range(world.height):
        for x in range(world.width):
            tile = world.get_tile(x, y)
            if tile is None:
                continue

            # Already visible from normal FOV
            if tile.visible:
                continue

            # Not bright enough
            if tile.illumination < illumination_threshold:
                continue

            # Respect entity-based occlusion
            if _los_check(world, (px, py), (x, y), opaque=opaque):
                tile.visible = True
                tile.explored = True


def update_level_lighting(
    game: "Game",
    level: "LevelState",
    player_pos: Tuple[int, int],
) -> None:
    """Full lighting update.

    Intended to be called from Game._update_fov().
    """
    sources = collect_light_sources(level)
    if not sources:
        return

    calculate_illumination(level, sources)
    mark_lit_tiles_visible(game, level, player_pos)
