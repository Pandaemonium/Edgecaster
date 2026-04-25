"""
Inventory System - Container logic and item manipulation.

This module manages:
- Inventory access and creation
- Picking up and dropping items
- Moving items between inventories
- Equipment tagging (equip/unequip slots)
- Eating/consuming items

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional, TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state.entities import Entity
    from edgecaster.state import Actor

from edgecaster.systems import equipment as equipment_system
from edgecaster.systems import item_grants
from edgecaster.systems import equip_rules
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_lifecycle as entity_lifecycle_system


# ---------------------------------------------------------------------------
# Item Stacking
# ---------------------------------------------------------------------------

STACK_MAX = 999


def _stack_key(item: Any) -> Optional[str]:
    """Return a key for stacking, or None if item cannot stack.

    Items stack by default. Use tags["not_stackable"] = True to prevent stacking
    (e.g., for wands with varying charges).
    """
    tags = getattr(item, "tags", {}) or {}
    if tags.get("not_stackable"):
        return None
    # Use proto_id (template) as primary key, fall back to item_type tag
    proto = getattr(item, "proto_id", None) or tags.get("item_type")
    return str(proto) if proto else None


def _find_stack_target(inv: List[Any], item: Any) -> Optional[Any]:
    """Find an existing stack in inventory that item can join."""
    key = _stack_key(item)
    if key is None:
        return None
    for existing in inv:
        # Don't stack into equipped items
        etags = getattr(existing, "tags", {}) or {}
        if etags.get("equipped_slot") or etags.get("equipped"):
            continue
        if _stack_key(existing) == key:
            # Check if stack has room
            if get_quantity(existing) < STACK_MAX:
                return existing
    return None


def get_quantity(item: Any) -> int:
    """Get the quantity of an item (default 1)."""
    tags = getattr(item, "tags", {}) or {}
    try:
        return max(1, int(tags.get("quantity", 1)))
    except (TypeError, ValueError):
        return 1


def set_quantity(item: Any, qty: int) -> None:
    """Set the quantity of an item, clamped to [1, STACK_MAX]."""
    qty = max(1, min(qty, STACK_MAX))
    tags = getattr(item, "tags", None)
    if tags is None:
        tags = {}
    tags["quantity"] = qty
    try:
        item.tags = tags
    except Exception:
        pass


def _add_to_stack(target: Any, amount: int) -> int:
    """Add amount to existing stack. Returns overflow (amount that didn't fit)."""
    current = get_quantity(target)
    total = current + amount
    if total <= STACK_MAX:
        set_quantity(target, total)
        return 0
    else:
        set_quantity(target, STACK_MAX)
        return total - STACK_MAX


# ---------------------------------------------------------------------------
# Inventory Access
# ---------------------------------------------------------------------------

def get_inventory(game: "Game", owner_id: str) -> List["Entity"]:
    """Return the inventory list for a given owner id.

    Queries containment edges from entity_graph. This is the absolute source
    of truth for inventory contents.
    """
    oid = str(owner_id)
    resolved: List[Any] = []
    graph = getattr(game, "entity_graph", None)
    if graph is not None:
        try:
            get_children = getattr(graph, "get_children", None)
            if callable(get_children):
                for cid in get_children(oid, socket_id="inventory"):
                    obj = entity_lifecycle_system.find_runtime_entity(game, str(cid))
                    if obj is not None:
                        resolved.append(obj)
        except Exception:
            pass
    return resolved


def get_player_inventory(game: "Game") -> List["Entity"]:
    """Return the current host's inventory.

    This automatically follows body-swaps by using the current player_id.
    """
    return get_inventory(game, game.player_id)


def iter_inventory_root_owner_ids(
    game: "Game",
    *,
    level: Optional[Any] = None,
) -> Iterator[str]:
    """Yield known top-level inventory owners without duplicating ids.

    This is the shared discovery surface for systems that need to walk
    inventory trees after list-backed ``game.inventories`` authority has been
    deleted.
    """
    seen: set[str] = set()

    def _yield_once(raw_owner_id: Any) -> Iterator[str]:
        owner_id = str(raw_owner_id or "").strip()
        if owner_id and owner_id not in seen:
            seen.add(owner_id)
            yield owner_id

    yield from _yield_once(getattr(game, "player_id", None))

    if level is None:
        try:
            level = game._level()
        except Exception:
            level = None

    if level is not None:
        for collection_name in ("actors", "entities"):
            collection = getattr(level, collection_name, None)
            if isinstance(collection, dict):
                for owner_id in collection.keys():
                    yield from _yield_once(owner_id)


def iter_inventory_tree(
    game: "Game",
    owner_id: str,
    *,
    _visited_owner_ids: Optional[set[str]] = None,
) -> Iterator[tuple[str, "Entity"]]:
    """Yield ``(owner_id, item)`` pairs for an owner's recursive inventory tree."""
    current_owner_id = str(owner_id or "").strip()
    if not current_owner_id:
        return

    visited_owner_ids = _visited_owner_ids if _visited_owner_ids is not None else set()
    if current_owner_id in visited_owner_ids:
        return
    visited_owner_ids.add(current_owner_id)

    try:
        inventory_items = list(get_inventory(game, current_owner_id) or [])
    except Exception:
        inventory_items = []

    for item in inventory_items:
        yield current_owner_id, item
        child_owner_id = str(getattr(item, "id", "") or "").strip()
        if child_owner_id:
            yield from iter_inventory_tree(
                game,
                child_owner_id,
                _visited_owner_ids=visited_owner_ids,
            )


def add_inventory_item(game: "Game", owner_id: str, ent: Any) -> None:
    """Attach an item to an owner's inventory through graph-first plumbing."""
    oid = str(owner_id or "")
    if not oid or ent is None:
        return

    try:
        entity_graph_ops_system.attach_entity_to_parent(game, ent, oid, socket_id="inventory")
    except Exception:
        pass


def remove_inventory_item(game: "Game", owner_id: str, ent: Any, *, reason: Optional[str] = None) -> bool:
    """Detach a specific inventory item."""
    oid = str(owner_id or "")
    if not oid or ent is None:
        return False

    is_attached = False
    if str(getattr(ent, "parent_entity_id", "")) == oid and str(getattr(ent, "socket_id", "")) == "inventory":
        is_attached = True
    else:
        graph = getattr(game, "entity_graph", None)
        if graph:
            node = graph.get_node(getattr(ent, "id", ""))
            if node and node.parent_entity_id == oid and node.socket_id == "inventory":
                is_attached = True

    if not is_attached:
        # verify via get_inventory just in case
        inv = get_inventory(game, oid)
        if ent not in inv:
            return False

    try:
        entity_graph_ops_system.detach_entity_from_parent(game, ent)
    except Exception:
        pass

    if reason:
        _mark_entity_removed(game, ent, reason=reason)
    return True


def remove_inventory_item_at(
    game: "Game",
    owner_id: str,
    index: int,
    *,
    reason: Optional[str] = None,
) -> Optional["Entity"]:
    """Detach and return the item at ``index`` from an inventory."""
    inv = get_inventory(game, owner_id)
    if not (0 <= index < len(inv)):
        return None
    ent = inv[index]
    if remove_inventory_item(game, owner_id, ent, reason=reason):
        return ent
    return None


def _mark_entity_removed(game: "Game", ent: Any, *, reason: str) -> None:
    """Best-effort persistence write keyed by entity id."""
    try:
        mark = getattr(game, "mark_entity_removed", None)
        if callable(mark):
            mark(ent, reason=reason)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pick Up / Drop
# ---------------------------------------------------------------------------

def _remove_ent_from_level(level: "LevelState", ent: Any, game: Any = None) -> None:
    """Remove *ent* from the level entity dict and from SpatialIndex.

    Passing *game* ensures the entity is also removed from the spatial index so
    the attention system does not re-mirror it on the next tick.
    """
    ent_id = getattr(ent, "id", None)
    if ent_id is not None and ent_id in level.entities:
        del level.entities[ent_id]
    else:
        # Fallback: scan by identity (legacy path where id may not match key)
        for eid, e in list(level.entities.items()):
            if e is ent:
                ent_id = eid
                del level.entities[eid]
                break

    # Remove from SpatialIndex so the attention system doesn't re-add it.
    if game is not None and ent_id is not None:
        try:
            idx = getattr(game, "spatial_index", None)
            if idx is not None:
                idx.remove(str(ent_id), source="attention")
        except Exception:
            pass


def _do_pickup(game: "Game", level: "LevelState", ent: Any) -> bool:
    """Shared pickup core — handles currency, stacking, logging, grants, FOV.

    Returns True if the item was picked up (fully or partially), False if
    the entity cannot be picked up.
    """
    tags = getattr(ent, "tags", {}) or {}

    # Currency piles are auto-absorbed.
    if tags.get("currency") == "bismuth":
        amt = int(tags.get("amount", 0))
        if amt > 0:
            game.adjust_currency(amt, log=True)
            game._play_sfx("assets/sfx/chaching.mp3", volume=0.7)
        _mark_entity_removed(game, ent, reason="pickup_currency")
        _remove_ent_from_level(level, ent, game)
        return True

    # Don't allow picking up actors or non-item entities.
    if hasattr(ent, "faction") or getattr(ent, "kind", None) != "item":
        game.log.add("You can't pick that up.")
        return False

    pickup_qty = get_quantity(ent)
    name = getattr(ent, "name", None) or "item"

    inv = get_player_inventory(game)
    stack_target = _find_stack_target(inv, ent)

    if stack_target is not None:
        overflow = _add_to_stack(stack_target, pickup_qty)
        if overflow > 0:
            set_quantity(ent, overflow)
            picked = pickup_qty - overflow
            if picked > 1:
                game.log.add(f"You pick up {picked} {name.lower()}s (stack full).")
            else:
                game.log.add(f"You pick up a {name.lower()} (stack full).")
        else:
            _mark_entity_removed(game, ent, reason="pickup")
            _remove_ent_from_level(level, ent, game)
            if pickup_qty > 1:
                game.log.add(f"You pick up {pickup_qty} {name.lower()}s.")
            else:
                article = "an" if name and name[0].lower() in "aeiou" else "a"
                game.log.add(f"You pick up {article} {name.lower()}.")
    else:
        _mark_entity_removed(game, ent, reason="pickup")
        _remove_ent_from_level(level, ent, game)
        set_quantity(ent, pickup_qty)
        add_inventory_item(game, game.player_id, ent)

        if pickup_qty > 1:
            game.log.add(f"You pick up {pickup_qty} {name.lower()}s.")
        else:
            article = "an" if name and name[0].lower() in "aeiou" else "a"
            game.log.add(f"You pick up {article} {name.lower()}.")

    # Item-granted actions
    try:
        grants = item_grants.get_item_grants(ent)
    except Exception:
        grants = []
    if grants:
        game.refresh_actor_actions(game.player_id)
        for action, mode in grants:
            if mode != "held":
                continue
            game.log.add(f"You can {action.replace('_', ' ')} while holding it.")

    # Refresh FOV if picked up item was emitting light
    if tags.get("light_radius", 0) > 0:
        level.need_fov = True
        try:
            game._update_fov(level)
        except Exception:
            pass

    return True


def player_pick_up(game: "Game") -> None:
    """Attempt to pick up an item under the player's feet."""
    level = game._level()
    if game.player_id not in level.actors:
        return
    player = level.actors[game.player_id]
    ent = game._entity_at(level, player.pos)
    if ent is None:
        game.log.add("There is nothing here to pick up.")
        return
    _do_pickup(game, level, ent)


def player_pick_up_item(game: "Game", item: Any) -> bool:
    """Pick up a specific item from the ground (not just the 'top' one).

    Used by CacheItemsScene for multi-item pickup selection.
    Returns True if the item was picked up, False otherwise.
    """
    level = game._level()
    if game.player_id not in level.actors:
        return False

    item_id = getattr(item, "id", None)
    if item_id is None or item_id not in level.entities:
        return False

    ent = level.entities[item_id]
    return _do_pickup(game, level, ent)


def drop_inventory_item(game: "Game", index: int) -> None:
    """Drop an item from the inventory onto the player's current tile."""
    inv = get_player_inventory(game)
    if not (0 <= index < len(inv)):
        return

    level = game._level()
    if game.player_id not in level.actors:
        return
    player = level.actors[game.player_id]

    ent = inv[index]
    # If the item was equipped, unequip it first so it stops granting stats/actions.
    try:
        if equipment_system.is_equipped(ent):
            unequip_item(game, game.player_id, str(getattr(ent, "id", "")))
    except Exception:
        pass
    ent = remove_inventory_item_at(game, game.player_id, index)
    if ent is None:
        return

    # Place the entity at the player's current position in the world.
    ent.pos = player.pos
    level.entities[ent.id] = ent  # type: ignore[index]

    name = getattr(ent, "name", None) or "item"
    article = "an" if name and name[0].lower() in "aeiou" else "a"
    game.log.add(f"You drop {article} {name.lower()}.")
    game.refresh_actor_actions(game.player_id)

    # Refresh FOV if dropped item emits light
    tags = getattr(ent, "tags", {}) or {}
    if tags.get("light_radius", 0) > 0:
        level.need_fov = True
        try:
            game._update_fov(level)
        except Exception:
            pass


def drop_inventory_item_qty(game: "Game", index: int, qty: Optional[int] = None) -> None:
    """Drop a specified quantity from a stack at index.

    Args:
        game: The game instance
        index: Index in player inventory
        qty: Number to drop. None or >= current quantity drops entire stack.
    """
    inv = get_player_inventory(game)
    if not (0 <= index < len(inv)):
        return

    level = game._level()
    if game.player_id not in level.actors:
        return
    player = level.actors[game.player_id]

    ent = inv[index]
    current_qty = get_quantity(ent)
    name = getattr(ent, "name", None) or "item"

    # If qty is None or >= current, drop entire stack
    if qty is None or qty >= current_qty:
        # Use existing drop logic for full stack
        drop_inventory_item(game, index)
        return

    # Partial drop: split the stack
    qty = max(1, qty)

    # Reduce quantity in inventory
    set_quantity(ent, current_qty - qty)

    # Create a clone for the ground
    dropped = entity_graph_ops_system.split_stack_entity(
        game,
        ent,
        qty,
        split_kind="drop",
        spawn_pos=player.pos,
        clear_equipped_tags=False,
    )
    entity_graph_ops_system.detach_entity_from_parent(game, dropped)
    dropped.pos = player.pos
    level.entities[dropped.id] = dropped

    if qty > 1:
        game.log.add(f"You drop {qty} {name.lower()}s.")
    else:
        article = "an" if name and name[0].lower() in "aeiou" else "a"
        game.log.add(f"You drop {article} {name.lower()}.")

    # Refresh FOV if dropped item emits light
    tags = getattr(ent, "tags", {}) or {}
    if tags.get("light_radius", 0) > 0:
        level.need_fov = True
        try:
            game._update_fov(level)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Eating / Consuming Items
# ---------------------------------------------------------------------------

def eat_item_from_inventory(game: "Game", owner_id: str, index: int) -> None:
    """Consume an item from the given owner's inventory, if edible.

    This is mainly used for test berries. The *player* always gets
    healed, regardless of where the item was stored.
    """
    inv = get_inventory(game, owner_id)
    if not inv:
        if owner_id == game.player_id:
            game.log.add("You have nothing to eat.")
        return

    if not (0 <= index < len(inv)):
        return

    ent = inv[index]
    tags = getattr(ent, "tags", {}) or {}

    is_berry = bool(tags.get("test_berry")) or tags.get("item_type") in {
        "blueberry",
        "raspberry",
        "strawberry",
    }
    if not is_berry:
        name = getattr(ent, "name", None) or "item"
        if owner_id == game.player_id:
            game.log.add(f"You can't eat the {name.lower()}.")
        else:
            # Slightly different flavour when rummaging in bags.
            game.log.add(f"You decide not to eat the {name.lower()}.")
        return

    # Consume one from the stack
    qty = get_quantity(ent)
    if qty > 1:
        # Decrement quantity instead of removing
        set_quantity(ent, qty - 1)
    else:
        # Last one - remove from inventory
        remove_inventory_item_at(game, owner_id, index, reason="consumed")

    # Heal the player a bit for eating a berry.
    player = game._player()
    before = player.stats.hp
    player.stats.hp = min(player.stats.max_hp, player.stats.hp + 1)
    after = player.stats.hp

    if after > before:
        game.log.add("That was tart!")
    else:
        game.log.add("That was really tart!")


def eat_inventory_item(game: "Game", index: int) -> None:
    """Backward-compatible wrapper for older code paths.

    Eats from the current host's inventory (player).
    """
    eat_item_from_inventory(game, game.player_id, index)


# ---------------------------------------------------------------------------
# Moving Items Between Inventories
# ---------------------------------------------------------------------------

def take_from_container(game: "Game", container_id: str, index: int) -> None:
    """Move an item from another entity's inventory into the player's.

    Legacy helper used by some older UIs / AI. Newer code should prefer
    ``move_item_between_inventories`` directly so we have a single place
    to handle recursive containers and logging.
    """
    move_item_between_inventories(game, container_id, index, game.player_id)


def move_item_between_inventories(
    game: "Game",
    src_owner_id: str,
    index: int,
    dest_owner_id: str,
) -> None:
    """Move an item from one entity's inventory to another's.

    Used by the UI to 'bag' items into containers (or later,
    for trading, stealing, etc.).
    """
    # No-op if same inventory
    if src_owner_id == dest_owner_id:
        return

    src_inv = get_inventory(game, src_owner_id)
    if not (0 <= index < len(src_inv)):
        return

    # Peek at the item first so we can detect self-removal on recursive bags.
    ent = src_inv[index]
    if getattr(ent, "id", None) == src_owner_id:
        # Special case: a container trying to remove itself from its own inventory.
        # Do not mutate any inventories; just narrate the failure.
        name = getattr(ent, "name", None) or "item"
        game.log.add(
            f"You turn the {name.lower()} inside out, but it remains itself."
        )
        return

    # If the item is equipped, unequip it before transferring inventories.
    # "Equipped" is a relationship to the current owner; it should not travel.
    try:
        if equipment_system.is_equipped(ent):
            tags = getattr(ent, "tags", {}) or {}
            tags.pop("equipped_slot", None)
            tags.pop("equipped", None)
            ent.tags = tags
    except Exception:
        pass

    # Normal case: actually move the item.
    dst_inv = get_inventory(game, dest_owner_id)
    entity_graph_ops_system.transfer_inventory_entity(
        game,
        ent,
        src_inventory=src_inv,
        dst_inventory=dst_inv,
        dst_owner_id=dest_owner_id,
        socket_id="inventory",
    )

    name = getattr(ent, "name", None) or "item"
    article = "an" if name and name[0].lower() in "aeiou" else "a"

    # Friendly label for the destination
    dest_label: str
    dest_ent: Optional[Any] = None
    if dest_owner_id == game.player_id:
        dest_label = "your inventory"
    else:
        dest_label = dest_owner_id
        level = game._level()

        # First try level entities / actors
        dest_ent = level.entities.get(dest_owner_id) or level.actors.get(dest_owner_id)

        # Fall back to entity registry (covers inventory items tracked at pickup).
        if dest_ent is None:
            dest_ent = entity_lifecycle_system.find_runtime_entity(game, dest_owner_id)

    if dest_ent is not None:
        dest_name = getattr(dest_ent, "name", None)
        if dest_name:
            dest_label = dest_name

    game.log.add(f"You put {article} {name.lower()} into {dest_label}.")

    # Item-granted actions can appear/disappear for either inventory owner.
    game.refresh_actor_actions(src_owner_id)
    game.refresh_actor_actions(dest_owner_id)


def move_item_between_inventories_qty(
    game: "Game",
    src_owner_id: str,
    index: int,
    dest_owner_id: str,
    qty: Optional[int] = None,
) -> None:
    """Move a specified quantity from one inventory to another.

    Args:
        qty: Number to move. None or >= current quantity moves entire stack.
    """
    # No-op if same inventory
    if src_owner_id == dest_owner_id:
        return

    src_inv = get_inventory(game, src_owner_id)
    if not (0 <= index < len(src_inv)):
        return

    ent = src_inv[index]

    # Self-removal check (container recursion)
    if getattr(ent, "id", None) == src_owner_id:
        name = getattr(ent, "name", None) or "item"
        game.log.add(
            f"You turn the {name.lower()} inside out, but it remains itself."
        )
        return

    current_qty = get_quantity(ent)

    # If qty is None or >= current, move entire stack
    if qty is None or qty >= current_qty:
        move_item_between_inventories(game, src_owner_id, index, dest_owner_id)
        return

    # Partial transfer: split the stack
    qty = max(1, qty)

    # Unequip if equipped (whole stack gets unequipped before split)
    try:
        if equipment_system.is_equipped(ent):
            tags = getattr(ent, "tags", {}) or {}
            tags.pop("equipped_slot", None)
            tags.pop("equipped", None)
            ent.tags = tags
    except Exception:
        pass

    # Reduce quantity in source
    set_quantity(ent, current_qty - qty)

    # Clone for destination
    transferred = entity_graph_ops_system.transfer_split_quantity(
        game,
        ent,
        qty=qty,
        split_kind="transfer",
        dst_inventory=get_inventory(game, dest_owner_id),
        dst_owner_id=dest_owner_id,
        spawn_pos=getattr(ent, "pos", (0, 0)),
        clear_equipped_tags=True,
        socket_id="inventory",
    )

    # Logging
    name = getattr(ent, "name", None) or "item"
    article = "an" if name and name[0].lower() in "aeiou" else "a"

    dest_label: str
    dest_ent: Optional[Any] = None
    if dest_owner_id == game.player_id:
        dest_label = "your inventory"
    else:
        dest_label = dest_owner_id
        # Find destination entity for friendly name
        level = game._level()
        dest_ent = level.entities.get(dest_owner_id) or level.actors.get(dest_owner_id)

        # Fall back to entity registry (covers inventory items tracked at pickup).
        if dest_ent is None:
            dest_ent = entity_lifecycle_system.find_runtime_entity(game, dest_owner_id)

    if dest_ent is not None:
        dest_name = getattr(dest_ent, "name", None)
        if dest_name:
            dest_label = dest_name

    if qty > 1:
        game.log.add(f"You put {qty} {name.lower()}s into {dest_label}.")
    else:
        game.log.add(f"You put {article} {name.lower()} into {dest_label}.")

    # Refresh actions for both inventories
    game.refresh_actor_actions(src_owner_id)
    game.refresh_actor_actions(dest_owner_id)


# ---------------------------------------------------------------------------
# Equipment Tagging
# ---------------------------------------------------------------------------


def _refresh_fov_if_player(game: "Game", owner_id: str) -> None:
    """Refresh FOV if owner is the player (for view radius equipment changes)."""
    try:
        if str(owner_id) == str(getattr(game, "player_id", "")):
            level = game._level()
            game._update_fov(level)
    except Exception:
        pass


def get_equipped_in_slot(
    game: "Game",
    owner_id: str,
    slot_id: str
) -> Optional["Entity"]:
    """Return the inventory entity currently tagged as equipped in `slot_id`, if any."""
    oid = str(owner_id)
    sid = str(slot_id)

    graph = getattr(game, "entity_graph", None)
    if graph is not None:
        child_ids = list(graph.get_children(oid, socket_id=sid))
        for cid in child_ids:
            try:
                obj = entity_lifecycle_system.find_runtime_entity(game, cid)
                if obj is not None:
                    return obj
            except Exception:
                pass

    inv = get_inventory(game, str(owner_id))
    for ent in inv:
        tags = getattr(ent, "tags", {}) or {}
        cur = tags.get("equipped_slot") or tags.get("equipped")
        if cur is not None and str(cur) == sid:
            return ent
    return None


def unequip_slot(game: "Game", owner_id: str, slot_id: str) -> None:
    """Clear any item currently equipped in `slot_id`."""
    ent = get_equipped_in_slot(game, owner_id, slot_id)
    if ent is None:
        return
    tags = getattr(ent, "tags", {}) or {}
    tags.pop("equipped_slot", None)
    tags.pop("equipped", None)
    try:
        entity_graph_ops_system.reparent_entity(game, ent, parent_id=str(owner_id), socket_id="inventory")
    except Exception:
        pass
    try:
        setattr(ent, "tags", tags)
    except Exception:
        pass
    game.refresh_actor_actions(str(owner_id))
    # Refresh FOV in case equipment affected view radius
    _refresh_fov_if_player(game, owner_id)


def unequip_item(game: "Game", owner_id: str, item_id: str) -> None:
    """Clear equipped tags from the given inventory item if present."""
    oid = str(owner_id)
    iid = str(item_id)

    ent = None
    try:
        ent = entity_lifecycle_system.find_runtime_entity(game, iid)
    except Exception:
        pass

    if ent is None:
        inv = get_inventory(game, oid)
        for e in inv:
            if str(getattr(e, "id", "")) == iid:
                ent = e
                break

    if ent is None:
        return

    tags = getattr(ent, "tags", {}) or {}
    tags.pop("equipped_slot", None)
    tags.pop("equipped", None)
    try:
        entity_graph_ops_system.reparent_entity(game, ent, parent_id=oid, socket_id="inventory")
    except Exception:
        pass
    try:
        setattr(ent, "tags", tags)
    except Exception:
        pass
    game.refresh_actor_actions(oid)
    # Refresh FOV in case equipment affected view radius
    _refresh_fov_if_player(game, owner_id)


def equip_item_to_slot(
    game: "Game",
    owner_id: str,
    item_id: str,
    slot_id: str
) -> None:
    """Tag an existing inventory item as 'equipped' in `slot_id`.

    For now, this is intentionally permissive: any item can be equipped into any slot.
    If the slot is already occupied, it will be replaced.
    """
    oid = str(owner_id)
    iid = str(item_id)
    sid = str(slot_id)

    ent = None
    try:
        ent = entity_lifecycle_system.find_runtime_entity(game, iid)
    except Exception:
        pass

    if ent is None:
        inv = get_inventory(game, oid)
        for e in inv:
            if str(getattr(e, "id", "")) == iid:
                ent = e
                break

    if ent is None:
        return

    # Validate slot compatibility before mutating currently equipped state.
    allowed, reason = equip_rules.can_equip_item_in_slot(ent, sid)
    if not allowed:
        if oid == str(getattr(game, "player_id", "")) and reason:
            game.log.add(str(reason))
        return

    # Ensure only one item occupies a slot.
    unequip_slot(game, oid, sid)

    tags = getattr(ent, "tags", {}) or {}
    tags["equipped_slot"] = sid
    try:
        entity_graph_ops_system.reparent_entity(game, ent, parent_id=oid, socket_id=sid)
    except Exception:
        pass
    try:
        setattr(ent, "tags", tags)
    except Exception:
        pass
    game.refresh_actor_actions(oid)
    # Refresh FOV in case equipment affected view radius
    _refresh_fov_if_player(game, oid)


def equip_item_to_slot_qty(
    game: "Game",
    owner_id: str,
    item_id: str,
    slot_id: str,
    qty: int = 1,
) -> None:
    """Equip a specific quantity from a stacked item to a slot.

    If qty is 1 and the item has quantity > 1, this will:
    1. Split off one item from the stack
    2. Equip the single item
    3. Leave the rest in the inventory

    Args:
        qty: How many to equip (default 1). For future use with throwables.
    """
    oid = str(owner_id)
    iid = str(item_id)
    sid = str(slot_id)

    # Find the item in inventory
    inv = get_inventory(game, oid)
    item = None
    for ent in inv:
        if str(getattr(ent, "id", "")) == iid:
            item = ent
            break

    if item is None:
        return

    current_qty = get_quantity(item)

    # Check if item allows stacked equipping (for future throwables)
    tags = getattr(item, "tags", {}) or {}
    allow_stack_equip = tags.get("equip_stacked", False)

    # If single item OR stack equipping allowed, use original logic
    if current_qty == 1 or allow_stack_equip:
        equip_item_to_slot(game, oid, iid, sid)
        return

    # Stack with qty > 1: split it
    qty_to_equip = min(qty, current_qty)
    qty_remaining = current_qty - qty_to_equip

    if qty_remaining > 0:
        # Reduce the original stack
        set_quantity(item, qty_remaining)

        # Clone for the equipped item
        equipped_item = entity_graph_ops_system.split_stack_entity(
            game,
            item,
            qty_to_equip,
            split_kind="equip",
            spawn_pos=getattr(item, "pos", (0, 0)),
            clear_equipped_tags=True,
        )

        # Add to inventory through the shared graph-backed helper.
        add_inventory_item(game, oid, equipped_item)

        # Equip the new single item
        equip_item_to_slot(game, oid, str(equipped_item.id), sid)
    else:
        # Equipping the full stack (shouldn't happen with qty=1, but handle it)
        equip_item_to_slot(game, oid, iid, sid)
