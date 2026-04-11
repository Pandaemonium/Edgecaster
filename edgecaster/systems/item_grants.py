"""Item-granted ability helpers.

Some items grant actions to the player while they are held or equipped.
This module provides a unified tag-schema parser (`get_item_grants`) that
reads the relevant tags from an entity and returns a list of
`(action_name, grant_mode)` pairs.

Grant modes:
- `"held"`: the action is available while the item is in the player's
  inventory (does not need to be in an equip slot).
- `"equipped"`: the action is available only when the item is actively
  equipped in a matching slot.

Supported tag schema (best-effort, backwards-compatible):
- `tags.grants_ability`: single string → held
- `tags.grants_abilities`: list → mode from `tags.grant_requires` (default held)
- `tags.grants_actions`: list → same as grants_abilities
- `tags.equip_grants_abilities`: list → always equipped
- `tags.grant_requires`: `"held"` | `"equipped"` override for the above lists

Callers (typically `systems/action_runner.py` and `systems/spawning.py`)
collect these grants at spawn/equip time and register them on the actor's
`actions` list.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple

from edgecaster.systems import equipment as equipment_system

GrantMode = str  # "held" | "equipped"

GRANT_MODE_HELD: GrantMode = "held"
GRANT_MODE_EQUIPPED: GrantMode = "equipped"


def _as_str_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for x in raw:
        if not x:
            continue
        out.append(str(x))
    return out


def get_item_grants(ent: Any) -> List[Tuple[str, GrantMode]]:
    """Return a list of (action_name, grant_mode) pairs for an item entity.

    Supported tag schema (best-effort / backwards compatible):
    - tags.grants_ability: "destabilize"                      -> held
    - tags.grants_abilities: ["ignite", "freeze"]             -> tags.grant_requires (default held)
    - tags.grants_actions: ["..."]                            -> tags.grant_requires (default held)
    - tags.equip_grants_abilities: ["..."]                    -> equipped
    - tags.grant_requires: "held" | "equipped"                -> mode for grants_abilities/grants_actions
    """
    tags = getattr(ent, "tags", None) or {}

    pairs: List[Tuple[str, GrantMode]] = []

    # Legacy single ability: always "held".
    legacy = tags.get("grants_ability")
    if legacy:
        pairs.append((str(legacy), GRANT_MODE_HELD))

    default_mode = str(tags.get("grant_requires") or GRANT_MODE_HELD).lower()
    if default_mode not in (GRANT_MODE_HELD, GRANT_MODE_EQUIPPED):
        default_mode = GRANT_MODE_HELD

    for action in _as_str_list(tags.get("grants_abilities")):
        pairs.append((action, default_mode))
    for action in _as_str_list(tags.get("grants_actions")):
        pairs.append((action, default_mode))

    # Explicit equip-only grants.
    for action in _as_str_list(tags.get("equip_grants_abilities")):
        pairs.append((action, GRANT_MODE_EQUIPPED))

    # Deduplicate while preserving order.
    out: List[Tuple[str, GrantMode]] = []
    seen: set[Tuple[str, GrantMode]] = set()
    for action, mode in pairs:
        key = (str(action), str(mode))
        if key in seen:
            continue
        seen.add(key)
        out.append((key[0], key[1]))
    return out


def is_grant_active(ent: Any, mode: GrantMode) -> bool:
    if mode == GRANT_MODE_EQUIPPED:
        return equipment_system.is_equipped(ent)
    return True  # held


def collect_active_granted_actions(inventory: Iterable[Any]) -> List[str]:
    """Collect active granted action names from an inventory."""
    out: List[str] = []
    for ent in inventory:
        grants = get_item_grants(ent)
        for action, mode in grants:
            is_active = is_grant_active(ent, mode)
            if not is_active:
                continue
            out.append(action)
    return out


def find_grant_origin(inventory: Iterable[Any], action_name: str) -> Optional[Any]:
    """Return the inventory entity that is currently granting `action_name`, if any.

    Preference order:
    1) Equipped items granting the action (for wand-style abilities)
    2) Any held item granting the action
    """
    action_name = str(action_name)
    items = list(inventory)

    # Prefer equipped origins.
    for ent in items:
        for action, mode in get_item_grants(ent):
            if action != action_name:
                continue
            if mode != GRANT_MODE_EQUIPPED:
                continue
            if is_grant_active(ent, mode):
                return ent

    # Fallback: any active grant.
    for ent in items:
        for action, mode in get_item_grants(ent):
            if action != action_name:
                continue
            if is_grant_active(ent, mode):
                return ent

    return None

