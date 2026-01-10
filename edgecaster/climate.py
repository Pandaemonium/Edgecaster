# edgecaster/climate.py
"""
Climate and biome system for Edgecaster.

This module provides:
- ClimateConfig: Configuration for climate simulation
- Biome enum: 21-biome classification system
- Climate field computation: temperature, wind, moisture
- Biome classification based on elevation, temperature, moisture

Ported from tools/julia_world_fields_viewer.py for game integration.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Tuple, Dict, Optional

import numpy as np


# -----------------------------
# Biome Classification
# -----------------------------

class Biome(IntEnum):
    """Biome types derived from elevation, temperature, and moisture."""
    OCEAN = 0
    LAKE = 1
    ICE = 2
    TUNDRA = 3
    BARE = 4           # High elevation, low moisture
    SCORCHED = 5       # Hot desert
    TAIGA = 6          # Cold forest
    SHRUBLAND = 7
    TEMPERATE_DESERT = 8
    TEMPERATE_GRASSLAND = 9
    TEMPERATE_FOREST = 10
    TEMPERATE_RAINFOREST = 11
    SUBTROPICAL_DESERT = 12
    SAVANNA = 13
    TROPICAL_FOREST = 14
    TROPICAL_RAINFOREST = 15
    SNOW = 16          # Highest peaks
    RIVER = 17         # Special: traced rivers
    # Corrupted variants (biome + corruption overlay)
    CORRUPTED_WASTELAND = 18     # Generic high-corruption land
    CORRUPTED_FOREST = 19        # Corrupted forest/jungle
    CORRUPTED_WATER = 20         # Corrupted ocean/lake


# Corruption threshold for biome override (distortion magnitude)
CORRUPTION_BIOME_THRESHOLD = 0.15


# Biome colors for visualization (RGB tuples, 0-255 range for Edgecaster)
BIOME_COLORS: Dict[Biome, Tuple[int, int, int]] = {
    Biome.OCEAN: (20, 56, 140),           # Deep blue
    Biome.LAKE: (51, 128, 191),           # Lighter blue
    Biome.ICE: (217, 242, 255),           # Icy white-blue
    Biome.TUNDRA: (179, 191, 153),        # Pale olive-gray
    Biome.BARE: (128, 115, 102),          # Rocky brown-gray
    Biome.SCORCHED: (191, 128, 77),       # Burnt orange
    Biome.TAIGA: (64, 128, 102),          # Dark teal-green
    Biome.SHRUBLAND: (179, 191, 89),      # Yellow-green
    Biome.TEMPERATE_DESERT: (230, 204, 140),   # Sandy tan
    Biome.TEMPERATE_GRASSLAND: (140, 204, 89), # Bright grass green
    Biome.TEMPERATE_FOREST: (51, 140, 64),     # Forest green
    Biome.TEMPERATE_RAINFOREST: (26, 115, 77), # Deep emerald
    Biome.SUBTROPICAL_DESERT: (242, 217, 128), # Bright sand
    Biome.SAVANNA: (217, 204, 102),       # Golden yellow
    Biome.TROPICAL_FOREST: (38, 153, 64),      # Lush green
    Biome.TROPICAL_RAINFOREST: (13, 115, 51),  # Dark jungle green
    Biome.SNOW: (255, 255, 255),          # Pure white
    Biome.RIVER: (51, 115, 204),          # Bright river blue
    # Corrupted variants - purplish/sickly colors
    Biome.CORRUPTED_WASTELAND: (115, 64, 128),     # Purple-brown wasteland
    Biome.CORRUPTED_FOREST: (77, 89, 115),         # Sickly gray-purple
    Biome.CORRUPTED_WATER: (89, 51, 115),          # Dark purple water
}

# Biome glyphs for ASCII rendering
BIOME_GLYPHS: Dict[Biome, str] = {
    Biome.OCEAN: '~',
    Biome.LAKE: '~',
    Biome.ICE: '#',
    Biome.TUNDRA: ':',
    Biome.BARE: '^',
    Biome.SCORCHED: '.',
    Biome.TAIGA: 't',
    Biome.SHRUBLAND: ';',
    Biome.TEMPERATE_DESERT: '.',
    Biome.TEMPERATE_GRASSLAND: ',',
    Biome.TEMPERATE_FOREST: 'T',
    Biome.TEMPERATE_RAINFOREST: 'Y',
    Biome.SUBTROPICAL_DESERT: '.',
    Biome.SAVANNA: ',',
    Biome.TROPICAL_FOREST: 'T',
    Biome.TROPICAL_RAINFOREST: 'Y',
    Biome.SNOW: '*',
    Biome.RIVER: '=',
    Biome.CORRUPTED_WASTELAND: '%',
    Biome.CORRUPTED_FOREST: '&',
    Biome.CORRUPTED_WATER: '~',
}

# Short names for display
BIOME_SHORT_NAMES: Dict[Biome, str] = {
    Biome.OCEAN: "Ocean",
    Biome.LAKE: "Lake",
    Biome.ICE: "Ice",
    Biome.TUNDRA: "Tundra",
    Biome.BARE: "Bare",
    Biome.SCORCHED: "Scorched",
    Biome.TAIGA: "Taiga",
    Biome.SHRUBLAND: "Shrub",
    Biome.TEMPERATE_DESERT: "T.Desert",
    Biome.TEMPERATE_GRASSLAND: "T.Grass",
    Biome.TEMPERATE_FOREST: "T.Forest",
    Biome.TEMPERATE_RAINFOREST: "T.Rain",
    Biome.SUBTROPICAL_DESERT: "S.Desert",
    Biome.SAVANNA: "Savanna",
    Biome.TROPICAL_FOREST: "Tr.Forest",
    Biome.TROPICAL_RAINFOREST: "Tr.Rain",
    Biome.SNOW: "Snow",
    Biome.RIVER: "River",
    Biome.CORRUPTED_WASTELAND: "C.Waste",
    Biome.CORRUPTED_FOREST: "C.Forest",
    Biome.CORRUPTED_WATER: "C.Water",
}


# -----------------------------
# Climate Configuration
# -----------------------------

@dataclass(frozen=True)
class ClimateConfig:
    """Configuration for climate simulation."""
    # Main switches
    earth_mode: bool = True
    humidity_capacity: bool = True
    advect_moisture: bool = True
    diffuse_moisture: bool = True
    asymmetry: bool = True

    # Land coverage control
    land_boost: float = 0.5   # 0.0 = narrow ridges, 1.0 = max land
    sea_level: float = 0.0    # Threshold for water (E_clim < sea_level)

    # Temperature knobs
    lat_power: float = 1.2
    elev_scale: float = 0.6
    elev_power: float = 1.2
    w_lat: float = 0.55
    w_elev: float = 0.40
    w_ocean: float = 0.05
    ocean_L: float = 10.0
    temp_offset: float = 0.0

    # Wind knobs (Earth-like mode)
    w_zonal_earth: float = 0.55
    w_temp_earth: float = 0.80
    w_terrain_earth: float = 0.12
    # Wind knobs (Weird mode)
    w_zonal_weird: float = 1.00
    w_temp_weird: float = 0.10
    w_terrain_weird: float = 0.35

    # Moisture knobs
    advect_steps: int = 42
    advect_dt: float = 0.75
    src_weight: float = 0.18
    drizzle: float = 0.025
    k_orog: float = 2.5
    cap_k: float = 1.2

    # Moisture diffusion
    diffuse_iters: int = 6
    diffuse_alpha: float = 0.22

    # Asymmetry knobs
    asym_scale: float = 1.4
    asym_strength: float = 0.08

    # Geothermal knobs
    use_geothermal: bool = False
    geothermal_strength: float = 0.12


# -----------------------------
# Helper Functions
# -----------------------------

def clamp01(a: np.ndarray) -> np.ndarray:
    """Clamp array values to [0, 1]."""
    return np.clip(a, 0.0, 1.0)


def gauss(x: np.ndarray, center: float, sigma: float) -> np.ndarray:
    """Gaussian function centered at 'center' with width 'sigma'."""
    return np.exp(-((x - center) ** 2) / (2 * sigma ** 2))


def smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    """Smooth Hermite interpolation between edge0 and edge1."""
    t = np.clip((x - edge0) / (edge1 - edge0 + 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# -----------------------------
# Elevation Transfer Function
# -----------------------------

def elev_ridge_belt(
    t: np.ndarray,
    *,
    land_boost: float = 0.0,
) -> np.ndarray:
    """
    Non-monotonic "ridge belt" elevation mapping.

    Args:
        t: Normalized escape time in [0, 1]. 0 = fast escape (ocean), 1 = bounded (lakes)
        land_boost: 0.0 = original narrow ridges, 1.0 = maximum land coverage

    Returns:
        Elevation array where:
        - Fast escape (low t) -> negative (ocean)
        - Moderate escape (medium t) -> positive peaks (land ridge belt)
        - Bounded (t ~= 1) -> zero or negative (inland freshwater)
    """
    # Boost factor affects ridge width and height
    boost = float(land_boost)
    # Original parameters (narrow ridges)
    ridge_center_base = 0.45
    ridge_sigma_base = 0.14
    ocean_depth_base = 0.8
    peak_height_base = 0.85

    # Boosted parameters (wider ridges, taller peaks, narrower ocean trough)
    ridge_center_boost = 0.50
    ridge_sigma_boost = 0.22
    ocean_depth_boost = 0.5
    peak_height_boost = 1.0

    ridge_center = ridge_center_base + boost * (ridge_center_boost - ridge_center_base)
    ridge_sigma = ridge_sigma_base + boost * (ridge_sigma_boost - ridge_sigma_base)
    ocean_depth = ocean_depth_base + boost * (ocean_depth_boost - ocean_depth_base)
    peak_height = peak_height_base + boost * (peak_height_boost - peak_height_base)

    # Ocean trough (fast escape = negative elevation)
    ocean_edge = 0.15 + boost * 0.05
    ocean_trough = -ocean_depth * (1.0 - smoothstep(0.0, ocean_edge, t))

    # Ridge belt (Gaussian peak)
    ridge = peak_height * gauss(t, ridge_center, ridge_sigma)

    # Inland depression (bounded = t near 1.0)
    inland_start = 0.92 - boost * 0.05
    inland_depth = 0.25 + boost * 0.15
    inland_dip = -inland_depth * smoothstep(inland_start, 1.0, t)

    E = ocean_trough + ridge + inland_dip
    return E.astype(np.float32)


# -----------------------------
# Water Classification
# -----------------------------

def ocean_lake_masks_from_water(
    water: np.ndarray,
    t: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Classify water cells into ocean vs inland lake.

    Ocean: exterior water connected to boundary (fast escape = low t)
    Lake: interior water not connected to ocean, OR bounded/slow-escape water

    Args:
        water: Boolean mask of all water cells
        t: Optional normalized escape time. If provided, cells with t > 0.85
           (slow escape / bounded) are classified as inland lake instead of ocean.

    Returns:
        (ocean_mask, lake_mask) as boolean arrays
    """
    h, w = water.shape

    # Separate exterior water (fast escape) from interior water (slow/bounded)
    if t is not None:
        # Use t to distinguish: fast escape (ocean candidate) vs slow/bounded (lake)
        # t > 0.85 typically means near the Julia set boundary or bounded
        exterior_water = water & (t <= 0.85)
        inland_water = water & (t > 0.85)
    else:
        exterior_water = water.copy()
        inland_water = np.zeros_like(water, dtype=bool)

    # BFS from boundary to find ocean (exterior water connected to edges)
    ocean = np.zeros_like(water, dtype=bool)
    dq = deque()

    # Seed from boundary water cells
    for x in range(w):
        if exterior_water[0, x]:
            ocean[0, x] = True
            dq.append((0, x))
        if exterior_water[h - 1, x]:
            ocean[h - 1, x] = True
            dq.append((h - 1, x))
    for y in range(h):
        if exterior_water[y, 0]:
            ocean[y, 0] = True
            dq.append((y, 0))
        if exterior_water[y, w - 1]:
            ocean[y, w - 1] = True
            dq.append((y, w - 1))

    # BFS through exterior water only
    while dq:
        y, x = dq.popleft()
        for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
            if 0 <= ny < h and 0 <= nx < w and exterior_water[ny, nx] and not ocean[ny, nx]:
                ocean[ny, nx] = True
                dq.append((ny, nx))

    # Lake = inland water OR (exterior water not connected to boundary)
    lake = inland_water | (exterior_water & (~ocean))
    return ocean, lake


def manhattan_distance_to(mask: np.ndarray) -> np.ndarray:
    """
    Multi-source BFS Manhattan distance to nearest True cell.
    Returns float32 distances in grid steps.
    """
    h, w = mask.shape
    dist = np.full((h, w), np.inf, dtype=np.float32)
    dq = deque()

    ys, xs = np.where(mask)
    if ys.size == 0:
        dist[:] = float(h + w)
        return dist

    for y, x in zip(ys, xs):
        dist[y, x] = 0.0
        dq.append((y, x))

    while dq:
        y, x = dq.popleft()
        d = dist[y, x]
        nd = d + 1.0
        for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
            if 0 <= ny < h and 0 <= nx < w and nd < dist[ny, nx]:
                dist[ny, nx] = nd
                dq.append((ny, nx))

    return dist


# -----------------------------
# Climate Field Computation
# -----------------------------

def _normalize_uv(U: np.ndarray, V: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Normalize wind vectors to unit length."""
    m = np.sqrt(U * U + V * V) + 1e-6
    return (U / m).astype(np.float32), (V / m).astype(np.float32)


def compute_temperature(
    E_clim: np.ndarray,
    ocean_dist: np.ndarray,
    lat: np.ndarray,
    cfg: ClimateConfig,
    geothermal: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Compute normalized temperature [0,1].

    Warmer near equator, cooler at high elevation, moderated near ocean.
    """
    # Latitude warmth
    T_lat = 1.0 - np.abs(lat) ** float(cfg.lat_power)

    # Elevation lapse
    E_land = np.maximum(E_clim, 0.0)
    H = clamp01(E_land / float(cfg.elev_scale))
    T_elev = 1.0 - (H ** float(cfg.elev_power))

    # Maritime moderation
    T_ocean = np.exp(-ocean_dist / float(cfg.ocean_L))

    # Blend
    T = float(cfg.w_lat) * T_lat + float(cfg.w_ocean) * T_ocean + float(cfg.w_elev) * T_elev

    # Temperature offset
    if cfg.temp_offset != 0:
        T = T + float(cfg.temp_offset)

    # Optional geothermal heat
    if geothermal is not None and cfg.use_geothermal and cfg.geothermal_strength > 0:
        T = T + float(cfg.geothermal_strength) * geothermal

    return clamp01(T).astype(np.float32)


def compute_wind_field(
    E_clim: np.ndarray,
    lat: np.ndarray,
    T: np.ndarray,
    cfg: ClimateConfig,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute wind field (U, V) vectors.

    Two modes:
    - earth_mode=True: temperature gradients steer wind
    - earth_mode=False: terrain deflection dominates
    """
    # Base zonal flow
    a = np.abs(lat)
    base_u = np.where((a < 0.25) | (a > 0.75), -1.0, +1.0).astype(np.float32)
    base_v = np.zeros_like(base_u, dtype=np.float32)

    # Terrain deflection
    dEy, dEx = np.gradient(E_clim.astype(np.float32))
    def_u = (-dEy).astype(np.float32)
    def_v = (dEx).astype(np.float32)
    def_u, def_v = _normalize_uv(def_u, def_v)

    # Temperature-driven component
    dTy, dTx = np.gradient(T.astype(np.float32))
    tmp_u = (-dTy).astype(np.float32)
    tmp_v = (dTx).astype(np.float32)

    hemi = np.where(lat >= 0.0, 1.0, -1.0).astype(np.float32)
    tmp_u *= hemi
    tmp_v *= hemi
    tmp_u, tmp_v = _normalize_uv(tmp_u, tmp_v)

    if cfg.earth_mode:
        w_zonal = cfg.w_zonal_earth
        w_temp = cfg.w_temp_earth
        w_terrain = cfg.w_terrain_earth
    else:
        w_zonal = cfg.w_zonal_weird
        w_temp = cfg.w_temp_weird
        w_terrain = cfg.w_terrain_weird

    U = w_zonal * base_u + w_temp * tmp_u + w_terrain * def_u
    V = w_zonal * base_v + w_temp * tmp_v + w_terrain * def_v
    return _normalize_uv(U.astype(np.float32), V.astype(np.float32))


def _bilinear_sample(field: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sampling of a 2D field at float coordinates."""
    h, w = field.shape
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = x0 + 1
    y1 = y0 + 1

    x0 = np.clip(x0, 0, w - 1)
    x1 = np.clip(x1, 0, w - 1)
    y0 = np.clip(y0, 0, h - 1)
    y1 = np.clip(y1, 0, h - 1)

    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)

    f00 = field[y0, x0]
    f10 = field[y0, x1]
    f01 = field[y1, x0]
    f11 = field[y1, x1]

    a = f00 * (1.0 - fx) + f10 * fx
    b = f01 * (1.0 - fx) + f11 * fx
    return (a * (1.0 - fy) + b * fy).astype(np.float32)


def _diffuse4(field: np.ndarray, *, alpha: float, iters: int) -> np.ndarray:
    """4-neighbor diffusion without wrap-around."""
    iters = int(iters)
    if iters <= 0:
        return field
    a = float(alpha)
    if a <= 0.0:
        return field
    if a > 1.0:
        a = 1.0

    F = field.astype(np.float32, copy=True)
    h, w = F.shape
    if h < 2 or w < 2:
        return F

    for _ in range(iters):
        neigh = np.zeros_like(F)
        cnt = np.zeros_like(F)

        neigh[1:, :] += F[:-1, :]
        cnt[1:, :] += 1.0
        neigh[:-1, :] += F[1:, :]
        cnt[:-1, :] += 1.0
        neigh[:, 1:] += F[:, :-1]
        cnt[:, 1:] += 1.0
        neigh[:, :-1] += F[:, 1:]
        cnt[:, :-1] += 1.0

        avg = neigh / np.maximum(cnt, 1.0)
        F = (1.0 - a) * F + a * avg

    return F


def _humidity_capacity(T: np.ndarray, cfg: ClimateConfig) -> np.ndarray:
    """Temperature-dependent humidity capacity multiplier."""
    cap = np.exp(float(cfg.cap_k) * (T.astype(np.float32) - 0.5))
    return np.clip(cap, 0.35, 2.2).astype(np.float32)


def compute_moisture(
    E_clim: np.ndarray,
    ocean_dist: np.ndarray,
    lake_dist: np.ndarray,
    U: np.ndarray,
    V: np.ndarray,
    T: np.ndarray,
    cfg: ClimateConfig,
) -> np.ndarray:
    """
    Compute moisture [0,1] from ocean/lake proximity and wind advection.
    """
    h, w = E_clim.shape

    # Source humidity
    Lm = 14.0
    Ll = 10.0
    src = np.exp(-ocean_dist / Lm) + 0.45 * np.exp(-lake_dist / Ll)
    src = src.astype(np.float32)

    # Temperature-dependent capacity
    cap = _humidity_capacity(T, cfg) if cfg.humidity_capacity else np.ones_like(T, dtype=np.float32)

    if cfg.advect_moisture:
        # Semi-Lagrangian advection
        H = np.zeros_like(E_clim, dtype=np.float32)
        P = np.zeros_like(E_clim, dtype=np.float32)
        dEy, dEx = np.gradient(E_clim.astype(np.float32))

        Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
        dt = float(cfg.advect_dt)

        steps = int(max(1, cfg.advect_steps))
        for _ in range(steps):
            x_back = X - U * dt
            y_back = Y - V * dt
            H = _bilinear_sample(H, x_back, y_back)

            H += float(cfg.src_weight) * src
            H = np.minimum(H, cap)

            upslope = np.maximum(0.0, U * dEx + V * dEy).astype(np.float32)
            rain = H * (1.0 - np.exp(-float(cfg.k_orog) * upslope)).astype(np.float32)
            H = np.maximum(0.0, H - rain)
            P += rain

            P += float(cfg.drizzle) * H

        moisture = P
    else:
        # Simple row sweep
        mean_u = float(np.mean(U))
        west_to_east = mean_u >= 0.0

        moisture = np.zeros_like(E_clim, dtype=np.float32)
        for y in range(h):
            m = 0.0
            if west_to_east:
                x_iter = range(w)
                dx = +1
            else:
                x_iter = range(w - 1, -1, -1)
                dx = -1

            for x in x_iter:
                m = 0.985 * m + 0.12 * float(src[y, x])
                m = min(float(m), float(cap[y, x]))

                x_prev = x - dx
                de = float(E_clim[y, x] - E_clim[y, x_prev]) if 0 <= x_prev < w else 0.0
                if de > 0.0:
                    rain = m * (1.0 - np.exp(-3.0 * de))
                    m = max(0.0, m - rain)
                    moisture[y, x] += float(rain)

                moisture[y, x] += 0.03 * float(m)

    # Lake effect
    moisture += 0.25 * np.exp(-lake_dist / 8.0).astype(np.float32)

    # Diffusion
    if cfg.diffuse_moisture:
        moisture = _diffuse4(moisture, alpha=float(cfg.diffuse_alpha), iters=int(cfg.diffuse_iters))

    # Normalize to [0,1]
    finite = moisture[np.isfinite(moisture)]
    if finite.size == 0:
        return np.zeros_like(moisture, dtype=np.float32)
    vmin, vmax = np.percentile(finite, [2, 98])
    if vmax <= vmin + 1e-6:
        return np.zeros_like(moisture, dtype=np.float32)
    M = (moisture - float(vmin)) / float(vmax - vmin)
    return clamp01(M).astype(np.float32)


# -----------------------------
# River Generation
# -----------------------------

def compute_flow_direction(E: np.ndarray) -> np.ndarray:
    """
    Compute flow direction (D8 algorithm).
    Returns direction index (0-7) or -1 for sinks.
    """
    h, w = E.shape
    flow_dir = np.full((h, w), -1, dtype=np.int8)

    dirs = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    dist = [1.0, 1.414, 1.0, 1.414, 1.0, 1.414, 1.0, 1.414]

    for y in range(h):
        for x in range(w):
            e0 = E[y, x]
            max_slope = 0.0
            best_dir = -1

            for d, (dy, dx) in enumerate(dirs):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w:
                    slope = (e0 - E[ny, nx]) / dist[d]
                    if slope > max_slope:
                        max_slope = slope
                        best_dir = d

            flow_dir[y, x] = best_dir

    return flow_dir


def compute_flow_accumulation(flow_dir: np.ndarray) -> np.ndarray:
    """Compute flow accumulation from flow direction."""
    h, w = flow_dir.shape
    accum = np.ones((h, w), dtype=np.float32)

    dirs = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]

    in_degree = np.zeros((h, w), dtype=np.int32)
    for y in range(h):
        for x in range(w):
            d = flow_dir[y, x]
            if d >= 0:
                dy, dx = dirs[d]
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w:
                    in_degree[ny, nx] += 1

    queue_y, queue_x = np.where(in_degree == 0)
    process_queue = deque(zip(queue_y, queue_x))

    while process_queue:
        y, x = process_queue.popleft()
        d = flow_dir[y, x]
        if d >= 0:
            dy, dx = dirs[d]
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                accum[ny, nx] += accum[y, x]
                in_degree[ny, nx] -= 1
                if in_degree[ny, nx] == 0:
                    process_queue.append((ny, nx))

    return accum


def trace_rivers(
    E: np.ndarray,
    flow_accum: np.ndarray,
    ocean_mask: np.ndarray,
    lake_mask: np.ndarray,
    threshold: float = 0.002,
) -> np.ndarray:
    """Create river mask based on flow accumulation."""
    max_accum = np.max(flow_accum)
    if max_accum <= 0:
        return np.zeros_like(E, dtype=bool)

    accum_norm = flow_accum / max_accum
    land = ~(ocean_mask | lake_mask)
    rivers = land & (accum_norm > threshold)

    return rivers


# -----------------------------
# Biome Classification
# -----------------------------

def classify_biome(
    E: np.ndarray,
    T: np.ndarray,
    M: np.ndarray,
    ocean_mask: np.ndarray,
    lake_mask: np.ndarray,
    corruption_env: Optional[np.ndarray] = None,
    corruption_threshold: float = CORRUPTION_BIOME_THRESHOLD,
) -> np.ndarray:
    """
    Classify each cell into a biome based on elevation, temperature, moisture.

    Args:
        E: Climate elevation (unscaled, ~0 to 1.5 range)
        T: Temperature [0, 1]
        M: Moisture [0, 1]
        ocean_mask: Boolean mask for ocean
        lake_mask: Boolean mask for lakes
        corruption_env: Optional corruption magnitude array
        corruption_threshold: Threshold for corruption biome override

    Returns:
        Integer array of Biome enum values
    """
    h, w = E.shape
    biome = np.full((h, w), Biome.TEMPERATE_GRASSLAND, dtype=np.int32)

    # Water bodies first
    biome[ocean_mask] = Biome.OCEAN
    biome[lake_mask] = Biome.LAKE

    # Land classification
    land = ~(ocean_mask | lake_mask)

    # Normalize elevation for land cells using median-based scaling
    E_land = np.where(land, E, 0)
    E_median = np.median(E_land[land]) if np.any(land) else 0.5
    E_median = max(E_median, 0.05)
    E_norm = np.clip(E_land / (E_median * 2.0), 0, 1)

    # High elevation
    high_elev = land & (E_norm > 0.65)
    biome[high_elev & (T < 0.25)] = Biome.SNOW
    biome[high_elev & (T >= 0.25) & (T < 0.45)] = Biome.BARE
    biome[high_elev & (T >= 0.45) & (M < 0.35)] = Biome.BARE
    biome[high_elev & (T >= 0.45) & (M >= 0.35)] = Biome.TUNDRA

    # Mid-high elevation
    mid_high = land & (E_norm > 0.40) & (E_norm <= 0.65)
    biome[mid_high & (T < 0.30)] = Biome.TUNDRA
    biome[mid_high & (T >= 0.30) & (T < 0.55) & (M < 0.35)] = Biome.SHRUBLAND
    biome[mid_high & (T >= 0.30) & (T < 0.55) & (M >= 0.35)] = Biome.TAIGA
    biome[mid_high & (T >= 0.55) & (M < 0.30)] = Biome.TEMPERATE_DESERT
    biome[mid_high & (T >= 0.55) & (M >= 0.30) & (M < 0.55)] = Biome.SHRUBLAND
    biome[mid_high & (T >= 0.55) & (M >= 0.55)] = Biome.TEMPERATE_FOREST

    # Low-mid elevation
    low_mid = land & (E_norm > 0.15) & (E_norm <= 0.40)

    # Cold
    biome[low_mid & (T < 0.25)] = Biome.TUNDRA
    biome[low_mid & (T < 0.1) & (M > 0.75)] = Biome.ICE

    # Cool-temperate
    biome[low_mid & (T >= 0.25) & (T < 0.5) & (M < 0.25)] = Biome.TEMPERATE_DESERT
    biome[low_mid & (T >= 0.25) & (T < 0.5) & (M >= 0.25) & (M < 0.55)] = Biome.TEMPERATE_GRASSLAND
    biome[low_mid & (T >= 0.25) & (T < 0.5) & (M >= 0.55) & (M < 0.80)] = Biome.TEMPERATE_FOREST
    biome[low_mid & (T >= 0.25) & (T < 0.5) & (M >= 0.80)] = Biome.TEMPERATE_RAINFOREST

    # Warm-subtropical
    biome[low_mid & (T >= 0.5) & (T < 0.70) & (M < 0.25)] = Biome.SUBTROPICAL_DESERT
    biome[low_mid & (T >= 0.5) & (T < 0.70) & (M >= 0.25) & (M < 0.55)] = Biome.SAVANNA
    biome[low_mid & (T >= 0.5) & (T < 0.70) & (M >= 0.55) & (M < 0.80)] = Biome.TROPICAL_FOREST
    biome[low_mid & (T >= 0.5) & (T < 0.70) & (M >= 0.80)] = Biome.TROPICAL_RAINFOREST

    # Hot-tropical
    biome[low_mid & (T >= 0.70) & (M < 0.20)] = Biome.SCORCHED
    biome[low_mid & (T >= 0.70) & (M >= 0.20) & (M < 0.40)] = Biome.SUBTROPICAL_DESERT
    biome[low_mid & (T >= 0.70) & (M >= 0.40) & (M < 0.65)] = Biome.SAVANNA
    biome[low_mid & (T >= 0.70) & (M >= 0.65)] = Biome.TROPICAL_RAINFOREST

    # Coastal / low elevation
    coastal = land & (E_norm <= 0.15)
    biome[coastal & (T < 0.15)] = Biome.ICE
    biome[coastal & (T >= 0.15) & (T < 0.30)] = Biome.TUNDRA
    biome[coastal & (T >= 0.30) & (T < 0.50) & (M < 0.35)] = Biome.TEMPERATE_GRASSLAND
    biome[coastal & (T >= 0.30) & (T < 0.50) & (M >= 0.35)] = Biome.TEMPERATE_FOREST
    biome[coastal & (T >= 0.50) & (T < 0.70) & (M < 0.35)] = Biome.SAVANNA
    biome[coastal & (T >= 0.50) & (T < 0.70) & (M >= 0.35)] = Biome.TROPICAL_FOREST
    biome[coastal & (T >= 0.70) & (M < 0.30)] = Biome.SUBTROPICAL_DESERT
    biome[coastal & (T >= 0.7) & (M >= 0.25)] = Biome.TROPICAL_RAINFOREST

    # Corruption overrides
    if corruption_env is not None:
        corrupted = corruption_env > corruption_threshold
        is_water = ocean_mask | lake_mask
        is_forest = np.isin(biome, [
            Biome.TAIGA, Biome.TEMPERATE_FOREST, Biome.TEMPERATE_RAINFOREST,
            Biome.TROPICAL_FOREST, Biome.TROPICAL_RAINFOREST
        ])

        biome[corrupted & is_water] = Biome.CORRUPTED_WATER
        biome[corrupted & is_forest & ~is_water] = Biome.CORRUPTED_FOREST
        biome[corrupted & land & ~is_forest] = Biome.CORRUPTED_WASTELAND

    return biome


def biome_to_color(biome: np.ndarray) -> np.ndarray:
    """
    Convert biome indices to RGB colors.

    Args:
        biome: Integer array of Biome enum values

    Returns:
        (h, w, 3) uint8 RGB array
    """
    h, w = biome.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)

    for b in Biome:
        mask = biome == int(b)
        if np.any(mask):
            color = BIOME_COLORS.get(b, (128, 128, 128))
            rgb[mask] = color

    return rgb


def biome_to_glyph(biome_id: int) -> str:
    """Get ASCII glyph for a biome."""
    try:
        b = Biome(biome_id)
        return BIOME_GLYPHS.get(b, '?')
    except ValueError:
        return '?'
