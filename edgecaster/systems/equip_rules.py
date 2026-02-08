"""Slot-constraint checks for item equipping.

This module centralizes slot compatibility policy so inventory/equipment UIs
do not each invent their own ad-hoc checks.

Current policy is tag-driven and intentionally simple:
- ``equip_slots`` / ``allowed_slots``: exact slot ids allowed.
- ``blocked_slots``: exact slot ids denied.
- ``equip_slot_prefixes`` / ``allowed_slot_prefixes``: slot path prefixes
  allowed (``arm`` allows ``arm:hand``, ``arm:hand:thumb``, etc.).
- ``blocked_slot_prefixes``: slot path prefixes denied.
- ``allowed_slot_kinds``: required slot segments (``hand``, ``foot`` ...).
- ``blocked_slot_kinds``: denied slot segments.
"""

from __future__ import annotations

from typing import Any, Iterable, Tuple


def _to_lower_list(raw: Any) -> list[str]:
    """Normalize common tag value shapes into a lowercase string list."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for v in raw:
            s = str(v or "").strip().lower()
            if s:
                out.append(s)
        return out
    text = str(raw).strip()
    if not text:
        return []
    # Accept comma-delimited strings in YAML tags for convenience.
    return [p.strip().lower() for p in text.split(",") if p.strip()]


def _normalize_slot_path(slot_id: str) -> str:
    """Normalize slot path delimiters to forward slashes."""
    return str(slot_id or "").strip().lower().replace(":", "/")


def _slot_prefix_match(slot_id: str, prefix: str) -> bool:
    """Return True when ``slot_id`` is the prefix itself or under it."""
    sid = _normalize_slot_path(slot_id)
    pfx = _normalize_slot_path(prefix)
    if not pfx:
        return False
    return sid == pfx or sid.startswith(pfx + "/")


def _slot_kinds(slot_id: str) -> set[str]:
    """Return slot path segments for kind-level filters."""
    sid = _normalize_slot_path(slot_id)
    out: set[str] = set()
    for p in sid.split("/"):
        seg = p.strip()
        if not seg:
            continue
        out.add(seg)
        # Mirror slots typically end with "_m". Include base token too so
        # kind filters like "hand" match both hand and hand_m.
        if seg.endswith("_m"):
            out.add(seg[:-2])
    return out


def can_equip_item_in_slot(item: Any, slot_id: str) -> Tuple[bool, str]:
    """Return ``(allowed, reason)`` for item->slot compatibility.

    ``reason`` is empty when allowed.
    """
    sid = _normalize_slot_path(slot_id)
    if not sid:
        return False, "Invalid equipment slot."

    tags = getattr(item, "tags", None) or {}

    allowed_exact = set(
        _to_lower_list(tags.get("equip_slots")) + _to_lower_list(tags.get("allowed_slots"))
    )
    blocked_exact = set(_to_lower_list(tags.get("blocked_slots")))

    allowed_prefix = set(
        _to_lower_list(tags.get("equip_slot_prefixes"))
        + _to_lower_list(tags.get("allowed_slot_prefixes"))
    )
    blocked_prefix = set(_to_lower_list(tags.get("blocked_slot_prefixes")))

    allowed_kinds = set(_to_lower_list(tags.get("allowed_slot_kinds")))
    blocked_kinds = set(_to_lower_list(tags.get("blocked_slot_kinds")))

    # Explicit deny wins.
    if sid in blocked_exact:
        return False, "That item cannot be equipped in this slot."
    for pfx in blocked_prefix:
        if _slot_prefix_match(sid, pfx):
            return False, "That item cannot be equipped in this slot."

    # Explicit allow lists.
    if allowed_exact and sid not in allowed_exact:
        return False, "That item does not fit this slot."
    if allowed_prefix:
        ok = False
        for pfx in allowed_prefix:
            if _slot_prefix_match(sid, pfx):
                ok = True
                break
        if not ok:
            return False, "That item does not fit this slot."

    kinds = _slot_kinds(sid)
    if blocked_kinds and (kinds & blocked_kinds):
        return False, "That item cannot be equipped on that body part."
    if allowed_kinds and not (kinds & allowed_kinds):
        return False, "That item cannot be equipped on that body part."

    return True, ""
