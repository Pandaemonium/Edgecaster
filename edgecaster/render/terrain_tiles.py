# edgecaster/render/terrain_tiles.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional

import pygame

try:
    from edgecaster.climate import Biome, BIOME_COLORS
except Exception:
    Biome = None  # type: ignore
    BIOME_COLORS = {}  # type: ignore


def _stable_variant(cx: int, cy: int, n: int = 4) -> int:
    """
    Deterministic per-cell variant (no shimmer).
    Do NOT use Python's hash(); it's salted per process.
    """
    v = (cx * 73856093) ^ (cy * 19349663)
    return int(v % n)


def _clamp_u8(x: int) -> int:
    return 0 if x < 0 else 255 if x > 255 else x


def _mix_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    ar, ag, ab = a
    br, bg, bb = b
    return (
        _clamp_u8(int(ar + (br - ar) * t)),
        _clamp_u8(int(ag + (bg - ag) * t)),
        _clamp_u8(int(ab + (bb - ab) * t)),
    )


@dataclass(frozen=True)
class TileKey:
    elev_cat: int
    biome_id: int
    w_px: int
    h_px: int
    variant: int


class TerrainTileBank:
    """
    Generates and caches composited (base elevation + biome overlay) tile surfaces.
    This is a POC: replace procedural overlays with real sprite overlays later.
    """

    def __init__(self) -> None:
        self._cache: Dict[TileKey, pygame.Surface] = {}
        self._max_cache = 8192  # plenty for POC

        # Elevation base colors: keep mostly neutral so biomes carry HUE.
        # Elevation should mostly modulate VALUE (brightness), not tint everything.
        self._elev_base: Dict[int, Tuple[int, int, int]] = {
            0: (20, 20, 28),     # deep ocean shadow
            1: (32, 32, 40),     # shallow water
            2: (48, 48, 52),     # coast
            3: (64, 64, 64),     # flats baseline
            4: (92, 92, 92),     # hills
            5: (128, 128, 128),  # mountains
            6: (210, 210, 210),  # peaks / snowcaps
        }

    def get(self, *, elev_cat: int, biome_id: int, w_px: int, h_px: int, cx: int, cy: int) -> pygame.Surface:
        elev_cat = int(elev_cat)
        biome_id = int(biome_id)
        w_px = max(1, int(w_px))
        h_px = max(1, int(h_px))
        variant = _stable_variant(int(cx), int(cy), 4)

        key = TileKey(elev_cat=elev_cat, biome_id=biome_id, w_px=w_px, h_px=h_px, variant=variant)
        surf = self._cache.get(key)
        if surf is not None:
            return surf

        surf = self._build_tile(elev_cat=elev_cat, biome_id=biome_id, w_px=w_px, h_px=h_px, variant=variant)

        self._cache[key] = surf
        if len(self._cache) > self._max_cache:
            # Simple eviction: drop arbitrary old entries
            for _ in range(512):
                try:
                    self._cache.pop(next(iter(self._cache)))
                except Exception:
                    break

        return surf

    def _build_tile(self, *, elev_cat: int, biome_id: int, w_px: int, h_px: int, variant: int) -> pygame.Surface:
        s = pygame.Surface((w_px, h_px), pygame.SRCALPHA)

        base = self._elev_base.get(elev_cat, (64, 64, 64))

        # Elevation-dependent contrast: mountains sharper, plains softer.
        contrast = 1.0 + 0.15 * float(max(0, min(6, elev_cat)))
        top = _mix_rgb(base, (0, 0, 0), 0.25 * contrast)
        bot = _mix_rgb(base, (255, 255, 255), 0.15 * contrast)
        self._fill_vertical_gradient(s, top, bot)

        # --- Biome color wash (this is the "make it pop" step) ---
        # We multiply a biome-colored wash into the tile so biome drives hue,
        # while elevation remains a brightness/relief modulator.
        biome_col = None
        if Biome is not None:
            try:
                b = Biome(int(biome_id))
                biome_col = BIOME_COLORS.get(b)
            except Exception:
                biome_col = None

        if biome_col is not None:
            wash = pygame.Surface((w_px, h_px), pygame.SRCALPHA)
            # Strength knob: 120..190 (higher = more vivid)
            wash.fill((*biome_col, 160))
            s.blit(wash, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # Biome overlay (procedural “sprite-like” textures).
        self._draw_biome_overlay(s, biome_id=biome_id, variant=variant)

        # Micro cue marks to help distinguish extremes at a glance (subtle).
        # High elevations: faint vertical "ridge" cue; low water: faint horizon cue.
        if elev_cat >= 5:
            pygame.draw.line(s, (255, 255, 255, 30), (w_px // 2, 0), (w_px // 2, h_px - 1), 1)
        elif elev_cat <= 1:
            pygame.draw.line(s, (0, 0, 0, 30), (0, h_px // 2), (w_px - 1, h_px // 2), 1)

        # Subtle border noise to reduce seams (very light; avoids grid look).
        self._draw_seam_softener(s, variant=variant)

        return s

    def _fill_vertical_gradient(self, s: pygame.Surface, top: Tuple[int, int, int], bot: Tuple[int, int, int]) -> None:
        w, h = s.get_size()
        if h <= 1:
            s.fill((*top, 255))
            return
        for y in range(h):
            t = y / float(h - 1)
            c = _mix_rgb(top, bot, t)
            pygame.draw.line(s, (*c, 255), (0, y), (w, y))

    def _draw_seam_softener(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        a = 18  # alpha
        # Slight corner speckle; deterministic via variant
        if variant & 1:
            pygame.draw.circle(s, (0, 0, 0, a), (0, 0), max(1, min(w, h) // 6))
        if variant & 2:
            pygame.draw.circle(s, (0, 0, 0, a), (w - 1, h - 1), max(1, min(w, h) // 6))

    def _draw_biome_overlay(self, s: pygame.Surface, *, biome_id: int, variant: int) -> None:
        # If you want to map by enum name later, keep this structure.
        bid = int(biome_id)

        # Helper: classify biome “families”
        def is_any(*vals: int) -> bool:
            return bid in vals

        # Climate Biome IDs (from edgecaster.climate.Biome)
        OCEAN = 0
        LAKE = 1
        ICE = 2
        TUNDRA = 3
        BARE = 4
        SCORCHED = 5
        TAIGA = 6
        SHRUB = 7
        TDES = 8
        TGRASS = 9
        TFOREST = 10
        TRAIN = 11
        SDES = 12
        SAVANNA = 13
        TROP = 14
        TROPRAIN = 15
        SNOW = 16
        RIVER = 17
        CWASTE = 18
        CFOREST = 19
        CWATER = 20

        if is_any(OCEAN, LAKE, RIVER, CWATER):
            self._overlay_waves(s, variant=variant, strong=is_any(RIVER))
            return

        if is_any(ICE, SNOW):
            self._overlay_ice(s, variant=variant)
            return

        if is_any(TDES, SDES):
            self._overlay_dunes(s, variant=variant, bright=True)
            return

        if is_any(SCORCHED, CWASTE):
            self._overlay_lava_cracks(s, variant=variant)
            return

        if is_any(TFOREST, TRAIN, TAIGA, TROP, TROPRAIN, CFOREST):
            self._overlay_forest(s, variant=variant)
            return

        if is_any(TGRASS, SAVANNA, SHRUB):
            self._overlay_grass(s, variant=variant, savanna=is_any(SAVANNA))
            return

        if is_any(TUNDRA, BARE):
            self._overlay_rock(s, variant=variant)
            return

        # Default: very light speckle
        self._overlay_speckle(s, variant=variant, alpha=26)

    # --- Overlay “sprite” primitives ---

    def _overlay_waves(self, s: pygame.Surface, *, variant: int, strong: bool = False) -> None:
        w, h = s.get_size()

        # Subtle cyan lift so water reads as "wet" and fantastical.
        pygame.draw.rect(s, (80, 180, 255, 24), pygame.Rect(0, 0, w, h), 0)

        a = 40 if strong else 26
        col = (255, 255, 255, a)
        step = max(2, h // (5 if strong else 7))
        amp = max(1, min(w, h) // (6 if strong else 8))
        phase = (variant * 3) % 7
        for y in range(phase, h, step):
            pts = []
            for x in range(0, w + 1, max(3, w // 8)):
                dy = int(((x // max(1, w // 8)) % 2) * amp - amp // 2)
                pts.append((x, max(0, min(h - 1, y + dy))))
            if len(pts) >= 2:
                pygame.draw.lines(s, col, False, pts, 1)

    def _overlay_ice(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        a = 44
        col = (235, 245, 255, a)
        # crystalline diagonals
        for i in range(0, w + h, max(5, min(w, h) // 6)):
            pygame.draw.line(s, col, (max(0, i - h), min(h - 1, i)), (min(w - 1, i), max(0, i - w)), 1)
        if variant & 1:
            pygame.draw.line(s, (255, 255, 255, a), (0, h // 2), (w - 1, h // 2), 1)

    def _overlay_dunes(self, s: pygame.Surface, *, variant: int, bright: bool) -> None:
        w, h = s.get_size()
        a = 42 if bright else 30
        col = (255, 245, 210, a) if bright else (240, 220, 180, a)
        step = max(3, h // 6)
        phase = (variant * 2) % step
        for y in range(phase, h, step):
            # gentle dune arcs
            pygame.draw.arc(s, col, pygame.Rect(-w // 3, y - step, w + w // 2, step * 2), 0.1, 3.0, 1)

    def _overlay_lava_cracks(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        # base glow
        pygame.draw.circle(s, (255, 120, 20, 28), (w // 2, h // 2), max(2, min(w, h) // 3))
        # crack lines
        col = (255, 60, 10, 52)
        thickness = 1
        if variant & 1:
            pygame.draw.line(s, col, (0, h // 3), (w - 1, h // 2), thickness)
        pygame.draw.line(s, col, (w // 4, 0), (w // 2, h - 1), thickness)
        if variant & 2:
            pygame.draw.line(s, col, (w - 1, h // 4), (w // 3, h - 1), thickness)

    def _overlay_forest(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        # leaf speckle
        self._overlay_speckle(s, variant=variant, alpha=34)
        # a few “canopy blobs”
        col = (0, 0, 0, 18)
        r = max(1, min(w, h) // 6)
        pygame.draw.circle(s, col, (w // 3, h // 3), r)
        if variant & 1:
            pygame.draw.circle(s, col, (2 * w // 3, h // 2), r)

    def _overlay_grass(self, s: pygame.Surface, *, variant: int, savanna: bool) -> None:
        w, h = s.get_size()
        a = 28
        col = (255, 255, 255, a) if not savanna else (255, 240, 180, a)
        # short streaks
        step = max(2, w // 7)
        for x in range((variant * 3) % step, w, step):
            pygame.draw.line(s, col, (x, h - 1), (x + 1, h // 2), 1)

    def _overlay_rock(self, s: pygame.Surface, *, variant: int) -> None:
        # rocky stipple
        self._overlay_speckle(s, variant=variant, alpha=30)
        w, h = s.get_size()
        col = (0, 0, 0, 22)
        pygame.draw.rect(s, col, pygame.Rect(w // 4, h // 3, max(1, w // 6), max(1, h // 6)), 0)

    def _overlay_speckle(self, s: pygame.Surface, *, variant: int, alpha: int) -> None:
        w, h = s.get_size()
        # deterministic pseudo-rng from variant and size
        seed = (variant + 1) * 101 + w * 3 + h * 7
        # tiny LCG
        x = seed & 0xFFFFFFFF

        def rnd() -> int:
            nonlocal x
            x = (1664525 * x + 1013904223) & 0xFFFFFFFF
            return x

        n = max(8, (w * h) // 24)
        col = (255, 255, 255, int(alpha))
        for _ in range(n):
            rx = rnd() % max(1, w)
            ry = rnd() % max(1, h)
            s.set_at((int(rx), int(ry)), col)


_BANK: Optional[TerrainTileBank] = None


def get_terrain_tile_surface(*, elev_cat: int, biome_id: int, w_px: int, h_px: int, cx: int, cy: int) -> pygame.Surface:
    global _BANK
    if _BANK is None:
        _BANK = TerrainTileBank()
    return _BANK.get(elev_cat=elev_cat, biome_id=biome_id, w_px=w_px, h_px=h_px, cx=cx, cy=cy)
