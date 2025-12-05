from typing import Tuple, Optional
import math
import random
from typing import Tuple, Optional, Dict, List

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

    # collect all walkable tiles for random placement
    floor_tiles = [(xx, yy) for yy in range(world.height) for xx in range(world.width) if world.get_tile(xx, yy).walkable]

    if floor_tiles:
        world.entry = rng.choice(floor_tiles)
    else:
        world.entry = (world.width // 2, world.height // 2)

    # stairs placement: pick random floor tiles so they aren't always centered
    if floor_tiles:
        # up stairs (if coming from above)
        if up_pos is not None:
            up_c = rng.choice(floor_tiles)
            tile = world.get_tile(*up_c)
            if tile:
                tile.glyph = "<"
                tile.walkable = True
                world.up_stairs = up_c
                world.entry = up_c
        # down stairs
        down_c = rng.choice(floor_tiles)
        tile = world.get_tile(*down_c)
        if tile:
            tile.glyph = ">"
            tile.walkable = True
            world.down_stairs = down_c


class FractalField:
    """Simple Mandelbrot-based field for height/biome sampling."""

    def __init__(
        self,
        scale: float = 0.012,
        offset: Tuple[float, float] = (-0.75, 0.0),
        iterations: int = 48,
        seed: Optional[int] = None,
    ):
        self.scale = scale
        self.offset = offset
        self.iterations = iterations
        self.seed = seed
        self._rng = random.Random(seed)
        # jitter the offset/scale slightly per seed to diversify runs
        self.offset = (
            self.offset[0] + self._rng.uniform(-0.2, 0.2),
            self.offset[1] + self._rng.uniform(-0.2, 0.2),
        )
        self.scale = self.scale * (0.9 + self._rng.random() * 0.2)

        # secondary constants for moisture/pattern sampling
        self.moisture_c = complex(
            self._rng.uniform(-0.6, 0.6),
            self._rng.uniform(-0.6, 0.6),
        )
        self.pattern_c = complex(
            self._rng.uniform(-0.4, 0.4),
            self._rng.uniform(-0.4, 0.4),
        )

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

    def _julia(self, wx: float, wy: float, c: complex, iters: int = 32) -> float:
        """Basic Julia escape fraction 0..1."""
        zx = wx * self.scale
        zy = wy * self.scale
        it = 0
        while zx * zx + zy * zy <= 4.0 and it < iters:
            xt = zx * zx - zy * zy + c.real
            zy = 2 * zx * zy + c.imag
            zx = xt
            it += 1
        if it >= iters:
            return 0.0
        mod = math.sqrt(zx * zx + zy * zy)
        smooth = it + 1 - math.log(math.log(max(mod, 1e-6))) / math.log(2)
        return max(0.0, min(1.0, smooth / iters))

    def sample_full(self, wx: float, wy: float) -> dict:
        """Return a dict of fields for a world coordinate."""
        height = self.sample(wx, wy)
        moisture = self._julia(wx, wy, self.moisture_c, iters=28)
        pattern = self._julia(wx, wy, self.pattern_c, iters=36)
        corruption = _hash01(int(wx * 13), int(wy * 17))  # simple static mask for now
        return {
            "height": height,
            "moisture": moisture,
            "pattern": pattern,
            "corruption": corruption,
        }


def _hash01(x: int, y: int) -> float:
    h = (x * 73856093) ^ (y * 19349663)
    h &= 0xFFFFFFFF
    return (h % 1000) / 1000.0

def _color_from_fields(fields: dict) -> tuple[int, int, int]:
    """Smooth gradient inspired by the overmap palette (water→plains→forest→hills→mountains)."""
    h = max(0.0, min(1.0, fields["height"]))
    anchors = [
        (0.00, (50, 90, 170)),   # deep water
        (0.15, (110, 160, 190)), # shore/shallows
        (0.32, (150, 200, 140)), # plains
        (0.52, (90, 170, 110)),  # forested low hills
        (0.72, (170, 150, 110)), # hills
        (1.00, (210, 210, 215)), # high/mountains
    ]
    for i in range(len(anchors) - 1):
        h0, c0 = anchors[i]
        h1, c1 = anchors[i + 1]
        if h <= h1:
            t = 0.0 if h1 == h0 else (h - h0) / (h1 - h0)
            return tuple(int(c0[j] + t * (c1[j] - c0[j])) for j in range(3))
    return anchors[-1][1]

def _julia_height_norm(nx: float, ny: float, c: complex, scale: float = 1.0, iters: int = 96) -> float:
    """Julia escape-time normalized height (0..1)."""
    zx = nx * scale
    zy = ny * scale
    it = 0
    while zx * zx + zy * zy <= 4.0 and it < iters:
        xt = zx * zx - zy * zy + c.real
        zy = 2 * zx * zy + c.imag
        zx = xt
        it += 1
    if it >= iters:
        return 0.0
    mod = math.sqrt(zx * zx + zy * zy)
    smooth = it + 1 - math.log(math.log(max(mod, 1e-6))) / math.log(2)
    return max(0.0, min(1.0, smooth / iters))


def _classify_tile(fields: dict, noise: float) -> Tuple[str, bool]:
    """Return glyph, walkable based on height/moisture and a dash of noise."""
    h = fields["height"]
    m = fields["moisture"]
    # Broad strokes: low = water, mid = shore/plains/forest, high = hills/mountains.
    if h < 0.16:
        return "~", False  # deep water
    if h < 0.24:
        return ",", True  # shallow/shore
    if h < 0.68:
        # lowlands
        if m > 0.64:
            # lush/forested; mostly walkable, with sparse blockers
            return ("T", noise > 0.015)
        if m < 0.28:
            # drier scrub/steppe
            return ("." if noise > 0.05 else ",", True)
        return ".", True
    if h < 0.82:
        # hills, lightly obstructive
        return ("^", noise > 0.05)
    # high mountains: still mostly passable to keep openness
    return ("#", noise > 0.35)


def generate_fractal_overworld(
    world: World,
    field: FractalField,
    coord: Tuple[int, int, int],
    rng,
    up_pos: Optional[Tuple[int, int]] = None,
    biome: Optional[str] = None,
    zoom_mult: float = 1.0,
    overmap_params: Optional[dict] = None,
    jx_slice: Optional[List[float]] = None,
    jy_slice: Optional[List[float]] = None,
) -> None:
    """Fractal-driven overworld; tint directly from per-tile Julia coords."""
    if overmap_params is None:
        raise RuntimeError("Overmap params missing; cannot generate local zone without exact correspondence.")
    zx, zy, _ = coord
    w, h = world.width, world.height
    cx0 = zx * w
    cy0 = zy * h
    surf = overmap_params.get("surface")
    min_wx = overmap_params.get("min_wx")
    min_wy = overmap_params.get("min_wy")
    span_x = overmap_params.get("span_x")
    span_y = overmap_params.get("span_y")
    surf_w = surf_h = None
    if surf is not None:
        surf_w, surf_h = surf.get_size()

    for y in range(h):
        for x in range(w):
            wx = cx0 + x
            wy = cy0 + y
            if jx_slice is not None and jy_slice is not None:
                jx = jx_slice[x]
                jy = jy_slice[y]
                h_val = _julia_height_norm(jx, jy, overmap_params["visual_c"], scale=1.0, iters=96)
                fields = {
                    "height": h_val,
                    "moisture": h_val,
                    "pattern": 0.0,
                    "corruption": 0.0,
                }
                glyph, _ = _classify_tile(fields, 0.5)
                tint = _color_from_fields(fields)
            else:
                if surf is None or surf_w is None or surf_h is None or min_wx is None or span_x is None:
                    raise RuntimeError("Overmap surface missing for tint sampling.")
                px = int((wx - min_wx) / span_x * surf_w)
                py = int((wy - min_wy) / span_y * surf_h)
                px = max(0, min(surf_w - 1, px))
                py = max(0, min(surf_h - 1, py))
                
                fields = field.sample_full(wx, wy)
                tint = _color_from_fields(fields)
                glyph, _ = _classify_tile(fields, 0.5)
            tile = world.tiles[y][x]
            tile.glyph = glyph if glyph != "~" else "."
            tile.walkable = True
            tile.tint = tint

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
