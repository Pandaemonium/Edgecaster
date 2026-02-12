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


# -----------------------------
# Generic resolver recipes (Phase 1: sites + buildings)
# -----------------------------

from dataclasses import dataclass
from typing import Any, Optional

@dataclass(frozen=True)
class SpawnIntent:
    """A deterministic instruction to spawn/stage a child at an ABS position."""
    eid: str
    proto_id: str
    abs_x: int
    abs_y: int
    zz: int
    child_type: str = "entity"   # "entity" or "actor" or "staged"
    staged: Optional[dict] = None  # for staged geometry like floors
    tags: Optional[dict] = None
    lineage_id: Optional[str] = None


def _resolve_recipe_list(parent_tags: dict) -> list[dict]:
    r = parent_tags.get("resolve")
    if isinstance(r, list):
        return [x for x in r if isinstance(x, dict)]
    return []


def _abs_from_zone_local(zx: int, zy: int, zone_w: int, zone_h: int, lx: int, ly: int) -> tuple[int, int]:
    return (int(zx) * int(zone_w) + int(lx), int(zy) * int(zone_h) + int(ly))


def _zone_local_from_abs(ax: int, ay: int, zone_w: int, zone_h: int) -> tuple[tuple[int, int], tuple[int, int]]:
    zxx = int(ax) // int(zone_w)
    zyy = int(ay) // int(zone_h)
    lx = int(ax) - zxx * int(zone_w)
    ly = int(ay) - zyy * int(zone_h)
    return (zxx, zyy), (lx, ly)


def resolve_spawn_intents_from_recipe(
    game: "Game",
    *,
    parent_ent: object,
    zone_coord: tuple[int, int, int],
    local_pos: tuple[int, int],
    zone_w: int,
    zone_h: int,
    zz: int,
    max_depth: int = 2,
) -> list[SpawnIntent]:
    """
    Generic resolver: read parent_ent.tags.resolve and return deterministic SpawnIntents.

    Phase 1 supports:
      - children_fixed + placement.pattern=cluster
      - children_fixed + placement.pattern=scatter_interior (requires a prior geom_rect)
      - geom_rect -> emits wall entities + staged floor tiles
    """
    tags = _tags(parent_ent)
    if not isinstance(tags, dict):
        return []

    # Simple depth limiter to prevent runaway recursion.
    depth = int(tags.get("_resolve_depth", 0) or 0)
    if depth >= int(max_depth):
        return []

    recipes = _resolve_recipe_list(tags)
    if not recipes:
        return []

    parent_id = _ent_id(parent_ent)
    zx, zy, _ = map(int, zone_coord)
    ox0, oy0 = map(int, local_pos)

    intents: list[SpawnIntent] = []

    # We allow geom_rect to define an "interior region" for later scatter rules.
    geom_rects: list[tuple[int, int, int, int]] = []  # abs x0,y0,x1,y1 for each rect

    for rule in recipes:
        kind = str(rule.get("kind") or "").strip().lower()
        if not kind:
            continue

        # -----------------
        # geom_rect
        # -----------------
        if kind == "geom_rect":
            w = int(rule.get("w", 7) or 7)
            h = int(rule.get("h", 7) or 7)
            emit = rule.get("emit") or {}
            if not isinstance(emit, dict):
                emit = {}

            wall_proto = str(emit.get("wall_proto", "wall") or "wall")
            floor = emit.get("floor") or {}
            if not isinstance(floor, dict):
                floor = {}
            floor_glyph = str(floor.get("glyph", ".") or ".")[0]
            fc = floor.get("color", [90, 80, 70])
            floor_color = tuple(fc) if isinstance(fc, (list, tuple)) and len(fc) == 3 else (90, 80, 70)

            # center around parent anchor ABS
            pax, pay = _abs_from_zone_local(zx, zy, zone_w, zone_h, ox0, oy0)
            ax0 = int(pax - w // 2)
            ay0 = int(pay - h // 2)
            ax1 = int(ax0 + w)
            ay1 = int(ay0 + h)
            geom_rects.append((ax0, ay0, ax1, ay1))

            # Optional deterministic door
            door = rule.get("door") or {}
            if not isinstance(door, dict):
                door = {}

            door_enabled = bool(door.get("enabled", True))
            door_side = str(door.get("side", "south") or "south").lower()  # north/south/east/west
            door_proto = str(door.get("proto", "") or "")  # e.g. "door" if you want an entity, otherwise "" = gap only

            door_xy = None
            if door_enabled:
                rng_door = random.Random(_seed_for(game, "resolve_door", parent_id, ax0, ay0, ax1, ay1))
                if door_side in ("north", "south"):
                    y = ay0 if door_side == "north" else (ay1 - 1)
                    x = rng_door.randint(ax0 + 1, ax1 - 2)
                    door_xy = (x, y)
                else:
                    x = ax0 if door_side == "west" else (ax1 - 1)
                    y = rng_door.randint(ay0 + 1, ay1 - 2)
                    door_xy = (x, y)


            # perimeter walls + interior floors
            for y in range(ay0, ay1):
                for x in range(ax0, ax1):
                    is_wall = (x == ax0 or x == ax1 - 1 or y == ay0 or y == ay1 - 1)
                    if is_wall:
                        # carve door gap
                        if door_xy is not None and (x, y) == door_xy:
                            # optionally spawn a door entity at the gap
                            if door_proto:
                                eid = f"{parent_id}:door:{x},{y}"
                                intents.append(
                                    SpawnIntent(
                                        eid=eid,
                                        proto_id=door_proto,
                                        abs_x=int(x),
                                        abs_y=int(y),
                                        zz=int(zz),
                                        child_type="entity",
                                        tags={"structure": True, "door": True, "owner": parent_id, "_resolve_depth": depth + 1},
                                        lineage_id=eid,
                                    )
                                )
                            else:
                                # If no door entity, at least ensure a walkable floor tile here:
                                eid = f"{parent_id}:floor:{x},{y}"
                                intents.append(
                                    SpawnIntent(
                                        eid=eid,
                                        proto_id="",
                                        abs_x=int(x),
                                        abs_y=int(y),
                                        zz=int(zz),
                                        child_type="staged",
                                        staged={
                                            "kind": "structure",
                                            "glyph": floor_glyph,
                                            "color": floor_color,
                                            "base_size": 1.0,
                                            "tags": {"structure": True, "floor": True, "owner": parent_id, "_resolve_depth": depth + 1},
                                        },
                                        lineage_id=eid,
                                    )
                                )
                            continue 
                        eid = f"{parent_id}:wall:{x},{y}"
                        intents.append(
                            SpawnIntent(
                                eid=eid,
                                proto_id=wall_proto,
                                abs_x=int(x),
                                abs_y=int(y),
                                zz=int(zz),
                                child_type="entity",
                                tags={"structure": True, "wall": True, "owner": parent_id, "_resolve_depth": depth + 1},
                                lineage_id=eid,
                            )
                        )
                    else:
                        eid = f"{parent_id}:floor:{x},{y}"
                        intents.append(
                            SpawnIntent(
                                eid=eid,
                                proto_id="",
                                abs_x=int(x),
                                abs_y=int(y),
                                zz=int(zz),
                                child_type="staged",
                                staged={
                                    "kind": "structure",
                                    "glyph": floor_glyph,
                                    "color": floor_color,
                                    "base_size": 1.0,
                                    "tags": {"structure": True, "floor": True, "owner": parent_id, "_resolve_depth": depth + 1},
                                },
                                lineage_id=eid,
                            )
                        )
            continue

        # -----------------
        # children_fixed
        # -----------------
        if kind == "children_fixed":
            children = rule.get("children") or []
            if not isinstance(children, list):
                continue
            children = [str(c or "") for c in children if str(c or "").strip()]
            if not children:
                continue

            placement = rule.get("placement") or {}
            if not isinstance(placement, dict):
                placement = {}
            pattern = str(placement.get("pattern", "cluster") or "cluster").strip().lower()
            salt = str(placement.get("salt", kind) or kind)

            # Base ABS anchor (parent)
            pax, pay = _abs_from_zone_local(zx, zy, zone_w, zone_h, ox0, oy0)

            if pattern == "cluster":
                radius = float(placement.get("radius", 8) or 8)
                min_sep = float(placement.get("min_sep", 0) or 0)
                min_sep2 = min_sep * min_sep

                rng = random.Random(_seed_for(game, "resolve_cluster", parent_id, salt, pax, pay, int(round(radius * 1000)), len(children)))

                pts: list[tuple[int, int]] = []
                attempts = 0
                while len(pts) < len(children) and attempts < 6000:
                    attempts += 1

                    dx = int(round(rng.gauss(0.0, radius / 2.5)))
                    dy = int(round(rng.gauss(0.0, radius / 2.5)))
                    if (dx * dx + dy * dy) > (radius * radius):
                        continue

                    x = pax + dx
                    y = pay + dy

                    # uniqueness
                    if (x, y) in pts:
                        continue

                    # separation constraint
                    if min_sep > 0:
                        too_close = False
                        for (px2, py2) in pts:
                            ddx = (x - px2)
                            ddy = (y - py2)
                            if (ddx * ddx + ddy * ddy) < min_sep2:
                                too_close = True
                                break
                        if too_close:
                            continue

                    pts.append((x, y))

                for i, proto_id in enumerate(children):
                    if i >= len(pts):
                        break
                    ax, ay = pts[i]
                    eid = f"{parent_id}:child:{salt}:{proto_id}:{i}"
                    intents.append(
                        SpawnIntent(
                            eid=eid,
                            proto_id=proto_id,
                            abs_x=int(ax),
                            abs_y=int(ay),
                            zz=int(zz),
                            child_type="entity",
                            tags={"from_parent": parent_id, "_resolve_depth": depth + 1},
                            lineage_id=eid,
                        )
                    )
                continue


                for i, proto_id in enumerate(children):
                    if i >= len(pts):
                        break
                    ax, ay = pts[i]
                    eid = f"{parent_id}:child:{salt}:{proto_id}:{i}"
                    intents.append(
                        SpawnIntent(
                            eid=eid,
                            proto_id=proto_id,
                            abs_x=int(ax),
                            abs_y=int(ay),
                            zz=int(zz),
                            child_type="entity",
                            tags={"from_parent": parent_id, "_resolve_depth": depth + 1},
                            lineage_id=eid,
                        )
                    )
                continue

            if pattern == "scatter_interior":
                # Requires a prior geom_rect to know interior
                if not geom_rects:
                    continue
                ax0, ay0, ax1, ay1 = geom_rects[-1]
                interior: list[tuple[int, int]] = []
                for y in range(ay0 + 1, ay1 - 1):
                    for x in range(ax0 + 1, ax1 - 1):
                        interior.append((x, y))
                if not interior:
                    continue
                rng = random.Random(_seed_for(game, "resolve_interior", parent_id, salt, ax0, ay0, ax1, ay1, len(children)))
                rng.shuffle(interior)

                for i, proto_id in enumerate(children):
                    x, y = interior[i % len(interior)]
                    eid = f"{parent_id}:child:{salt}:{proto_id}:{i}"
                    # NPCs are actors today; everything else is entity.
                    # We keep this heuristic only in aggregate_resolution (not attention):
                    child_type = "actor" if proto_id in ("chakra_sage", "merchant", "local_guide", "lair_informant") else "entity"
                    intents.append(
                        SpawnIntent(
                            eid=eid,
                            proto_id=proto_id,
                            abs_x=int(x),
                            abs_y=int(y),
                            zz=int(zz),
                            child_type=child_type,
                            tags={"from_parent": parent_id, "_resolve_depth": depth + 1},
                            lineage_id=eid,
                        )
                    )
                continue

        # -----------------
        # children_pool  (Phase 1: deterministic item shelves / clutter)
        # -----------------
        if kind == "children_pool":
            pool = rule.get("pool") or []
            if not isinstance(pool, list):
                continue
            pool = [str(p or "") for p in pool if str(p or "").strip()]
            if not pool:
                continue

            placement = rule.get("placement") or {}
            if not isinstance(placement, dict):
                placement = {}
            pattern = str(placement.get("pattern", "scatter_interior") or "scatter_interior").strip().lower()
            salt = str(placement.get("salt", kind) or kind)

            # Requires a prior geom_rect to know interior
            if pattern != "scatter_interior":
                continue
            if not geom_rects:
                continue

            ax0, ay0, ax1, ay1 = geom_rects[-1]
            interior: list[tuple[int, int]] = []
            for y in range(ay0 + 1, ay1 - 1):
                for x in range(ax0 + 1, ax1 - 1):
                    interior.append((x, y))
            if not interior:
                continue

            # count can be:
            #  - int
            #  - "fill_interior" (string) to fill every interior tile
            raw_count = rule.get("count", 0)
            if isinstance(raw_count, str) and raw_count.strip().lower() == "fill_interior":
                target_n = len(interior)
            else:
                try:
                    target_n = int(raw_count or 0)
                except Exception:
                    target_n = 0
                if target_n <= 0:
                    continue
                target_n = min(target_n, len(interior))

            # Deterministic spawn order: shuffle pool, repeat as needed, and lay onto a shuffled interior list.
            rng = random.Random(_seed_for(game, "resolve_pool", parent_id, salt, ax0, ay0, ax1, ay1, len(pool), target_n))

            interior2 = list(interior)
            rng.shuffle(interior2)

            pool2 = list(pool)
            rng.shuffle(pool2)

            spawn_queue: list[str] = []
            while len(spawn_queue) < target_n:
                # repeat but reshuffle each cycle so repeats aren't identical blocks
                batch = list(pool2)
                rng.shuffle(batch)
                spawn_queue.extend(batch)

            for i in range(target_n):
                proto_id = spawn_queue[i]
                x, y = interior2[i % len(interior2)]
                eid = f"{parent_id}:pool:{salt}:{proto_id}:{i}"
                intents.append(
                    SpawnIntent(
                        eid=eid,
                        proto_id=proto_id,
                        abs_x=int(x),
                        abs_y=int(y),
                        zz=int(zz),
                        child_type="entity",
                        tags={"from_parent": parent_id, "_resolve_depth": depth + 1, "spawned_by": "children_pool"},
                        lineage_id=eid,
                    )
                )
            continue


    return intents



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

