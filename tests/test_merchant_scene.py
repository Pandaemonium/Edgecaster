from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edgecaster.scenes.merchant_scene import MerchantScene, _TradeRow


def test_sell_id_is_equipped_reads_player_inventory_through_get_inventory() -> None:
    scene = MerchantScene.__new__(MerchantScene)
    equipped_item = SimpleNamespace(id="dagger_1")
    scene.game = SimpleNamespace(
        player_id="player",
        get_inventory=lambda owner_id: [equipped_item] if owner_id == "player" else [],
    )

    with patch("edgecaster.scenes.merchant_scene.equipment_system.is_equipped", return_value=True):
        assert scene._sell_id_is_equipped("dagger_1") is True


def test_on_sell_click_resolves_entity_from_shared_inventory_query() -> None:
    scene = MerchantScene.__new__(MerchantScene)
    item = SimpleNamespace(id="apple_1")
    scene.game = SimpleNamespace(
        player_id="player",
        get_inventory=lambda owner_id: [item] if owner_id == "player" else [],
    )
    scene._status = None
    scene._current_manager = None
    scene.focus = "buy"
    scene.sell_index = 0
    scene.pending_sell_qtys = {}
    scene._toggle_pending_sell = MagicMock()
    scene._sell_id_is_equipped = MagicMock(return_value=False)

    row = _TradeRow(label="Apple", value="1b", index=0, ent_id="apple_1")
    scene._on_sell_click(0, row)

    scene._toggle_pending_sell.assert_called_once_with("apple_1", ent=item, manager=None)
