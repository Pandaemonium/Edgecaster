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
    # Base distortion frequency in Julia-plane units.
    # (Used as the "detail" frequency for the mountain-like field.)
    freq: float = 3.2
    # Distortion amplitude at corruption_level=1, before envelope scaling.
    amp: float = 0.5

    # Right-side "mountains of corruption" shaping (in normalized Julia x space).
    # 0.0 = start at far left of the current Julia view, 1.0 = only at far right.
    right_start: float = 0.4
    right_sharpness: float = 2.5
    right_weight: float = 1.0

    # Scattered hotspots (rare peaks) derived from noise.
    spot_freq: float = 1.1
    spot_threshold: float = 0.86
    spot_weight: float = 0.12

    # Optional explicit hotspots in Julia-plane coordinates: (x, y, strength, sigma)
    # This is for later content-driven placement; not required for phase 1.
    hotspots: list[tuple[float, float, float, float]] = field(default_factory=list)


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
    spot_freq: float,
    spot_threshold: float,
    spot_weight: float,
    right_start: float,
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

    nx_tex = array("f", [0.0]) * (res * res)
    ny_tex = array("f", [0.0]) * (res * res)
    spots_tex = array("f", [0.0]) * (res * res)

    step = _LUT_SPAN_Z / (res - 1)

    # Build mountain-ish heightfield H (normalized) first.
    h_tex = array("f", [0.0]) * (res * res)
    base_mult = 0.25
    ridge_strength = 1.25
    persistence = 0.55
    lacunarity = 2.0

    mountain_start = clamp(float(right_start), 0.0, 0.98)

    for iy in range(res):
        zy = _LUT_MIN_Z + iy * step
        for ix in range(res):
            zx = _LUT_MIN_Z + ix * step
            idx = iy * res + ix

            # Two fBm layers: big hills + detail. Keep both tied to `freq` so
            # a single knob changes the overall "grain size".
            base = _fbm_value_noise_2d(
                zx * float(freq) * base_mult,
                zy * float(freq) * base_mult,
                seed + 11,
                octaves=4,
                persistence=persistence,
                lacunarity=lacunarity,
            )
            detail = _fbm_value_noise_2d(
                zx * float(freq),
                zy * float(freq),
                seed + 101,
                octaves=8,
                persistence=persistence,
                lacunarity=lacunarity,
            )
            ridge = (1.0 - abs(detail)) ** 2.0

            # Left->right mountain mask in the bounded z-domain. This produces
            # clearer "mountain ridges" on the right side of the map, similar
            # to distorted_Julia.py's `init_landscape()` scheme.
            x_norm = ix / max(1, (res - 1))
            m = smoothstep(mountain_start, 1.0, float(x_norm))
            m = m ** 2.5

            # Apply the left->right mask to all layers except the base map so the left side is
            # mathematically flat (H=0) and therefore has zero distortion
            # (d(z)=0). This keeps the far-left of the overmap close to an
            # uncorrupted Julia set, while still allowing strong "mountain"
            # ridges on the right.
            h_tex[idx] = float(0.35 * base + m * (0.70 * detail + ridge_strength * ridge))

            # Scattered peaks derived from a separate low-frequency noise.
            sx = zx * float(spot_freq)
            sy = zy * float(spot_freq)
            s = abs(_fbm_value_noise_2d(sx, sy, seed + 9001, octaves=3, persistence=0.6, lacunarity=2.0))
            spots_tex[idx] = float(smoothstep(float(spot_threshold), 1.0, s) * float(spot_weight))

    # Normalize H to ~[-1,1].
    max_val = max(h_tex, default=1.0)
    if max_val < 1e-9:
        max_val = 1.0
    for i in range(len(h_tex)):
        h_tex[i] = float(h_tex[i] / max_val)

    # Compute gradient of H into nx/ny.
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
    env = (right + spots * spot_mask + hot) * corruption_level
    return max(0.0, env)


def distortion_dz(
    zx: float,
    zy: float,
    *,
    params: CorruptionParams,
    j_min_x: float, # These are now ignored for masking logic
    j_max_x: float, # These are now ignored for masking logic
    corruption_level: float,
) -> tuple[float, float, float]:
    """Return (dx, dy, env) for d(z) = amp*env*(nx+i*ny)."""
    
    corruption_level = max(0.0, float(corruption_level))
    if corruption_level <= 0.0:
        return 0.0, 0.0, 0.0

    try:
        # 1. Sample the Noise Texture (Generated in World Space -2.0 to 2.0)
        nx_tex, ny_tex, spots_tex = _noise_lut(
            int(params.seed),
            float(params.freq),
            float(params.spot_freq),
            float(params.spot_threshold),
            float(params.spot_weight),
            float(params.right_start),
            int(_LUT_RES),
        )
        nx = _sample_tex_nn(nx_tex, _LUT_RES, zx, zy)
        ny = _sample_tex_nn(ny_tex, _LUT_RES, zx, zy)
        spots = _sample_tex_nn(spots_tex, _LUT_RES, zx, zy)

        # 2. Hotspot Sampling
        hot = 0.0
        hot_dir_x = 0.0
        hot_dir_y = 0.0
        if params.hotspots:
             # ... (Keep existing hotspot logic) ...
            hot_tex, hot_gx_tex, hot_gy_tex = _hot_lut(_hotspots_cache_key(params.hotspots), int(_LUT_RES))
            hot = _sample_tex_nn(hot_tex, _LUT_RES, zx, zy)
            if hot > 0.0:
                gx = _sample_tex_nn(hot_gx_tex, _LUT_RES, zx, zy)
                gy = _sample_tex_nn(hot_gy_tex, _LUT_RES, zx, zy)
                mag = math.sqrt(gx * gx + gy * gy)
                if mag > 1e-9:
                    hot_dir_x = gx / mag
                    hot_dir_y = gy / mag

        # 3. FIX: Calculate Mask in GLOBAL WORLD SPACE, not Screen Space.
        #    This ensures x=-1.5 is ALWAYS safe, regardless of camera position.
        #    (Using the same bounds as the LUT generation: -2.0 to 2.0)
        x_norm = (float(zx) - _LUT_MIN_Z) / _LUT_SPAN_Z

        right = smoothstep(float(params.right_start), 1.0, x_norm)
        if params.right_sharpness > 0:
            right = right ** float(params.right_sharpness)
        right *= float(params.right_weight)

        right01 = clamp(float(right), 0.0, 1.0)
        spot_mask = smoothstep(0.0, 0.5, right01)

        env_base = (right + float(spots) * spot_mask)
        env_total = (env_base + float(hot)) * corruption_level
        
        if env_total <= 0.0:
            return 0.0, 0.0, 0.0

        dx = float(params.amp) * corruption_level * (env_base * float(nx) + float(hot) * hot_dir_x)
        dy = float(params.amp) * corruption_level * (env_base * float(ny) + float(hot) * hot_dir_y)
        return dx, dy, env_total

    except Exception:
        # Fallback must ALSO use global bounds to match visual expectations
        # (We inline the logic here to ensure it uses _LUT constants)
        x_norm = (float(zx) - _LUT_MIN_Z) / _LUT_SPAN_Z
        
        right = smoothstep(float(params.right_start), 1.0, x_norm)
        if params.right_sharpness > 0: right = right ** float(params.right_sharpness)
        right *= float(params.right_weight)
        
        # Recalculate spots/hot raw
        s = abs(_fbm_value_noise_2d(zx * params.spot_freq, zy * params.spot_freq, params.seed + 9001, octaves=3, persistence=0.6, lacunarity=2.0))
        spots = smoothstep(params.spot_threshold, 1.0, s) * params.spot_weight
        
        hot = 0.0
        if params.hotspots:
             hot = _hotspot_envelope(zx, zy, params.hotspots)
             
        spot_mask = smoothstep(0.0, 0.5, clamp(right, 0.0, 1.0))
        env = (right + spots * spot_mask + hot) * corruption_level
        
        if env <= 0.0: return 0.0, 0.0, 0.0
        
        fx = zx * params.freq
        fy = zy * params.freq
        nx = value_noise_2d(fx, fy, params.seed + 101)
        ny = value_noise_2d(fx, fy, params.seed + 202)
        dx = params.amp * env * nx
        dy = params.amp * env * ny
        return dx, dy, env

    except Exception:
        # Defensive fallback: exact evaluation.
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
        fx = zx * params.freq
        fy = zy * params.freq
        nx = value_noise_2d(fx, fy, params.seed + 101)
        ny = value_noise_2d(fx, fy, params.seed + 202)
        dx = params.amp * env * nx
        dy = params.amp * env * ny
        return dx, dy, env


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
