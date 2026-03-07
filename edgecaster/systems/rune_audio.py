# edgecaster/systems/rune_audio.py
from __future__ import annotations

import hashlib
import math
import traceback
from dataclasses import dataclass
from typing import List

import numpy as np
import pygame


# -----------------------------------------------------------------------------
# Rune audio synthesis
#
# Design intent:
#   - Geometry drives a multiscale harmonic drone.
#   - Color nudges pitch in a Newton-ish / just-intonation direction.
#   - Runtime recolors should invalidate the signature and retrigger synthesis.
#
# Engineering intent:
#   - Fail soft, but log exceptions.
#   - Match pygame mixer channel count.
#   - Avoid obvious phase/perf traps.
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class RuneAudioConfig:
    sr: int = 44100
    dur_s: float = 8.0            # preferred loop, quantized to 4/8/16
    base_freq_min: float = 55.0   # ~A1
    base_freq_max: float = 220.0  # ~A3
    max_partials: int = 40
    volume: float = 0.22          # baked into waveform
    channel_index: int = 6        # preferred sfx lane
    fade_ms: int = 25             # edge fade for clickless loop


# -----------------------------
# Logging
# -----------------------------

def _log_err(msg: str) -> None:
    try:
        with open("rune_audio_errors.log", "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def _log_exc(prefix: str, ex: BaseException) -> None:
    _log_err(f"{prefix} EXCEPTION: {repr(ex)}")
    try:
        _log_err(traceback.format_exc().rstrip())
    except Exception:
        pass


# -----------------------------
# Pattern feature extraction
# -----------------------------

def _safe_float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _edge_lengths(pattern) -> List[float]:
    verts = getattr(pattern, "vertices", None) or []
    edges = getattr(pattern, "edges", None) or []
    out: List[float] = []
    for e in edges:
        try:
            a = verts[int(e.a)].pos
            b = verts[int(e.b)].pos
            dx = _safe_float(b[0]) - _safe_float(a[0])
            dy = _safe_float(b[1]) - _safe_float(a[1])
            L = math.hypot(dx, dy)
            if L > 1e-6:
                out.append(L)
        except Exception:
            continue
    return out


def _adjacency(pattern) -> dict[int, list[int]]:
    edges = getattr(pattern, "edges", None) or []
    adj: dict[int, list[int]] = {}
    for e in edges:
        try:
            a = int(e.a)
            b = int(e.b)
        except Exception:
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    return adj


def _angle_sharpness(pattern) -> float:
    """
    [0..1] proxy for pointiness (rotation invariant).
    0 => mostly collinear; 1 => lots of acute-ish corners.
    """
    verts = getattr(pattern, "vertices", None) or []
    adj = _adjacency(pattern)
    scores: List[float] = []

    for v_idx, nbrs in adj.items():
        if len(nbrs) < 2:
            continue
        try:
            vx, vy = verts[v_idx].pos
            vx = _safe_float(vx)
            vy = _safe_float(vy)
        except Exception:
            continue

        for i in range(len(nbrs)):
            for j in range(i + 1, len(nbrs)):
                ni = nbrs[i]
                nj = nbrs[j]
                try:
                    ax, ay = verts[ni].pos
                    bx, by = verts[nj].pos
                    ax = _safe_float(ax) - vx
                    ay = _safe_float(ay) - vy
                    bx = _safe_float(bx) - vx
                    by = _safe_float(by) - vy
                except Exception:
                    continue

                la = math.hypot(ax, ay)
                lb = math.hypot(bx, by)
                if la < 1e-6 or lb < 1e-6:
                    continue

                dot = (ax * bx + ay * by) / (la * lb)
                dot = max(-1.0, min(1.0, dot))
                theta = math.acos(dot)

                s = math.sin(theta)
                acute_bonus = max(0.0, (math.pi / 3.0 - theta) / (math.pi / 3.0))
                scores.append(0.75 * s + 0.25 * acute_bonus)

    if not scores:
        return 0.0
    m = sum(scores) / len(scores)
    return max(0.0, min(1.0, m))


def _normalize_edge_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _canonical_color_name(name: str) -> str:
    """
    Normalize likely aliases into coarse spectral buckets.
    """
    s = str(name or "neutral").strip().lower()

    alias_map = {
        "purple": "violet",
        "magenta": "violet",
        "pink": "violet",
        "cyan": "blue",
        "teal": "blue",
        "azure": "blue",
        "gold": "yellow",
        "lime": "green",
        "emerald": "green",
        "scarlet": "red",
        "crimson": "red",
        "amber": "orange",
        "white": "neutral",
        "gray": "neutral",
        "grey": "neutral",
        "black": "neutral",
        "brown": "neutral",
    }
    return alias_map.get(s, s)


def _color_token_to_name(token) -> str:
    """
    Convert either symbolic names or RGB-ish tuples into a coarse spectral name.
    """
    if isinstance(token, str):
        return _canonical_color_name(token)

    if isinstance(token, (list, tuple)) and len(token) >= 3:
        try:
            r = float(token[0])
            g = float(token[1])
            b = float(token[2])
        except Exception:
            return "neutral"

        # Support both 0..1 and 0..255 conventions.
        if max(abs(r), abs(g), abs(b)) > 1.5:
            r /= 255.0
            g /= 255.0
            b /= 255.0

        r = max(0.0, min(1.0, r))
        g = max(0.0, min(1.0, g))
        b = max(0.0, min(1.0, b))

        mx = max(r, g, b)
        if mx <= 1e-6:
            return "neutral"

        if r >= g and r >= b:
            if g > 0.75 * r:
                return "orange" if b < 0.45 * r else "violet"
            return "red"

        if g >= r and g >= b:
            if r > 0.78 * g:
                return "yellow"
            if b > 0.70 * g:
                return "indigo"
            return "green"

        # blue-dominant
        if r > 0.60 * b:
            return "violet"
        if g > 0.50 * b:
            return "indigo"
        return "blue"

    return "neutral"


def _edge_runtime_color_names(pattern) -> list[str]:
    """
    Prefer runtime edge color overrides if present.
    Fallback to baked edge.color.
    """
    out: list[str] = []
    edges = getattr(pattern, "edges", None) or []
    edge_colors = getattr(pattern, "edge_colors", None)

    for e in edges:
        token = None

        if isinstance(edge_colors, dict):
            try:
                token = edge_colors.get(_normalize_edge_key(int(e.a), int(e.b)), None)
            except Exception:
                token = None

        if token is None:
            token = getattr(e, "color", "neutral")

        out.append(_color_token_to_name(token))

    return out


def _vertex_runtime_color_names(pattern) -> list[str]:
    """
    Prefer runtime vertex color overrides if present.
    Fallback to baked vertex.color.
    """
    out: list[str] = []
    verts = getattr(pattern, "vertices", None) or []
    vertex_colors = getattr(pattern, "vertex_colors", None)

    for i, v in enumerate(verts):
        token = None

        if isinstance(vertex_colors, (list, tuple)) and i < len(vertex_colors):
            token = vertex_colors[i]

        if token is None:
            token = getattr(v, "color", "neutral")

        out.append(_color_token_to_name(token))

    return out


def _iter_pattern_color_names(pattern) -> list[str]:
    return _edge_runtime_color_names(pattern) + _vertex_runtime_color_names(pattern)


def _color_fingerprint(pattern) -> str:
    """
    Stable digest of runtime colors for signature invalidation.
    """
    colors = _iter_pattern_color_names(pattern)
    if not colors:
        return "none"

    counts: dict[str, int] = {}
    for c in colors:
        c = _canonical_color_name(c)
        counts[c] = counts.get(c, 0) + 1

    parts = [f"{k}:{counts[k]}" for k in sorted(counts.keys())]
    return "|".join(parts)


def _signature(pattern) -> str:
    """
    Rotation-invariant signature:
      - vertex/edge counts
      - quantized sorted edge lengths
      - quantized sharpness
      - color fingerprint
    """
    lengths = _edge_lengths(pattern)
    lengths.sort()
    q = [int(round(L * 1000.0)) for L in lengths[:256]]
    sharp = int(round(_angle_sharpness(pattern) * 1000.0))
    nv = len(getattr(pattern, "vertices", None) or [])
    ne = len(getattr(pattern, "edges", None) or [])
    color_fp = _color_fingerprint(pattern)
    raw = f"nv={nv}|ne={ne}|sharp={sharp}|L={q}|C={color_fp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _seed_from_sig(sig: str) -> int:
    try:
        return int(sig[:8], 16)
    except Exception:
        return 0


def _rng_from_sig(sig: str) -> np.random.Generator:
    return np.random.default_rng(_seed_from_sig(sig))


def _base_freq_from_lengths(lengths: List[float], cfg: RuneAudioConfig) -> float:
    if not lengths:
        return 110.0
    Lmax = max(lengths)
    scale = 0.6 * Lmax + 0.4 * (sum(lengths) / len(lengths))
    f = 220.0 / max(0.5, scale)
    return max(cfg.base_freq_min, min(cfg.base_freq_max, f))


def _spectral_ratio_for_color(name: str) -> float:
    """
    Newton-ish rainbow -> just-intonation style mapping.
    """
    c = _canonical_color_name(name)
    mapping = {
        "red": 1.0,            # unison
        "orange": 9.0 / 8.0,   # major 2nd
        "yellow": 5.0 / 4.0,   # major 3rd
        "green": 4.0 / 3.0,    # perfect 4th
        "blue": 3.0 / 2.0,     # perfect 5th
        "indigo": 5.0 / 3.0,   # major 6th
        "violet": 15.0 / 8.0,  # major 7th
        "neutral": 1.0,
    }
    return float(mapping.get(c, 1.0))


def _fold_ratio_to_octave(r: float) -> float:
    r = float(max(1e-6, r))
    while r < 1.0:
        r *= 2.0
    while r >= 2.0:
        r *= 0.5
    return r


def _pitch_ratio_from_colors(pattern) -> float:
    """
    Convert rune color distribution into one octave-folded pitch ratio.
    Edges dominate; vertices are lighter seasoning.
    """
    weights: list[tuple[str, float]] = []

    edges = getattr(pattern, "edges", None) or []
    edge_colors = getattr(pattern, "edge_colors", None)

    for e in edges:
        token = None
        if isinstance(edge_colors, dict):
            try:
                token = edge_colors.get(_normalize_edge_key(int(e.a), int(e.b)), None)
            except Exception:
                token = None
        if token is None:
            token = getattr(e, "color", "neutral")

        c = _color_token_to_name(token)
        w = max(0.001, _safe_float(getattr(e, "weight", 1.0), 1.0))
        weights.append((c, w))

    verts = getattr(pattern, "vertices", None) or []
    vertex_colors = getattr(pattern, "vertex_colors", None)

    for i, v in enumerate(verts):
        token = None
        if isinstance(vertex_colors, (list, tuple)) and i < len(vertex_colors):
            token = vertex_colors[i]
        if token is None:
            token = getattr(v, "color", "neutral")

        c = _color_token_to_name(token)
        w = 0.35 * max(0.001, _safe_float(getattr(v, "power", 1.0), 1.0))
        weights.append((c, w))

    if not weights:
        return 1.0

    total_w = 0.0
    acc = 0.0
    for c, w in weights:
        ratio = _spectral_ratio_for_color(c)
        acc += w * math.log(max(1e-6, ratio))
        total_w += w

    if total_w <= 1e-9:
        return 1.0

    ratio = math.exp(acc / total_w)
    return _fold_ratio_to_octave(ratio)


def _edge_color_token(pattern, e):
    """
    Prefer runtime edge color overrides if present.
    Fallback to baked edge.color.
    """
    edge_colors = getattr(pattern, "edge_colors", None)

    token = None
    if isinstance(edge_colors, dict):
        try:
            token = edge_colors.get(_normalize_edge_key(int(e.a), int(e.b)), None)
        except Exception:
            token = None

    if token is None:
        token = getattr(e, "color", "neutral")

    return token


def _color_ratio_signal_along_path(pattern, bins: int = 2048) -> tuple[np.ndarray, float]:
    """
    Paint a just-ratio color signal along rune arc length.
    Each edge contributes its own color ratio across its span.
    Returns (ratio_bins, total_length).
    """
    verts = getattr(pattern, "vertices", None) or []
    edges = getattr(pattern, "edges", None) or []

    if not verts or not edges:
        return np.ones(bins, dtype=np.float32), 0.0

    segs: list[tuple[float, float]] = []
    total = 0.0

    for e in edges:
        try:
            a = verts[int(e.a)].pos
            b = verts[int(e.b)].pos
            dx = _safe_float(b[0]) - _safe_float(a[0])
            dy = _safe_float(b[1]) - _safe_float(a[1])
            L = math.hypot(dx, dy)
            if L <= 1e-9:
                continue

            token = _edge_color_token(pattern, e)
            cname = _color_token_to_name(token)
            ratio = _spectral_ratio_for_color(cname)

            segs.append((L, float(ratio)))
            total += L
        except Exception:
            continue

    if total <= 1e-9:
        return np.ones(bins, dtype=np.float32), 0.0

    out = np.ones(bins, dtype=np.float32)

    s0 = 0.0
    for L, ratio in segs:
        s1 = s0 + L
        i0 = int((s0 / total) * bins)
        i1 = int((s1 / total) * bins)
        if i1 <= i0:
            i1 = i0 + 1

        i0 = max(0, min(bins - 1, i0))
        i1 = max(i0 + 1, min(bins, i1))
        out[i0:i1] = ratio
        s0 = s1

    return out, float(total)


def _snap_log_ratio_windows(x: np.ndarray, window: int, allowed_ratios: list[float]) -> np.ndarray:
    """
    Quantize a log-ratio control signal in short windows to nearest allowed just-ratio.
    Used for kiki / angular patterns.
    """
    if len(x) == 0:
        return x.astype(np.float32)

    window = max(1, int(window))
    allowed = np.log(np.asarray(allowed_ratios, dtype=np.float32))
    y = x.astype(np.float32).copy()

    for i in range(0, len(y), window):
        j = min(len(y), i + window)
        m = float(np.mean(y[i:j]))
        k = int(np.argmin(np.abs(allowed - m)))
        y[i:j] = allowed[k]

    return y.astype(np.float32)

# -----------------------------
# Geometry -> curvature signal
# -----------------------------

def _edges_ordered_polyline(pattern) -> list[tuple[float, float]]:
    """
    Attempt to build an ordered polyline from pattern.edges order.
    """
    verts = getattr(pattern, "vertices", None) or []
    edges = getattr(pattern, "edges", None) or []
    if not verts or not edges:
        return []

    def vpos(i: int) -> tuple[float, float]:
        x, y = verts[i].pos
        return (_safe_float(x), _safe_float(y))

    pts: list[tuple[float, float]] = []
    e0 = edges[0]
    a0, b0 = int(e0.a), int(e0.b)
    pts.append(vpos(a0))
    pts.append(vpos(b0))
    last = b0

    for e in edges[1:]:
        a, b = int(e.a), int(e.b)
        if a == last:
            pts.append(vpos(b))
            last = b
        elif b == last:
            pts.append(vpos(a))
            last = a
        else:
            pts.append(vpos(a))
            pts.append(vpos(b))
            last = b

    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or (abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9):
            out.append(p)
    return out


def _curvature_signal_along_path(
    pts: list[tuple[float, float]],
    bins: int = 2048,
) -> tuple[np.ndarray, float]:
    """
    Curvature impulse signal k(s) along normalized arc-length s in [0,1).
    Returns (k_bins, total_length).
    """
    if len(pts) < 3:
        return np.zeros(bins, dtype=np.float32), 0.0

    segs: list[tuple[float, float]] = []
    total = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        L = math.hypot(dx, dy)
        if L <= 1e-9:
            continue
        ang = math.atan2(dy, dx)
        segs.append((L, ang))
        total += L

    if len(segs) < 2 or total <= 1e-9:
        return np.zeros(bins, dtype=np.float32), float(total)

    k = np.zeros(bins, dtype=np.float32)
    s = 0.0
    for i in range(1, len(segs)):
        prev_L, prev_ang = segs[i - 1]
        L, ang = segs[i]
        s += prev_L

        d = ang - prev_ang
        while d > math.pi:
            d -= 2.0 * math.pi
        while d < -math.pi:
            d += 2.0 * math.pi
        turn = abs(d)

        u = (s / total) % 1.0
        idx = int(u * bins) % bins

        mag = float(turn / math.pi)
        k[idx] += (0.6 * mag + 0.4 * (mag ** 1.5))

    k[0] += 0.5 * k[-1]
    k[-1] *= 0.5
    return k, float(total)


# -----------------------------
# Signal helpers
# -----------------------------

def _gaussian_kernel(sigma: float) -> np.ndarray:
    sigma = max(0.5, float(sigma))
    radius = int(max(3, math.ceil(4.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    w = np.exp(-(x * x) / (2.0 * sigma * sigma)).astype(np.float32)
    w /= max(1e-9, float(w.sum()))
    return w


def _circular_convolve(x: np.ndarray, k: np.ndarray) -> np.ndarray:
    r = (len(k) - 1) // 2
    if r <= 0:
        return x.astype(np.float32).copy()
    xp = np.concatenate([x[-r:], x, x[:r]]).astype(np.float32)
    y = np.convolve(xp, k, mode="valid").astype(np.float32)
    return y


def _normalize01(x: np.ndarray) -> np.ndarray:
    lo = float(np.min(x))
    hi = float(np.max(x))
    if hi - lo < 1e-9:
        return np.zeros_like(x, dtype=np.float32)
    return ((x - lo) / (hi - lo)).astype(np.float32)


def _lowpass_1pole(x: np.ndarray, alpha: float) -> np.ndarray:
    """
    y[i] = alpha*x[i] + (1-alpha)*y[i-1]
    alpha near 0 => smoother.
    """
    alpha = float(max(0.0, min(1.0, alpha)))
    y = x.astype(np.float32).copy()
    for i in range(1, len(y)):
        y[i] = alpha * y[i] + (1.0 - alpha) * y[i - 1]
    return y


def _periodic_resample(x_bins: np.ndarray, n: int, warp: np.ndarray | None = None) -> np.ndarray:
    """
    Resample a periodic signal defined on bins to length n.
    Optional warp in [0..1) length n provides non-linear scan positions.
    """
    bins = len(x_bins)
    x_ext = np.concatenate([x_bins, x_bins[:1]]).astype(np.float32)

    if warp is None:
        u = (np.arange(n, dtype=np.float32) / float(n)).astype(np.float32)
    else:
        if len(warp) != n:
            warp = np.resize(warp.astype(np.float32), n)
        u = warp.astype(np.float32)

    pos = u * bins
    i0 = np.floor(pos).astype(np.int32)
    frac = (pos - i0).astype(np.float32)
    i0 = np.clip(i0, 0, bins - 1)
    return (x_ext[i0] * (1.0 - frac) + x_ext[i0 + 1] * frac).astype(np.float32)


def _mixer_channels() -> tuple[int, int]:
    """
    Returns (sr, channels) from pygame mixer if initialized, else sensible defaults.
    """
    mix = pygame.mixer.get_init()
    if not mix:
        return 44100, 2
    sr, _fmt, ch = mix
    return int(sr), int(ch)


# -----------------------------
# Synthesis
# -----------------------------

def synth_rune_drone(pattern, cfg: RuneAudioConfig) -> tuple[pygame.mixer.Sound | None, str]:
    """
    Returns (Sound or None, signature).
    """
    sig = _signature(pattern)

    try:
        lengths = _edge_lengths(pattern)
        if not lengths:
            return None, sig

        sr, mix_ch = _mixer_channels()
        rng = _rng_from_sig(sig)

        # --- Complexity proxies ---
        sharp = float(_angle_sharpness(pattern))
        complexity = min(1.0, len(lengths) / 64.0)

        # --- Loop length quantization ---
        # Still quantized for now; we can continuous-ify later if desired.
        if complexity < 0.22:
            T = 4.0
        elif complexity < 0.70:
            T = 8.0
        else:
            T = 16.0
        if cfg.dur_s >= 12.0:
            T = 16.0
        elif cfg.dur_s <= 5.5:
            T = 4.0

        n = int(max(2048, sr * T))

        # --- Base frequency (geometry) ---
        f0 = float(_base_freq_from_lengths(lengths, cfg))

        # --- Curvature controls ---
        pts = _edges_ordered_polyline(pattern)
        k_bins, total_len = _curvature_signal_along_path(pts, bins=2048)
        if total_len <= 1e-9:
            k_bins = np.zeros(2048, dtype=np.float32)

        smear = 0.65 + 0.20 * (1.0 - sharp)
        smear = float(max(0.0, min(1.0, smear)))

        k0 = _circular_convolve(k_bins, _gaussian_kernel(80.0 * smear + 20.0))
        k1 = _circular_convolve(k_bins, _gaussian_kernel(22.0 * smear + 6.0))
        k2 = _circular_convolve(k_bins, _gaussian_kernel(6.0 * smear + 2.0))
        k3 = _circular_convolve(k_bins, _gaussian_kernel(2.0 * smear + 1.0))

        c0 = _normalize01(k0)
        c1 = _normalize01(k1)
        c2 = _normalize01(k2)
        c3 = _normalize01(k3)

        # Gentle time warp to avoid a single global clock.
        warp_strength = 0.08 + 0.10 * complexity
        w_bins = (c0 - 0.5).astype(np.float32)
        w_bins = _lowpass_1pole(w_bins, 0.08 + 0.12 * smear)

        u0 = np.arange(n, dtype=np.float32) / float(n)
        w = _periodic_resample(w_bins, n)
        warp = (u0 + warp_strength * w) % 1.0

        C0 = _periodic_resample(c0, n, warp=warp)
        C1 = _periodic_resample(c1, n, warp=warp)
        C2 = _periodic_resample(c2, n, warp=warp)
        C3 = _periodic_resample(c3, n, warp=warp)

        C0 = _lowpass_1pole(C0, 0.02 + 0.10 * smear)
        C1 = _lowpass_1pole(C1, 0.03 + 0.12 * smear)
        C2 = _lowpass_1pole(C2, 0.06 + 0.14 * smear)
        C3 = _lowpass_1pole(C3, 0.10 + 0.20 * smear)

        Z0 = (C0 - float(C0.mean())).astype(np.float32)
        Z1 = (C1 - float(C1.mean())).astype(np.float32)
        Z2 = (C2 - float(C2.mean())).astype(np.float32)
        Z3 = (C3 - float(C3.mean())).astype(np.float32)

        # --- Color signal along path (multiscale, analogous to curvature) ---
        ratio_bins, _color_total = _color_ratio_signal_along_path(pattern, bins=2048)
        log_ratio_bins = np.log(np.maximum(1e-6, ratio_bins)).astype(np.float32)

        lr0 = _circular_convolve(log_ratio_bins, _gaussian_kernel(120.0 * smear + 24.0))
        lr1 = _circular_convolve(log_ratio_bins, _gaussian_kernel(34.0 * smear + 8.0))
        lr2 = _circular_convolve(log_ratio_bins, _gaussian_kernel(10.0 * smear + 3.0))
        lr3 = _circular_convolve(log_ratio_bins, _gaussian_kernel(3.0 * smear + 1.0))

        LR0 = _periodic_resample(lr0, n, warp=warp)
        LR1 = _periodic_resample(lr1, n, warp=warp)
        LR2 = _periodic_resample(lr2, n, warp=warp)
        LR3 = _periodic_resample(lr3, n, warp=warp)

        # Bouba vs kiki articulation:
        # rounded runes glide; angular runes step between just centers.
        glide_amt = float(max(0.0, min(1.0, 1.0 - 1.15 * sharp)))
        step_amt = float(max(0.0, min(1.0, 1.35 * sharp - 0.15)))

        LR1_smooth = _lowpass_1pole(LR1, 0.01 + 0.05 * smear)
        LR2_smooth = _lowpass_1pole(LR2, 0.03 + 0.10 * smear)

        allowed_ratios = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
        win = max(32, int(n / (18.0 + 24.0 * complexity)))
        LRstep = _snap_log_ratio_windows(LR1 + 0.65 * LR2, win, allowed_ratios)

        color_log = (
            0.22 * LR0 +                       # global color mood / mode
            glide_amt * (0.95 * LR1_smooth + 0.30 * LR2_smooth) +
            step_amt * (0.75 * LRstep) +
            0.10 * LR3                         # micro ornament only
        ).astype(np.float32)

        color_log = _lowpass_1pole(color_log, 0.02 + 0.08 * smear)
        color_ratio_t = np.exp(color_log).astype(np.float32)

        # modest global fold into sane octave vicinity
        mean_ratio = float(np.exp(np.mean(color_log)))
        while f0 * mean_ratio < cfg.base_freq_min:
            mean_ratio *= 2.0
        while f0 * mean_ratio > cfg.base_freq_max:
            mean_ratio *= 0.5

        color_ratio_t *= mean_ratio / max(1e-6, float(np.mean(color_ratio_t)))

        f0_t = (f0 * color_ratio_t).astype(np.float32)
        f0_t = np.clip(f0_t, cfg.base_freq_min * 0.7, cfg.base_freq_max * 1.8).astype(np.float32)

        # --- Harmonic ladder ---
        N = int(12 + 44 * complexity)
        N = int(max(8, min(int(cfg.max_partials), N)))

        roll = 1.15 + 0.55 * sharp
        base_w = (1.0 / (np.arange(1, N + 1, dtype=np.float32) ** roll)).astype(np.float32)
        base_w /= max(1e-9, float(base_w.max()))

        mu_min = 1.5
        mu_max = max(mu_min + 1.0, 0.65 * N)
        mu = (mu_min + (mu_max - mu_min) * _normalize01(C1)).astype(np.float32)

        sigma = (3.5 - 2.4 * complexity) * (0.85 + 0.6 * smear)
        sigma = float(max(0.9, sigma))
        spot_gain = 0.35 + 0.95 * complexity

        breath_amt = 0.05 + 0.15 * (complexity ** 0.8)
        breath = (1.0 + breath_amt * Z0).astype(np.float32)
        breath = np.clip(breath, 0.7, 1.3)

        # Geometry drift remains, but color now supplies the main melodic contour.
        cents = (1.5 + 7.0 * complexity) * (0.35 + 0.65 * (1.0 - smear))
        drift = (cents / 1200.0) * Z0
        f_main = (f0_t * (2.0 ** drift)).astype(np.float32)

        base_phase = np.cumsum((2.0 * math.pi * f_main / float(sr)).astype(np.float32))
        base_phase += float(rng.random() * 2.0 * math.pi)

        shimmer_amt = 0.03 + 0.10 * (complexity ** 1.1)
        shimmer_amt *= (0.35 + 0.65 * sharp)
        shimmer_amt = float(min(0.11, shimmer_amt))

        tilt = (0.12 * Z1 + 0.06 * Z2).astype(np.float32)
        tilt = np.clip(tilt, -0.35, 0.35)

        # Additional color spectral temperature:
        # warmer colors favor lower partials; cooler colors favor upper ones.
        color_brightness = np.clip(
            0.35 * LR1 + 0.20 * LR2,
            math.log(0.85),
            math.log(1.65),
        ).astype(np.float32)

        y = np.zeros(n, dtype=np.float32)

        for idx in range(1, N + 1):
            nn = (idx - 1) / max(1.0, (N - 1))

            W = np.exp(-((float(idx) - mu) ** 2) / (2.0 * sigma * sigma)).astype(np.float32)

            a = base_w[idx - 1] * (1.0 + spot_gain * W)
            a = a * (1.0 + tilt * (0.2 + 0.8 * nn))
            a = a * (1.0 + shimmer_amt * (nn ** 1.8) * Z3)

            # color overtone "temperature"
            temp = np.exp(color_brightness * (-0.55 + 1.10 * nn)).astype(np.float32)
            a = a * temp

            a_t = (a * breath).astype(np.float32)

            det = float(rng.normal(0.0, 1.0)) * 0.0015 * (0.2 + 0.8 * complexity) * (0.3 + 0.7 * nn)
            harm_phase = (float(idx) * (1.0 + det)) * base_phase + float(rng.random() * 2.0 * math.pi)
            y += a_t * np.sin(harm_phase).astype(np.float32)

        pk = float(np.max(np.abs(y)))
        if pk > 1e-6:
            y /= pk

        loud = (0.50 + 0.80 * (complexity ** 0.9))
        color_richness = float(np.std(log_ratio_bins))
        loud *= (1.0 + 0.18 * min(1.0, 3.0 * color_richness))
        y *= float(cfg.volume) * loud

        y = _lowpass_1pole(y, 0.08 + 0.20 * smear)

        fade = int(sr * (cfg.fade_ms / 1000.0))
        fade = max(1, min(fade, n // 12))
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        y[:fade] *= ramp
        y[-fade:] *= ramp[::-1]

        if mix_ch <= 1:
            pcm = np.clip(y, -1.0, 1.0)
            pcm16 = (pcm * 32767.0).astype(np.int16)
        else:
            pcm = np.clip(y, -1.0, 1.0).astype(np.float32)
            pcmN = np.repeat(pcm[:, None], mix_ch, axis=1)
            pcm16 = (pcmN * 32767.0).astype(np.int16)

        pcm16 = np.ascontiguousarray(pcm16)

        try:
            snd = pygame.sndarray.make_sound(pcm16)
        except Exception as ex:
            _log_exc("[synth_rune_drone] make_sound failed:", ex)
            return None, sig


        return snd, sig

    except Exception as ex:
        _log_exc("[synth_rune_drone]", ex)
        return None, sig


# -----------------------------
# Runtime hook
# -----------------------------

def sync_rune_drone(game, audio_manager, *, cfg: RuneAudioConfig | None = None) -> None:
    """
    Cheap per-frame sync:
      - compute signature of current rune pattern
      - if it changed, resynth + loop it on a dedicated SFX channel.
    """
    if cfg is None:
        cfg = RuneAudioConfig()

    try:
        lvl = game._level()
        pattern = getattr(lvl, "pattern", None)
    except Exception:
        return

    if pattern is None:
        try:
            audio_manager.stop_sfx("rune_drone")
        except Exception:
            pass
        return

    verts = getattr(pattern, "vertices", None) or []
    edges = getattr(pattern, "edges", None) or []
    if not verts or not edges:
        try:
            audio_manager.stop_sfx("rune_drone")
        except Exception:
            pass
        return

    sig = _signature(pattern)
    prev = getattr(game, "_rune_audio_sig", None)


    snd, sig2 = synth_rune_drone(pattern, cfg)
    setattr(game, "_rune_audio_sig", sig2)

    if snd is None:
        try:
            audio_manager.stop_sfx("rune_drone")
        except Exception:
            pass
        return

    try:
        audio_manager.play_sfx_loop(
            "rune_drone",
            snd,
            sig=sig2,
            channel_index=int(cfg.channel_index),
            volume=1.0,
        )
    except Exception as ex:
        _log_exc("[sync_rune_drone] play_sfx_loop failed:", ex)