"""Shared geometry query helpers for entity/chakra graph consumers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from edgecaster.state import chakra_component as chakra_component_state
from edgecaster.systems import entity_body as entity_body_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_lifecycle as entity_lifecycle_system


def _entity_id(obj: object) -> str:
    try:
        return str(getattr(obj, "entity_id", "") or getattr(obj, "id", "") or "").strip()
    except Exception:
        return ""


def _tags(obj: object) -> Dict[str, Any]:
    try:
        raw = getattr(obj, "tags", None)
    except Exception:
        raw = None
    return dict(raw or {}) if isinstance(raw, dict) else {}


def _warn_once(warnings: List[str], code: str) -> None:
    if code not in warnings:
        warnings.append(str(code))


def _coerce_component(root_entity_id: str, runtime_obj: object):
    try:
        raw = getattr(runtime_obj, "chakra_component", None)
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        return chakra_component_state.coerce_chakra_component(raw, entity_id=str(root_entity_id))
    except Exception:
        return None


def _runtime_shape(obj: object) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        abs_pos = getattr(obj, "abs_pos", None)
        if isinstance(abs_pos, (tuple, list)) and len(abs_pos) >= 2:
            out["abs_pos"] = (float(abs_pos[0]), float(abs_pos[1]))
    except Exception:
        pass
    try:
        footprint_abs = getattr(obj, "footprint_abs", None)
        if isinstance(footprint_abs, (tuple, list)) and len(footprint_abs) >= 4:
            out["footprint_abs"] = tuple(float(v) for v in footprint_abs[:4])
    except Exception:
        pass
    try:
        stats = getattr(obj, "stats", None)
        if stats is not None:
            stats_out: Dict[str, Any] = {}
            for key in ("hp", "max_hp", "mana", "max_mana"):
                try:
                    value = getattr(stats, key, None)
                except Exception:
                    value = None
                if value is not None:
                    stats_out[str(key)] = value
            if stats_out:
                out["stats"] = stats_out
    except Exception:
        pass
    return out


def _root_runtime_and_node(game: object, root_entity_id: str) -> Tuple[Optional[object], Optional[object]]:
    runtime_obj = entity_lifecycle_system.find_runtime_entity(game, root_entity_id)
    graph = getattr(game, "entity_graph", None)
    node = graph.get_node(str(root_entity_id)) if graph is not None else None
    if runtime_obj is not None and node is None:
        try:
            entity_graph_ops_system.register_entity(game, runtime_obj)
            node = graph.get_node(str(root_entity_id)) if graph is not None else None
        except Exception:
            node = node
    return (runtime_obj, node)


def _add_entity_node(
    out_nodes: List[Dict[str, Any]],
    *,
    entity_id: str,
    graph_node: object | None,
    runtime_obj: object | None,
) -> None:
    entry: Dict[str, Any] = {
        "id": f"entity:{entity_id}",
        "node_type": "entity",
        "entity_id": str(entity_id),
    }
    if graph_node is not None:
        for attr in ("kind", "semantic_id", "parent_entity_id", "socket_id", "lod_state", "layout_id", "rule_id"):
            try:
                value = getattr(graph_node, attr, None)
            except Exception:
                value = None
            if value is not None:
                entry[str(attr)] = value
        try:
            tags = getattr(graph_node, "tags", None)
            if isinstance(tags, dict) and tags:
                entry["graph_tags"] = dict(tags)
        except Exception:
            pass
    if runtime_obj is not None:
        try:
            entry["name"] = str(getattr(runtime_obj, "name", "") or entity_id)
        except Exception:
            pass
        try:
            entry["kind"] = str(getattr(runtime_obj, "kind", entry.get("kind", "generic")) or entry.get("kind", "generic"))
        except Exception:
            pass
        runtime_tags = _tags(runtime_obj)
        if runtime_tags:
            entry["tags"] = runtime_tags
        entry.update(_runtime_shape(runtime_obj))
    out_nodes.append(entry)


def _add_component_geometry(
    out_nodes: List[Dict[str, Any]],
    out_edges: List[Dict[str, Any]],
    *,
    root_entity_id: str,
    runtime_obj: object,
    include_relation_edges: bool,
    derived: Dict[str, Any],
) -> None:
    comp = _coerce_component(root_entity_id, runtime_obj)
    if comp is None:
        return

    derived["chakra_root_node_id"] = str(getattr(comp, "root_node_id", "") or "")
    try:
        derived["layout_id"] = str(getattr(comp, "tags", {}).get("layout_id", "") or "")
    except Exception:
        pass

    root_abs = None
    try:
        abs_pos = getattr(runtime_obj, "abs_pos", None)
        if isinstance(abs_pos, (tuple, list)) and len(abs_pos) >= 2:
            root_abs = (float(abs_pos[0]), float(abs_pos[1]))
    except Exception:
        root_abs = None

    for node_id in sorted(comp.nodes.keys()):
        chakra_node = comp.nodes[node_id]
        entry: Dict[str, Any] = {
            "id": f"chakra:{root_entity_id}:{node_id}",
            "node_type": "chakra",
            "entity_id": str(root_entity_id),
            "chakra_node_id": str(node_id),
            "kind": str(getattr(chakra_node, "kind", "core") or "core"),
            "active": bool(getattr(chakra_node, "active", True)),
            "channels": dict(getattr(chakra_node, "channels", {}) or {}),
            "tags": dict(getattr(chakra_node, "tags", {}) or {}),
        }
        try:
            pos = getattr(chakra_node, "abs_pos", None)
            if isinstance(pos, (tuple, list)) and len(pos) >= 2:
                entry["abs_pos"] = (float(pos[0]), float(pos[1]))
            elif str(node_id) == str(comp.root_node_id) and root_abs is not None:
                entry["abs_pos"] = root_abs
        except Exception:
            pass
        out_nodes.append(entry)

    for edge_id in sorted(comp.edges.keys()):
        chakra_edge = comp.edges[edge_id]
        out_edges.append(
            {
                "id": f"chakra_edge:{root_entity_id}:{edge_id}",
                "edge_type": "chakra",
                "entity_id": str(root_entity_id),
                "src": f"chakra:{root_entity_id}:{chakra_edge.src_node_id}",
                "dst": f"chakra:{root_entity_id}:{chakra_edge.dst_node_id}",
                "src_node_id": str(chakra_edge.src_node_id),
                "dst_node_id": str(chakra_edge.dst_node_id),
                "kind": str(getattr(chakra_edge, "kind", "contains") or "contains"),
                "propagation": str(getattr(chakra_edge, "propagation", "automatic") or "automatic"),
                "weight": float(getattr(chakra_edge, "weight", 1.0) or 1.0),
                "tags": dict(getattr(chakra_edge, "tags", {}) or {}),
            }
        )

    if include_relation_edges and str(getattr(comp, "root_node_id", "") or "").strip():
        out_edges.append(
            {
                "id": f"entity_chakra_root:{root_entity_id}",
                "edge_type": "relation",
                "src": f"entity:{root_entity_id}",
                "dst": f"chakra:{root_entity_id}:{comp.root_node_id}",
                "kind": "chakra_root",
                "tags": {},
            }
        )


def query_geometry(
    game: object,
    root_entity_id: str,
    *,
    detail_level: str = "raw",
    max_depth: Optional[int] = None,
    include_relation_edges: bool = True,
    derive: Tuple[str, ...] | List[str] = (),
    realize_policy: str = "forbid",
) -> Dict[str, Any]:
    """Return a stable geometry envelope for an entity root."""
    del detail_level  # Reserved for richer shape projections later.

    root_id = str(root_entity_id or "").strip()
    warnings: List[str] = []
    graph = getattr(game, "entity_graph", None)
    runtime_obj, root_node = _root_runtime_and_node(game, root_id)

    if realize_policy == "require":
        runtime_tags = _tags(runtime_obj) if runtime_obj is not None else {}
        if "resolve" in runtime_tags:
            entity_lifecycle_system.expand_entity(game, root_id, reason="geometry_query")
            runtime_obj, root_node = _root_runtime_and_node(game, root_id)
        if runtime_obj is not None and entity_body_system.can_expand_entity(runtime_obj):
            queue = [root_id]
            seen_expand: set[str] = set()
            while queue:
                current_id = str(queue.pop(0) or "")
                if not current_id or current_id in seen_expand:
                    continue
                seen_expand.add(current_id)
                current_obj = entity_lifecycle_system.find_runtime_entity(game, current_id)
                if current_obj is None or not entity_body_system.can_expand_entity(current_obj):
                    continue
                child_ids = entity_lifecycle_system.expand_entity(game, current_id, reason="geometry_query")
                queue.extend([str(child_id) for child_id in child_ids])
            runtime_obj, root_node = _root_runtime_and_node(game, root_id)

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    derived_out: Dict[str, Any] = {}

    if not root_id:
        _warn_once(warnings, "missing_root_entity_id")
        return {
            "root_entity_id": "",
            "realization_state": "missing",
            "precision": "approximate",
            "nodes": [],
            "edges": [],
            "derived": {},
            "warnings": warnings,
        }

    _add_entity_node(nodes, entity_id=root_id, graph_node=root_node, runtime_obj=runtime_obj)
    if runtime_obj is not None:
        _add_component_geometry(
            nodes,
            edges,
            root_entity_id=root_id,
            runtime_obj=runtime_obj,
            include_relation_edges=bool(include_relation_edges),
            derived=derived_out,
        )

    queue: List[Tuple[str, int]] = [(root_id, 0)]
    visited = {root_id}
    precision = "exact"

    while queue and graph is not None:
        current_id, depth = queue.pop(0)
        current_node = graph.get_node(current_id)
        current_obj = entity_lifecycle_system.find_runtime_entity(game, current_id)
        current_tags = _tags(current_obj) if current_obj is not None else {}

        if current_node is not None and str(getattr(current_node, "lod_state", "") or "") == "collapsed":
            if "resolve" in current_tags and (max_depth is None or depth < int(max_depth or 0) or current_id == root_id):
                precision = "approximate"
                _warn_once(warnings, "collapsed_subtree")

        if max_depth is not None and depth >= int(max_depth):
            continue

        if graph is None:
            continue
        for child_id in sorted(graph.get_children(current_id)):
            child_node = graph.get_node(child_id)
            child_obj = entity_lifecycle_system.find_runtime_entity(game, child_id)
            if child_id not in visited:
                _add_entity_node(nodes, entity_id=child_id, graph_node=child_node, runtime_obj=child_obj)
                visited.add(child_id)
            if include_relation_edges:
                edges.append(
                    {
                        "id": f"relation:{current_id}->{child_id}",
                        "edge_type": "relation",
                        "src": f"entity:{current_id}",
                        "dst": f"entity:{child_id}",
                        "kind": str(getattr(child_node, "socket_id", None) or "contains"),
                        "tags": {
                            "socket_id": getattr(child_node, "socket_id", None),
                        },
                    }
                )
            if child_id not in {eid for eid, _ in queue}:
                queue.append((child_id, depth + 1))

    if realize_policy == "require":
        runtime_tags = _tags(runtime_obj) if runtime_obj is not None else {}
        if "resolve" in runtime_tags or (runtime_obj is not None and entity_body_system.can_expand_entity(runtime_obj)):
            root_children = graph.get_children(root_id, socket_id="resolve") if graph is not None else []
            if graph is not None:
                root_children = list(root_children) + list(graph.get_children(root_id, socket_id="body"))
            if not root_children and str(getattr(root_node, "lod_state", "") or "") != "expanded":
                precision = "approximate"
                _warn_once(warnings, "exact_geometry_unavailable")
    elif realize_policy in {"forbid", "allow"}:
        runtime_tags = _tags(runtime_obj) if runtime_obj is not None else {}
        if (
            ("resolve" in runtime_tags) or
            (runtime_obj is not None and entity_body_system.can_expand_entity(runtime_obj))
        ) and str(getattr(root_node, "lod_state", "") or "") != "expanded":
            precision = "approximate"
            _warn_once(warnings, "collapsed_subtree")
        if runtime_obj is not None and entity_body_system.can_expand_entity(runtime_obj) and graph is not None:
            if not graph.get_children(root_id, socket_id="body"):
                precision = "approximate"
                _warn_once(warnings, "collapsed_subtree")

    if derive:
        for helper in derive:
            helper_id = str(helper or "").strip()
            if helper_id == "seed_pattern":
                derived_out["seed_pattern"] = query_normalized_pattern(
                    game,
                    root_id,
                    helper_id="seed_pattern",
                    realize_policy=realize_policy,
                )

    nodes.sort(key=lambda item: str(item.get("id", "")))
    edges.sort(key=lambda item: str(item.get("id", "")))

    realization_state = "missing"
    if runtime_obj is not None:
        realization_state = "live"
    elif root_node is not None:
        realization_state = "graph_only"
    if precision != "exact":
        realization_state = "partial" if realization_state != "missing" else realization_state

    return {
        "root_entity_id": root_id,
        "realization_state": realization_state,
        "precision": precision,
        "nodes": nodes,
        "edges": edges,
        "derived": derived_out,
        "warnings": warnings,
    }


def query_normalized_pattern(
    game: object,
    root_entity_id: str,
    *,
    helper_id: str = "seed_pattern",
    realize_policy: str = "forbid",
) -> Dict[str, Any]:
    """Return a normalized pattern helper payload when component geometry exists."""
    geom = query_geometry(
        game,
        root_entity_id,
        include_relation_edges=True,
        derive=(),
        realize_policy=realize_policy,
    )

    warnings = list(geom.get("warnings", []) or [])
    chakra_nodes = [
        node for node in geom.get("nodes", [])
        if node.get("node_type") == "chakra" and isinstance(node.get("abs_pos"), tuple)
    ]
    edge_rows = [
        edge for edge in geom.get("edges", [])
        if edge.get("edge_type") == "chakra"
    ]

    root_id = str(geom.get("derived", {}).get("chakra_root_node_id", "") or "").strip()
    positions: Dict[str, Tuple[float, float]] = {}
    active_nodes: List[str] = []
    for node in chakra_nodes:
        node_id = str(node.get("chakra_node_id", "") or "").strip()
        pos = node.get("abs_pos")
        if not node_id or not isinstance(pos, tuple) or len(pos) < 2:
            continue
        positions[node_id] = (float(pos[0]), float(pos[1]))
        if bool(node.get("active", True)):
            active_nodes.append(node_id)

    compact_edges: List[Tuple[str, str]] = []
    active_set = set(active_nodes)
    for edge in edge_rows:
        src_node_id = str(edge.get("src_node_id", "") or "").strip()
        dst_node_id = str(edge.get("dst_node_id", "") or "").strip()
        if src_node_id in active_set and dst_node_id in active_set:
            compact_edges.append((src_node_id, dst_node_id))

    if not root_id or root_id not in positions:
        _warn_once(warnings, "missing_pattern_root")
    if root_id and root_id not in active_set and root_id in positions:
        active_set.add(root_id)
        if root_id not in active_nodes:
            active_nodes.append(root_id)

    if len(active_set) < 2:
        _warn_once(warnings, "insufficient_active_nodes")
        return {
            "helper_id": str(helper_id or "seed_pattern"),
            "root_entity_id": str(root_entity_id or ""),
            "realization_state": geom.get("realization_state", "missing"),
            "precision": geom.get("precision", "approximate"),
            "positions": positions,
            "compact_edges": compact_edges,
            "root_id": root_id,
            "terminus_id": "",
            "verts": [],
            "edges": [],
            "node_order": [],
            "base_len": 0.0,
            "warnings": warnings,
        }

    rx, ry = positions[root_id]
    candidates = [nid for nid in active_set if nid != root_id and nid in positions]
    terminus_id = max(
        candidates,
        key=lambda nid: (positions[nid][0] - rx) ** 2 + (positions[nid][1] - ry) ** 2,
    )

    try:
        from edgecaster.systems import chakras as chakra_system

        verts, edges, node_order, base_len = chakra_system.normalized_custom_graph_from_positions(
            {nid: positions[nid] for nid in active_set if nid in positions},
            compact_edges,
            root_id=root_id,
            terminus_id=terminus_id,
        )
    except Exception:
        _warn_once(warnings, "pattern_normalization_failed")
        return {
            "helper_id": str(helper_id or "seed_pattern"),
            "root_entity_id": str(root_entity_id or ""),
            "realization_state": geom.get("realization_state", "missing"),
            "precision": "approximate",
            "positions": positions,
            "compact_edges": compact_edges,
            "root_id": root_id,
            "terminus_id": terminus_id,
            "verts": [],
            "edges": [],
            "node_order": [],
            "base_len": 0.0,
            "warnings": warnings,
        }

    return {
        "helper_id": str(helper_id or "seed_pattern"),
        "root_entity_id": str(root_entity_id or ""),
        "realization_state": geom.get("realization_state", "missing"),
        "precision": geom.get("precision", "exact"),
        "positions": {nid: positions[nid] for nid in node_order if nid in positions},
        "compact_edges": compact_edges,
        "root_id": root_id,
        "terminus_id": terminus_id,
        "verts": verts,
        "edges": edges,
        "node_order": node_order,
        "base_len": base_len,
        "warnings": warnings,
    }
