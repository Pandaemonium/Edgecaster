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

from edgecaster.state import chakra_component as chakra_component_state
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


def _baseline_chakra_root(actor: Any) -> str:
    """Infer a stable default chakra root for actors missing legacy state."""
    try:
        body_schema = getattr(actor, "body_schema", None)
    except Exception:
        body_schema = None
    if isinstance(body_schema, dict):
        root = _normalize_node_id(str(body_schema.get("root", "") or ""))
        if root:
            return root

    try:
        comp = getattr(actor, "chakra_component", None)
    except Exception:
        comp = None
    if comp is not None:
        root = _normalize_node_id(str(getattr(comp, "root_node_id", "") or ""))
        if root:
            return root

    return "body"


def _component_node_sets(actor: Any) -> tuple[set[str], set[str]]:
    """Extract normalized (unlocked, active) node ids from chakra_component."""
    try:
        comp = getattr(actor, "chakra_component", None)
    except Exception:
        comp = None
    if comp is None:
        return (set(), set())

    if isinstance(comp, dict):
        nodes_raw = comp.get("nodes")
    else:
        nodes_raw = getattr(comp, "nodes", None)
    if not isinstance(nodes_raw, dict):
        return (set(), set())

    unlocked: set[str] = set()
    active: set[str] = set()
    for key, node in nodes_raw.items():
        node_id = ""
        is_active = True
        if isinstance(node, dict):
            node_id = str(node.get("node_id") or key or "")
            is_active = bool(node.get("active", True))
        else:
            node_id = str(getattr(node, "node_id", "") or key or "")
            is_active = bool(getattr(node, "active", True))
        nid = _normalize_node_id(node_id)
        if not nid:
            continue
        unlocked.add(nid)
        if is_active:
            active.add(nid)
    return (unlocked, active)


def _actor_entity_id(actor: Any) -> str:
    try:
        eid = str(getattr(actor, "entity_id", "") or "").strip()
    except Exception:
        eid = ""
    if eid:
        return eid
    try:
        aid = str(getattr(actor, "id", "") or "").strip()
    except Exception:
        aid = ""
    return aid or "entity:unknown"


def _actor_max_hp(actor: Any) -> Optional[float]:
    try:
        stats = getattr(actor, "stats", None)
        if stats is not None:
            return float(getattr(stats, "max_hp", None))
    except Exception:
        return None
    return None


def _coerce_actor_chakra_component(actor: Any) -> Any:
    """Return actor.chakra_component as a typed ChakraComponent."""
    raw = getattr(actor, "chakra_component", None)
    if raw is not None and not isinstance(
        raw,
        (dict, chakra_component_state.ChakraComponent),
    ):
        return raw
    try:
        comp = chakra_component_state.coerce_chakra_component(
            raw,
            entity_id=_actor_entity_id(actor),
            max_hp=_actor_max_hp(actor),
            mass=1.0,
        )
    except Exception:
        return None
    try:
        actor.chakra_component = comp
    except Exception:
        pass
    return comp


def _component_charge_map(comp: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if comp is None:
        return out
    nodes_raw = getattr(comp, "nodes", None)
    if not isinstance(nodes_raw, dict):
        return out
    for key, node in nodes_raw.items():
        if isinstance(node, dict):
            node_id = str(node.get("node_id") or key or "")
            channels = node.get("channels")
        else:
            node_id = str(getattr(node, "node_id", "") or key or "")
            channels = getattr(node, "channels", None)
        if not isinstance(channels, dict):
            continue
        nid = _normalize_node_id(node_id)
        if not nid:
            continue
        raw = channels.get("charge", channels.get("chakra_charge"))
        try:
            out[nid] = float(raw)
        except Exception:
            continue
    return out


def _component_state_overrides(comp: Any) -> tuple[dict[str, tuple[float, float]], dict[str, str], Optional[str]]:
    alignments: dict[str, tuple[float, float]] = {}
    generators: dict[str, str] = {}
    pattern_root: Optional[str] = None
    if comp is None:
        return (alignments, generators, pattern_root)
    tags = getattr(comp, "tags", None)
    if not isinstance(tags, dict):
        return (alignments, generators, pattern_root)

    raw_align = tags.get("compat_alignments")
    if isinstance(raw_align, dict):
        for key, value in raw_align.items():
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            nid = _normalize_node_id(str(key))
            if not nid:
                continue
            try:
                alignments[nid] = (float(value[0]), float(value[1]))
            except Exception:
                continue

    raw_gens = tags.get("compat_generators")
    if isinstance(raw_gens, dict):
        for key, value in raw_gens.items():
            nid = _normalize_node_id(str(key))
            if not nid:
                continue
            try:
                sval = str(value or "").strip()
            except Exception:
                sval = ""
            if sval:
                generators[nid] = sval

    raw_root = tags.get("compat_pattern_root")
    if raw_root is not None:
        root = _normalize_node_id(str(raw_root))
        if root:
            pattern_root = root
    return (alignments, generators, pattern_root)


def ensure_actor_chakra_state(actor: Any) -> Any:
    """Return legacy ChakraState, creating a baseline state when missing."""
    # Unification note: this is a compatibility adapter for actor-only callers.
    # The long-term target is ChakraComponent-backed runtime state for every
    # entity, with ChakraState surviving only as a thin facade or disappearing.
    state = getattr(actor, "chakra_state", None)
    if state is not None:
        return state

    try:
        from edgecaster.systems.chakras import ChakraState
    except Exception:
        return None

    comp = _coerce_actor_chakra_component(actor)
    root = _baseline_chakra_root(actor)
    unlocked, active = _component_node_sets(actor)
    charges = _component_charge_map(comp)
    alignments, generators, pattern_root = _component_state_overrides(comp)
    unlocked.add(root)
    if not active:
        active = {root}
    else:
        active.add(root)
    if pattern_root and pattern_root not in active:
        pattern_root = None
    state = ChakraState(
        unlocked=set(unlocked),
        active=set(active),
        alignments=dict(alignments),
        generators=dict(generators),
        charges=dict(charges),
        pattern_root=pattern_root,
    )
    try:
        actor.chakra_state = state
    except Exception:
        pass
    sync_actor_chakra_state(actor)
    return state


def sync_actor_chakra_state(actor: Any) -> None:
    """Mirror legacy ChakraState into ChakraComponent for migration cutover."""
    # Unification note: keep this mirror narrow. New geometry, propagation, and
    # reducer semantics should live on ChakraComponent/rule evaluation, not as
    # ever-growing compat_* payloads.
    state = getattr(actor, "chakra_state", None)
    if state is None:
        return
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return
    nodes = getattr(comp, "nodes", None)
    if not isinstance(nodes, dict):
        return

    unlocked = _normalize_nodes(getattr(state, "unlocked", set()) or set())
    active = _normalize_nodes(getattr(state, "active", set()) or set())
    root = _baseline_chakra_root(actor)
    if root:
        unlocked.add(root)
        active.add(root)
    charges_raw = getattr(state, "charges", {}) or {}
    charges: dict[str, float] = {}
    if isinstance(charges_raw, dict):
        for key, value in charges_raw.items():
            nid = _normalize_node_id(str(key))
            if not nid:
                continue
            try:
                charges[nid] = float(value)
            except Exception:
                continue

    key_by_node_id: dict[str, str] = {}
    for key, node in nodes.items():
        if isinstance(node, dict):
            node_id = str(node.get("node_id") or key or "")
        else:
            node_id = str(getattr(node, "node_id", "") or key or "")
        nid = _normalize_node_id(node_id)
        if nid and nid not in key_by_node_id:
            key_by_node_id[nid] = str(key)

    for nid in sorted(unlocked | set(charges.keys())):
        node_key = key_by_node_id.get(nid)
        if node_key is None:
            node_key = nid
            nodes[node_key] = chakra_component_state.ChakraNode(
                node_id=nid,
                kind="compat",
                active=(nid in active),
                channels={},
                tags={"compat_unlocked": True},
            )
            key_by_node_id[nid] = node_key

        node = nodes[node_key]
        if isinstance(node, dict):
            channels = node.get("channels")
            if not isinstance(channels, dict):
                channels = {}
                node["channels"] = channels
            node["node_id"] = str(node.get("node_id") or nid)
            node["active"] = bool(nid in active)
            if nid in charges:
                channels["charge"] = float(charges[nid])
            else:
                channels.pop("charge", None)
                channels.pop("chakra_charge", None)
        else:
            node.node_id = str(getattr(node, "node_id", "") or nid)
            node.active = bool(nid in active)
            if not isinstance(getattr(node, "channels", None), dict):
                node.channels = {}
            if nid in charges:
                node.channels["charge"] = float(charges[nid])
            else:
                node.channels.pop("charge", None)
                node.channels.pop("chakra_charge", None)

    tags = getattr(comp, "tags", None)
    if not isinstance(tags, dict):
        tags = {}
        try:
            comp.tags = tags
        except Exception:
            pass
    tags["compat_unlocked_nodes"] = sorted(unlocked)
    tags["compat_active_nodes"] = sorted(active)
    root_choice = _normalize_node_id(str(getattr(state, "pattern_root", "") or ""))
    tags["compat_pattern_root"] = root_choice if root_choice in active else None

    alignments_raw = getattr(state, "alignments", {}) or {}
    alignments_out: dict[str, list[float]] = {}
    if isinstance(alignments_raw, dict):
        for key, value in alignments_raw.items():
            nid = _normalize_node_id(str(key))
            if not nid:
                continue
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            try:
                alignments_out[nid] = [float(value[0]), float(value[1])]
            except Exception:
                continue
    tags["compat_alignments"] = alignments_out

    gens_raw = getattr(state, "generators", {}) or {}
    gens_out: dict[str, str] = {}
    if isinstance(gens_raw, dict):
        for key, value in gens_raw.items():
            nid = _normalize_node_id(str(key))
            if not nid:
                continue
            sval = str(value or "").strip()
            if sval:
                gens_out[nid] = sval
    tags["compat_generators"] = gens_out


def set_actor_chakra_charge(actor: Any, node_id: str, amount: float) -> None:
    """Set one chakra node charge in legacy+component state."""
    state = ensure_actor_chakra_state(actor)
    if state is None:
        return
    nid = _normalize_node_id(str(node_id))
    if not nid:
        return
    try:
        state.charges[nid] = float(amount)
    except Exception:
        return
    sync_actor_chakra_state(actor)


def unlock_actor_chakra(actor: Any, node_id: str, *, auto_activate: bool = True) -> bool:
    """Unlock chakra node on actor state and mirror to ChakraComponent."""
    state = ensure_actor_chakra_state(actor)
    if state is None:
        return False
    try:
        from edgecaster.systems import chakras as chakra_system
    except Exception:
        return False
    try:
        changed = bool(chakra_system.unlock_chakra(state, str(node_id), auto_activate=bool(auto_activate)))
    except Exception:
        return False
    if changed:
        sync_actor_chakra_state(actor)
    return changed


def toggle_actor_chakra(actor: Any, node_id: str, *, active: Optional[bool] = None) -> bool:
    """Toggle/activate/deactivate chakra node and mirror to ChakraComponent."""
    state = ensure_actor_chakra_state(actor)
    if state is None:
        return False
    try:
        from edgecaster.systems import chakras as chakra_system
    except Exception:
        return False
    try:
        now_active = bool(chakra_system.toggle_chakra_active(state, str(node_id), active=active))
    except Exception:
        return False
    sync_actor_chakra_state(actor)
    return now_active


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
    state = ensure_actor_chakra_state(actor)
    base = _normalize_nodes(getattr(state, "unlocked", set()) or set())
    base.update(temporary_unlocked_nodes(game, str(getattr(actor, "id", ""))))
    return base


def effective_active_nodes(game: Any, actor: Any) -> set[str]:
    """Return active chakras including explicit item auto-activations."""
    state = ensure_actor_chakra_state(actor)
    active = _normalize_nodes(getattr(state, "active", set()) or set())
    active.update(auto_active_nodes(game, str(getattr(actor, "id", ""))))
    return active


def effective_chakra_state(game: Any, actor: Any) -> Any:
    """Return an ephemeral ChakraState that includes equipped-item effects.

    The returned state is *not* persisted; it is used as a runtime view for
    systems that need a coherent unlocked/active snapshot without mutating the
    actor's stored chakra state.
    """
    # Unification note: this currently projects item effects onto legacy actor
    # state. The final version should build the effective view from graph edges
    # plus ChakraComponent channels so items, limbs, buildings, and sites all
    # use the same evaluation path.
    state = ensure_actor_chakra_state(actor)
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
