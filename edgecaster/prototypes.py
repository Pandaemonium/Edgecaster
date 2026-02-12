from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import copy
import yaml

def _bake_mirrored_nodes_in_schema(schema: dict) -> dict:
    """
    Normalize/complete any *_m nodes inside THIS schema:
      - props.mirrored = True
      - proto rewritten to __m
      - if *_m is missing layout/size, copy from base node and mirror x
    """
    if not isinstance(schema, dict):
        return schema

    schema = _ensure_schema_shape(schema)
    nodes = _node_dict(schema)

    # Pass 1: normalize mirrored flag and rewrite proto to __m when present
    for nid, node in list(nodes.items()):
        if not isinstance(node, dict):
            continue

        nid_str = str(nid)
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        is_m = bool(nid_str.endswith("_m") or props.get("mirrored"))

        if is_m:
            props = dict(props)
            props["mirrored"] = True

            node = dict(node)
            node["props"] = props

            proto = node.get("proto")
            if isinstance(proto, str) and proto:
                node["proto"] = mirrored_proto_id(proto)

            nodes[nid] = node

    # Pass 2: copy layout/size/proto from base node (if base exists)
    for nid, node in list(nodes.items()):
        if not isinstance(node, dict):
            continue

        nid_str = str(nid)
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        is_m = bool(nid_str.endswith("_m") or props.get("mirrored"))
        if not is_m:
            continue

        base_nid = nid_str[:-2] if nid_str.endswith("_m") else None
        if not base_nid or base_nid not in nodes:
            continue

        base_node = nodes.get(base_nid)
        if not isinstance(base_node, dict):
            continue

        # size
        bn_props = base_node.get("props") if isinstance(base_node.get("props"), dict) else {}
        n_props = node.get("props") if isinstance(node.get("props"), dict) else {}
        size = bn_props.get("size")
        if isinstance(size, (int, float)) and not isinstance(n_props.get("size"), (int, float)):
            n_props = dict(n_props)
            n_props["size"] = size
            node["props"] = n_props

        # layout (mirror x, copy y)
        bn_layout = base_node.get("layout") if isinstance(base_node.get("layout"), dict) else {}
        n_layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}

        changed = False
        if "y" in bn_layout and "y" not in n_layout:
            n_layout = dict(n_layout)
            n_layout["y"] = bn_layout.get("y")
            changed = True

        if "x" in bn_layout and isinstance(bn_layout.get("x"), (int, float)):
            n_layout = dict(n_layout)
            n_layout["x"] = -float(bn_layout.get("x"))
            changed = True

        if changed:
            node["layout"] = n_layout

        # proto (if missing/empty)
        proto = node.get("proto")
        if not (isinstance(proto, str) and proto):
            base_proto = base_node.get("proto")
            if isinstance(base_proto, str) and base_proto:
                node["proto"] = mirrored_proto_id(base_proto)

        nodes[nid] = node

    return _validate_body_schema(schema)


def bake_instance_body_schema(pid: str) -> dict:
    """
    Resolve and 'bake' a *root* body schema for a concrete instance:
      - mirrored nodes (id suffix _m or props.mirrored) get their layout/size copied
        from their non-mirrored counterpart with x negated
      - mirrored nodes have their proto rewritten to a mirrored proto id (pid + '__m')
      - if a mirrored node is missing proto, copy it from the base node (then mirror it)

    This does NOT change the global prototype bucket; it's meant to be stored on a
    runtime Entity/Actor instance (e.g., obj.body_schema) so later mutations
    (missing eye, amputations) are possible.
    """
    base_pid = base_proto_id(pid)
    schema = _resolve_body_schema_unmirrored(base_pid)
    schema = copy.deepcopy(schema)
    schema = _ensure_schema_shape(schema)
    nodes = _node_dict(schema)

    # First pass: normalize node dicts and ensure mirrored flag is explicit
    for nid, node in list(nodes.items()):
        if not isinstance(node, dict):
            continue

        nid_str = str(nid)
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        is_m = bool(nid_str.endswith("_m") or props.get("mirrored"))

        if is_m:
            props = dict(props)
            props["mirrored"] = True
            node = dict(node)
            node["props"] = props

            # Only mirrored nodes should redirect to mirrored protos
            proto = node.get("proto")
            if isinstance(proto, str) and proto:
                node["proto"] = mirrored_proto_id(proto)

        nodes[nid] = node

    # Second pass: copy layout/size/proto from counterpart when possible
    for nid, node in list(nodes.items()):
        if not isinstance(node, dict):
            continue

        nid_str = str(nid)
        props = node.get("props") if isinstance(node.get("props"), dict) else {}
        is_m = bool(nid_str.endswith("_m") or props.get("mirrored"))
        if not is_m:
            continue

        base_nid = nid_str[:-2] if nid_str.endswith("_m") else None
        if not base_nid or base_nid not in nodes:
            continue

        base_node = nodes.get(base_nid)
        if not isinstance(base_node, dict):
            continue

        # ---- props.size ----
        bn_props = base_node.get("props") if isinstance(base_node.get("props"), dict) else {}
        n_props = node.get("props") if isinstance(node.get("props"), dict) else {}
        size = bn_props.get("size")
        if isinstance(size, (int, float)):
            if not isinstance(n_props.get("size"), (int, float)) or n_props.get("size") != size:
                n_props = dict(n_props)
                n_props["size"] = size
                node["props"] = n_props

        # ---- layout (x mirrored) ----
        bn_layout = base_node.get("layout") if isinstance(base_node.get("layout"), dict) else {}
        n_layout = node.get("layout") if isinstance(node.get("layout"), dict) else {}

        # copy y
        if "y" in bn_layout:
            n_layout = dict(n_layout)
            n_layout["y"] = bn_layout.get("y")



        # always mirror x from base if base has x
        if "x" in bn_layout and isinstance(bn_layout.get("x"), (int, float)):
            n_layout = dict(n_layout)
            n_layout["x"] = -float(bn_layout.get("x"))
        if n_layout is not node.get("layout"):
            node["layout"] = n_layout

        # ---- proto (critical for zoom!) ----
        proto = node.get("proto")
        if not (isinstance(proto, str) and proto):
            base_proto = base_node.get("proto")
            if isinstance(base_proto, str) and base_proto:
                node["proto"] = mirrored_proto_id(base_proto)

        nodes[nid] = node

    return _validate_body_schema(schema)




# ---------------------------------------------------------------------------
# Prototype loading + resolution
# ---------------------------------------------------------------------------

# Files we treat as top-level "prototype buckets".
# (These are human convenience groupings; we merge them into one ontological pool.)
DEFAULT_BUCKET_FILES: Tuple[str, ...] = (
    "entities.yaml",
    "anatomy.yaml",
    "biology.yaml",
    "enemies.yaml",
    "quests.yaml",
    "site_types.yaml",
)


# Internal caches
_RAW_PROTO_INDEX: Dict[str, dict] = {}
_RESOLVED_CACHE: Dict[str, dict] = {}
# Body schema cache (separate from resolved proto cache)
_RESOLVED_BODY_CACHE: Dict[str, dict] = {}
_LOADED: bool = False


def _content_root() -> Path:
    """
    edgecaster/prototypes.py lives in edgecaster/.
    Content is expected at edgecaster/content/.
    """
    return Path(__file__).resolve().parent / "content"


def _coerce_id(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s or None

def _proto_id_from_obj(obj: Any) -> Optional[str]:
    """
    Extract a prototype id from either:
      - a proto id string
      - a runtime entity instance
      - a dict-like entity/spec

    IMPORTANT: we only accept ids that actually exist in the master bucket.
    This prevents accidentally treating instance ids as prototype ids.
    """
    if obj is None:
        return None

    if not _LOADED:
        load_master_bucket()

    # Already a string-like proto id?
    if isinstance(obj, str):
        pid = _coerce_id(obj)
        if pid and pid in _RAW_PROTO_INDEX:
            return pid
        return None

    # Dict-like
    if isinstance(obj, dict):
        # Prefer explicit proto keys
        for k in ("proto_id", "prototype_id", "proto", "prototype", "kind", "type"):
            pid = _coerce_id(obj.get(k))
            if pid and pid in _RAW_PROTO_INDEX:
                return pid

        # Sometimes the dict itself *is* a raw proto (has id/parent/etc).
        pid = _coerce_id(obj.get("id"))
        if pid and pid in _RAW_PROTO_INDEX:
            return pid

        return None

    # Object attributes
    for attr in ("proto_id", "prototype_id", "proto", "prototype", "kind", "type", "template_id", "spec_id"):
        pid = _coerce_id(getattr(obj, attr, None))
        if pid and pid in _RAW_PROTO_INDEX:
            return pid

    # Some entities keep a spec dict (or resolved proto) attached
    spec = getattr(obj, "spec", None)
    if isinstance(spec, dict):
        pid = _proto_id_from_obj(spec)
        if pid:
            return pid

    data = getattr(obj, "data", None)
    if isinstance(data, dict):
        pid = _proto_id_from_obj(data)
        if pid:
            return pid

    tags = getattr(obj, "tags", None)
    if isinstance(tags, dict):
        pid = _proto_id_from_obj(tags)
        if pid:
            return pid

    return None


def _load_yaml_list(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        return []
    out: List[dict] = []
    for entry in data:
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _normalize_entry(entry: dict) -> dict:
    # Make a shallow copy so we never mutate the YAML-loaded original.
    return dict(entry)


def load_master_bucket(bucket_files: Iterable[str] = DEFAULT_BUCKET_FILES) -> Dict[str, dict]:
    """
    Build (or return cached) master prototype bucket: id -> raw prototype dict.
    """
    global _RAW_PROTO_INDEX, _RESOLVED_CACHE, _RESOLVED_BODY_CACHE, _LOADED

    if _LOADED and _RAW_PROTO_INDEX:
        return _RAW_PROTO_INDEX

    _RAW_PROTO_INDEX = {}
    _RESOLVED_CACHE = {}
    _RESOLVED_BODY_CACHE = {}

    root = _content_root()
    for fname in bucket_files:
        path = root / fname
        for entry in _load_yaml_list(path):
            eid = _coerce_id(entry.get("id"))
            if not eid:
                continue
            # Last writer wins if there is an id collision across files.
            _RAW_PROTO_INDEX[eid] = _normalize_entry(entry)

    _LOADED = True
    return _RAW_PROTO_INDEX


def get_raw_proto(proto_id: str) -> dict:
    """
    Return the raw (unresolved) prototype dict. No inheritance applied.
    """
    if not _LOADED:
        load_master_bucket()
    return _RAW_PROTO_INDEX.get(str(proto_id), {})


def get_master_bucket() -> Dict[str, dict]:
    if not _LOADED:
        load_master_bucket()
    return _RAW_PROTO_INDEX


def _merge_tags(parent_tags: Any, child_tags: Any) -> Any:
    """
    Tags can be dict-like (entities.yaml) or list-like (enemies.yaml).
    We try to "do the sensible thing" without forcing a single schema yet.
    """
    if parent_tags is None:
        return copy.deepcopy(child_tags)
    if child_tags is None:
        return copy.deepcopy(parent_tags)

    # dict + dict => deep-ish merge (child overrides)
    if isinstance(parent_tags, dict) and isinstance(child_tags, dict):
        out = dict(parent_tags)
        out.update(child_tags)
        return out

    # list + list => union preserving order
    if isinstance(parent_tags, list) and isinstance(child_tags, list):
        seen: Set[str] = set()
        out_list: List[Any] = []
        for v in parent_tags + child_tags:
            key = str(v)
            if key in seen:
                continue
            seen.add(key)
            out_list.append(v)
        return out_list

    # Mismatched types => child wins (explicit override)
    return copy.deepcopy(child_tags)


def _deep_merge(parent: Any, child: Any, *, key: Optional[str] = None) -> Any:
    """
    Merge 'child' on top of 'parent' to produce a new value.
    Rules:
      - dict + dict: recurse key-wise
      - list + list: child replaces parent (EXCEPT tags, handled separately)
      - otherwise: child replaces parent
    """
    # Special case: tags
    if key == "tags":
        return _merge_tags(parent, child)

    if isinstance(parent, dict) and isinstance(child, dict):
        out = dict(parent)
        for k, v_child in child.items():
            if k in out:
                out[k] = _deep_merge(out[k], v_child, key=str(k))
            else:
                out[k] = copy.deepcopy(v_child)
        return out

    # Default: child overrides (including lists)
    return copy.deepcopy(child)


def _apply_delete_ops(spec: dict) -> dict:
    """
    If a spec defines `_delete: [...]`, remove those top-level keys after merge.
    (Override behavior for now; we can later support dotted paths / structural patches.)
    """
    delete_list = spec.get("_delete")
    if not delete_list:
        return spec
    if not isinstance(delete_list, list):
        return spec

    out = dict(spec)
    for k in delete_list:
        if not k:
            continue
        out.pop(str(k), None)
    # Keep _delete out of the resolved spec (purely an authoring directive).
    out.pop("_delete", None)
    return out


def resolve_proto(proto_id: str) -> dict:
    """
    Return the fully-resolved prototype dict for proto_id, with parent inheritance applied.
    Caches results.

    Inheritance:
      - walk parent chain until parent is None / missing / cycle
      - merge parent -> child at each step
    """
    if not _LOADED:
        load_master_bucket()

    pid = str(proto_id)
    if pid in _RESOLVED_CACHE:
        return _RESOLVED_CACHE[pid]

    # Build ancestry chain (root first, then down to pid)
    chain: List[str] = []
    visited: Set[str] = set()
    cur: Optional[str] = pid

    while cur and cur not in visited:
        visited.add(cur)
        raw = _RAW_PROTO_INDEX.get(cur)
        if not raw:
            break
        chain.append(cur)
        parent = _coerce_id(raw.get("parent"))
        cur = parent

    # If chain is ["child","parent","grandparent"], reverse to root->leaf
    chain.reverse()

    resolved: dict = {}
    for cid in chain:
        spec = _RAW_PROTO_INDEX.get(cid, {})
        resolved = _deep_merge(resolved, spec)
        resolved = _apply_delete_ops(resolved)

    # Ensure id is correct even after merges
    resolved["id"] = pid

    _RESOLVED_CACHE[pid] = resolved
    return resolved


def resolve_field(proto_id: str, field: str, default: Any = None) -> Any:
    """
    Convenience for "closest ancestor defines this" behavior (by using resolved proto).
    """
    spec = resolve_proto(proto_id)
    return spec.get(field, default)


# ---------------------------------------------------------------------------
# Body schema resolution (pure data; no instantiation)
# ---------------------------------------------------------------------------

def _ancestry_chain(proto_id: str) -> List[str]:
    """Return ancestry from root -> proto_id (inclusive), fail-soft on missing."""
    if not _LOADED:
        load_master_bucket()

    pid = str(proto_id)
    chain: List[str] = []
    visited: Set[str] = set()
    cur: Optional[str] = pid

    while cur and cur not in visited:
        visited.add(cur)
        raw = _RAW_PROTO_INDEX.get(cur)
        if not raw:
            break
        chain.append(cur)
        cur = _coerce_id(raw.get("parent"))

    chain.reverse()
    return chain


def _ensure_schema_shape(schema: Any) -> dict:
    """
    Ensure schema is dict with keys: root(str), nodes(dict), links(list).
    Fail-soft: returns empty schema if malformed.

    NOTE: 'links' are *render-only* edges and may legitimately reference nodes
    not present in this schema (e.g. cross-layer ghost connections).
    """
    if not isinstance(schema, dict):
        return {"root": None, "nodes": {}, "links": []}

    root = schema.get("root")
    nodes = schema.get("nodes")
    links = schema.get("links")

    if not isinstance(nodes, dict):
        nodes = {}

    # links can be list of [a,b] pairs or dicts {from,to}; keep as-is, deep copied.
    if not isinstance(links, list):
        links = []

    out = {
        "root": root if isinstance(root, str) else None,
        "nodes": copy.deepcopy(nodes),
        "links": copy.deepcopy(links),
    }
    return out



def _node_dict(schema: dict) -> Dict[str, dict]:
    nodes = schema.get("nodes")
    if not isinstance(nodes, dict):
        schema["nodes"] = {}
        return schema["nodes"]
    return nodes


def _get_children(node_spec: Any) -> List[str]:
    if not isinstance(node_spec, dict):
        return []
    ch = node_spec.get("children")
    if isinstance(ch, list):
        return [str(x) for x in ch if x is not None]
    return []


def _set_children(node_spec: dict, children: List[str]) -> None:
    node_spec["children"] = list(children)


def _remove_child_references(nodes: Dict[str, dict], doomed: str) -> None:
    for ns in nodes.values():
        if not isinstance(ns, dict):
            continue
        ch = _get_children(ns)
        if doomed in ch:
            _set_children(ns, [c for c in ch if c != doomed])


def _collect_subtree(nodes: Dict[str, dict], root_node: str) -> Set[str]:
    """Collect node + descendants via children edges."""
    out: Set[str] = set()
    stack: List[str] = [root_node]
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out.add(n)
        ns = nodes.get(n)
        if isinstance(ns, dict):
            stack.extend(_get_children(ns))
    return out


def _op_add_node(schema: dict, op: dict) -> None:
    nodes = _node_dict(schema)
    node_key = op.get("node")
    proto = op.get("proto")
    attach_to = op.get("attach_to")

    if not node_key or not proto:
        return

    nk = str(node_key)

    # Create/replace node spec
    new_spec: dict = {}
    new_spec["proto"] = str(proto)

    # Optional instance props (e.g., mirrored: true)
    props = op.get("props")
    if isinstance(props, dict) and props:
        new_spec["props"] = copy.deepcopy(props)

    # Optional layout hint
    layout = op.get("layout")
    if isinstance(layout, dict) and layout:
        new_spec["layout"] = copy.deepcopy(layout)

    # Optional children list
    children = op.get("children")
    if isinstance(children, list):
        new_spec["children"] = [str(x) for x in children if x is not None]
    else:
        new_spec["children"] = []

    nodes[nk] = new_spec

    # Attach as child if requested
    if attach_to:
        parent_key = str(attach_to)
        parent_spec = nodes.get(parent_key)
        if isinstance(parent_spec, dict):
            ch = _get_children(parent_spec)
            if nk not in ch:
                ch.append(nk)
            _set_children(parent_spec, ch)


def _op_delete_node(schema: dict, op: dict) -> None:
    nodes = _node_dict(schema)
    node_key = op.get("node")
    if not node_key:
        return
    nk = str(node_key)
    if nk not in nodes:
        return

    # Cascade delete subtree (node + descendants).
    doomed = _collect_subtree(nodes, nk)

    # Remove references in all children lists.
    for d in doomed:
        _remove_child_references(nodes, d)

    # Delete nodes.
    for d in doomed:
        nodes.pop(d, None)

    # If root was deleted, clear it (fail-soft).
    if schema.get("root") in doomed:
        schema["root"] = None


def _op_replace_node(schema: dict, op: dict) -> None:
    nodes = _node_dict(schema)
    node_key = op.get("node")
    if not node_key:
        return
    nk = str(node_key)
    if nk not in nodes or not isinstance(nodes.get(nk), dict):
        # Fail-soft: replace on missing node does nothing.
        return

    ns = nodes[nk]

    # Overwrite fields if present
    if "proto" in op and op.get("proto"):
        ns["proto"] = str(op["proto"])

    if "props" in op:
        props = op.get("props")
        if isinstance(props, dict):
            ns["props"] = copy.deepcopy(props)
        elif props is None:
            ns.pop("props", None)

    if "layout" in op:
        layout = op.get("layout")
        if isinstance(layout, dict):
            ns["layout"] = copy.deepcopy(layout)
        elif layout is None:
            ns.pop("layout", None)

    if "children" in op:
        children = op.get("children")
        if isinstance(children, list):
            ns["children"] = [str(x) for x in children if x is not None]
        elif children is None:
            ns.pop("children", None)


def _op_set_children(schema: dict, op: dict) -> None:
    nodes = _node_dict(schema)
    node_key = op.get("node")
    children = op.get("children")
    if not node_key or not isinstance(children, list):
        return
    nk = str(node_key)
    ns = nodes.get(nk)
    if not isinstance(ns, dict):
        return
    ns["children"] = [str(x) for x in children if x is not None]


def _apply_body_patch(schema: dict, patch_ops: Any) -> dict:
    """Apply a list of patch ops to schema (mutating a copy)."""
    if not isinstance(patch_ops, list) or not patch_ops:
        return schema

    for raw_op in patch_ops:
        if not isinstance(raw_op, dict):
            continue
        op_name = raw_op.get("op")
        if not op_name:
            continue
        op_name = str(op_name)

        if op_name == "add_node":
            _op_add_node(schema, raw_op)
        elif op_name == "delete_node":
            _op_delete_node(schema, raw_op)
        elif op_name == "replace_node":
            _op_replace_node(schema, raw_op)
        elif op_name == "set_children":
            _op_set_children(schema, raw_op)
        else:
            # Unknown op: ignore for forward-compatibility.
            continue

    return schema


def _validate_body_schema(schema: dict) -> dict:
    """
    Fail-soft validation:
      - ensure nodes dict
      - ensure root exists (or None)
      - remove child refs to missing nodes
    """
    schema = _ensure_schema_shape(schema)
    nodes = _node_dict(schema)

    # Root must exist in nodes if set
    root = schema.get("root")
    if root is not None and str(root) not in nodes:
        schema["root"] = None

    # Remove child refs to missing nodes
    existing = set(nodes.keys())
    for ns in nodes.values():
        if not isinstance(ns, dict):
            continue
        ch = _get_children(ns)
        filtered = [c for c in ch if c in existing]
        if filtered != ch:
            _set_children(ns, filtered)

    return schema



def _resolve_body_schema_unmirrored(pid: str) -> dict:
    """Internal: resolve a body schema for a *non-mirrored* prototype id."""
    if not _LOADED:
        load_master_bucket()

    if not pid or pid not in _RAW_PROTO_INDEX:
        # Fail-soft: unknown prototype -> empty schema
        return {"root": None, "nodes": {}}

    if pid in _RESOLVED_BODY_CACHE:
        return _RESOLVED_BODY_CACHE[pid]

    schema: dict = {"root": None, "nodes": {}}
    chain = _ancestry_chain(pid)

    for cid in chain:
        raw = _RAW_PROTO_INDEX.get(cid, {})
        if not isinstance(raw, dict):
            continue

        if "body" in raw and raw.get("body") is not None:
            schema = _ensure_schema_shape(raw.get("body"))
            schema = _validate_body_schema(schema)

        if "body_patch" in raw and raw.get("body_patch") is not None:
            schema = copy.deepcopy(schema)
            schema = _apply_body_patch(schema, raw.get("body_patch"))
            schema = _validate_body_schema(schema)

    schema = _validate_body_schema(schema)
    schema = _bake_mirrored_nodes_in_schema(schema)
    _RESOLVED_BODY_CACHE[pid] = schema
    return schema


MIRROR_PROTO_SUFFIX = "__m"

def is_mirrored_proto_id(pid: str) -> bool:
    return isinstance(pid, str) and pid.endswith(MIRROR_PROTO_SUFFIX)

def base_proto_id(pid: str) -> str:
    if is_mirrored_proto_id(pid):
        return pid[: -len(MIRROR_PROTO_SUFFIX)]
    return pid

def mirrored_proto_id(pid: str) -> str:
    pid = base_proto_id(pid)
    return f"{pid}{MIRROR_PROTO_SUFFIX}"

def mirror_body_schema(schema: dict) -> dict:
    """
    Produce a fully mirrored copy of a body schema.

    Semantics:
    - Every node becomes ontologically distinct:
        id -> id_m
    - layout.x is negated
    - layout.y and size are inherited unchanged
    - props.mirrored = True is set
    - proto ids are rewritten to mirrored proto ids (__m)

    This guarantees mirrored body parts have unique slot ids
    (e.g. wrist vs wrist_m) and never collide at equip time.
    """
    if not schema:
        return schema

    # Map old node ids -> mirrored node ids
    id_map: dict[str, str] = {}
    for nid in schema.get("nodes", {}):
        if nid.endswith("_m"):
            id_map[nid] = nid
        else:
            id_map[nid] = f"{nid}_m"

    mirrored_nodes: dict[str, dict] = {}

    for old_nid, node in schema.get("nodes", {}).items():
        new_nid = id_map[old_nid]

        new_node = dict(node)

        # Mirror layout
        layout = dict(node.get("layout", {}) or {})
        if "x" in layout:
            layout["x"] = -layout["x"]
        new_node["layout"] = layout

        # Mark as mirrored
        props = dict(node.get("props", {}) or {})
        props["mirrored"] = True
        new_node["props"] = props

        # Rewrite proto id to mirrored proto
        proto = node.get("proto")
        if proto:
            new_node["proto"] = mirrored_proto_id(proto)

        # Rewrite children ids
        children = node.get("children", [])
        if children:
            new_node["children"] = [id_map[c] for c in children]
        else:
            new_node["children"] = []

        mirrored_nodes[new_nid] = new_node

    # Rewrite root
    root = schema.get("root")
    new_root = id_map.get(root, root)
    # Rewrite links (if any). Only rewrite endpoints that are node ids in this schema.
    new_links = []
    links = schema.get("links")
    if isinstance(links, list):
        for ln in links:
            if isinstance(ln, (list, tuple)) and len(ln) >= 2:
                a, b = str(ln[0]), str(ln[1])
                new_links.append([id_map.get(a, a), id_map.get(b, b)])
            elif isinstance(ln, dict):
                a = ln.get("from", ln.get("a"))
                b = ln.get("to", ln.get("b"))
                if a is None or b is None:
                    continue
                a, b = str(a), str(b)
                new_ln = dict(ln)
                if "from" in new_ln or "to" in new_ln:
                    new_ln["from"] = id_map.get(a, a)
                    new_ln["to"] = id_map.get(b, b)
                else:
                    new_ln["a"] = id_map.get(a, a)
                    new_ln["b"] = id_map.get(b, b)
                new_links.append(new_ln)

    return {
        **schema,
        "root": new_root,
        "nodes": mirrored_nodes,
        "links": new_links if new_links else copy.deepcopy(schema.get("links", [])),
    }



def resolve_body_schema(proto_or_obj: Any) -> dict:
    """
    Resolve the attachment-body schema for:
      - a prototype id string, OR
      - a runtime entity object with .proto_id, OR
      - a runtime entity object with an instance override .body_schema.

    Mirrored prototype ids are supported via the suffix '__m' and are derived
    deterministically from the base prototype body schema.
    """
    if not _LOADED:
        load_master_bucket()
    # Allow synthetic mirrored proto ids like "arm__m" even though they are not in the master bucket.
    if isinstance(proto_or_obj, str):
        pid0 = _coerce_id(proto_or_obj)
        if pid0 and is_mirrored_proto_id(pid0):
            if pid0 in _RESOLVED_BODY_CACHE:
                return _RESOLVED_BODY_CACHE[pid0]
            base = base_proto_id(pid0)
            base_schema = resolve_body_schema(base)  # base MUST exist in bucket
            mirrored = mirror_body_schema(base_schema)
            _RESOLVED_BODY_CACHE[pid0] = mirrored
            return mirrored

    # Instance override: if an object already carries a baked schema, use it.
    try:
        inst_schema = getattr(proto_or_obj, "body_schema", None)
        if isinstance(inst_schema, dict) and inst_schema.get("nodes") is not None:
            return _bake_mirrored_nodes_in_schema(_validate_body_schema(inst_schema))
    except Exception:
        pass

    pid = _proto_id_from_obj(proto_or_obj)
    if not pid:
        return {"root": None, "nodes": {}}

    # Mirrored pid handling
    if is_mirrored_proto_id(pid):
        if pid in _RESOLVED_BODY_CACHE:
            return _RESOLVED_BODY_CACHE[pid]
        base_pid = base_proto_id(pid)
        base_schema = _resolve_body_schema_unmirrored(base_pid)
        mirrored_schema = mirror_body_schema(base_schema)
        _RESOLVED_BODY_CACHE[pid] = mirrored_schema
        return mirrored_schema

    return _resolve_body_schema_unmirrored(pid)




def clear_proto_caches() -> None:
    global _RAW_PROTO_INDEX, _RESOLVED_CACHE, _RESOLVED_BODY_CACHE, _LOADED
    _RAW_PROTO_INDEX = {}
    _RESOLVED_CACHE = {}
    _RESOLVED_BODY_CACHE = {}
    _LOADED = False


# ---------------------------------------------------------------------------
# Branch Root Detection (for Chakra System)
# ---------------------------------------------------------------------------
# A "branch root" is a node whose prototype defines its own body schema.
# These nodes act as gating points for the chakra system - you must unlock
# a branch root before you can access any chakras in its sub-schema.
#
# Example: "shoulder" is the root of the "arm" schema, so it gates access
# to all arm chakras (upper_arm, elbow, forearm, hand, etc.)
# ---------------------------------------------------------------------------

def proto_has_body_root(proto_id: str) -> bool:
    """
    Check if a prototype defines a body schema with a root node.

    This identifies "branch roots" - prototypes that introduce a new
    body sub-schema (like arm, hand, finger, leg, foot, etc.)

    Args:
        proto_id: The prototype ID to check

    Returns:
        True if this proto defines body.root (is a sub-schema entry point)

    Examples:
        proto_has_body_root("arm")    -> True  (has body.root = "shoulder")
        proto_has_body_root("hand")   -> True  (has body.root = "wrist")
        proto_has_body_root("elbow")  -> False (no body schema)
        proto_has_body_root("torso")  -> False (is a node, not a schema definer)
    """
    if not proto_id:
        return False

    if not _LOADED:
        load_master_bucket()

    # Handle mirrored protos
    pid = base_proto_id(str(proto_id))

    spec = resolve_proto(pid)
    if not spec:
        return False

    body = spec.get("body", {})
    if not isinstance(body, dict):
        return False

    root = body.get("root")
    return bool(root)


def get_proto_body_root(proto_id: str) -> Optional[str]:
    """
    Get the root node ID of a prototype's body schema, if any.

    Args:
        proto_id: The prototype ID to check

    Returns:
        The root node ID string, or None if this proto has no body schema

    Examples:
        get_proto_body_root("arm")    -> "shoulder"
        get_proto_body_root("hand")   -> "wrist"
        get_proto_body_root("finger") -> "knuckle_1"
        get_proto_body_root("elbow")  -> None
    """
    if not proto_id:
        return None

    if not _LOADED:
        load_master_bucket()

    pid = base_proto_id(str(proto_id))
    spec = resolve_proto(pid)
    if not spec:
        return None

    body = spec.get("body", {})
    if not isinstance(body, dict):
        return None

    root = body.get("root")
    return str(root) if root else None


def get_body_node_info(body_schema: dict, node_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a node in a body schema.

    Args:
        body_schema: The body schema dict (with 'nodes' key)
        node_id: The node ID to look up

    Returns:
        Dict with keys: proto, children, layout (x, y), size, exists
    """
    if not isinstance(body_schema, dict):
        return {"exists": False}

    # Handle nested 'body' key
    body = body_schema.get("body", body_schema)
    nodes = body.get("nodes", {}) if isinstance(body, dict) else {}

    if not isinstance(nodes, dict) or node_id not in nodes:
        return {"exists": False}

    node = nodes[node_id]
    if not isinstance(node, dict):
        return {"exists": False}

    layout = node.get("layout", {})
    if not isinstance(layout, dict):
        layout = {}

    props = node.get("props", {})
    if not isinstance(props, dict):
        props = {}

    children = node.get("children", [])
    if not isinstance(children, list):
        children = []

    return {
        "exists": True,
        "proto": node.get("proto", ""),
        "children": [str(c) for c in children if c],
        "layout": (float(layout.get("x", 0)), float(layout.get("y", 0))),
        "size": float(props.get("size", 0.5)),
    }


def find_parent_in_body_schema(body_schema: dict, target_node_id: str) -> Optional[str]:
    """
    Find the parent node ID for a given node in a body schema.

    Args:
        body_schema: The body schema dict
        target_node_id: The node whose parent we want to find

    Returns:
        Parent node ID, or None if target is root or not found
    """
    if not isinstance(body_schema, dict):
        return None

    body = body_schema.get("body", body_schema)
    nodes = body.get("nodes", {}) if isinstance(body, dict) else {}

    if not isinstance(nodes, dict):
        return None

    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        children = node.get("children", [])
        if isinstance(children, list) and target_node_id in children:
            return str(node_id)

    return None


def get_all_body_nodes(body_schema: dict) -> List[str]:
    """
    Get a list of all node IDs in a body schema.

    Args:
        body_schema: The body schema dict

    Returns:
        List of node ID strings
    """
    if not isinstance(body_schema, dict):
        return []

    body = body_schema.get("body", body_schema)
    nodes = body.get("nodes", {}) if isinstance(body, dict) else {}

    if not isinstance(nodes, dict):
        return []

    return list(nodes.keys())
