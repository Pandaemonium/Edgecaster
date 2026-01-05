from __future__ import annotations

from typing import Any, Dict, Iterable


EQUIP_STAT_KEYS: tuple[str, ...] = ("con", "agi", "int", "res")


def is_equipped(ent: Any) -> bool:
    tags = getattr(ent, "tags", None) or {}
    try:
        return bool(tags.get("equipped_slot") or tags.get("equipped"))
    except Exception:
        return False


def collect_equip_mods(inventory: Iterable[Any]) -> Dict[str, int]:
    mods: Dict[str, int] = {k: 0 for k in EQUIP_STAT_KEYS}
    for ent in inventory:
        if not is_equipped(ent):
            continue
        tags = getattr(ent, "tags", None) or {}
        raw = tags.get("equip_mods")
        if not isinstance(raw, dict):
            continue
        for k in EQUIP_STAT_KEYS:
            if k not in raw:
                continue
            try:
                mods[k] += int(raw[k])
            except Exception:
                continue
    # Remove zeros for convenience.
    return {k: v for k, v in mods.items() if v}


def apply_mods(base: Dict[str, int], mods: Dict[str, int]) -> Dict[str, int]:
    out = dict(base)
    for k, dv in mods.items():
        try:
            out[k] = int(out.get(k, 0)) + int(dv)
        except Exception:
            out[k] = int(out.get(k, 0))
    return out

