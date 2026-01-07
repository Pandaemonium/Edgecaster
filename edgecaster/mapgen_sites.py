from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


from edgecaster.state.world import World


Room = Tuple[int, int, int, int]  # x, y, w, h


@dataclass(frozen=True)
class LairLayoutResult:
    entry: Tuple[int, int]
    boss_pos: Tuple[int, int]
    rooms: List[Room]


def _fill(world: World, *, glyph: str, walkable: bool) -> None:
    for y in range(world.height):
        for x in range(world.width):
            tile = world.tiles[y][x]
            tile.glyph = glyph
            tile.walkable = walkable


def _carve_floor(world: World, x: int, y: int) -> None:
    tile = world.get_tile(x, y)
    if tile is None:
        return
    tile.glyph = "."
    tile.walkable = True


def _carve_room(world: World, room: Room) -> None:
    x, y, w, h = room
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if world.in_bounds(xx, yy):
                _carve_floor(world, xx, yy)


def _carve_h_tunnel(world: World, x1: int, x2: int, y: int) -> None:
    for xx in range(min(x1, x2), max(x1, x2) + 1):
        if world.in_bounds(xx, y):
            _carve_floor(world, xx, y)


def _carve_v_tunnel(world: World, y1: int, y2: int, x: int) -> None:
    for yy in range(min(y1, y2), max(y1, y2) + 1):
        if world.in_bounds(x, yy):
            _carve_floor(world, x, yy)


def _nearest_walkable(
    world: World,
    origin: Tuple[int, int],
    *,
    max_radius: int = 32,
) -> Optional[Tuple[int, int]]:
    ox, oy = origin
    if world.in_bounds(ox, oy) and world.is_walkable(ox, oy):
        return origin
    for r in range(1, max_radius + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                x = ox + dx
                y = oy + dy
                if not world.in_bounds(x, y):
                    continue
                if world.is_walkable(x, y):
                    return (x, y)
    return None


def _build_multi_room_lair(world: World, rng) -> LairLayoutResult:
    """Generate a simple multi-room lair layout.

    This is the first (and currently only) lair "building type". Keep the API
    flexible so we can add arena/maze/fortress variants later without touching
    zone-generation glue.
    """
    _fill(world, glyph="#", walkable=False)

    margin = 2
    max_rooms = 9
    min_size = 5
    max_size = 10

    rooms: List[Room] = []
    attempts = 0
    max_attempts = 200

    while len(rooms) < max_rooms and attempts < max_attempts:
        attempts += 1
        w = int(rng.randint(min_size, max_size))
        h = int(rng.randint(min_size, max_size))
        x = int(rng.randint(margin, max(margin, world.width - w - margin)))
        y = int(rng.randint(margin, max(margin, world.height - h - margin)))
        new_room = (x, y, w, h)

        # overlap check (with a 1-tile buffer)
        if any(
            (
                x < rx + rw + 1
                and x + w + 1 > rx
                and y < ry + rh + 1
                and y + h + 1 > ry
            )
            for rx, ry, rw, rh in rooms
        ):
            continue

        _carve_room(world, new_room)

        if rooms:
            # connect to a previous room center
            px, py, pw, ph = rooms[-1]
            prev_cx = px + pw // 2
            prev_cy = py + ph // 2
            cx = x + w // 2
            cy = y + h // 2
            if rng.random() < 0.5:
                _carve_h_tunnel(world, prev_cx, cx, prev_cy)
                _carve_v_tunnel(world, prev_cy, cy, cx)
            else:
                _carve_v_tunnel(world, prev_cy, cy, prev_cx)
                _carve_h_tunnel(world, prev_cx, cx, cy)

        rooms.append(new_room)

    # Entry: middle of the bottom edge (slightly inset so the outer border can remain walls).
    entry_guess = (world.width // 2, max(0, world.height - 2))
    _carve_floor(world, *entry_guess)

    # Carve a corridor upward toward the closest room center (or just upward a bit).
    if rooms:
        # pick the room whose center is closest to the entry
        best = None
        best_d2 = 1e18
        for rx, ry, rw, rh in rooms:
            cx = rx + rw // 2
            cy = ry + rh // 2
            dx = cx - entry_guess[0]
            dy = cy - entry_guess[1]
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best = (cx, cy)
        if best is not None:
            _carve_v_tunnel(world, entry_guess[1], best[1], entry_guess[0])
            _carve_h_tunnel(world, entry_guess[0], best[0], best[1])
    else:
        _carve_v_tunnel(world, entry_guess[1], max(0, entry_guess[1] - 6), entry_guess[0])

    entry = _nearest_walkable(world, entry_guess) or entry_guess

    # Boss spawn: prefer a room farthest from the entry to encourage exploration.
    boss_guess = (world.width // 2, world.height // 2)
    if rooms:
        farthest = None
        farthest_d2 = -1
        ex, ey = entry
        for rx, ry, rw, rh in rooms:
            cx = rx + rw // 2
            cy = ry + rh // 2
            dx = cx - ex
            dy = cy - ey
            d2 = dx * dx + dy * dy
            if d2 > farthest_d2:
                farthest_d2 = d2
                farthest = (cx, cy)
        if farthest is not None:
            boss_guess = farthest

    boss_pos = _nearest_walkable(world, boss_guess) or boss_guess

    return LairLayoutResult(entry=entry, boss_pos=boss_pos, rooms=rooms)


LAIR_LAYOUT_BUILDERS: Dict[str, Callable[[World, object], LairLayoutResult]] = {
    # Multi-room building (current default).
    "multi_room": _build_multi_room_lair,
}


def generate_legendary_lair(world: World, rng, *, layout: str = "multi_room") -> LairLayoutResult:
    """Generate a legendary lair zone layout.

    This is a "site" generator: it can fully replace the underlying overworld
    terrain for that zone. Keep this logic in mapgen (not scenes/renderers) so
    it stays deterministic and testable.
    """
    layout = str(layout or "multi_room")
    builder = LAIR_LAYOUT_BUILDERS.get(layout)
    if builder is None:
        builder = LAIR_LAYOUT_BUILDERS["multi_room"]

    result = builder(world, rng)

    # World integration fields:
    world.entry = result.entry
    world.up_stairs = None
    world.down_stairs = None
    world.is_lair = True  # type: ignore[attr-defined]
    world.lair_info = {  # type: ignore[attr-defined]
        "layout": layout,
        "entry": result.entry,
        "boss_pos": result.boss_pos,
        "rooms": result.rooms,
    }

    return result

