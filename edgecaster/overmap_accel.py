from __future__ import annotations

from typing import Iterable, Optional


def render_overmap_buffers_numpy(
    *,
    px_w: int,
    px_h: int,
    wx_map: list[int],
    wy_map: list[int],
    xgrid: Optional[list[float]],
    ygrid: Optional[list[float]],
    total_w: int,
    total_h: int,
    j_min_x: float,
    j_max_x: float,
    j_min_y: float,
    j_max_y: float,
    visual_c: complex,
    iters: int,
    corruption_level: float,
    corruption_seed: int,
    hotspots: Optional[Iterable[tuple[float, float, float, float]]] = None,
) -> tuple["object", "object", "object"]:
    """
    Fast numpy-based overmap renderer.

    Returns (rgb_main, rgb_corr, peak_env) as numpy arrays:
      - rgb_main: (px_h, px_w, 3) uint8
      - rgb_corr: (px_h, px_w, 3) uint8
      - peak_env: (px_h, px_w) float32

    This avoids per-pixel Python loops by vectorizing the Julia iteration across the
    entire low-res overmap grid.
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

    iters = int(iters)
    if iters <= 0:
        iters = 1

    corr_level = float(corruption_level or 0.0)
    corr_seed = int(corruption_seed)
    hot_list = list(hotspots or [])

    corr_params = corr.CorruptionParams(seed=corr_seed, hotspots=hot_list)

    # -------------------------------------------------------------------------
    # Pixel -> world tile -> Julia coord mapping (preserves overmap/local tie).
    # -------------------------------------------------------------------------
    if xgrid is not None:
        jx_line = np.asarray([float(xgrid[int(wx)]) for wx in wx_map], dtype=np.float64)
    else:
        span_jx = float(j_max_x) - float(j_min_x)
        denom = float(max(1, total_w - 1))
        jx_line = np.asarray([float(j_min_x) + (float(wx) / denom) * span_jx for wx in wx_map], dtype=np.float64)

    if ygrid is not None:
        jy_line = np.asarray([float(ygrid[int(wy)]) for wy in wy_map], dtype=np.float64)
    else:
        span_jy = float(j_max_y) - float(j_min_y)
        denom = float(max(1, total_h - 1))
        jy_line = np.asarray([float(j_min_y) + (float(wy) / denom) * span_jy for wy in wy_map], dtype=np.float64)

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
    lut_min_z = float(getattr(corr, "_LUT_MIN_Z", -2.0))
    lut_inv_span = float(getattr(corr, "_LUT_INV_SPAN_Z", 1.0 / 4.0))
    lut_scale = float(lut_res - 1)

    nx_arr, ny_arr, spots_arr = corr._noise_lut(  # type: ignore[attr-defined]
        int(corr_params.seed),
        float(corr_params.freq),
        float(corr_params.spot_freq),
        float(corr_params.spot_threshold),
        float(corr_params.spot_weight),
        float(corr_params.right_start),
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

    def smoothstep(edge0: float, edge1: float, x: "np.ndarray") -> "np.ndarray":
        denom = float(edge1) - float(edge0)
        if abs(denom) < 1e-12:
            return np.zeros_like(x, dtype=np.float64)
        t = (x - float(edge0)) / denom
        t = np.clip(t, 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    def sample_nn_flat(tex: "np.ndarray", zx_a: "np.ndarray", zy_a: "np.ndarray") -> "np.ndarray":
        ix = np.rint(((zx_a - lut_min_z) * lut_inv_span) * lut_scale).astype(np.int32)
        iy = np.rint(((zy_a - lut_min_z) * lut_inv_span) * lut_scale).astype(np.int32)
        ix = np.clip(ix, 0, lut_res - 1)
        iy = np.clip(iy, 0, lut_res - 1)
        return tex[iy * lut_res + ix]

    def distortion_np(zx_a: "np.ndarray", zy_a: "np.ndarray") -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
        """Vectorized analogue of edgecaster.corruption.distortion_dz (for arrays)."""
        if corr_level <= 0.0:
            z = np.zeros_like(zx_a, dtype=np.float64)
            return z, z, z

        nx = sample_nn_flat(nx_tex, zx_a, zy_a).astype(np.float64, copy=False)
        ny = sample_nn_flat(ny_tex, zx_a, zy_a).astype(np.float64, copy=False)
        spots = sample_nn_flat(spots_tex, zx_a, zy_a).astype(np.float64, copy=False)

        hot = np.zeros_like(zx_a, dtype=np.float64)
        hot_dir_x = np.zeros_like(zx_a, dtype=np.float64)
        hot_dir_y = np.zeros_like(zx_a, dtype=np.float64)
        if hot_tex is not None and hot_gx_tex is not None and hot_gy_tex is not None:
            hot = sample_nn_flat(hot_tex, zx_a, zy_a).astype(np.float64, copy=False)
            if np.any(hot > 0.0):
                gx = sample_nn_flat(hot_gx_tex, zx_a, zy_a).astype(np.float64, copy=False)
                gy = sample_nn_flat(hot_gy_tex, zx_a, zy_a).astype(np.float64, copy=False)
                mag = np.sqrt(gx * gx + gy * gy)
                ok = mag > 1e-9
                hot_dir_x = np.where(ok, gx / mag, 0.0)
                hot_dir_y = np.where(ok, gy / mag, 0.0)

        span = float(j_max_x) - float(j_min_x)
        if abs(span) < 1e-12:
            x_norm = np.zeros_like(zx_a, dtype=np.float64)
        else:
            x_norm = (zx_a - float(j_min_x)) / span

        right = smoothstep(float(corr_params.right_start), 1.0, x_norm)
        if float(corr_params.right_sharpness) > 0.0:
            right = right ** float(corr_params.right_sharpness)
        right = right * float(corr_params.right_weight)

        right01 = np.clip(right, 0.0, 1.0)
        spot_mask = smoothstep(0.0, 0.5, right01)

        env_base = right + spots * spot_mask
        env_total = (env_base + hot) * corr_level

        dx = float(corr_params.amp) * corr_level * (env_base * nx + hot * hot_dir_x)
        dy = float(corr_params.amp) * corr_level * (env_base * ny + hot * hot_dir_y)
        return dx, dy, env_total

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

    # Palette mapping via the same thresholds as mapgen._classify_tile(fields, 0.5).
    idx_map = np.full((px_h, px_w), 2, dtype=np.int8)
    idx_map[h2 < 0.16] = 0
    idx_map[(h2 >= 0.16) & (h2 < 0.24)] = 1
    idx_map[(h2 >= 0.64) & (h2 < 0.68)] = 3
    idx_map[(h2 >= 0.68) & (h2 < 0.82)] = 4
    idx_map[h2 >= 0.82] = 5

    palette = np.asarray(
        [
            (70, 110, 200),
            (120, 170, 190),
            (150, 200, 120),
            (70, 150, 90),
            (170, 140, 100),
            (200, 200, 210),
        ],
        dtype=np.uint8,
    )
    rgb_main = palette[idx_map]

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

