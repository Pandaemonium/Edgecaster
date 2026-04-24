"""Tests for shared entity graph attach/detach helpers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from edgecaster.systems import entity_graph_ops as entity_graph_ops_system


def test_attach_entity_to_parent_sets_runtime_fields() -> None:
    game = MagicMock()
    ent = SimpleNamespace(
        id="item:1",
        tags={},
        parent_entity_id=None,
        socket_id=None,
    )

    entity_graph_ops_system.attach_entity_to_parent(game, ent, "player:1", socket_id="inventory")

    assert ent.parent_entity_id == "player:1"
    assert ent.socket_id == "inventory"
    assert ent.tags.get("inventory_owner_id") == "player:1"
    assert ent.tags.get("in_inventory") is True
    game.patch_entity_state.assert_not_called()


def test_detach_entity_from_parent_clears_runtime_fields() -> None:
    game = MagicMock()
    ent = SimpleNamespace(
        id="item:2",
        tags={"inventory_owner_id": "player:1", "in_inventory": True},
        parent_entity_id="player:1",
        socket_id="inventory",
    )

    entity_graph_ops_system.detach_entity_from_parent(game, ent)

    assert ent.parent_entity_id is None
    assert ent.socket_id is None
    assert "inventory_owner_id" not in ent.tags
    assert "in_inventory" not in ent.tags
    game.patch_entity_state.assert_not_called()


def test_reparent_entity_attaches_or_detaches() -> None:
    game = MagicMock()
    ent = SimpleNamespace(
        id="item:3",
        tags={},
        parent_entity_id=None,
        socket_id=None,
    )

    entity_graph_ops_system.reparent_entity(game, ent, parent_id="merchant:1", socket_id="inventory")
    assert ent.parent_entity_id == "merchant:1"
    assert ent.socket_id == "inventory"

    entity_graph_ops_system.reparent_entity(game, ent, parent_id=None)
    assert ent.parent_entity_id is None
    assert ent.socket_id is None


def test_transfer_inventory_entity_moves_between_lists_and_reparents() -> None:
    game = MagicMock()
    ent = SimpleNamespace(id="item:4", tags={}, parent_entity_id="a", socket_id="inventory")
    src = [ent]
    dst: list = []

    entity_graph_ops_system.transfer_inventory_entity(
        game,
        ent,
        src_inventory=src,
        dst_inventory=dst,
        dst_owner_id="owner:b",
    )

    assert ent not in src
    assert ent in dst
    assert ent.parent_entity_id == "owner:b"
    assert ent.socket_id == "inventory"


def test_transfer_split_quantity_creates_child_and_attaches() -> None:
    game = MagicMock()
    src_item = SimpleNamespace(
        id="item:5",
        entity_id="item:5",
        proto_id="berry",
        name="Berry",
        pos=(2, 3),
        tags={"quantity": 10},
    )
    dst: list = []

    child = entity_graph_ops_system.transfer_split_quantity(
        game,
        src_item,
        qty=3,
        split_kind="transfer",
        dst_inventory=dst,
        dst_owner_id="owner:c",
        spawn_pos=(2, 3),
        clear_equipped_tags=True,
    )

    assert child in dst
    assert getattr(child, "parent_entity_id", None) == "owner:c"
    assert getattr(child, "socket_id", None) == "inventory"
    assert getattr(child, "tags", {}).get("quantity") == 3
