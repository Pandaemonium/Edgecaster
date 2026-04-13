"""Resolver-backed entity expansion and collapse helpers.

This module owns the lifecycle of deterministic resolver children once a parent
entity enters or leaves the active attention window. The scope for this first
pass is intentionally narrow:

- only resolver-backed parents (`tags.resolve`)
- only one expansion step at a time
- no save-format changes

Attention decides *which* parents need detail. This module handles *how* those
children are materialized, registered, persisted, and collapsed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.enemies import factory as enemy_factory
from edgecaster.state import chakra_component as chakra_component_state
from edgecaster.state.actors import Human, Stats
from edgecaster.state.entities import Entity
from edgecaster.systems import aggregate_resolution as aggregate_system
from edgecaster.systems import entity_body as entity_body_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_snapshots as entity_snapshots_system
from edgecaster.systems import spawning as spawning_system


def _entity_id(obj: object) -> str:
    try:
        eid = str(getattr(obj, "entity_id", "") or getattr(obj, "id", "") or "").strip()
    except Exception:
        eid = ""
    return eid


def _tags(obj: object) -> Dict[str, Any]:
    try:
        raw = getattr(obj, "tags", None)
    except Exception:
        raw = None
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _normalize_optional_lineage_id(raw: object) -> Optional[str]:
    try:
        lineage_id = str(raw or "").strip()
    except Exception:
        lineage_id = ""
    return lineage_id or None


def _zone_dims(game: object) -> Tuple[int, int]:
    cfg = getattr(game, "cfg", None)
    try:
        zone_w = int(getattr(cfg, "world_width", 0) or 0)
    except Exception:
        zone_w = 0
    try:
        zone_h = int(getattr(cfg, "world_height", 0) or 0)
    except Exception:
        zone_h = 0
    if zone_w <= 0 or zone_h <= 0:
        try:
            level = game._level()
            world = getattr(level, "world", None)
            if zone_w <= 0:
                zone_w = int(getattr(world, "width", 64) or 64)
            if zone_h <= 0:
                zone_h = int(getattr(world, "height", 64) or 64)
        except Exception:
            zone_w = zone_w or 64
            zone_h = zone_h or 64
    return (max(1, int(zone_w)), max(1, int(zone_h)))


def _effective_entity_state(
    game: object,
    *,
    entity_id: str | None,
    lineage_id: str | None,
) -> Dict[str, Any]:
    try:
        getter = getattr(game, "get_effective_entity_state", None)
        if callable(getter):
            key = str(entity_id or "").strip() or str(lineage_id or "").strip()
            return dict(getter(key, lineage_id=lineage_id) or {})
    except Exception:
        pass

    store = getattr(game, "entity_state", None)
    if not isinstance(store, dict):
        return {}

    out: Dict[str, Any] = {}
    lid = str(lineage_id or "").strip()
    eid = str(entity_id or "").strip()
    if lid:
        state = store.get(lid)
        if isinstance(state, dict):
            out.update(dict(state))
    if eid:
        state = store.get(eid)
        if isinstance(state, dict):
            out.update(dict(state))
    return out


def _is_suppressed(
    game: object,
    *,
    entity_id: str,
    lineage_id: Optional[str],
) -> bool:
    state = _effective_entity_state(game, entity_id=entity_id, lineage_id=lineage_id)
    return bool(state.get("removed")) or bool(state.get("dead"))


def _mark_entity_live(
    game: object,
    entity_id: str,
    *,
    lineage_id: Optional[str] = None,
) -> None:
    try:
        patch = getattr(game, "patch_entity_state", None)
        if callable(patch):
            if lineage_id:
                patch(str(entity_id), removed=False, dead=False, lineage_id=str(lineage_id))
            else:
                patch(str(entity_id), removed=False, dead=False)
    except Exception:
        pass


def _coerce_chakra_component_for_entity(ent: object):
    try:
        raw = getattr(ent, "chakra_component", None)
    except Exception:
        raw = None
    eid = _entity_id(ent)
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
    """Create a lightweight parent->child chakra edge for realized descendants."""
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
        nodes = getattr(pcomp, "nodes", None)
        if not isinstance(nodes, dict):
            nodes = {}
            pcomp.nodes = nodes
        if dst not in nodes:
            # Capture the child's abs_pos so geometry queries on the parent
            # can read positions without needing a separate entity lookup.
            child_abs_pos = None
            try:
                # Prefer body_float_pos tag for sub-tile precision; fall back to
                # integer abs_pos which is only the tile anchor.
                child_tags = getattr(child, "tags", None) or {}
                float_pos = child_tags.get("body_float_pos") if isinstance(child_tags, dict) else None
                if isinstance(float_pos, (tuple, list)) and len(float_pos) >= 2:
                    child_abs_pos = (float(float_pos[0]), float(float_pos[1]))
                else:
                    raw_pos = getattr(child, "abs_pos", None)
                    if isinstance(raw_pos, (tuple, list)) and len(raw_pos) >= 2:
                        child_abs_pos = (float(raw_pos[0]), float(raw_pos[1]))
            except Exception:
                pass
            nodes[dst] = chakra_component_state.ChakraNode(
                node_id=dst,
                kind="external_child_root",
                active=True,
                abs_pos=child_abs_pos,
                channels={},
                tags={
                    "external_ref": True,
                    "entity_id": _entity_id(child),
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
                    "dst_entity_id": _entity_id(child),
                },
            )
    except Exception:
        pass

    try:
        root = ccomp.nodes.get(dst)
        if root is not None:
            root.tags["parent_root_node_id"] = src
    except Exception:
        pass


def _entity_abs_anchor(
    ent: object,
    *,
    zone_coord: Tuple[int, int, int],
    local_pos: Tuple[int, int],
    zone_w: int,
    zone_h: int,
) -> Tuple[int, int]:
    ap = getattr(ent, "abs_pos", None)
    if isinstance(ap, (list, tuple)) and len(ap) >= 2:
        try:
            return (int(ap[0]), int(ap[1]))
        except Exception:
            pass
    zx, zy, _ = zone_coord
    lx, ly = local_pos
    return (int(zx) * int(zone_w) + int(lx), int(zy) * int(zone_h) + int(ly))


def _zone_local_for_entity(game: object, ent: object) -> Tuple[Tuple[int, int, int], Tuple[int, int]]:
    zone_w, zone_h = _zone_dims(game)
    ap = getattr(ent, "abs_pos", None)
    if isinstance(ap, (list, tuple)) and len(ap) >= 2:
        ax = int(ap[0])
        ay = int(ap[1])
        z = 0
        try:
            zc = getattr(ent, "zone_coord", None)
            if isinstance(zc, (list, tuple)) and len(zc) >= 3:
                z = int(zc[2])
        except Exception:
            z = 0
        zx = ax // zone_w
        zy = ay // zone_h
        return ((int(zx), int(zy), int(z)), (int(ax - zx * zone_w), int(ay - zy * zone_h)))

    try:
        zc = getattr(ent, "zone_coord", None)
        lp = getattr(ent, "pos", None)
        if isinstance(zc, (list, tuple)) and len(zc) >= 3 and isinstance(lp, (list, tuple)) and len(lp) >= 2:
            return (
                (int(zc[0]), int(zc[1]), int(zc[2])),
                (int(lp[0]), int(lp[1])),
            )
    except Exception:
        pass

    try:
        levels = getattr(game, "levels", None)
        if isinstance(levels, dict):
            eid = _entity_id(ent)
            for coord, level in levels.items():
                if eid and eid in getattr(level, "entities", {}):
                    pos = getattr(level.entities[eid], "pos", getattr(ent, "pos", (0, 0)))
                    return (
                        (int(coord[0]), int(coord[1]), int(coord[2])),
                        (int(pos[0]), int(pos[1])),
                    )
    except Exception:
        pass

    pos = getattr(ent, "pos", None)
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        return ((0, 0, 0), (int(pos[0]), int(pos[1])))
    return ((0, 0, 0), (0, 0))


def _runtime_entity_registry(game: object) -> Dict[str, object]:
    """Internal runtime-object registry for realized entities outside level/attn caches."""
    # [LEGACY_DELETE][ENTITY_CHAKRA][RUNTIME_REGISTRY]
    # This is a migration bridge until one authoritative runtime-entity registry
    # exists for *all* live entities instead of attention/level/world caches plus
    # this extra fallback for internal realized subtrees.
    raw = getattr(game, "_runtime_entity_index", None)
    if isinstance(raw, dict):
        return raw
    raw = {}
    try:
        setattr(game, "_runtime_entity_index", raw)
    except Exception:
        pass
    return raw


def _track_runtime_entity(game: object, obj: object) -> None:
    eid = _entity_id(obj)
    if not eid:
        return
    _runtime_entity_registry(game)[eid] = obj


def _stage_object(game: object, obj: object, *, abs_x: int, abs_y: int, zz: int) -> None:
    attn_store = getattr(game, "attn_store", None)
    try:
        if attn_store is not None:
            attn_store.stage(obj, abs_x=int(abs_x), abs_y=int(abs_y), zz=int(zz))
    except Exception:
        pass
    _track_runtime_entity(game, obj)


def _mirror_entity_into_loaded_zone(game: object, obj: object, *, abs_x: int, abs_y: int, zz: int) -> None:
    try:
        getter = getattr(game, "get_zone_for_render", None)
        if not callable(getter):
            return
        zone_w, zone_h = _zone_dims(game)
        level = getter((int(abs_x) // zone_w, int(abs_y) // zone_h, int(zz)))
        if level is None:
            return
        eid = _entity_id(obj)
        if not eid:
            return
        level.entities[eid] = obj
        try:
            level.spatial_dirty = True
        except Exception:
            pass
        _track_runtime_entity(game, obj)
    except Exception:
        pass


def _register_actor_in_loaded_zone(game: object, actor: object, *, abs_x: int, abs_y: int, zz: int) -> None:
    try:
        getter = getattr(game, "get_zone_for_render", None)
        if not callable(getter):
            return
        zone_w, zone_h = _zone_dims(game)
        level = getter((int(abs_x) // zone_w, int(abs_y) // zone_h, int(zz)))
        if level is None:
            return
        eid = _entity_id(actor)
        if not eid:
            return
        if eid not in getattr(level, "actors", {}):
            spawning_system.register_actor(game, level, actor, schedule_ai=True)
            try:
                level.spatial_dirty = True
            except Exception:
                pass
        try:
            attn_store = getattr(game, "attn_store", None)
            if attn_store is not None:
                attn_store.despawn(eid)
        except Exception:
            pass
        _track_runtime_entity(game, actor)
    except Exception:
        pass


def _remove_runtime_entity(game: object, entity_id: str) -> Optional[object]:
    eid = str(entity_id or "").strip()
    if not eid:
        return None

    found = None
    attn_store = getattr(game, "attn_store", None)
    try:
        if attn_store is not None:
            found = getattr(attn_store, "entities", {}).get(eid)
            attn_store.despawn(eid)
    except Exception:
        found = found

    try:
        levels = getattr(game, "levels", None)
        if isinstance(levels, dict):
            for level in levels.values():
                removed_here = False
                if found is None:
                    found = getattr(level, "entities", {}).get(eid) or getattr(level, "actors", {}).get(eid)
                try:
                    if eid in getattr(level, "entities", {}):
                        level.entities.pop(eid, None)
                        removed_here = True
                except Exception:
                    pass
                try:
                    if eid in getattr(level, "actors", {}):
                        level.actors.pop(eid, None)
                        removed_here = True
                except Exception:
                    pass
                if removed_here:
                    try:
                        level.spatial_dirty = True
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        reg = _runtime_entity_registry(game)
        if found is None:
            found = reg.get(eid)
        reg.pop(eid, None)
    except Exception:
        pass
    return found


def _set_parent_lod_state(game: object, entity_id: str, lod_state: str) -> None:
    try:
        graph = getattr(game, "entity_graph", None)
        if graph is None:
            return
        node = graph.get_node(str(entity_id))
        if node is not None:
            node.lod_state = str(lod_state or "expanded")
    except Exception:
        pass


def _expanded_children_map(game: object) -> Dict[str, set[str]]:
    # [LEGACY_DELETE][ENTITY_CHAKRA][ATTENTION]
    # The name is attention-specific historical baggage. After the broader
    # lifecycle cutover, rename this to a neutral expanded-children registry.
    raw = getattr(game, "_attn_active_resolved_children", None)
    if isinstance(raw, dict):
        return raw
    raw = {}
    try:
        setattr(game, "_attn_active_resolved_children", raw)
    except Exception:
        pass
    return raw


def _build_staged_actor(
    game: object,
    *,
    eid: str,
    npc_id: str,
    name: str,
    glyph: str,
    color: Tuple[int, int, int],
    abs_pos: Tuple[int, int],
    local_pos: Tuple[int, int],
    owner_id: str,
    zz: int,
    spec: Dict[str, Any],
):
    """Build a resolver-spawned actor while preserving current special cases."""
    spec_tags = (spec.get("tags") or {}) if isinstance(spec, dict) else {}
    zone_w, zone_h = _zone_dims(game)

    if npc_id == "caged_demon":
        try:
            actor = enemy_factory.spawn_enemy("caged_demon", local_pos, abs_pos=abs_pos, game=game)
        except TypeError:
            actor = enemy_factory.spawn_enemy("caged_demon", local_pos, game=game)
            try:
                actor.abs_pos = abs_pos
            except Exception:
                pass
        actor.id = eid
        actor.pos = local_pos
        actor.faction = "neutral"
        actor.actions = ()
        actor.ai = "idle"
        actor.tags = getattr(actor, "tags", {}) or {}
        actor.tags.update({"npc": True, "npc_id": npc_id, "owner_id": owner_id})
        actor.tags["show_exact_hp"] = True
        actor.name = name
        try:
            actor.glyph = glyph
            actor.color = color  # type: ignore[assignment]
        except Exception:
            pass
        desc = spec.get("description") or getattr(actor, "description", None)
        if desc:
            actor.description = desc
        try:
            actor.regen_per_tick = (1, 10)
            getter = getattr(game, "get_zone_for_render", None)
            level = None
            if callable(getter):
                level = getter((abs_pos[0] // zone_w, abs_pos[1] // zone_h, int(zz)))
            if level is None:
                level = game._level()
            game._start_regen(level, actor.id, amount=1, interval=10)
        except Exception:
            pass
        return actor

    if npc_id == "merchant":
        try:
            actor = enemy_factory.spawn_enemy("merchant", local_pos, abs_pos=abs_pos, game=game)
        except TypeError:
            actor = enemy_factory.spawn_enemy("merchant", local_pos, game=game)
            try:
                actor.abs_pos = abs_pos
            except Exception:
                pass
        actor.id = eid
        actor.pos = local_pos
        actor.faction = "npc"
        actor.actions = ()
        actor.ai = "idle"
        actor.tags = getattr(actor, "tags", {}) or {}
        actor.tags.update({"npc": True, "npc_id": npc_id, "owner_id": owner_id})
        actor.tags["merchant_id"] = spec_tags.get("merchant_id", "general_store")
        actor.name = name
        try:
            actor.glyph = glyph
            actor.color = color  # type: ignore[assignment]
        except Exception:
            pass
        desc = spec.get("description") or getattr(actor, "description", None)
        if desc:
            actor.description = desc
        try:
            from edgecaster.systems import trade as trade_system

            getter = getattr(game, "get_zone_for_render", None)
            if callable(getter):
                level = getter((abs_pos[0] // zone_w, abs_pos[1] // zone_h, int(zz)))
                if level is not None:
                    trade_system.ensure_merchant_initialized(game, level, actor)
        except Exception:
            pass
        return actor

    try:
        actor = Human(
            id=eid,
            name=name,
            pos=local_pos,
            abs_pos=abs_pos,
            faction="npc",
            stats=Stats(hp=50, max_hp=50),
            tags={"npc": True, "npc_id": npc_id, "owner_id": owner_id},
            disposition=int(spec_tags.get("base_disposition", 0) or 0),
            affiliations=tuple(spec_tags.get("factions", [])),
            glyph=glyph,
            color=color,  # type: ignore[arg-type]
        )
    except TypeError:
        actor = Human(
            id=eid,
            name=name,
            pos=local_pos,
            faction="npc",
            stats=Stats(hp=50, max_hp=50),
            tags={"npc": True, "npc_id": npc_id, "owner_id": owner_id},
            disposition=int(spec_tags.get("base_disposition", 0) or 0),
            affiliations=tuple(spec_tags.get("factions", [])),
            glyph=glyph,
            color=color,  # type: ignore[arg-type]
        )
        try:
            actor.abs_pos = abs_pos
        except Exception:
            pass
    desc = spec.get("description")
    if desc:
        actor.description = desc
    return actor


def _build_staged_entity(
    *,
    eid: str,
    abs_x: int,
    abs_y: int,
    local_pos: Tuple[int, int],
    staged: Dict[str, Any],
) -> Entity:
    tags = dict(staged.get("tags", {}) or {})
    obj = Entity(
        id=str(eid),
        entity_id=str(eid),
        name=str(tags.get("name") or eid),
        pos=(int(local_pos[0]), int(local_pos[1])),
        abs_pos=(int(abs_x), int(abs_y)),
        glyph=str(staged.get("glyph", "#") or "#")[:1] or "#",
        color=tuple(staged.get("color", (140, 120, 100))),
        kind=str(staged.get("kind", "structure") or "structure"),
        blocks_movement=bool(tags.get("blocks_movement", False)),
        tags=tags,
    )
    obj.footprint_abs = (float(abs_x), float(abs_y), float(abs_x) + 1.0, float(abs_y) + 1.0)
    obj.footprint_local = (
        float(local_pos[0]),
        float(local_pos[1]),
        float(local_pos[0]) + 1.0,
        float(local_pos[1]) + 1.0,
    )
    try:
        obj.base_size = float(staged.get("base_size", 1.0) or 1.0)
    except Exception:
        obj.base_size = 1.0
    return obj


def find_runtime_entity(game: object, entity_id: str) -> Optional[object]:
    """Return the live runtime object for entity_id from any current cache."""
    eid = str(entity_id or "").strip()
    if not eid:
        return None

    try:
        obj = _runtime_entity_registry(game).get(eid)
        if obj is not None:
            return obj
    except Exception:
        pass

    attn_store = getattr(game, "attn_store", None)
    try:
        if attn_store is not None:
            obj = getattr(attn_store, "entities", {}).get(eid)
            if obj is not None:
                return obj
    except Exception:
        pass

    try:
        levels = getattr(game, "levels", None)
        if isinstance(levels, dict):
            for level in levels.values():
                obj = getattr(level, "entities", {}).get(eid)
                if obj is not None:
                    return obj
                obj = getattr(level, "actors", {}).get(eid)
                if obj is not None:
                    return obj
    except Exception:
        pass

    try:
        index = getattr(game, "world_entity_index", None)
        buckets = getattr(index, "_by_zone", None)
        if isinstance(buckets, dict):
            for refs in buckets.values():
                for ref in refs:
                    ent = getattr(ref, "ent", None)
                    if ent is None:
                        continue
                    if _entity_id(ent) == eid:
                        return ent
    except Exception:
        pass
    return None


def _materialize_child(
    game: object,
    *,
    parent_ent: object,
    parent_id: str,
    intent: aggregate_system.SpawnIntent,
) -> Optional[object]:
    zone_w, zone_h = _zone_dims(game)
    abs_x = int(intent.abs_x)
    abs_y = int(intent.abs_y)
    zz = int(intent.zz)
    zxx = abs_x // zone_w
    zyy = abs_y // zone_h
    local_pos = (int(abs_x - zxx * zone_w), int(abs_y - zyy * zone_h))
    lineage_id = _normalize_optional_lineage_id(intent.lineage_id)
    eid = str(intent.eid)

    existing = find_runtime_entity(game, eid)
    if existing is not None:
        try:
            entity_graph_ops_system.attach_entity_to_parent(game, existing, parent_id, socket_id="resolve")
        except Exception:
            pass
        _link_parent_child_chakra(parent_ent, existing)
        state = _effective_entity_state(game, entity_id=eid, lineage_id=lineage_id)
        if not entity_snapshots_system.apply_entity_snapshot(existing, state, lineage_id=lineage_id):
            return None
        return existing

    child_type = str(intent.child_type or "entity").strip().lower()
    obj = None

    if child_type == "staged" and isinstance(intent.staged, dict):
        staged = dict(intent.staged)
        staged_tags = dict(staged.get("tags", {}) or {})
        if intent.tags:
            staged_tags.update(dict(intent.tags))
        if lineage_id:
            staged_tags["lineage_id"] = lineage_id
        staged["tags"] = staged_tags
        obj = _build_staged_entity(
            eid=eid,
            abs_x=abs_x,
            abs_y=abs_y,
            local_pos=local_pos,
            staged=staged,
        )
    elif child_type == "actor":
        try:
            spec = prototypes.resolve_proto(str(intent.proto_id))
        except Exception:
            spec = None
        if not isinstance(spec, dict):
            spec = {}
        glyph = str(spec.get("glyph") or "@")[:1] or "@"
        name = str(spec.get("name") or intent.proto_id or "Actor")
        raw_color = spec.get("color") or (255, 255, 255)
        color = tuple(raw_color) if isinstance(raw_color, (tuple, list)) else (255, 255, 255)
        obj = _build_staged_actor(
            game,
            eid=eid,
            npc_id=str(intent.proto_id),
            name=name,
            glyph=glyph,
            color=color,
            abs_pos=(abs_x, abs_y),
            local_pos=local_pos,
            owner_id=parent_id,
            zz=zz,
            spec=spec,
        )
        try:
            obj.entity_id = eid
        except Exception:
            pass
    else:
        try:
            spec = prototypes.resolve_proto(str(intent.proto_id))
        except Exception:
            spec = None
        if not isinstance(spec, dict):
            return None
        overrides = {"tags": dict(intent.tags or {})}
        obj = spawn_factory.build_entity_from_spec(
            spec=spec,
            eid=eid,
            pos=local_pos,
            abs_pos=(abs_x, abs_y),
            overrides=overrides,
        )

    if obj is None:
        return None

    tags = _tags(obj)
    if intent.tags:
        tags.update(dict(intent.tags))
    if lineage_id:
        tags["lineage_id"] = lineage_id
    try:
        setattr(obj, "tags", tags)
    except Exception:
        pass

    try:
        entity_graph_ops_system.attach_entity_to_parent(game, obj, parent_id, socket_id="resolve")
    except Exception:
        pass
    _link_parent_child_chakra(parent_ent, obj)

    state = _effective_entity_state(game, entity_id=eid, lineage_id=lineage_id)
    if not entity_snapshots_system.apply_entity_snapshot(obj, state, lineage_id=lineage_id):
        return None

    if child_type == "actor":
        _stage_object(game, obj, abs_x=abs_x, abs_y=abs_y, zz=zz)
        _mark_entity_live(game, eid, lineage_id=lineage_id)
        _register_actor_in_loaded_zone(game, obj, abs_x=abs_x, abs_y=abs_y, zz=zz)
    elif child_type == "staged":
        _stage_object(game, obj, abs_x=abs_x, abs_y=abs_y, zz=zz)
        _mark_entity_live(game, eid, lineage_id=lineage_id)
    else:
        _stage_object(game, obj, abs_x=abs_x, abs_y=abs_y, zz=zz)
        _mark_entity_live(game, eid, lineage_id=lineage_id)
        _mirror_entity_into_loaded_zone(game, obj, abs_x=abs_x, abs_y=abs_y, zz=zz)

    return obj


def _materialize_body_child(
    game: object,
    *,
    owner_ent: object,
    parent_ent: object,
    parent_id: str,
    spec: entity_body_system.BodyNodeSpec,
) -> Optional[object]:
    eid = str(spec.entity_id)
    existing = find_runtime_entity(game, eid)
    if existing is not None:
        try:
            entity_graph_ops_system.attach_entity_to_parent(game, existing, parent_id, socket_id="body")
        except Exception:
            pass
        _link_parent_child_chakra(parent_ent, existing)
        _track_runtime_entity(game, existing)
        return existing

    try:
        proto_id = str(spec.node_proto_id or "").strip()
    except Exception:
        proto_id = ""
    if not proto_id:
        return None

    base_proto_id = str(prototypes.base_proto_id(proto_id) or proto_id)
    try:
        resolved = prototypes.resolve_proto(base_proto_id)
    except Exception:
        resolved = None
    if not isinstance(resolved, dict):
        return None

    owner_id = _entity_id(owner_ent)
    # Preserve sub-tile float precision in a tag; integer pos is the tile anchor.
    float_x = float(spec.abs_pos[0])
    float_y = float(spec.abs_pos[1])
    abs_x = int(round(float_x))
    abs_y = int(round(float_y))
    obj = spawn_factory.build_entity_from_spec(
        spec=resolved,
        eid=eid,
        pos=(abs_x, abs_y),
        abs_pos=(abs_x, abs_y),
        overrides={
            "tags": {
                "body_node": True,
                "body_owner_id": owner_id,
                "body_full_id": str(spec.full_id),
                "body_parent_full_id": str(spec.parent_full_id or ""),
                "body_local_node_id": str(spec.local_node_id),
                "body_schema_proto_id": str(spec.schema_proto_id),
                "body_node_proto_id": str(spec.node_proto_id),
                "body_schema_root": bool(spec.is_schema_root),
                "body_mirrored": bool(spec.mirrored),
                "internal_entity": True,
                # Sub-tile float position for geometry queries; coarser abs_pos
                # is the tile anchor used for grid-based lookups.
                "body_float_pos": (float_x, float_y),
            }
        },
    )
    try:
        obj.semantic_id = str(spec.full_id)
    except Exception:
        pass
    try:
        obj.name = str(getattr(obj, "name", "") or spec.full_id.replace(".", " ").replace("_", " ").title())
    except Exception:
        pass
    try:
        obj.render_layer = -100  # internal hierarchy only; not world-rendered
    except Exception:
        pass
    try:
        obj.blocks_movement = False
    except Exception:
        pass
    try:
        obj.base_size = float(spec.local_scale)
    except Exception:
        pass
    try:
        obj.abs_size = float(spec.local_scale)
    except Exception:
        pass
    try:
        obj.footprint_abs = (
            float(spec.abs_pos[0]) - 0.05,
            float(spec.abs_pos[1]) - 0.05,
            float(spec.abs_pos[0]) + 0.05,
            float(spec.abs_pos[1]) + 0.05,
        )
    except Exception:
        pass

    try:
        entity_graph_ops_system.attach_entity_to_parent(game, obj, parent_id, socket_id="body")
    except Exception:
        pass
    _link_parent_child_chakra(parent_ent, obj)
    _track_runtime_entity(game, obj)
    _mark_entity_live(game, eid)
    return obj


def _remove_resolved_subtree(game: object, root_entity_id: str) -> None:
    graph = getattr(game, "entity_graph", None)
    if graph is None:
        return

    eid = str(root_entity_id or "").strip()
    if not eid:
        return

    expanded_children = _expanded_children_map(game)
    child_ids = set(graph.get_children(eid, socket_id="resolve"))
    child_ids.update(graph.get_children(eid, socket_id="body"))
    child_ids.update(expanded_children.get(eid, set()) or set())
    for child_id in sorted(child_ids):
        _remove_resolved_subtree(game, child_id)

    runtime_obj = find_runtime_entity(game, eid)
    if runtime_obj is not None:
        lineage_id = _normalize_optional_lineage_id(_tags(runtime_obj).get("lineage_id"))
        entity_snapshots_system.persist_entity_snapshot(
            game,
            runtime_obj,
            entity_id=eid,
            lineage_id=lineage_id,
        )
    _remove_runtime_entity(game, eid)
    try:
        graph.deregister(eid)
    except Exception:
        pass
    expanded_children.pop(eid, None)


def expand_entity(game: object, entity_id: str, *, reason: str = "attention") -> List[str]:
    """Expand a deterministic parent by materializing its direct children."""
    del reason  # reserved for profiling/telemetry hooks later

    parent_id = str(entity_id or "").strip()
    if not parent_id:
        return []

    graph = getattr(game, "entity_graph", None)
    if graph is None:
        return []

    parent_ent = find_runtime_entity(game, parent_id)
    node = graph.get_node(parent_id)
    existing_children = sorted(graph.get_children(parent_id, socket_id="resolve"))
    if node is not None and node.lod_state == "expanded" and existing_children:
        missing = [cid for cid in existing_children if find_runtime_entity(game, cid) is None]
        if not missing:
            _expanded_children_map(game)[parent_id] = set(existing_children)
            return existing_children

    if parent_ent is None:
        if node is not None and node.lod_state == "expanded":
            return existing_children
        return []

    parent_tags = _tags(parent_ent)

    if entity_body_system.can_expand_entity(parent_ent):
        owner_id = entity_body_system.body_owner_entity_id(parent_ent)
        owner_ent = parent_ent if owner_id == parent_id else find_runtime_entity(game, owner_id)
        if owner_ent is None:
            return existing_children
        parent_full_id = entity_body_system.body_full_id(parent_ent)
        body_specs = entity_body_system.child_specs_for_entity(owner_ent, parent_full_id)

        realized_ids: List[str] = []
        realized_set: set[str] = set()
        existing_body_children = sorted(graph.get_children(parent_id, socket_id="body"))
        for spec in body_specs:
            child_id = str(spec.entity_id)
            if not child_id:
                continue
            obj = _materialize_body_child(
                game,
                owner_ent=owner_ent,
                parent_ent=parent_ent,
                parent_id=parent_id,
                spec=spec,
            )
            if obj is None or child_id in realized_set:
                continue
            realized_ids.append(child_id)
            realized_set.add(child_id)

        for stale_id in existing_body_children:
            if stale_id in realized_set:
                continue
            _remove_resolved_subtree(game, stale_id)

        _expanded_children_map(game)[parent_id] = set(realized_ids)
        _set_parent_lod_state(game, parent_id, "expanded")
        return realized_ids

    if "resolve" not in parent_tags:
        _set_parent_lod_state(game, parent_id, "expanded")
        _expanded_children_map(game)[parent_id] = set(existing_children)
        return existing_children

    zone_w, zone_h = _zone_dims(game)
    zone_coord, local_pos = _zone_local_for_entity(game, parent_ent)
    zz = int(zone_coord[2])
    parent_depth = int(parent_tags.get("_resolve_depth", 0) or 0)

    try:
        intents = aggregate_system.resolve_spawn_intents_from_recipe(
            game,
            parent_ent=parent_ent,
            zone_coord=zone_coord,
            local_pos=local_pos,
            zone_w=int(zone_w),
            zone_h=int(zone_h),
            zz=int(zz),
            max_depth=int(parent_depth + 1),
        )
    except Exception:
        intents = []

    realized_ids: List[str] = []
    realized_set: set[str] = set()
    for intent in intents:
        child_id = str(intent.eid or "").strip()
        lineage_id = _normalize_optional_lineage_id(intent.lineage_id)
        if not child_id or _is_suppressed(game, entity_id=child_id, lineage_id=lineage_id):
            continue
        obj = _materialize_child(game, parent_ent=parent_ent, parent_id=parent_id, intent=intent)
        if obj is None or child_id in realized_set:
            continue
        realized_ids.append(child_id)
        realized_set.add(child_id)

    # If the resolver shape or suppression state changed, remove stale direct children.
    stale_ids = [cid for cid in existing_children if cid not in realized_set]
    for stale_id in stale_ids:
        _remove_resolved_subtree(game, stale_id)

    _expanded_children_map(game)[parent_id] = set(realized_ids)
    _set_parent_lod_state(game, parent_id, "expanded")
    return realized_ids


def collapse_entity(game: object, entity_id: str, *, reason: str = "attention") -> None:
    """Collapse a resolver-backed parent by removing its realized descendants."""
    del reason  # reserved for profiling/telemetry hooks later

    parent_id = str(entity_id or "").strip()
    if not parent_id:
        return

    graph = getattr(game, "entity_graph", None)
    if graph is None:
        return

    expanded_children = _expanded_children_map(game)
    child_ids = set(graph.get_children(parent_id, socket_id="resolve"))
    child_ids.update(graph.get_children(parent_id, socket_id="body"))
    child_ids.update(expanded_children.get(parent_id, set()) or set())

    for child_id in sorted(child_ids):
        _remove_resolved_subtree(game, child_id)

    expanded_children.pop(parent_id, None)
    _set_parent_lod_state(game, parent_id, "collapsed")
