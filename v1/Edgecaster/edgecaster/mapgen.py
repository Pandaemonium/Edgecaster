from typing import Tuple, Optional
from edgecaster.state.world import World

Room = Tuple[int, int, int, int]  # x, y, w, h


def carve_room(world: World, room: Room) -> None:
    x, y, w, h = room
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if world.in_bounds(xx, yy):
                tile = world.get_tile(xx, yy)
                if tile:
                    tile.walkable = True
                    tile.glyph = "."


def carve_h_tunnel(world: World, x1: int, x2: int, y: int) -> None:
    for xx in range(min(x1, x2), max(x1, x2) + 1):
        tile = world.get_tile(xx, y)
        if tile:
            tile.walkable = True
            tile.glyph = "."


def carve_v_tunnel(world: World, y1: int, y2: int, x: int) -> None:
    for yy in range(min(y1, y2), max(y1, y2) + 1):
        tile = world.get_tile(x, yy)
        if tile:
            tile.walkable = True
            tile.glyph = "."


def generate_basic(world: World, rng, up_pos: Optional[Tuple[int, int]] = None) -> None:
    """Simple rectangular rooms connected by halls, with optional stairs."""
    # start filled with walls
    for y in range(world.height):
        for x in range(world.width):
            tile = world.tiles[y][x]
            tile.walkable = False
            tile.glyph = "#"

    rooms = []
    max_rooms = 6
    min_size = 4
    max_size = 8

    for _ in range(max_rooms):
        w = rng.randint(min_size, max_size)
        h = rng.randint(min_size, max_size)
        x = rng.randint(1, world.width - w - 1)
        y = rng.randint(1, world.height - h - 1)
        new_room = (x, y, w, h)

        # overlap check
        if any((x < rx + rw and x + w > rx and y < ry + rh and y + h > ry) for rx, ry, rw, rh in rooms):
            continue

        carve_room(world, new_room)
        if rooms:
            # connect to previous room center
            px, py, pw, ph = rooms[-1]
            prev_cx = px + pw // 2
            prev_cy = py + ph // 2
            cx = x + w // 2
            cy = y + h // 2
            if rng.random() < 0.5:
                carve_h_tunnel(world, prev_cx, cx, prev_cy)
                carve_v_tunnel(world, prev_cy, cy, cx)
            else:
                carve_v_tunnel(world, prev_cy, cy, prev_cx)
                carve_h_tunnel(world, prev_cx, cx, cy)
        rooms.append(new_room)

    if rooms:
        x, y, w, h = rooms[0]
        entry = (x + w // 2, y + h // 2)
        world.entry = entry
    else:
        world.entry = (world.width // 2, world.height // 2)

    # stairs placement: always inside carved rooms so player is not boxed in
    if rooms:
        # up stairs (if coming from above) go in the first room center
        ux, uy, uw, uh = rooms[0]
        up_c = (ux + uw // 2, uy + uh // 2)
        if up_pos is not None:
            tile = world.get_tile(*up_c)
            if tile:
                tile.glyph = "<"
                tile.walkable = True
                world.up_stairs = up_c
                world.entry = up_c

        # down stairs in the last room center
        lx, ly, lw, lh = rooms[-1]
        down_c = (lx + lw // 2, ly + lh // 2)
        tile = world.get_tile(*down_c)
        if tile:
            tile.glyph = ">"
            tile.walkable = True
            world.down_stairs = down_c
