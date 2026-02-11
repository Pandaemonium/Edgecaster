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

from edgecaster import prototypes
from edgecaster import spawn_factory  # <-- IMPORTANT: bind spawn_factory name here

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

# We still use these dataclasses as convenient internal placement products.
# They do NOT imply SiteRegistry is authoritative.
from edgecaster.state.sites import SiteSpec, SiteTypeConfig, SiteVisibility

if TYPE_CHECKING:
    from edgecaster.game import Game


# ------------------------------------------------------------
# Helper: Build placement configs from site_* prototypes
# ------------------------------------------------------------

def load_site_type_configs_from_prototypes() -> Dict[str, SiteTypeConfig]:
    """
    Build SiteTypeConfig objects from site_* prototypes.
    Placement knobs live in proto['tags'].
    """
    bucket = prototypes.get_master_bucket()
    out: Dict[str, SiteTypeConfig] = {}

    for pid, proto in bucket.items():
        if not pid.startswith("site_"):
            continue
        tags = (proto.get("tags") or {})
        if not tags.get("site", False):
            continue

        kind = str(tags.get("site_kind") or pid[len("site_"):])

        def _biomes(key: str) -> List[Biome]:
            raw = tags.get(key) or []
            out_b: List[Biome] = []
            for x in raw:
                try:
                    out_b.append(Biome[str(x)])
                except Exception:
                    try:
                        out_b.append(Biome(int(x)))
                    except Exception:
                        pass
            return out_b

        cfg = SiteTypeConfig(
            kind=kind,
            name=str(proto.get("name") or kind),
            primary_biomes=_biomes("primary_biomes"),
            secondary_biomes=_biomes("secondary_biomes"),
            elevation_min=float(tags.get("elevation_min", 0.0) or 0.0),
            elevation_max=float(tags.get("elevation_max", 1.0) or 1.0),
            temperature_min=float(tags.get("temperature_min", 0.0) or 0.0),
            temperature_max=float(tags.get("temperature_max", 1.0) or 1.0),
            moisture_min=float(tags.get("moisture_min", 0.0) or 0.0),
            moisture_max=float(tags.get("moisture_max", 1.0) or 1.0),
            corruption_max=float(tags.get("corruption_max", 1.0) or 1.0),
            water_proximity=(int(tags["water_proximity"]) if tags.get("water_proximity") is not None else None),
            spacing=int(tags.get("spacing", 10) or 10),
            rarity=float(tags.get("rarity", 1.0) or 1.0),
            max_count=(int(tags["max_count"]) if tags.get("max_count") is not None else None),
            structures=list(tags.get("structures") or []),
            npc_pool=list(tags.get("npc_pool") or []),
            enemy_pool=list(tags.get("enemy_pool") or []),
        )
        out[kind] = cfg

    return out


# ------------------------------------------------------------
# Climate sampling
# ------------------------------------------------------------

def _sample_climate_grid(game: "Game", resolution: int = 4) -> Dict[str, np.ndarray]:
    params = getattr(game, "overmap_params", None)
    grid = getattr(game, "tile_julia_grid", None)
    if not params or not grid:
        return {}

    cfg = getattr(game, "climate_config", None) or ClimateConfig()

    total_x = grid["total_x"]
    total_y = grid["total_y"]

    px_w = max(1, total_x // resolution)
    px_h = max(1, total_y // resolution)

    j_min_x = grid["view_min_jx"]
    j_max_x = grid["view_max_jx"]
    j_min_y = grid["view_min_jy"]
    j_max_y = grid["view_max_jy"]

    jx_line = np.linspace(j_min_x, j_max_x, px_w, dtype=np.float64)
    jy_line = np.linspace(j_min_y, j_max_y, px_h, dtype=np.float64)

    zx0 = np.tile(jx_line, (px_h, 1))
    zy0 = np.tile(jy_line.reshape(-1, 1), (1, px_w))

    zx = zx0.reshape(-1).astype(np.float64, copy=True)
    zy = zy0.reshape(-1).astype(np.float64, copy=True)
    n = zx.size

    visual_c = params.get("visual_c", complex(-0.4, 0.6))
    c_real = float(visual_c.real)
    c_imag = float(visual_c.imag)
    iters = 64

    alive = np.ones(n, dtype=np.bool_)
    escaped_it = np.full(n, iters, dtype=np.int32)

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

    E_clim = elev_ridge_belt(t, land_boost=cfg.land_boost)
    water = (E_clim < cfg.sea_level)
    ocean_mask, lake_mask = ocean_lake_masks_from_water(water, t=t)
    ocean_dist = manhattan_distance_to(ocean_mask)
    lake_dist = manhattan_distance_to(lake_mask)
    water_dist = np.minimum(ocean_dist, lake_dist)

    y_mid = 0.5 * (j_min_y + j_max_y)
    y_half = 0.5 * (j_max_y - j_min_y) + 1e-6
    lat_1d = (jy_line.astype(np.float32) - y_mid) / y_half
    lat_1d = np.clip(lat_1d, -1.0, 1.0)
    lat = np.repeat(lat_1d[:, None], px_w, axis=1).astype(np.float32)

    T = compute_temperature(E_clim, ocean_dist, lat, cfg)
    U, V = compute_wind_field(E_clim, lat, T, cfg)
    M = compute_moisture(E_clim, ocean_dist, lake_dist, U, V, T, cfg)

    biome = classify_biome(E_clim, T, M, ocean_mask, lake_mask, corruption_env=None)

    E_norm = np.clip((E_clim - E_clim.min()) / (E_clim.max() - E_clim.min() + 1e-9), 0.0, 1.0)

    corruption = np.zeros_like(E_clim, dtype=np.float32)
    if corr_level > 0:
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


def compute_suitability(site_cfg: SiteTypeConfig, climate: Dict[str, np.ndarray]) -> np.ndarray:
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

    biome_suit = np.zeros_like(suit)
    for b in site_cfg.primary_biomes:
        biome_suit[biome == int(b)] = 1.0
    for b in site_cfg.secondary_biomes:
        mask = (biome == int(b)) & (biome_suit < 0.6)
        biome_suit[mask] = 0.6
    suit *= biome_suit

    if T is not None:
        elev_ok = (E_norm >= site_cfg.elevation_min) & (E_norm <= site_cfg.elevation_max)
        suit[~elev_ok] *= 0.1

    if T is not None:
        temp_ok = (T >= site_cfg.temperature_min) & (T <= site_cfg.temperature_max)
        suit[~temp_ok] *= 0.2

    if M is not None:
        moist_ok = (M >= site_cfg.moisture_min) & (M <= site_cfg.moisture_max)
        suit[~moist_ok] *= 0.2

    if corruption is not None:
        if site_cfg.kind == "corruption_outpost":
            suit[corruption < 0.1] *= 0.1
        else:
            corr_ok = corruption <= site_cfg.corruption_max
            suit[~corr_ok] *= 0.1

    if site_cfg.water_proximity is not None and water_dist is not None:
        resolution = climate.get("resolution", 1)
        max_dist = site_cfg.water_proximity * resolution
        near_water = water_dist <= max_dist
        suit[~near_water] *= 0.05

    water = climate.get("water")
    if water is not None:
        suit[water] = 0.0

    suit *= site_cfg.rarity
    return suit


def _derive_seed(world_seed: int, kind: str, coord: Tuple[int, int, int]) -> int:
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
    debug = getattr(game, "_debug", None)

    # If the world_entity_index is not ready yet, defer placement.
    # Game.__init__ creates the index later; the attention loop will call ensure_world_sites().
    if getattr(game, "world_entity_index", None) is None:
        setattr(game, "_sites_need_world_index", True)
        return []


    # Ensure the WorldEntityIndex exists. Game.__init__ may call this before attention.py
    # has created the index, so we defensively create it here (idempotent).
    if getattr(game, "world_entity_index", None) is None:
        try:
            from edgecaster.systems.world_entity_index import WorldEntityIndex
            cfg0 = getattr(game, "cfg", None)
            zw = int(getattr(cfg0, "world_width", 60) or 60) if cfg0 else 60
            zh = int(getattr(cfg0, "world_height", 40) or 40) if cfg0 else 40
            game.world_entity_index = WorldEntityIndex(zone_w=zw, zone_h=zh)
            # Track dims like other systems do (helps with later rebuild logic).
            game._world_entity_index_wh = (zw, zh)
            if debug:
                debug(f"[site_placement] Created WorldEntityIndex early (zone_w={zw}, zone_h={zh})")
        except Exception:
            if debug:
                debug("[site_placement] Failed to create WorldEntityIndex; aborting site placement")
            return
    suit = compute_suitability(site_cfg, climate)

    resolution = climate.get("resolution", 1)
    total_x = climate.get("total_x", 1)
    total_y = climate.get("total_y", 1)
    px_w = climate.get("px_w", 1)
    px_h = climate.get("px_h", 1)

    cfg = getattr(game, "cfg", None)
    zone_w = int(getattr(cfg, "world_width", 60) or 60) if cfg else 60
    zone_h = int(getattr(cfg, "world_height", 40) or 40) if cfg else 40
    num_zones_x = max(1, total_x // zone_w)
    num_zones_y = max(1, total_y // zone_h)

    biome_arr = climate.get("biome")
    world_seed = getattr(game, "seed", 1337)

    placed: List[SiteSpec] = []
    placed_coords: Set[Tuple[int, int]] = set(existing_coords)

    max_count = site_cfg.max_count or 20
    attempts = max_count * 10

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

        idx = rng.choice(len(probs), p=probs)
        py = idx // px_w
        px = idx % px_w

        tile_x = int(px * resolution)
        tile_y = int(py * resolution)
        zx = tile_x // zone_w
        zy = tile_y // zone_h

        zx = min(zx, num_zones_x - 1)
        zy = min(zy, num_zones_y - 1)

        coord_2d = (zx, zy)
        coord_3d = (zx, zy, 0)

        too_close = False
        for existing in placed:
            dx = abs(existing.coord[0] - zx)
            dy = abs(existing.coord[1] - zy)
            if dx < site_cfg.spacing and dy < site_cfg.spacing:
                too_close = True
                break
        if too_close:
            continue

        if coord_2d in placed_coords:
            continue

        if biome_arr is not None:
            biome_val = Biome(int(biome_arr[py, px]))
        else:
            biome_val = Biome.TEMPERATE_GRASSLAND

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


def place_all_sites(game: "Game") -> None:
    """
    Place all site_* entity prototypes directly into WorldEntityIndex.
    SiteRegistry is no longer the authoritative truth.
    """
    debug = getattr(game, "_debug", None)

    climate = _sample_climate_grid(game, resolution=8)
    if not climate:
        if debug:
            debug("[site_placement] No climate data available")
        return

    site_types = load_site_type_configs_from_prototypes()
    if not site_types:
        if debug:
            debug("[site_placement] No site types loaded from prototypes")
        return

    world_seed = getattr(game, "seed", 1337)
    rng = np.random.default_rng(world_seed)

    cfg = getattr(game, "cfg", None)
    zone_w = int(getattr(cfg, "world_width", 60) or 60) if cfg else 60
    zone_h = int(getattr(cfg, "world_height", 40) or 40) if cfg else 40

    total_x = int(climate.get("total_x", 1))
    total_y = int(climate.get("total_y", 1))
    num_zones_x = max(1, total_x // zone_w)
    num_zones_y = max(1, total_y // zone_h)

    existing_coords: Set[Tuple[int, int]] = set()
    total_placed = 0

    for kind, site_cfg in site_types.items():
        specs = place_sites_for_type(game, site_cfg, climate, existing_coords, rng)

        for spec in specs:
            zx, zy, zz = map(int, spec.coord)
            ox = zone_w // 2
            oy = zone_h // 2

            proto_id = f"site_{spec.kind}"
            site_proto = prototypes.resolve_proto(proto_id)
            if not site_proto:
                continue

            eid = f"site:{spec.id}"

            ent = spawn_factory.build_entity_from_spec(
                spec=site_proto,
                eid=eid,
                pos=(ox, oy),
                overrides={
                    "kind": "feature",
                    "base_size": 64,
                    "tags": {
                        "world_entity": True,
                        "site": True,
                        "site_id": spec.id,
                        "site_kind": spec.kind,
                        "site_seed": int(spec.seed or 0),
                        "site_biome": str(getattr(spec.biome, "name", str(spec.biome))),
                        **(spec.tags or {}),
                    },
                },
            )

            game.world_entity_index.add(
                ent,
                zone_coord=(zx, zy, zz),
                local_pos=(ox, oy),
            )

            existing_coords.add((zx, zy))
            total_placed += 1

        if debug and specs:
            debug(f"[site_placement] Placed {len(specs)} {kind}")

    if debug:
        debug(f"[site_placement] Total sites placed: {total_placed}")

    # record outcome so ensure_world_sites can decide if it should retry
    game._site_placement_total = int(total_placed)

    # Only mark complete if we actually placed something
    game.site_placement_complete = (total_placed > 0)



def ensure_world_sites(game: "Game") -> None:
    """Ensure world-map site entities are present in game.world_entity_index.

    This is an idempotent bridge for init order:
    - Game.__init__ currently calls place_all_sites(self) before creating world_entity_index.
    - We therefore defer placement until the first attention pass, when the index exists.

    This function is safe to call every frame; it will only place once per index lifecycle.
    """
    try:
        if getattr(game, "world_entity_index", None) is None:
            return

        # If we've already placed for this specific index instance, we're done.
        idx_id = id(getattr(game, "world_entity_index"))
        if getattr(game, "_sites_placed_for_index_id", None) == idx_id:
            return

        # Only place if requested (or if nothing placed yet).
        need = bool(getattr(game, "_sites_need_world_index", True))
        if not need:
            setattr(game, "_sites_placed_for_index_id", idx_id)
            return

        # Try placing. Do NOT clear need/mark-done until we confirm success.
        place_all_sites(game)

        placed_total = int(getattr(game, "_site_placement_total", 0) or 0)
        complete = bool(getattr(game, "site_placement_complete", False))

        if complete and placed_total > 0:
            setattr(game, "_sites_need_world_index", False)
            setattr(game, "_sites_placed_for_index_id", idx_id)
            dbg = getattr(game, "_debug", None)
            if dbg:
                dbg(f"[site_placement] ensure_world_sites: placed {placed_total} sites into WorldEntityIndex")
        else:
            # keep it armed to retry later (e.g. climate not ready yet)
            setattr(game, "_sites_need_world_index", True)


    except Exception as e:
        # If anything goes wrong, allow a retry later but keep the game running.
        setattr(game, "_sites_need_world_index", True)
        dbg = getattr(game, "_debug", None)
        if dbg:
            dbg(f"[site_placement] ensure_world_sites error: {e!r}")
