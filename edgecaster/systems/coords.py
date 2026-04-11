"""Canonical ABS ↔ zone/local coordinate transforms.

The world uses two layered coordinate systems:

- **ABS (absolute) space**: a single flat integer grid spanning the entire
  world map.  `Entity.abs_pos` is the source of truth.  All cross-zone
  reasoning (FOV, pathfinding, ABS-space POI queries) works in this space.

- **Zone/local space**: the world is divided into rectangular *zones*
  (`cfg.world_width` × `cfg.world_height` tiles each).  A zone is identified
  by `(zx, zy, depth)` and a local position `(lx, ly)` within it.
  `Entity.pos` is a zone-local cache loaded when a zone is active.

These two functions are the only approved converters between those spaces.
Do not inline the division/modulo arithmetic elsewhere; use these helpers so
edge cases (negative coordinates, world-edge clamping) are handled once.

Invariant: `abs_from_zone_local(game, *zone_local_from_abs(game, p))` should
round-trip back to `p` for any in-bounds ABS position `p`.
"""
from __future__ import annotations


def zone_dims(game) -> tuple[int, int]:
    """Return canonical zone width/height in tiles."""
    return int(game.cfg.world_width), int(game.cfg.world_height)


def floor_divmod(a: int, b: int) -> tuple[int, int]:
    """Div/mod with non-negative remainder for negative inputs."""
    q = a // b
    r = a - q * b
    if r < 0:
        q -= 1
        r += b
    return q, r


def abs_from_zone_local(
    game,
    zone_coord: tuple[int, int, int],
    local_pos: tuple[int, int],
) -> tuple[int, int]:
    """Convert zone-local tile coordinates into absolute tile coordinates."""
    zx, zy, _zz = zone_coord
    lx, ly = int(local_pos[0]), int(local_pos[1])
    zw, zh = zone_dims(game)
    return int(zx) * zw + lx, int(zy) * zh + ly


def zone_local_from_abs(
    game,
    abs_pos: tuple[int, int],
    *,
    depth: int | None = None,
    clamp_to_world: bool = True,
) -> tuple[tuple[int, int, int], tuple[int, int]]:
    """Convert absolute tile coordinates into (zone_coord, local_pos)."""
    ax, ay = int(abs_pos[0]), int(abs_pos[1])
    zw, zh = zone_dims(game)

    zx, lx = floor_divmod(ax, zw)
    zy, ly = floor_divmod(ay, zh)

    if clamp_to_world:
        max_screen = max(0, int(game.cfg.world_map_screens) - 1)
        zx = max(0, min(max_screen, zx))
        zy = max(0, min(max_screen, zy))
        lx = max(0, min(zw - 1, lx))
        ly = max(0, min(zh - 1, ly))

    zz = int(depth if depth is not None else game.zone_coord[2])
    return (int(zx), int(zy), int(zz)), (int(lx), int(ly))
