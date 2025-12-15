from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class CorruptionParams:
    """
    Parameters for the Julia distortion field:

        z_{n+1} = z_n^2 + c + d(z_n)

    The distortion vector d(z) is a deterministic, Perlin-like value-noise vector
    field sampled in the complex plane.
    """

    seed: int = 1337
    # Global frequency multiplier for the landscape heightfield used to build
    # the distortion vector field (see _noise_lut). 1.0 matches the reference
    # "Landscape" feel from distorted_Julia.py when combined with base_res/detail_res.
    freq: float = 1.0
    # Distortion amplitude at corruption_level=1, before envelope scaling.
    amp: float = 0.5

    # Right-side "mountains of corruption" shaping (in normalized Julia x space).
    # 0.0 = start at far left of the current Julia view, 1.0 = only at far right.
    right_start: float = 0.30
    right_sharpness: float = 2.0
    right_weight: float = 1.0

    # Landscape builder knobs (ported from distorted_Julia.py init_landscape).
    base_res: float = 3.0
    detail_res: float = 3.0
    ridge_strength: float = 0.90

    # "Mountain-ness" shaping: we threshold the local distortion vector magnitude
    # so the right side is not uniformly corrupted. Values are in the same units
    # as the LUT vector field magnitude (after normalization in _noise_lut).
    #
    # - mag <= mount_mag_floor: treated as "near zero" (no corruption)
    # - mag >= mount_mag_ceil: treated as "full mountain"
    mount_mag_floor: float = 0.08
    mount_mag_ceil: float = 0.35

    # Scattered hotspots (rare peaks) derived from noise.
    spot_freq: float = 1.1
    spot_threshold: float = 0.86
    spot_weight: float = 0.12

    # Optional explicit hotspots in Julia-plane coordinates: (x, y, strength, sigma)
    # This is for later content-driven placement; not required for phase 1.
    hotspots: list[tuple[float, float, float, float]] = field(default_factory=list)

    # Rune anchors (x, y, sigma, strength) in the Julia z-plane.
    #
    # Each anchor suppresses corruption locally via:
    #   suppress(z) = 1 - strength * exp(-||z-a||^2 / (2*sigma^2))
    # Combined multiplicatively across anchors.
    #
    # Parameter notes:
    # - (x, y): anchor center in Julia-plane coordinates (same space as zx/zy in the iterator).
    # - sigma: Gaussian falloff radius in Julia-plane units.
    #     * Larger sigma => wider "safe zone" around the anchor.
    #     * sigma is NOT in tiles; to convert to tiles, divide by the mean world->Julia
    #       step size (tile_julia_grid["step_x"/"step_y"]) used by the overmap.
    # - strength: suppression strength in [0..1].
    #     * 1.0 means corruption is driven to ~0 exactly at the anchor center.
    #     * 0.5 means corruption is halved at the anchor center.
    #
    # Anchors are intended to be generated as POIs (so they can be visited) but
    # their effect is global and applies to both world-map rendering and local
    # zone generation.
    anchors: list[tuple[float, float, float, float]] = field(default_factory=list)

    # Optional "random points + spline" distortion field (prototype parity).
    # This is intentionally disabled by default (weight=0) so the current landscape
    # field remains the canonical baseline until tuning is complete.
    spline_weight: float = 0.0
    spline_points: int = 40
    spline_strength: float = 0.35
    spline_iters: int = 120


# --- Distortion lookup tables -------------------------------------------------
#
# Computing d(z) inside the Julia iterator can be expensive if we evaluate
# Perlin-like noise and Gaussian hotspots every iteration. For world-map
# rendering (and repeated local-zone generation), we can trade a small amount of
# accuracy for a large speed win by sampling from a precomputed texture over
# the bounded z-domain (the iterator only runs while |z| <= 2).

_LUT_RES: int = 256
_LUT_MIN_Z: float = -2.0
_LUT_MAX_Z: float = 2.0
_LUT_SPAN_Z: float = _LUT_MAX_Z - _LUT_MIN_Z
_LUT_INV_SPAN_Z: float = 1.0 / _LUT_SPAN_Z if abs(_LUT_SPAN_Z) > 1e-12 else 1.0


def _hotspots_cache_key(
    hotspots: Iterable[tuple[float, float, float, float]],
    *,
    ndigits: int = 6,
    ) -> tuple[tuple[float, float, float, float], ...]:
    return tuple(
        (round(float(x), ndigits), round(float(y), ndigits), round(float(s), ndigits), round(float(sig), ndigits))
        for (x, y, s, sig) in hotspots
    )


def _anchors_cache_key(
    anchors: Iterable[tuple[float, float, float, float]],
    *,
    ndigits: int = 6,
) -> tuple[tuple[float, float, float, float], ...]:
    return tuple(
        (round(float(x), ndigits), round(float(y), ndigits), round(float(sig), ndigits), round(float(w), ndigits))
        for (x, y, sig, w) in anchors
    )


@lru_cache(maxsize=4)
def _spline_lut(
    seed: int,
    points: int,
    strength: float,
    iters: int,
    res: int,
) -> tuple["array[float]", "array[float]"]:
    """Return (sx_tex, sy_tex) over the bounded z-plane domain.

    This is a lightweight analogue of the prototype "random points + spline" field:
    - random control points are pinned to values (dx, dy)
    - boundaries are pinned to 0
    - the interior is solved via harmonic relaxation (Jacobi iterations)

    The result is a smooth vector field that can be used as an alternative to the
    landscape/gradient-based field for corruption distortion.
    """
    seed = int(seed)
    points = max(0, min(int(points), 400))
    strength = float(strength)
    iters = max(1, min(int(iters), 2000))
    res = int(res)
    if res <= 2:
        res = 2

    n = res * res
    pinned = bytearray(n)  # 0/1 flags
    pin_re = array("f", [0.0]) * n
    pin_im = array("f", [0.0]) * n

    # Pin boundaries to 0.
    for ix in range(res):
        pinned[ix] = 1
        pinned[(res - 1) * res + ix] = 1
    for iy in range(res):
        pinned[iy * res] = 1
        pinned[iy * res + (res - 1)] = 1

    # Deterministic random control points (biased to the right side of the z-domain).
    import random

    rng = random.Random(seed + 31337)
    for _ in range(points):
        # Bias x towards the right edge (more corruption "sources" on the right).
        u = rng.random()
        x_norm = math.sqrt(u)  # PDF ~ 2x; more mass near 1
        y_norm = rng.random()
        zx = _LUT_MIN_Z + x_norm * _LUT_SPAN_Z
        zy = _LUT_MIN_Z + y_norm * _LUT_SPAN_Z

        ix = int(((zx - _LUT_MIN_Z) * _LUT_INV_SPAN_Z) * (res - 1) + 0.5)
        iy = int(((zy - _LUT_MIN_Z) * _LUT_INV_SPAN_Z) * (res - 1) + 0.5)
        if ix < 0:
            ix = 0
        elif ix >= res:
            ix = res - 1
        if iy < 0:
            iy = 0
        elif iy >= res:
            iy = res - 1

        idx = iy * res + ix
        pinned[idx] = 1
        pin_re[idx] += float((rng.random() * 2.0 - 1.0) * strength)
        pin_im[idx] += float((rng.random() * 2.0 - 1.0) * strength)

    # Two-buffer Jacobi relaxation, solved for both components together.
    arr_re = array("f", [0.0]) * n
    arr_im = array("f", [0.0]) * n
    buf_re = array("f", [0.0]) * n
    buf_im = array("f", [0.0]) * n

    # Initialize pinned values.
    for i in range(n):
        if pinned[i]:
            arr_re[i] = pin_re[i]
            arr_im[i] = pin_im[i]
            buf_re[i] = pin_re[i]
            buf_im[i] = pin_im[i]

    for _ in range(iters):
        for iy in range(1, res - 1):
            row = iy * res
            up = row - res
            down = row + res
            for ix in range(1, res - 1):
                idx = row + ix
                if pinned[idx]:
                    buf_re[idx] = pin_re[idx]
                    buf_im[idx] = pin_im[idx]
                else:
                    buf_re[idx] = float(
                        0.25
                        * (
                            float(arr_re[idx - 1])
                            + float(arr_re[idx + 1])
                            + float(arr_re[up + ix])
                            + float(arr_re[down + ix])
                        )
                    )
                    buf_im[idx] = float(
                        0.25
                        * (
                            float(arr_im[idx - 1])
                            + float(arr_im[idx + 1])
                            + float(arr_im[up + ix])
                            + float(arr_im[down + ix])
                        )
                    )
        arr_re, buf_re = buf_re, arr_re
        arr_im, buf_im = buf_im, arr_im

    return arr_re, arr_im


def _sample_tex_nn(tex: "array[float]", res: int, zx: float, zy: float) -> float:
    """Nearest-neighbor sample of a flat res*res float texture over [_LUT_MIN_Z, _LUT_MAX_Z]^2."""
    if zx <= _LUT_MIN_Z:
        ix = 0
    elif zx >= _LUT_MAX_Z:
        ix = res - 1
    else:
        ix = int(((zx - _LUT_MIN_Z) * _LUT_INV_SPAN_Z) * (res - 1) + 0.5)
        ix = 0 if ix < 0 else (res - 1 if ix >= res else ix)

    if zy <= _LUT_MIN_Z:
        iy = 0
    elif zy >= _LUT_MAX_Z:
        iy = res - 1
    else:
        iy = int(((zy - _LUT_MIN_Z) * _LUT_INV_SPAN_Z) * (res - 1) + 0.5)
        iy = 0 if iy < 0 else (res - 1 if iy >= res else iy)

    return float(tex[iy * res + ix])


def _fbm_value_noise_2d(
    x: float,
    y: float,
    seed: int,
    *,
    octaves: int,
    persistence: float,
    lacunarity: float,
) -> float:
    """Fractal Brownian motion using the lightweight value_noise_2d kernel."""
    octaves = max(1, min(int(octaves), 12))
    persistence = clamp(float(persistence), 0.1, 0.95)
    lacunarity = clamp(float(lacunarity), 1.5, 3.5)

    total = 0.0
    amp = 1.0
    amp_sum = 0.0
    freq = 1.0
    for i in range(octaves):
        total += amp * float(value_noise_2d(x * freq, y * freq, int(seed) + i * 1013))
        amp_sum += amp
        amp *= persistence
        freq *= lacunarity

    return total / amp_sum if amp_sum > 1e-9 else 0.0


@lru_cache(maxsize=4)
def _noise_lut(
    seed: int,
    freq: float,
    base_res: float,
    detail_res: float,
    ridge_strength: float,
    spot_freq: float,
    spot_threshold: float,
    spot_weight: float,
    right_start: float,
    right_sharpness: float,
    right_weight: float,
    j_min_x: float,
    j_max_x: float,
    res: int,
) -> tuple["array[float]", "array[float]", "array[float]"]:
    """Return (nx_tex, ny_tex, spots_tex) over the bounded z-plane domain.

    We build a "mountain" heightfield (fBm + ridges), then use its gradient as a
    coherent distortion vector field, similar in spirit to distorted_Julia.py's
    init_landscape() approach (but in pure Python and precomputed into a LUT).
    """
    seed = int(seed)
    res = int(res)
    if res <= 2:
        res = 2

    # Numpy-accelerated LUT build: this is on the critical startup path because
    # local-zone generation and the world-map renderer both need the LUTs.
    #
    # The prior pure-Python implementation did ~700k value-noise calls per LUT
    # build, which is too slow. Vectorizing the same math with numpy keeps the
    # visuals deterministic while cutting build time dramatically.
    try:
        import numpy as np
    except Exception:
        np = None  # type: ignore[assignment]

    if np is None:
        # --- Fallback: pure-Python LUT build (kept for environments without numpy) ---
        nx_tex = array("f", [0.0]) * (res * res)
        ny_tex = array("f", [0.0]) * (res * res)
        spots_tex = array("f", [0.0]) * (res * res)

        step = _LUT_SPAN_Z / (res - 1)

        # Build a "landscape" heightfield H (normalized) first, based on the
        # prototype `init_landscape()` in distorted_Julia.py.
        h_tex = array("f", [0.0]) * (res * res)
        persistence = 0.55
        lacunarity = 2.0

        mountain_start = clamp(float(right_start), 0.0, 0.98)
        sharpness = max(0.25, float(right_sharpness))
        right_weight = float(right_weight)
        base_res = max(0.01, float(base_res))
        detail_res = max(0.01, float(detail_res))
        ridge_strength = float(ridge_strength)

        span = float(j_max_x) - float(j_min_x)
        if abs(span) < 1e-9:
            span = 1.0

        # Precompute left->right mountain mask per column (saves work in the inner loop).
        m_by_ix: list[float] = [0.0] * res
        for ix in range(res):
            zx = _LUT_MIN_Z + ix * step
            x_norm = (float(zx) - float(j_min_x)) / span
            m = smoothstep(mountain_start, 1.0, x_norm)
            m_by_ix[ix] = (m ** sharpness) * right_weight

        for iy in range(res):
            zy = _LUT_MIN_Z + iy * step
            for ix in range(res):
                zx = _LUT_MIN_Z + ix * step
                idx = iy * res + ix

                m = m_by_ix[ix]

                # Two fBm layers: big hills + detail.
                base = _fbm_value_noise_2d(
                    zx * float(freq) * base_res,
                    zy * float(freq) * base_res,
                    seed + 11,
                    octaves=3,
                    persistence=persistence,
                    lacunarity=lacunarity,
                )
                detail = _fbm_value_noise_2d(
                    zx * float(freq) * detail_res,
                    zy * float(freq) * detail_res,
                    seed + 101,
                    octaves=5,
                    persistence=persistence,
                    lacunarity=lacunarity,
                )
                ridge = (1.0 - abs(detail)) ** 2.0

                # Heightfield: gentle base + mountains on the right.
                # Note: we gate the *gradient* by m later so the far-left is exactly uncorrupted.
                h_tex[idx] = float(0.35 * base + m * (0.70 * detail + ridge_strength * ridge))

                # Scattered peaks derived from a separate low-frequency noise.
                sx = zx * float(spot_freq)
                sy = zy * float(spot_freq)
                s = abs(_fbm_value_noise_2d(sx, sy, seed + 9001, octaves=3, persistence=0.6, lacunarity=2.0))
                spots_tex[idx] = float(smoothstep(float(spot_threshold), 1.0, s) * float(spot_weight))

        # Normalize H to ~[-1,1] like the prototype: subtract mean, divide by max-abs.
        mean_h = float(sum(h_tex) / max(1, len(h_tex)))
        for i in range(len(h_tex)):
            h_tex[i] = float(h_tex[i] - mean_h)
        max_abs = max((abs(float(v)) for v in h_tex), default=1.0)
        if max_abs < 1e-9:
            max_abs = 1.0
        for i in range(len(h_tex)):
            h_tex[i] = float(h_tex[i] / max_abs)

        # Compute gradient of H into nx/ny, and gate the vector field by the same
        # left->right mask so d(z)=0 on the far-left.
        inv_step = 1.0 / step if abs(step) > 1e-12 else 1.0
        max_g2 = 1e-18
        for iy in range(res):
            for ix in range(res):
                idx = iy * res + ix
                ix_l = 0 if ix <= 0 else ix - 1
                ix_r = res - 1 if ix >= res - 1 else ix + 1
                iy_d = 0 if iy <= 0 else iy - 1
                iy_u = res - 1 if iy >= res - 1 else iy + 1
                h_l = float(h_tex[iy * res + ix_l])
                h_r = float(h_tex[iy * res + ix_r])
                h_d = float(h_tex[iy_d * res + ix])
                h_u = float(h_tex[iy_u * res + ix])
                # Central differences (clamped at edges).
                gx = (h_r - h_l) * 0.5 * inv_step
                gy = (h_u - h_d) * 0.5 * inv_step
                m = float(m_by_ix[ix])
                gx *= m
                gy *= m
                nx_tex[idx] = float(gx)
                ny_tex[idx] = float(gy)
                max_g2 = max(max_g2, gx * gx + gy * gy)

        max_g = math.sqrt(max_g2) if max_g2 > 1e-18 else 1.0
        if max_g < 1e-9:
            max_g = 1.0

        # Normalization note:
        # Avoid a large boost + hard clamp here. That combination compresses the
        # dynamic range between the quiet left side and the loud right side (because
        # peaks clamp but the "floor" doesn't), which makes the left side look
        # unnaturally noisy once env(z) is nonzero (e.g. due to spots/hotspots).
        #
        # Instead, normalize by the max gradient magnitude to preserve relative
        # quietness, and apply only a small global scale factor.
        final_scale = (1.2 / max_g)  # small boost; keep max magnitude ~1.2
        for i in range(len(nx_tex)):
            nx_tex[i] = float(nx_tex[i]) * final_scale
            ny_tex[i] = float(ny_tex[i]) * final_scale

        return nx_tex, ny_tex, spots_tex

    # --- Vectorized LUT build (numpy) ---
    step = _LUT_SPAN_Z / (res - 1)
    persistence = 0.55
    lacunarity = 2.0

    mountain_start = clamp(float(right_start), 0.0, 0.98)
    sharpness = max(0.25, float(right_sharpness))
    right_weight_f = float(right_weight)
    base_res_f = max(0.01, float(base_res))
    detail_res_f = max(0.01, float(detail_res))
    ridge_strength_f = float(ridge_strength)
    freq_f = float(freq)

    span = float(j_max_x) - float(j_min_x)
    if abs(span) < 1e-9:
        span = 1.0

    # 1D coordinate lines over the bounded z-plane domain.
    zx_line = (_LUT_MIN_Z + step * np.arange(res, dtype=np.float64))
    zy_line = (_LUT_MIN_Z + step * np.arange(res, dtype=np.float64))
    zx_grid = np.tile(zx_line, (res, 1))
    zy_grid = np.tile(zy_line.reshape(-1, 1), (1, res))

    # Precompute mountain mask per column (same math as the scalar path).
    x_norm = (zx_line - float(j_min_x)) / span
    # Smoothstep for arrays (inline for speed).
    t = (x_norm - float(mountain_start)) / (1.0 - float(mountain_start) if abs(1.0 - float(mountain_start)) > 1e-12 else 1.0)
    t = np.clip(t, 0.0, 1.0)
    right = t * t * (3.0 - 2.0 * t)
    m_by_ix = (right ** float(sharpness)) * right_weight_f

    # Vectorized value-noise + fBm helpers.
    u32 = np.uint32

    def hash01_grid(ix: "np.ndarray", iy: "np.ndarray", seed_i: int) -> "np.ndarray":
        n = (ix.astype(np.int64) * 374761393 + iy.astype(np.int64) * 668265263 + np.int64(seed_i) * 1442695041).astype(u32)
        n ^= (n >> u32(16))
        n *= u32(0x7FEB352D)
        n ^= (n >> u32(15))
        n *= u32(0x846CA68B)
        n ^= (n >> u32(16))
        return (n & u32(0xFFFFFF)).astype(np.float64) / float(0x1000000)

    def value_noise_grid(x: "np.ndarray", y: "np.ndarray", seed_i: int) -> "np.ndarray":
        x0 = np.floor(x).astype(np.int64)
        y0 = np.floor(y).astype(np.int64)
        x1 = x0 + 1
        y1 = y0 + 1
        xf = x - x0
        yf = y - y0

        # Fade curve (Perlin-style).
        u = xf * xf * xf * (xf * (xf * 6.0 - 15.0) + 10.0)
        v = yf * yf * yf * (yf * (yf * 6.0 - 15.0) + 10.0)

        v00 = hash01_grid(x0, y0, seed_i)
        v10 = hash01_grid(x1, y0, seed_i)
        v01 = hash01_grid(x0, y1, seed_i)
        v11 = hash01_grid(x1, y1, seed_i)

        nx0 = v00 + u * (v10 - v00)
        nx1 = v01 + u * (v11 - v01)
        n = nx0 + v * (nx1 - nx0)
        return np.clip(n * 2.0 - 1.0, -1.0, 1.0)

    def fbm_grid(x: "np.ndarray", y: "np.ndarray", seed_i: int, *, octaves: int, persistence_f: float, lacunarity_f: float) -> "np.ndarray":
        total = np.zeros_like(x, dtype=np.float64)
        amp = 1.0
        amp_sum = 0.0
        f = 1.0
        for i in range(int(octaves)):
            total += amp * value_noise_grid(x * f, y * f, int(seed_i) + i * 1013)
            amp_sum += amp
            amp *= float(persistence_f)
            f *= float(lacunarity_f)
        if amp_sum <= 1e-9:
            return total
        return total / float(amp_sum)

    # Build heightfield + spots.
    base = fbm_grid(zx_grid * freq_f * base_res_f, zy_grid * freq_f * base_res_f, seed + 11, octaves=3, persistence_f=persistence, lacunarity_f=lacunarity)
    detail = fbm_grid(zx_grid * freq_f * detail_res_f, zy_grid * freq_f * detail_res_f, seed + 101, octaves=5, persistence_f=persistence, lacunarity_f=lacunarity)
    ridge = (1.0 - np.abs(detail)) ** 2.0

    h = 0.35 * base + (m_by_ix.reshape(1, -1) * (0.70 * detail + ridge_strength_f * ridge))

    s = np.abs(fbm_grid(zx_grid * float(spot_freq), zy_grid * float(spot_freq), seed + 9001, octaves=3, persistence_f=0.6, lacunarity_f=2.0))
    tt = (s - float(spot_threshold)) / (1.0 - float(spot_threshold) if abs(1.0 - float(spot_threshold)) > 1e-12 else 1.0)
    tt = np.clip(tt, 0.0, 1.0)
    spots = (tt * tt * (3.0 - 2.0 * tt)) * float(spot_weight)

    # Normalize H to ~[-1,1] like the scalar path.
    h = h - float(h.mean())
    max_abs = float(np.max(np.abs(h))) if h.size else 1.0
    if max_abs < 1e-9:
        max_abs = 1.0
    h = h / max_abs

    # Gradient with edge clamping.
    inv_step = 1.0 / step if abs(step) > 1e-12 else 1.0
    h_xpad = np.pad(h, ((0, 0), (1, 1)), mode="edge")
    h_ypad = np.pad(h, ((1, 1), (0, 0)), mode="edge")
    gx = (h_xpad[:, 2:] - h_xpad[:, :-2]) * 0.5 * inv_step
    gy = (h_ypad[2:, :] - h_ypad[:-2, :]) * 0.5 * inv_step

    gx *= m_by_ix.reshape(1, -1)
    gy *= m_by_ix.reshape(1, -1)

    max_g2 = float(np.max(gx * gx + gy * gy)) if gx.size else 1.0
    max_g = math.sqrt(max_g2) if max_g2 > 1e-18 else 1.0
    if max_g < 1e-9:
        max_g = 1.0
    final_scale = 1.2 / max_g
    gx *= final_scale
    gy *= final_scale

    # Convert to compact array('f') buffers for both scalar and numpy callers.
    nx_tex = array("f")
    ny_tex = array("f")
    spots_tex = array("f")
    nx_tex.frombytes(gx.astype(np.float32, copy=False).ravel(order="C").tobytes())
    ny_tex.frombytes(gy.astype(np.float32, copy=False).ravel(order="C").tobytes())
    spots_tex.frombytes(spots.astype(np.float32, copy=False).ravel(order="C").tobytes())

    return nx_tex, ny_tex, spots_tex


@lru_cache(maxsize=8)
def _hot_lut(
    hotspots_key: tuple[tuple[float, float, float, float], ...],
    res: int,
) -> tuple["array[float]", "array[float]", "array[float]"]:
    """Return (hot_tex, hot_gx_tex, hot_gy_tex) over the bounded z-plane domain."""
    res = int(res)
    if res <= 2:
        res = 2

    hot_tex = array("f", [0.0]) * (res * res)
    hot_gx_tex = array("f", [0.0]) * (res * res)
    hot_gy_tex = array("f", [0.0]) * (res * res)
    if not hotspots_key:
        return hot_tex, hot_gx_tex, hot_gy_tex

    step = _LUT_SPAN_Z / (res - 1)
    for iy in range(res):
        zy = _LUT_MIN_Z + iy * step
        for ix in range(res):
            zx = _LUT_MIN_Z + ix * step
            idx = iy * res + ix
            total = 0.0
            for hx, hy, strength, sigma in hotspots_key:
                dx = zx - float(hx)
                dy = zy - float(hy)
                s2 = max(1e-9, float(sigma) * float(sigma))
                total += float(strength) * math.exp(-(dx * dx + dy * dy) / (2.0 * s2))
            hot_tex[idx] = float(total)

    # Gradient of the hotspot scalar field. We use this as a direction field so
    # explicit hotspots ("corruption cones") can cause distortion even in areas
    # where the mountain field is masked to zero (far-left uncorrupted regions).
    inv_step = 1.0 / step if abs(step) > 1e-12 else 1.0
    for iy in range(res):
        for ix in range(res):
            idx = iy * res + ix
            ix_l = 0 if ix <= 0 else ix - 1
            ix_r = res - 1 if ix >= res - 1 else ix + 1
            iy_d = 0 if iy <= 0 else iy - 1
            iy_u = res - 1 if iy >= res - 1 else iy + 1
            h_l = float(hot_tex[iy * res + ix_l])
            h_r = float(hot_tex[iy * res + ix_r])
            h_d = float(hot_tex[iy_d * res + ix])
            h_u = float(hot_tex[iy_u * res + ix])
            hot_gx_tex[idx] = float((h_r - h_l) * 0.5 * inv_step)
            hot_gy_tex[idx] = float((h_u - h_d) * 0.5 * inv_step)

    return hot_tex, hot_gx_tex, hot_gy_tex


@lru_cache(maxsize=8)
def _anchor_lut(
    anchors_key: tuple[tuple[float, float, float, float], ...],
    res: int,
) -> "array[float]":
    """Return a multiplicative suppression mask over the bounded z-plane domain."""
    res = int(res)
    if res <= 2:
        res = 2

    mask_tex = array("f", [1.0]) * (res * res)
    if not anchors_key:
        return mask_tex

    # Prefer numpy here as well: the per-anchor Gaussian evaluation is a hot
    # path for startup (we seed many anchors), and vectorization is a major win.
    try:
        import numpy as np
    except Exception:
        np = None  # type: ignore[assignment]

    if np is None:
        step = _LUT_SPAN_Z / (res - 1)
        for iy in range(res):
            zy = _LUT_MIN_Z + iy * step
            for ix in range(res):
                zx = _LUT_MIN_Z + ix * step
                idx = iy * res + ix
                m = 1.0
                for ax, ay, sigma, strength in anchors_key:
                    dx = zx - float(ax)
                    dy = zy - float(ay)
                    s2 = max(1e-9, float(sigma) * float(sigma))
                    g = math.exp(-(dx * dx + dy * dy) / (2.0 * s2))
                    sup = 1.0 - float(strength) * g
                    if sup < 0.0:
                        sup = 0.0
                    m *= sup
                    if m <= 0.0:
                        m = 0.0
                        break
                mask_tex[idx] = float(clamp(m, 0.0, 1.0))
        return mask_tex

    step = _LUT_SPAN_Z / (res - 1)
    zx_line = (_LUT_MIN_Z + step * np.arange(res, dtype=np.float64))
    zy_line = (_LUT_MIN_Z + step * np.arange(res, dtype=np.float64))
    zx_grid = np.tile(zx_line, (res, 1))
    zy_grid = np.tile(zy_line.reshape(-1, 1), (1, res))

    mask = np.ones((res, res), dtype=np.float64)
    for ax, ay, sigma, strength in anchors_key:
        dx = zx_grid - float(ax)
        dy = zy_grid - float(ay)
        s2 = float(max(1e-9, float(sigma) * float(sigma)))
        g = np.exp(-(dx * dx + dy * dy) / (2.0 * s2))
        sup = 1.0 - float(strength) * g
        sup = np.clip(sup, 0.0, 1.0)
        mask *= sup

    mask = np.clip(mask, 0.0, 1.0).astype(np.float32, copy=False)
    out = array("f")
    out.frombytes(mask.ravel(order="C").tobytes())
    return out


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 == edge0:
        return 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash_u32(n: int) -> int:
    n &= 0xFFFFFFFF
    n ^= (n >> 16)
    n = (n * 0x7FEB352D) & 0xFFFFFFFF
    n ^= (n >> 15)
    n = (n * 0x846CA68B) & 0xFFFFFFFF
    n ^= (n >> 16)
    return n & 0xFFFFFFFF


def _hash2(ix: int, iy: int, seed: int) -> int:
    # Large odd constants (similar to typical spatial hashing).
    n = ix * 374761393 + iy * 668265263 + seed * 1442695041
    return _hash_u32(n)


def _hash01(ix: int, iy: int, seed: int) -> float:
    return (_hash2(ix, iy, seed) & 0xFFFFFF) / float(0x1000000)


def value_noise_2d(x: float, y: float, seed: int) -> float:
    """
    Smooth value-noise in [-1, 1], using bilinear interpolation and a Perlin-style fade.

    This is "Perlin-like" and fast enough for inner Julia iteration loops.
    """
    x0 = math.floor(x)
    y0 = math.floor(y)
    x1 = x0 + 1
    y1 = y0 + 1
    xf = x - x0
    yf = y - y0

    # Fade curve
    u = xf * xf * xf * (xf * (xf * 6.0 - 15.0) + 10.0)
    v = yf * yf * yf * (yf * (yf * 6.0 - 15.0) + 10.0)

    # Corner values in [0,1]
    v00 = _hash01(x0, y0, seed)
    v10 = _hash01(x1, y0, seed)
    v01 = _hash01(x0, y1, seed)
    v11 = _hash01(x1, y1, seed)

    # Bilinear interpolate
    nx0 = v00 + u * (v10 - v00)
    nx1 = v01 + u * (v11 - v01)
    n = nx0 + v * (nx1 - nx0)

    return clamp(n * 2.0 - 1.0, -1.0, 1.0)


def _hotspot_envelope(zx: float, zy: float, hotspots: Iterable[tuple[float, float, float, float]]) -> float:
    total = 0.0
    for hx, hy, strength, sigma in hotspots:
        dx = zx - hx
        dy = zy - hy
        s2 = max(1e-9, float(sigma) * float(sigma))
        total += float(strength) * math.exp(-(dx * dx + dy * dy) / (2.0 * s2))
    return total


def corruption_envelope(
    zx: float,
    zy: float,
    *,
    params: CorruptionParams,
    j_min_x: float,
    j_max_x: float,
    corruption_level: float,
) -> float:
    """
    Return a nonnegative scalar multiplier for the distortion field at the current z.

    The envelope concentrates corruption on the right side of the Julia plane and
    adds occasional scattered peaks (gated by the right-side cloud by default).
    """
    if corruption_level <= 0.0:
        return 0.0

    span = j_max_x - j_min_x
    if abs(span) < 1e-9:
        x_norm = 0.0
    else:
        x_norm = (zx - j_min_x) / span

    right = smoothstep(params.right_start, 1.0, x_norm)
    if params.right_sharpness > 0:
        right = right ** params.right_sharpness
    right *= params.right_weight

    # Rare peaks scattered everywhere.
    s = abs(
        _fbm_value_noise_2d(
            zx * params.spot_freq,
            zy * params.spot_freq,
            params.seed + 9001,
            octaves=3,
            persistence=0.6,
            lacunarity=2.0,
        )
    )
    spots = smoothstep(params.spot_threshold, 1.0, s) * params.spot_weight

    hot = 0.0
    if params.hotspots:
        hot = _hotspot_envelope(zx, zy, params.hotspots)

    # Keep the far-left side clean by default: gate random "spots" by the right-side
    # cloud. Important left-side corruption should be represented by explicit hotspots.
    right01 = clamp(float(right), 0.0, 1.0)
    spot_mask = smoothstep(0.0, 0.5, right01)

    # Global corruption scales *everything* (including explicit hotspots) so that a
    # "scale to 0" UI can cleanly return to the classic Julia set.
    env = (spots * spot_mask + hot) * corruption_level
    return max(0.0, env)


def distortion_dz(
    zx: float,
    zy: float,
    *,
    params: CorruptionParams,
    j_min_x: float,
    j_max_x: float,
    corruption_level: float,
) -> tuple[float, float, float]:
    """Return (dx, dy, env) for z_{n+1} = z_n^2 + c + d(z_n)."""

    corruption_level = max(0.0, float(corruption_level))
    if corruption_level <= 0.0:
        return 0.0, 0.0, 0.0

    # Rune anchors: suppress corruption locally regardless of source (mountains/spots/hotspots).
    if params.anchors:
        anchor_factor = 1.0
        try:
            a_tex = _anchor_lut(_anchors_cache_key(params.anchors), int(_LUT_RES))
            anchor_factor = _sample_tex_nn(a_tex, _LUT_RES, zx, zy)
        except Exception:
            # Fallback: direct (non-LUT) evaluation.
            anchor_factor = 1.0
            for ax, ay, sigma, strength in params.anchors:
                dx0 = float(zx) - float(ax)
                dy0 = float(zy) - float(ay)
                s2 = max(1e-9, float(sigma) * float(sigma))
                g = math.exp(-(dx0 * dx0 + dy0 * dy0) / (2.0 * s2))
                sup = 1.0 - float(strength) * g
                if sup < 0.0:
                    sup = 0.0
                anchor_factor *= sup
                if anchor_factor <= 0.0:
                    anchor_factor = 0.0
                    break

        corruption_level *= clamp(float(anchor_factor), 0.0, 1.0)
        if corruption_level <= 0.0:
            return 0.0, 0.0, 0.0

    try:
        nx_tex, ny_tex, spots_tex = _noise_lut(
            int(params.seed),
            float(params.freq),
            float(params.base_res),
            float(params.detail_res),
            float(params.ridge_strength),
            float(params.spot_freq),
            float(params.spot_threshold),
            float(params.spot_weight),
            float(params.right_start),
            float(params.right_sharpness),
            float(params.right_weight),
            float(j_min_x),
            float(j_max_x),
            int(_LUT_RES),
        )

        # Optional prototype parity: replace the landscape field with a random spline field.
        use_spline = float(getattr(params, "spline_weight", 0.0) or 0.0) > 1e-9
        if use_spline:
            spline_x_tex, spline_y_tex = _spline_lut(
                int(params.seed),
                int(getattr(params, "spline_points", 40) or 40),
                float(getattr(params, "spline_strength", 0.35) or 0.35),
                int(getattr(params, "spline_iters", 120) or 120),
                int(_LUT_RES),
            )
            w = float(getattr(params, "spline_weight", 0.0) or 0.0)
            nx = _sample_tex_nn(spline_x_tex, _LUT_RES, zx, zy) * w
            ny = _sample_tex_nn(spline_y_tex, _LUT_RES, zx, zy) * w
        else:
            nx = _sample_tex_nn(nx_tex, _LUT_RES, zx, zy)
            ny = _sample_tex_nn(ny_tex, _LUT_RES, zx, zy)
        spots = _sample_tex_nn(spots_tex, _LUT_RES, zx, zy)

        hot = 0.0
        hot_dir_x = 0.0
        hot_dir_y = 0.0
        if params.hotspots:
            hot_tex, hot_gx_tex, hot_gy_tex = _hot_lut(_hotspots_cache_key(params.hotspots), int(_LUT_RES))
            hot = _sample_tex_nn(hot_tex, _LUT_RES, zx, zy)
            if hot > 0.0:
                gx = _sample_tex_nn(hot_gx_tex, _LUT_RES, zx, zy)
                gy = _sample_tex_nn(hot_gy_tex, _LUT_RES, zx, zy)
                mag = math.sqrt(gx * gx + gy * gy)
                if mag > 1e-9:
                    hot_dir_x = gx / mag
                    hot_dir_y = gy / mag

        span = float(j_max_x) - float(j_min_x)
        if abs(span) < 1e-9:
            x_norm = 0.0
        else:
            x_norm = (float(zx) - float(j_min_x)) / span

        right = smoothstep(float(params.right_start), 1.0, x_norm)
        if params.right_sharpness > 0:
            right = right ** float(params.right_sharpness)
        right *= float(params.right_weight)

        right01 = clamp(float(right), 0.0, 1.0)
        spot_mask = smoothstep(0.0, 0.5, right01)

        # We threshold the *magnitude* so the right side contains quiet valleys
        # (near-zero corruption) and distinct "mountains" rather than a uniform fog.
        spot_boost = 1.0 + float(spots) * spot_mask
        field_x = spot_boost * float(nx) + float(hot) * hot_dir_x
        field_y = spot_boost * float(ny) + float(hot) * hot_dir_y

        mag = math.sqrt(field_x * field_x + field_y * field_y)
        mount = smoothstep(float(params.mount_mag_floor), float(params.mount_mag_ceil), mag)
        if mount <= 0.0:
            return 0.0, 0.0, 0.0

        dx = float(params.amp) * corruption_level * field_x * mount
        dy = float(params.amp) * corruption_level * field_y * mount
        env_total = mount * corruption_level
        return dx, dy, env_total

    except Exception:
        env = corruption_envelope(
            zx,
            zy,
            params=params,
            j_min_x=j_min_x,
            j_max_x=j_max_x,
            corruption_level=corruption_level,
        )
        if env <= 0.0:
            return 0.0, 0.0, 0.0
        fx = zx * float(params.freq) * float(params.detail_res)
        fy = zy * float(params.freq) * float(params.detail_res)
        nx = value_noise_2d(fx, fy, params.seed + 101)
        ny = value_noise_2d(fx, fy, params.seed + 202)
        dx = float(params.amp) * env * nx
        dy = float(params.amp) * env * ny
        return dx, dy, env


def distortion_np(
    zx_a,
    zy_a,
    *,
    params: CorruptionParams,
    j_min_x: float,
    j_max_x: float,
    corruption_level: float,
    nx_tex,
    ny_tex,
    spots_tex,
    spline_x_tex=None,
    spline_y_tex=None,
    anchor_tex=None,
    hot_tex=None,
    hot_gx_tex=None,
    hot_gy_tex=None,
    lut_res: int = _LUT_RES,
):
    """Vectorized analogue of distortion_dz for numpy arrays.

    This exists so the overmap "numpy fast-path" uses the exact same corruption
    rules as the scalar path (local mapgen + python fallback renderer).
    """
    # Local import so core gameplay doesn't require numpy.
    import numpy as np

    corr_level = float(corruption_level or 0.0)
    if corr_level <= 0.0:
        z = np.zeros_like(zx_a, dtype=np.float64)
        return z, z, z

    lut_res = int(lut_res)
    if lut_res <= 2:
        lut_res = 2

    lut_min_z = float(_LUT_MIN_Z)
    lut_inv_span = float(_LUT_INV_SPAN_Z)
    lut_scale = float(lut_res - 1)

    def smoothstep_np(edge0: float, edge1: float, x: "np.ndarray") -> "np.ndarray":
        denom = float(edge1) - float(edge0)
        if abs(denom) < 1e-12:
            return np.zeros_like(x, dtype=np.float64)
        t = (x - float(edge0)) / denom
        t = np.clip(t, 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    def sample_nn_flat(tex: "np.ndarray", zx: "np.ndarray", zy: "np.ndarray") -> "np.ndarray":
        ix = np.rint(((zx - lut_min_z) * lut_inv_span) * lut_scale).astype(np.int32)
        iy = np.rint(((zy - lut_min_z) * lut_inv_span) * lut_scale).astype(np.int32)
        ix = np.clip(ix, 0, lut_res - 1)
        iy = np.clip(iy, 0, lut_res - 1)
        return tex[iy * lut_res + ix]

    # Rune anchors: multiplicatively suppress the effective corruption level.
    if params.anchors:
        if anchor_tex is not None:
            anchor = sample_nn_flat(anchor_tex, zx_a, zy_a).astype(np.float64, copy=False)
        else:
            anchor = np.ones_like(zx_a, dtype=np.float64)
            for ax, ay, sigma, strength in params.anchors:
                dx0 = zx_a - float(ax)
                dy0 = zy_a - float(ay)
                s2 = float(max(1e-9, float(sigma) * float(sigma)))
                g = np.exp(-(dx0 * dx0 + dy0 * dy0) / (2.0 * s2))
                sup = 1.0 - float(strength) * g
                sup = np.clip(sup, 0.0, 1.0)
                anchor *= sup
        corr_level_eff = corr_level * np.clip(anchor, 0.0, 1.0)
    else:
        corr_level_eff = corr_level

    # Sample LUT textures at the current z.
    spots = sample_nn_flat(spots_tex, zx_a, zy_a).astype(np.float64, copy=False)

    # Choose the underlying vector field:
    # - landscape (gradient) by default
    # - optional spline-based random field when params.spline_weight > 0
    use_spline = (
        float(getattr(params, "spline_weight", 0.0) or 0.0) > 1e-9
        and spline_x_tex is not None
        and spline_y_tex is not None
    )
    if use_spline:
        w = float(getattr(params, "spline_weight", 0.0) or 0.0)
        nx = sample_nn_flat(spline_x_tex, zx_a, zy_a).astype(np.float64, copy=False) * w
        ny = sample_nn_flat(spline_y_tex, zx_a, zy_a).astype(np.float64, copy=False) * w
    else:
        nx = sample_nn_flat(nx_tex, zx_a, zy_a).astype(np.float64, copy=False)
        ny = sample_nn_flat(ny_tex, zx_a, zy_a).astype(np.float64, copy=False)

    # Hotspots: scalar envelope + direction from its gradient.
    hot = np.zeros_like(nx, dtype=np.float64)
    hot_dir_x = np.zeros_like(nx, dtype=np.float64)
    hot_dir_y = np.zeros_like(nx, dtype=np.float64)
    if hot_tex is not None and hot_gx_tex is not None and hot_gy_tex is not None:
        hot = sample_nn_flat(hot_tex, zx_a, zy_a).astype(np.float64, copy=False)
        if np.any(hot > 0.0):
            gx = sample_nn_flat(hot_gx_tex, zx_a, zy_a).astype(np.float64, copy=False)
            gy = sample_nn_flat(hot_gy_tex, zx_a, zy_a).astype(np.float64, copy=False)
            mag = np.sqrt(gx * gx + gy * gy)
            ok = mag > 1e-9
            hot_dir_x = np.where(ok, gx / mag, 0.0)
            hot_dir_y = np.where(ok, gy / mag, 0.0)

    # Right-side ramp only gates "spots" (and drives UI positioning), not a baseline corruption floor.
    span = float(j_max_x) - float(j_min_x)
    if abs(span) < 1e-12:
        x_norm = np.zeros_like(nx, dtype=np.float64)
    else:
        x_norm = (zx_a - float(j_min_x)) / span

    right = smoothstep_np(float(params.right_start), 1.0, x_norm)
    if float(params.right_sharpness) > 0.0:
        right = right ** float(params.right_sharpness)
    right = right * float(params.right_weight)

    right01 = np.clip(right, 0.0, 1.0)
    spot_mask = smoothstep_np(0.0, 0.5, right01)

    # We threshold the *magnitude* so the right side contains quiet valleys
    # (near-zero corruption) and distinct "mountains" rather than a uniform fog.
    spot_boost = 1.0 + spots * spot_mask
    field_x = spot_boost * nx + hot * hot_dir_x
    field_y = spot_boost * ny + hot * hot_dir_y

    mag = np.sqrt(field_x * field_x + field_y * field_y)
    mount = smoothstep_np(float(params.mount_mag_floor), float(params.mount_mag_ceil), mag)

    dx = float(params.amp) * corr_level_eff * field_x * mount
    dy = float(params.amp) * corr_level_eff * field_y * mount
    env_total = mount * corr_level_eff
    return dx, dy, env_total


def julia_height_norm_corrupted(
    nx: float,
    ny: float,
    c: complex,
    *,
    iters: int = 96,
    scale: float = 1.0,
    corruption_level: float = 0.0,
    params: Optional[CorruptionParams] = None,
    j_min_x: float = -2.0,
    j_max_x: float = 2.0,
) -> tuple[float, float]:
    """
    Return (height, corruption_strength) where height is 0..1.

    corruption_strength is a nonnegative scalar useful for tinting / UI.
    """
    if params is None:
        params = CorruptionParams()

    zx = nx * scale
    zy = ny * scale
    it = 0
    peak_env = 0.0

    while zx * zx + zy * zy <= 4.0 and it < iters:
        dx, dy, env = distortion_dz(
            zx,
            zy,
            params=params,
            j_min_x=j_min_x,
            j_max_x=j_max_x,
            corruption_level=corruption_level,
        )
        if env > peak_env:
            peak_env = env

        xt = zx * zx - zy * zy + c.real + dx
        zy = 2.0 * zx * zy + c.imag + dy
        zx = xt
        it += 1

    if it >= iters:
        return 0.0, peak_env

    mod = math.sqrt(zx * zx + zy * zy)
    smooth = it + 1.0 - math.log(math.log(max(mod, 1e-6))) / math.log(2.0)
    h = clamp(smooth / iters, 0.0, 1.0)

    return h, peak_env
