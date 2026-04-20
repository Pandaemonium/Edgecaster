from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from edgecaster.content import merchants as merchant_content
from edgecaster.systems import equipment as equipment_system
from edgecaster.systems import inventory as inventory_system


MERCHANT_ID_TAG = "merchant_id"
MERCHANT_FUNDS_TAG = "merchant_funds_bismuth"
MERCHANT_INITIALIZED_TAG = "merchant_initialized"

# Dev/test-only: if set on the merchant actor, restock will populate one of every
# item prototype in the game (leaf items only).
MERCHANT_ALL_ITEMS_TAG = "merchant_all_items"


@dataclass(frozen=True)
class PriceQuote:
    base_value: int
    buy_price: int
    sell_price: int


@dataclass(frozen=True)
class ProposalSummary:
    buy_total: int
    sell_total: int
    net_player: int
    player_bismuth_before: int
    player_bismuth_after: int
    merchant_funds_before: int
    merchant_funds_after: int
    ok: bool
    reason: str = ""


def _append_inventory_item(game: Any, inv: Any, owner_id: str, ent: Any) -> None:
    """Attach an item to inventory through the shared graph-first helper."""
    del inv
    inventory_system.add_inventory_item(game, owner_id, ent)


def _player_inventory(game: Any) -> Any:
    """Read the current player inventory through the shared query surface."""
    player_id = str(getattr(game, "player_id", "player"))
    return game.get_inventory(player_id)  # type: ignore[attr-defined]


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def merchant_def_from_actor(merchant_actor: Any) -> Optional[merchant_content.MerchantDef]:
    tags = getattr(merchant_actor, "tags", None) or {}
    merchant_id = tags.get(MERCHANT_ID_TAG)
    if not merchant_id:
        return None
    return merchant_content.MERCHANTS.get(str(merchant_id))


def ensure_merchant_initialized(game: Any, level: Any, merchant_actor: Any) -> None:
    tags = getattr(merchant_actor, "tags", None)
    if not isinstance(tags, dict):
        tags = {}
        merchant_actor.tags = tags

    if tags.get(MERCHANT_INITIALIZED_TAG):
        return

    mdef = merchant_def_from_actor(merchant_actor)
    if mdef is None:
        return

    tags[MERCHANT_INITIALIZED_TAG] = True
    tags[MERCHANT_FUNDS_TAG] = int(mdef.max_funds_bismuth)
    merchant_actor.tags = tags

    # Initial restock and schedule repeating restocks.
    restock_merchant(game, level, merchant_actor, force=True)

    interval = int(mdef.restock_interval_ticks)
    interval = 1 if interval <= 0 else interval

    def _restock_tick(aid: str = getattr(merchant_actor, "id", ""), lvl: Any = level) -> None:
        act = getattr(lvl, "actors", {}).get(aid)
        if act is None or not getattr(act, "alive", True):
            return
        try:
            restock_merchant(game, lvl, act, force=False)
        except Exception:
            pass
        try:
            game._schedule(lvl, interval, _restock_tick)  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        game._schedule(level, interval, _restock_tick)  # type: ignore[attr-defined]
    except Exception:
        pass


def merchant_funds(merchant_actor: Any) -> int:
    tags = getattr(merchant_actor, "tags", None) or {}
    return _safe_int(tags.get(MERCHANT_FUNDS_TAG, 0), 0)


def set_merchant_funds(merchant_actor: Any, amount: int) -> None:
    tags = getattr(merchant_actor, "tags", None)
    if not isinstance(tags, dict):
        tags = {}
        merchant_actor.tags = tags
    tags[MERCHANT_FUNDS_TAG] = max(0, int(amount))
    merchant_actor.tags = tags


def _entity_base_value(ent: Any) -> int:
    tags = getattr(ent, "tags", None) or {}
    v = tags.get("value_bismuth", None)
    if v is not None:
        return max(0, _safe_int(v, 0))
    # Default: items have at least a nominal value so merchants "buy everything".
    kind = getattr(ent, "kind", None)
    if kind == "item":
        return 1
    return 0


def quote_prices(merchant_actor: Any, ent: Any) -> Optional[PriceQuote]:
    mdef = merchant_def_from_actor(merchant_actor)
    if mdef is None:
        return None
    base = _entity_base_value(ent)
    buy = int(round(base * _safe_float(mdef.buy_multiplier, 1.0)))
    sell = int(round(base * _safe_float(mdef.sell_multiplier, 1.0)))
    buy = max(0, buy)
    sell = max(0, sell)
    return PriceQuote(base_value=base, buy_price=buy, sell_price=sell)


def proposal_summary(
    game: Any,
    merchant_actor_id: str,
    buy_item_ids: list[str] | set[str] | tuple[str, ...],
    sell_item_ids: list[str] | set[str] | tuple[str, ...],
) -> ProposalSummary:
    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return ProposalSummary(
            buy_total=0,
            sell_total=0,
            net_player=0,
            player_bismuth_before=_safe_int(getattr(game, "bismuth", 0), 0),
            player_bismuth_after=_safe_int(getattr(game, "bismuth", 0), 0),
            merchant_funds_before=0,
            merchant_funds_after=0,
            ok=False,
            reason="Merchant unavailable.",
        )

    ensure_merchant_initialized(game, level, merchant)

    buy_ids = {str(x) for x in buy_item_ids if x}
    sell_ids = {str(x) for x in sell_item_ids if x}

    player_before = _safe_int(getattr(game, "bismuth", 0), 0)
    merchant_before = merchant_funds(merchant)

    if not buy_ids and not sell_ids:
        return ProposalSummary(
            buy_total=0,
            sell_total=0,
            net_player=0,
            player_bismuth_before=player_before,
            player_bismuth_after=player_before,
            merchant_funds_before=merchant_before,
            merchant_funds_after=merchant_before,
            ok=True,
        )

    minv = list(game.get_inventory(merchant_actor_id))  # type: ignore[attr-defined]
    pinv = list(_player_inventory(game))

    by_id_minv = {getattr(ent, "id", ""): ent for ent in minv}
    by_id_pinv = {getattr(ent, "id", ""): ent for ent in pinv}

    buy_total = 0
    sell_total = 0
    reason: str | None = None

    for ent_id in buy_ids:
        ent = by_id_minv.get(ent_id)
        if ent is None:
            if reason is None:
                reason = "One or more items are no longer available."
            continue
        q = quote_prices(merchant, ent)
        if q is None or int(q.buy_price) <= 0:
            if reason is None:
                reason = "One or more proposed purchases are not for sale."
            continue
        buy_total += int(q.buy_price)

    for ent_id in sell_ids:
        ent = by_id_pinv.get(ent_id)
        if ent is None:
            if reason is None:
                reason = "One or more items are no longer in your inventory."
            continue
        if equipment_system.is_equipped(ent):
            if reason is None:
                reason = "Unequip items before selling them."
            continue
        q = quote_prices(merchant, ent)
        if q is None or int(q.sell_price) <= 0:
            if reason is None:
                reason = "The merchant isn't interested in one or more items."
            continue
        sell_total += int(q.sell_price)

    net_player = int(sell_total - buy_total)
    player_after = player_before + net_player
    merchant_after = merchant_before - net_player

    ok = reason is None
    if ok and player_after < 0:
        ok = False
        reason = "You don't have enough bismuth."
    if ok and merchant_after < 0:
        ok = False
        reason = "The merchant can't afford that right now."

    return ProposalSummary(
        buy_total=int(buy_total),
        sell_total=int(sell_total),
        net_player=int(net_player),
        player_bismuth_before=int(player_before),
        player_bismuth_after=int(player_after),
        merchant_funds_before=int(merchant_before),
        merchant_funds_after=int(merchant_after),
        ok=bool(ok),
        reason=str(reason or ""),
    )


def apply_proposal(
    game: Any,
    merchant_actor_id: str,
    buy_item_ids: list[str] | set[str] | tuple[str, ...],
    sell_item_ids: list[str] | set[str] | tuple[str, ...],
) -> tuple[bool, str]:
    from edgecaster.systems.entity_graph_ops import transfer_inventory_entity

    summary = proposal_summary(game, merchant_actor_id, buy_item_ids, sell_item_ids)
    if not (buy_item_ids or sell_item_ids):
        return True, ""
    if not summary.ok:
        return False, summary.reason or "Trade invalid."

    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return False, "Merchant unavailable."

    ensure_merchant_initialized(game, level, merchant)

    buy_ids = {str(x) for x in buy_item_ids if x}
    sell_ids = {str(x) for x in sell_item_ids if x}

    minv = game.get_inventory(merchant_actor_id)  # type: ignore[attr-defined]
    pinv = _player_inventory(game)

    buy_items = [ent for ent in list(minv) if getattr(ent, "id", None) in buy_ids]
    sell_items = [ent for ent in list(pinv) if getattr(ent, "id", None) in sell_ids]

    for ent in buy_items:
        transfer_inventory_entity(
            game,
            ent,
            src_inventory=minv,
            dst_inventory=pinv,
            dst_owner_id=str(getattr(game, "player_id", "player")),
        )
    for ent in sell_items:
        transfer_inventory_entity(
            game,
            ent,
            src_inventory=pinv,
            dst_inventory=minv,
            dst_owner_id=str(merchant_actor_id),
        )

    # Transfer funds
    try:
        game.adjust_currency(int(summary.net_player), log=False)  # type: ignore[attr-defined]
    except Exception:
        try:
            game.bismuth = int(getattr(game, "bismuth", 0)) + int(summary.net_player)
        except Exception:
            pass

    set_merchant_funds(merchant, int(summary.merchant_funds_after))

    # Log per-item messages (mirrors old immediate-trade UX, but only after commit).
    try:
        for ent in sell_items:
            q = quote_prices(merchant, ent)
            payout = int(q.sell_price) if q else 0
            game.log.add(f"You sell {ent.name} for {payout} bismuth.")  # type: ignore[attr-defined]
        for ent in buy_items:
            q = quote_prices(merchant, ent)
            price = int(q.buy_price) if q else 0
            game.log.add(f"You buy {ent.name} for {price} bismuth.")  # type: ignore[attr-defined]
    except Exception:
        pass

    return True, ""


def restock_merchant(game: Any, level: Any, merchant_actor: Any, *, force: bool) -> None:
    mdef = merchant_def_from_actor(merchant_actor)
    if mdef is None:
        return

    inv = game.get_inventory(getattr(merchant_actor, "id"))  # type: ignore[attr-defined]

    # Refill funds each restock.
    set_merchant_funds(merchant_actor, int(mdef.max_funds_bismuth))

    if force:
        merchant_id = str(getattr(merchant_actor, "id", "") or "")
        for item in list(inv):
            inventory_system.remove_inventory_item(
                game,
                merchant_id,
                item,
                reason="merchant_restock",
            )

    tags = getattr(merchant_actor, "tags", None) or {}
    if tags.get(MERCHANT_ALL_ITEMS_TAG):
        # Avoid expensive "all items" rebuilds on periodic restock ticks; we only
        # refresh on explicit interactions (force=True).
        if not force:
            return
        return _restock_merchant_all_items(game, merchant_actor, inv)

    max_stock = int(mdef.max_stock)
    if max_stock <= 0:
        return

    if len(inv) >= max_stock:
        return

    # If no stock table is defined, do nothing.
    entries = list(getattr(mdef, "stock", []) or [])
    if not entries:
        return

    # Weighted random selection, using game.rng if available.
    rng = getattr(game, "rng", None)

    def pick_entry() -> merchant_content.StockEntry:
        weights = [max(0.0, float(e.weight)) for e in entries]
        total = sum(weights)
        if total <= 0.0:
            return entries[0]
        r = (rng.random() if rng else 0.0) * total
        acc = 0.0
        for e, w in zip(entries, weights):
            acc += w
            if r <= acc:
                return e
        return entries[-1]

    # Fill until max_stock.
    while len(inv) < max_stock:
        e = pick_entry()
        qty_min = max(1, int(getattr(e, "qty_min", 1)))
        qty_max = max(qty_min, int(getattr(e, "qty_max", qty_min)))
        qty = qty_min
        try:
            if rng:
                qty = rng.randint(qty_min, qty_max)
        except Exception:
            qty = qty_min

        for _ in range(qty):
            if len(inv) >= max_stock:
                break
            try:
                item = game._spawn_entity_from_template(  # type: ignore[attr-defined]
                    str(e.proto),
                    pos=getattr(merchant_actor, "pos", (0, 0)),
                )
            except Exception:
                continue
            _append_inventory_item(game, inv, str(getattr(merchant_actor, "id", "")), item)


def _restock_merchant_all_items(game: Any, merchant_actor: Any, inv: Any) -> None:
    """Populate inventory with one of every leaf item prototype."""
    try:
        from edgecaster import prototypes
    except Exception:
        return

    bucket = prototypes.get_master_bucket()

    # Exclude non-leaf prototypes (authoring templates) for a cleaner stock list.
    parents: set[str] = set()
    for raw in bucket.values():
        try:
            parent = raw.get("parent")
        except Exception:
            parent = None
        if isinstance(parent, str) and parent:
            parents.add(parent)

    entries: list[tuple[str, str]] = []
    for pid in list(bucket.keys()):
        if pid in parents:
            continue
        try:
            spec = prototypes.resolve_proto(pid)
        except Exception:
            continue
        if str(spec.get("kind") or "") != "item":
            continue
        name = str(spec.get("name") or pid)
        entries.append((name.casefold(), str(pid)))

    entries.sort()

    pos = getattr(merchant_actor, "pos", (0, 0))
    for _, pid in entries:
        try:
            item = game._spawn_entity_from_template(  # type: ignore[attr-defined]
                str(pid),
                pos=pos,
            )
        except Exception:
            continue
        _append_inventory_item(game, inv, str(getattr(merchant_actor, "id", "")), item)


def try_buy(game: Any, merchant_actor_id: str, item_index: int) -> bool:
    from edgecaster.systems.entity_graph_ops import transfer_inventory_entity

    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return False

    ensure_merchant_initialized(game, level, merchant)

    minv = game.get_inventory(merchant_actor_id)  # type: ignore[attr-defined]
    if not (0 <= int(item_index) < len(minv)):
        return False

    ent = minv[int(item_index)]
    quote = quote_prices(merchant, ent)
    if quote is None:
        return False

    price = int(quote.buy_price)
    if price <= 0:
        try:
            game.log.add("That item is not for sale.")  # type: ignore[attr-defined]
        except Exception:
            pass
        return False

    player_money = _safe_int(getattr(game, "bismuth", 0), 0)
    if player_money < price:
        try:
            game.log.add("You don't have enough bismuth.")  # type: ignore[attr-defined]
        except Exception:
            pass
        return False

    # Move item with graph-op ownership updates.
    ent = minv[int(item_index)]
    transfer_inventory_entity(
        game,
        ent,
        src_inventory=minv,
        dst_inventory=_player_inventory(game),
        dst_owner_id=str(getattr(game, "player_id", "player")),
    )
    # Item-granted actions can appear/disappear when inventory contents change.
    try:
        if hasattr(game, "refresh_actor_actions"):
            game.refresh_actor_actions(getattr(game, "player_id", ""))  # type: ignore[attr-defined]
            game.refresh_actor_actions(merchant_actor_id)  # type: ignore[arg-type]
    except Exception:
        pass

    # Transfer funds
    try:
        game.adjust_currency(-price, log=False)  # type: ignore[attr-defined]
    except Exception:
        pass
    set_merchant_funds(merchant, merchant_funds(merchant) + price)

    try:
        game.log.add(f"You buy {ent.name} for {price} bismuth.")  # type: ignore[attr-defined]
    except Exception:
        pass
    return True


def proposal_summary_with_qty(
    game: Any,
    merchant_actor_id: str,
    buy_items: dict[str, int],  # entity_id -> quantity
    sell_items: dict[str, int],  # entity_id -> quantity
) -> ProposalSummary:
    """Calculate proposal totals with quantity support for stacked items."""
    from edgecaster.systems.inventory import get_quantity

    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return ProposalSummary(
            buy_total=0,
            sell_total=0,
            net_player=0,
            player_bismuth_before=_safe_int(getattr(game, "bismuth", 0), 0),
            player_bismuth_after=_safe_int(getattr(game, "bismuth", 0), 0),
            merchant_funds_before=0,
            merchant_funds_after=0,
            ok=False,
            reason="Merchant unavailable.",
        )

    ensure_merchant_initialized(game, level, merchant)

    player_before = _safe_int(getattr(game, "bismuth", 0), 0)
    merchant_before = merchant_funds(merchant)

    if not buy_items and not sell_items:
        return ProposalSummary(
            buy_total=0,
            sell_total=0,
            net_player=0,
            player_bismuth_before=player_before,
            player_bismuth_after=player_before,
            merchant_funds_before=merchant_before,
            merchant_funds_after=merchant_before,
            ok=True,
        )

    minv = list(game.get_inventory(merchant_actor_id))  # type: ignore[attr-defined]
    pinv = list(_player_inventory(game))

    by_id_minv = {getattr(ent, "id", ""): ent for ent in minv}
    by_id_pinv = {getattr(ent, "id", ""): ent for ent in pinv}

    buy_total = 0
    sell_total = 0
    reason: str | None = None

    for ent_id, qty in buy_items.items():
        ent = by_id_minv.get(ent_id)
        if ent is None:
            if reason is None:
                reason = "One or more items are no longer available."
            continue
        available = get_quantity(ent)
        if qty > available:
            if reason is None:
                reason = f"Not enough of {getattr(ent, 'name', 'item')} available."
            continue
        q = quote_prices(merchant, ent)
        if q is None or int(q.buy_price) <= 0:
            if reason is None:
                reason = "One or more proposed purchases are not for sale."
            continue
        buy_total += int(q.buy_price) * qty

    for ent_id, qty in sell_items.items():
        ent = by_id_pinv.get(ent_id)
        if ent is None:
            if reason is None:
                reason = "One or more items are no longer in your inventory."
            continue
        available = get_quantity(ent)
        if qty > available:
            if reason is None:
                reason = f"Not enough of {getattr(ent, 'name', 'item')} in inventory."
            continue
        if equipment_system.is_equipped(ent):
            if reason is None:
                reason = "Unequip items before selling them."
            continue
        q = quote_prices(merchant, ent)
        if q is None or int(q.sell_price) <= 0:
            if reason is None:
                reason = "The merchant isn't interested in one or more items."
            continue
        sell_total += int(q.sell_price) * qty

    net_player = int(sell_total - buy_total)
    player_after = player_before + net_player
    merchant_after = merchant_before - net_player

    ok = reason is None
    if ok and player_after < 0:
        ok = False
        reason = "You don't have enough bismuth."
    if ok and merchant_after < 0:
        ok = False
        reason = "The merchant can't afford that right now."

    return ProposalSummary(
        buy_total=int(buy_total),
        sell_total=int(sell_total),
        net_player=int(net_player),
        player_bismuth_before=int(player_before),
        player_bismuth_after=int(player_after),
        merchant_funds_before=int(merchant_before),
        merchant_funds_after=int(merchant_after),
        ok=bool(ok),
        reason=str(reason or ""),
    )


def apply_proposal_with_qty(
    game: Any,
    merchant_actor_id: str,
    buy_items: dict[str, int],  # entity_id -> quantity
    sell_items: dict[str, int],  # entity_id -> quantity
) -> tuple[bool, str]:
    """Execute a trade proposal with quantity support for stacked items."""
    from edgecaster.systems.inventory import (
        get_quantity,
        set_quantity,
    )
    from edgecaster.systems.entity_graph_ops import (
        transfer_inventory_entity,
        transfer_split_quantity,
    )

    summary = proposal_summary_with_qty(game, merchant_actor_id, buy_items, sell_items)
    if not (buy_items or sell_items):
        return True, ""
    if not summary.ok:
        return False, summary.reason or "Trade invalid."

    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return False, "Merchant unavailable."

    ensure_merchant_initialized(game, level, merchant)

    minv = game.get_inventory(merchant_actor_id)  # type: ignore[attr-defined]
    pinv = _player_inventory(game)

    by_id_minv = {getattr(ent, "id", ""): ent for ent in list(minv)}
    by_id_pinv = {getattr(ent, "id", ""): ent for ent in list(pinv)}

    # Process buys: move from merchant to player
    for ent_id, qty in buy_items.items():
        ent = by_id_minv.get(ent_id)
        if ent is None:
            continue
        available = get_quantity(ent)
        q = quote_prices(merchant, ent)
        price = int(q.buy_price) if q else 0

        if qty >= available:
            # Take entire stack
            transfer_inventory_entity(
                game,
                ent,
                src_inventory=minv,
                dst_inventory=pinv,
                dst_owner_id=str(getattr(game, "player_id", "player")),
            )
            try:
                game.log.add(f"You buy {ent.name} x{available} for {price * available} bismuth.")
            except Exception:
                pass
        else:
            # Split stack: reduce merchant's, create new for player
            set_quantity(ent, available - qty)
            bought = transfer_split_quantity(
                game,
                ent,
                qty=qty,
                split_kind="trade",
                dst_inventory=pinv,
                dst_owner_id=str(getattr(game, "player_id", "player")),
                spawn_pos=getattr(ent, "pos", (0, 0)),
                clear_equipped_tags=True,
            )
            try:
                game.log.add(f"You buy {ent.name} x{qty} for {price * qty} bismuth.")
            except Exception:
                pass

    # Process sells: move from player to merchant
    for ent_id, qty in sell_items.items():
        ent = by_id_pinv.get(ent_id)
        if ent is None:
            continue
        available = get_quantity(ent)
        q = quote_prices(merchant, ent)
        payout = int(q.sell_price) if q else 0

        if qty >= available:
            # Sell entire stack
            transfer_inventory_entity(
                game,
                ent,
                src_inventory=pinv,
                dst_inventory=minv,
                dst_owner_id=str(merchant_actor_id),
            )
            try:
                game.log.add(f"You sell {ent.name} x{available} for {payout * available} bismuth.")
            except Exception:
                pass
        else:
            # Split stack: reduce player's, create new for merchant
            set_quantity(ent, available - qty)
            sold = transfer_split_quantity(
                game,
                ent,
                qty=qty,
                split_kind="trade",
                dst_inventory=minv,
                dst_owner_id=str(merchant_actor_id),
                spawn_pos=getattr(ent, "pos", (0, 0)),
                clear_equipped_tags=True,
            )
            try:
                game.log.add(f"You sell {ent.name} x{qty} for {payout * qty} bismuth.")
            except Exception:
                pass

    # Transfer funds
    try:
        game.adjust_currency(int(summary.net_player), log=False)  # type: ignore[attr-defined]
    except Exception:
        try:
            game.bismuth = int(getattr(game, "bismuth", 0)) + int(summary.net_player)
        except Exception:
            pass

    set_merchant_funds(merchant, int(summary.merchant_funds_after))

    return True, ""


def try_sell(game: Any, merchant_actor_id: str, item_index: int) -> bool:
    from edgecaster.systems.entity_graph_ops import transfer_inventory_entity

    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return False

    ensure_merchant_initialized(game, level, merchant)

    pinv = _player_inventory(game)
    if not (0 <= int(item_index) < len(pinv)):
        return False

    ent = pinv[int(item_index)]
    if equipment_system.is_equipped(ent):
        try:
            game.log.add("You must unequip that item before selling it.")  # type: ignore[attr-defined]
        except Exception:
            pass
        return False
    quote = quote_prices(merchant, ent)
    if quote is None:
        return False

    payout = int(quote.sell_price)
    if payout <= 0:
        try:
            game.log.add("The merchant isn't interested in that.")  # type: ignore[attr-defined]
        except Exception:
            pass
        return False

    funds = merchant_funds(merchant)
    if funds < payout:
        try:
            game.log.add("The merchant can't afford that right now.")  # type: ignore[attr-defined]
        except Exception:
            pass
        return False

    # Move item with graph-op ownership updates.
    ent = pinv[int(item_index)]
    transfer_inventory_entity(
        game,
        ent,
        src_inventory=pinv,
        dst_inventory=game.get_inventory(merchant_actor_id),  # type: ignore[attr-defined]
        dst_owner_id=str(merchant_actor_id),
    )
    # Item-granted actions can appear/disappear when inventory contents change.
    try:
        if hasattr(game, "refresh_actor_actions"):
            game.refresh_actor_actions(getattr(game, "player_id", ""))  # type: ignore[attr-defined]
            game.refresh_actor_actions(merchant_actor_id)  # type: ignore[arg-type]
    except Exception:
        pass

    # Transfer funds
    try:
        game.adjust_currency(payout, log=False)  # type: ignore[attr-defined]
    except Exception:
        pass
    set_merchant_funds(merchant, funds - payout)

    try:
        game.log.add(f"You sell {ent.name} for {payout} bismuth.")  # type: ignore[attr-defined]
    except Exception:
        pass
    return True
