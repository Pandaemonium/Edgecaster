"""Unit tests for trade ownership/identity bridge behavior."""

from unittest.mock import MagicMock, patch

from edgecaster.state.entities import Entity
from edgecaster.systems.trade import (
    ProposalSummary,
    PriceQuote,
    apply_proposal_with_qty,
    restock_merchant,
)


def _mk_item(*, item_id: str, qty: int = 1) -> Entity:
    ent = Entity(
        id=item_id,
        entity_id=item_id,
        name="Widget",
        pos=(0, 0),
        abs_pos=(0, 0),
        glyph="*",
        color=(255, 255, 255),
        kind="item",
    )
    ent.tags = {"quantity": int(qty)}
    return ent


def test_apply_proposal_with_qty_partial_buy_sets_player_ownership() -> None:
    game = MagicMock()
    game.player_id = "player"
    game.bismuth = 100
    game.log = MagicMock()
    game.adjust_currency = MagicMock()
    game.patch_entity_state = MagicMock()
    game.zone_coord = (0, 0, 0)

    merchant = MagicMock()
    merchant.id = "merchant_1"
    merchant.tags = {}

    level = MagicMock()
    level.actors = {"merchant_1": merchant}
    game._level.return_value = level

    merchant_item = _mk_item(item_id="merch_item", qty=3)
    inventories = {"merchant_1": [merchant_item], "player": []}
    game.get_inventory.side_effect = lambda owner_id: inventories[str(owner_id)]

    summary = ProposalSummary(
        buy_total=2,
        sell_total=0,
        net_player=-2,
        player_bismuth_before=100,
        player_bismuth_after=98,
        merchant_funds_before=10,
        merchant_funds_after=12,
        ok=True,
        reason="",
    )

    with patch("edgecaster.systems.trade.ensure_merchant_initialized"), patch(
        "edgecaster.systems.trade.proposal_summary_with_qty", return_value=summary
    ), patch(
        "edgecaster.systems.trade.quote_prices", return_value=PriceQuote(1, 2, 1)
    ):
        ok, msg = apply_proposal_with_qty(
            game,
            "merchant_1",
            {"merch_item": 1},
            {},
        )

    assert ok is True
    assert msg == ""
    assert inventories["merchant_1"][0].tags.get("quantity") == 2
    assert len(inventories["player"]) == 1
    bought = inventories["player"][0]
    assert bought.tags.get("inventory_owner_id") == "player"
    assert bought.tags.get("in_inventory") is True
    assert getattr(bought, "parent_entity_id", None) == "player"
    assert getattr(bought, "socket_id", None) == "inventory"
    assert bought.tags.get("split_kind") == "trade"
    game.adjust_currency.assert_called_once_with(-2, log=False)


def test_apply_proposal_with_qty_full_sell_sets_merchant_ownership() -> None:
    game = MagicMock()
    game.player_id = "player"
    game.bismuth = 5
    game.log = MagicMock()
    game.adjust_currency = MagicMock()
    game.patch_entity_state = MagicMock()
    game.zone_coord = (0, 0, 0)

    merchant = MagicMock()
    merchant.id = "merchant_1"
    merchant.tags = {}

    level = MagicMock()
    level.actors = {"merchant_1": merchant}
    game._level.return_value = level

    player_item = _mk_item(item_id="player_item", qty=1)
    inventories = {"merchant_1": [], "player": [player_item]}
    game.get_inventory.side_effect = lambda owner_id: inventories[str(owner_id)]

    summary = ProposalSummary(
        buy_total=0,
        sell_total=1,
        net_player=1,
        player_bismuth_before=5,
        player_bismuth_after=6,
        merchant_funds_before=10,
        merchant_funds_after=9,
        ok=True,
        reason="",
    )

    with patch("edgecaster.systems.trade.ensure_merchant_initialized"), patch(
        "edgecaster.systems.trade.proposal_summary_with_qty", return_value=summary
    ), patch(
        "edgecaster.systems.trade.quote_prices", return_value=PriceQuote(1, 2, 1)
    ):
        ok, msg = apply_proposal_with_qty(
            game,
            "merchant_1",
            {},
            {"player_item": 1},
        )

    assert ok is True
    assert msg == ""
    assert len(inventories["player"]) == 0
    assert len(inventories["merchant_1"]) == 1
    sold = inventories["merchant_1"][0]
    assert sold.tags.get("inventory_owner_id") == "merchant_1"
    assert sold.tags.get("in_inventory") is True
    assert getattr(sold, "parent_entity_id", None) == "merchant_1"
    assert getattr(sold, "socket_id", None) == "inventory"
    game.adjust_currency.assert_called_once_with(1, log=False)


def test_restock_merchant_force_clears_existing_stock_via_shared_remove() -> None:
    game = MagicMock()
    merchant = MagicMock()
    merchant.id = "merchant_1"
    merchant.pos = (0, 0)
    merchant.tags = {"merchant_id": "test_merchant"}

    stale_item = _mk_item(item_id="old_stock")
    fresh_item = _mk_item(item_id="new_stock")
    inventories = {"merchant_1": [stale_item]}
    game.get_inventory.side_effect = lambda owner_id: inventories[str(owner_id)]
    game._spawn_entity_from_template.return_value = fresh_item
    game.rng = None

    with patch(
        "edgecaster.systems.trade.merchant_def_from_actor",
        return_value=MagicMock(
            max_funds_bismuth=50,
            max_stock=1,
            stock=[MagicMock(weight=1.0, qty_min=1, qty_max=1, proto="widget_proto")],
        ),
    ), patch(
        "edgecaster.systems.trade.inventory_system.remove_inventory_item",
        side_effect=lambda _game, owner_id, ent, reason=None: inventories[str(owner_id)].remove(ent),
    ) as remove_item, patch(
        "edgecaster.systems.trade._append_inventory_item",
        side_effect=lambda _game, inv, owner_id, ent: inventories[str(owner_id)].append(ent),
    ):
        restock_merchant(game, MagicMock(), merchant, force=True)

    remove_item.assert_called_once()
    assert inventories["merchant_1"] == [fresh_item]
