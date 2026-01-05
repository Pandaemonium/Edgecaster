from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from edgecaster.content import merchants as merchant_content


MERCHANT_ID_TAG = "merchant_id"
MERCHANT_FUNDS_TAG = "merchant_funds_bismuth"
MERCHANT_INITIALIZED_TAG = "merchant_initialized"


@dataclass(frozen=True)
class PriceQuote:
    base_value: int
    buy_price: int
    sell_price: int


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


def restock_merchant(game: Any, level: Any, merchant_actor: Any, *, force: bool) -> None:
    mdef = merchant_def_from_actor(merchant_actor)
    if mdef is None:
        return

    inv = game.get_inventory(getattr(merchant_actor, "id"))  # type: ignore[attr-defined]

    # Refill funds each restock.
    set_merchant_funds(merchant_actor, int(mdef.max_funds_bismuth))

    if force:
        inv.clear()

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
            inv.append(item)


def try_buy(game: Any, merchant_actor_id: str, item_index: int) -> bool:
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

    # Move item
    ent = minv.pop(int(item_index))
    game.player_inventory.append(ent)  # type: ignore[attr-defined]

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


def try_sell(game: Any, merchant_actor_id: str, item_index: int) -> bool:
    level = game._level()  # type: ignore[attr-defined]
    merchant = getattr(level, "actors", {}).get(merchant_actor_id)
    if merchant is None:
        return False

    ensure_merchant_initialized(game, level, merchant)

    pinv = game.player_inventory  # type: ignore[attr-defined]
    if not (0 <= int(item_index) < len(pinv)):
        return False

    ent = pinv[int(item_index)]
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

    # Move item
    ent = pinv.pop(int(item_index))
    game.get_inventory(merchant_actor_id).append(ent)  # type: ignore[attr-defined]

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
