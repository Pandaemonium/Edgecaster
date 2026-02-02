"""Consolidated math utilities for Edgecaster.

This module provides common mathematical helper functions used throughout
the codebase for interpolation, clamping, and easing. Using these centralized
implementations avoids duplication and ensures consistent behavior.

Usage:
    from edgecaster.math_utils import lerp, smoothstep, clamp, lerp_rgb
"""

from typing import Tuple

# Type alias for RGB tuples
RGB = Tuple[int, int, int]


# =============================================================================
# CLAMPING FUNCTIONS
# =============================================================================

def clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value to the range [lo, hi].

    Args:
        val: Value to clamp
        lo: Lower bound (default 0.0)
        hi: Upper bound (default 1.0)

    Returns:
        Value clamped to [lo, hi]
    """
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def clamp01(x: float) -> float:
    """Clamp a value to the range [0.0, 1.0].

    Optimized version of clamp() for the common 0..1 case.
    """
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def clamp_u8(x: float) -> int:
    """Clamp a value to the range [0, 255] and convert to int.

    Useful for RGB color component values.
    """
    return max(0, min(255, int(x)))


# =============================================================================
# INTERPOLATION FUNCTIONS
# =============================================================================

def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between two values.

    Args:
        a: Start value (returned when t=0)
        b: End value (returned when t=1)
        t: Interpolation factor (typically 0..1, but not clamped)

    Returns:
        Interpolated value: a + (b - a) * t
    """
    return a + (b - a) * t


def lerp_rgb(c1: RGB, c2: RGB, t: float) -> RGB:
    """Linear interpolation between two RGB colors.

    Args:
        c1: Start color (r, g, b) - returned when t=0
        c2: End color (r, g, b) - returned when t=1
        t: Interpolation factor (clamped to 0..1)

    Returns:
        Interpolated color with components clamped to 0..255
    """
    t = clamp01(t)
    return (
        clamp_u8(lerp(c1[0], c2[0], t)),
        clamp_u8(lerp(c1[1], c2[1], t)),
        clamp_u8(lerp(c1[2], c2[2], t)),
    )


# =============================================================================
# EASING FUNCTIONS
# =============================================================================

def smoothstep(t: float) -> float:
    """Smooth easing function for animations (Hermite interpolation).

    Maps input t from [0, 1] to a smooth S-curve output in [0, 1].
    The function has zero first derivatives at t=0 and t=1.

    Args:
        t: Input value (clamped to 0..1)

    Returns:
        Smoothly interpolated value: 3t² - 2t³
    """
    t = clamp01(t)
    return t * t * (3.0 - 2.0 * t)


def smoothstep_range(edge0: float, edge1: float, x: float) -> float:
    """Smoothstep with custom input range.

    Maps x from [edge0, edge1] to a smooth S-curve output in [0, 1].

    Args:
        edge0: Lower edge of input range (returns 0)
        edge1: Upper edge of input range (returns 1)
        x: Input value

    Returns:
        0 if x <= edge0
        1 if x >= edge1
        Smooth interpolation between 0 and 1 otherwise
    """
    if edge0 == edge1:
        return 1.0 if x >= edge1 else 0.0
    t = clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)
