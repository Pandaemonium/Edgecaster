from typing import Tuple, Optional
import math
import random
from typing import Tuple, Optional, Dict, List
from edgecaster.content import pois
from edgecaster.corruption import CorruptionParams, julia_height_norm_corrupted

# Import climate system for biome-aware generation
from edgecaster.climate import (
    ClimateConfig,
    Biome,
    BIOME_COLORS as CLIMATE_BIOME_COLORS,
    BIOME_GLYPHS as CLIMATE_BIOME_GLYPHS,
    elev_ridge_belt,
    ocean_lake_masks_from_water,
    manhattan_distance_to,
    compute_temperature,
    compute_wind_field,
    compute_moisture,
    classify_biome as climate_classify_biome,
    biome_to_glyph,
)

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


def generate_lab(world: World, rng) -> None:
    """Small lab layout: single room with a central console '='."""
    # fill with walls
    for y in range(world.height):
        for x in range(world.width):
            tile = world.tiles[y][x]
            tile.walkable = False
            tile.glyph = "#"

    room_margin = 2
    x0 = room_margin
    y0 = room_margin
    x1 = world.width - room_margin - 1
    y1 = world.height - room_margin - 1
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            tile = world.get_tile(x, y)
            if tile:
                tile.walkable = True
                tile.glyph = "."

    # console at center
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    tile = world.get_tile(cx, cy)
    if tile:
        tile.glyph = "="
        tile.walkable = True

    # entry: any floor tile
    floor_tiles = [(x, y) for y in range(world.height) for x in range(world.width) if world.get_tile(x, y).walkable]
    if floor_tiles:
        world.entry = rng.choice(floor_tiles)
    else:
        world.entry = (world.width // 2, world.height // 2)
    world.up_stairs = None
    world.down_stairs = None
    world.is_lab = True

def apply_pois(
    world: World,
    coord: Tuple[int, int, int],
    poi_registry: Optional["POIRegistry"] = None,  # type: ignore[name-defined]
) -> List[str]:
    """Return list of POI ids that apply to this coord.

    If poi_registry is provided, uses spatial queries to find POIs that
    overlap this zone (supporting multi-zone POIs). Otherwise falls back
    to legacy exact coord matching.
    """
    zx, zy, depth = coord
    hits = []

    if poi_registry is not None:
        # V2: Use registry spatial query (supports multi-zone POIs)
        for poi_spec in poi_registry.get_at_zone(zx, zy, depth):
            hits.append(poi_spec.id)
    else:
        # Legacy: Exact coord match
        for pid, poi in pois.POIS.items():
            if tuple(poi.coord) == tuple(coord):
                hits.append(pid)

    world.poi_ids = hits  # type: ignore[attr-defined]
    return hits


def build_item_depot(world: World, rng, entry: Tuple[int, int]) -> dict:
    """
    Carve an item depot to the left of entry. Returns metadata for placement.

    IMPORTANT: This no longer stamps terrain walls. It returns wall_positions so
    the Game can spawn wall entities (and a door entity) at realization time.
    """
    w = rng.randint(8, 12)
    h = rng.randint(8, 12)
    ex, ey = entry
    x = max(1, ex - w - 4)
    y = max(1, min(world.height - h - 2, ey - h // 2))

    # Door on east wall
    door_y = y + rng.randint(1, max(1, h - 2))
    door_x = x + w - 1

    interior = []
    wall_positions = []

    for yy in range(y, y + h):
        for xx in range(x, x + w):
            tile = world.get_tile(xx, yy)
            if tile is None:
                continue

            is_border = (xx in (x, x + w - 1) or yy in (y, y + h - 1))
            is_door = (xx == door_x and yy == door_y)

            if is_border and not is_door:
                # Leave terrain alone visually; just record where to spawn wall entities.
                wall_positions.append((xx, yy))

                # Ensure the underlying terrain isn't acting like a wall anymore.
                # (Let the wall entity handle blocking + visibility.)
                tile.walkable = True
                # Keep the tile glyph as-is if you prefer; using '.' makes testing obvious.
                tile.glyph = "."
            else:
                tile.walkable = True
                tile.glyph = "."
                interior.append((xx, yy))

    # Door tile should be walkable at the terrain level; the door ENTITY blocks movement.
    if world.in_bounds(door_x, door_y):
        dtile = world.get_tile(door_x, door_y)
        if dtile:
            dtile.walkable = True
            dtile.glyph = "."

    # sign just outside door (east side)
    sign_pos = None
    if world.in_bounds(door_x + 1, door_y):
        sign_pos = (door_x + 1, door_y)
        tile = world.get_tile(*sign_pos)
        if tile:
            tile.walkable = True
            tile.glyph = "."

    return {
        "rect": (x, y, w, h),
        "door": (door_x, door_y),
        "sign": sign_pos,
        "interior": interior,
        "wall_positions": wall_positions,
    }



def generate_basic(world: World, rng, up_pos: Optional[Tuple[int, int]] = None, coord: Tuple[int, int, int] = (0, 0, 0)) -> None:
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


def _color_from_biome(biome_id: int) -> tuple[int, int, int]:
    """Get RGB color from a climate biome ID."""
    try:
        b = Biome(biome_id)
        return CLIMATE_BIOME_COLORS.get(b, (128, 128, 128))
    except (ValueError, KeyError):
        return (128, 128, 128)


def _compute_zone_climate_fields(
    *,
    jx_slice: List[float],
    jy_slice: List[float],
    h_grid: "object",  # numpy array
    env_grid: "object",  # numpy array
    j_min_y: float,
    j_max_y: float,
    climate_config: Optional[ClimateConfig] = None,
) -> tuple["object", "object", "object", "object", "object"]:
    """
    Compute climate fields for a local zone.

    Returns: (biome_grid, E_clim, T, M, ocean_mask) as numpy arrays.
    """
    import numpy as np

    if climate_config is None:
        climate_config = ClimateConfig()
    cfg = climate_config

    h, w = h_grid.shape

    # Convert normalized height (t) to climate elevation
    t = h_grid.astype(np.float32)
    E_clim = elev_ridge_belt(t, land_boost=cfg.land_boost)

    # Water classification
    water = (E_clim < cfg.sea_level)
    ocean_mask, lake_mask = ocean_lake_masks_from_water(water, t=t)

    # Distance fields
    ocean_dist = manhattan_distance_to(ocean_mask)
    lake_dist = manhattan_distance_to(lake_mask)

    # Latitude from Julia y-coordinates
    jy_arr = np.array(jy_slice, dtype=np.float32)
    y_mid = 0.5 * (j_min_y + j_max_y)
    y_half = 0.5 * (j_max_y - j_min_y) + 1e-6
    lat_1d = np.clip((jy_arr - y_mid) / y_half, -1.0, 1.0)
    lat = np.repeat(lat_1d[:, None], w, axis=1).astype(np.float32)

    # Climate fields
    T = compute_temperature(E_clim, ocean_dist, lat, cfg)
    U, V = compute_wind_field(E_clim, lat, T, cfg)
    M = compute_moisture(E_clim, ocean_dist, lake_dist, U, V, T, cfg)

    # Biome classification
    corruption_env = env_grid.astype(np.float32) if env_grid is not None and np.any(env_grid > 0) else None
    biome_grid = climate_classify_biome(E_clim, T, M, ocean_mask, lake_mask, corruption_env=corruption_env)

    return biome_grid, E_clim, T, M, ocean_mask


def _apply_corruption_tint(color: tuple[int, int, int], strength: float) -> tuple[int, int, int]:
    """Blend base tint towards a demonic purple based on corruption strength.

    strength is allowed to be an unbounded "corruption intensity" signal.
    We compress it into 0..1 so strong cones don't turn the overmap neon pink.
    """
    s = max(0.0, float(strength))
    if s <= 0.0:
        return color

    # Convert unbounded signal to 0..1 with diminishing returns.
    strength01 = 1.0 - math.exp(-0.55 * s)
    strength01 = max(0.0, min(1.0, strength01))

    # Darker purple target (less magenta/pink).
    target = (70, 30, 90)
    r, g, b = color
    tr, tg, tb = target
    t = 0.35 * strength01
    return (
        int(r + (tr - r) * t),
        int(g + (tg - g) * t),
        int(b + (tb - b) * t),
    )

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


def _julia_height_norm_with_corruption(
    nx: float,
    ny: float,
    c: complex,
    *,
    scale: float = 1.0,
    iters: int = 96,
    corruption_level: float = 0.0,
    corruption_seed: int = 1337,
    spline_weight: float = 0.0,
    j_min_x: float = -2.0,
    j_max_x: float = 2.0,
    hotspots: Optional[List[tuple[float, float, float, float]]] = None,
    anchors: Optional[List[tuple[float, float, float, float]]] = None,
) -> tuple[float, float]:
    """Return (height, corruption_strength), where height is 0..1."""
    if corruption_level <= 0.0:
        return _julia_height_norm(nx, ny, c, scale=scale, iters=iters), 0.0
    params = CorruptionParams(
        seed=int(corruption_seed),
        hotspots=list(hotspots or []),
        anchors=list(anchors or []),
        spline_weight=float(spline_weight or 0.0),
    )
    return julia_height_norm_corrupted(
        nx,
        ny,
        c,
        iters=iters,
        scale=scale,
        corruption_level=float(corruption_level),
        params=params,
        j_min_x=float(j_min_x),
        j_max_x=float(j_max_x),
    )


def _julia_height_env_grid_numpy(
    *,
    jx_slice: List[float],
    jy_slice: List[float],
    visual_c: complex,
    iters: int,
    corruption_level: float,
    corruption_seed: int,
    spline_weight: float,
    hotspots: List[tuple[float, float, float, float]],
    anchors: List[tuple[float, float, float, float]],
    j_min_x: float,
    j_max_x: float,
) -> Optional[tuple["object", "object"]]:
    """Compute (height_grid, env_grid) for a local zone using numpy acceleration.

    This keeps *exact* local/world correspondence because it operates directly on the
    per-tile Julia coordinates provided by `tile_julia_grid` slices.

    Returns numpy arrays (h, w) on success, otherwise None (falls back to scalar).
    """
    try:
        import numpy as np
    except Exception:
        return None

    from edgecaster import corruption as corr

    w = int(len(jx_slice))
    h = int(len(jy_slice))
    if w <= 0 or h <= 0:
        return None

    iters = int(iters)
    if iters <= 0:
        iters = 1

    corr_level = float(corruption_level or 0.0)
    corr_params = corr.CorruptionParams(
        seed=int(corruption_seed),
        hotspots=list(hotspots or []),
        anchors=list(anchors or []),
        spline_weight=float(spline_weight or 0.0),
    )

    lut_res = int(getattr(corr, "_LUT_RES", 256))

    # Precompute LUT textures once for the whole grid.
    nx_arr, ny_arr, spots_arr = corr._noise_lut(  # type: ignore[attr-defined]
        int(corr_params.seed),
        float(corr_params.freq),
        float(corr_params.base_res),
        float(corr_params.detail_res),
        float(corr_params.ridge_strength),
        float(corr_params.spot_freq),
        float(corr_params.spot_threshold),
        float(corr_params.spot_weight),
        float(corr_params.right_start),
        float(corr_params.right_sharpness),
        float(corr_params.right_weight),
        float(j_min_x),
        float(j_max_x),
        int(lut_res),
    )
    nx_tex = np.frombuffer(nx_arr, dtype=np.float32)
    ny_tex = np.frombuffer(ny_arr, dtype=np.float32)
    spots_tex = np.frombuffer(spots_arr, dtype=np.float32)

    spline_x_tex = spline_y_tex = None
    if float(getattr(corr_params, "spline_weight", 0.0) or 0.0) > 1e-9:
        sx_arr, sy_arr = corr._spline_lut(  # type: ignore[attr-defined]
            int(corr_params.seed),
            int(getattr(corr_params, "spline_points", 40) or 40),
            float(getattr(corr_params, "spline_strength", 0.35) or 0.35),
            int(getattr(corr_params, "spline_iters", 120) or 120),
            int(lut_res),
        )
        spline_x_tex = np.frombuffer(sx_arr, dtype=np.float32)
        spline_y_tex = np.frombuffer(sy_arr, dtype=np.float32)

    anchor_tex = None
    if corr_params.anchors:
        a_arr = corr._anchor_lut(  # type: ignore[attr-defined]
            corr._anchors_cache_key(corr_params.anchors),  # type: ignore[attr-defined]
            int(lut_res),
        )
        anchor_tex = np.frombuffer(a_arr, dtype=np.float32)

    hot_tex = hot_gx_tex = hot_gy_tex = None
    if corr_params.hotspots:
        h_arr, gx_arr, gy_arr = corr._hot_lut(  # type: ignore[attr-defined]
            corr._hotspots_cache_key(corr_params.hotspots),  # type: ignore[attr-defined]
            int(lut_res),
        )
        hot_tex = np.frombuffer(h_arr, dtype=np.float32)
        hot_gx_tex = np.frombuffer(gx_arr, dtype=np.float32)
        hot_gy_tex = np.frombuffer(gy_arr, dtype=np.float32)

    # Build initial z grid (flattened, for faster alive indexing).
    jx_line = np.asarray(jx_slice, dtype=np.float64)
    jy_line = np.asarray(jy_slice, dtype=np.float64)
    zx0 = np.tile(jx_line, (h, 1))
    zy0 = np.tile(jy_line.reshape(-1, 1), (1, w))
    zx = zx0.reshape(-1).astype(np.float64, copy=True)
    zy = zy0.reshape(-1).astype(np.float64, copy=True)

    n = zx.size
    alive = np.ones(n, dtype=np.bool_)
    escaped_it = np.full(n, iters, dtype=np.int32)
    peak_env = np.zeros(n, dtype=np.float32)

    c_real = float(visual_c.real)
    c_imag = float(visual_c.imag)

    for i in range(iters):
        idx = np.nonzero(alive)[0]
        if idx.size == 0:
            break

        zx_a = zx[idx]
        zy_a = zy[idx]

        if corr_level > 0.0:
            dx_a, dy_a, env_a = corr.distortion_np(  # type: ignore[attr-defined]
                zx_a,
                zy_a,
                params=corr_params,
                j_min_x=float(j_min_x),
                j_max_x=float(j_max_x),
                corruption_level=corr_level,
                nx_tex=nx_tex,
                ny_tex=ny_tex,
                spots_tex=spots_tex,
                spline_x_tex=spline_x_tex,
                spline_y_tex=spline_y_tex,
                anchor_tex=anchor_tex,
                hot_tex=hot_tex,
                hot_gx_tex=hot_gx_tex,
                hot_gy_tex=hot_gy_tex,
                lut_res=lut_res,
            )
            peak_env[idx] = np.maximum(peak_env[idx], env_a.astype(np.float32, copy=False))
        else:
            dx_a = dy_a = 0.0

        zx2 = zx_a * zx_a
        zy2 = zy_a * zy_a
        xt = zx2 - zy2 + c_real + dx_a
        new_zy = 2.0 * zx_a * zy_a + c_imag + dy_a
        zx[idx] = xt
        zy[idx] = new_zy

        r2 = xt * xt + new_zy * new_zy
        escaped = r2 > 4.0
        if np.any(escaped):
            esc_idx = idx[escaped]
            escaped_it[esc_idx] = i + 1
            alive[esc_idx] = False

    # Smooth escape-time height.
    h_out = np.zeros(n, dtype=np.float64)
    mask_escaped = escaped_it < iters
    if np.any(mask_escaped):
        zx_e = zx[mask_escaped]
        zy_e = zy[mask_escaped]
        it_e = escaped_it[mask_escaped].astype(np.float64)
        mod = np.sqrt(zx_e * zx_e + zy_e * zy_e)
        smooth = it_e + 1.0 - (np.log(np.log(np.maximum(mod, 1e-6))) / math.log(2.0))
        h_out[mask_escaped] = np.clip(smooth / float(iters), 0.0, 1.0)

    return h_out.reshape((h, w)), peak_env.reshape((h, w))


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

    visual_c = overmap_params["visual_c"]
    corruption_level = float(overmap_params.get("corruption_level", 0.0) or 0.0)
    corruption_seed = int(overmap_params.get("corruption_seed", 1337) or 1337)
    spline_weight = float(overmap_params.get("corruption_spline_weight", 0.0) or 0.0)
    hotspots = list(overmap_params.get("corruption_hotspots") or [])
    anchors = list(overmap_params.get("corruption_anchors") or [])
    j_min_x = float(overmap_params.get("view_min_jx", overmap_params.get("orig_min_jx", -2.0)) or -2.0)
    j_max_x = float(overmap_params.get("view_max_jx", overmap_params.get("orig_max_jx", 2.0)) or 2.0)

    # Critical performance note:
    # The per-tile Julia loop is already heavy; avoid rebuilding CorruptionParams
    # thousands of times per zone.
    corr_params: CorruptionParams | None = None
    if corruption_level > 0.0:
        corr_params = CorruptionParams(
            seed=int(corruption_seed),
            hotspots=hotspots,
            anchors=anchors,
            spline_weight=float(spline_weight or 0.0),
 
         
        )

    # Fast-path: numpy vectorization for local zones (60x40) makes startup essentially instant,
    # while still using the exact same corruption rules as the world map.
    h_grid = env_grid = None
    biome_grid = None
    if jx_slice is not None and jy_slice is not None:
        try:
            h_grid, env_grid = _julia_height_env_grid_numpy(
                jx_slice=jx_slice,
                jy_slice=jy_slice,
                visual_c=visual_c,
                iters=96,
                corruption_level=corruption_level,
                corruption_seed=corruption_seed,
                spline_weight=spline_weight,
                hotspots=hotspots,
                anchors=anchors,
                j_min_x=j_min_x,
                j_max_x=j_max_x,
            ) or (None, None)
        except Exception:
            h_grid = env_grid = None

    # Compute climate-based biome grid if we have the height data
    j_min_y = float(overmap_params.get("view_min_jy", overmap_params.get("orig_min_jy", -1.5)) or -1.5)
    j_max_y = float(overmap_params.get("view_max_jy", overmap_params.get("orig_max_jy", 1.5)) or 1.5)
    climate_config = overmap_params.get("climate_config")
    if climate_config is None:
        climate_config = ClimateConfig()

    if h_grid is not None and env_grid is not None and jx_slice is not None and jy_slice is not None:
        try:
            biome_grid, _, _, _, ocean_mask = _compute_zone_climate_fields(
                jx_slice=jx_slice,
                jy_slice=jy_slice,
                h_grid=h_grid,
                env_grid=env_grid,
                j_min_y=j_min_y,
                j_max_y=j_max_y,
                climate_config=climate_config,
            )
        except Exception:
            biome_grid = None

    for y in range(h):
        for x in range(w):
            wx = cx0 + x
            wy = cy0 + y

            # Climate-aware biome path (preferred)
            if biome_grid is not None and h_grid is not None and env_grid is not None:
                biome_id = int(biome_grid[y, x])
                corr = float(env_grid[y, x])
                glyph = biome_to_glyph(biome_id)
                tint = _color_from_biome(biome_id)
                # Apply corruption tint on top of biome color
                if corr > 0.0:
                    tint = _apply_corruption_tint(tint, corr)
            # Legacy height-only path (fallback)
            elif jx_slice is not None and jy_slice is not None and h_grid is not None and env_grid is not None:
                h_val = float(h_grid[y, x])
                corr = float(env_grid[y, x])
                fields = {"height": h_val, "moisture": h_val, "pattern": 0.0, "corruption": corr}
                glyph, _ = _classify_tile(fields, 0.5)
                tint = _apply_corruption_tint(_color_from_fields(fields), corr)
            elif jx_slice is not None and jy_slice is not None:
                jx = jx_slice[x]
                jy = jy_slice[y]
                if corr_params is None:
                    h_val = _julia_height_norm(jx, jy, visual_c, scale=1.0, iters=96)
                    corr = 0.0
                else:
                    h_val, corr = julia_height_norm_corrupted(
                        jx,
                        jy,
                        visual_c,
                        iters=96,
                        scale=1.0,
                        corruption_level=corruption_level,
                        params=corr_params,
                        j_min_x=j_min_x,
                        j_max_x=j_max_x,
                    )
                fields = {"height": h_val, "moisture": h_val, "pattern": 0.0, "corruption": corr}
                glyph, _ = _classify_tile(fields, 0.5)
                tint = _apply_corruption_tint(_color_from_fields(fields), corr)
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
            # Store biome ID on the tile for gameplay use
            if biome_grid is not None:
                tile.biome_id = int(biome_grid[y, x])

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


def refresh_fractal_overworld_visuals(
    world: World,
    coord: Tuple[int, int, int],
    *,
    overmap_params: dict,
    jx_slice: Optional[List[float]] = None,
    jy_slice: Optional[List[float]] = None,
) -> None:
    """Recompute glyph+tint for an existing overworld zone without touching structures.

    This is used for corruption morphing (phase 1: visuals only).
    """
    zx, zy, depth = coord
    if depth != 0:
        return
    if jx_slice is None or jy_slice is None:
        return

    w, h = world.width, world.height
    cx0 = zx * w
    cy0 = zy * h

    # Respect any known structure footprints (e.g., starting-zone depot).
    protected: set[tuple[int, int]] = set()
    depot_info = getattr(world, "depot_info", None)
    if depot_info and isinstance(depot_info, dict):
        rect = depot_info.get("rect")
        if rect and len(rect) == 4:
            rx, ry, rw, rh = rect
            for yy in range(int(ry), int(ry + rh)):
                for xx in range(int(rx), int(rx + rw)):
                    protected.add((xx, yy))

    for pos in (getattr(world, "up_stairs", None), getattr(world, "down_stairs", None)):
        if pos and isinstance(pos, tuple) and len(pos) == 2:
            protected.add((int(pos[0]), int(pos[1])))

    visual_c = overmap_params["visual_c"]
    corruption_level = float(overmap_params.get("corruption_level", 0.0) or 0.0)
    corruption_seed = int(overmap_params.get("corruption_seed", 1337) or 1337)
    spline_weight = float(overmap_params.get("corruption_spline_weight", 0.0) or 0.0)
    hotspots = list(overmap_params.get("corruption_hotspots") or [])
    anchors = list(overmap_params.get("corruption_anchors") or [])
    j_min_x = float(overmap_params.get("view_min_jx", overmap_params.get("orig_min_jx", -2.0)) or -2.0)
    j_max_x = float(overmap_params.get("view_max_jx", overmap_params.get("orig_max_jx", 2.0)) or 2.0)

    corr_params: CorruptionParams | None = None
    if corruption_level > 0.0:
        corr_params = CorruptionParams(
            seed=int(corruption_seed),
            hotspots=hotspots,
            anchors=anchors,
            spline_weight=float(spline_weight or 0.0),
        )

    h_grid = env_grid = None
    try:
        h_grid, env_grid = _julia_height_env_grid_numpy(
            jx_slice=jx_slice,
            jy_slice=jy_slice,
            visual_c=visual_c,
            iters=96,
            corruption_level=corruption_level,
            corruption_seed=corruption_seed,
            spline_weight=spline_weight,
            hotspots=hotspots,
            anchors=anchors,
            j_min_x=j_min_x,
            j_max_x=j_max_x,
        ) or (None, None)
    except Exception:
        h_grid = env_grid = None

    for y in range(h):
        for x in range(w):
            if (x, y) in protected:
                continue
            jx = jx_slice[x]
            jy = jy_slice[y]
            if h_grid is not None and env_grid is not None:
                h_val = float(h_grid[y, x])
                corr = float(env_grid[y, x])
            elif corr_params is None:
                h_val = _julia_height_norm(jx, jy, visual_c, scale=1.0, iters=96)
                corr = 0.0
            else:
                h_val, corr = julia_height_norm_corrupted(
                    jx,
                    jy,
                    visual_c,
                    iters=96,
                    scale=1.0,
                    corruption_level=corruption_level,
                    params=corr_params,
                    j_min_x=j_min_x,
                    j_max_x=j_max_x,
                )
            fields = {"height": h_val, "moisture": h_val, "pattern": 0.0, "corruption": corr}
            glyph, _ = _classify_tile(fields, 0.5)
            tint = _apply_corruption_tint(_color_from_fields(fields), corr)
            tile = world.get_tile(x, y)
            if tile:
                tile.glyph = glyph if glyph != "~" else "."
                tile.tint = tint


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
