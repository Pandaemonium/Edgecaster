# edgecaster/systems/site_placement.py
"""
Site placement system - Procedural POI placement based on biome suitability.

This module handles:
- Computing suitability maps for different site types
- Placing sites using weighted sampling with spacing constraints
- Integration with the climate/biome system

Site placement follows a "meaning-first" approach:
1. Compute climate fields over a coarse grid (overmap resolution)
2. Classify biomes
3. For each site type, compute suitability based on biome affinity + thresholds
4. Place sites via weighted Poisson-disk-like sampling
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import numpy as np

from edgecaster.climate import (
    Biome,
    ClimateConfig,
    classify_biome,
    compute_moisture,
    compute_temperature,
    compute_wind_field,
    elev_ridge_belt,
    manhattan_distance_to,
    ocean_lake_masks_from_water,
)
from edgecaster.state.sites import SiteSpec, SiteTypeConfig, SiteVisibility
from edgecaster.systems.sites import SiteRegistry, load_site_types

if TYPE_CHECKING:
    from edgecaster.game import Game


def _sample_climate_grid(
    game: "Game",
    resolution: int = 4,
) -> Dict[str, np.ndarray]:
    """
    Sample climate fields over the entire world at reduced resolution.

    Args:
        game: Game instance with overmap_params and tile_julia_grid
        resolution: Downsample factor (1 = full resolution, 4 = 1/4 pixels)

    Returns:
        Dict with climate arrays: biome, E_clim, T, M, corruption, water_dist
    """
    params = getattr(game, "overmap_params", None)
    grid = getattr(game, "tile_julia_grid", None)
    if not params or not grid:
        return {}

    cfg = getattr(game, "climate_config", None) or ClimateConfig()

    # World dimensions
    total_x = grid["total_x"]
    total_y = grid["total_y"]

    # Reduced resolution grid
    px_w = max(1, total_x // resolution)
    px_h = max(1, total_y // resolution)

    j_min_x = grid["view_min_jx"]
    j_max_x = grid["view_max_jx"]
    j_min_y = grid["view_min_jy"]
    j_max_y = grid["view_max_jy"]

    # Julia coordinates for sampling
    jx_line = np.linspace(j_min_x, j_max_x, px_w, dtype=np.float64)
    jy_line = np.linspace(j_min_y, j_max_y, px_h, dtype=np.float64)

    zx0 = np.tile(jx_line, (px_h, 1))
    zy0 = np.tile(jy_line.reshape(-1, 1), (1, px_w))

    zx = zx0.reshape(-1).astype(np.float64, copy=True)
    zy = zy0.reshape(-1).astype(np.float64, copy=True)
    n = zx.size

    # Julia iteration (simplified - no corruption for site placement)
    visual_c = params.get("visual_c", complex(-0.4, 0.6))
    c_real = float(visual_c.real)
    c_imag = float(visual_c.imag)
    iters = 64

    alive = np.ones(n, dtype=np.bool_)
    escaped_it = np.full(n, iters, dtype=np.int32)
    peak_env = np.zeros(n, dtype=np.float32)

    # Get corruption envelope if available
    corr_level = float(params.get("corruption_level", 0.0) or 0.0)

    for i in range(iters):
        idx = np.nonzero(alive)[0]
        if idx.size == 0:
            break

        zx_a = zx[idx]
        zy_a = zy[idx]

        zx2 = zx_a * zx_a
        zy2 = zy_a * zy_a
        xt = zx2 - zy2 + c_real
        new_zy = 2.0 * zx_a * zy_a + c_imag

        zx[idx] = xt
        zy[idx] = new_zy

        r2 = xt * xt + new_zy * new_zy
        escaped = r2 > 4.0
        if np.any(escaped):
            esc_idx = idx[escaped]
            escaped_it[esc_idx] = i + 1
            alive[esc_idx] = False

    # Smooth escape time
    mu = np.full(n, float(iters), dtype=np.float64)
    mask_escaped = escaped_it < iters
    if np.any(mask_escaped):
        zx_e = zx[mask_escaped]
        zy_e = zy[mask_escaped]
        it_e = escaped_it[mask_escaped].astype(np.float64)
        mod = np.sqrt(zx_e * zx_e + zy_e * zy_e)
        smooth = it_e + 1.0 - (np.log(np.log(np.maximum(mod, 1e-6))) / math.log(2.0))
        mu[mask_escaped] = smooth

    t = np.clip(mu / float(iters), 0.0, 1.0).reshape((px_h, px_w)).astype(np.float32)

    # Climate fields
    E_clim = elev_ridge_belt(t, land_boost=cfg.land_boost)
    water = (E_clim < cfg.sea_level)
    ocean_mask, lake_mask = ocean_lake_masks_from_water(water, t=t)
    ocean_dist = manhattan_distance_to(ocean_mask)
    lake_dist = manhattan_distance_to(lake_mask)
    water_dist = np.minimum(ocean_dist, lake_dist)

    # Latitude
    y_mid = 0.5 * (j_min_y + j_max_y)
    y_half = 0.5 * (j_max_y - j_min_y) + 1e-6
    lat_1d = (jy_line.astype(np.float32) - y_mid) / y_half
    lat_1d = np.clip(lat_1d, -1.0, 1.0)
    lat = np.repeat(lat_1d[:, None], px_w, axis=1).astype(np.float32)

    # Temperature, wind, moisture
    T = compute_temperature(E_clim, ocean_dist, lat, cfg)
    U, V = compute_wind_field(E_clim, lat, T, cfg)
    M = compute_moisture(E_clim, ocean_dist, lake_dist, U, V, T, cfg)

    # Biome classification
    biome = classify_biome(E_clim, T, M, ocean_mask, lake_mask, corruption_env=None)

    # Normalize E_clim to 0-1 for suitability checks
    E_norm = np.clip((E_clim - E_clim.min()) / (E_clim.max() - E_clim.min() + 1e-9), 0.0, 1.0)

    # Corruption estimate (simplified - just use x-position as proxy for now)
    # In a full implementation, we'd sample the corruption field
    corruption = np.zeros_like(E_clim, dtype=np.float32)
    if corr_level > 0:
        # Corruption increases toward right side of map
        x_frac = np.linspace(0.0, 1.0, px_w, dtype=np.float32)
        corruption = np.tile(x_frac, (px_h, 1)) * float(corr_level) * 0.3

    return {
        "biome": biome,
        "E_clim": E_clim,
        "E_norm": E_norm,
        "T": T,
        "M": M,
        "water": water,
        "water_dist": water_dist,
        "corruption": corruption,
        "resolution": resolution,
        "px_w": px_w,
        "px_h": px_h,
        "total_x": total_x,
        "total_y": total_y,
    }


def compute_suitability(
    site_cfg: SiteTypeConfig,
    climate: Dict[str, np.ndarray],
) -> np.ndarray:
    """
    Compute suitability map for a site type.

    Returns a 2D array of suitability values in [0, 1].
    """
    biome = climate.get("biome")
    E_norm = climate.get("E_norm")
    T = climate.get("T")
    M = climate.get("M")
    water_dist = climate.get("water_dist")
    corruption = climate.get("corruption")

    if biome is None or E_norm is None:
        return np.zeros((1, 1), dtype=np.float32)

    h, w = biome.shape
    suit = np.ones((h, w), dtype=np.float32)

    # Biome affinity (primary = 1.0, secondary = 0.6, other = 0.0)
    biome_suit = np.zeros_like(suit)
    for b in site_cfg.primary_biomes:
        biome_suit[biome == int(b)] = 1.0
    for b in site_cfg.secondary_biomes:
        mask = (biome == int(b)) & (biome_suit < 0.6)
        biome_suit[mask] = 0.6
    suit *= biome_suit

    # Elevation thresholds
    if T is not None:
        elev_ok = (E_norm >= site_cfg.elevation_min) & (E_norm <= site_cfg.elevation_max)
        suit[~elev_ok] *= 0.1

    # Temperature thresholds
    if T is not None:
        temp_ok = (T >= site_cfg.temperature_min) & (T <= site_cfg.temperature_max)
        suit[~temp_ok] *= 0.2

    # Moisture thresholds
    if M is not None:
        moist_ok = (M >= site_cfg.moisture_min) & (M <= site_cfg.moisture_max)
        suit[~moist_ok] *= 0.2

    # Corruption threshold
    if corruption is not None:
        # Special case for corruption outposts: they WANT high corruption
        if site_cfg.kind == "corruption_outpost":
            # Require minimum corruption of 0.1
            suit[corruption < 0.1] *= 0.1
        else:
            corr_ok = corruption <= site_cfg.corruption_max
            suit[~corr_ok] *= 0.1

    # Water proximity requirement
    if site_cfg.water_proximity is not None and water_dist is not None:
        resolution = climate.get("resolution", 1)
        max_dist = site_cfg.water_proximity * resolution
        near_water = water_dist <= max_dist
        suit[~near_water] *= 0.05

    # Don't place in water
    water = climate.get("water")
    if water is not None:
        suit[water] = 0.0

    # Apply rarity multiplier
    suit *= site_cfg.rarity

    return suit


def _derive_seed(world_seed: int, kind: str, coord: Tuple[int, int, int]) -> int:
    """Derive a deterministic seed for a site from world seed, kind, and coord."""
    data = f"{world_seed}:{kind}:{coord[0]}:{coord[1]}:{coord[2]}"
    digest = hashlib.md5(data.encode()).hexdigest()[:8]
    return int(digest, 16)


def place_sites_for_type(
    game: "Game",
    site_cfg: SiteTypeConfig,
    climate: Dict[str, np.ndarray],
    existing_coords: Set[Tuple[int, int]],
    rng: np.random.Generator,
) -> List[SiteSpec]:
    """
    Place sites of a given type using weighted sampling with spacing.

    Args:
        game: Game instance
        site_cfg: Configuration for this site type
        climate: Pre-computed climate field arrays
        existing_coords: Set of (zx, zy) already occupied by sites
        rng: Random number generator

    Returns:
        List of placed SiteSpec objects
    """
    debug = getattr(game, "_debug", None)
    suit = compute_suitability(site_cfg, climate)

    # Convert suitability to world coordinates
    resolution = climate.get("resolution", 1)
    total_x = climate.get("total_x", 1)  # Total tiles in world (e.g., 6000)
    total_y = climate.get("total_y", 1)  # Total tiles in world (e.g., 4000)
    px_w = climate.get("px_w", 1)
    px_h = climate.get("px_h", 1)

    # Get zone dimensions to convert tile coords to zone coords
    cfg = getattr(game, "cfg", None)
    zone_w = int(getattr(cfg, "world_width", 60) or 60) if cfg else 60
    zone_h = int(getattr(cfg, "world_height", 40) or 40) if cfg else 40
    # Number of zones in each direction
    num_zones_x = max(1, total_x // zone_w)
    num_zones_y = max(1, total_y // zone_h)

    # Get biome array for recording which biome each site is in
    biome_arr = climate.get("biome")

    world_seed = getattr(game, "seed", 1337)

    placed: List[SiteSpec] = []
    placed_coords: Set[Tuple[int, int]] = set(existing_coords)

    # Determine how many sites to attempt
    max_count = site_cfg.max_count or 20
    attempts = max_count * 10

    # Flatten suitability for weighted sampling
    suit_flat = suit.flatten()
    total_suit = suit_flat.sum()
    if total_suit < 0.01:
        if debug:
            debug(f"[site_placement] {site_cfg.kind}: no suitable locations (total_suit={total_suit:.4f})")
        return []

    probs = suit_flat / total_suit

    for _ in range(attempts):
        if len(placed) >= max_count:
            break

        # Weighted random sample
        idx = rng.choice(len(probs), p=probs)
        py = idx // px_w
        px = idx % px_w

        # Convert pixel position to tile position, then to zone coordinates
        tile_x = int(px * resolution)
        tile_y = int(py * resolution)
        zx = tile_x // zone_w
        zy = tile_y // zone_h

        # Clamp to zone bounds
        zx = min(zx, num_zones_x - 1)
        zy = min(zy, num_zones_y - 1)

        coord_2d = (zx, zy)
        coord_3d = (zx, zy, 0)  # Surface level

        # Check spacing from same-type sites
        too_close = False
        for existing in placed:
            dx = abs(existing.coord[0] - zx)
            dy = abs(existing.coord[1] - zy)
            if dx < site_cfg.spacing and dy < site_cfg.spacing:
                too_close = True
                break

        if too_close:
            continue

        # Check not already occupied
        if coord_2d in placed_coords:
            continue

        # Get biome at this location
        if biome_arr is not None:
            biome_val = Biome(int(biome_arr[py, px]))
        else:
            biome_val = Biome.TEMPERATE_GRASSLAND

        # Create the site
        site_id = f"{site_cfg.kind}_{zx}_{zy}"
        seed = _derive_seed(world_seed, site_cfg.kind, coord_3d)

        spec = SiteSpec(
            id=site_id,
            kind=site_cfg.kind,
            coord=coord_3d,
            seed=seed,
            biome=biome_val,
            tags={
                "name": site_cfg.name,
                "structures": list(site_cfg.structures),
                "npc_pool": list(site_cfg.npc_pool),
                "enemy_pool": list(site_cfg.enemy_pool),
            },
            visibility=SiteVisibility.HIDDEN,
        )

        placed.append(spec)
        placed_coords.add(coord_2d)

    return placed


def _place_all_sites_sync(game: "Game", registry: SiteRegistry) -> None:
    """
    Place all site types across the world (synchronous implementation).

    This populates the given registry with sites based on climate suitability.
    """
    debug = getattr(game, "_debug", None)

    # Sample climate at reduced resolution for efficiency
    # Use resolution=8 for faster startup (coarser sampling is fine for site placement)
    climate = _sample_climate_grid(game, resolution=8)
    if not climate:
        if debug:
            debug("[site_placement] No climate data available")
        return

    # Load site type definitions
    site_types = load_site_types()
    if not site_types:
        if debug:
            debug("[site_placement] No site types loaded")
        return

    if debug:
        debug(f"[site_placement] Loaded {len(site_types)} site types, climate grid: {climate.get('px_w')}x{climate.get('px_h')}")
        # Log biome distribution
        biome_arr = climate.get("biome")
        if biome_arr is not None:
            unique, counts = np.unique(biome_arr, return_counts=True)
            biome_names = [Biome(int(b)).name for b in unique]
            debug(f"[site_placement] Biome distribution: {dict(zip(biome_names, counts.tolist()))}")

    # Use world seed for reproducibility
    world_seed = getattr(game, "seed", 1337)
    rng = np.random.default_rng(world_seed)

    # Track all placed coordinates to avoid overlap
    existing_coords: Set[Tuple[int, int]] = set()

    # Place each site type
    total_placed = 0
    for kind, site_cfg in site_types.items():
        sites = place_sites_for_type(game, site_cfg, climate, existing_coords, rng)
        for spec in sites:
            registry.add(spec)
            existing_coords.add(spec.zone_coord)
        total_placed += len(sites)
        if debug and len(sites) > 0:
            debug(f"[site_placement] Placed {len(sites)} {kind}")

    if debug:
        debug(f"[site_placement] Total sites placed: {total_placed}")

    # Mark placement as complete
    game.site_placement_complete = True


def place_all_sites(game: "Game") -> SiteRegistry:
    """
    Place all site types across the world.

    This should be called during world initialization, after
    overmap_params and tile_julia_grid are set up.

    Returns an empty SiteRegistry immediately, with sites populated
    asynchronously in a background thread.
    """
    registry = SiteRegistry()

    # Mark placement as in-progress
    game.site_placement_complete = False

    # Start background thread for site placement
    import threading

    def _background_place():
        try:
            _place_all_sites_sync(game, registry)
        except Exception as e:
            # Log error but don't crash
            if hasattr(game, "_debug"):
                game._debug(f"[site_placement] Background error: {e!r}")
            game.site_placement_complete = True  # Mark complete even on error

    thread = threading.Thread(target=_background_place, daemon=True)
    thread.start()

    return registry


def get_site_at_zone(game: "Game", zx: int, zy: int, depth: int = 0) -> Optional[SiteSpec]:
    """Get the site at a specific zone, if any."""
    registry = getattr(game, "site_registry", None)
    if registry is None:
        return None
    return registry.get_at((zx, zy, depth))


def discover_site_at_zone(game: "Game", zx: int, zy: int, depth: int = 0) -> Optional[SiteSpec]:
    """Mark any site at this zone as discovered. Returns the site if found."""
    registry = getattr(game, "site_registry", None)
    if registry is None:
        return None
    return registry.discover_at_coord((zx, zy, depth))
