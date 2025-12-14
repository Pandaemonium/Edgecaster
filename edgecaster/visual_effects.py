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
import math
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


# Overlay hook: draw extra pixels onto an already-rendered surface
# (embers, shimmer bands, scanlines, smoke, etc.)
OverlayHook = Callable[[Any, pygame.Surface, pygame.Rect, int], None]
# Overlay bounds hook: choose where this effect is allowed to draw, in LOCAL coords.
# base_rect is typically the tile rect (0,0,w,h) or panel rect.
OverlayRectHook = Callable[[Any, pygame.Rect, int], pygame.Rect]


@dataclass
class VisualEffectDef:
    """Definition registered under a name."""
    name: str
    modify_profile: Optional[ProfileHook] = None
    modify_entity_color: Optional[ColorHook] = None
    modify_present_rect: Optional[PresentRectHook] = None
    # Optional: tells renderer what LOCAL rect this overlay may draw into.
    # Can extend beyond base_rect (e.g. smoke rising above a tile).
    overlay_rect: Optional[OverlayRectHook] = None

    draw_overlay: Optional[OverlayHook] = None



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
    "jittery_inventory": ["jittery"],

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

def apply_screen_tint_overlays(
    obj: Any,
    surface: pygame.Surface,
    effect_names: Iterable[str],
    *,
    base_color: RGB = (128, 128, 128),
    alpha: int = 70,
    now_ms: Optional[int] = None,
) -> None:
    """
    Screen-space tint lane.

    Convert any modify_entity_color hooks into a translucent full-surface tint overlay.

    Important:
    - If no modify_entity_color hooks actually run, we DO NOTHING (prevents "white wash").
    - base_color defaults to mid-gray so lerps can go darker as well as lighter.
    """
    if not effect_names:
        return

    t = pygame.time.get_ticks() if now_ms is None else int(now_ms)

    tint = base_color
    touched = False

    for name in effect_names:
        eff = get_effect(name)
        if eff and eff.modify_entity_color:
            touched = True
            try:
                tint = eff.modify_entity_color(obj, tint, t)
            except Exception:
                pass

    # If nothing had a color hook, or it effectively didn't change, don't overlay.
    if not touched or tint == base_color:
        return

    a = max(0, min(255, int(alpha)))
    if a <= 0:
        return

    overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    overlay.fill((int(tint[0]), int(tint[1]), int(tint[2]), a))
    surface.blit(overlay, (0, 0))



def apply_surface_overlays(
    obj: Any,
    surface: pygame.Surface,
    rect: pygame.Rect,
    effect_names: Iterable[str],
    now_ms: Optional[int] = None,
    rect_by_name: Optional[dict[str, pygame.Rect]] = None,
) -> None:
    """
    Overlay lane: draw particles/shimmer/etc onto an existing surface.
    If rect_by_name is provided, each effect gets its own local rect.
    """
    t = pygame.time.get_ticks() if now_ms is None else int(now_ms)
    for name in effect_names:
        eff = get_effect(name)
        if eff and eff.draw_overlay:
            r = rect_by_name.get(name, rect) if rect_by_name else rect
            eff.draw_overlay(obj, surface, r, t)


def compute_overlay_union_rect(
    obj: Any,
    base_rect: pygame.Rect,
    effect_names: Iterable[str],
    now_ms: Optional[int] = None,
) -> tuple[pygame.Rect, dict[str, pygame.Rect]]:
    """
    Returns:
      (union_rect, rect_by_name)
    Where rect_by_name gives each effect's overlay rect in the SAME coord system as base_rect.
    """
    t = pygame.time.get_ticks() if now_ms is None else int(now_ms)

    union = base_rect.copy()
    rect_by_name: dict[str, pygame.Rect] = {}

    for name in effect_names:
        eff = get_effect(name)
        r = base_rect
        if eff and eff.overlay_rect:
            try:
                r = eff.overlay_rect(obj, base_rect, t)
            except Exception:
                r = base_rect
        rect_by_name[name] = r
        union = union.union(r)

    return union, rect_by_name


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
def _vibrate_offset(now_ms: int, amplitude_px: float, period_ms: int) -> tuple[int, int]:
    """Shared vibrate math: returns (dx, dy) jitter."""
    # pseudo oscillation: square-ish jitter based on phase
    period = max(1, int(period_ms))
    phase = (now_ms % period) / period
    # map phase to [-1, 1]
    s = (phase * 2.0) - 1.0
    dx = int(s * float(amplitude_px))
    dy = int((-s) * float(amplitude_px))
    return dx, dy


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
        dx, dy = _vibrate_offset(now_ms, self.amplitude_px, self.period_ms)
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
        self.global_effects = merge_effect_names(self.global_effects, list(names))

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


def _with_alpha_layer(surf: pygame.Surface, rect: pygame.Rect) -> pygame.Surface:
    """Return a SRCALPHA layer sized to rect; caller draws onto it then blits."""
    return pygame.Surface((max(1, rect.w), max(1, rect.h)), pygame.SRCALPHA)



def _clockwise_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Historical behavior: -5 degrees
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle - 90.0,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )
def _counter_clockwise_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Historical behavior: -5 degrees
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle + 90.0,
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

def _jittery_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Back to snappy jitter
    dx, dy = _vibrate_offset(t, amplitude_px=2.0, period_ms=80)
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x + dx,
        offset_y=p.offset_y + dy,
        angle=p.angle,
        alpha=p.alpha,
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





def _ghostly_color(ent: Any, base: RGB, t: int) -> RGB:
    phase = (t % 1200) / 1200.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    pale = _lerp_rgb(base, (210, 220, 255), 0.40)
    return _mul_rgb(pale, 0.70 + 0.20 * tri)


def _bismuth_color(ent: Any, base: RGB, t: int) -> RGB:
    # Slower hue drift (less frantic)
    targets: List[RGB] = [(180, 255, 220), (180, 220, 255), (255, 200, 240)]
    idx = (t // 450) % len(targets)
    nxt = (idx + 1) % len(targets)
    local_t = (t % 450) / 450.0
    target = _lerp_rgb(targets[idx], targets[nxt], local_t)
    return _lerp_rgb(base, target, 0.35)


def _bismuth_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Slower diagonal shimmer band + fewer, chunkier sparkles
    w, h = rect.size
    if w <= 0 or h <= 0:
        return

    phase = (t % 3200) / 3200.0  # slower sweep than 1600ms
    band_x = int((phase * (w + h)) - h)

    for i in range(-1, 2):
        x0 = rect.left + band_x + i * 7
        y0 = rect.top
        x1 = x0 + h
        y1 = rect.bottom
        col = (rng := random.Random(12345 + i)).choice([
            (180, 255, 220, 45),
            (180, 220, 255, 45),
            (255, 200, 240, 45),
        ])
        pygame.draw.aaline(surf, col, (x0, y0), (x1, y1))

    # Sparkles: fewer updates + draw as small circles instead of single pixels
    rng = random.Random(_stable_seed(obj, 9001) ^ ((t // 260) & 0xFFFFFFFF))
    s = max(2, (w * h) // 24000)
    for _ in range(s):
        x = rng.randrange(rect.left, rect.right)
        y = rng.randrange(rect.top, rect.bottom)
        r = rng.randrange(1, 3)
        col = rng.choice([
            (200, 255, 240, 110),
            (200, 230, 255, 110),
            (255, 220, 245, 110),
        ])
        pygame.draw.circle(surf, col, (x, y), r)



def _stable_seed(obj: Any, salt: int = 0) -> int:
    # Deterministic per-object-ish seed without needing obj to be hashable
    base = id(obj) if obj is not None else 0
    return (base ^ (salt * 0x9E3779B9)) & 0xFFFFFFFF


def _fiery_color(ent: Any, base: RGB, t: int) -> RGB:
    a = (255, 110, 50)
    b = (255, 200, 80)
    phase = (t % 140) / 140.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    hot = _lerp_rgb(a, b, tri)
    return _lerp_rgb(base, hot, 0.55)


def _fiery_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Tiny ember puffs; deterministic per object, animated by time buckets.
    rng = random.Random(_stable_seed(obj, 1337) ^ ((t // 90) & 0xFFFFFFFF))
    n = max(6, (rect.w * rect.h) // 8000)  # scale with area (cheap)
    for _ in range(n):
        x = rng.randrange(rect.left, rect.right)
        y = rng.randrange(rect.top, rect.bottom)
        r = rng.randrange(1, 3)
        # Ember color with slight variance
        col = (255, rng.randrange(120, 200), rng.randrange(40, 90), rng.randrange(90, 170))
        pygame.draw.circle(surf, col, (x, y), r)



def _fiery_overlay_rect(obj: Any, base: pygame.Rect, t: int) -> pygame.Rect:
    # Mostly above the tile (smoke/embers rise), with a tiny horizontal halo.
    return pygame.Rect(
        base.left - base.w // 4,
        base.top - base.h,
        base.w + base.w // 2,
        base.h * 2,
    )

def _bismuth_overlay_rect(obj: Any, base: pygame.Rect, t: int) -> pygame.Rect:
    # Uniform tiny aura around the tile.
    return base.inflate(6, 6)


# ---------------------------------------------------------------------------
# Batch 1 experimental inventory effects
# ---------------------------------------------------------------------------

def _colossal_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Big, heavy, slow bob
    bob = int(2 * ((t % 2000) / 2000.0 - 0.5))
    return VisualProfile(
        scale_x=p.scale_x * 1.5,
        scale_y=p.scale_y * 1.5,
        offset_x=p.offset_x,
        offset_y=p.offset_y + bob,
        angle=p.angle,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _smoky_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Big, slow, billowy smoke
    # - slower time bucket so it doesn't "sparkle"
    # - larger radii + softer alpha
    # - upward drift so it reads as smoke
    rng = random.Random(_stable_seed(obj, 4242) ^ ((t // 340) & 0xFFFFFFFF))

    # fewer blobs overall
    n = max(5, (rect.w * rect.h) // 18000)

    # slow rise
    rise = (t // 22) % max(1, rect.h)

    for _ in range(n):
        x = rng.randrange(rect.left, rect.right)
        # bias towards lower half, then drift upward
        y0 = rng.randrange(rect.top + rect.h // 3, rect.bottom)
        y = y0 - rise
        # wrap if needed
        while y < rect.top:
            y += rect.h

        r = rng.randrange(5, 11)
        alpha = rng.randrange(18, 55)
        pygame.draw.circle(surf, (180, 180, 180, alpha), (x, y), r)



def _smoky_overlay_rect(obj: Any, base: pygame.Rect, t: int) -> pygame.Rect:
    # Smoke rises above
    return pygame.Rect(
        base.left - base.w // 3,
        base.top - base.h,
        base.w + base.w // 1,
        base.h * 2,
    )


def _malfunctioning_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Occasional snap
    dx = random.choice([-2, -1, 1, 2]) if (t // 90) % 7 == 0 else 0
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x + dx,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _malfunctioning_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Rare horizontal tear
    if (t // 120) % 5 != 0:
        return
    y = rect.top + ((t // 7) % max(1, rect.h))
    pygame.draw.line(
        surf,
        (255, 255, 255, 80),
        (rect.left, y),
        (rect.right, y),
    )
def _carbonated_color(ent: Any, base: RGB, t: int) -> RGB:
    return _lerp_rgb(base, (170, 255, 170), 0.45)


def _carbonated_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    rng = random.Random(_stable_seed(obj, 777) ^ ((t // 260) & 0xFFFFFFFF))

    bubbles = 4
    rise = (t // 16)

    for _ in range(bubbles):
        x = rng.randrange(rect.left, rect.right)
        y = rect.bottom - ((rise + rng.randrange(0, rect.h)) % max(1, rect.h))
        r = rng.randrange(3, 7)

        # fizzy: faint green fill + white-ish outline
        pygame.draw.circle(surf, (190, 255, 190, 28), (x, y), r)
        pygame.draw.circle(surf, (245, 255, 245, 95), (x, y), r, 1)


# ---------------------------------------------------------------------------
# Batch 2 experimental inventory/menu effects (paced, readable, weird)
# ---------------------------------------------------------------------------

def _celestial_overlay_rect(obj: Any, base: pygame.Rect, t: int) -> pygame.Rect:
    # A gentle aura around the panel/tile.
    return base.inflate(18, 18)


def _celestial_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    if rect.w <= 0 or rect.h <= 0:
        return

    layer = _with_alpha_layer(surf, rect)
    # local coords on layer
    lr = layer.get_rect()

    # Deep space tint wash (very faint)
    pygame.draw.rect(layer, (10, 15, 35, 28), lr, 0)

    rng = random.Random(_stable_seed(obj, 202) ^ ((t // 900) & 0xFFFFFFFF))
    n = max(7, (rect.w * rect.h) // 26000)

    stars: list[tuple[int, int, int]] = []  # (x,y,alpha)

    for _ in range(n):
        x = rng.randrange(0, lr.w)
        y = rng.randrange(0, lr.h)

        tw = rng.randrange(0, 2000)
        phase = ((t + tw) % 5200) / 5200.0  # much slower
        tri = 1.0 - abs(phase * 2.0 - 1.0)
        alpha = int(12 + 95 * tri)

        col = (255, 245, 180, alpha)
        r = 1 if tri < 0.65 else 2
        pygame.draw.circle(layer, col, (x, y), r)

        if tri > 0.92:
            pygame.draw.line(layer, (255, 250, 210, alpha), (x - 2, y), (x + 2, y), 1)
            if rng.random() < 0.55:
                pygame.draw.line(layer, (255, 250, 210, alpha), (x, y - 2), (x, y + 2), 1)

        stars.append((x, y, alpha))

    if len(stars) >= 3 and rng.random() < 0.55:
        s1, s2, s3 = rng.sample(stars, 3)
        a = min(s1[2], s2[2], s3[2], 70)
        col = (255, 245, 190, a)
        pygame.draw.aaline(layer, col, (s1[0], s1[1]), (s2[0], s2[1]))
        pygame.draw.aaline(layer, col, (s2[0], s2[1]), (s3[0], s3[1]))

    surf.blit(layer, rect.topleft)




def _entropic_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Gentle alpha instability (not epileptic): slow noise-ish pulse
    phase = (t % 2600) / 2600.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    a = max(0.12, min(1.0, p.alpha * (0.85 - 0.18 * tri)))
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=a,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _entropic_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Decay/diffusion: motes originate near center and drift outward.
    if rect.w <= 0 or rect.h <= 0:
        return

    rng = random.Random(_stable_seed(obj, 303) ^ ((t // 260) & 0xFFFFFFFF))
    n = max(6, (rect.w * rect.h) // 22000)

    cx = rect.left + rect.w // 2
    cy = rect.top + rect.h // 2

    # radius grows with time (slow)
    R = int((t // 24) % max(1, min(rect.w, rect.h) // 2))

    for _ in range(n):
        # direction-ish in [-1,1]
        dx = rng.uniform(-1.0, 1.0)
        dy = rng.uniform(-1.0, 1.0)
        # push outward
        x = int(cx + dx * R)
        y = int(cy + dy * R)

        if not rect.collidepoint(x, y):
            continue

        r = rng.randrange(1, 3)
        alpha = rng.randrange(18, 60)
        # slightly brown/gray “dust”
        pygame.draw.circle(surf, (170, 165, 150, alpha), (x, y), r)



def _extropic_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Opposite vibe: slow "gathering" clarity (slight brightening pulse)
    phase = (t % 3200) / 3200.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    a = max(0.10, min(1.0, p.alpha * (0.90 + 0.10 * tri)))
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=a,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _extropic_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Crystalline geometric order: faint facets + lattice, slow cadence.
    if rect.w <= 0 or rect.h <= 0:
        return

    rng = random.Random(_stable_seed(obj, 404) ^ ((t // 600) & 0xFFFFFFFF))
    a = 28 + (t // 60) % 18
    col = (200, 255, 235, a)

    # concentric rectangles (order)
    inset1 = 4
    inset2 = 9
    r1 = rect.inflate(-2 * inset1, -2 * inset1)
    r2 = rect.inflate(-2 * inset2, -2 * inset2)
    if r1.w > 0 and r1.h > 0:
        pygame.draw.rect(surf, col, r1, 1)
    if r2.w > 0 and r2.h > 0:
        pygame.draw.rect(surf, (220, 255, 245, a), r2, 1)

    # diagonal facet lines
    for _ in range(3):
        x0 = rng.randrange(rect.left, rect.right)
        y0 = rng.randrange(rect.top, rect.bottom)
        x1 = rng.randrange(rect.left, rect.right)
        y1 = rng.randrange(rect.top, rect.bottom)
        pygame.draw.aaline(surf, (210, 255, 240, a), (x0, y0), (x1, y1))

    # a few “vertices” sparkle points
    for _ in range(4):
        x = rng.randrange(rect.left, rect.right)
        y = rng.randrange(rect.top, rect.bottom)
        pygame.draw.circle(surf, (235, 255, 250, 70), (x, y), 2)



def _octonionic_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Randomized per time bucket: spin/flip/scale/offset, but deterministic per-object-ish
    bucket = (t // 220)
    rng = random.Random(0x0C70110 ^ bucket)

    # random angle impulses
    angle = p.angle + rng.choice([-20, -10, -5, 0, 5, 10, 20])

    # random flips sometimes
    fx = p.flip_x ^ (rng.random() < 0.20)
    fy = p.flip_y ^ (rng.random() < 0.15)

    # random scale wobble
    s = 1.0 + rng.choice([-0.12, -0.06, 0.0, 0.06, 0.12])

    # random offset jitter
    dx = rng.randint(-3, 3)
    dy = rng.randint(-3, 3)

    return VisualProfile(
        scale_x=p.scale_x * s,
        scale_y=p.scale_y * s,
        offset_x=p.offset_x + dx,
        offset_y=p.offset_y + dy,
        angle=angle,
        alpha=p.alpha,
        flip_x=fx,
        flip_y=fy,
    )
def _octonionic_color(ent: Any, base: RGB, t: int) -> RGB:
    # RGB cycling every ~300ms
    cols = [(255, 90, 90), (90, 255, 120), (90, 140, 255)]
    idx = (t // 300) % 3
    return _lerp_rgb(base, cols[idx], 0.55)


def _underwhelming_profile(p: VisualProfile, t: int) -> VisualProfile:
    # A joke effect: almost nothing. Slight shrink + slight dim.
    return VisualProfile(
        scale_x=p.scale_x * 0.98,
        scale_y=p.scale_y * 0.98,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle,
        alpha=max(0.15, p.alpha * 0.92),
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _underwhelming_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Barely-visible scanline every so often (the saddest CRT)
    if (t // 900) % 3 != 0:
        return
    y = rect.top + ((t // 18) % max(1, rect.h))
    pygame.draw.line(surf, (255, 255, 255, 18), (rect.left, y), (rect.right, y), 1)

# ---------------------------------------------------------------------------
# Batch 3 experimental effects (readable, flavorful, not too fast)
# ---------------------------------------------------------------------------

def _toasty_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Slight “heat wobble” + subtle breathing scale
    phase = (t % 2200) / 2200.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    dy = int((-1 + 2 * tri) * 1)  # -1..+1
    s = 1.0 + 0.015 * tri
    return VisualProfile(
        scale_x=p.scale_x * s,
        scale_y=p.scale_y * s,
        offset_x=p.offset_x,
        offset_y=p.offset_y + dy,
        angle=p.angle,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _toasty_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Heat shimmer bands: a few faint wavy horizontal lines, slow.
    if rect.w <= 0 or rect.h <= 0:
        return
    rng = random.Random(_stable_seed(obj, 9090) ^ ((t // 240) & 0xFFFFFFFF))

    bands = 3
    base_shift = int(2 * (1.0 - abs(((t % 1600) / 1600.0) * 2.0 - 1.0)))  # 0..2..0

    for i in range(bands):
        y = rect.top + int((i + 1) * rect.h / (bands + 1))
        # “wavy” by shifting endpoints differently
        xshift = base_shift + rng.choice([-1, 0, 1])
        a = 18 + i * 6
        pygame.draw.aaline(
            surf,
            (255, 200, 150, a),
            (rect.left + xshift, y),
            (rect.right - xshift, y),
        )


def _toasty_color(ent: Any, base: RGB, t: int) -> RGB:
    # dull toaster red/orange
    return _lerp_rgb(base, (210, 90, 60), 0.40)


def _arctic_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Occasional tiny shiver + slightly cooler transparency pulse
    phase = (t % 4200) / 4200.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)

    shiver = 0
    if (t // 260) % 11 == 0:
        shiver = random.choice([-1, 1])

    a = max(0.12, min(1.0, p.alpha * (0.92 + 0.06 * tri)))
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x + shiver,
        offset_y=p.offset_y,
        angle=p.angle + (0.4 * shiver),
        alpha=a,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _arctic_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Frost: a couple icy lines + occasional hex motifs, slow cadence
    if rect.w <= 0 or rect.h <= 0:
        return

    rng = random.Random(_stable_seed(obj, 8888) ^ ((t // 520) & 0xFFFFFFFF))

    # faint frosty edge lines
    edge_a = 40 + (t // 80) % 20
    col = (180, 220, 255, edge_a)
    pygame.draw.rect(surf, col, rect, 1)

    # a few internal frost cracks (diagonals)
    for _ in range(2):
        x0 = rng.randrange(rect.left, rect.right)
        y0 = rng.randrange(rect.top, rect.bottom)
        x1 = x0 + rng.choice([-1, 1]) * rng.randrange(8, 20)
        y1 = y0 + rng.choice([-1, 1]) * rng.randrange(8, 20)
        pygame.draw.aaline(surf, (200, 235, 255, 55), (x0, y0), (x1, y1))

    # occasional hexagon motif
    if rng.random() < 0.35:
        cx = rng.randrange(rect.left + 10, rect.right - 10) if rect.w > 20 else (rect.left + rect.w // 2)
        cy = rng.randrange(rect.top + 10, rect.bottom - 10) if rect.h > 20 else (rect.top + rect.h // 2)
        r = 7
        pts = [
            (cx + r, cy),
            (cx + r // 2, cy + r),
            (cx - r // 2, cy + r),
            (cx - r, cy),
            (cx - r // 2, cy - r),
            (cx + r // 2, cy - r),
        ]
        pygame.draw.polygon(surf, (210, 245, 255, 55), pts, 1)


def _arctic_color(ent: Any, base: RGB, t: int) -> RGB:
    return _lerp_rgb(base, (170, 210, 255), 0.40)


def _syrupy_color(ent: Any, base: RGB, t: int) -> RGB:
    # caramelize
    return _lerp_rgb(base, (120, 85, 35), 0.45)

def _syrupy_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    # Big gooey brown drips that slide down slowly.
    rng = random.Random(_stable_seed(obj, 5151))
    lanes = 4
    speed = 16  # larger = slower
    fall = (t // speed)

    for i in range(lanes):
        # fixed x lanes per object
        x = rect.left + int((i + 1) * rect.w / (lanes + 1))
        # each lane has its own offset and wobble
        off = rng.randrange(0, rect.h)
        y = rect.top + ((fall + off) % max(1, rect.h))

        length = 18 + i * 6
        thickness = 3 if i in (1, 2) else 2

        # chocolate/caramel palette
        core = (95, 55, 20, 95)
        shine = (160, 120, 70, 70)

        y2 = min(rect.bottom - 1, y + length)
        pygame.draw.line(surf, core, (x, y), (x, y2), thickness)

        # highlight on one side to read "wet"
        pygame.draw.line(surf, shine, (x - 1, y + 2), (x - 1, min(rect.bottom - 1, y2 - 2)), 1)

        # bulbous drip head
        pygame.draw.circle(surf, core, (x, y2), 5)
        pygame.draw.circle(surf, shine, (x - 2, y2 - 2), 2)



def _candlelit_overlay_rect(obj: Any, base: pygame.Rect, t: int) -> pygame.Rect:
    # Slight vignette around the panel/tile.
    return base.inflate(10, 10)


def _candlelit_overlay(obj: Any, surf: pygame.Surface, rect: pygame.Rect, t: int) -> None:
    if rect.w <= 0 or rect.h <= 0:
        return

    layer = _with_alpha_layer(surf, rect)
    lr = layer.get_rect()

    phase = (t % 1400) / 1400.0
    tri = 1.0 - abs(phase * 2.0 - 1.0)
    flick = 0.70 + 0.30 * tri

    h = lr.h
    for i in range(h):
        up = i / max(1, h - 1)
        inten = (1.0 - up) ** 1.7
        a = int(10 + 90 * inten * flick)

        col = (
            int(80 + 175 * inten),
            int(50 + 120 * inten),
            int(10 + 50 * inten),
            a
        )
        y = lr.bottom - 1 - i
        pygame.draw.line(layer, col, (lr.left, y), (lr.right, y), 1)

    if (t // 240) % 6 == 0:
        rng = random.Random(_stable_seed(obj, 6161) ^ ((t // 240) & 0xFFFFFFFF))
        x = rng.randrange(0, lr.w)
        y = rng.randrange(max(0, lr.h - max(4, lr.h // 5)), lr.h)
        pygame.draw.circle(layer, (255, 200, 120, 120), (x, y), 2)

    surf.blit(layer, rect.topleft)


def _revolving_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Spins in place (axis rotation). Slow enough to read.
    period_ms = 10000
    theta = (t % period_ms) / period_ms * 360.0
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x,
        offset_y=p.offset_y,
        angle=p.angle + theta,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )


def _orbiting_profile(p: VisualProfile, t: int) -> VisualProfile:
    # Translates in a small circular orbit.
    period_ms = 3200
    radius_px = 20
    theta = (t % period_ms) / period_ms * (2.0 * math.pi)
    dx = int(math.cos(theta) * radius_px)
    dy = int(math.sin(theta) * radius_px)
    return VisualProfile(
        scale_x=p.scale_x,
        scale_y=p.scale_y,
        offset_x=p.offset_x + dx,
        offset_y=p.offset_y + dy,
        angle=p.angle,
        alpha=p.alpha,
        flip_x=p.flip_x,
        flip_y=p.flip_y,
    )



###Install Built-in Effects

def _install_builtin_effects() -> None:
    register_effect(VisualEffectDef("clockwise", modify_profile=_clockwise_profile))
    register_effect(VisualEffectDef("counter-clockwise", modify_profile=_counter_clockwise_profile))

    register_effect(VisualEffectDef("ghostly", modify_profile=_ghostly_profile, modify_entity_color=_ghostly_color))
    register_effect(VisualEffectDef("mirror_x", modify_profile=_mirror_x_profile))
    register_effect(VisualEffectDef("mirror_y", modify_profile=_mirror_y_profile))
    register_effect(VisualEffectDef(
        "fiery",
        modify_entity_color=_fiery_color,
        overlay_rect=_fiery_overlay_rect,
        draw_overlay=_fiery_overlay,
    ))

    register_effect(VisualEffectDef(
        "bismuth",
        modify_entity_color=_bismuth_color,
        overlay_rect=_bismuth_overlay_rect,
        draw_overlay=_bismuth_overlay,
    ))
    register_effect(VisualEffectDef(
        "jittery",
        modify_profile=_jittery_profile,
    ))
    register_effect(VisualEffectDef(
        "colossal",
        modify_profile=_colossal_profile,
    ))

    register_effect(VisualEffectDef(
        "smoky",
        overlay_rect=_smoky_overlay_rect,
        draw_overlay=_smoky_overlay,
    ))

    register_effect(VisualEffectDef(
        "malfunctioning",
        modify_profile=_malfunctioning_profile,
        draw_overlay=_malfunctioning_overlay,
    ))

    register_effect(
        VisualEffectDef("carbonated", modify_entity_color=_carbonated_color, draw_overlay=_carbonated_overlay))

    register_effect(VisualEffectDef(
        "celestial",
        overlay_rect=_celestial_overlay_rect,
        draw_overlay=_celestial_overlay,
    ))

    register_effect(VisualEffectDef(
        "entropic",
        modify_profile=_entropic_profile,
        draw_overlay=_entropic_overlay,
    ))

    register_effect(VisualEffectDef(
        "extropic",
        modify_profile=_extropic_profile,
        draw_overlay=_extropic_overlay,
    ))

    register_effect(
        VisualEffectDef("octonionic", modify_profile=_octonionic_profile, modify_entity_color=_octonionic_color))

    register_effect(VisualEffectDef(
        "underwhelming",
        modify_profile=_underwhelming_profile,
        draw_overlay=_underwhelming_overlay,
    ))
    register_effect(VisualEffectDef("toasty", modify_profile=_toasty_profile, modify_entity_color=_toasty_color,
                                    draw_overlay=_toasty_overlay))

    register_effect(VisualEffectDef("arctic", modify_profile=_arctic_profile, modify_entity_color=_arctic_color,
                                    draw_overlay=_arctic_overlay))

    register_effect(VisualEffectDef("syrupy", modify_entity_color=_syrupy_color, draw_overlay=_syrupy_overlay))

    register_effect(VisualEffectDef(
        "candlelit",
        overlay_rect=_candlelit_overlay_rect,
        draw_overlay=_candlelit_overlay,
    ))
    register_effect(VisualEffectDef("revolving", modify_profile=_revolving_profile))
    register_effect(VisualEffectDef("orbiting", modify_profile=_orbiting_profile))


_install_builtin_effects()
