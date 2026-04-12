"""Actor/body hierarchy expansion helpers.

This module turns resolved body-schema data into deterministic entity-tree
children. It is a migration bridge: the old body-schema readers still exist,
but new lifecycle/query code can now ask for real entity children instead of
re-walking schema data ad hoc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from edgecaster import prototypes


@dataclass(frozen=True)
class BodyNodeSpec:
    owner_entity_id: str
    full_id: str
    parent_full_id: Optional[str]
    local_node_id: str
    node_proto_id: str
    schema_proto_id: str
    abs_pos: Tuple[float, float]
    local_scale: float
    mirrored: bool = False
    is_schema_root: bool = False

    @property
    def entity_id(self) -> str:
        return f"{self.owner_entity_id}:body:{self.full_id}"


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


def _node_dict(body_schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(body_schema, dict):
        return {}
    body = body_schema.get("body", body_schema)
    if not isinstance(body, dict):
        body = body_schema
    nodes = body.get("nodes", {}) if isinstance(body, dict) else {}
    return dict(nodes) if isinstance(nodes, dict) else {}


def _root_node_id(body_schema: Dict[str, Any]) -> Optional[str]:
    if not isinstance(body_schema, dict):
        return None
    body = body_schema.get("body", body_schema)
    if not isinstance(body, dict):
        body = body_schema
    raw = body.get("root")
    try:
        value = str(raw or "").strip()
    except Exception:
        value = ""
    return value or None


def _node_children(node: Dict[str, Any]) -> List[str]:
    raw = node.get("children", [])
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for child in raw:
        try:
            value = str(child or "").strip()
        except Exception:
            value = ""
        if value:
            out.append(value)
    return out


def _node_layout(node: Dict[str, Any]) -> Tuple[float, float]:
    layout = node.get("layout", {})
    if not isinstance(layout, dict):
        return (0.0, 0.0)
    try:
        x = float(layout.get("x", 0.0) or 0.0)
    except Exception:
        x = 0.0
    try:
        y = float(layout.get("y", 0.0) or 0.0)
    except Exception:
        y = 0.0
    return (x, y)


def _node_size(node: Dict[str, Any]) -> float:
    props = node.get("props", {})
    if not isinstance(props, dict):
        props = {}
    try:
        size = float(props.get("size", 1.0) or 1.0)
    except Exception:
        size = 1.0
    return max(0.01, float(size))


def _local_parent_map(body_schema: Dict[str, Any]) -> Dict[str, str]:
    nodes = _node_dict(body_schema)
    out: Dict[str, str] = {}
    for parent_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        for child_id in _node_children(node):
            out[str(child_id)] = str(parent_id)
    return out


def _base_scale_for_owner(owner_ent: object) -> float:
    for raw in (
        getattr(owner_ent, "body_graph_scale", None),
        _tags(owner_ent).get("body_graph_scale"),
    ):
        if isinstance(raw, (int, float)) and float(raw) > 0.0:
            return float(raw)
    return 1.0


def _is_branch_root(proto_id: str) -> bool:
    """Check whether a prototype introduces its own body schema."""
    if not proto_id:
        return False
    try:
        base_pid = prototypes.base_proto_id(str(proto_id))
        spec = prototypes.get_raw_proto(base_pid)
    except Exception:
        return False
    if not isinstance(spec, dict):
        return False
    body = spec.get("body", {})
    if not isinstance(body, dict):
        return False
    return bool(body.get("root"))


def _body_schema_for_owner(owner_ent: object) -> Dict[str, Any]:
    try:
        return prototypes.resolve_body_schema(owner_ent) or {"root": None, "nodes": {}}
    except Exception:
        return {"root": None, "nodes": {}}


def _schema_proto_for_owner(owner_ent: object) -> str:
    try:
        raw = getattr(owner_ent, "proto_id", None)
    except Exception:
        raw = None
    if raw:
        return str(raw)
    try:
        raw = _tags(owner_ent).get("template_id")
    except Exception:
        raw = None
    return str(raw or "entity")


def _explicit_body_expand_root(ent: object) -> Optional[bool]:
    for raw in (
        getattr(ent, "body_expand_root", None),
        _tags(ent).get("body_expand_root"),
    ):
        if raw is None:
            continue
        return bool(raw)
    return None


def _is_actor_like_root(ent: object) -> bool:
    """Return True for current runtime roots that should body-expand by default.

    Phase 2 actor-body expansion is intentionally limited to actor-like roots
    plus explicit opt-ins. Generic world/site/building entities still carry a
    compatibility `body_schema` copy, but that must not divert resolver-backed
    parents into anatomy expansion.
    """
    try:
        has_faction = hasattr(ent, "faction")
    except Exception:
        has_faction = False
    try:
        has_actions = hasattr(ent, "actions")
    except Exception:
        has_actions = False
    return bool(has_faction and has_actions)


def can_expand_entity(ent: object) -> bool:
    """Return True when *ent* participates in deterministic body expansion."""
    tags = _tags(ent)
    if bool(tags.get("body_node")) and str(tags.get("body_owner_id", "") or "").strip():
        return True
    explicit_root = _explicit_body_expand_root(ent)
    if explicit_root is False:
        return False
    if explicit_root is None and not _is_actor_like_root(ent):
        return False
    try:
        schema = getattr(ent, "body_schema", None)
    except Exception:
        schema = None
    if isinstance(schema, dict) and _node_dict(schema):
        return True
    try:
        resolved = prototypes.resolve_body_schema(ent)
    except Exception:
        resolved = None
    return bool(isinstance(resolved, dict) and _node_dict(resolved))


def body_owner_entity_id(ent: object) -> str:
    tags = _tags(ent)
    try:
        owner_id = str(tags.get("body_owner_id", "") or "").strip()
    except Exception:
        owner_id = ""
    return owner_id or _entity_id(ent)


def body_full_id(ent: object) -> Optional[str]:
    tags = _tags(ent)
    try:
        full_id = str(tags.get("body_full_id", "") or "").strip()
    except Exception:
        full_id = ""
    return full_id or None


def build_body_node_specs(owner_ent: object) -> Dict[str, BodyNodeSpec]:
    """Return full body-node specs for an owner's complete schema graph."""
    owner_id = _entity_id(owner_ent)
    if not owner_id:
        return {}
    schema = _body_schema_for_owner(owner_ent)
    nodes = _node_dict(schema)
    root_id = _root_node_id(schema)
    if not nodes or not root_id:
        return {}

    owner_abs = getattr(owner_ent, "abs_pos", None)
    if not (isinstance(owner_abs, (tuple, list)) and len(owner_abs) >= 2):
        owner_abs = (0.0, 0.0)
    base_scale = _base_scale_for_owner(owner_ent)
    specs: Dict[str, BodyNodeSpec] = {}

    def walk_schema(
        *,
        schema_data: Dict[str, Any],
        schema_proto_id: str,
        prefix: str,
        parent_anchor: Tuple[float, float],
        parent_scale: float,
        branch_parent_full_id: Optional[str],
        recursion_guard: Set[str],
    ) -> None:
        local_nodes = _node_dict(schema_data)
        local_root_id = _root_node_id(schema_data)
        if not local_nodes or not local_root_id:
            return

        parent_map = _local_parent_map(schema_data)
        visited_local: Set[str] = set()

        def walk_node(node_id: str, anchor: Tuple[float, float], scale: float) -> None:
            if node_id in visited_local:
                return
            node = local_nodes.get(node_id)
            if not isinstance(node, dict):
                return
            visited_local.add(node_id)

            layout = _node_layout(node)
            local_size = _node_size(node)
            full_id = f"{prefix}{node_id}" if prefix else str(node_id)

            if node_id == local_root_id:
                parent_full_id = branch_parent_full_id
            else:
                local_parent_id = parent_map.get(str(node_id))
                if local_parent_id:
                    parent_full_id = f"{prefix}{local_parent_id}" if prefix else str(local_parent_id)
                else:
                    parent_full_id = f"{prefix}{local_root_id}" if prefix else str(local_root_id)

            abs_x = float(anchor[0]) + float(layout[0]) * float(scale)
            abs_y = float(anchor[1]) + float(layout[1]) * float(scale)

            proto_id = str(node.get("proto", node_id) or node_id)
            specs[full_id] = BodyNodeSpec(
                owner_entity_id=owner_id,
                full_id=full_id,
                parent_full_id=parent_full_id,
                local_node_id=str(node_id),
                node_proto_id=proto_id,
                schema_proto_id=str(schema_proto_id),
                abs_pos=(float(abs_x), float(abs_y)),
                local_scale=float(scale),
                mirrored=bool(str(proto_id).endswith("__m")),
                is_schema_root=bool(node_id == local_root_id),
            )

            # Expand into this node's own body schema, if it defines one.
            if _is_branch_root(proto_id) and str(proto_id) not in recursion_guard:
                try:
                    sub_schema = prototypes.resolve_body_schema(proto_id)
                except Exception:
                    sub_schema = None
                if isinstance(sub_schema, dict) and _node_dict(sub_schema):
                    walk_schema(
                        schema_data=sub_schema,
                        schema_proto_id=str(prototypes.base_proto_id(proto_id)),
                        prefix=f"{full_id}.",
                        parent_anchor=(float(abs_x), float(abs_y)),
                        parent_scale=float(scale) * float(local_size),
                        branch_parent_full_id=full_id,
                        recursion_guard=set(recursion_guard) | {str(proto_id)},
                    )

            for child_id in _node_children(node):
                walk_node(child_id, (float(abs_x), float(abs_y)), float(scale))

        walk_node(local_root_id, parent_anchor, parent_scale)

        root_full_id = f"{prefix}{local_root_id}" if prefix else str(local_root_id)
        root_spec = specs.get(root_full_id)
        if root_spec is None:
            return
        root_anchor = root_spec.abs_pos
        root_scale = float(root_spec.local_scale)

        for orphan_id in sorted(local_nodes.keys()):
            sid = str(orphan_id)
            if sid == local_root_id or sid in parent_map or sid in visited_local:
                continue
            walk_node(sid, root_anchor, root_scale)

    walk_schema(
        schema_data=schema,
        schema_proto_id=_schema_proto_for_owner(owner_ent),
        prefix="",
        parent_anchor=(float(owner_abs[0]), float(owner_abs[1])),
        parent_scale=float(base_scale),
        branch_parent_full_id=None,
        recursion_guard=set(),
    )
    return specs


def child_specs_for_entity(owner_ent: object, parent_full_id: Optional[str]) -> List[BodyNodeSpec]:
    """Return deterministic direct children for the given body parent."""
    specs = build_body_node_specs(owner_ent)
    want_parent = str(parent_full_id or "").strip() or None
    out = [
        spec for spec in specs.values()
        if (str(spec.parent_full_id or "").strip() or None) == want_parent
    ]
    out.sort(key=lambda spec: str(spec.full_id))
    return out
