from typing import Tuple, Optional
import math

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


class FractalField:
    """Simple Mandelbrot-based field for height/biome sampling."""

    def __init__(self, scale: float = 0.012, offset: Tuple[float, float] = (-0.75, 0.0), iterations: int = 48):
        self.scale = scale
        self.offset = offset
        self.iterations = iterations

    def sample(self, wx: float, wy: float) -> float:
        """Return height in 0..1 from Mandelbrot escape time."""
        cx = wx * self.scale + self.offset[0]
        cy = wy * self.scale + self.offset[1]
        zx = 0.0
        zy = 0.0
        it = 0
        while zx * zx + zy * zy <= 4.0 and it < self.iterations:
            xt = zx * zx - zy * zy + cx
            zy = 2 * zx * zy + cy
            zx = xt
            it += 1
        if it >= self.iterations:
            return 0.0  # inside set -> treat as lowland/sea-level
        mod = math.sqrt(zx * zx + zy * zy)
        smooth = it + 1 - math.log(math.log(max(mod, 1e-6))) / math.log(2)
        return max(0.0, min(1.0, smooth / self.iterations))


def _hash01(x: int, y: int) -> float:
    h = (x * 73856093) ^ (y * 19349663)
    h &= 0xFFFFFFFF
    return (h % 1000) / 1000.0


def generate_fractal_overworld(
    world: World, field: FractalField, coord: Tuple[int, int, int], rng, up_pos: Optional[Tuple[int, int]] = None
) -> None:
    """Fractal-driven overworld: Mandelbrot height -> terrain."""
    zx, zy, _ = coord
    w, h = world.width, world.height
    cx0 = zx * w
    cy0 = zy * h

    for y in range(h):
        for x in range(w):
            wx = cx0 + x
            wy = cy0 + y
            height = field.sample(wx, wy)
            noise = _hash01(int(wx), int(wy))
            tile = world.tiles[y][x]
            if height < 0.24:
                tile.glyph = "~"
                tile.walkable = False
            elif height < 0.32:
                tile.glyph = ","
                tile.walkable = True
            elif height < 0.72:
                tile.glyph = "."
                tile.walkable = True
                if 0.5 < height < 0.7 and noise < 0.08:
                    tile.glyph = "T"
                    tile.walkable = False
            elif height < 0.88:
                tile.glyph = "^"
                tile.walkable = True
            else:
                tile.glyph = "#"
                tile.walkable = False

    # entry: use up_pos if provided, else nearest walkable to center
    if up_pos and world.in_bounds(*up_pos) and world.is_walkable(*up_pos):
        world.entry = up_pos
    else:
        cx, cy = w // 2, h // 2
        world.entry = (cx, cy)
        if not world.is_walkable(cx, cy):
            found = None
            radius = 1
            while radius < max(w, h) and not found:
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        tx, ty = cx + dx, cy + dy
                        if not world.in_bounds(tx, ty):
                            continue
                        if world.is_walkable(tx, ty):
                            found = (tx, ty)
                            break
                    if found:
                        break
                radius += 1
            if found:
                world.entry = found

    # down stairs near center on walkable tile
    for radius in range(0, max(w, h)):
        placed = False
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                tx = w // 2 + dx
                ty = h // 2 + dy
                if not world.in_bounds(tx, ty):
                    continue
                if not world.is_walkable(tx, ty):
                    continue
                tile = world.get_tile(tx, ty)
                if tile:
                    tile.glyph = ">"
                    world.down_stairs = (tx, ty)
                    placed = True
                    break
            if placed:
                break
        if placed:
            break


def generate_overworld(world: World, rng, up_pos: Optional[Tuple[int, int]] = None) -> None:
    """Open, walkable overworld slice with light scatter of obstacles."""
    # start as open grass
    for y in range(world.height):
        for x in range(world.width):
            tile = world.tiles[y][x]
            tile.walkable = True
            tile.glyph = "."
    # sprinkle obstacles
    for y in range(1, world.height - 1):
        for x in range(1, world.width - 1):
            if rng.random() < 0.05:
                tile = world.tiles[y][x]
                tile.walkable = False
                tile.glyph = "#"
    # entry
    if up_pos:
        world.entry = up_pos
    else:
        world.entry = (world.width // 2, world.height // 2)
    # put a single down stair somewhere central-ish
    sx = world.width // 2 + rng.randint(-3, 3)
    sy = world.height // 2 + rng.randint(-3, 3)
    sx = max(1, min(world.width - 2, sx))
    sy = max(1, min(world.height - 2, sy))
    tile = world.get_tile(sx, sy)
    if tile:
        tile.glyph = ">"
        tile.walkable = True
        world.down_stairs = (sx, sy)
