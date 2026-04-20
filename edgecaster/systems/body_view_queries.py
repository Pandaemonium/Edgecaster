"""Shared read-only body/entity view queries for scenes and UI helpers.

These helpers give scenes a stable query surface for body-node layout,
zoom-path schema synthesis, and runtime body-node enumeration without forcing
each scene to reimplement graph/schema fallback ladders independently.
"""

from __future__ import annotations

from typing import Any, Optional

from edgecaster.prototypes import resolve_body_schema


def owner_entity_id(owner: object) -> str:
    """Return the stable entity id used by shared body-query helpers."""
    try:
        return str(getattr(owner, "entity_id", "") or getattr(owner, "id", "") or "").strip()
    except Exception:
        return ""


def _schema_node_count(schema: Any) -> int:
    """Return the number of nodes in a schema-shaped mapping."""
    if not isinstance(schema, dict):
        return 0
    nodes = schema.get("nodes", {})
    return len(nodes) if isinstance(nodes, dict) else 0


def _entity_zoom_view_is_usable(
    schema: Any,
    zoom_stack: list[str] | tuple[str, ...],
) -> bool:
    """Return True when an entity-backed schema is rich enough for this zoom depth.

    Requires more than one node at every depth, including depth 0.  A single-node
    entity schema (e.g. the bare "body" root wrapper) should fall through to the
    proto-schema, which shows all body parts flat without requiring the user to
    click through a trivial wrapper node first.
    """
    return _schema_node_count(schema) > 1


def _traverse_entity_zoom_path(
    owner_id: str,
    get_children: Any,
    find_entity: Any,
    game: Any,
    zoom_stack: list[str] | tuple[str, ...],
) -> str | None:
    """Walk the entity graph step-by-step matching local_ids to reach the correct parent.

    Zoom_stack holds local_ids (last segment of body_full_id). Entity IDs are keyed by
    full body_full_id (e.g. "arm", not "body.arm"), so we cannot reconstruct the parent
    entity ID by joining the zoom_stack. Instead we match children by local_id at each
    level, following the actual graph edges.
    """
    current_eid: str = owner_id
    for local_id_wanted in zoom_stack:
        try:
            child_ids = get_children(current_eid, socket_id="body")
        except Exception:
            child_ids = []
        matched: str | None = None
        for cid in child_ids:
            child = find_entity(game, str(cid))
            if child is None:
                continue
            tags = getattr(child, "tags", {}) or {}
            full_id_tag = str(tags.get("body_full_id", "") or "")
            child_local = full_id_tag.rsplit(".", 1)[-1] if "." in full_id_tag else full_id_tag
            if child_local == local_id_wanted:
                matched = str(cid)
                break
        if matched is None:
            return None
        current_eid = matched
    return current_eid


def build_entity_schema_at_zoom_depth(
    owner: object,
    game: Any,
    zoom_stack: list[str] | tuple[str, ...],
) -> dict | None:
    """Build a synthetic body-schema dict from entity-graph data at zoom depth."""
    try:
        from edgecaster.systems import entity_body as entity_body_system
        from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
        from edgecaster.systems.chakras import is_branch_root
    except Exception:
        return None

    owner_id = owner_entity_id(owner)
    if not owner_id:
        return None

    child_rows: list[dict[str, Any]] = []

    graph = getattr(game, "entity_graph", None) if game is not None else None
    get_children = getattr(graph, "get_children", None) if graph is not None else None

    if callable(get_children):
        parent_eid = _traverse_entity_zoom_path(
            owner_id, get_children, entity_lifecycle_system.find_runtime_entity, game, zoom_stack
        )
        if parent_eid is None:
            parent_eid = owner_id if not zoom_stack else None
        if parent_eid is None:
            child_ids = []
        else:
            try:
                child_ids = get_children(parent_eid, socket_id="body")
            except Exception:
                child_ids = []
        for child_id in child_ids:
            child = entity_lifecycle_system.find_runtime_entity(game, str(child_id))
            if child is None:
                continue
            tags = getattr(child, "tags", {}) or {}

            full_id_tag = str(tags.get("body_full_id", "") or "")
            if not full_id_tag:
                continue
            local_id = full_id_tag.rsplit(".", 1)[-1] if "." in full_id_tag else full_id_tag
            if not local_id:
                continue

            try:
                rel = tags.get("body_schema_rel_pos", (0.0, 0.0))
                local_x, local_y = float(rel[0]), float(rel[1])
            except Exception:
                local_x, local_y = 0.0, 0.0

            try:
                size = float(tags.get("body_local_scale", 1.0) or 1.0)
            except Exception:
                size = 1.0

            node_proto_id = str(tags.get("body_node_proto_id", "") or "")
            schema_proto_id = str(tags.get("body_schema_proto_id", "") or "")
            proto_for_zoom = schema_proto_id if schema_proto_id and is_branch_root(node_proto_id) else None

            child_rows.append(
                {
                    "local_id": local_id,
                    "layout": {"x": local_x, "y": local_y},
                    "props": {"size": size},
                    "proto": proto_for_zoom,
                    "zoomable": bool(proto_for_zoom),
                    "is_schema_root": bool(tags.get("body_schema_root", False)),
                }
            )

    if not child_rows:
        try:
            spec_map = entity_body_system.build_body_node_specs(owner)
        except Exception:
            spec_map = {}
        if not spec_map:
            return None

        # Resolve the actual body_full_id of the target entity.  Zoom_stack holds
        # local_ids (last segment of full_id), so we can't reconstruct the full_id
        # by joining (e.g. "body.arm" != "arm").  Try two strategies in order:
        #   1. Walk the entity graph via local_id matching (fast when expanded).
        #   2. Walk the spec_map by local_id matching (works before lazy expansion).
        actual_parent_full_id: Optional[str] = None
        if zoom_stack:
            # Strategy 1: entity graph traversal
            if callable(get_children):
                parent_eid = _traverse_entity_zoom_path(
                    owner_id, get_children, entity_lifecycle_system.find_runtime_entity, game, zoom_stack
                )
                if parent_eid is not None:
                    parent_ent = entity_lifecycle_system.find_runtime_entity(game, parent_eid)
                    if parent_ent is not None:
                        tags = getattr(parent_ent, "tags", {}) or {}
                        actual_parent_full_id = str(tags.get("body_full_id", "") or "") or None

            # Strategy 2: spec_map traversal (handles not-yet-expanded entity graph)
            if actual_parent_full_id is None:
                cur_parent: Optional[str] = None
                for local_id in zoom_stack:
                    matched_full_id: Optional[str] = None
                    for full_id, spec in spec_map.items():
                        spec_local = full_id.rsplit(".", 1)[-1] if "." in full_id else full_id
                        spec_parent = str(spec.parent_full_id or "").strip() or None
                        if spec_local == local_id and spec_parent == cur_parent:
                            matched_full_id = full_id
                            break
                    if matched_full_id is None:
                        break
                    cur_parent = matched_full_id
                actual_parent_full_id = cur_parent

            if actual_parent_full_id is None:
                return None

        if actual_parent_full_id:
            parent_spec = spec_map.get(actual_parent_full_id)
            if parent_spec is None:
                return None
            try:
                parent_anchor = (float(parent_spec.abs_pos[0]), float(parent_spec.abs_pos[1]))
            except Exception:
                parent_anchor = (0.0, 0.0)
        else:
            owner_abs = getattr(owner, "abs_pos", None)
            if isinstance(owner_abs, (tuple, list)) and len(owner_abs) >= 2:
                parent_anchor = (float(owner_abs[0]), float(owner_abs[1]))
            else:
                parent_anchor = (0.0, 0.0)

        try:
            child_specs = entity_body_system.child_specs_for_entity(owner, actual_parent_full_id)
        except Exception:
            child_specs = []
        if not child_specs:
            return None

        for spec in child_specs:
            try:
                local_x = float(spec.abs_pos[0]) - float(parent_anchor[0])
                local_y = float(spec.abs_pos[1]) - float(parent_anchor[1])
            except Exception:
                local_x, local_y = 0.0, 0.0

            proto_for_zoom = (
                str(spec.schema_proto_id or "")
                if str(spec.schema_proto_id or "") and is_branch_root(str(spec.node_proto_id or ""))
                else None
            )
            child_rows.append(
                {
                    "local_id": str(spec.local_node_id or ""),
                    "layout": {"x": local_x, "y": local_y},
                    "props": {"size": float(spec.local_scale or 1.0)},
                    "proto": proto_for_zoom,
                    "zoomable": bool(proto_for_zoom),
                    "is_schema_root": bool(spec.is_schema_root),
                }
            )

    nodes: dict[str, dict] = {}
    root_local_id: Optional[str] = None
    for row in child_rows:
        local_id = str(row.get("local_id", "") or "")
        if not local_id:
            continue
        nodes[local_id] = {
            "layout": dict(row.get("layout") or {}),
            "props": dict(row.get("props") or {}),
            "proto": row.get("proto"),
            "zoomable": bool(row.get("zoomable")),
        }
        if bool(row.get("is_schema_root")):
            root_local_id = local_id

    if not nodes:
        return None
    if root_local_id is None:
        root_local_id = next(iter(nodes))
    return {"root": root_local_id, "nodes": nodes}


def entity_body_view_for_zoom_path(
    owner: object,
    game: Any,
    zoom_stack: list[str],
) -> tuple[dict, tuple[float, float], float] | None:
    """Return entity-backed `(schema, offset, scale)` for a zoom path."""
    offset_x, offset_y = 0.0, 0.0
    scale = 1.0

    for index, node_id in enumerate(zoom_stack):
        schema_here = build_entity_schema_at_zoom_depth(owner, game, zoom_stack[:index])
        if not schema_here:
            return None
        node = (schema_here.get("nodes", {}) or {}).get(str(node_id))
        if not isinstance(node, dict):
            return None

        layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}
        try:
            node_x = float(layout.get("x", 0.0) or 0.0)
            node_y = float(layout.get("y", 0.0) or 0.0)
        except Exception:
            node_x, node_y = 0.0, 0.0

        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        try:
            size = float(props.get("size", 1.0) or 1.0)
        except Exception:
            size = 1.0
        if size <= 0.0:
            size = 1.0

        offset_x += scale * node_x
        offset_y += scale * node_y
        scale *= size

    final_schema = build_entity_schema_at_zoom_depth(owner, game, zoom_stack)
    if not final_schema:
        return None
    return final_schema, (float(offset_x), float(offset_y)), float(scale)


def entity_body_view_chain_for_zoom_path(
    owner: object,
    game: Any,
    zoom_stack: list[str],
) -> list[tuple[dict, tuple[float, float], float]] | None:
    """Return root->active entity-backed zoom chain for a body view."""
    root_schema = build_entity_schema_at_zoom_depth(owner, game, [])
    if not root_schema:
        return None

    chain: list[tuple[dict, tuple[float, float], float]] = [(root_schema, (0.0, 0.0), 1.0)]
    offset_x, offset_y = 0.0, 0.0
    scale = 1.0

    for index, node_id in enumerate(zoom_stack):
        schema_here = build_entity_schema_at_zoom_depth(owner, game, zoom_stack[:index])
        if not schema_here:
            return chain
        node = (schema_here.get("nodes", {}) or {}).get(str(node_id))
        if not isinstance(node, dict):
            return chain

        layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}
        try:
            node_x = float(layout.get("x", 0.0) or 0.0)
            node_y = float(layout.get("y", 0.0) or 0.0)
        except Exception:
            node_x, node_y = 0.0, 0.0

        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        try:
            size = float(props.get("size", 1.0) or 1.0)
        except Exception:
            size = 1.0
        if size <= 0.0:
            size = 1.0

        offset_x += scale * node_x
        offset_y += scale * node_y
        scale *= size

        next_schema = build_entity_schema_at_zoom_depth(owner, game, zoom_stack[: index + 1])
        if not next_schema:
            return chain
        chain.append((next_schema, (float(offset_x), float(offset_y)), float(scale)))

    return chain


def resolve_body_schema_for_zoom_path(
    owner: object | None,
    zoom_stack: list[str] | tuple[str, ...],
    *,
    game: Any = None,
) -> dict:
    """Resolve the schema currently viewed by a body zoom stack."""
    if game is not None and owner is not None:
        entity_schema = build_entity_schema_at_zoom_depth(
            owner,
            game,
            list(zoom_stack) if zoom_stack else [],
        )
        if entity_schema is not None:
            return entity_schema

    try:
        schema = resolve_body_schema(owner) if owner is not None else {"root": None, "nodes": {}}
    except Exception:
        schema = {"root": None, "nodes": {}}

    zoom_ids = list(zoom_stack) if zoom_stack else []
    for node_id in zoom_ids:
        nodes = schema.get("nodes", {}) or {}
        node = nodes.get(str(node_id))
        if not isinstance(node, dict):
            break
        proto = node.get("proto")
        if not proto:
            break
        try:
            schema = resolve_body_schema(proto) or {"root": None, "nodes": {}}
        except Exception:
            schema = {"root": None, "nodes": {}}
            break

    if not isinstance(schema, dict):
        schema = {"root": None, "nodes": {}}
    if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
        schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}
    return schema


def resolve_body_view_for_zoom_path(
    owner: object | None,
    zoom_stack: list[str] | tuple[str, ...],
    *,
    game: Any = None,
) -> tuple[dict, tuple[float, float], float]:
    """Resolve `(schema, embed_offset, embed_scale)` for the current zoom path."""
    if game is not None and owner is not None:
        entity_result = entity_body_view_for_zoom_path(owner, game, list(zoom_stack) if zoom_stack else [])
        if entity_result is not None and _entity_zoom_view_is_usable(entity_result[0], zoom_stack):
            return entity_result

    try:
        schema = resolve_body_schema(owner) if owner is not None else {"root": None, "nodes": {}}
    except Exception:
        schema = {"root": None, "nodes": {}}

    offset_x, offset_y = 0.0, 0.0
    scale = 1.0

    zoom_ids = list(zoom_stack) if zoom_stack else []
    for node_id in zoom_ids:
        nodes = schema.get("nodes", {}) or {}
        node = nodes.get(str(node_id))
        if not isinstance(node, dict):
            break

        layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}
        try:
            node_x = float(layout.get("x", 0.0) or 0.0)
            node_y = float(layout.get("y", 0.0) or 0.0)
        except Exception:
            node_x, node_y = 0.0, 0.0

        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        try:
            size = float(props.get("size", 1.0) or 1.0)
        except Exception:
            size = 1.0
        if size <= 0.0:
            size = 1.0

        offset_x += scale * node_x
        offset_y += scale * node_y
        scale *= size

        proto = node.get("proto")
        if not proto:
            break
        try:
            schema = resolve_body_schema(proto) or {"root": None, "nodes": {}}
        except Exception:
            schema = {"root": None, "nodes": {}}
            break

    if not isinstance(schema, dict):
        schema = {"root": None, "nodes": {}}
    if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
        schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}

    return schema, (float(offset_x), float(offset_y)), float(scale)


def resolve_body_view_chain_for_zoom_path(
    owner: object | None,
    zoom_stack: list[str] | tuple[str, ...],
    *,
    game: Any = None,
) -> list[tuple[dict, tuple[float, float], float]]:
    """Resolve the root->active embedded schema chain for a body zoom stack."""
    if game is not None and owner is not None:
        entity_chain = entity_body_view_chain_for_zoom_path(owner, game, list(zoom_stack) if zoom_stack else [])
        expected_len = len(list(zoom_stack) if zoom_stack else []) + 1
        if (
            entity_chain is not None
            and len(entity_chain) >= expected_len
            and _entity_zoom_view_is_usable(entity_chain[-1][0], zoom_stack)
        ):
            return entity_chain

    try:
        schema = resolve_body_schema(owner) if owner is not None else {"root": None, "nodes": {}}
    except Exception:
        schema = {"root": None, "nodes": {}}

    if not isinstance(schema, dict):
        schema = {"root": None, "nodes": {}}
    if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
        schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}

    offset_x = 0.0
    offset_y = 0.0
    scale = 1.0
    chain: list[tuple[dict, tuple[float, float], float]] = [(schema, (offset_x, offset_y), scale)]

    zoom_ids = list(zoom_stack) if zoom_stack else []
    for node_id in zoom_ids:
        nodes = schema.get("nodes", {}) or {}
        node = nodes.get(str(node_id))
        if not isinstance(node, dict):
            break

        layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}
        try:
            node_x = float(layout.get("x", 0.0) or 0.0)
            node_y = float(layout.get("y", 0.0) or 0.0)
        except Exception:
            node_x, node_y = 0.0, 0.0

        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        try:
            size = float(props.get("size", 1.0) or 1.0)
        except Exception:
            size = 1.0
        if size <= 0.0:
            size = 1.0

        offset_x += scale * node_x
        offset_y += scale * node_y
        scale *= size

        proto = node.get("proto")
        if not proto:
            break
        try:
            schema = resolve_body_schema(proto) or {"root": None, "nodes": {}}
        except Exception:
            schema = {"root": None, "nodes": {}}
            break

        if not isinstance(schema, dict):
            schema = {"root": None, "nodes": {}}
        if "nodes" not in schema or not isinstance(schema.get("nodes"), dict):
            schema = {"root": schema.get("root") if isinstance(schema.get("root"), str) else None, "nodes": {}}

        chain.append((schema, (float(offset_x), float(offset_y)), float(scale)))

    return chain


def body_nodes_for_owner(game: Any, owner: Any) -> list[dict[str, Any]]:
    """Return realized body-node rows for an owner, or deterministic specs."""
    from edgecaster.systems import entity_body as entity_body_system
    from edgecaster.systems import entity_lifecycle as entity_lifecycle_system

    result: list[dict[str, Any]] = []
    owner_id = owner_entity_id(owner)
    if not owner_id:
        return result

    graph = getattr(game, "entity_graph", None) if game is not None else None
    if graph is not None:
        queue: list[str] = [owner_id]
        visited: set[str] = set()
        while queue:
            parent_entity_id = queue.pop(0)
            try:
                child_ids = graph.get_children(parent_entity_id, socket_id="body")
            except Exception:
                child_ids = []
            for child_id in child_ids:
                child_id = str(child_id)
                if child_id in visited:
                    continue
                visited.add(child_id)
                queue.append(child_id)

                obj = entity_lifecycle_system.find_runtime_entity(game, child_id)
                if obj is None:
                    continue
                tags = getattr(obj, "tags", None) or {}
                if not isinstance(tags, dict) or not tags.get("body_node"):
                    continue
                full_id = str(tags.get("body_full_id", "") or "").strip()
                if not full_id:
                    continue

                raw_rel = tags.get("body_schema_rel_pos", (0.0, 0.0))
                try:
                    schema_rel_pos = (float(raw_rel[0]), float(raw_rel[1]))
                except Exception:
                    schema_rel_pos = (0.0, 0.0)

                result.append(
                    {
                        "full_id": full_id,
                        "parent_full_id": str(tags.get("body_parent_full_id", "") or "").strip() or None,
                        "schema_rel_pos": schema_rel_pos,
                        "local_scale": float(tags.get("body_local_scale", 1.0) or 1.0),
                        "node_proto_id": str(tags.get("body_node_proto_id", "") or ""),
                        "schema_proto_id": str(tags.get("body_schema_proto_id", "") or ""),
                        "is_schema_root": bool(tags.get("body_schema_root", False)),
                    }
                )

    if result:
        result.sort(key=lambda row: str(row.get("full_id", "")))
        return result

    owner_abs = getattr(owner, "abs_pos", None)
    if not (isinstance(owner_abs, (tuple, list)) and len(owner_abs) >= 2):
        owner_abs = (0.0, 0.0)

    try:
        specs = entity_body_system.build_body_node_specs(owner)
    except Exception:
        specs = {}

    for full_id in sorted(specs.keys()):
        spec = specs.get(full_id)
        if spec is None:
            continue
        try:
            rel_x = float(spec.abs_pos[0]) - float(owner_abs[0])
            rel_y = float(spec.abs_pos[1]) - float(owner_abs[1])
        except Exception:
            rel_x, rel_y = 0.0, 0.0

        result.append(
            {
                "full_id": str(spec.full_id),
                "parent_full_id": str(spec.parent_full_id or "").strip() or None,
                "schema_rel_pos": (rel_x, rel_y),
                "local_scale": float(spec.local_scale),
                "node_proto_id": str(spec.node_proto_id or ""),
                "schema_proto_id": str(spec.schema_proto_id or ""),
                "is_schema_root": bool(spec.is_schema_root),
            }
        )

    return result
