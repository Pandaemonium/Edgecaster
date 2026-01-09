"""
Lighting System - Light emission from entities.

This module manages:
- Collecting light sources from entities in a level
- Calculating per-tile illumination
- Integrating with the FOV system to make lit tiles visible

Light sources are entities with a 'light_radius' tag > 0.
Optional tags: 'light_intensity' (default 1.0), 'light_color' (RGB tuple).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

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


def _los_check(world: "World", a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    """Check line-of-sight between two points (simplified Bresenham)."""
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
        if (x, y) == (x1, y1):
            return True
        if not tile.walkable:
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

        sources.append(LightSource(
            pos=pos,
            radius=radius,
            intensity=intensity,
            color=color,
        ))

    return sources


def calculate_illumination(level: "LevelState", sources: List[LightSource]) -> None:
    """Calculate and store illumination values for all tiles.

    For each light source, illuminates tiles within its radius
    with falloff based on distance. Light requires line-of-sight.
    """
    world = level.world
    world.clear_illumination()

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

                # Check LOS from light source to tile
                if not _los_check(world, (lx, ly), (tx, ty)):
                    continue

                tile = world.get_tile(tx, ty)
                if tile is None:
                    continue

                # Calculate illumination with distance falloff
                dist = (dist2 ** 0.5) if dist2 > 0 else 0
                falloff = 1.0 - (dist / (r + 1))
                illumination = src.intensity * falloff

                # Accumulate light from multiple sources
                tile.illumination = max(tile.illumination, illumination)


def mark_lit_tiles_visible(
    game: "Game",
    level: "LevelState",
    player_pos: Tuple[int, int],
    illumination_threshold: float = 0.1,
) -> None:
    """Mark sufficiently lit tiles as visible if player has LOS to them.

    This allows the player to see distant light sources (like a dropped
    Glowing Band) even if they're outside the normal FOV radius, as long
    as there's no wall blocking the view.

    Args:
        game: The game instance (for accessing _los helper if needed)
        level: The current level
        player_pos: Player's position for LOS checks
        illumination_threshold: Minimum illumination to consider a tile "lit"
    """
    world = level.world
    px, py = player_pos

    # Check all tiles for illumination
    for y in range(world.height):
        for x in range(world.width):
            tile = world.get_tile(x, y)
            if tile is None:
                continue

            # Skip if already visible (from normal FOV)
            if tile.visible:
                continue

            # Skip if not illuminated enough
            if tile.illumination < illumination_threshold:
                continue

            # Check if player has LOS to this lit tile
            if _los_check(world, (px, py), (x, y)):
                tile.visible = True
                tile.explored = True


def update_level_lighting(
    game: "Game",
    level: "LevelState",
    player_pos: Tuple[int, int],
) -> None:
    """Full lighting update: collect sources, calculate illumination, mark visible.

    Call this from _update_fov() after clearing visibility but before
    or after the normal FOV calculation.
    """
    sources = collect_light_sources(level)
    if not sources:
        return

    calculate_illumination(level, sources)
    mark_lit_tiles_visible(game, level, player_pos)
