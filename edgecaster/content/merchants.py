from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Dict, List

import yaml


@dataclass(frozen=True)
class StockEntry:
    proto: str
    weight: float = 1.0
    qty_min: int = 1
    qty_max: int = 1


@dataclass(frozen=True)
class MerchantDef:
    id: str
    name: str
    buy_multiplier: float = 1.25
    sell_multiplier: float = 0.75
    restock_interval_ticks: int = 400
    max_stock: int = 12
    max_funds_bismuth: int = 80
    stock: List[StockEntry] = field(default_factory=list)


def _load_merchants() -> Dict[str, MerchantDef]:
    path = pathlib.Path(__file__).resolve().parent / "merchants.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, MerchantDef] = {}
    for mid, spec in data.items():
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or mid)
        buy_mult = float(spec.get("buy_multiplier", 1.25) or 1.25)
        sell_mult = float(spec.get("sell_multiplier", 0.75) or 0.75)
        restock = int(spec.get("restock_interval_ticks", 400) or 400)
        max_stock = int(spec.get("max_stock", 12) or 12)
        max_funds = int(spec.get("max_funds_bismuth", 80) or 80)

        entries: List[StockEntry] = []
        for row in (spec.get("stock") or []):
            if not isinstance(row, dict):
                continue
            proto = str(row.get("proto") or row.get("id") or "")
            if not proto:
                continue
            weight = float(row.get("weight", 1.0) or 1.0)
            qty = row.get("qty") or [1, 1]
            try:
                qty_min = int(qty[0])
                qty_max = int(qty[1])
            except Exception:
                qty_min = 1
                qty_max = 1
            qty_min = max(1, qty_min)
            qty_max = max(qty_min, qty_max)
            entries.append(StockEntry(proto=proto, weight=weight, qty_min=qty_min, qty_max=qty_max))

        out[str(mid)] = MerchantDef(
            id=str(mid),
            name=name,
            buy_multiplier=max(0.01, buy_mult),
            sell_multiplier=max(0.0, sell_mult),
            restock_interval_ticks=max(1, restock),
            max_stock=max(0, max_stock),
            max_funds_bismuth=max(0, max_funds),
            stock=entries,
        )
    return out


MERCHANTS: Dict[str, MerchantDef] = _load_merchants()
