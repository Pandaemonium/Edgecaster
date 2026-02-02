# edgecaster/render/terrain_tiles.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional

import pygame

from edgecaster.math_utils import clamp_u8, lerp_rgb

try:
    # Project-side biome enum + palette
    from edgecaster.biome import Biome, BIOME_COLORS
except Exception:
    Biome = None  # type: ignore
    BIOME_COLORS = {}  # type: ignore


# Variants per (elev_cat, biome, tile_px). More = less repetition, larger cache.
_VARIANTS = 16

# Corruption intensity levels (quantized).
_CORR_LEVELS = 16  # 0..15


def _stable_variant(cx: int, cy: int, n: int = _VARIANTS) -> int:
    """Deterministic per-cell variant selector (avoid salted hash())."""
    v = (cx * 73856093) ^ (cy * 19349663)
    return int(v % max(1, int(n)))




@dataclass(frozen=True)
class TileKey:
    elev_cat: int
    biome_id: int
    w_px: int
    h_px: int
    variant: int
    corr_level: int  # 0..15
    dim: int         # 0/1


class TerrainTileBank:
    """
    Procedural tile surface generator.

    - Most tile work happens at cache-build time (stable while panning/zooming).
    - Corruption is a STATIC per-tile bismuth/Q*bert rectilinear overlay, with intensity from corr_level.
    """

    def __init__(self) -> None:
        self._cache: Dict[TileKey, pygame.Surface] = {}
        self._max_cache = 24_000

        # Separate cache for corruption line-pattern overlays (STATIC).
        # Keyed by (w,h,variant).
        self._corrupt_pattern_cache: Dict[Tuple[int, int, int], pygame.Surface] = {}

        # Elevation bases.
        # NOTE: water categories are intentionally bluish so “deep water in jungle” still reads blue.
        self._elev_base: Dict[int, Tuple[int, int, int]] = {
            0: (10, 14, 30),     # deep water (inky blue)
            1: (16, 22, 44),     # shallow water
            2: (26, 32, 54),     # coast / shelf
            3: (64, 64, 64),     # flats
            4: (92, 92, 92),     # hills
            5: (128, 128, 128),  # mountains
            6: (220, 220, 220),  # peaks / snowcaps
        }

    def get(
        self,
        *,
        elev_cat: int,
        biome_id: int,
        w_px: int,
        h_px: int,
        cx: int,
        cy: int,
        corruption_level: int = 0,
        dim: bool = False,
    ) -> pygame.Surface:
        elev_cat = int(elev_cat)
        biome_id = int(biome_id)
        w_px = max(1, int(w_px))
        h_px = max(1, int(h_px))
        variant = _stable_variant(int(cx), int(cy), _VARIANTS)

        cl = int(corruption_level)
        if cl < 0:
            cl = 0
        if cl > (_CORR_LEVELS - 1):
            cl = _CORR_LEVELS - 1

        key = TileKey(
            elev_cat=elev_cat,
            biome_id=biome_id,
            w_px=w_px,
            h_px=h_px,
            variant=variant,
            corr_level=cl,
            dim=1 if dim else 0,
        )

        s = self._cache.get(key)
        if s is None:
            s = self._build_tile(
                elev_cat=elev_cat,
                biome_id=biome_id,
                w_px=w_px,
                h_px=h_px,
                variant=variant,
                corr_level=cl,
                dim=dim,
            )
            self._cache[key] = s
            if len(self._cache) > self._max_cache:
                for _ in range(1000):
                    try:
                        self._cache.pop(next(iter(self._cache)))
                    except Exception:
                        break
        return s

    def _build_tile(
        self,
        *,
        elev_cat: int,
        biome_id: int,
        w_px: int,
        h_px: int,
        variant: int,
        corr_level: int,
        dim: bool,
    ) -> pygame.Surface:
        s = pygame.Surface((w_px, h_px), pygame.SRCALPHA)

        base = self._elev_base.get(int(elev_cat), (64, 64, 64))

        # Elevation contrast: mountains sharper, plains softer.
        contrast = 1.0 + 0.15 * float(max(0, min(6, elev_cat)))
        top = lerp_rgb(base, (0, 0, 0), 0.25 * contrast)
        bot = lerp_rgb(base, (255, 255, 255), 0.15 * contrast)
        self._fill_vertical_gradient(s, top, bot)

        # Biome color wash (multiply): strong but not crushing.
        biome_col = None
        if Biome is not None:
            try:
                biome_col = BIOME_COLORS.get(Biome(int(biome_id)))
            except Exception:
                biome_col = None

        if biome_col is not None:
            wash = pygame.Surface((w_px, h_px), pygame.SRCALPHA)
            wash_alpha = 180

            # If corruption is present here, keep the base calmer/darker so the overlay reads ominous.
            if corr_level > 0:
                wash_alpha = 110

            wash.fill((int(biome_col[0]), int(biome_col[1]), int(biome_col[2]), int(wash_alpha)))
            s.blit(wash, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # Family pop tweaks AFTER wash (so we can enforce deep oceans, baked-clay scorched, etc.)
        self._apply_family_pop(s, elev_cat=elev_cat, biome_id=int(biome_id))

        # Biome overlay texture (all the old per-biome material patterns)
        self._draw_biome_overlay(s, biome_id=int(biome_id), variant=int(variant))

        # Micro cue marks (subtle)
        if elev_cat >= 5:
            pygame.draw.line(s, (255, 255, 255, 18), (w_px // 2, 0), (w_px // 2, h_px - 1), 1)
        elif elev_cat <= 1:
            pygame.draw.line(s, (0, 0, 0, 16), (0, h_px // 2), (w_px - 1, h_px // 2), 1)

        # Much weaker seam softener (avoid “dark blotches”)
        self._draw_seam_softener(s, variant=int(variant))

        # Corruption overlay: STATIC, rectilinear “bismuth/Q*bert pyramid” vibe.
        # IMPORTANT: apply ONLY when corr_level > 0 so normal tiles keep their normal effects.
        if corr_level > 0:
            i = float(corr_level) / float(_CORR_LEVELS - 1)
            self._overlay_corruption_qbert(s, intensity=i, variant=int(variant))

        # Fog-of-war dim: cache the dimmed version too (no per-frame copy/fill).
        if dim:
            s.fill((102, 102, 102, 255), special_flags=pygame.BLEND_RGBA_MULT)  # ~0.4

        return s

    # ----------------------------
    # Base helpers
    # ----------------------------

    def _fill_vertical_gradient(self, s: pygame.Surface, top: Tuple[int, int, int], bot: Tuple[int, int, int]) -> None:
        w, h = s.get_size()
        if h <= 1:
            s.fill((*top, 255))
            return
        for y in range(h):
            t = y / float(h - 1)
            c = lerp_rgb(top, bot, t)
            pygame.draw.line(s, (*c, 255), (0, y), (w, y))

    def _draw_seam_softener(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        a = 6
        r = max(1, min(w, h) // 10)
        if variant & 1:
            pygame.draw.circle(s, (0, 0, 0, a), (0, 0), r)
        if variant & 2:
            pygame.draw.circle(s, (0, 0, 0, a), (w - 1, h - 1), r)

    # ----------------------------
    # Biome / family styling (restored)
    # ----------------------------

    def _apply_family_pop(self, s: pygame.Surface, *, elev_cat: int, biome_id: int) -> None:
        """
        Enforce global readability rules:
          - Water: deep saturated blue (NOT sky-blue)
          - Ice/Snow: less pop
          - Scorched: warm baked-clay bias (not volcanic)
        """
        bid = int(biome_id)
        w, h = s.get_size()

        # Climate Biome IDs (legacy mapping; use IDs only for "family pop")
        OCEAN = 0
        LAKE = 1
        ICE = 2
        SCORCHED = 5
        SNOW = 16
        RIVER = 17

        # --- Water: deep oceanic saturation without whitening ---
        if bid in (OCEAN, LAKE, RIVER) or elev_cat in (0, 1, 2):
            mult = pygame.Surface((w, h), pygame.SRCALPHA)
            mult.fill((55, 85, 235, 255))
            s.blit(mult, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

            add = pygame.Surface((w, h), pygame.SRCALPHA)
            add.fill((0, 10, 45, 10))
            s.blit(add, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        # --- Ice/Snow: subtle cold lift (less pop) ---
        if bid in (ICE, SNOW):
            pop = pygame.Surface((w, h), pygame.SRCALPHA)
            pop.fill((80, 90, 110, 16))
            s.blit(pop, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        # --- Scorched: warm baked-clay bias (not volcanic) ---
        if bid == SCORCHED:
            warm = pygame.Surface((w, h), pygame.SRCALPHA)
            warm.fill((255, 210, 175, 255))
            s.blit(warm, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    def _draw_biome_overlay(self, s: pygame.Surface, *, biome_id: int, variant: int) -> None:
        """
        Adds biome-specific texture on top of the already-biome-washed base.
        This is the older "material library" the project already had.
        """
        bid = int(biome_id)
        w, h = s.get_size()

        # Climate Biome IDs
        OCEAN = 0
        LAKE = 1
        ICE = 2
        TUNDRA = 3
        TAIGA = 4
        SCORCHED = 5
        DESERT = 6
        GRASSLAND = 7
        TEMPERATE_FOREST = 8
        TROPICAL_FOREST = 9
        SAVANNA = 10
        MARSH = 11
        MOUNTAIN = 12
        HILLS = 13
        BEACH = 14
        JUNGLE = 15
        SNOW = 16
        RIVER = 17
        # Corrupted variants exist elsewhere; we DO NOT special-case them here.
        # Corruption overlay is driven solely by corr_level in the tile key.

        # --- water material ---
        if bid in (OCEAN, LAKE, RIVER):
            self._overlay_waves(s, variant=variant)
            return

        # --- cold material ---
        if bid in (ICE, SNOW, TUNDRA):
            self._overlay_ice(s, variant=variant)
            return

        # --- desert / beach / scorched ---
        if bid in (DESERT, BEACH, SAVANNA):
            self._overlay_dunes(s, variant=variant)
            return

        if bid == SCORCHED:
            self._overlay_hot_desert(s, variant=variant)
            return



        # --- grasslands / savanna ---
        if bid in (GRASSLAND, SAVANNA):
            self._overlay_grass(s, variant=variant)
            return




    # ----------------------------
    # Biome overlay primitives
    # ----------------------------

    def _overlay_speckle(self, s: pygame.Surface, *, variant: int, alpha: int) -> None:
        w, h = s.get_size()
        rng = (variant * 1103515245 + 12345) & 0x7FFFFFFF
        n = max(6, (w * h) // 24)
        for i in range(n):
            rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
            x = rng % w
            rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
            y = rng % h
            a = alpha if (i & 1) else max(0, alpha - 10)
            s.set_at((int(x), int(y)), (0, 0, 0, int(a)))

    def _overlay_rock_noise(self, s: pygame.Surface, *, variant: int) -> None:
        # dark little chips, rectilinear
        w, h = s.get_size()
        rng = (variant * 1664525 + 1013904223) & 0xFFFFFFFF
        n = max(8, (w * h) // 18)
        for _ in range(n):
            rng = (rng * 1664525 + 1013904223) & 0xFFFFFFFF
            x = (rng >> 16) % w
            rng = (rng * 1664525 + 1013904223) & 0xFFFFFFFF
            y = (rng >> 16) % h
            rng = (rng * 1664525 + 1013904223) & 0xFFFFFFFF
            dx = 1 + ((rng >> 28) & 1)
            dy = 1 + ((rng >> 29) & 1)
            pygame.draw.rect(s, (0, 0, 0, 22), pygame.Rect(int(x), int(y), int(dx), int(dy)))

    def _overlay_forest_noise(self, s: pygame.Surface, *, variant: int) -> None:
        # soft mottled darker blobs
        w, h = s.get_size()
        rng = (variant * 69069 + 1) & 0xFFFFFFFF
        n = max(6, (w * h) // 40)
        for _ in range(n):
            rng = (rng * 69069 + 1) & 0xFFFFFFFF
            x = (rng >> 16) % w
            rng = (rng * 69069 + 1) & 0xFFFFFFFF
            y = (rng >> 16) % h
            r = 1 + ((rng >> 30) & 1)
            pygame.draw.circle(s, (0, 0, 0, 18), (int(x), int(y)), int(r))

    def _overlay_waves(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        # thin horizontal dashes
        rng = (variant * 134775813 + 1) & 0xFFFFFFFF
        n = max(6, (w * h) // 30)
        for i in range(n):
            rng = (rng * 134775813 + 1) & 0xFFFFFFFF
            x = (rng >> 16) % w
            rng = (rng * 134775813 + 1) & 0xFFFFFFFF
            y = (rng >> 16) % h
            ln = 1 + ((rng >> 30) & 3)
            a = 14 if (i & 1) else 10
            pygame.draw.line(s, (255, 255, 255, a), (int(x), int(y)), (int(min(w - 1, x + ln)), int(y)), 1)

    def _overlay_ice(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        rng = (variant * 22695477 + 1) & 0xFFFFFFFF
        n = max(8, (w * h) // 26)
        for _ in range(n):
            rng = (rng * 22695477 + 1) & 0xFFFFFFFF
            x = (rng >> 16) % w
            rng = (rng * 22695477 + 1) & 0xFFFFFFFF
            y = (rng >> 16) % h
            # little cracks
            dx = -1 if (rng & 1) else 1
            dy = 1 if (rng & 2) else -1
            pygame.draw.line(s, (255, 255, 255, 20), (int(x), int(y)), (int(max(0, min(w - 1, x + dx))), int(max(0, min(h - 1, y + dy)))), 1)

    def _overlay_dunes(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        rng = (variant * 747796405 + 2891336453) & 0xFFFFFFFF
        # diagonal-ish bands
        for k in range(3):
            rng = (rng * 747796405 + 2891336453) & 0xFFFFFFFF
            y0 = (rng >> 16) % h
            a = 10 + (k * 4)
            pygame.draw.line(s, (255, 255, 255, a), (0, int(y0)), (w - 1, int(max(0, min(h - 1, y0 + (w // 6))))), 1)

    def _overlay_hot_desert(self, s: pygame.Surface, *, variant: int) -> None:
        # baked clay: sparse darker cracks
        w, h = s.get_size()
        rng = (variant * 1597334677 + 1) & 0xFFFFFFFF
        n = max(6, (w * h) // 34)
        for _ in range(n):
            rng = (rng * 1597334677 + 1) & 0xFFFFFFFF
            x = (rng >> 16) % w
            rng = (rng * 1597334677 + 1) & 0xFFFFFFFF
            y = (rng >> 16) % h
            ln = 2 + ((rng >> 30) & 3)
            pygame.draw.line(s, (0, 0, 0, 20), (int(x), int(y)), (int(max(0, min(w - 1, x + ln))), int(y)), 1)

    def _overlay_grass(self, s: pygame.Surface, *, variant: int) -> None:
        w, h = s.get_size()
        rng = (variant * 101427 + 321) & 0xFFFFFFFF
        n = max(10, (w * h) // 22)
        for _ in range(n):
            rng = (rng * 101427 + 321) & 0xFFFFFFFF
            x = (rng >> 16) % w
            rng = (rng * 101427 + 321) & 0xFFFFFFFF
            y = (rng >> 16) % h
            # tiny blades
            pygame.draw.line(s, (0, 0, 0, 14), (int(x), int(y)), (int(x), int(max(0, y - 1))), 1)

    # ----------------------------
    # Corruption overlay (NEW static bismuth/Q*bert)
    # ----------------------------

    def _overlay_corruption_qbert(self, s: pygame.Surface, *, intensity: float, variant: int) -> None:
        """Apply a static rectilinear bismuth overlay with intensity in [0,1]."""
        w, h = s.get_size()
        i = 0.0 if intensity < 0.0 else 1.0 if intensity > 1.0 else float(intensity)

        # Darken toward deep purple-black.
        shadow = pygame.Surface((w, h), pygame.SRCALPHA)
        # Stronger darkness with intensity; bias slightly purple.
        dark_a = int(30 + 160 * i)
        shadow.fill((35, 0, 55, dark_a))
        s.blit(shadow, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        # Bright bismuth lines (pattern surface stores vivid hues; here we just scale alpha).
        pat = self._get_corrupt_pattern(w, h, int(variant))
        pat_alpha = int(40 + 210 * i)  # punchier than before, still allows low-level hints
        if pat_alpha <= 0:
            return

        overlay = pat.copy()
        overlay.set_alpha(pat_alpha)
        s.blit(overlay, (0, 0))

    def _get_corrupt_pattern(self, w: int, h: int, variant: int) -> pygame.Surface:
        key = (int(w), int(h), int(variant))
        cached = self._corrupt_pattern_cache.get(key)
        if cached is not None:
            return cached

        pat = pygame.Surface((w, h), pygame.SRCALPHA)

        # Choose a vivid bismuth palette (hard-coded; no per-tile random hue drift).
        palette = [
            (0, 220, 255),   # cyan
            (0, 255, 120),   # green
            (255, 230, 0),   # yellow
            (255, 120, 0),   # orange
            (255, 0, 200),   # magenta
            (120, 80, 255),  # violet
        ]

        # Deterministic RNG from variant
        x = ((variant + 11) * 1103515245 + 12345) & 0x7FFFFFFF

        def rnd() -> int:
            nonlocal x
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
            return x

        # "Q*bert pyramid" as rectilinear nested right-angles. Make 2-4 nests.
        nests = 2 + (rnd() % 3)
        for n in range(nests):
            col = palette[(variant + n + (rnd() % 3)) % len(palette)]
            a = 170  # vivid internal alpha
            # inset grows per nest
            inset = (n + 1) * max(1, min(w, h) // (6 + (rnd() % 4)))
            x0 = inset
            y0 = inset
            x1 = max(x0 + 1, w - 1 - inset)
            y1 = max(y0 + 1, h - 1 - inset)

            # Draw a stepped square spiral
            thickness = 1
            for k in range(0, 3):
                # top
                pygame.draw.line(pat, (*col, a), (x0, y0 + k), (x1, y0 + k), thickness)
                # left
                pygame.draw.line(pat, (*col, a), (x0 + k, y0), (x0 + k, y1), thickness)

            # little notch to feel "crystalline"
            if (rnd() & 1) and (x1 - x0 > 3) and (y1 - y0 > 3):
                pygame.draw.rect(pat, (*col, a), pygame.Rect(x1 - 2, y0 + 1, 2, 2))

        self._corrupt_pattern_cache[key] = pat
        return pat


# ----------------------------
# Public API
# ----------------------------

_BANK: Optional[TerrainTileBank] = None


def get_terrain_tile_surface(
    *,
    elev_cat: int,
    biome_id: int,
    w_px: int,
    h_px: int,
    cx: int,
    cy: int,
    corruption_level: int = 0,
    dim: bool = False,
) -> pygame.Surface:
    global _BANK
    if _BANK is None:
        _BANK = TerrainTileBank()
    return _BANK.get(
        elev_cat=elev_cat,
        biome_id=biome_id,
        w_px=w_px,
        h_px=h_px,
        cx=cx,
        cy=cy,
        corruption_level=corruption_level,
        dim=dim,
    )
