"""Shared runtime snapshot helpers for deterministic entities.

This module centralizes the transient mutable fields we persist for deterministic
entities that may collapse out of the active attention window and later be
rehydrated.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


PERSIST_TAG_KEYS: Tuple[str, ...] = (
    "quantity",
    "charges",
    "max_charges",
    "door_state",
    "locked",
    "broken",
    "opened",
)


def snapshot_patch_for_entity(obj: object) -> Dict[str, Any]:
    """Return the current persistence delta payload for a runtime entity."""
    patch: Dict[str, Any] = {}

    try:
        stats = getattr(obj, "stats", None)
    except Exception:
        stats = None
    if stats is not None:
        for src, dst in (
            ("hp", "hp"),
            ("max_hp", "max_hp"),
            ("mana", "mana"),
            ("max_mana", "max_mana"),
        ):
            try:
                raw = getattr(stats, src, None)
                if raw is not None:
                    patch[dst] = int(raw)
            except Exception:
                continue

    try:
        tags = getattr(obj, "tags", None)
    except Exception:
        tags = None
    if isinstance(tags, dict):
        kept: Dict[str, Any] = {}
        for key in PERSIST_TAG_KEYS:
            if key in tags:
                kept[str(key)] = tags.get(key)
        if kept:
            patch["tags_patch"] = kept

    try:
        glyph = getattr(obj, "glyph", None)
        if glyph is not None:
            patch["glyph"] = str(glyph)[:1]
    except Exception:
        pass
    try:
        patch["blocks_movement"] = bool(getattr(obj, "blocks_movement", False))
    except Exception:
        pass

    try:
        raw_statuses = getattr(obj, "statuses", None)
        if isinstance(raw_statuses, dict) and raw_statuses:
            patch["statuses"] = dict(raw_statuses)
    except Exception:
        pass
    try:
        raw_cooldowns = getattr(obj, "cooldowns", None)
        if isinstance(raw_cooldowns, dict) and raw_cooldowns:
            patch["cooldowns"] = dict(raw_cooldowns)
    except Exception:
        pass

    return patch


def persist_entity_snapshot(
    game: object,
    obj: object,
    *,
    entity_id: str,
) -> None:
    """Persist the current mutable runtime delta for an entity.

    Snapshot authority lives on ``entity_id``.
    """
    patch = snapshot_patch_for_entity(obj)
    if not patch:
        return
    try:
        fn = getattr(game, "patch_entity_state", None)
        if callable(fn):
            try:
                fn(str(entity_id), patch)
            except TypeError:
                fn(str(entity_id), patch)
    except Exception:
        pass


def apply_entity_snapshot(obj: object, state: Dict[str, Any]) -> bool:
    """Apply a persisted snapshot to a runtime object; False means suppress/remove."""
    if not isinstance(state, dict) or not state:
        return True
    if bool(state.get("removed")) or bool(state.get("dead")):
        return False

    try:
        tags = getattr(obj, "tags", None)
        if not isinstance(tags, dict):
            tags = {}
        last_tags = state.get("tags_patch")
        if isinstance(last_tags, dict):
            tags.update(dict(last_tags))
        setattr(obj, "tags", tags)
    except Exception:
        pass

    try:
        if "glyph" in state:
            setattr(obj, "glyph", str(state.get("glyph") or getattr(obj, "glyph", "?"))[:1] or "?")
    except Exception:
        pass
    try:
        if "blocks_movement" in state:
            setattr(obj, "blocks_movement", bool(state.get("blocks_movement")))
    except Exception:
        pass

    stats = getattr(obj, "stats", None)
    if stats is not None:
        try:
            if "max_hp" in state:
                stats.max_hp = max(1, int(state.get("max_hp", getattr(stats, "max_hp", 1)) or 1))
        except Exception:
            pass
        try:
            if "hp" in state:
                stats.hp = int(state.get("hp", getattr(stats, "hp", 0)) or 0)
        except Exception:
            pass
        try:
            if "max_mana" in state and hasattr(stats, "max_mana"):
                stats.max_mana = max(0, int(state.get("max_mana", getattr(stats, "max_mana", 0)) or 0))
        except Exception:
            pass
        try:
            if "mana" in state and hasattr(stats, "mana"):
                stats.mana = int(state.get("mana", getattr(stats, "mana", 0)) or 0)
        except Exception:
            pass
        try:
            if hasattr(stats, "clamp"):
                stats.clamp()
        except Exception:
            pass

    try:
        raw_statuses = state.get("statuses")
        if isinstance(raw_statuses, dict):
            setattr(obj, "statuses", dict(raw_statuses))
    except Exception:
        pass
    try:
        raw_cooldowns = state.get("cooldowns")
        if isinstance(raw_cooldowns, dict):
            setattr(obj, "cooldowns", dict(raw_cooldowns))
    except Exception:
        pass
    return True
