"""Attention-driven render candidate assembly and world proxy staging.

This module is the bridge between the ABS-space world ontology and the
per-frame render candidate list.  Its central job is to decide *which* world
entities (sites, POIs, aggregates) should be visible and materialized at the
current camera LoD, without triggering gameplay-side-effects.

Key responsibilities:
- `renderables_in_abs_rect`: builds the render candidate list for the current
  view rect, merging live level actors/entities with world-level proxy stubs.
- World proxy staging helpers: `ensure_site_entities`, `ensure_poi_entities`,
  `ensure_aggregate_entities`, `realize_aggregate_details_in_zone` — these
  decide when a world-level stub should be promoted to a realized interactive
  entity and when it should remain a lightweight render proxy.
- Suppression tracking: entities that have been removed/killed in a zone are
  suppressed in the world index so they do not reappear on the overmap.
- `sync_attention_instantiation` (called from `game.py`): ensures the staged
  ontology matches the last known view before each game tick so render
  candidates and simulation state stay aligned.

Invariant: none of the query paths in this module may instantiate zones or
directly mutate gameplay state.  Side effects (spawning real actors, marking
entities removed) are intentional and go through `game` delegate calls, but
the render query path itself must remain free of observable gameplay mutations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Tuple

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.content import npcs
from edgecaster.systems import aggregate_resolution as aggregate_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
from edgecaster.systems import entity_snapshots as entity_snapshots_system
from edgecaster.systems import spawning as spawning_system
from edgecaster.systems.world_entity_index import WorldEntityIndex
from edgecaster.state import chakra_component as chakra_component_state


_PRIMARY_ENTITY_STATE_KEYS = {
    "removed",
    "dead",
    "removed_reason",
    "dead_reason",
    "updated_tick",
    "owner_id",
    "parent_entity_id",
    "socket_id",
    "in_inventory",
}


# [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
# The persistence/suppression wrappers in this block keep older attention paths
# working while entity_state, snapshots, and attention still overlap. Once
# attention reads lifecycle state through one shared authority path, collapse
# these helpers into direct calls or delete them.
def _peek_entity_state(game: object, key: str) -> Dict[str, Any]:
    store = getattr(game, "entity_state", None)
    if isinstance(store, dict):
        st = store.get(str(key))
        if isinstance(st, dict):
            return st
        return {}
    try:
        getter = getattr(game, "get_entity_state", None)
        if callable(getter):
            st = getter(str(key))
            if isinstance(st, dict):
                return st
    except Exception:
        pass
    return {}


def _normalize_optional_lineage_id(raw: object) -> str | None:
    try:
        lineage_id = str(raw or "").strip()
    except Exception:
        lineage_id = ""
    return lineage_id or None


def _effective_entity_state(game: object, *, entity_id: str | None, lineage_id: str | None) -> Dict[str, Any]:
    lid = str(lineage_id or "").strip()
    eid = str(entity_id or "").strip()
    try:
        get_effective = getattr(game, "get_effective_entity_state", None)
        if callable(get_effective) and (eid or lid):
            st = get_effective(eid or lid, lineage_id=lid or None)
            if isinstance(st, dict):
                return dict(st)
    except Exception:
        pass

    primary: Dict[str, Any] = {}
    fallback: Dict[str, Any] = {}
    # Unification note: lineage_id merging is still a migration bridge. Once
    # graph/persistence cutover is complete, entity_id should be authoritative
    # and lineage should survive only as contextual provenance/history.
    if eid:
        st = _peek_entity_state(game, eid)
        if isinstance(st, dict):
            primary = dict(st)
    if lid and lid != eid:
        st = _peek_entity_state(game, lid)
        if isinstance(st, dict):
            fallback = dict(st)
    if not primary:
        return fallback

    out: Dict[str, Any] = {}
    for key, value in fallback.items():
        if str(key) not in _PRIMARY_ENTITY_STATE_KEYS:
            out[str(key)] = value
    out.update(primary)
    return out


def _entity_is_suppressed(game: object, entity_id: str, *, lineage_id: str | None = None) -> bool:
    lid = str(lineage_id or "").strip()
    st2 = _effective_entity_state(game, entity_id=str(entity_id or ""), lineage_id=lid)
    if bool(st2.get("removed")) or bool(st2.get("dead")):
        return True

    try:
        fn = getattr(game, "entity_is_suppressed", None)
        if callable(fn):
            if entity_id and bool(fn(entity_id)):
                return True
    except Exception:
        pass
    return False


def _mark_entity_removed(game: object, entity_id: str, *, reason: str, lineage_id: str | None = None) -> None:
    try:
        fn = getattr(game, "patch_entity_state", None)
        if callable(fn):
            kwargs = {
                "removed": True,
                "removed_reason": str(reason or "removed"),
            }
            lid = str(lineage_id or "").strip()
            if lid:
                kwargs["lineage_id"] = lid
            try:
                fn(str(entity_id), **kwargs)
            except TypeError:
                fn(str(entity_id), removed=True, removed_reason=str(reason or "removed"))
    except Exception:
        pass


def _mark_removed(game: object, *, entity_id: str, reason: str, lineage_id: str | None = None) -> None:
    if entity_id:
        _mark_entity_removed(game, str(entity_id), reason=reason, lineage_id=lineage_id)


def _mark_entity_live(game: object, entity_id: str, *, lineage_id: str | None = None) -> None:
    try:
        fn = getattr(game, "patch_entity_state", None)
        if callable(fn):
            kwargs = {
                "removed": False,
                "dead": False,
            }
            lid = str(lineage_id or "").strip()
            if lid:
                kwargs["lineage_id"] = lid
            try:
                fn(str(entity_id), **kwargs)
            except TypeError:
                fn(str(entity_id), removed=False, dead=False)
    except Exception:
        pass


def _is_suppressed(game: object, *, entity_id: str | None = None, lineage_id: str | None = None) -> bool:
    if entity_id and _entity_is_suppressed(game, str(entity_id), lineage_id=lineage_id):
        return True
    lid = str(lineage_id or "").strip()
    if lid and not entity_id:
        st = _peek_entity_state(game, lid)
        if bool(st.get("removed")) or bool(st.get("dead")):
            return True
        try:
            fn = getattr(game, "entity_is_suppressed", None)
            if callable(fn) and bool(fn(lid)):
                return True
        except Exception:
            pass
    return False


def _apply_persisted_entity_state(
    game: object,
    obj: object,
    *,
    entity_id: str,
    lineage_id: str | None,
) -> bool:
    """Apply persisted lineage/entity deltas to runtime object; return False if suppressed."""
    state = _effective_entity_state(game, entity_id=str(entity_id or ""), lineage_id=str(lineage_id or ""))
    return entity_snapshots_system.apply_entity_snapshot(obj, state, lineage_id=lineage_id)


def _persist_entity_snapshot(
    game: object,
    obj: object,
    *,
    entity_id: str,
    lineage_id: str | None = None,
) -> None:
    """Persist mutable runtime deltas for deterministic descendants."""
    entity_snapshots_system.persist_entity_snapshot(
        game,
        obj,
        entity_id=str(entity_id or ""),
        lineage_id=lineage_id,
    )


def _resolve_depth_ladder(game: object) -> tuple[tuple[float, int], ...]:
    """Return ordered (cam_lod_cutoff, max_depth) thresholds for resolver expansion."""
    default_ladder = (
        (1.20, 1),
        (0.60, 2),
        (-0.10, 3),
        (-0.80, 4),
        (-1.50, 5),
        (-2.30, 6),
    )
    cfg = getattr(game, "cfg", None)
    raw = getattr(cfg, "resolve_depth_ladder", None) if cfg is not None else None
    if not isinstance(raw, (list, tuple)):
        return default_ladder

    parsed: list[tuple[float, int]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            cutoff = float(row[0])
            depth = int(row[1])
        except Exception:
            continue
        parsed.append((cutoff, max(0, depth)))
    if not parsed:
        return default_ladder
    return tuple(sorted(parsed, key=lambda x: float(x[0]), reverse=True))


def _resolve_max_depth_for_lod(game: object, *, cam_lod: float, parent_tags: Dict | None = None) -> int:
    """Compute resolver depth cap from camera LoD with optional per-parent overrides."""
    tags = parent_tags if isinstance(parent_tags, dict) else {}
    cfg = getattr(game, "cfg", None)

    try:
        disable_above = float(getattr(cfg, "resolve_disable_above_lod", 2.0) or 2.0)
    except Exception:
        disable_above = 2.0
    if float(cam_lod) > float(disable_above):
        return 0

    try:
        lod_bias = float(tags.get("resolve_lod_bias", 0.0) or 0.0)
    except Exception:
        lod_bias = 0.0
    effective_lod = float(cam_lod) - float(lod_bias)

    depth = 0
    for cutoff, max_depth in _resolve_depth_ladder(game):
        if effective_lod <= float(cutoff):
            depth = max(depth, int(max_depth))

    try:
        global_cap = int(getattr(cfg, "resolve_depth_cap_default", 6) or 6)
    except Exception:
        global_cap = 6
    global_cap = max(0, min(7, int(global_cap)))

    parent_cap = global_cap
    if isinstance(tags, dict) and "resolve_max_depth" in tags:
        try:
            parent_cap = int(tags.get("resolve_max_depth"))
        except Exception:
            parent_cap = global_cap
    parent_cap = max(0, min(7, int(parent_cap)))
    return max(0, min(int(depth), int(parent_cap)))


def _resolve_distance_depth_ladder(game: object) -> tuple[tuple[float, int], ...]:
    """Return ordered (distance_in_screens, max_depth) thresholds."""
    default_ladder = (
        (1.25, 6),
        (2.50, 5),
        (4.00, 4),
        (7.00, 3),
        (12.0, 2),
        (9999.0, 1),
    )
    cfg = getattr(game, "cfg", None)
    raw = getattr(cfg, "resolve_distance_depth_ladder", None) if cfg is not None else None
    if not isinstance(raw, (list, tuple)):
        return default_ladder

    parsed: list[tuple[float, int]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            max_screens = float(row[0])
            depth = int(row[1])
        except Exception:
            continue
        parsed.append((max(0.0, max_screens), max(0, min(7, depth))))
    if not parsed:
        return default_ladder
    return tuple(sorted(parsed, key=lambda x: float(x[0])))


def _resolve_max_depth_for_distance(
    game: object,
    *,
    distance_screens: float,
    parent_tags: Dict | None = None,
) -> int:
    tags = parent_tags if isinstance(parent_tags, dict) else {}
    try:
        dist_bias = float(tags.get("resolve_distance_bias_screens", 0.0) or 0.0)
    except Exception:
        dist_bias = 0.0
    effective = max(0.0, float(distance_screens) - float(dist_bias))
    for max_screens, depth in _resolve_distance_depth_ladder(game):
        if effective <= float(max_screens):
            return int(depth)
    return 1


def _entity_abs_anchor(
    ent: object,
    *,
    zone_coord: tuple[int, int, int],
    local_pos: tuple[int, int],
    zone_w: int,
    zone_h: int,
) -> tuple[int, int]:
    ap = getattr(ent, "abs_pos", None)
    if isinstance(ap, (list, tuple)) and len(ap) >= 2:
        try:
            return (int(ap[0]), int(ap[1]))
        except Exception:
            pass
    zx, zy, _ = zone_coord
    lx, ly = local_pos
    return (int(zx) * int(zone_w) + int(lx), int(zy) * int(zone_h) + int(ly))


def _coerce_chakra_component_for_entity(ent: object):
    try:
        raw = getattr(ent, "chakra_component", None)
    except Exception:
        raw = None
    try:
        eid = str(getattr(ent, "entity_id", "") or getattr(ent, "id", "") or "").strip()
    except Exception:
        eid = ""
    if not eid:
        return None

    max_hp = None
    try:
        stats = getattr(ent, "stats", None)
        if stats is not None:
            raw_hp = getattr(stats, "max_hp", None)
            if raw_hp is not None:
                max_hp = float(raw_hp)
    except Exception:
        max_hp = None

    # Unification note: attention currently coerces components opportunistically.
    # Once the shared entity/chakra substrate is authoritative, these call sites
    # should mostly become validation/checkpoints instead of shape-repair hooks.
    try:
        comp = chakra_component_state.coerce_chakra_component(
            raw,
            entity_id=eid,
            max_hp=max_hp,
            mass=1.0,
        )
        setattr(ent, "chakra_component", comp)
        return comp
    except Exception:
        return None


def _link_parent_child_chakra(parent: object, child: object) -> None:
    """Create parent->child hierarchy edge; future sibling edges can layer on top."""
    # Unification note: this is still a lightweight attention-time stitch. The
    # final system needs authoritative graph edges with provenance, geometry
    # payloads, and pattern invalidation instead of remote placeholder nodes.
    pcomp = _coerce_chakra_component_for_entity(parent)
    ccomp = _coerce_chakra_component_for_entity(child)
    if pcomp is None or ccomp is None:
        return
    src = str(getattr(pcomp, "root_node_id", "") or "").strip()
    dst = str(getattr(ccomp, "root_node_id", "") or "").strip()
    if not src or not dst:
        return
    edge_id = f"{src}->contains->{dst}"
    try:
        # Keep a lightweight remote-child node in the parent graph so future
        # lateral/sibling edges can be authored without changing storage shape.
        nodes = getattr(pcomp, "nodes", None)
        if not isinstance(nodes, dict):
            nodes = {}
            pcomp.nodes = nodes
        if dst not in nodes:
            nodes[dst] = chakra_component_state.ChakraNode(
                node_id=dst,
                kind="external_child_root",
                active=True,
                channels={},
                tags={
                    "external_ref": True,
                    "entity_id": str(getattr(child, "entity_id", "") or getattr(child, "id", "") or ""),
                },
            )

        edges = getattr(pcomp, "edges", None)
        if not isinstance(edges, dict):
            edges = {}
            pcomp.edges = edges
        if edge_id not in edges:
            edges[edge_id] = chakra_component_state.ChakraEdge(
                edge_id=edge_id,
                src_node_id=src,
                dst_node_id=dst,
                kind="contains",
                propagation="automatic",
                weight=1.0,
                tags={
                    "edge_scope": "parent_child",
                    "extensible": "sibling_ready",
                    "dst_entity_id": str(getattr(child, "entity_id", "") or getattr(child, "id", "") or ""),
                },
            )
    except Exception:
        pass
    try:
        croot = ccomp.nodes.get(dst)
        if croot is not None:
            croot.tags["parent_root_node_id"] = src
    except Exception:
        pass


@dataclass
class _YogaStagedEntity:
    """Minimal entity-like object for attention-staged renderables (e.g., structure walls).

    We use this when we don't yet have a full prototype/spec for an entity, but we still
    want the renderer + LoD system to treat it as a real object. It intentionally mirrors
    the small surface area that renderables_in_abs_rect() relies on: id, pos, abs_pos,
    kind, tags, glyph, color/base_size.
    """
    id: str
    pos: Tuple[int, int]
    abs_pos: Tuple[int, int]
    kind: str = "structure"
    glyph: str = "#"
    color: Tuple[int, int, int] = (140, 120, 100)
    base_size: float = 1.0
    tags: Dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.tags.get("name", self.id)


class AttentionCellStore:
    """Unified cache of instantiated entities keyed by a hierarchical ABS-space index.

    Public methods stay intentionally small:
      - `stage(obj, abs_x, abs_y, zz)`
      - `despawn(eid)`
      - `query_abs_rect(abs_rect, zz=...)`

    Internally we bucket entities into scale-aware rect cells so large
    footprints do not explode into duplicates and queries can respect
    footprint rectangles instead of point anchors.

    [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
    AttentionCellStore is still a parallel runtime cache. The end-state is one
    authoritative realization/index path shared with the entity graph and
    geometry query system.
    """

    def __init__(self, *, bin_size: int = 32) -> None:
        self.bin_size = max(1, int(bin_size))
        self.entities: Dict[str, object] = {}
        self.eid_to_bin: Dict[str, Tuple[int, int, int, int]] = {}
        self.bins: Dict[Tuple[int, int, int, int], List[str]] = {}
        self.entity_rects: Dict[str, Tuple[float, float, float, float]] = {}
        self.entity_cells: Dict[str, List[Tuple[int, int, int, int]]] = {}

    @staticmethod
    def _normalize_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x0, y0, x1, y1 = rect
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        return (float(x0), float(y0), float(x1), float(y1))

    @staticmethod
    def _coerce_rect(raw: object) -> Tuple[float, float, float, float] | None:
        if isinstance(raw, dict):
            if all(k in raw for k in ("x0", "y0", "x1", "y1")):
                try:
                    return AttentionCellStore._normalize_rect(
                        (
                            float(raw["x0"]),
                            float(raw["y0"]),
                            float(raw["x1"]),
                            float(raw["y1"]),
                        )
                    )
                except Exception:
                    return None
            return None
        if isinstance(raw, (tuple, list)) and len(raw) >= 4:
            try:
                return AttentionCellStore._normalize_rect(
                    (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
                )
            except Exception:
                return None
        return None

    def _rect_for_obj(
        self,
        obj: object,
        *,
        abs_x: float,
        abs_y: float,
    ) -> Tuple[float, float, float, float]:
        rect = self._coerce_rect(getattr(obj, "footprint_abs", None))
        if rect is None:
            tags = getattr(obj, "tags", None)
            if isinstance(tags, dict):
                rect = self._coerce_rect(tags.get("footprint_abs"))

        if rect is None:
            size_val = None
            for raw in (
                getattr(obj, "abs_size", None),
                getattr(obj, "base_size", None),
            ):
                if isinstance(raw, (int, float)) and float(raw) > 1.0:
                    size_val = float(raw)
                    break
            if size_val is None:
                tags = getattr(obj, "tags", None)
                if isinstance(tags, dict):
                    raw = tags.get("abs_size")
                    if isinstance(raw, (int, float)) and float(raw) > 1.0:
                        size_val = float(raw)
            if size_val is not None:
                half = 0.5 * float(size_val)
                rect = (float(abs_x) - half, float(abs_y) - half, float(abs_x) + half, float(abs_y) + half)
            else:
                rect = (float(abs_x), float(abs_y), float(abs_x) + 1.0, float(abs_y) + 1.0)
        return self._normalize_rect(rect)

    def _cell_size_for_rect(self, rect: Tuple[float, float, float, float]) -> Tuple[int, float]:
        x0, y0, x1, y1 = rect
        max_dim = max(1.0, float(x1) - float(x0), float(y1) - float(y0))
        cell_size = float(self.bin_size)
        level = 0
        while cell_size < max_dim and level < 16:
            cell_size *= 2.0
            level += 1
        return (int(level), float(cell_size))

    def _cells_for_rect(
        self,
        rect: Tuple[float, float, float, float],
        *,
        level: int,
        cell_size: float,
        zz: int,
    ) -> List[Tuple[int, int, int, int]]:
        x0, y0, x1, y1 = rect
        bx0 = int(math.floor(float(x0) / float(cell_size)))
        by0 = int(math.floor(float(y0) / float(cell_size)))
        bx1 = int(math.floor((float(x1) - 1e-6) / float(cell_size)))
        by1 = int(math.floor((float(y1) - 1e-6) / float(cell_size)))
        out: List[Tuple[int, int, int, int]] = []
        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                out.append((int(level), int(bx), int(by), int(zz)))
        return out

    def _remove_entity_cells(self, eid: str) -> None:
        prev_cells = self.entity_cells.pop(str(eid), None)
        if prev_cells:
            for cell in prev_cells:
                try:
                    ids = self.bins.get(cell)
                    if ids and eid in ids:
                        ids.remove(eid)
                    if not ids:
                        self.bins.pop(cell, None)
                except Exception:
                    pass
        self.entity_rects.pop(str(eid), None)
        self.eid_to_bin.pop(str(eid), None)

    def stage(self, obj: object, *, abs_x: float, abs_y: float, zz: int) -> str:
        eid = str(getattr(obj, "id", "") or "")
        if not eid:
            raise ValueError("staged object missing id")
        try:
            if getattr(obj, "abs_pos", None) is None:
                obj.abs_pos = (int(abs_x), int(abs_y))
        except Exception:
            pass

        rect = self._rect_for_obj(obj, abs_x=abs_x, abs_y=abs_y)
        level, cell_size = self._cell_size_for_rect(rect)
        cells = self._cells_for_rect(rect, level=level, cell_size=cell_size, zz=int(zz))

        self._remove_entity_cells(eid)

        self.entities[eid] = obj
        self.entity_rects[eid] = rect
        self.entity_cells[eid] = list(cells)
        self.eid_to_bin[eid] = cells[0] if cells else (int(level), 0, 0, int(zz))
        for cell in cells:
            self.bins.setdefault(cell, []).append(eid)
        return eid

    def despawn(self, eid: str) -> None:
        eid = str(eid)
        self._remove_entity_cells(eid)
        self.entities.pop(eid, None)

    def query_abs_rect(self, abs_rect: Tuple[float, float, float, float], *, zz: int) -> List[Tuple[object, float, float]]:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
        if ax1 < ax0:
            ax0, ax1 = ax1, ax0
        if ay1 < ay0:
            ay0, ay1 = ay1, ay0
        if ax1 == ax0 or ay1 == ay0:
            return []

        out: List[Tuple[object, float, float]] = []
        seen: set[str] = set()
        levels = sorted({cell[0] for cell in self.bins.keys() if int(cell[3]) == int(zz)})
        for level in levels:
            cell_size = float(self.bin_size) * float(2 ** int(level))
            for cell in self._cells_for_rect((ax0, ay0, ax1, ay1), level=int(level), cell_size=cell_size, zz=int(zz)):
                ids = self.bins.get(cell)
                if not ids:
                    continue
                for eid in ids:
                    if eid in seen:
                        continue
                    obj = self.entities.get(eid)
                    if obj is None:
                        continue
                    rect = self.entity_rects.get(eid)
                    if rect is None:
                        continue
                    rx0, ry0, rx1, ry1 = rect
                    if rx1 <= ax0 or rx0 >= ax1 or ry1 <= ay0 or ry0 >= ay1:
                        continue
                    ap = getattr(obj, "abs_pos", None)
                    if isinstance(ap, (tuple, list)) and len(ap) >= 2:
                        ax = float(ap[0])
                        ay = float(ap[1])
                    else:
                        ax = float(rx0)
                        ay = float(ry0)
                    seen.add(eid)
                    out.append((obj, ax, ay))
        return out

def sync_attention_instantiation(game, abs_rect: tuple[float, float, float, float], *, cam_lod: float) -> None:
    """
    Phase-1 LifecycleManager (Route 2): stage/unstage real entities based on attention.

    - May instantiate/de-instantiate entities as camera pans/zooms (even while paused).
    - Must NOT advance time or apply simulation deltas.
    - Deterministic layout for berry_patch -> berries (cluster mode).
    - Derived children are staged into game.attn_store (ABS-binned), not into zones.
    - Basic persistence (Phase 1): if a spawned berry disappears from the *loaded* zone (picked up),
      we record its slot as harvested on the aggregate so it won't respawn.
    """
    # Remember latest view (used as a safety net before time advances)
    try:
        game._last_view_abs_rect = tuple(map(float, abs_rect))
        game._last_view_cam_lod = float(cam_lod)
    except Exception:
        pass

    try:
        current_rect = tuple(map(float, abs_rect))
    except Exception:
        current_rect = None
    try:
        previous_rect = tuple(getattr(game, "_attn_last_sync_abs_rect", ()) or ())
    except Exception:
        previous_rect = ()
    try:
        previous_lod = float(getattr(game, "_attn_last_sync_cam_lod", 0.0))
        has_previous_lod = hasattr(game, "_attn_last_sync_cam_lod")
    except Exception:
        previous_lod = 0.0
        has_previous_lod = False

    if current_rect is not None and len(previous_rect) == 4 and has_previous_lod:
        same_rect = all(abs(float(a) - float(b)) <= 1e-6 for a, b in zip(current_rect, previous_rect))
        same_lod = abs(float(cam_lod) - float(previous_lod)) <= 1e-6
        if same_rect and same_lod:
            try:
                levels = getattr(game, "levels", None)
                has_spatial_dirty = False
                if isinstance(levels, dict):
                    for level in levels.values():
                        if bool(getattr(level, "spatial_dirty", False)):
                            has_spatial_dirty = True
                            break
                if not has_spatial_dirty:
                    return
            except Exception:
                pass

    try:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
    except Exception:
        return
    if ax1 < ax0:
        ax0, ax1 = ax1, ax0
    if ay1 < ay0:
        ay0, ay1 = ay1, ay0
    if ax1 == ax0 or ay1 == ay0:
        return

    cfg = getattr(game, "cfg", None)

    # Zone dimensions are still used as a spatial hash (NOT ontology).
    zone_w = int(getattr(cfg, "world_width", 0) or 0)
    zone_h = int(getattr(cfg, "world_height", 0) or 0)
    if zone_w <= 0 or zone_h <= 0:
        try:
            lvl0 = game._level()
            zone_w = int(getattr(lvl0.world, "width", 60) or 60)
            zone_h = int(getattr(lvl0.world, "height", 40) or 40)
        except Exception:
            zone_w = 60
            zone_h = 40

    # Ensure world-map site entities exist (yogic: everything is an entity).
    # Idempotent; bridges Game.__init__ init order.
    from edgecaster.systems import site_placement as site_placement_system
    site_placement_system.ensure_world_sites(game)


    # Current depth only in Phase 1
    _czx, _czy, zz = getattr(game, "zone_coord", (0, 0, 0))
    zz = int(zz)

    # Warm rect (reuse same idea as renderables_in_abs_rect)
    pad_tiles = float(getattr(cfg, "entity_render_pad_tiles", 0.0) or 0.0)
    if pad_tiles <= 0.0:
        pad_tiles = 0.5 * max((ax1 - ax0), (ay1 - ay0))

    wx0 = ax0 - pad_tiles
    wy0 = ay0 - pad_tiles
    wx1 = ax1 + pad_tiles
    wy1 = ay1 + pad_tiles

    # Camera center (ABS)
    ccx = (ax0 + ax1) * 0.5
    ccy = (ay0 + ay1) * 0.5

    # Union with player abs_pos ONLY if camera is reasonably close to the player.
    # (God-vision / remote panning must not create a giant "bridge" warm rect.)
    try:
        pax, pay = game._get_player_abs()
        dx = float(pax) - float(ccx)
        dy = float(pay) - float(ccy)
        near_thresh = 2.0 * float(pad_tiles)
        if (dx * dx + dy * dy) <= (near_thresh * near_thresh):
            wx0 = min(wx0, float(pax) - pad_tiles)
            wy0 = min(wy0, float(pay) - pad_tiles)
            wx1 = max(wx1, float(pax) + pad_tiles)
            wy1 = max(wy1, float(pay) + pad_tiles)
    except Exception:
        pass

    world_index = getattr(game, "world_entity_index", None)
    if world_index is None:
        return

    # Query macro entities in warm rect (clamped around camera so god-vision doesn't stage the universe)
    max_zone_span = int(getattr(cfg, "render_max_zone_span", 9) or 9)
    max_zone_span = max(1, int(max_zone_span))

    # Compute warm-rect zone window
    zx0 = int(math.floor(wx0 / float(zone_w)))
    zy0 = int(math.floor(wy0 / float(zone_h)))
    zx1 = int(math.floor((wx1 - 1e-9) / float(zone_w)))
    zy1 = int(math.floor((wy1 - 1e-9) / float(zone_h)))

    zx0c, zx1c, zy0c, zy1c, was_clamped = game._clamp_zone_window(
        zx0, zx1, zy0, zy1,
        zone_span_cap=max_zone_span,
        ccx=ccx, ccy=ccy,
        zone_w=zone_w, zone_h=zone_h,
    )

    # Ensure aggregate proxies exist at least for the clamped camera window.
    # (This avoids "only a few patches near player" when panning far away.)
    try:
        game._ensure_world_aggregate_entities(
            zone_w=zone_w,
            zone_h=zone_h,
            zx0=zx0c,
            zx1=zx1c,
            zy0=zy0c,
            zy1=zy1c,
            zz=zz,
            kinds=None,
        )
    except Exception:
        pass

    try:
        refs = world_index.query_abs_rect((wx0, wy0, wx1, wy1), z=zz, zone_span_cap=max_zone_span)
    except Exception:
        return

    partial_knowledge = bool(was_clamped)

    attn_store: AttentionCellStore = getattr(game, "attn_store", None)
    if attn_store is None:
        return

    if not hasattr(game, "_attn_active_resolved_children"):
        game._attn_active_resolved_children = {}  # parent_eid -> set[child_eid]


    # -----------------------------
    # A) BERRY PATCH -> BERRIES
    # -----------------------------
    in_scope_aggs: set[str] = set()

    for r in refs:
        ent = getattr(r, "ent", None)
        if ent is None:
            continue

        tags = getattr(ent, "tags", {}) or {}
        kind = str(tags.get("aggregate_kind", "") or getattr(ent, "kind", "") or "")
        if not kind:
            continue

        # Generic aggregate refinement (cluster mode)
        if tags.get("aggregate") is not True:
            continue
        if str(tags.get("detail_mode", "") or "") != "cluster":
            continue

        # ------------------------------------------------------------
        # YOGA: Detail instantiation threshold derives from the child’s size,
        # using the same LoD regime as renderables_in_abs_rect().
        #
        # Child is eligible to render when:
        #   delta = cam_lod - log2(child_abs_size) <= dmax
        # so instantiate when:
        #   cam_lod <= log2(child_abs_size) + dmax
        #
        # We keep an escape hatch: if tags set detail_lod_threshold_mode="fixed",
        # we honor the explicit detail_lod_threshold for bespoke tuning.
        # ------------------------------------------------------------
        mode = str(tags.get("detail_lod_threshold_mode", "") or "")
        if mode == "fixed":
            thresh = float(tags.get("detail_lod_threshold", -1.25))
        else:
            # Use the same dmax the renderer used most recently (defaults to 0.75).
            # Use the same dmax the renderer used most recently (defaults to 0.75),
            # and include the renderer fade margin so instantiation doesn't lag behind visibility.
            dmax = float(getattr(game, "_attn_render_dmax", 0.75) or 0.75)
            fade_w = float(getattr(cfg, "entity_lod_fade_w", 0.6) or 0.6)  # fallback; matches renderables default

            # Derive child render size the same way the renderer would:
            # prefer tags.abs_size if present, otherwise base_size.
            child_size = 1.0
            try:
                child_id = str(tags.get("detail_child", "") or "")
                if child_id:
                    child_proto = prototypes.resolve_proto(child_id)

                    # proto may be dict-like or object-like
                    ptags = {}
                    try:
                        ptags = (getattr(child_proto, "tags", None) or {})  # object style
                    except Exception:
                        ptags = {}
                    if not ptags:
                        try:
                            ptags = (child_proto.get("tags", None) or {})  # dict style
                        except Exception:
                            ptags = {}

                    abs_size_tag = None
                    try:
                        abs_size_tag = ptags.get("abs_size", None)
                    except Exception:
                        abs_size_tag = None

                    if abs_size_tag is not None:
                        child_size = float(abs_size_tag)
                    else:
                        # base_size fallback
                        try:
                            child_size = float(getattr(child_proto, "base_size", None) or 1.0)
                        except Exception:
                            try:
                                child_size = float(child_proto.get("base_size", 1.0) or 1.0)
                            except Exception:
                                child_size = 1.0
            except Exception:
                child_size = 1.0

            child_size = max(1e-9, float(child_size))
            child_lod = math.log2(child_size)

            # Instantiate whenever it could plausibly render in the same band as normal entities.
            thresh = child_lod + (dmax + fade_w)


        if float(cam_lod) > float(thresh):
            continue

        zc = tuple(getattr(r, "zone_coord", (0, 0, zz)))
        if len(zc) != 3:
            continue

        agg_id = str(getattr(ent, "id", "") or "")
        if not agg_id:
            continue

        # Allow combat.kill_actor to find the macro aggregate entity by id
        try:
            amap = getattr(game, "_attn_agg_id_to_ent", None)
            if not isinstance(amap, dict):
                amap = {}
                game._attn_agg_id_to_ent = amap
            amap[str(agg_id)] = ent
        except Exception:
            pass


        in_scope_aggs.add(agg_id)

        slot_to_eid = game._attn_active_agg_children.get(agg_id)
        if not isinstance(slot_to_eid, dict):
            slot_to_eid = {}
            game._attn_active_agg_children[agg_id] = slot_to_eid

        child_type = str(tags.get("detail_child_type", "entity") or "entity").lower().strip()
        is_harvestable = (str(kind) == "berry_patch") or (child_type == "actor")

        # Harvest persistence stored on the aggregate itself (truthy macro object)
        harvested = getattr(ent, "_agg_harvested_slots", None)
        if not isinstance(harvested, set):
            harvested = set()
            if is_harvestable:
                try:
                    setattr(ent, "_agg_harvested_slots", harvested)
                except Exception:
                    pass

        # If something removed a harvestable child from a *loaded zone* (pickup), record its slot harvested.
        # (This keeps existing gameplay interactions working while zones still exist locally.)
        try:
            level = game.get_zone_for_render(zc)
        except Exception:
            level = None




        # Harvest persistence (berries only): only treat "missing from level.entities" as pickup
        # if we KNOW this eid was previously mirrored into this loaded level.
        if is_harvestable and level is not None and child_type != "actor":
            try:
                mirrored = getattr(level, "_attn_mirrored_ids", None)
                if not isinstance(mirrored, set):
                    mirrored = set()
                    setattr(level, "_attn_mirrored_ids", mirrored)

                for s, eid in list(slot_to_eid.items()):
                    if (eid in mirrored) and (eid not in level.entities) and (eid in attn_store.entities):
                        harvested.add(int(s))
                        _mark_removed(game, entity_id=str(eid), reason="pickup")
                        attn_store.despawn(eid)
                        del slot_to_eid[s]
            except Exception:
                pass

        # Deterministic child layout (local coords)
        try:
            child_id, pts = aggregate_system.compute_cluster_children_layout(
                game,
                aggregate_ent=ent,
                zone_coord=zc,
                local_pos=tuple(getattr(r, "local_pos", (0, 0))),
                zone_w=zone_w,
                zone_h=zone_h,
            )
        except Exception:
            continue
        if not child_id or not pts:
            continue

        # Resolve child prototype once
        try:
            child_proto = prototypes.resolve_proto(str(child_id))
        except Exception:
            continue

        zx, zy, _z = map(int, zc)

        for slot, (lx, ly) in enumerate(pts):
            lineage_id = aggregate_system.aggregate_slot_lineage_id(agg_id, child_id, int(slot))
            eid = aggregate_system.entity_id_from_lineage(lineage_id)
            if _is_suppressed(game, entity_id=eid, lineage_id=lineage_id):
                # Keep slot locally marked harvested to avoid churn this frame.
                harvested.add(int(slot))
                existing_eid = slot_to_eid.get(int(slot))
                if existing_eid:
                    try:
                        attn_store.despawn(existing_eid)
                    except Exception:
                        pass
                    try:
                        if level is not None:
                            level.entities.pop(existing_eid, None)
                            level.actors.pop(existing_eid, None)
                            level.spatial_dirty = True
                    except Exception:
                        pass
                    try:
                        del slot_to_eid[int(slot)]
                    except Exception:
                        pass
                continue
            if slot in harvested:
                continue

            ax = int(zx * int(zone_w) + int(lx))
            ay = int(zy * int(zone_h) + int(ly))

            # Keep within warm rect (prevents weird edge-instantiation)
            if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                continue

            # If this is an actor slot and the zone is loaded, the loaded level is authoritative.
            # This prevents re-staging "graduated" actors and avoids invisible/ghost desync.
            if child_type == "actor" and level is not None:
                try:
                    if eid in level.actors:
                        if eid in attn_store.entities:
                            attn_store.despawn(eid)
                        slot_to_eid[slot] = eid
                        continue
                except Exception:
                    pass

            if eid in attn_store.entities:
                slot_to_eid[slot] = eid

                # If this slot is an ACTOR and we have a loaded level, promote it into simulation
                # instead of leaving it as a non-simulated render-only ghost.
                if child_type == "actor" and level is not None:
                    try:
                        # Ensure it exists in the level entity registry (some systems expect this)
                        if eid not in level.entities:
                            level.entities[eid] = attn_store.entities[eid]
                            level.spatial_dirty = True

                        # Register/promote into the actor system if not already there
                        if eid not in level.actors:
                            try:
                                # Prefer the canonical path if present
                                spawning_system.register_actor(game, level, attn_store.entities[eid], schedule_ai=True)
                            except Exception:
                                print("What an exception")
                                # Fallback: direct insertion if register_actor signature differs
                                level.actors[eid] = attn_store.entities[eid]
                                level.spatial_dirty = True

                        # Once promoted, remove from attention-store to avoid duplicate ownership
                        try:
                            attn_store.despawn(eid)
                        except Exception:
                            pass
                    except Exception:
                        pass

                    continue

                # Non-actor: mirror into loaded zone if present (enables pickup/look)
                if level is not None and eid not in level.entities:
                    try:
                        level.entities[eid] = attn_store.entities[eid]
                        level.spatial_dirty = True
                    except Exception:
                        pass

                continue


            child_type = str(tags.get("detail_child_type", "entity") or "entity").lower().strip()

            if child_type == "actor":
                # Deterministic actor child (e.g., ecology_controller -> wolves).
                # CRITICAL: actor id must be the deterministic slot eid, otherwise we spawn forever.

                # If already staged, we're done.
                if eid in attn_store.entities:
                    slot_to_eid[slot] = eid
                    # If this zone is loaded, ensure it is registered once for simulation,
                    # then remove from attn_store so zone rendering/simulation stays coherent for moving actors.
                    if level is not None:
                        try:
                            if eid not in level.actors:
                                spawning_system.register_actor(game, level, attn_store.entities[eid], schedule_ai=True)
                                level.spatial_dirty = True
                            try:
                                attn_store.despawn(eid)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    continue

                # Build Actor from spec with deterministic id.
                try:
                    actor_spec = prototypes.resolve_proto(str(child_id))
                except Exception:
                    continue
                if not isinstance(actor_spec, dict) or not actor_spec:
                    continue

                try:
                    actor_obj = spawn_factory.build_actor_from_spec(
                        spec=actor_spec,
                        aid=eid,  # deterministic!
                        pos=(int(lx), int(ly)),
                        abs_pos=(int(ax), int(ay)),
                        overrides={
                            "tags": {
                                "from_aggregate": agg_id,
                                "aggregate_slot": int(slot),
                                "aggregate_kind": str(kind),
                                "lineage_id": lineage_id,
                            }
                        },
                    )
                except Exception:
                    continue

                # Stage into attention store (primary truth)
                try:
                    attn_store.stage(
                        actor_obj,
                        abs_x=ax,
                        abs_y=ay,
                        zz=zz,
                    )
                    _mark_entity_live(game, str(eid))
                except Exception:
                    continue

                # If this zone is loaded, register exactly once (schedules AI).
                if level is not None:
                    try:
                        if eid not in level.actors:
                            spawning_system.register_actor(game, level, actor_obj, schedule_ai=True)
                            # IMPORTANT: once an actor is simulating in a loaded zone, don't keep it in attn_store,
                            # otherwise attn_store bin staleness makes it "invisible" while still attacking.
                            try:
                                attn_store.despawn(eid)
                            except Exception:
                                pass

                            level.spatial_dirty = True
                    except Exception:
                        pass

                slot_to_eid[slot] = eid
                continue


            else:
                # Generic entity child (berry patch style)
                try:
                    bent = spawn_factory.build_entity_from_spec(
                        spec=child_proto,
                        eid=eid,
                        pos=(int(lx), int(ly)),
                        abs_pos=(int(ax), int(ay)),
                        overrides={
                            "tags": {
                                "from_aggregate": agg_id,
                                "aggregate_slot": int(slot),
                                "aggregate_kind": str(kind),
                                "lineage_id": lineage_id,
                            }
                        },
                    )
                except Exception:
                    continue

                # Stage into attention store (primary)
                try:
                    attn_store.stage(bent, abs_x=ax, abs_y=ay, zz=zz)
                    _mark_entity_live(game, str(eid))
                except Exception:
                    continue

                # Mirror into loaded zone if present
                if level is not None:
                    try:
                        level.entities[eid] = bent
                        level.spatial_dirty = True
                        try:
                            mirrored = getattr(level, "_attn_mirrored_ids", None)
                            if not isinstance(mirrored, set):
                                mirrored = set()
                                setattr(level, "_attn_mirrored_ids", mirrored)
                            mirrored.add(eid)
                        except Exception:
                            pass
                    except Exception:
                        pass

            slot_to_eid[slot] = eid


    # Evict berries for aggregates no longer in scope.
    # IMPORTANT: only evict when our macro query is complete. If the query was clamped/capped,
    # eviction causes flicker (entities appear for a tick then vanish).
    if not partial_knowledge:
        try:
            for agg_id, slot_map in list(game._attn_active_agg_children.items()):
                if agg_id in in_scope_aggs:
                    continue
                if isinstance(slot_map, dict):
                    for _slot, eid in list(slot_map.items()):
                        try:
                            attn_store.despawn(eid)
                        except Exception:
                            pass
                del game._attn_active_agg_children[agg_id]
        except Exception:
            pass

    # -----------------------------
    # C) GENERIC RESOLVE RECIPES (sites + buildings + any future resolve-tagged parents)
    #
    # attention.py decides *when/where* to resolve; aggregate_resolution decides *what/how*.
    # -----------------------------
    try:
        if not hasattr(game, "_attn_active_resolved_children"):
            game._attn_active_resolved_children = {}  # parent_eid -> set[child_eid]

        in_scope_parents: set[str] = set()

        # Same size-derived threshold regime as player-ish actors
        dmax = float(getattr(game, "_attn_render_dmax", 0.75) or 0.75)
        fade_w = float(getattr(cfg, "entity_lod_fade_w", 0.6) or 0.6)
        actor_size = 1.0
        resolve_thresh = math.log2(max(1e-9, actor_size)) + (dmax + fade_w)

        if float(cam_lod) <= float(resolve_thresh):
            # Parents come from TWO sources:
            #  1) macro WIE refs (sites, aggregates, etc.)
            #  2) already-staged children in attn_store (e.g., buildings), so they can resolve further
            parents: list[tuple[object, tuple[int, int, int], tuple[int, int]]] = []

            def _entity_overlaps_warm_window(ent: object) -> bool:
                rect = getattr(ent, "footprint_abs", None)
                if isinstance(rect, (tuple, list)) and len(rect) >= 4:
                    rx0, ry0, rx1, ry1 = rect[:4]
                    return not (
                        float(rx1) <= float(wx0)
                        or float(rx0) >= float(wx1)
                        or float(ry1) <= float(wy0)
                        or float(ry0) >= float(wy1)
                    )
                ap = getattr(ent, "abs_pos", None)
                if isinstance(ap, (tuple, list)) and len(ap) >= 2:
                    ax = float(ap[0])
                    ay = float(ap[1])
                    return float(wx0) <= ax < float(wx1) and float(wy0) <= ay < float(wy1)
                return False

            # 1) WIE refs already computed earlier as `refs`
            for r in refs:
                ent = getattr(r, "ent", None)
                if ent is None:
                    continue
                tags = getattr(ent, "tags", {}) or {}
                if not isinstance(tags, dict) or "resolve" not in tags:
                    continue
                zc = tuple(getattr(r, "zone_coord", (0, 0, zz)))
                lp = tuple(getattr(r, "local_pos", (0, 0)))
                parents.append((ent, zc, lp))

            # 2) staged entities inside warm rect
            try:
                staged = attn_store.query_abs_rect((wx0, wy0, wx1, wy1), zz=zz)
                for ent, ax, ay in staged:
                    tags = getattr(ent, "tags", {}) or {}
                    if not isinstance(tags, dict) or "resolve" not in tags:
                        continue
                    # derive zc/lp from abs
                    zxx = int(ax) // int(zone_w)
                    zyy = int(ay) // int(zone_h)
                    lp = (int(ax) - zxx * int(zone_w), int(ay) - zyy * int(zone_h))
                    parents.append((ent, (zxx, zyy, zz), lp))
            except Exception:
                pass

            # Frontier-expanding loop: if a newly spawned child itself has "resolve",
            # process it as a parent within the SAME attention sync (site -> building -> walls).
            i = 0
            while i < len(parents):
                parent_ent, zc, lp = parents[i]
                i += 1

                parent_id = str(getattr(parent_ent, "entity_id", "") or getattr(parent_ent, "id", "") or "")
                if not parent_id:
                    continue
                in_scope_parents.add(parent_id)
                parent_tags = getattr(parent_ent, "tags", {}) or {}
                current_depth = int(parent_tags.get("_resolve_depth", 0) or 0)
                max_depth_cap = _resolve_max_depth_for_lod(
                    game,
                    cam_lod=float(cam_lod),
                    parent_tags=parent_tags if isinstance(parent_tags, dict) else {},
                )
                parent_ax, parent_ay = _entity_abs_anchor(
                    parent_ent,
                    zone_coord=tuple(map(int, zc)),
                    local_pos=tuple(map(int, lp)),
                    zone_w=int(zone_w),
                    zone_h=int(zone_h),
                )
                screen_span = max(1.0, float(ax1 - ax0), float(ay1 - ay0))
                dist_tiles = math.hypot(float(parent_ax) - float(ccx), float(parent_ay) - float(ccy))
                dist_screens = float(dist_tiles) / float(screen_span)
                dist_depth_cap = _resolve_max_depth_for_distance(
                    game,
                    distance_screens=float(dist_screens),
                    parent_tags=parent_tags if isinstance(parent_tags, dict) else {},
                )
                max_depth_cap = min(int(max_depth_cap), int(dist_depth_cap))

                if int(max_depth_cap) <= int(current_depth):
                    entity_lifecycle_system.collapse_entity(
                        game,
                        parent_id,
                        reason="attention_depth",
                    )
                    continue

                child_ids = entity_lifecycle_system.expand_entity(
                    game,
                    parent_id,
                    reason="attention",
                )

                for child_id in child_ids:
                    child_obj = entity_lifecycle_system.find_runtime_entity(game, child_id)
                    if child_obj is None:
                        continue
                    try:
                        child_tags = getattr(child_obj, "tags", {}) or {}
                    except Exception:
                        child_tags = {}
                    if not isinstance(child_tags, dict) or "resolve" not in child_tags:
                        continue
                    if not _entity_overlaps_warm_window(child_obj):
                        continue
                    try:
                        ap = getattr(child_obj, "abs_pos", None)
                        if not isinstance(ap, (tuple, list)) or len(ap) < 2:
                            continue
                        cax = int(ap[0])
                        cay = int(ap[1])
                        zpp = (cax // int(zone_w), cay // int(zone_h), int(zz))
                        lpp = (cax - zpp[0] * int(zone_w), cay - zpp[1] * int(zone_h))
                        parents.append((child_obj, zpp, lpp))
                    except Exception:
                        continue

        # Evict parents that left scope entirely or no longer qualify for detail.
        for parent_id in list(getattr(game, "_attn_active_resolved_children", {}).keys()):
            if parent_id in in_scope_parents:
                continue
            entity_lifecycle_system.collapse_entity(
                game,
                str(parent_id),
                reason="attention_out_of_scope",
            )

    except Exception:
        # Never let resolve-recipe logic break the rest of attention.
        pass

    try:
        game._attn_last_sync_abs_rect = (float(ax0), float(ay0), float(ax1), float(ay1))
        game._attn_last_sync_cam_lod = float(cam_lod)
    except Exception:
        pass







def renderables_in_abs_rect(
    game,
    abs_rect: Tuple[float, float, float, float],
    *,
    include_actors: bool = True,
    include_entities: bool = True,
    cam_lod: float,
    dmin: float = -5.0,
    dmax: float = 0.75,
    fade_w: float = 0.6,
    max_count: int = 2000,
    proxy_cls,
) -> List[object]:
    """Return renderable objects intersecting an absolute-world tile rect.

    abs_rect = (x0, y0, x1, y1) in absolute world-tile coordinates.
    Rect is half-open: [x0,x1) Ãƒâ€” [y0,y1).

    Camera-centric query for god-vision / scry / macro-view.

    Critical invariants:
    - Rendering must not instantiate gameplay state.
    - get_zone_for_render() may return None for unloaded zones (and that's OK).
    - World-index entities (sites, later forests/cities/etc.) must still be queryable
      even when the camera spans many zones.
    """
    try:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
    except Exception:
        return []

    if ax1 < ax0:
        ax0, ax1 = ax1, ax0
    if ay1 < ay0:
        ay0, ay1 = ay1, ay0
    if ax1 == ax0 or ay1 == ay0:
        return []

    cfg = getattr(game, "cfg", None)

    # Zone dimensions MUST match actual zone tile dims.
    zone_w = int(getattr(cfg, "world_width", 0) or 0)
    zone_h = int(getattr(cfg, "world_height", 0) or 0)
    if zone_w <= 0 or zone_h <= 0:
        try:
            lvl0 = game._level()
            zone_w = int(getattr(lvl0.world, "width", 60) or 60)
            zone_h = int(getattr(lvl0.world, "height", 40) or 40)
        except Exception:
            zone_w = 60
            zone_h = 40

    # Which depth are we on?
    _czx, _czy, zz = getattr(game, "zone_coord", (0, 0, 0))
    zz = int(zz)

    # ------------------------------------------------------------
    # YOGA: attention-driven resolution must run when the camera changes,
    # regardless of *how* the change happened (pan vs zoom vs look).
    #
    # The renderer calls renderables_in_abs_rect every frame; if the camera
    # rect / lod band changed since last frame, sync our attention store now.
    # ------------------------------------------------------------
    try:
        sig = (
            round(float(ax0), 3), round(float(ay0), 3), round(float(ax1), 3), round(float(ay1), 3),
            round(float(cam_lod), 4), int(zz),
        )
        last = getattr(game, "_attn_last_sig", None)
        if sig != last:
            game._attn_last_sig = sig

            # Keep attention instantiation thresholds consistent with the renderer LoD band.
            try:
                game._attn_render_dmax = float(dmax)
            except Exception:
                pass

            try:
                game.sync_attention_instantiation((ax0, ay0, ax1, ay1), cam_lod=float(cam_lod))
            except Exception:
                # Never fail rendering because attention sync hiccuped.
                pass

    except Exception:
        pass


    # ------------------------------------------------------------
    # WARM RECT (IMPORTANT): drive *candidate gathering* from warmth,
    # not from the razor-thin camera rect. This prevents "player disappears
    # when panning a few pixels" due to float/rounding/tight rect edges.
    # ------------------------------------------------------------
    # Default padding in ABS tiles around camera rect (offscreen pursuit / warmth)
    pad_tiles = float(getattr(cfg, "entity_render_pad_tiles", 0.0) or 0.0)
    if pad_tiles <= 0.0:
        try:
            pad_tiles = 0.5 * max((ax1 - ax0), (ay1 - ay0))
        except Exception:
            pad_tiles = 0.0

    wx0 = ax0 - pad_tiles
    wy0 = ay0 - pad_tiles
    wx1 = ax1 + pad_tiles
    wy1 = ay1 + pad_tiles

    # Union with inhabited actor position (keeps the fovea "warm" even if camera pans slightly)
    pax = pay = None
    try:
        pax, pay = game._get_player_abs()
        wx0 = min(wx0, float(pax) - pad_tiles)
        wy0 = min(wy0, float(pay) - pad_tiles)
        wx1 = max(wx1, float(pax) + pad_tiles)
        wy1 = max(wy1, float(pay) + pad_tiles)
    except Exception:
        pax = pay = None

    # ------------------------------------------------------------
    # Zone span covered by *warm rect* (NOT raw camera rect)
    # ------------------------------------------------------------
    zx0 = int(math.floor(wx0 / float(zone_w)))
    zy0 = int(math.floor(wy0 / float(zone_h)))
    zx1 = int(math.floor((wx1 - 1e-9) / float(zone_w)))
    zy1 = int(math.floor((wy1 - 1e-9) / float(zone_h)))

    max_zone_span = int(getattr(cfg, "render_max_zone_span", 9) or 9)
    max_zone_span = max(1, max_zone_span)
    span_x = (zx1 - zx0 + 1)
    span_y = (zy1 - zy0 + 1)
    too_many_zones = (span_x > max_zone_span) or (span_y > max_zone_span)

    out: List[object] = []
    candidates: List[Tuple[object, float, float, Tuple[int, int, int], Tuple[int, int], float]] = []

    # Reuse a single handle to attention store throughout this function.
    attn_store = getattr(game, "attn_store", None)
    attn_ids = set(getattr(attn_store, "entities", {}) or {}) if attn_store is not None else set()


    # Camera center for scoring (raw camera center, not warm center)
    ccx = 0.5 * (ax0 + ax1)
    ccy = 0.5 * (ay0 + ay1)

    def _score(abs_size: float, abs_x: float, abs_y: float) -> float:
        dx = abs_x - ccx
        dy = abs_y - ccy
        return abs_size * 1000.0 - (dx * dx + dy * dy)

    # ------------------------------------------------------------
    # 1) WORLD INDEX ENTITIES (POIs and other world-markers)
    # NOTE: Sites are now placed directly into WorldEntityIndex upstream (site_placement.py).
    # ------------------------------------------------------------


    # Always ensure aggregate proxies for a **clamped camera window**.
    # This keeps god-vision panning responsive while still allowing distant areas
    # to resolve aggregates when the camera is there.
    try:
        zx0c, zx1c, zy0c, zy1c, _clamped = game._clamp_zone_window(
            zx0, zx1, zy0, zy1,
            zone_span_cap=max_zone_span,
            ccx=ccx, ccy=ccy,
            zone_w=zone_w, zone_h=zone_h,
        )
        game._ensure_world_aggregate_entities(
            zone_w=zone_w,
            zone_h=zone_h,
            zx0=zx0c,
            zx1=zx1c,
            zy0=zy0c,
            zy1=zy1c,
            zz=zz,
            kinds=None,
        )
    except Exception:
        pass


    # Query world index using WARM rect so panning doesn't "drop" things on the edge.
    try:
        if getattr(game, "world_entity_index", None) is not None:
            for ref in game.world_entity_index.query_abs_rect((wx0, wy0, wx1, wy1), z=zz, zone_span_cap=None):
                obj = ref.ent
                zx, zy, _z = ref.zone_coord
                ox, oy = ref.local_pos



                abs_x = float(zx * zone_w + ox)
                abs_y = float(zy * zone_h + oy)

                abs_size = game._size_for_render(obj)
                ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                delta = float(cam_lod) - float(ent_lod)

                if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                    continue

                # Intersection test against WARM rect (gather candidates), not camera rect.
                half = 0.5 * float(abs_size)
                ex0 = abs_x - half
                ey0 = abs_y - half
                ex1 = abs_x + half
                ey1 = abs_y + half
                if ex1 <= wx0 or ex0 >= wx1 or ey1 <= wy0 or ey0 >= wy1:
                    continue

                sc = _score(abs_size, abs_x, abs_y)

                # Render-only detail proxies for aggregates when zoomed in.
                # NOTE (Yoga): detail proxies are disabled.
                # If berries/soldiers/etc. are visible at a given band, they must be real entities
                # staged into LevelState.entities by the attention lifecycle (not invented here).
                pass

                candidates.append((obj, abs_x, abs_y, (int(zx), int(zy), int(zz)), (int(ox), int(oy)), sc))
    except Exception:
        pass

    
    # ------------------------------------------------------------
    # 1.5) ATTENTION-STAGED ENTITIES (Route 2)
    # ------------------------------------------------------------
    try:
        attn_store = getattr(game, "attn_store", None)
        if attn_store is not None and include_entities:
            for obj, abs_x, abs_y in attn_store.query_abs_rect((wx0, wy0, wx1, wy1), zz=zz):
                abs_size = game._size_for_render(obj)
                ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                delta = float(cam_lod) - float(ent_lod)

                if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                    continue

                sc = _score(abs_size, abs_x, abs_y)

                # Derive zone/local purely for renderer convenience.
                zx = int(math.floor(abs_x / float(zone_w)))
                zy = int(math.floor(abs_y / float(zone_h)))
                ox = int(abs_x - zx * zone_w)
                oy = int(abs_y - zy * zone_h)

                candidates.append((obj, float(abs_x), float(abs_y), (int(zx), int(zy), int(zz)), (int(ox), int(oy)), sc))
    except Exception:
        pass


    # ------------------------------------------------------------
    # 2) LOADED ZONES ONLY (never instantiate zones here)
    # ------------------------------------------------------------
    coords_to_check: List[Tuple[int, int, int]] = []

    if too_many_zones:
        # Camera spans many zones; do NOT iterate them all.
        # But do render entities from zones already in memory that intersect WARM rect.
        try:
            for coord in getattr(game, "levels", {}).keys():
                try:
                    zx, zy, zdepth = coord
                except Exception:
                    continue
                if int(zdepth) != int(zz):
                    continue

                zax0 = float(zx * zone_w)
                zay0 = float(zy * zone_h)
                zax1 = zax0 + float(zone_w)
                zay1 = zay0 + float(zone_h)
                if zax1 <= wx0 or zax0 >= wx1 or zay1 <= wy0 or zay0 >= wy1:
                    continue

                coords_to_check.append((int(zx), int(zy), int(zz)))
        except Exception:
            coords_to_check = []
    else:
        for zx in range(zx0, zx1 + 1):
            for zy in range(zy0, zy1 + 1):
                coords_to_check.append((int(zx), int(zy), int(zz)))

    for coord in coords_to_check:
        zx, zy, _ = coord
        try:
            level = game.get_zone_for_render(coord)
        except Exception:
            continue
        if level is None:
            continue

        if getattr(level, "spatial_dirty", True) or not getattr(level, "spatial_bins", None):
            game._rebuild_spatial_bins(level)

        # Local rect within this zone for WARM rect.
        lx0 = wx0 - float(zx * zone_w)
        ly0 = wy0 - float(zy * zone_h)
        lx1 = wx1 - float(zx * zone_w)
        ly1 = wy1 - float(zy * zone_h)

        if lx1 <= 0 or ly1 <= 0 or lx0 >= zone_w or ly0 >= zone_h:
            continue
        lx0 = max(0.0, min(float(zone_w), lx0))
        ly0 = max(0.0, min(float(zone_h), ly0))
        lx1 = max(0.0, min(float(zone_w), lx1))
        ly1 = max(0.0, min(float(zone_h), ly1))
        if lx1 <= lx0 or ly1 <= ly0:
            continue

        bs = int(getattr(level, "spatial_bin_size", 16) or 16)
        bs = max(1, bs)
        bx0 = int(math.floor(lx0 / bs))
        by0 = int(math.floor(ly0 / bs))
        bx1 = int(math.floor((lx1 - 1e-6) / bs))
        by1 = int(math.floor((ly1 - 1e-6) / bs))

        bins = level.spatial_bins
        seen_ids: set[str] = set()

        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                ids = bins.get((bx, by))
                if not ids:
                    continue
                for obj_id in ids:
                    if obj_id in seen_ids:
                        continue
                    seen_ids.add(obj_id)

                    # YOGA: If an object is managed by attention (staged into attn_store),
                    # do NOT also render it from the loaded-zone dictionaries. Zone load
                    # must not change LoD bands or visibility.
                    if obj_id in attn_ids:
                        continue

                    obj = None
                    if include_actors:
                        obj = level.actors.get(obj_id)
                    if obj is None and include_entities:
                        obj = level.entities.get(obj_id)
                    if obj is None:
                        continue

                    # Suppress legacy stamped *site/POI* children so attention remains the sole truth source for those.
                    # DO NOT suppress aggregate children here: they may have "graduated" into the loaded zone for simulation.
                    try:
                        _tags = getattr(obj, "tags", {}) or {}
                        if _tags.get("site_npc") or _tags.get("poi_npc"):
                            continue
                    except Exception:
                        pass


                    try:
                        ox, oy = obj.pos

                    except Exception:
                        continue
                    if not (lx0 <= ox < lx1 and ly0 <= oy < ly1):
                        continue

                    abs_x = float(zx * zone_w + ox)
                    abs_y = float(zy * zone_h + oy)

                    abs_size = game._size_for_render(obj)
                    ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                    delta = float(cam_lod) - float(ent_lod)
                    if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                        continue

                    sc = _score(abs_size, abs_x, abs_y)
                    candidates.append((obj, abs_x, abs_y, coord, (int(ox), int(oy)), sc))

    if not candidates:
        return []

    # ------------------------------------------------------------
    # Banded multi-scale attention selection
    # ------------------------------------------------------------
    band_width = int(getattr(cfg, "entity_band_width", 4) or 4)
    band_width = max(1, band_width)

    bucket_slack = int(getattr(cfg, "entity_bucket_slack", 3) or 3)

    k_cell = int(getattr(cfg, "entity_render_k_cell", 12) or 12)
    k_cell = max(1, k_cell)

    k_layer = int(getattr(cfg, "entity_render_k_layer", max_count) or max_count)
    if k_layer <= 0:
        k_layer = max_count

    # Band overlap allows adjacent bands to still render (prevents "sites vanish entirely")
    band_overlap = int(getattr(cfg, "entity_band_overlap", 1) or 1)
    band_overlap = max(0, band_overlap)

    cam_lod_f = float(cam_lod)

    # Bias lets you zoom in closer before band transitions.
    zoom_in_bias = float(getattr(cfg, "entity_zoom_in_bias_lod", 3.0) or 3.0)
    cam_lod_f += zoom_in_bias

    # Optional additional band bias (simple constant nudge).
    band_bias = float(getattr(cfg, "entity_band_bias", 0.0) or 0.0)
    cam_lod_f += band_bias

    if not hasattr(game, "_entity_active_band"):
        game._entity_active_band = int(math.floor(cam_lod_f / float(band_width)))

    b = int(getattr(game, "_entity_active_band", 0) or 0)

    # Less sticky hysteresis by default.
    h = float(getattr(cfg, "entity_band_hysteresis", 0.05) or 0.05)

    if cam_lod_f > ((b + 1) * band_width + h):
        b = int(math.floor(cam_lod_f / float(band_width)))
    elif cam_lod_f < (b * band_width - h):
        b = int(math.floor(cam_lod_f / float(band_width)))

    game._entity_active_band = b

    lod_min = b * band_width
    lod_max = lod_min + (band_width - 1)

    cell_lod_min = lod_min + bucket_slack
    cell_lod_max = lod_max + bucket_slack

    def _ent_lod(sz: float) -> float:
        return math.log2(sz) if sz and sz > 0.0 else -30.0

    # Bucket candidates by native cell.
    cell_map = {}
    for obj, ex, ey, coord, local_pos, _sc in candidates:
        try:
            abs_size = float(game._size_for_render(obj))
        except Exception:
            abs_size = 1.0

        e_lod = _ent_lod(abs_size)
        e_band = int(math.floor(e_lod / float(band_width)))

        # Allow overlap bands around active band.
        if abs(e_band - b) > band_overlap:
            continue

        try:
            native_cell_lod = int(math.floor(e_lod)) + int(bucket_slack)
        except Exception:
            native_cell_lod = cell_lod_min

        if native_cell_lod < cell_lod_min:
            native_cell_lod = cell_lod_min
        elif native_cell_lod > cell_lod_max:
            native_cell_lod = cell_lod_max

        cell_size = float(2 ** int(native_cell_lod))
        cx = int(math.floor(float(ex) / cell_size))
        cy = int(math.floor(float(ey) / cell_size))

        key = (int(native_cell_lod), cx, cy)
        cell_map.setdefault(key, []).append((obj, float(ex), float(ey), coord, local_pos, abs_size))

    if not cell_map:
        return []

    selected = []

    # Iterate cell LODs for this band
    for cell_lod in range(int(cell_lod_min), int(cell_lod_max) + 1):
        cell_size = float(2 ** int(cell_lod))

        # Hot cells from WARM rect
        cx0 = int(math.floor(wx0 / cell_size))
        cy0 = int(math.floor(wy0 / cell_size))
        cx1 = int(math.floor((wx1 - 1e-9) / cell_size))
        cy1 = int(math.floor((wy1 - 1e-9) / cell_size))

        hot_keys = []
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                key = (int(cell_lod), cx, cy)
                if key not in cell_map:
                    continue
                ccx_cell = (float(cx) + 0.5) * cell_size
                ccy_cell = (float(cy) + 0.5) * cell_size
                dx = ccx_cell - ccx
                dy = ccy_cell - ccy
                hot_keys.append((dx * dx + dy * dy, key))

        hot_keys.sort(key=lambda t: t[0])

        for _d2, key in hot_keys:
            ents = cell_map.get(key, [])
            if not ents:
                continue

            def _rank(rec):
                _obj, _x, _y, _coord, _lp, _sz = rec
                dx = _x - ccx
                dy = _y - ccy
                return (dx * dx + dy * dy, id(_obj))

            ents.sort(key=_rank)
            for rec in ents[:k_cell]:
                selected.append(rec)

            if k_layer > 0 and len(selected) >= k_layer:
                break

        if k_layer > 0 and len(selected) >= k_layer:
            break

    # ------------------------------------------------------------
    # YOGA INVARIANT: EVERYTHING ONSCREEN MUST RENDER.
    #
    # k_cell / k_layer / max_count are *attention budgets* and may
    # cull OFFSCREEN (warm padding) candidates, but they must never
    # hide entities that are actually inside the raw camera rect.
    #
    # Otherwise dense piles (e.g. item_depot) get arbitrarily clipped
    # by k_cell because many items share the same native bucket cell.
    # ------------------------------------------------------------
    onscreen: list[tuple[object, float, float, tuple[int, int, int], tuple[int, int], float]] = []
    onscreen_ids: set[object] = set()

    def _obj_key(o: object) -> object:
        # Prefer stable entity ids if present; fallback to object identity.
        oid = getattr(o, "id", None)
        return oid if oid is not None else id(o)

    # NOTE: candidates are already LoD-filtered (delta vs dmin/dmax/fade_w),
    # so we only enforce geometric inclusion here.
    for obj, ex, ey, coord, local_pos, _sc in candidates:
        if ax0 <= float(ex) < ax1 and ay0 <= float(ey) < ay1:
            try:
                abs_size = float(game._size_for_render(obj))
            except Exception:
                abs_size = 1.0
            rec = (obj, float(ex), float(ey), coord, local_pos, abs_size)
            key = _obj_key(obj)
            if key in onscreen_ids:
                continue
            onscreen_ids.add(key)
            onscreen.append(rec)

    # Merge: onscreen first, then the budget-selected offscreen/warm set.
    merged: list[tuple[object, float, float, tuple[int, int, int], tuple[int, int], float]] = []
    merged.extend(onscreen)

    for rec in selected:
        obj = rec[0]
        if _obj_key(obj) in onscreen_ids:
            continue
        merged.append(rec)

    # Apply max_count ONLY to the warm/offscreen remainder.
    # Never truncate onscreen.
    if max_count > 0 and len(merged) > max_count:
        keep_n = max_count - len(onscreen)
        if keep_n <= 0:
            merged = onscreen
        else:
            merged = onscreen + merged[len(onscreen) : len(onscreen) + keep_n]

    selected = merged


    # Hard guarantee: include player if we can find it.
    # (Not a "render special-case"; it's an observer invariant.)
    try:
        p = getattr(game, "player", None)
        if p is not None:
            pid = getattr(p, "id", None)
            if pid is not None:
                has_player = any(getattr(rec[0], "id", None) == pid for rec in selected)
                if not has_player and pax is not None and pay is not None:
                    # If player was in candidates, it would've been selected; but if not,
                    # include it anyway to satisfy "fovea never disappears".
                    # Compute correct local_pos (not 0,0!) for tile lookup in rendering.
                    try:
                        p_zone_coord, p_local = game.zone_local_from_abs((int(pax), int(pay)), depth=zz)
                        p_local_pos = (int(p_local[0]), int(p_local[1]))
                    except Exception:
                        # Fallback: use player.pos if abs conversion fails
                        p_zone_coord = getattr(game, "zone_coord", (0, 0, zz))
                        p_local_pos = getattr(p, "pos", (0, 0))
                    selected.append((p, float(pax), float(pay), p_zone_coord, p_local_pos, 1.0))
    except Exception:
        pass

    for obj, abs_x, abs_y, coord, local_pos, _abs_size in selected:
        out.append(
            proxy_cls(
                obj=obj,
                abs_x=float(abs_x),
                abs_y=float(abs_y),
                zone_coord=coord,
                local_pos=local_pos,
            )
        )

    return out




def _ensure_world_aggregate_entities(
    game,
    *,
    zone_w: int,
    zone_h: int,
    zx0: int,
    zx1: int,
    zy0: int,
    zy1: int,
    zz: int,
    kinds=None,
) -> None:
    """Ensure aggregate world entities exist in WorldEntityIndex (no gameplay side effects).

    General-purpose mechanism used for berry patches today; goblin bands / forests / armies tomorrow.
    """
    # If the world_entity_index was rebuilt (e.g. zone dims changed), aggregates must be re-added.
    wh = (int(zone_w), int(zone_h))
    prev = getattr(game, "_agg_world_entity_index_wh", None)
    if prev != wh:
        try:
            # Clear incremental generation tracking so we can repopulate the fresh index.
            if hasattr(game, "_agg_worldgen_done"):
                game._agg_worldgen_done.clear()
        except Exception:
            pass
        game._agg_world_entity_index_wh = wh

    aggregate_system.ensure_world_aggregates(
        game,
        zone_w=int(zone_w),
        zone_h=int(zone_h),
        zx0=int(zx0),
        zx1=int(zx1),
        zy0=int(zy0),
        zy1=int(zy1),
        zz=int(zz),
        kinds=kinds,
    )

def _realize_aggregate_details_in_zone(game, level: "LevelState", coord: Tuple[int, int, int], kinds=None) -> None:
    """DEPRECATED (Yoga): do not stamp aggregate children into zones on entry.

    Aggregate detail is now attention-driven and staged via AttnStore so that
    god-vision / camera observation is sufficient to realize entities anywhere.

    Zone entry must not create a parallel population (duplication + perf cliffs).

    If, in the future, we want remote attention-staged actors to *simulate* while their
    zone is loaded, we should register/import the already-staged entities by stable eid/lineage.
    """
    return
