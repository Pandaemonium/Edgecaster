"""
Difficulty System - Zone danger field and enemy tier filtering.

Goals:
- Provide a transparent, tunable "difficulty by zone" field that is decoupled from biomes.
- Keep the logic deterministic and modular so designers can tweak numbers later.
- Expose a 10-tier system (T1..T10) with optional overrides and local modifiers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple
import math

from edgecaster import corruption as corruption_system
from edgecaster.content import pois as poi_content
from edgecaster import prototypes
from edgecaster.math_utils import clamp, smoothstep_range
import yaml

# Type alias for readability.
ZoneCoord = Tuple[int, int, int]




def _hash2(ix: int, iy: int, seed: int) -> float:
    # Deterministic hash to 0..1 (cheap + reproducible).
    n = ix * 374761393 + iy * 668265263 + seed * 1442695041
    n = (n ^ (n >> 16)) & 0xFFFFFFFF
    n = (n * 0x7FEB352D) & 0xFFFFFFFF
    n = (n ^ (n >> 15)) & 0xFFFFFFFF
    n = (n * 0x846CA68B) & 0xFFFFFFFF
    n = (n ^ (n >> 16)) & 0xFFFFFFFF
    return (n & 0xFFFFFF) / 16777216.0


def _value_noise_2d(x: float, y: float, seed: int) -> float:
    """Simple value noise in [-1, 1]."""
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
    x1 = x0 + 1
    y1 = y0 + 1
    fx = x - x0
    fy = y - y0

    # Smoothstep for interpolation.
    u = fx * fx * (3.0 - 2.0 * fx)
    v = fy * fy * (3.0 - 2.0 * fy)

    v00 = _hash2(x0, y0, seed)
    v10 = _hash2(x1, y0, seed)
    v01 = _hash2(x0, y1, seed)
    v11 = _hash2(x1, y1, seed)

    nx0 = v00 + u * (v10 - v00)
    nx1 = v01 + u * (v11 - v01)
    val = nx0 + v * (nx1 - nx0)
    return val * 2.0 - 1.0


def _fractal_noise_2d(
    x: float,
    y: float,
    seed: int,
    *,
    octaves: int,
    persistence: float,
    lacunarity: float,
) -> float:
    """Fractal noise in [-1, 1], used for gentle difficulty variation."""
    amp = 1.0
    freq = 1.0
    total = 0.0
    norm = 0.0
    for _ in range(max(1, int(octaves))):
        total += _value_noise_2d(x * freq, y * freq, seed) * amp
        norm += amp
        amp *= persistence
        freq *= lacunarity
    if norm <= 1e-9:
        return 0.0
    return total / norm


@dataclass
class DifficultyConfig:
    """Tunable config for the zone difficulty field.

    All parameters are designed to be adjusted without rewriting code.
    """
    tiers: int = 10

    # Base west->east gradient.
    gradient_start: float = 0.15  # x_norm where difficulty starts rising
    gradient_end: float = 0.85    # x_norm where difficulty reaches "full"
    gradient_power: float = 1.0   # >1 makes the ramp steeper near the right

    # Low-frequency variation (keeps large swaths coherent).
    noise_scale: float = 0.08
    noise_freq: float = 1.25
    noise_octaves: int = 1
    noise_persistence: float = 0.5
    noise_lacunarity: float = 2.0

    # Starting zone safety clamp.
    safe_radius: int = 4          # Chebyshev radius in zones
    safe_cap: float = 0.20        # Max difficulty inside safe_radius

    # Corruption contribution. env_total is ~0..1, multiply by this.
    corruption_weight: float = 0.25

    # Site / POI difficulty offsets (per zone).
    site_bonuses: Dict[str, float] = field(default_factory=lambda: {
        "corruption_outpost": 0.25,
        "tropical_shrine": 0.08,
        "spriggan_grove": 0.06,
        "bird_aerie": 0.06,
        "desert_camp": 0.05,
        "hunter_lodge": 0.04,
        "fishing_village": -0.08,
    })

    poi_structure_bonuses: Dict[str, float] = field(default_factory=lambda: {
        "legendary_lair": 0.35,
        "lab": 0.12,
        "starting_zone": -0.25,
        "tower": 0.08,
        "academy": -0.10,
    })


def danger_to_tier(danger: float, *, tiers: int) -> int:
    """Map danger in [0,1] to tier 1..tiers."""
    tiers = max(1, int(tiers))
    danger = clamp(float(danger))
    # 0.0 -> 1, 0.999 -> tiers.
    return max(1, min(tiers, int(danger * tiers) + 1))


def compute_zone_difficulty(
    game,
    coord: ZoneCoord,
    *,
    config: Optional[DifficultyConfig] = None,
) -> Tuple[float, Dict[str, float]]:
    """Compute difficulty scalar for a zone and return (danger_value, sources).

    This function is pure: it does not mutate game/level state.
    """
    if config is None:
        config = getattr(game, "difficulty_config", None) or DifficultyConfig()

    zx, zy, depth = coord
    sources: Dict[str, float] = {}

    # Base west->east gradient.
    screens = max(1, int(getattr(game.cfg, "world_map_screens", 1)))
    x_norm = float(zx) / float(max(1, screens - 1))
    base = smoothstep_range(config.gradient_start, config.gradient_end, x_norm)
    if config.gradient_power != 1.0:
        base = base ** float(config.gradient_power)
    sources["base_gradient"] = base

    # Low-frequency noise to add gentle region variation.
    seed = int(getattr(game.cfg, "seed", 0) or 0) + 12345
    n = _fractal_noise_2d(
        float(zx) / float(screens) * config.noise_freq,
        float(zy) / float(screens) * config.noise_freq,
        seed,
        octaves=config.noise_octaves,
        persistence=config.noise_persistence,
        lacunarity=config.noise_lacunarity,
    )
    noise = n * float(config.noise_scale)
    sources["noise"] = noise

    danger = base + noise

    # Site-based modifiers (if any).
    site_bonus = 0.0
    try:
        registry = getattr(game, "site_registry", None)
        if registry is not None and depth == 0:
            spec = registry.get_at(coord)
            if spec is not None:
                site_bonus += float(config.site_bonuses.get(spec.kind, 0.0))
    except Exception:
        pass
    if abs(site_bonus) > 1e-9:
        sources["site_bonus"] = site_bonus
        danger += site_bonus

    # POI structure modifiers (labs, lairs, starting zone, etc.)
    poi_bonus = 0.0
    try:
        for poi in poi_content.POIS.values():
            if tuple(poi.coord) != coord:
                continue
            for struct in getattr(poi, "structures", []) or []:
                kind = str(struct.get("kind") or "")
                if not kind:
                    continue
                poi_bonus += float(config.poi_structure_bonuses.get(kind, 0.0))
    except Exception:
        pass
    if abs(poi_bonus) > 1e-9:
        sources["poi_bonus"] = poi_bonus
        danger += poi_bonus

    # Corruption contribution (sample at zone center in Julia space).
    corr_bonus = 0.0
    try:
        params = corruption_system.CorruptionParams(
            seed=int(getattr(game, "corruption_seed", 1337) or 1337),
            hotspots=list(getattr(game, "corruption_hotspots", []) or []),
            anchors=list(getattr(game, "corruption_anchors", []) or []),
            spline_weight=float(getattr(game, "corruption_spline_weight", 0.0) or 0.0),
        )
        overmap = getattr(game, "overmap_params", None)
        grid = getattr(game, "tile_julia_grid", None)
        if overmap and grid:
            wx = zx * game.cfg.world_width + (game.cfg.world_width // 2)
            wy = zy * game.cfg.world_height + (game.cfg.world_height // 2)
            if 0 <= wx < len(grid.get("x", [])) and 0 <= wy < len(grid.get("y", [])):
                jx = float(grid["x"][wx])
                jy = float(grid["y"][wy])
                _, _, env = corruption_system.distortion_dz(
                    jx,
                    jy,
                    params=params,
                    j_min_x=float(overmap.get("view_min_jx", -2.0)),
                    j_max_x=float(overmap.get("view_max_jx", 2.0)),
                    corruption_level=float(getattr(game, "corruption_level", 0.0) or 0.0),
                )
                corr_bonus = float(env) * float(config.corruption_weight)
    except Exception:
        corr_bonus = 0.0
    if corr_bonus > 0.0:
        sources["corruption_bonus"] = corr_bonus
        danger += corr_bonus

    # Optional per-zone override (set by quests/scripting).
    overrides = getattr(game, "zone_difficulty_overrides", None)
    if isinstance(overrides, dict) and coord in overrides:
        danger = float(overrides[coord])
        sources["override"] = danger

    danger = clamp(danger, 0.0, 1.0)

    # Starting-zone safety clamp.
    try:
        start_zx, start_zy, _ = getattr(game, "zone_coord", (zx, zy, depth))
        dist = max(abs(zx - int(start_zx)), abs(zy - int(start_zy)))
        if dist <= int(config.safe_radius):
            danger = min(danger, float(config.safe_cap))
            sources["safe_cap"] = float(config.safe_cap)
    except Exception:
        pass

    danger = clamp(danger, 0.0, 1.0)
    return danger, sources


def apply_zone_difficulty(game, level, coord: ZoneCoord) -> None:
    """Compute and stamp difficulty metadata onto a LevelState."""
    config = getattr(game, "difficulty_config", None) or DifficultyConfig()
    danger, sources = compute_zone_difficulty(game, coord, config=config)
    level.danger_value = float(danger)
    level.danger_tier = danger_to_tier(danger, tiers=config.tiers)
    level.danger_sources = sources


def _enemy_default_tier_from_xp(xp: float, *, tiers: int, xp_max: float) -> int:
    """Convert XP to a rough tier hint (fallback when no explicit tier is set)."""
    if xp_max <= 0:
        return 1
    return danger_to_tier(float(xp) / float(xp_max), tiers=tiers)


def enemy_tier_bounds(spec: dict, *, tiers: int, xp_max: float) -> Tuple[int, int]:
    """Return (tier_min, tier_max) for an enemy spec.

    Explicit tier_min/tier_max in YAML always win.
    Otherwise, we derive a tier from XP for a reasonable default.
    """
    tmin = spec.get("tier_min")
    tmax = spec.get("tier_max")
    if tmin is not None or tmax is not None:
        tmin = int(tmin if tmin is not None else 1)
        tmax = int(tmax if tmax is not None else tiers)
        return max(1, tmin), min(tiers, tmax)

    xp = spec.get("xp", 0)
    tier_hint = _enemy_default_tier_from_xp(float(xp), tiers=tiers, xp_max=xp_max)
    # Default to a narrow window to keep high-tier enemies out of low zones.
    # Designers can widen via explicit tier_min/tier_max in YAML.
    tmin = tier_hint
    tmax = min(tiers, tier_hint + 2)
    return max(1, tmin), max(1, tmax)


def filter_enemy_pool(
    game,
    enemy_ids: Iterable[str],
    zone_tier: int,
) -> Tuple[list[str], dict[str, Tuple[int, int]]]:
    """Filter enemy IDs by the zone's difficulty tier.

    Returns (filtered_ids, bounds_by_id) for debug/inspection.
    """
    config = getattr(game, "difficulty_config", None) or DifficultyConfig()
    tiers = int(config.tiers)

    # Cache global xp max for fallback tiering (avoid pool-relative scaling).
    xp_max = _global_enemy_xp_max()

    filtered: list[str] = []
    bounds: dict[str, Tuple[int, int]] = {}
    for eid in enemy_ids:
        try:
            spec = prototypes.resolve_proto(eid) or {}
        except Exception:
            spec = {}
        tmin, tmax = enemy_tier_bounds(spec, tiers=tiers, xp_max=xp_max)
        bounds[eid] = (tmin, tmax)
        if tmin <= zone_tier <= tmax:
            filtered.append(eid)

    return filtered, bounds


_GLOBAL_XP_MAX: float | None = None


def _global_enemy_xp_max() -> float:
    """Compute a stable XP max across *all* enemy prototypes.

    This keeps tier derivation consistent no matter which pool is used.
    """
    global _GLOBAL_XP_MAX
    if _GLOBAL_XP_MAX is not None:
        return _GLOBAL_XP_MAX

    xp_vals: list[float] = []
    yaml_path = Path(__file__).resolve().parent.parent / "content" / "enemies.yaml"
    try:
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except Exception:
        data = []

    for entry in data:
        if not isinstance(entry, dict):
            continue
        if entry.get("faction") in ("template", "npc", "player", "neutral"):
            continue
        xp = entry.get("xp")
        if xp is None:
            continue
        try:
            xp_vals.append(float(xp))
        except Exception:
            continue

    _GLOBAL_XP_MAX = max(xp_vals) if xp_vals else 1.0
    return _GLOBAL_XP_MAX
