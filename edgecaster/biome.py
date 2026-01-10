# edgecaster/biome.py
"""
Biome classification and rendering for Edgecaster.

This module provides backwards-compatible biome classification while
leveraging the new climate-aware system from edgecaster.climate.

Legacy 6-biome system (for height-only classification):
    0=water, 1=shore, 2=plains, 3=forest, 4=hills, 5=mountains

New 21-biome system (for full climate classification):
    See edgecaster.climate.Biome for the full enum.
"""
from __future__ import annotations

from typing import Optional, Tuple, Dict, Sequence

import numpy as np

# Import the new climate-aware biome system
from edgecaster.climate import (
    Biome,
    BIOME_COLORS as CLIMATE_BIOME_COLORS,
    BIOME_GLYPHS as CLIMATE_BIOME_GLYPHS,
    BIOME_SHORT_NAMES,
    ClimateConfig,
    classify_biome as climate_classify_biome,
    biome_to_color,
    biome_to_glyph,
)


# -----------------------------
# Legacy 6-biome system (backwards compatibility)
# -----------------------------

# Legacy biome index convention:
# 0 water "~"
# 1 shore ","
# 2 plains "."
# 3 forest "T"
# 4 hills "^"
# 5 mountains "#"

BIOME_CHARS: Tuple[str, ...] = ("~", ",", ".", "T", "^", "#")
BIOME_GLYPHS_NP = np.asarray([ord(c) for c in BIOME_CHARS], dtype=np.int16)

# Legacy color anchors (used by height-based interpolation in overmap_accel)
ANCHORS_RGB: Tuple[Tuple[int, int, int], ...] = (
    (50, 90, 170),    # deep water
    (110, 160, 190),  # shore/shallows
    (150, 200, 140),  # plains
    (90, 170, 110),   # forested low hills
    (170, 150, 110),  # hills
    (210, 210, 215),  # high/mountains
)

# Legacy biome glyphs (by index)
BIOME_GLYPHS: Dict[int, str] = {i: BIOME_CHARS[i] for i in range(len(BIOME_CHARS))}

# Legacy biome colors (by index) - backwards compatible
BIOME_COLORS: Dict[int, Tuple[int, int, int]] = {i: ANCHORS_RGB[i] for i in range(len(ANCHORS_RGB))}

# Extend with new climate biome colors (using Biome enum values as keys)
for b, color in CLIMATE_BIOME_COLORS.items():
    BIOME_COLORS[int(b)] = color


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
