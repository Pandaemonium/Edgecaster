"""Targeted tests for inventory-driven event helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from edgecaster.events import _consume_one_berry, _player_has_berry


def test_player_has_berry_recurses_nested_inventory_tree() -> None:
    berry = SimpleNamespace(id="berry_1", tags={"test_berry": True})
    bag = SimpleNamespace(id="bag_1", tags={"container": True})
    game = SimpleNamespace(
        player_id="player",
        inventories={
            "player": [bag],
            "bag_1": [berry],
        },
        entity_graph=None,
    )

    assert _player_has_berry(game) is True


def test_consume_one_berry_removes_nested_item_via_shared_inventory_helper() -> None:
    berry = SimpleNamespace(id="berry_1", tags={"test_berry": True})
    bag = SimpleNamespace(id="bag_1", tags={"container": True})
    inventories = {
        "player": [bag],
        "bag_1": [berry],
    }
    game = SimpleNamespace(
        player_id="player",
        inventories=inventories,
        entity_graph=None,
    )

    def _remove(game_obj, owner_id, index, reason=None):
        assert game_obj is game
        assert reason is None
        return inventories[str(owner_id)].pop(index)

    with patch(
        "edgecaster.systems.inventory.remove_inventory_item_at",
        side_effect=_remove,
    ) as remove_inventory_item_at:
        assert _consume_one_berry(game) is True

    remove_inventory_item_at.assert_called_once_with(game, "bag_1", 0)
    assert inventories["bag_1"] == []
