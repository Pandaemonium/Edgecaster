from __future__ import annotations

from typing import Iterable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.climate import ClimateConfig


def render_overmap_buffers_numpy(
    *,
    px_w: int,
    px_h: int,
    total_w: int,
    total_h: int,
    j_min_x: float,
    j_max_x: float,
    j_min_y: float,
    j_max_y: float,
    view_min_wx: float = 0.0,
    view_min_wy: float = 0.0,
    view_span_wx: Optional[float] = None,
    view_span_wy: Optional[float] = None,
    visual_c: complex,
    iters: int,
    corruption_level: float,
    corruption_seed: int,
    spline_weight: float = 0.0,
    hotspots: Optional[Iterable[tuple[float, float, float, float]]] = None,
    anchors: Optional[Iterable[tuple[float, float, float, float]]] = None,
) -> tuple["object", "object", "object"]:
    """
    Fast numpy-based overmap renderer.

    Returns (rgb_main, rgb_corr, peak_env) as numpy arrays:
      - rgb_main: (px_h, px_w, 3) uint8
      - rgb_corr: (px_h, px_w, 3) uint8
      - peak_env: (px_h, px_w) float32

    This avoids per-pixel Python loops by vectorizing the Julia iteration across the
    entire low-res overmap grid.

    Corruption logic is delegated to `edgecaster.corruption.distortion_np` so the
    numpy fast-path cannot drift from the scalar corruption rules used elsewhere.
    """
    import math

    import numpy as np

    from edgecaster import corruption as corr

    px_w = int(px_w)
    px_h = int(px_h)
    if px_w <= 0 or px_h <= 0:
        raise ValueError("px_w/px_h must be > 0")

    total_w = int(total_w)
    total_h = int(total_h)
    if total_w <= 1 or total_h <= 1:
        raise ValueError("total_w/total_h must be > 1")

    # World coordinate domain is tile-index space [0 .. total_w-1] / [0 .. total_h-1].
    world_max_wx = float(max(1, total_w - 1))
    world_max_wy = float(max(1, total_h - 1))

    iters = int(iters)
    if iters <= 0:
        iters = 1

    corr_level = float(corruption_level or 0.0)
    corr_seed = int(corruption_seed)
    hot_list = list(hotspots or [])
    anchor_list = list(anchors or [])

    corr_params = corr.CorruptionParams(
        seed=corr_seed,
        hotspots=hot_list,
        anchors=anchor_list,
        spline_weight=float(spline_weight or 0.0),
    )

    # -------------------------------------------------------------------------
    # Pixel -> world coord -> Julia coord mapping (preserves overmap/local tie).
    #
    # This uses continuous world coordinates, so zooming can reveal sub-tile
    # fractal detail instead of just scaling up pre-rendered pixels.
    # -------------------------------------------------------------------------
    view_min_wx = float(view_min_wx)
    view_min_wy = float(view_min_wy)
    span_wx = float(view_span_wx) if view_span_wx is not None else float(world_max_wx)
    span_wy = float(view_span_wy) if view_span_wy is not None else float(world_max_wy)
    if span_wx <= 0.0 or span_wy <= 0.0:
        raise ValueError("view_span_wx/view_span_wy must be > 0")

    span_jx = float(j_max_x) - float(j_min_x)
    span_jy = float(j_max_y) - float(j_min_y)
    step_x = span_jx / world_max_wx
    step_y = span_jy / world_max_wy

    # Use inclusive linspace so the edges are stable at any zoom.
    wx_line = view_min_wx + np.linspace(0.0, span_wx, px_w, dtype=np.float64)
    wy_line = view_min_wy + np.linspace(0.0, span_wy, px_h, dtype=np.float64)
    wx_line = np.clip(wx_line, 0.0, world_max_wx)
    wy_line = np.clip(wy_line, 0.0, world_max_wy)

    jx_line = float(j_min_x) + wx_line * step_x
    jy_line = float(j_min_y) + wy_line * step_y

    # Broadcast into 2D grids.
    zx0 = np.tile(jx_line, (px_h, 1))
    zy0 = np.tile(jy_line.reshape(-1, 1), (1, px_w))

    # Flattened working buffers (faster to index with alive indices).
    zx = zx0.reshape(-1).astype(np.float64, copy=True)
    zy = zy0.reshape(-1).astype(np.float64, copy=True)
    n = zx.size
    alive = np.ones(n, dtype=np.bool_)
    escaped_it = np.full(n, iters, dtype=np.int32)
    peak_env = np.zeros(n, dtype=np.float32)

    # -------------------------------------------------------------------------
    # Corruption LUTs (sampled by nearest-neighbor in bounded z domain).
    # -------------------------------------------------------------------------
    lut_res = int(getattr(corr, "_LUT_RES", 256))

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

    if hot_list:
        hot_arr, hot_gx_arr, hot_gy_arr = corr._hot_lut(  # type: ignore[attr-defined]
            corr._hotspots_cache_key(hot_list),  # type: ignore[attr-defined]
            int(lut_res),
        )
        hot_tex = np.frombuffer(hot_arr, dtype=np.float32)
        hot_gx_tex = np.frombuffer(hot_gx_arr, dtype=np.float32)
        hot_gy_tex = np.frombuffer(hot_gy_arr, dtype=np.float32)
    else:
        hot_tex = None
        hot_gx_tex = None
        hot_gy_tex = None

    if anchor_list:
        anchor_arr = corr._anchor_lut(  # type: ignore[attr-defined]
            corr._anchors_cache_key(anchor_list),  # type: ignore[attr-defined]
            int(lut_res),
        )
        anchor_tex = np.frombuffer(anchor_arr, dtype=np.float32)
    else:
        anchor_tex = None

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
    else:
        spline_x_tex = None
        spline_y_tex = None

    def distortion_np(zx_a: "np.ndarray", zy_a: "np.ndarray") -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
        # Delegate to the canonical implementation so we never drift from
        # the scalar corruption rules (used by local mapgen and the python renderer fallback).
        return corr.distortion_np(  # type: ignore[attr-defined]
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

    # -------------------------------------------------------------------------
    # Julia iteration (vectorized over alive points).
    # -------------------------------------------------------------------------
    c_real = float(visual_c.real)
    c_imag = float(visual_c.imag)
    for i in range(iters):
        idx = np.nonzero(alive)[0]
        if idx.size == 0:
            break

        zx_a = zx[idx]
        zy_a = zy[idx]

        dx_a, dy_a, env_a = distortion_np(zx_a, zy_a)
        peak_env[idx] = np.maximum(peak_env[idx], env_a.astype(np.float32, copy=False))

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

    # Height output.
    h = np.zeros(n, dtype=np.float64)
    mask_escaped = escaped_it < iters
    if np.any(mask_escaped):
        zx_e = zx[mask_escaped]
        zy_e = zy[mask_escaped]
        it_e = escaped_it[mask_escaped].astype(np.float64)
        mod = np.sqrt(zx_e * zx_e + zy_e * zy_e)
        smooth = it_e + 1.0 - (np.log(np.log(np.maximum(mod, 1e-6))) / math.log(2.0))
        h[mask_escaped] = np.clip(smooth / float(iters), 0.0, 1.0)

    h2 = h.reshape((px_h, px_w))
    peak2 = peak_env.reshape((px_h, px_w))

    # Smooth palette mapping (matches mapgen._color_from_fields anchors).
    anchors_h = np.asarray([0.00, 0.15, 0.32, 0.52, 0.72, 1.00], dtype=np.float64)
    anchors_rgb = np.asarray(
        [
            (50, 90, 170),    # deep water
            (110, 160, 190),  # shore/shallows
            (150, 200, 140),  # plains
            (90, 170, 110),   # forested low hills
            (170, 150, 110),  # hills
            (210, 210, 215),  # high/mountains
        ],
        dtype=np.float64,
    )
    r = np.interp(h2, anchors_h, anchors_rgb[:, 0]).astype(np.float64)
    g = np.interp(h2, anchors_h, anchors_rgb[:, 1]).astype(np.float64)
    b = np.interp(h2, anchors_h, anchors_rgb[:, 2]).astype(np.float64)
    rgb_main = np.stack([r, g, b], axis=2)

    # Simple hillshading from the height gradient to give a higher-res/relief look.
    gx = np.zeros_like(h2, dtype=np.float64)
    gy = np.zeros_like(h2, dtype=np.float64)
    gx[:, 1:-1] = (h2[:, 2:] - h2[:, :-2]) * 0.5
    gx[:, 0] = h2[:, 1] - h2[:, 0]
    gx[:, -1] = h2[:, -1] - h2[:, -2]
    gy[1:-1, :] = (h2[2:, :] - h2[:-2, :]) * 0.5
    gy[0, :] = h2[1, :] - h2[0, :]
    gy[-1, :] = h2[-1, :] - h2[-2, :]

    z_scale = 3.0
    nxn = -gx * z_scale
    nyn = -gy * z_scale
    nzn = 1.0
    inv_len = 1.0 / np.sqrt(nxn * nxn + nyn * nyn + nzn * nzn)
    nxn *= inv_len
    nyn *= inv_len

    # Light from the top-left (northwest), slightly "above" the surface.
    lx, ly, lz = -0.6, -0.8, 1.0
    l_inv = 1.0 / math.sqrt(lx * lx + ly * ly + lz * lz)
    lx *= l_inv
    ly *= l_inv
    lz *= l_inv

    dot = nxn * lx + nyn * ly + (nzn * inv_len) * lz
    shade = np.clip(0.70 + 0.30 * dot, 0.0, 1.0)
    rgb_main = np.clip(rgb_main * shade[:, :, None], 0.0, 255.0).astype(np.uint8)

    # ALT corruption field visualization.
    if corr_level > 0.0:
        dx0, dy0, env0 = distortion_np(zx0.reshape(-1), zy0.reshape(-1))
        env0 = np.clip(env0, 0.0, 1.0)
        denom = float(corr_params.amp) * max(0.15, float(corr_level))
        dxn = np.clip(dx0 / max(1e-9, denom), -1.0, 1.0)
        dyn = np.clip(dy0 / max(1e-9, denom), -1.0, 1.0)

        r = (255.0 * (0.5 + 0.5 * dxn) * env0).astype(np.uint8).reshape((px_h, px_w))
        g = (255.0 * env0).astype(np.uint8).reshape((px_h, px_w))
        b = (255.0 * (0.5 + 0.5 * dyn) * env0).astype(np.uint8).reshape((px_h, px_w))
        rgb_corr = np.stack([r, g, b], axis=2)
    else:
        rgb_corr = np.zeros((px_h, px_w, 3), dtype=np.uint8)

    return rgb_main, rgb_corr, peak2


def render_overmap_buffers_climate(
    *,
    px_w: int,
    px_h: int,
    total_w: int,
    total_h: int,
    j_min_x: float,
    j_max_x: float,
    j_min_y: float,
    j_max_y: float,
    view_min_wx: float = 0.0,
    view_min_wy: float = 0.0,
    view_span_wx: Optional[float] = None,
    view_span_wy: Optional[float] = None,
    visual_c: complex,
    iters: int,
    corruption_level: float,
    corruption_seed: int,
    spline_weight: float = 0.0,
    hotspots: Optional[Iterable[tuple[float, float, float, float]]] = None,
    anchors: Optional[Iterable[tuple[float, float, float, float]]] = None,
    climate_config: Optional["ClimateConfig"] = None,
) -> tuple["object", "object", "object"]:
    """
    Climate-aware numpy-based overmap renderer.

    This is the new rendering path that uses the full climate simulation
    (temperature, moisture, wind) to classify biomes instead of just
    using height-based color interpolation.

    Returns (rgb_main, rgb_corr, peak_env) as numpy arrays.
    """
    import math

    import numpy as np

    from edgecaster import corruption as corr
    from edgecaster.climate import (
        ClimateConfig,
        elev_ridge_belt,
        ocean_lake_masks_from_water,
        manhattan_distance_to,
        compute_temperature,
        compute_wind_field,
        compute_moisture,
        classify_biome,
        biome_to_color,
        Biome,
        BIOME_COLORS,
    )

    if climate_config is None:
        climate_config = ClimateConfig()
    cfg = climate_config

    px_w = int(px_w)
    px_h = int(px_h)
    if px_w <= 0 or px_h <= 0:
        raise ValueError("px_w/px_h must be > 0")

    total_w = int(total_w)
    total_h = int(total_h)
    if total_w <= 1 or total_h <= 1:
        raise ValueError("total_w/total_h must be > 1")

    world_max_wx = float(max(1, total_w - 1))
    world_max_wy = float(max(1, total_h - 1))

    iters = int(iters)
    if iters <= 0:
        iters = 1

    corr_level = float(corruption_level or 0.0)
    corr_seed = int(corruption_seed)
    hot_list = list(hotspots or [])
    anchor_list = list(anchors or [])

    corr_params = corr.CorruptionParams(
        seed=corr_seed,
        hotspots=hot_list,
        anchors=anchor_list,
        spline_weight=float(spline_weight or 0.0),
    )

    # Pixel -> world coord -> Julia coord mapping
    view_min_wx = float(view_min_wx)
    view_min_wy = float(view_min_wy)
    span_wx = float(view_span_wx) if view_span_wx is not None else float(world_max_wx)
    span_wy = float(view_span_wy) if view_span_wy is not None else float(world_max_wy)
    if span_wx <= 0.0 or span_wy <= 0.0:
        raise ValueError("view_span_wx/view_span_wy must be > 0")

    span_jx = float(j_max_x) - float(j_min_x)
    span_jy = float(j_max_y) - float(j_min_y)
    step_x = span_jx / world_max_wx
    step_y = span_jy / world_max_wy

    wx_line = view_min_wx + np.linspace(0.0, span_wx, px_w, dtype=np.float64)
    wy_line = view_min_wy + np.linspace(0.0, span_wy, px_h, dtype=np.float64)
    wx_line = np.clip(wx_line, 0.0, world_max_wx)
    wy_line = np.clip(wy_line, 0.0, world_max_wy)

    jx_line = float(j_min_x) + wx_line * step_x
    jy_line = float(j_min_y) + wy_line * step_y

    zx0 = np.tile(jx_line, (px_h, 1))
    zy0 = np.tile(jy_line.reshape(-1, 1), (1, px_w))

    zx = zx0.reshape(-1).astype(np.float64, copy=True)
    zy = zy0.reshape(-1).astype(np.float64, copy=True)
    n = zx.size
    alive = np.ones(n, dtype=np.bool_)
    escaped_it = np.full(n, iters, dtype=np.int32)
    peak_env = np.zeros(n, dtype=np.float32)

    # Corruption LUTs
    lut_res = int(getattr(corr, "_LUT_RES", 256))

    nx_arr, ny_arr, spots_arr = corr._noise_lut(
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

    if hot_list:
        hot_arr, hot_gx_arr, hot_gy_arr = corr._hot_lut(
            corr._hotspots_cache_key(hot_list),
            int(lut_res),
        )
        hot_tex = np.frombuffer(hot_arr, dtype=np.float32)
        hot_gx_tex = np.frombuffer(hot_gx_arr, dtype=np.float32)
        hot_gy_tex = np.frombuffer(hot_gy_arr, dtype=np.float32)
    else:
        hot_tex = None
        hot_gx_tex = None
        hot_gy_tex = None

    if anchor_list:
        anchor_arr = corr._anchor_lut(
            corr._anchors_cache_key(anchor_list),
            int(lut_res),
        )
        anchor_tex = np.frombuffer(anchor_arr, dtype=np.float32)
    else:
        anchor_tex = None

    if float(getattr(corr_params, "spline_weight", 0.0) or 0.0) > 1e-9:
        sx_arr, sy_arr = corr._spline_lut(
            int(corr_params.seed),
            int(getattr(corr_params, "spline_points", 40) or 40),
            float(getattr(corr_params, "spline_strength", 0.35) or 0.35),
            int(getattr(corr_params, "spline_iters", 120) or 120),
            int(lut_res),
        )
        spline_x_tex = np.frombuffer(sx_arr, dtype=np.float32)
        spline_y_tex = np.frombuffer(sy_arr, dtype=np.float32)
    else:
        spline_x_tex = None
        spline_y_tex = None

    def distortion_np(zx_a, zy_a):
        return corr.distortion_np(
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

    # Julia iteration
    c_real = float(visual_c.real)
    c_imag = float(visual_c.imag)
    for i in range(iters):
        idx = np.nonzero(alive)[0]
        if idx.size == 0:
            break

        zx_a = zx[idx]
        zy_a = zy[idx]

        dx_a, dy_a, env_a = distortion_np(zx_a, zy_a)
        peak_env[idx] = np.maximum(peak_env[idx], env_a.astype(np.float32, copy=False))

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

    # Smooth escape time (mu) and normalized t
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
    peak2 = peak_env.reshape((px_h, px_w))

    # ----- Climate field computation -----

    # Elevation from ridge-belt transfer function
    E_clim = elev_ridge_belt(t, land_boost=cfg.land_boost)

    # Water classification
    water = (E_clim < cfg.sea_level)
    ocean_mask, lake_mask = ocean_lake_masks_from_water(water, t=t)

    # Distance fields
    ocean_dist = manhattan_distance_to(ocean_mask)
    lake_dist = manhattan_distance_to(lake_mask)

    # Latitude (from Julia y-coordinates, normalized to [-1, 1])
    y_mid = 0.5 * (float(j_min_y) + float(j_max_y))
    y_half = 0.5 * (float(j_max_y) - float(j_min_y)) + 1e-6
    lat_1d = (jy_line.astype(np.float32) - y_mid) / y_half
    lat_1d = np.clip(lat_1d, -1.0, 1.0)
    lat = np.repeat(lat_1d[:, None], px_w, axis=1).astype(np.float32)

    # Temperature
    T = compute_temperature(E_clim, ocean_dist, lat, cfg)

    # Wind
    U, V = compute_wind_field(E_clim, lat, T, cfg)

    # Moisture
    M = compute_moisture(E_clim, ocean_dist, lake_dist, U, V, T, cfg)

    # Biome classification
    corruption_env = peak2 if corr_level > 0.0 else None
    biome = classify_biome(E_clim, T, M, ocean_mask, lake_mask, corruption_env=corruption_env)

    # Convert biome to colors
    rgb_main = biome_to_color(biome).astype(np.float64)

    # Apply hillshading for relief
    gx = np.zeros_like(E_clim, dtype=np.float64)
    gy = np.zeros_like(E_clim, dtype=np.float64)
    gx[:, 1:-1] = (E_clim[:, 2:] - E_clim[:, :-2]) * 0.5
    gx[:, 0] = E_clim[:, 1] - E_clim[:, 0]
    gx[:, -1] = E_clim[:, -1] - E_clim[:, -2]
    gy[1:-1, :] = (E_clim[2:, :] - E_clim[:-2, :]) * 0.5
    gy[0, :] = E_clim[1, :] - E_clim[0, :]
    gy[-1, :] = E_clim[-1, :] - E_clim[-2, :]

    z_scale = 3.0
    nxn = -gx * z_scale
    nyn = -gy * z_scale
    nzn = 1.0
    inv_len = 1.0 / np.sqrt(nxn * nxn + nyn * nyn + nzn * nzn)
    nxn *= inv_len
    nyn *= inv_len

    lx, ly, lz = -0.6, -0.8, 1.0
    l_inv = 1.0 / math.sqrt(lx * lx + ly * ly + lz * lz)
    lx *= l_inv
    ly *= l_inv
    lz *= l_inv

    dot = nxn * lx + nyn * ly + (nzn * inv_len) * lz
    shade = np.clip(0.70 + 0.30 * dot, 0.0, 1.0)
    rgb_main = np.clip(rgb_main * shade[:, :, None], 0.0, 255.0).astype(np.uint8)

    # Corruption visualization
    if corr_level > 0.0:
        dx0, dy0, env0 = distortion_np(zx0.reshape(-1), zy0.reshape(-1))
        env0 = np.clip(env0, 0.0, 1.0)
        denom = float(corr_params.amp) * max(0.15, float(corr_level))
        dxn = np.clip(dx0 / max(1e-9, denom), -1.0, 1.0)
        dyn = np.clip(dy0 / max(1e-9, denom), -1.0, 1.0)

        r = (255.0 * (0.5 + 0.5 * dxn) * env0).astype(np.uint8).reshape((px_h, px_w))
        g = (255.0 * env0).astype(np.uint8).reshape((px_h, px_w))
        b = (255.0 * (0.5 + 0.5 * dyn) * env0).astype(np.uint8).reshape((px_h, px_w))
        rgb_corr = np.stack([r, g, b], axis=2)
    else:
        rgb_corr = np.zeros((px_h, px_w, 3), dtype=np.uint8)

    return rgb_main, rgb_corr, peak2
