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


def _body_node_entity_id(actor: Any, node_id: str) -> str:
    """Return the deterministic body-node entity id for an actor chakra node."""
    actor_id = _actor_entity_id(actor)
    nid = _normalize_node_id(node_id)
    if not actor_id or not nid:
        return ""
    return f"{actor_id}:body:{nid}"


def _mark_actor_chakra_dirty(game: Any, actor: Any, node_id: str) -> None:
    """Mark a mutated chakra path dirty in the entity graph when available."""
    graph = getattr(game, "entity_graph", None)
    if graph is None:
        return

    body_entity_id = _body_node_entity_id(actor, node_id)
    if body_entity_id:
        try:
            if graph.get_node(body_entity_id) is not None:
                graph.mark_dirty_up(body_entity_id)
                return
        except Exception:
            pass

    actor_id = _actor_entity_id(actor)
    if actor_id:
        try:
            graph.mark_dirty_up(actor_id)
        except Exception:
            pass


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


def _is_compat_component_node(node: Any) -> bool:
    """Return True for actor-side compat nodes mirrored from ChakraState."""
    if node is None:
        return False
    if isinstance(node, dict):
        kind = str(node.get("kind") or "")
        tags = node.get("tags")
    else:
        kind = str(getattr(node, "kind", "") or "")
        tags = getattr(node, "tags", None)
    if kind == "compat":
        return True
    return isinstance(tags, dict) and bool(tags.get("compat_unlocked"))


def _remove_component_node(comp: Any, node_id: str) -> None:
    """Delete a compat node plus any incident edges from a ChakraComponent."""
    nodes = getattr(comp, "nodes", None)
    if isinstance(nodes, dict):
        nodes.pop(str(node_id), None)
    edges = getattr(comp, "edges", None)
    if not isinstance(edges, dict):
        return
    doomed_edge_ids: list[str] = []
    for edge_id, edge in edges.items():
        if isinstance(edge, dict):
            src = str(edge.get("src_node_id") or "")
            dst = str(edge.get("dst_node_id") or "")
        else:
            src = str(getattr(edge, "src_node_id", "") or "")
            dst = str(getattr(edge, "dst_node_id", "") or "")
        if src == str(node_id) or dst == str(node_id):
            doomed_edge_ids.append(str(edge_id))
    for edge_id in doomed_edge_ids:
        edges.pop(edge_id, None)


def _rebuild_chakra_state_from_component(actor: Any) -> Any:
    """Build a fresh ChakraState view from ChakraComponent data when available.

    Runtime readers should prefer this over the cached ``actor.chakra_state``
    facade so entity/component writes become visible immediately even before the
    remaining Phase 8 callers stop touching the legacy cache directly.
    """
    try:
        from edgecaster.systems.chakras import ChakraState
    except Exception:
        return None

    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return None

    root = _baseline_chakra_root(actor)
    unlocked, active = _component_node_sets(actor)
    charges = _component_charge_map(comp)
    alignments, generators, pattern_root = _component_state_overrides(comp)

    if root:
        unlocked.add(root)
        active.add(root)
    if pattern_root and pattern_root not in active:
        pattern_root = None

    return ChakraState(
        unlocked=set(unlocked),
        active=set(active),
        alignments=dict(alignments),
        generators=dict(generators),
        charges=dict(charges),
        pattern_root=pattern_root,
    )



def apply_chakra_state_snapshot(actor: Any, state: Any, *, game: Any = None) -> None:
    """Apply a full ChakraState snapshot to the actor's ChakraComponent.

    Used for character-class initialisation paths that produce a ChakraState
    object to apply (e.g. Monk setup from ``chakra_init`` dict).  Routes
    through the public wrapper functions so dirty-flag propagation happens
    correctly.  Does NOT mutate ``state`` — reads from it and writes to the
    component.
    """
    if state is None:
        return
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return

    unlocked = _normalize_nodes(getattr(state, "unlocked", set()) or set())
    active = _normalize_nodes(getattr(state, "active", set()) or set())
    managed_node_ids = set(unlocked)
    managed_node_ids.update(active)
    root = _baseline_chakra_root(actor)
    if root:
        unlocked.add(root)
        active.add(root)
        managed_node_ids.add(root)

    charge_values: dict[str, float] = {}
    charges_raw = getattr(state, "charges", {}) or {}
    if isinstance(charges_raw, dict):
        for key, value in charges_raw.items():
            nid = _normalize_node_id(str(key))
            if not nid:
                continue
            try:
                charge_values[nid] = float(value)
                managed_node_ids.add(nid)
            except Exception:
                continue

    # Ensure every unlocked node exists in the component and has correct
    # active state.  We call the public wrappers so B2 mirrors and dirty
    # flags are applied even for bulk restores.
    for nid in sorted(managed_node_ids):
        is_active = nid in active
        if nid not in comp.nodes:
            unlock_actor_chakra(actor, nid, auto_activate=is_active, game=game)
        else:
            toggle_actor_chakra(actor, nid, active=is_active, game=game)

    # Full-state restore means compat nodes absent from the snapshot should
    # disappear instead of lingering after undo/redo or bootstrap restores.
    existing_nodes = getattr(comp, "nodes", None)
    if isinstance(existing_nodes, dict):
        stale_compat_ids = [
            str(node_id)
            for node_id, node in existing_nodes.items()
            if _is_compat_component_node(node) and _normalize_node_id(str(node_id)) not in managed_node_ids
        ]
        for nid in stale_compat_ids:
            _remove_component_node(comp, nid)
            if game is not None:
                _mark_actor_chakra_dirty(game, actor, nid)

    # Clear stale compat charges before applying the new snapshot values.
    nodes = getattr(comp, "nodes", None)
    if isinstance(nodes, dict):
        for nid in sorted(managed_node_ids):
            node = nodes.get(nid)
            if node is None:
                continue
            channels = getattr(node, "channels", None)
            if not isinstance(channels, dict):
                continue
            if nid not in charge_values:
                channels.pop("charge", None)

    # Apply charges.
    for nid, value in sorted(charge_values.items()):
        try:
            set_actor_chakra_charge(actor, nid, value, game=game)
        except Exception:
            pass

    # Write compat metadata tags for pattern root, alignments, generators.
    tags = getattr(comp, "tags", None)
    if not isinstance(tags, dict):
        tags = {}
        try:
            comp.tags = tags
        except Exception:
            pass

    tags["compat_unlocked_nodes"] = sorted(unlocked)
    tags["compat_active_nodes"] = sorted(active)
    pattern_root_raw = _normalize_node_id(str(getattr(state, "pattern_root", "") or ""))
    tags["compat_pattern_root"] = pattern_root_raw if pattern_root_raw in active else None

    alignments_raw = getattr(state, "alignments", {}) or {}
    alignments_out: dict = {}
    if isinstance(alignments_raw, dict):
        for k, v in alignments_raw.items():
            nid = _normalize_node_id(str(k))
            if nid and isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    alignments_out[nid] = [float(v[0]), float(v[1])]
                except Exception:
                    pass
    tags["compat_alignments"] = alignments_out

    gens_raw = getattr(state, "generators", {}) or {}
    gens_out: dict = {}
    if isinstance(gens_raw, dict):
        for k, v in gens_raw.items():
            nid = _normalize_node_id(str(k))
            if nid:
                sval = str(v or "").strip()
                if sval:
                    gens_out[nid] = sval
    tags["compat_generators"] = gens_out


def tick_actor_chakra_charge(
    actor: Any,
    game: Any,
    delta: int,
    *,
    charging: bool,
    dex: int = 0,
) -> None:
    """Component-backed charge tick.  ChakraComponent is the read/write authority.

    Active nodes are determined from ``effective_active_nodes`` so item
    auto-activations are included.

    [ENTITY_CHAKRA][PHASE_3]
    """
    if delta <= 0:
        return
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return

    try:
        from edgecaster.systems.chakras import (
            check_resonance_bonuses_from_active_nodes,
            get_resonance_modifiers,
            CHARGE_GAIN_PER_TICK,
            CHARGE_DECAY_PER_TICK,
            CHARGE_MAX_BASE,
        )
        from edgecaster.state import chakra_component as chakra_component_state
    except Exception:
        return

    active = effective_active_nodes(game, actor)
    bonuses = check_resonance_bonuses_from_active_nodes(active)
    mods = get_resonance_modifiers(bonuses)

    gain = CHARGE_GAIN_PER_TICK * mods.charge_gain_mult
    decay = CHARGE_DECAY_PER_TICK
    if dex > 0:
        gain *= 1.0 + float(dex) * 0.01
    cap = CHARGE_MAX_BASE + mods.charge_cap_bonus

    # Normalize active IDs to the form used in component nodes.
    normalized_active = _normalize_nodes(active)

    dirtied: set[str] = set()
    all_node_ids = list(getattr(comp, "nodes", {}).keys())
    if charging:
        for nid in normalized_active:
            cur = chakra_component_state.get_node_charge(comp, nid)
            new_val = min(cap, cur + gain * delta)
            chakra_component_state.set_node_charge(comp, nid, new_val)
            dirtied.add(nid)
        # Decay nodes not in the active set.
        for nid in all_node_ids:
            if nid not in normalized_active:
                cur = chakra_component_state.get_node_charge(comp, nid)
                if cur > 0.0:
                    new_val = max(0.0, cur - decay * delta)
                    chakra_component_state.set_node_charge(comp, nid, new_val)
                    dirtied.add(nid)
    else:
        # No charging state: decay all nodes.
        for nid in all_node_ids:
            cur = chakra_component_state.get_node_charge(comp, nid)
            if cur > 0.0:
                new_val = max(0.0, cur - decay * delta)
                chakra_component_state.set_node_charge(comp, nid, new_val)
                dirtied.add(nid)

    # Active pattern channeling already forces reducer recomputation every tick,
    # so re-dirtying the expanded body subtree here only adds graph-walk churn.
    if game is not None and not charging:
        for nid in sorted(dirtied):
            _mark_actor_chakra_dirty(game, actor, nid)


def set_actor_chakra_charge(actor: Any, node_id: str, amount: float, game: Any = None) -> None:
    """Set one chakra node charge. ChakraComponent is the write authority."""
    nid = _normalize_node_id(str(node_id))
    if not nid:
        return
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return
    chakra_component_state.set_node_charge(comp, nid, float(amount))
    if game is not None:
        _mark_actor_chakra_dirty(game, actor, nid)


def restore_actor_chakra_component_snapshot(
    actor: Any,
    snap_dict: dict,
    *,
    game: Any = None,
) -> None:
    """Restore a full ChakraComponent from a snapshot dict.

    Used by the chakra-scene undo system.  Assigns the restored component to
    the actor, rebuilds the ChakraState view, and fires dirty marks for every
    node in the restored component so the renderer refreshes.

    [ENTITY_CHAKRA][PHASE_3]
    """
    try:
        from edgecaster.state.chakra_component import ChakraComponent as _CC
        restored = _CC.from_dict(snap_dict)
        actor.chakra_component = restored
    except Exception:
        return
    state = _rebuild_chakra_state_from_component(actor)
    if state is not None:
        try:
            actor.chakra_state = state
        except Exception:
            pass
    if game is not None:
        try:
            nodes = getattr(restored, "nodes", None)
            if isinstance(nodes, dict):
                for nid in sorted(nodes):
                    _mark_actor_chakra_dirty(game, actor, nid)
        except Exception:
            pass


def consume_actor_chakra_charge(actor: Any, amount: float, game: Any = None) -> None:
    """Deduct ``amount`` from every active chakra node.  ChakraComponent is the write authority.

    [ENTITY_CHAKRA][PHASE_3]
    """
    if amount <= 0:
        return
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return
    active = effective_active_nodes(game, actor)
    if not active:
        return
    normalized_active = _normalize_nodes(active)
    for nid in normalized_active:
        cur = chakra_component_state.get_node_charge(comp, nid)
        new_val = max(0.0, cur - amount)
        chakra_component_state.set_node_charge(comp, nid, new_val)
        if game is not None:
            _mark_actor_chakra_dirty(game, actor, nid)


def get_actor_average_charge(game: Any, actor: Any) -> float:
    """Return average charge across all active nodes, reading from ChakraComponent.

    Used as a component-backed fallback when the reducer snapshot is absent.

    [ENTITY_CHAKRA][PHASE_3]
    """
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return 0.0
    active = effective_active_nodes(game, actor)
    if not active:
        return 0.0
    normalized_active = _normalize_nodes(active)
    vals = [chakra_component_state.get_node_charge(comp, nid) for nid in normalized_active]
    return sum(vals) / max(1, len(vals))


def _write_active_to_body_node_entity(game: Any, actor: Any, nid: str, *, active: bool) -> None:
    """B2: Mirror active-state write to the realized body-node entity when present.

    The body-node entity's chakra_component root node is the long-term authority
    for that node's active state.  This write is best-effort and silent on
    failure so it never blocks the actor-level write above it.
    """
    try:
        from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
        # Body entity IDs are formed as "{actor_id}:body:{full_node_id}".
        # _normalize_node_id converts ":" → "." so we use the raw node_id string.
        body_entity_id = _body_node_entity_id(actor, nid)
        body_ent = entity_lifecycle_system.find_runtime_entity(game, body_entity_id)
        if body_ent is None:
            return
        body_comp = getattr(body_ent, "chakra_component", None)
        if body_comp is None:
            return
        body_comp = chakra_component_state.coerce_chakra_component(
            body_comp, entity_id=str(body_entity_id)
        )
        # The body entity's root node represents the whole body-node entity.
        root_nid = str(getattr(body_comp, "root_node_id", "") or "")
        if root_nid:
            chakra_component_state.set_node_active(body_comp, root_nid, active=active)
        # Keep coerced comp on the entity so subsequent reads see the mutation.
        try:
            body_ent.chakra_component = body_comp
        except Exception:
            pass
    except Exception:
        pass


def unlock_actor_chakra(actor: Any, node_id: str, *, auto_activate: bool = True, game: Any = None) -> bool:
    """Unlock chakra node on actor. ChakraComponent is the write authority.

    Presence in comp.nodes is the canonical definition of unlocked. Returns
    True when the node is newly added, False when already present.

    Pass `game` to also mirror the active state onto the body-node entity when
    the entity tree has been realized (B2).
    """
    nid = _normalize_node_id(str(node_id))
    if not nid:
        return False
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return False
    # Component is authority: if node already present, nothing to do.
    newly_added = chakra_component_state.unlock_node(comp, nid, active=bool(auto_activate))
    if not newly_added:
        return False
    # Keep cached ChakraState in sync when present.
    state = getattr(actor, "chakra_state", None)
    if state is not None:
        unlocked = getattr(state, "unlocked", None)
        if isinstance(unlocked, set):
            unlocked.add(nid)
        if auto_activate:
            active_set = getattr(state, "active", None)
            if isinstance(active_set, set):
                active_set.add(nid)
    # B2: Mirror active state to body-node entity when tree is realized.
    if game is not None:
        _write_active_to_body_node_entity(game, actor, nid, active=bool(auto_activate))
        _mark_actor_chakra_dirty(game, actor, nid)
    return True


def toggle_actor_chakra(actor: Any, node_id: str, *, active: Optional[bool] = None, game: Any = None) -> bool:
    """Toggle/activate/deactivate chakra node. ChakraComponent is the write authority.

    Pass `game` to also mirror the active state onto the body-node entity when
    the entity tree has been realized (B2).
    """
    nid = _normalize_node_id(str(node_id))
    if not nid:
        return False
    comp = _coerce_actor_chakra_component(actor)
    if comp is None:
        return False
    # Compat: node may be in legacy ChakraState.unlocked but not yet in component.
    # Backfill it so subsequent writes have a target.
    if nid not in comp.nodes:
        state_compat = getattr(actor, "chakra_state", None)
        if state_compat is not None:
            state_unlocked = _normalize_nodes(getattr(state_compat, "unlocked", set()) or set())
            if nid in state_unlocked:
                chakra_component_state.unlock_node(comp, nid, active=False)
    if nid not in comp.nodes:
        return False
    node = comp.nodes[nid]
    now_active = (not bool(getattr(node, "active", True))) if active is None else bool(active)
    chakra_component_state.set_node_active(comp, nid, active=now_active)
    # Keep cached ChakraState in sync when present.
    state = getattr(actor, "chakra_state", None)
    if state is not None:
        active_set = getattr(state, "active", None)
        if isinstance(active_set, set):
            if now_active:
                active_set.add(nid)
            else:
                active_set.discard(nid)
    # B2: Mirror active state to body-node entity when tree is realized.
    if game is not None:
        _write_active_to_body_node_entity(game, actor, nid, active=now_active)
        _mark_actor_chakra_dirty(game, actor, nid)
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
    """Return actor unlocked chakras including temporary equipped unlocks.

    Reads directly from the component node list rather than building a full
    ChakraState, so this is safe to call on hot paths like the per-tick charge
    loop without per-tick ChakraState allocation overhead.
    """
    unlocked, _ = _component_node_sets(actor)
    root = _baseline_chakra_root(actor)
    if root:
        unlocked.add(root)
    unlocked.update(temporary_unlocked_nodes(game, str(getattr(actor, "id", ""))))
    return unlocked


def effective_active_nodes(game: Any, actor: Any) -> set[str]:
    """Return active chakras including explicit item auto-activations.

    Reads directly from the component node list rather than building a full
    ChakraState, so this is safe to call on hot paths like the per-tick charge
    loop without per-tick ChakraState allocation overhead.
    """
    _, active = _component_node_sets(actor)
    root = _baseline_chakra_root(actor)
    if root:
        active.add(root)
    active.update(auto_active_nodes(game, str(getattr(actor, "id", ""))))
    return active


def effective_chakra_state(game: Any, actor: Any) -> Any:
    """Return an ephemeral ChakraState that includes equipped-item effects.

    The returned state is *not* persisted; it is used as a runtime view for
    systems that need a coherent unlocked/active snapshot without mutating the
    actor's stored chakra state.

    [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
    Replace this temporary projected ChakraState with a graph/component query
    result once items, bodies, and other entity hierarchies all share one
    runtime evaluation path.
    """
    # Unification note: this currently projects item effects onto legacy actor
    # state. The final version should build the effective view from graph edges
    # plus ChakraComponent channels so items, limbs, buildings, and sites all
    # use the same evaluation path.
    # _rebuild_chakra_state_from_component always returns a non-None state in
    # normal operation. See effective_unlocked_nodes for the reasoning.
    state = _rebuild_chakra_state_from_component(actor)
    if state is None:
        return None

    actor_id = str(getattr(actor, "id", ""))
    base_unlocked = _normalize_nodes(getattr(state, "unlocked", set()) or set())
    base_active = _normalize_nodes(getattr(state, "active", set()) or set())

    # Layer item effects on top of the base state.  Avoid calling
    # effective_unlocked_nodes / effective_active_nodes here — those would each
    # call _component_node_sets again, tripling the node-iteration cost.
    unlocked = set(base_unlocked)
    unlocked.update(temporary_unlocked_nodes(game, actor_id))
    active = set(base_active)
    active.update(auto_active_nodes(game, actor_id))
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
