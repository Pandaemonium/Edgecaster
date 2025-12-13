from __future__ import annotations

"""
edgecaster/visual_effects.py

A unified, compoundable visual effects system.

Key idea: everything is a "VisualEffect" by name, whether it means:
- a geometric transform (clockwise, mirror, ghostly translucency) which contributes
  to a VisualProfile (see visuals.py), OR
- a draw-style modifier (fiery flicker, bismuth glint), OR
- a screen-space present modifier (shake, vibrate), possibly time-based.

Scenes/entities simply *declare* effect names (e.g. ["clockwise","fiery"]).
The actual math/behavior lives here, in a registry.

This is a first design pass: intentionally lightweight, meant to replace
scene-specific spaghetti like inventory_scene.maybe_apply_clockwise.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import random
import pygame

from edgecaster.visuals import VisualProfile

RGB = Tuple[int, int, int]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _clamp_u8(x: float) -> int:
    return max(0, min(255, int(x)))


def _mul_rgb(col: RGB, m: float) -> RGB:
    return (_clamp_u8(col[0] * m), _clamp_u8(col[1] * m), _clamp_u8(col[2] * m))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    return (
        _clamp_u8(_lerp(a[0], b[0], t)),
        _clamp_u8(_lerp(a[1], b[1], t)),
        _clamp_u8(_lerp(a[2], b[2], t)),
    )


def concat_effect_names(*lists: Iterable[str]) -> List[str]:
    """Concatenate effect names in order (keeps duplicates for compounding)."""
    out: List[str] = []
    for lst in lists:
        for name in lst:
            if name:
                out.append(str(name))
    return out


def merge_unique_effect_names(*lists: Iterable[str]) -> List[str]:
    """Merge lists while preserving order but removing duplicates (boolean-style)."""
    out: List[str] = []
    seen = set()
    for lst in lists:
        for name in lst:
            if not name:
                continue
            name = str(name)
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# Effect registry: each effect name may implement multiple hooks
# ---------------------------------------------------------------------------

# Geometry hook: contributes to a VisualProfile
ProfileHook = Callable[[VisualProfile, int], VisualProfile]

# Style hook: modifies entity draw color (future: glyph/sprite, alpha, etc.)
ColorHook = Callable[[Any, RGB, int], RGB]

# Present hook: modifies the final blit rect (shake/jitter)
PresentRectHook = Callable[[pygame.Rect, int], pygame.Rect]


@dataclass
class VisualEffectDef:
    """Definition registered under a name."""
    name: str
    modify_profile: Optional[ProfileHook] = None
    modify_entity_color: Optional[ColorHook] = None
    # NOTE: present rect hooks are typically stateful; we handle those via
    # VisualEffectManager impulses instead of static defs.
    # Keeping this field for future "pure" present hooks if needed.
    modify_present_rect: Optional[PresentRectHook] = None


EFFECTS: Dict[str, VisualEffectDef] = {}


def register_effect(defn: VisualEffectDef) -> None:
    EFFECTS[defn.name] = defn


def get_effect(name: str) -> Optional[VisualEffectDef]:
    return EFFECTS.get(name)


# ---------------------------------------------------------------------------
# Tag/metadata resolution (legacy compatibility lives here, not in scenes)
# ---------------------------------------------------------------------------

LEGACY_TAG_TO_EFFECTS: Dict[str, List[str]] = {
    # Old inventory-specific tags → new unified names
    "clockwise_inventory": ["clockwise"],
    "ghostly_inventory": ["ghostly"],
    # If you later add "smoky_inventory" etc, map them here.
}


def effect_names_from_tags(tags: Dict[str, Any]) -> List[str]:
    """Extract effect names from a tags dict, including legacy compatibility."""
    if not tags:
        return []

    # Preferred modern forms
    ve = tags.get("visual_effects")
    if isinstance(ve, str) and ve:
        return [ve]
    if isinstance(ve, (list, tuple)):
        return [str(x) for x in ve if x]

    # Alternate field some code may use
    v = tags.get("visual")
    if isinstance(v, str) and v:
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x]

    # Legacy boolean tags
    out: List[str] = []
    for tag, mapped in LEGACY_TAG_TO_EFFECTS.items():
        if tags.get(tag):
            out.extend(mapped)
    return out


def effect_names_from_obj(obj: Any) -> List[str]:
    """
    Best-effort extraction from an object:
    - obj.visual_effects (preferred)
    - obj.tags['visual_effects'] / obj.tags['visual']
    - legacy tags mapping
    """
    if obj is None:
        return []
    ve = getattr(obj, "visual_effects", None)
    if isinstance(ve, str) and ve:
        return [ve]
    if isinstance(ve, (list, tuple)):
        return [str(x) for x in ve if x]

    tags = getattr(obj, "tags", None) or {}
    if isinstance(tags, dict):
        return effect_names_from_tags(tags)
    return []


# ---------------------------------------------------------------------------
# Geometry lane: build a VisualProfile from effect names
# ---------------------------------------------------------------------------

def build_visual_profile(
    base: Optional[VisualProfile],
    effect_names: Iterable[str],
    now_ms: Optional[int] = None,
) -> VisualProfile:
    """
    Apply all registered modify_profile hooks in order to produce a VisualProfile.
    """
    profile = base or VisualProfile()
    t = pygame.time.get_ticks() if now_ms is None else int(now_ms)

    for name in effect_names:
        eff = get_effect(name)
        if eff and eff.modify_profile:
            profile = eff.modify_profile(profile, t)

    return profile


# ---------------------------------------------------------------------------
# Style lane: entity color modifiers
# ---------------------------------------------------------------------------

def apply_entity_color_effects(
    ent: Any,
    base_color: RGB,
    effect_names: Iterable[str],
    now_ms: Optional[int] = None,
) -> RGB:
    col = base_color
    t = pygame.time.get_ticks() if now_ms is None else int(now_ms)
    for name in effect_names:
        eff = get_effect(name)
        if eff and eff.modify_entity_color:
            col = eff.modify_entity_color(ent, col, t)
    return col


# ---------------------------------------------------------------------------
# Present lane: stateful impulses (shake, vibrate) owned by renderer
# ---------------------------------------------------------------------------

@dataclass
class ShakeImpulse:
    remaining_ms: int
    duration_ms: int
    amplitude_px: float

    def update(self, dt_ms: int) -> None:
        self.remaining_ms = max(0, self.remaining_ms - int(dt_ms))

    @property
    def expired(self) -> bool:
        return self.remaining_ms <= 0

    def apply(self, rect: pygame.Rect) -> pygame.Rect:
        frac = max(0.0, min(1.0, self.remaining_ms / max(1, self.duration_ms)))
        amp = self.amplitude_px * frac
        dx = int(random.uniform(-amp, amp))
        dy = int(random.uniform(-amp, amp))
        r = rect.copy()
        r.x += dx
        r.y += dy
        return r


@dataclass
class VibrateEffectState:
    """Persistent jitter (never expires)."""
    amplitude_px: float = 2.0
    period_ms: int = 80

    def apply(self, rect: pygame.Rect, now_ms: int) -> pygame.Rect:
        # pseudo oscillation: square-ish jitter based on phase
        phase = (now_ms % max(1, self.period_ms)) / max(1, self.period_ms)
        # map phase to [-1, 1]
        s = 1.0 if phase < 0.5 else -1.0
        dx = int(s * self.amplitude_px)
        dy = int((-s) * self.amplitude_px)
        r = rect.copy()
        r.x += dx
        r.y += dy
        return r


@dataclass
class VisualEffectManager:
    """
    Renderer-owned state:
    - global_effects: effect names applied everywhere (mirror curse, global ghostly, etc.)
      (Geometry+style; for present effects use vibrate/shake below.)
    - impulses: transient present effects like yawp shake
    - vibrate: optional persistent present jitter
    """
    global_effects: List[str] = field(default_factory=list)
    impulses: List[ShakeImpulse] = field(default_factory=list)
    vibrate: Optional[VibrateEffectState] = None

    _last_tick_ms: int = 0

    def set_global_effects(self, names: Iterable[str]) -> None:
        self.global_effects = list(names)

    def add_global_effects(self, names: Iterable[str]) -> None:
        # Global effects are generally boolean/presence-style (don't duplicate).
        self.global_effects = merge_unique_effect_names(self.global_effects, list(names))


    # ---- triggers ---------------------------------------------------------

    def trigger_shake(self, amplitude_px: float = 12.0, duration_ms: int = 250) -> None:
        self.impulses.append(
            ShakeImpulse(
                remaining_ms=int(duration_ms),
                duration_ms=max(1, int(duration_ms)),
                amplitude_px=float(amplitude_px),
            )
        )

    def enable_vibrate(self, amplitude_px: float = 2.0, period_ms: int = 80) -> None:
        self.vibrate = VibrateEffectState(amplitude_px=float(amplitude_px), period_ms=int(period_ms))

    def disable_vibrate(self) -> None:
        self.vibrate = None

    # ---- update / apply ---------------------------------------------------

    def update(self, now_ms: Optional[int] = None) -> None:
        now = pygame.time.get_ticks() if now_ms is None else int(now_ms)
        if self._last_tick_ms == 0:
            self._last_tick_ms = now
            return
        dt = max(0, now - self._last_tick_ms)
        self._last_tick_ms = now

        for imp in self.impulses:
            imp.update(dt)
        self.impulses = [imp for imp in self.impulses if not imp.expired]

    def apply_present_rect(self, rect: pygame.Rect, now_ms: Optional[int] = None) -> pygame.Rect:
        now = pygame.time.get_ticks() if now_ms is None else int(now_ms)
        r = rect
        # persistent vibrate first, then decaying shake on top
        if self.vibrate is not None:
            r = self.vibrate.apply(r, now)
        for imp in self.impulses:
            r = imp.apply(r)
        return r


# ---------------------------------------------------------------------------
# Built-in effects
# ---------------------------------------------------------------------------

def _clockwise_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Historical behavior: -5 degrees
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle - 5.0,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _ghostly_profile(p: VisualProfile, t: int) -> VisualProfile:
    new_alpha = max(0.05, min(1.0, p.alpha * 0.6))
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=new_alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _mirror_x_profile(p: VisualProfile, t: int) -> VisualProfile:
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=p.alpha,
        flip_x=not p.flip_x,
        flip_y=p.flip_y,
    )


def _mirror_y_profile(p: VisualProfile, t: int) -> VisualProfile:
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=not p.flip_y,
    )


def _fiery_color(ent: Any, base: RGB, t: int) -> RGB:
    a = (255, 110, 50)
    b = (255, 200, 80)
    phase = (t % 140) / 140.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    hot = _lerp_rgb(a, b, tri)
    return _lerp_rgb(base, hot, 0.55)


def _ghostly_color(ent: Any, base: RGB, t: int) -> RGB:
    phase = (t % 1200) / 1200.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    pale = _lerp_rgb(base, (210, 220, 255), 0.40)
    return _mul_rgb(pale, 0.70 + 0.20 * tri)


def _bismuth_color(ent: Any, base: RGB, t: int) -> RGB:
    targets: List[RGB] = [(180, 255, 220), (180, 220, 255), (255, 200, 240)]
    idx = (t // 250) % len(targets)
    nxt = (idx + 1) % len(targets)
    local_t = (t % 250) / 250.0
    target = _lerp_rgb(targets[idx], targets[nxt], local_t)
    return _lerp_rgb(base, target, 0.35)


def _install_builtin_effects() -> None:
    register_effect(VisualEffectDef("clockwise", modify_profile=_clockwise_profile))
    register_effect(VisualEffectDef("ghostly", modify_profile=_ghostly_profile, modify_entity_color=_ghostly_color))
    register_effect(VisualEffectDef("mirror_x", modify_profile=_mirror_x_profile))
    register_effect(VisualEffectDef("mirror_y", modify_profile=_mirror_y_profile))
    register_effect(VisualEffectDef("fiery", modify_entity_color=_fiery_color))
    register_effect(VisualEffectDef("bismuth", modify_entity_color=_bismuth_color))


_install_builtin_effects()
