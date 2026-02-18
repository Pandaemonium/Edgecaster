"""Entity graph operations for containment edges.

Phase-1 scope:
- Attach/detach/reparent runtime entities using parent/socket metadata.
- Keep inventory semantics compatible with existing UI/content expectations.
- Deterministic stack splitting via shared graph operation.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from edgecaster.systems import entity_identity as entity_identity_system


def _ensure_tags(ent: Any) -> dict:
    tags = getattr(ent, "tags", None)
    if not isinstance(tags, dict):
        tags = {}
    return tags


def _patch_entity_state(game: Any, entity_or_id: Any, **fields: Any) -> None:
    try:
        patch = getattr(game, "patch_entity_state", None)
        if callable(patch):
            patch(entity_or_id, **fields)
    except Exception:
        pass


def attach_entity_to_parent(
    game: Any,
    ent: Any,
    parent_id: str,
    *,
    socket_id: str = "inventory",
    inventory_socket: str = "inventory",
) -> None:
    """Attach an entity to a parent node using runtime containment metadata."""
    pid = str(parent_id)
    sid = str(socket_id or inventory_socket)

    try:
        setattr(ent, "parent_entity_id", pid)
    except Exception:
        pass
    try:
        setattr(ent, "socket_id", sid)
    except Exception:
        pass

    tags = _ensure_tags(ent)
    if sid == inventory_socket:
        tags["inventory_owner_id"] = pid
        tags["in_inventory"] = True
    else:
        tags.pop("inventory_owner_id", None)
        tags["in_inventory"] = False
    try:
        ent.tags = tags
    except Exception:
        pass

    _patch_entity_state(
        game,
        ent,
        owner_id=pid,
        parent_entity_id=pid,
        socket_id=sid,
        in_inventory=bool(sid == inventory_socket),
        removed=False,
    )


def detach_entity_from_parent(
    game: Any,
    ent: Any,
    *,
    inventory_socket: str = "inventory",
) -> None:
    """Detach an entity from its current parent/socket metadata."""
    try:
        setattr(ent, "parent_entity_id", None)
    except Exception:
        pass
    try:
        setattr(ent, "socket_id", None)
    except Exception:
        pass

    tags = _ensure_tags(ent)
    tags.pop("inventory_owner_id", None)
    tags.pop("in_inventory", None)
    try:
        ent.tags = tags
    except Exception:
        pass

    _patch_entity_state(
        game,
        ent,
        owner_id=None,
        parent_entity_id=None,
        socket_id=None,
        in_inventory=False,
    )


def reparent_entity(
    game: Any,
    ent: Any,
    *,
    parent_id: Optional[str],
    socket_id: str = "inventory",
    inventory_socket: str = "inventory",
) -> None:
    """Move an entity to a new parent in one operation."""
    if parent_id is None:
        detach_entity_from_parent(game, ent, inventory_socket=inventory_socket)
        return
    attach_entity_to_parent(
        game,
        ent,
        str(parent_id),
        socket_id=socket_id,
        inventory_socket=inventory_socket,
    )


def _set_quantity(ent: Any, qty: int) -> None:
    q = max(1, int(qty))
    tags = _ensure_tags(ent)
    tags["quantity"] = q
    try:
        ent.tags = tags
    except Exception:
        pass


def _source_item_id(item: Any) -> str:
    try:
        eid = str(getattr(item, "entity_id", "") or "").strip()
    except Exception:
        eid = ""
    if eid:
        return eid
    try:
        rid = str(getattr(item, "id", "") or "").strip()
    except Exception:
        rid = ""
    return rid or "unknown_source"


def _source_item_proto(item: Any) -> str:
    try:
        proto = str(getattr(item, "proto_id", "") or "").strip()
    except Exception:
        proto = ""
    if proto:
        return proto
    try:
        tags = getattr(item, "tags", {}) or {}
        proto = str(tags.get("item_type", "") or "").strip()
    except Exception:
        proto = ""
    if proto:
        return proto
    try:
        return str(getattr(item, "name", "item") or "item")
    except Exception:
        return "item"


def _safe_zone_coord(game: Any) -> tuple[int, int, int]:
    try:
        zc = getattr(game, "zone_coord", (0, 0, 0))
        if isinstance(zc, (list, tuple)) and len(zc) >= 3:
            return (int(zc[0]), int(zc[1]), int(zc[2]))
    except Exception:
        pass
    return (0, 0, 0)


def _to_abs_pos(game: Any, local_pos: tuple[int, int], *, item: Any = None) -> tuple[int, int]:
    lp = (int(local_pos[0]), int(local_pos[1]))
    zc = _safe_zone_coord(game)
    try:
        fn = getattr(game, "abs_from_zone_local", None)
        if callable(fn):
            ap = fn(zc, lp)
            if isinstance(ap, (list, tuple)) and len(ap) >= 2:
                return (int(ap[0]), int(ap[1]))
    except Exception:
        pass
    try:
        ap = getattr(item, "abs_pos", None)
        if isinstance(ap, (list, tuple)) and len(ap) >= 2:
            return (int(ap[0]), int(ap[1]))
    except Exception:
        pass
    return lp


def _next_split_seq(item: Any) -> int:
    tags = _ensure_tags(item)
    try:
        seq = int(tags.get("_split_seq", 0) or 0)
    except Exception:
        seq = 0
    seq = max(0, seq) + 1
    tags["_split_seq"] = int(seq)
    try:
        item.tags = tags
    except Exception:
        pass
    return int(seq)


def _deterministic_split_identity(
    game: Any,
    source_item: Any,
    *,
    split_kind: str,
    qty: int,
    local_pos: tuple[int, int],
) -> tuple[str, int]:
    seq = _next_split_seq(source_item)
    proto = _source_item_proto(source_item)
    src_id = _source_item_id(source_item)
    abs_pos = _to_abs_pos(game, local_pos, item=source_item)
    recipe = f"split:{split_kind}:{proto}:{src_id}:qty:{int(qty)}"
    sid = entity_identity_system.deterministic_runtime_id(
        game,
        recipe_id=recipe,
        abs_pos=abs_pos,
        namespace="split",
        salt=str(seq),
    )
    return (sid, seq)


def _apply_split_identity(clone: Any, *, split_id: str, source_item: Any, split_kind: str, split_seq: int) -> None:
    try:
        clone.id = str(split_id)
    except Exception:
        pass
    try:
        clone.entity_id = str(split_id)
    except Exception:
        pass
    try:
        if not getattr(clone, "semantic_id", None):
            clone.semantic_id = None
    except Exception:
        pass

    tags = _ensure_tags(clone)
    tags["split_from_entity_id"] = _source_item_id(source_item)
    tags["split_kind"] = str(split_kind or "split")
    tags["split_seq"] = int(split_seq)
    try:
        clone.tags = tags
    except Exception:
        pass


def split_stack_entity(
    game: Any,
    item: Any,
    qty: int,
    *,
    split_kind: str,
    spawn_pos: Optional[Tuple[int, int]] = None,
    clear_equipped_tags: bool = False,
) -> Any:
    """Create a deterministic split-child entity for stack operations."""
    from edgecaster.state.entities import Entity
    from edgecaster.systems.spawning import spawn_entity_from_template

    local_pos = tuple(map(int, spawn_pos)) if spawn_pos is not None else tuple(getattr(item, "pos", (0, 0)))
    local_pos = (int(local_pos[0]), int(local_pos[1]))
    split_kind = str(split_kind or "split")

    split_id, split_seq = _deterministic_split_identity(
        game,
        item,
        split_kind=split_kind,
        qty=int(qty),
        local_pos=local_pos,
    )

    proto_id = getattr(item, "proto_id", None)
    if proto_id is None:
        tags = getattr(item, "tags", {}) or {}
        proto_id = tags.get("item_type")

    recipe = f"split:{split_kind}:{_source_item_proto(item)}:{_source_item_id(item)}:qty:{int(qty)}"

    clone = None
    if proto_id:
        try:
            clone = spawn_entity_from_template(
                game,
                str(proto_id),
                local_pos,
                deterministic=True,
                deterministic_recipe=recipe,
                deterministic_namespace="split",
                deterministic_salt=str(split_seq),
                zone_coord=_safe_zone_coord(game),
            )
        except TypeError:
            clone = spawn_entity_from_template(game, str(proto_id), local_pos)
            _apply_split_identity(
                clone,
                split_id=split_id,
                source_item=item,
                split_kind=split_kind,
                split_seq=split_seq,
            )
        except Exception:
            clone = None

    if clone is None:
        clone = Entity(
            id=str(split_id),
            name=getattr(item, "name", "item"),
            pos=local_pos,
            glyph=getattr(item, "glyph", "?"),
            color=getattr(item, "color", (255, 255, 255)),
            kind=getattr(item, "kind", "item"),
            entity_id=str(split_id),
        )
        clone.tags = dict(getattr(item, "tags", {}) or {})

    try:
        clone.tags = dict(getattr(item, "tags", {}) or {})
    except Exception:
        pass

    _set_quantity(clone, int(qty))

    if clear_equipped_tags:
        clone_tags = getattr(clone, "tags", {}) or {}
        clone_tags.pop("equipped_slot", None)
        clone_tags.pop("equipped", None)
        clone.tags = clone_tags

    _apply_split_identity(
        clone,
        split_id=split_id,
        source_item=item,
        split_kind=split_kind,
        split_seq=split_seq,
    )
    return clone


def transfer_inventory_entity(
    game: Any,
    ent: Any,
    *,
    src_inventory: list,
    dst_inventory: list,
    dst_owner_id: str,
    socket_id: str = "inventory",
) -> None:
    """Move an entity reference between inventories and reparent it."""
    try:
        if ent in src_inventory:
            src_inventory.remove(ent)
    except Exception:
        pass
    try:
        dst_inventory.append(ent)
    except Exception:
        pass
    attach_entity_to_parent(game, ent, str(dst_owner_id), socket_id=socket_id)


def transfer_split_quantity(
    game: Any,
    source_item: Any,
    *,
    qty: int,
    split_kind: str,
    dst_inventory: list,
    dst_owner_id: str,
    spawn_pos: Optional[Tuple[int, int]] = None,
    clear_equipped_tags: bool = True,
    socket_id: str = "inventory",
) -> Any:
    """Split quantity from source and transfer new split-child into destination."""
    child = split_stack_entity(
        game,
        source_item,
        int(qty),
        split_kind=str(split_kind or "split"),
        spawn_pos=spawn_pos,
        clear_equipped_tags=bool(clear_equipped_tags),
    )
    try:
        dst_inventory.append(child)
    except Exception:
        pass
    attach_entity_to_parent(game, child, str(dst_owner_id), socket_id=socket_id)
    return child
