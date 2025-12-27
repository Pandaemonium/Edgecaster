# edgecaster/biome.py
from __future__ import annotations

from typing import Optional, Tuple, Dict, Sequence

import numpy as np


# Biome index convention:
# 0 water "~"
# 1 shore ","
# 2 plains "."
# 3 forest "T"
# 4 hills "^"
# 5 mountains "#"

BIOME_CHARS: Tuple[str, ...] = ("~", ",", ".", "T", "^", "#")
BIOME_GLYPHS_NP = np.asarray([ord(c) for c in BIOME_CHARS], dtype=np.int16)

# These color anchors intentionally match the palette used inside overmap_accel
# (so global tiles derived from overmap buffers stay consistent with local mapgen).
ANCHORS_RGB: Tuple[Tuple[int, int, int], ...] = (
    (50, 90, 170),    # deep water
    (110, 160, 190),  # shore/shallows
    (150, 200, 140),  # plains
    (90, 170, 110),   # forested low hills
    (170, 150, 110),  # hills
    (210, 210, 215),  # high/mountains
)

BIOME_GLYPHS: Dict[int, str] = {i: BIOME_CHARS[i] for i in range(len(BIOME_CHARS))}
BIOME_COLORS: Dict[int, Tuple[int, int, int]] = {i: ANCHORS_RGB[i] for i in range(len(ANCHORS_RGB))}


def classify_biome_idx(height_norm: np.ndarray, moisture_norm: Optional[np.ndarray] = None) -> np.ndarray:
    """Vectorized biome classification matching mapgen thresholds (0..5)."""
    h = height_norm
    m = moisture_norm if moisture_norm is not None else h

    out = np.empty(h.shape, dtype=np.uint8)
    out[:] = 2  # plains default
    out[h < 0.16] = 0
    out[(h >= 0.16) & (h < 0.24)] = 1

    low = (h >= 0.24) & (h < 0.68)
    out[low & (m > 0.64)] = 3

    out[(h >= 0.68) & (h < 0.82)] = 4
    out[h >= 0.82] = 5
    return out


def classify_tile(height: float, moisture: float, noise: float) -> Tuple[str, bool]:
    """Scalar classification matching mapgen._classify_tile behavior."""
    h = float(height)
    m = float(moisture)

    if h < 0.16:
        return "~", False
    if h < 0.24:
        return ",", True
    if h < 0.68:
        if m > 0.64:
            return ("T", noise > 0.015)
        if m < 0.28:
            return ("." if noise > 0.05 else ",", True)
        return ".", True
    if h < 0.82:
        return ("^", noise > 0.05)
    return ("#", noise > 0.35)


def biome_idx_from_rgb(rgb: Tuple[int, int, int]) -> int:
    """Map an overmap_accel RGB sample to a biome index (0..5) by nearest palette anchor."""
    r, g, b = rgb
    best_i = 0
    best_d = 10**18
    for i, (ar, ag, ab) in enumerate(ANCHORS_RGB):
        dr = r - ar
        dg = g - ag
        db = b - ab
        d = dr * dr + dg * dg + db * db
        if d < best_d:
            best_d = d
            best_i = i
    return int(best_i)


def biome_char_from_rgb(rgb: Tuple[int, int, int]) -> str:
    return BIOME_GLYPHS.get(biome_idx_from_rgb(rgb), ".")


def biome_color_from_rgb(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return BIOME_COLORS.get(biome_idx_from_rgb(rgb), (220, 230, 240))
