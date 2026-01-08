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

from typing import Any, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state.entities import Entity
    from edgecaster.state import Actor

from edgecaster.systems import equipment as equipment_system
from edgecaster.systems import item_grants


# ---------------------------------------------------------------------------
# Inventory Access
# ---------------------------------------------------------------------------

def get_inventory(game: "Game", owner_id: str) -> List["Entity"]:
    """Return the inventory list for a given owner id, creating it if needed.

    This keeps all inventories in a single registry on the Game object,
    while still conceptually treating them as per-entity state.
    """
    return game.inventories.setdefault(owner_id, [])


def get_player_inventory(game: "Game") -> List["Entity"]:
    """Return the current host's inventory.

    This automatically follows body-swaps by using the current player_id.
    """
    return get_inventory(game, game.player_id)


# ---------------------------------------------------------------------------
# Pick Up / Drop
# ---------------------------------------------------------------------------

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

    # Currency piles are auto-absorbed.
    tags = getattr(ent, "tags", {}) or {}
    if tags.get("currency") == "bismuth":
        amt = int(tags.get("amount", 0))
        if amt > 0:
            game.adjust_currency(amt, log=True)
            # Play cash pickup sound (best-effort).
            game._play_sfx("assets/sfx/chaching.mp3", volume=0.7)
        # remove entity from world
        for eid, e in list(level.entities.items()):
            if e is ent:
                del level.entities[eid]
                break
        return

    # Don't allow picking up actors or non-item entities (for now).
    if hasattr(ent, "faction") or getattr(ent, "kind", None) != "item":
        game.log.add("You can't pick that up.")
        return

    # Remove from the level's entity list.
    for eid, e in list(level.entities.items()):
        if e is ent:
            del level.entities[eid]
            break

    # Append the item to the current host's inventory.
    inv = get_player_inventory(game)
    inv.append(ent)

    name = getattr(ent, "name", None) or "item"
    article = "an" if name and name[0].lower() in "aeiou" else "a"
    game.log.add(f"You pick up {article} {name.lower()}.")

    # Item-granted actions (held/equipped) are computed from inventory state,
    # so they appear/disappear automatically when the item is moved.
    try:
        grants = item_grants.get_item_grants(ent)
    except Exception:
        grants = []
    if grants:
        game.refresh_actor_actions(game.player_id)
        for action, mode in grants:
            if mode != "held":
                continue
            # Held-grants are temporary, so avoid "learned" language here.
            game.log.add(f"You can {action.replace('_', ' ')} while holding it.")


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
    ent = inv.pop(index)

    # Place the entity at the player's current position in the world.
    ent.pos = player.pos
    level.entities[ent.id] = ent  # type: ignore[index]

    name = getattr(ent, "name", None) or "item"
    article = "an" if name and name[0].lower() in "aeiou" else "a"
    game.log.add(f"You drop {article} {name.lower()}.")
    game.refresh_actor_actions(game.player_id)


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

    # Actually consume the item from that inventory.
    inv.pop(index)

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
    ent = src_inv.pop(index)
    dst_inv = get_inventory(game, dest_owner_id)
    dst_inv.append(ent)

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

        # If not found there, search through all inventories for a matching entity id
        if dest_ent is None:
            for inv_owner, items in game.inventories.items():
                for it in items:
                    if getattr(it, "id", None) == dest_owner_id:
                        dest_ent = it
                        break
                if dest_ent is not None:
                    break

    if dest_ent is not None:
        dest_name = getattr(dest_ent, "name", None)
        if dest_name:
            dest_label = dest_name

    game.log.add(f"You put {article} {name.lower()} into {dest_label}.")

    # Item-granted actions can appear/disappear for either inventory owner.
    game.refresh_actor_actions(src_owner_id)
    game.refresh_actor_actions(dest_owner_id)


# ---------------------------------------------------------------------------
# Equipment Tagging
# ---------------------------------------------------------------------------

def get_equipped_in_slot(
    game: "Game",
    owner_id: str,
    slot_id: str
) -> Optional["Entity"]:
    """Return the inventory entity currently tagged as equipped in `slot_id`, if any."""
    inv = get_inventory(game, str(owner_id))
    sid = str(slot_id)
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
        setattr(ent, "tags", tags)
    except Exception:
        pass
    game.refresh_actor_actions(str(owner_id))


def unequip_item(game: "Game", owner_id: str, item_id: str) -> None:
    """Clear equipped tags from the given inventory item if present."""
    inv = get_inventory(game, str(owner_id))
    iid = str(item_id)
    for ent in inv:
        if str(getattr(ent, "id", "")) != iid:
            continue
        tags = getattr(ent, "tags", {}) or {}
        tags.pop("equipped_slot", None)
        tags.pop("equipped", None)
        try:
            setattr(ent, "tags", tags)
        except Exception:
            pass
        game.refresh_actor_actions(str(owner_id))
        return


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

    # Ensure only one item occupies a slot.
    unequip_slot(game, oid, sid)

    inv = get_inventory(game, oid)
    for ent in inv:
        if str(getattr(ent, "id", "")) != iid:
            continue
        tags = getattr(ent, "tags", {}) or {}
        tags["equipped_slot"] = sid
        try:
            setattr(ent, "tags", tags)
        except Exception:
            pass
        game.refresh_actor_actions(oid)
        return
