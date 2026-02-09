# edgecaster/systems/aggregate_resolution.py
"""
Aggregate / Detail Resolution System (first pass)

Goal:
- Keep "everything is an entity" while supporting multi-scale abstraction.
- WorldEntityIndex stores *aggregate* entities at macro scale (no gameplay state).
- When observed closely (camera LoD) we can emit render-only *detail proxies*.
- When a zone is actually created/loaded (simulation allowed), we can *realize*
  aggregate details into real interactive entities, then later collapse (future).

This module is intentionally GENERAL:
Berry patches are just one aggregate kind ("berry_patch"). Tomorrow: goblin bands,
forests, battalions, city plans, etc.

Design invariants:
- Rendering-time operations MUST NOT instantiate zones or mutate gameplay state.
- Deterministic generation: children are derived from (fractal_seed, aggregate_id, ...)
  so we don't have to cache the whole world.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
import math
import random

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.systems import spawning as spawning_system


# -----------------------------
# Utility helpers
# -----------------------------

def _tags(ent: object) -> Dict:
    try:
        t = getattr(ent, "tags", None)
        if isinstance(t, dict):
            return t
    except Exception:
        pass
    return {}

def _ent_id(ent: object) -> str:
    try:
        eid = getattr(ent, "id", None)
        if isinstance(eid, str) and eid:
            return eid
    except Exception:
        pass
    return f"obj:{id(ent)}"

def _stable_int_hash(*parts: object) -> int:
    # Stable within a run; determinism comes from fixed seeds + stringification.
    s = "|".join(str(p) for p in parts)
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)

def _seed_for(game: "Game", *parts: object) -> int:
    # Use game.fractal_seed if present; fallback to cfg.seed; else 0.
    base = 0
    try:
        base = int(getattr(game, "fractal_seed", 0) or 0)
    except Exception:
        base = 0
    if not base:
        try:
            base = int(getattr(getattr(game, "cfg", None), "seed", 0) or 0)
        except Exception:
            base = 0
    return (base ^ _stable_int_hash(*parts)) & 0xFFFFFFFF


# -----------------------------
# Aggregate discovery / worldgen
# -----------------------------

def _biome_id_at_abs(game: "Game", abs_x: int, abs_y: int, *, depth: int = 0) -> Optional[int]:
    if int(depth) != 0:
        return None

    try:
        from edgecaster.overmap_accel import sample_biome_id_at_world_xy
    except Exception:
        return None

    # World extents should come from config (same truth as renderer uses).
    cfg = getattr(game, "cfg", None)
    if cfg is None:
        return None

    try:
        total_w = int(cfg.world_map_screens * cfg.world_width)
        total_h = int(cfg.world_map_screens * cfg.world_height)
    except Exception:
        return None

    params = getattr(game, "overmap_params", None) or {}
    return sample_biome_id_at_world_xy(
        int(abs_x), int(abs_y),
        total_w=total_w,
        total_h=total_h,
        params=params,
        iters=24,  # intentionally approximate & stable
    )


def _ecology_choose_child_id(
    game: "Game",
    *,
    agg_eid: str,
    biome_id: Optional[int],
    default_child: str,
) -> str:
    """Choose a deterministic enemy template id for an ecology controller."""
    if biome_id is None:
        biome_id = -1

    try:
        pool = spawning_system.get_biome_enemy_pool(int(biome_id), include_neutral_factions=False)
    except Exception:
        pool = []

    if not pool:
        return str(default_child)

    rng = random.Random(_seed_for(game, "eco_child", str(agg_eid), int(biome_id)))
    try:
        return str(rng.choice(list(pool)))
    except Exception:
        return str(default_child)

def _discover_worldgen_aggregate_kinds(game: "Game") -> tuple[str, ...]:
    """Return aggregate kinds that should be worldgen'd, discovered from prototypes.

    Primary rule (safe + data-driven):
      - tags.world_entity == True
      - tags.aggregate == True
      - tags.aggregate_kind present (defaults to proto id)
      - tags.worldgen_chance present (and optionally worldgen_min/worldgen_max)

    Back-compat rule:
      - Include berry_patch if it's marked world_entity+aggregate even if worldgen_chance is missing.
        (Remove once YAML is fully migrated.)

    Cached on the Game instance to avoid scanning prototypes every frame.
    """
    cache = getattr(game, "_agg_worldgen_kinds_cache", None)
    if isinstance(cache, tuple) and cache:
        return cache

    kinds: list[str] = []
    try:
        bucket = prototypes.get_master_bucket()
    except Exception:
        bucket = {}

    for proto_id in (bucket or {}).keys():
        try:
            spec = prototypes.resolve_proto(str(proto_id))
        except Exception:
            continue
        if not isinstance(spec, dict):
            continue
        tags = spec.get("tags", {}) or {}
        if not isinstance(tags, dict):
            continue
        if tags.get("world_entity") is not True:
            continue
        if tags.get("aggregate") is not True:
            continue

        if "worldgen_chance" not in tags and str(proto_id) != "berry_patch":
            continue

        kind = str(tags.get("aggregate_kind") or proto_id or "").strip()
        if not kind:
            continue
        kinds.append(kind)

    kinds = sorted(set(kinds))
    out = tuple(kinds)
    try:
        setattr(game, "_agg_worldgen_kinds_cache", out)
    except Exception:
        pass
    return out

def ensure_world_aggregates(
    game: "Game",
    *,
    zone_w: int,
    zone_h: int,
    zx0: int,
    zx1: int,
    zy0: int,
    zy1: int,
    zz: int,
    kinds: Optional[Sequence[str]] = None,
) -> None:
    """
    Ensure aggregate world entities exist for the requested zone bucket range.

    Side-effect constraints:
      - Adds *only* macro entities to WorldEntityIndex.
      - No zone creation, no time advance, no simulation deltas.

    `kinds`: optional filter on aggregate_kind (e.g. ["berry_patch"]).
    """
    if not hasattr(game, "_agg_worldgen_done"):
        game._agg_worldgen_done = set()  # type: ignore[attr-defined]

    if getattr(game, "world_entity_index", None) is None:
        return

    if kinds is None:
        kinds = _discover_worldgen_aggregate_kinds(game)
        if not kinds:
            return
    else:
        kinds = tuple(str(k) for k in kinds if k)

    for zx in range(int(zx0), int(zx1) + 1):
        for zy in range(int(zy0), int(zy1) + 1):
            for kind in kinds:
                key = (str(kind), int(zx), int(zy), int(zz))
                if key in game._agg_worldgen_done:  # type: ignore[attr-defined]
                    continue

                # Resolve prototype/spec for this aggregate kind (proto id == kind in current content)
                try:
                    proto = prototypes.resolve_proto(str(kind))
                except Exception:
                    game._agg_worldgen_done.add(key)  # type: ignore[attr-defined]
                    continue

                # Deterministic RNG per (kind, zone bucket)
                rng = random.Random(_seed_for(game, "agg_world", kind, zx, zy, zz))

                # Read knobs from tags
                tags = {}
                if isinstance(proto, dict):
                    tags = proto.get("tags", {}) or {}
                else:
                    try:
                        tags = getattr(proto, "tags", {}) or {}
                    except Exception:
                        tags = {}

                # Back-compat: if berry_patch has no knobs, emulate the old hardcoded behavior.
                if str(kind) == "berry_patch" and "worldgen_chance" not in (tags or {}):
                    # old behavior: 10% => 2, else 25% => 1, else 0
                    count = 0
                    r = rng.random()
                    if r < 0.10:
                        count = 2
                    elif r < 0.35:
                        count = 1
                else:
                    try:
                        chance = float((tags or {}).get("worldgen_chance", 0.0) or 0.0)
                    except Exception:
                        chance = 0.0
                    try:
                        cmin = int((tags or {}).get("worldgen_min", 1) or 1)
                    except Exception:
                        cmin = 1
                    try:
                        cmax = int((tags or {}).get("worldgen_max", cmin) or cmin)
                    except Exception:
                        cmax = cmin

                    chance = max(0.0, min(1.0, chance))
                    cmin = max(0, cmin)
                    cmax = max(cmin, cmax)

                    count = 0
                    if chance > 0.0 and rng.random() < chance:
                        count = cmin if cmax == cmin else rng.randint(cmin, cmax)

                if count <= 0:
                    game._agg_worldgen_done.add(key)  # type: ignore[attr-defined]
                    continue

                for i in range(int(count)):
                    ox = rng.randint(2, max(2, int(zone_w) - 3))
                    oy = rng.randint(2, max(2, int(zone_h) - 3))

                    eid = f"agg:{kind}:{zx},{zy},{zz}:{i}"

                    base_tags = {}
                    if isinstance(proto, dict):
                        base_tags = dict(proto.get("tags", {}) or {})
                    else:
                        try:
                            base_tags = dict(getattr(proto, "tags", {}) or {})
                        except Exception:
                            base_tags = {}


                    # Ecology controllers: bind to biome + choose a biome-appropriate enemy at spawn time.
                    eco_extra_tags: Dict = {}
                    try:
                        agg_kind = str(base_tags.get("aggregate_kind") or kind)
                    except Exception:
                        agg_kind = str(kind)

                    if agg_kind == "ecology_controller":
                        ax = int(zx) * int(zone_w) + int(ox)
                        ay = int(zy) * int(zone_h) + int(oy)

                        b_id = _biome_id_at_abs(game, ax, ay, depth=int(zz))
                        try:
                            if b_id is not None:
                                from edgecaster.climate import Biome as _Biome
                                eco_extra_tags["eco_biome_id"] = int(b_id)
                                try:
                                    eco_extra_tags["eco_biome_name"] = str(_Biome(int(b_id)).name)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        default_child = str(base_tags.get("detail_child") or "wolf")
                        chosen_child = _ecology_choose_child_id(
                            game,
                            agg_eid=str(eid),
                            biome_id=b_id,
                            default_child=default_child,
                        )
                        eco_extra_tags["detail_child"] = str(chosen_child)


                    overrides = {
                        "tags": {
                            **base_tags,
                            **(eco_extra_tags or {}),
                            "world_entity": True,
                            "aggregate": True,
                            "aggregate_kind": str(base_tags.get("aggregate_kind") or kind),
                            "detail_mode": base_tags.get("detail_mode", "cluster"),
                        }
                    }

                    try:
                        ent = spawn_factory.build_entity_from_spec(
                            spec=proto,
                            eid=eid,
                            pos=(int(ox), int(oy)),
                            overrides=overrides,
                        )
                    except Exception:
                        continue

                    try:
                        game.world_entity_index.add(
                            ent,
                            zone_coord=(int(zx), int(zy), int(zz)),
                            local_pos=(int(ox), int(oy)),
                        )
                    except Exception:
                        pass

                game._agg_worldgen_done.add(key)  # type: ignore[attr-defined]

# -----------------------------
# Detail resolution (cluster mode)
# -----------------------------

@dataclass(frozen=True)
class DetailProxy:
    ent: object
    abs_x: int
    abs_y: int
    zone_coord: Tuple[int, int, int]
    local_pos: Tuple[int, int]


def _cluster_points(
    game: "Game",
    *,
    agg_id: str,
    child_id: str,
    center: Tuple[int, int],
    radius: float,
    count: int,
    zone_w: int,
    zone_h: int,
) -> List[Tuple[int, int]]:
    """Deterministically generate (x,y) points in a disk around `center`.

    Critical invariant for 'no shuffling on zoom':
    - The point set is a pure function of (world seed, agg_id, child_id, center, radius, count, zone dims)
    - It does NOT depend on camera LoD.
    """
    ox0, oy0 = map(int, center)
    r = float(radius)
    n = int(max(0, count))

    rng = random.Random(_seed_for(game, "agg_children", agg_id, child_id, ox0, oy0, int(round(r * 1000)), n, int(zone_w), int(zone_h)))

    pts: List[Tuple[int, int]] = []
    made = 0
    attempts = 0
    max_attempts = max(50, n * 30)

    while made < n and attempts < max_attempts:
        attempts += 1
        dx = int(round(rng.gauss(0.0, r / 2.5)))
        dy = int(round(rng.gauss(0.0, r / 2.5)))
        if (dx * dx + dy * dy) > (r * r):
            continue
        x = ox0 + dx
        y = oy0 + dy
        if x < 0 or y < 0 or x >= int(zone_w) or y >= int(zone_h):
            continue
        pts.append((int(x), int(y)))
        made += 1

    return pts


def compute_cluster_children_layout(
    game: "Game",
    *,
    aggregate_ent: object,
    zone_coord: Tuple[int, int, int],
    local_pos: Tuple[int, int],
    zone_w: int,
    zone_h: int,
) -> Tuple[str, List[Tuple[int, int]]]:
    """
    Deterministically compute cluster child layout for an aggregate.

    Returns: (child_proto_id, pts_local)
      - child_proto_id: e.g. "blueberry"
      - pts_local: list of (x,y) points in *local zone coords*

    IMPORTANT:
    - Pure function of (seed, agg_id, child_id, center, radius, count, zone dims)
    - No camera/LoD dependence (prevents shuffling on zoom)
    """
    tags = _tags(aggregate_ent)
    if not isinstance(tags, dict) or not tags.get("aggregate"):
        return ("", [])

    # Only cluster mode supported in Phase 1
    mode = str(tags.get("detail_mode", "cluster"))
    if mode != "cluster":
        return ("", [])

    agg_id = str(getattr(aggregate_ent, "id", "") or "")
    if not agg_id:
        return ("", [])

    child_id = str(tags.get("detail_child") or tags.get("child") or "blueberry")

    radius = float(tags.get("radius", 6.0))
    density = float(tags.get("density", 0.25))
    max_children = int(tags.get("detail_max_children", 120))
    approx = int(max(1, min(max_children, density * math.pi * radius * radius)))

    ox0, oy0 = map(int, local_pos)

    pts = _cluster_points(
        game,
        agg_id=agg_id,
        child_id=child_id,
        center=(ox0, oy0),
        radius=radius,
        count=approx,
        zone_w=int(zone_w),
        zone_h=int(zone_h),
    )
    return (child_id, pts)


def resolve_detail_proxies(
    game: "Game",
    *,
    aggregate_ent: object,
    zone_coord: Tuple[int, int, int],
    local_pos: Tuple[int, int],
    zone_w: int,
    zone_h: int,
    cam_lod: float,
) -> List[DetailProxy]:
    """
    Rendering-time detail: return render-only child entities (proxies).

    Determinism goal:
    - If you zoom in/out, the *same* aggregate produces the *same* child layout.
      (The layout may be hidden/shown by thresholds, but must not shuffle.)
    """
    tags = _tags(aggregate_ent)
    if not tags.get("aggregate"):
        return []
    mode = str(tags.get("detail_mode") or "")
    if mode != "cluster":
        return []

    # When do we show children? (More negative cam_lod = more zoomed in in your system.)
    thresh = float(tags.get("detail_lod_threshold", -1.25))
    if float(cam_lod) > thresh:
        return []

    # Cache proxy layouts per aggregate (NOT per lod band, to prevent shuffling).
    if not hasattr(game, "_agg_proxy_cache"):
        game._agg_proxy_cache = {}  # type: ignore[attr-defined]
    cache: Dict[str, List[DetailProxy]] = game._agg_proxy_cache  # type: ignore[assignment]

    agg_id = _ent_id(aggregate_ent)
    if agg_id in cache:
        return cache[agg_id]

    # Determine child prototype
    child_id = str(tags.get("detail_child") or tags.get("child") or "blueberry")

    # Count heuristic: density * area (clamped). Same tag used for proxy and realization.
    radius = float(tags.get("radius", 6.0))
    density = float(tags.get("density", 0.25))
    max_children = int(tags.get("detail_max_children", 120))
    approx = int(max(1, min(max_children, density * math.pi * radius * radius)))

    zx, zy, zz = map(int, zone_coord)
    ox0, oy0 = map(int, local_pos)
    proxies: List[DetailProxy] = []

    # Resolve child prototype once
    try:
        child_proto = prototypes.resolve_proto(child_id)
    except Exception:
        cache[agg_id] = []
        return []

    # Deterministic points (independent of cam_lod)
    pts = _cluster_points(
        game,
        agg_id=agg_id,
        child_id=child_id,
        center=(ox0, oy0),
        radius=radius,
        count=approx,
        zone_w=int(zone_w),
        zone_h=int(zone_h),
    )

    for i, (x, y) in enumerate(pts):
        # Absolute coords
        ax = int(zx * int(zone_w) + x)
        ay = int(zy * int(zone_h) + y)

        # Build a tiny render proxy entity
        peid = f"{agg_id}:proxy:{i}"
        try:
            pent = spawn_factory.build_entity_from_spec(
                spec=child_proto,
                eid=peid,
                pos=(x, y),
                overrides={"tags": {"render_proxy": True, "from_aggregate": agg_id}},
            )
        except Exception:
            continue

        proxies.append(
            DetailProxy(
                ent=pent,
                abs_x=ax,
                abs_y=ay,
                zone_coord=(zx, zy, zz),
                local_pos=(x, y),
            )
        )

    cache[agg_id] = proxies
    return proxies


# -----------------------------
# Simulation-time realization (interactive)
# -----------------------------

def realize_details_for_loaded_zone(
    game: "Game",
    level: "LevelState",
    *,
    zone_coord: Tuple[int, int, int],
    zone_w: int,
    zone_h: int,
    kinds: Optional[Sequence[str]] = None,
) -> int:
    """DEPRECATED (Yoga): do not stamp aggregate children into LevelState on zone load.

    Detail is realized/staged by the attention lifecycle so it can work via god-vision.
    Zone entry must not create a parallel population (duplication + perf cliffs).

    Returns:
        0 (no entities placed).
    """
    return 0

