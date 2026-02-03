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

    This function MUST be side-effect free with respect to gameplay state:
    it only adds *world entities* to WorldEntityIndex.

    `kinds`: optional filter on aggregate_kind (e.g. ["berry_patch"]).
    """
    # Track which zone buckets we've generated per kind (incremental worldgen).
    if not hasattr(game, "_agg_worldgen_done"):
        game._agg_worldgen_done = set()  # type: ignore[attr-defined]

    # Determine which aggregate prototype ids exist.
    # Convention (for now): aggregate_kind == prototype id.
    # (Later: you can add a mapping table or allow tags to specify proto_id.)
    if kinds is None:
        # First pass: discover from prototypes by scanning templates is expensive;
        # so we default to a safe small list if user hasn't specified.
        kinds = ("berry_patch",)

    for zx in range(int(zx0), int(zx1) + 1):
        for zy in range(int(zy0), int(zy1) + 1):
            for kind in kinds:
                key = (str(kind), int(zx), int(zy), int(zz))
                if key in game._agg_worldgen_done:  # type: ignore[attr-defined]
                    continue

                # Deterministic RNG per (kind, zone bucket)
                rng = random.Random(_seed_for(game, "agg_world", kind, zx, zy, zz))

                # Spawn probability/count heuristics per kind (can be moved to YAML later)
                # For berry_patch test: 35% chance of 1 patch, 10% chance of 2.
                count = 0
                roll = rng.random()
                if roll < 0.10:
                    count = 2
                elif roll < 0.45:
                    count = 1

                if count <= 0:
                    game._agg_worldgen_done.add(key)  # type: ignore[attr-defined]
                    continue

                try:
                    proto = prototypes.resolve_proto(str(kind))
                except Exception:
                    # Prototype doesn't exist (yet); skip cleanly.
                    game._agg_worldgen_done.add(key)  # type: ignore[attr-defined]
                    continue

                for i in range(count):
                    # Choose a location within the zone bucket (avoid extreme edges).
                    ox = rng.randint(2, max(2, int(zone_w) - 3))
                    oy = rng.randint(2, max(2, int(zone_h) - 3))

                    eid = f"agg:{kind}:{zx},{zy},{zz}:{i}"
                    # Build entity using prototype; override ensures world_entity+aggregate tags exist.
                    base_tags = dict(_tags(proto))
                    # If proto is dict, _tags won't work. We'll just pull tags from proto when dict.
                    if isinstance(proto, dict):
                        base_tags = dict(proto.get("tags", {}) or {})

                    overrides = {
                        "tags": {
                            **base_tags,
                            "world_entity": True,
                            "aggregate": True,
                            "aggregate_kind": str(kind),
                            # Ensure detail mode exists (defaults can be in YAML)
                            "detail_mode": base_tags.get("detail_mode", "cluster"),
                        }
                    }

                    try:
                        ent = spawn_factory.build_entity_from_spec(
                            spec=proto,
                            eid=eid,
                            pos=(ox, oy),
                            overrides=overrides,
                        )
                    except Exception:
                        continue

                    try:
                        game.world_entity_index.add(ent, zone_coord=(int(zx), int(zy), int(zz)), local_pos=(int(ox), int(oy)))
                    except Exception:
                        # If index not available, bail (site system probably hasn't initialized it yet)
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
    """
    When a zone is created/entered (simulation allowed), realize aggregate details
    into real entities owned by the LevelState.

    Idempotent per zone instance: doesn't double-spawn for the same aggregate.

    Determinism goal:
    - The *candidate* child positions are the same ones used by render proxies.
    - Placement may skip blocked/occupied tiles, but zoom should never reshuffle.
    """
    if kinds is None:
        kinds = ("berry_patch",)

    if not hasattr(level, "_realized_aggregate_ids"):
        level._realized_aggregate_ids = set()  # type: ignore[attr-defined]
    realized = level._realized_aggregate_ids  # type: ignore[attr-defined]

    placed_total = 0

    # Pull aggregates from world index for this zone bucket
    try:
        refs = list(game.world_entity_index.iter_zone(tuple(map(int, zone_coord))))
    except Exception:
        return 0

    for ref in refs:
        agg = ref.ent
        tags = _tags(agg)
        if not tags.get("aggregate"):
            continue
        kind = str(tags.get("aggregate_kind") or "")
        if kinds and kind not in set(map(str, kinds)):
            continue

        agg_id = _ent_id(agg)
        if agg_id in realized:
            continue

        mode = str(tags.get("detail_mode") or "")
        if mode != "cluster":
            continue

        # Determine child prototype
        child_id = str(tags.get("detail_child") or tags.get("child") or "blueberry")

        radius = float(tags.get("radius", 6.0))
        density = float(tags.get("density", 0.25))
        max_children = int(tags.get("detail_max_children", 120))
        approx = int(max(1, min(max_children, density * math.pi * radius * radius)))

        ox0, oy0 = map(int, ref.local_pos)

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

        made = 0
        for (x, y) in pts:
            # Basic placement constraints
            try:
                if not level.world.in_bounds(int(x), int(y)):
                    continue
                if not level.world.is_walkable(int(x), int(y)):
                    continue
                if game._actor_at(level, (int(x), int(y))):
                    continue
                if game._entity_at(level, (int(x), int(y))):
                    continue
            except Exception:
                pass

            try:
                ent = spawning_system.spawn_entity_from_template(game, child_id, (int(x), int(y)))
                level.entities[ent.id] = ent
                made += 1
                placed_total += 1
            except Exception:
                continue

        realized.add(agg_id)

    return placed_total
