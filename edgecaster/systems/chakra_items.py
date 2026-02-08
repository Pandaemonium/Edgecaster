"""Item-driven chakra unlocks and hit modifiers.

This module keeps chakra-related item behavior in one place:
- temporary chakra unlock/activation while equipped
- additive/multiplicative per-hit damage modifiers for chakra-aware actions

The intent is to avoid scattering item-tag parsing across combat and pattern
runtime modules while staying data-driven for content authoring.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence, Set

from edgecaster.systems import equipment as equipment_system

# Debug throttle: actor_id -> last effective-state signature.
_LAST_EFFECTIVE_SIG: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {}


def _debug(game: Any, msg: str) -> None:
    """Best-effort debug log helper (silent when unavailable)."""
    try:
        dbg = getattr(game, "_debug", None)
        if callable(dbg):
            dbg(msg)
    except Exception:
        pass


def _to_list(raw: Any) -> list[str]:
    """Normalize YAML-ish scalars/lists to a clean string list."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for v in raw:
            s = str(v or "").strip()
            if s:
                out.append(s)
        return out
    text = str(raw).strip()
    if not text:
        return []
    return [p.strip() for p in text.split(",") if p.strip()]


def _to_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except Exception:
        return float(default)


def _normalize_node_id(node_id: str) -> str:
    """Normalize chakra node id to canonical dotted lowercase form."""
    n = str(node_id or "").strip().lower()
    if not n:
        return ""
    # Chakra slots may use ':' or '/' paths; chakra nodes use '.' paths.
    n = n.replace(":", ".")
    n = n.replace("/", ".")
    # Keep mirror semantics explicit; we do not auto-collide mirrored ids here.
    return n


def _normalize_nodes(nodes: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for n in nodes:
        nn = _normalize_node_id(str(n))
        if nn:
            out.add(nn)
    return out


def _node_tokens(node_id: str) -> set[str]:
    """Tokenize a node id for tag-based matching."""
    out: set[str] = set()
    n = _normalize_node_id(node_id)
    if not n:
        return out
    for tok in n.split("."):
        t = tok.strip()
        if not t:
            continue
        out.add(t)
        if t.endswith("_m"):
            out.add(t[:-2])
    return out


def _equipped_slot_node(item: Any) -> str:
    """Return normalized node id for an equipped slot tag, if any."""
    tags = getattr(item, "tags", None) or {}
    sid = str(tags.get("equipped_slot") or tags.get("equipped") or "").strip()
    return _normalize_node_id(sid)


def equipped_items(game: Any, actor_id: str) -> list[Any]:
    """Return all equipped inventory items for actor."""
    try:
        inv = game.get_inventory(str(actor_id))
    except Exception:
        inv = []
    out: list[Any] = []
    for it in inv:
        try:
            if equipment_system.is_equipped(it):
                out.append(it)
        except Exception:
            continue
    return out


def _item_temporary_unlocks(item: Any) -> set[str]:
    """Return chakra nodes temporarily unlocked by a single equipped item."""
    tags = getattr(item, "tags", None) or {}
    out: set[str] = set()

    if bool(tags.get("chakra_unlock_equipped_slot", False)):
        slot_node = _equipped_slot_node(item)
        if slot_node:
            out.add(slot_node)

    out.update(_normalize_nodes(_to_list(tags.get("chakra_unlock_nodes"))))
    out.update(_normalize_nodes(_to_list(tags.get("chakra_unlock_chakras"))))
    out.update(_normalize_nodes(_to_list(tags.get("chakra_unlock_node"))))
    return out


def temporary_unlocked_nodes(game: Any, actor_id: str) -> set[str]:
    """Union of temporary chakra unlocks granted by equipped items."""
    out: set[str] = set()
    for item in equipped_items(game, actor_id):
        out.update(_item_temporary_unlocks(item))
    return out


def auto_active_nodes(game: Any, actor_id: str) -> set[str]:
    """Chakra nodes auto-activated while items are equipped.

    This is explicit/opt-in via item tags. No implicit fallback activation.
    """
    out: set[str] = set()
    for item in equipped_items(game, actor_id):
        tags = getattr(item, "tags", None) or {}
        auto = bool(tags.get("chakra_auto_activate", False))
        auto_slot = bool(tags.get("chakra_auto_activate_equipped_slot", False))
        # Explicit node/chakra lists are opt-in auto-activations and should
        # apply without requiring chakra_auto_activate=true.
        explicit = _normalize_nodes(_to_list(tags.get("chakra_auto_activate_nodes")))
        explicit.update(_normalize_nodes(_to_list(tags.get("chakra_auto_activate_chakras"))))
        out.update(explicit)

        if not auto and not auto_slot:
            continue
        if auto_slot:
            slot_node = _equipped_slot_node(item)
            if slot_node:
                out.add(slot_node)
        if auto:
            out.update(_item_temporary_unlocks(item))
    return out


def effective_unlocked_nodes(game: Any, actor: Any) -> set[str]:
    """Return actor unlocked chakras including temporary equipped unlocks."""
    state = getattr(actor, "chakra_state", None)
    base = _normalize_nodes(getattr(state, "unlocked", set()) or set())
    base.update(temporary_unlocked_nodes(game, str(getattr(actor, "id", ""))))
    return base


def effective_active_nodes(game: Any, actor: Any) -> set[str]:
    """Return active chakras including explicit item auto-activations."""
    state = getattr(actor, "chakra_state", None)
    active = _normalize_nodes(getattr(state, "active", set()) or set())
    active.update(auto_active_nodes(game, str(getattr(actor, "id", ""))))
    return active


def effective_chakra_state(game: Any, actor: Any) -> Any:
    """Return an ephemeral ChakraState that includes equipped-item effects.

    The returned state is *not* persisted; it is used as a runtime view for
    systems that need a coherent unlocked/active snapshot without mutating the
    actor's stored chakra state.
    """
    state = getattr(actor, "chakra_state", None)
    if state is None:
        return None

    actor_id = str(getattr(actor, "id", ""))
    base_unlocked = _normalize_nodes(getattr(state, "unlocked", set()) or set())
    base_active = _normalize_nodes(getattr(state, "active", set()) or set())
    unlocked = effective_unlocked_nodes(game, actor)
    active = effective_active_nodes(game, actor)
    # Active must always be a subset of unlocked in the effective view.
    unlocked.update(active)

    # Preserve chosen root only when still active in the effective set.
    pattern_root = getattr(state, "pattern_root", None)
    if pattern_root not in active:
        pattern_root = None

    try:
        from edgecaster.systems.chakras import ChakraState

        eff = ChakraState(
            unlocked=set(unlocked),
            active=set(active),
            alignments=dict(getattr(state, "alignments", {}) or {}),
            generators=dict(getattr(state, "generators", {}) or {}),
            charges=dict(getattr(state, "charges", {}) or {}),
            pattern_root=pattern_root,
        )
        # Log only when effective unlocked/active state changes, to avoid spam.
        try:
            sig = (
                tuple(sorted(eff.unlocked)),
                tuple(sorted(eff.active)),
                str(getattr(eff, "pattern_root", "") or ""),
            )
            prev = _LAST_EFFECTIVE_SIG.get(actor_id)
            if sig != prev:
                _LAST_EFFECTIVE_SIG[actor_id] = sig
                _debug(
                    game,
                    f"[chakra_items] effective actor={actor_id} unlocked={len(eff.unlocked)} active={len(eff.active)} "
                    f"item_unlocked={sorted(unlocked - base_unlocked)} item_active={sorted(active - base_active)} root={eff.pattern_root!r}",
                )
        except Exception:
            pass
        return eff
    except Exception:
        _debug(game, f"[chakra_items] effective_chakra_state fallback actor={actor_id}")
        # Graceful fallback for call sites that can operate on the original state.
        return state


def _iter_hit_mod_entries(item: Any) -> list[dict[str, Any]]:
    """Yield normalized chakra hit modifier entries for one item."""
    tags = getattr(item, "tags", None) or {}
    raw = tags.get("chakra_hit_mods")
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    return []


def _action_matches(entry: dict[str, Any], action_name: str) -> bool:
    actions = _to_list(entry.get("actions"))
    if not actions:
        actions = _to_list(entry.get("action"))
    if not actions:
        return True
    name = str(action_name or "").strip().lower()
    for a in actions:
        aa = str(a).strip().lower()
        if aa in ("*", "any", "all"):
            return True
        if aa == name:
            return True
    return False


def _entry_node_filters(entry: dict[str, Any], item: Any) -> tuple[set[str], set[str], set[str], bool]:
    """Return (exact, prefixes, tokens, descendants) for filter matching."""
    exact = _normalize_nodes(
        _to_list(entry.get("nodes"))
        + _to_list(entry.get("chakras"))
        + _to_list(entry.get("chakra_nodes"))
    )
    prefixes = _normalize_nodes(
        _to_list(entry.get("prefixes"))
        + _to_list(entry.get("chakra_prefixes"))
    )
    tokens = _normalize_nodes(
        _to_list(entry.get("tokens"))
        + _to_list(entry.get("chakra_tokens"))
    )
    descendants = bool(entry.get("descendants", True))

    # Optional shorthand: bind this modifier to the equipped slot node.
    if bool(entry.get("use_equipped_slot", False)):
        slot_node = _equipped_slot_node(item)
        if slot_node:
            exact.add(slot_node)
    return exact, prefixes, tokens, descendants


def _node_filter_match(
    node_id: str,
    *,
    exact: set[str],
    prefixes: set[str],
    tokens: set[str],
    descendants: bool,
) -> bool:
    n = _normalize_node_id(node_id)
    if not n:
        return False

    if n in exact:
        return True
    if descendants:
        for e in exact:
            if n.startswith(e + "."):
                return True

    for p in prefixes:
        if n == p or n.startswith(p + "."):
            return True

    if tokens and (_node_tokens(n) & tokens):
        return True
    return False


def _entry_matches_nodes(
    entry: dict[str, Any],
    item: Any,
    source_nodes: set[str],
    illuminated_nodes: set[str],
) -> bool:
    match_mode = str(entry.get("match", "either")).strip().lower()
    if match_mode == "source":
        nodes = source_nodes
    elif match_mode in ("illuminated", "target", "lit"):
        nodes = illuminated_nodes
    else:
        nodes = source_nodes | illuminated_nodes

    exact, prefixes, tokens, descendants = _entry_node_filters(entry, item)
    if not exact and not prefixes and not tokens:
        # Action-only modifier with no chakra filter.
        return True
    if not nodes:
        return False
    return any(
        _node_filter_match(
            nid,
            exact=exact,
            prefixes=prefixes,
            tokens=tokens,
            descendants=descendants,
        )
        for nid in nodes
    )


def apply_damage_modifiers(
    game: Any,
    actor_id: str,
    action_name: str,
    base_damage: int,
    *,
    source_nodes: Optional[Iterable[str]] = None,
    illuminated_nodes: Optional[Iterable[str]] = None,
) -> int:
    """Apply equipped chakra hit modifiers and return integer damage.

    Stacking semantics:
    - ``op=add``: additive
    - ``op=mul``: multiplicative
    - ``op=max``: lower bound floor
    """
    dmg = float(max(0, int(base_damage)))
    source = _normalize_nodes(source_nodes or ())
    lit = _normalize_nodes(illuminated_nodes or ())

    for item in equipped_items(game, actor_id):
        for entry in _iter_hit_mod_entries(item):
            if not _action_matches(entry, action_name):
                continue
            if not _entry_matches_nodes(entry, item, source, lit):
                continue
            op = str(entry.get("op", "add")).strip().lower()
            value = _to_float(entry.get("value", 0.0), 0.0)
            if op in ("mul", "multiply", "x"):
                dmg *= value
            elif op in ("max", "floor"):
                dmg = max(dmg, value)
            else:
                # Default/unknown op -> additive.
                dmg += value

    return int(max(0, math.ceil(dmg)))
