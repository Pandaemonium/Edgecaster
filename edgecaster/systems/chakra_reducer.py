"""Channel reducer pipeline for ChakraComponent.

R1: Deterministic channel reduction over a ChakraComponent node graph.

Rules are loaded from `chakra_rules.yaml` and define:
- Per-channel defaults (clamp policy, combine policy)
- Per-edge-kind propagation behavior (automatic, none, opt_in)

The reducer computes effective channel values for every node by propagating
channel state through edges according to the combine and propagation rules.

Combine policies:
  additive   — parent + child values sum.  Leaf values accumulate up the tree.
  override   — parent value replaces child value when the parent sets it.
  max        — effective value is max(parent, child).
  min        — effective value is min(parent, child).

Propagation policies (per edge kind):
  automatic  — values flow across this edge without opt-in.
  none       — no propagation across this edge kind.
  opt_in     — propagation only when the edge or node explicitly enables it.

Nearest-parent override: a node that sets a channel value explicitly overrides
the inherited value from its ancestor.  This supports layered rule application
where leaf nodes can specialize without losing parent defaults.

Phase: R1 of the entity+chakra unification plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from edgecaster.state import chakra_component as chakra_component_state
from edgecaster.systems import chakra_content as chakra_content_system


# ---------------------------------------------------------------------------
# Rule dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChannelRule:
    """Reduction rules for a single channel."""
    combine: str = "additive"          # additive | override | max | min
    clamp_min: Optional[float] = None  # None = no lower bound
    clamp_max: Optional[float] = None  # None = no upper bound


@dataclass(frozen=True)
class EdgeKindRule:
    """Propagation rule for a single edge kind."""
    propagation: str = "automatic"  # automatic | none | opt_in


@dataclass
class ChakraRules:
    """Parsed representation of chakra_rules.yaml."""
    channel_default: ChannelRule = field(default_factory=ChannelRule)
    per_channel: Dict[str, ChannelRule] = field(default_factory=dict)
    edge_kinds: Dict[str, EdgeKindRule] = field(default_factory=dict)

    def channel_rule(self, channel_name: str) -> ChannelRule:
        return self.per_channel.get(str(channel_name), self.channel_default)

    def edge_rule(self, edge_kind: str) -> EdgeKindRule:
        return self.edge_kinds.get(str(edge_kind), EdgeKindRule(propagation="automatic"))


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

_RULES_INSTANCE: Optional[ChakraRules] = None


def _parse_rules(raw: Dict[str, Any]) -> ChakraRules:
    # Channel defaults
    ch_defaults_raw = (raw.get("channels") or {}).get("defaults") or {}
    default_clamp = ch_defaults_raw.get("clamp")
    default_clamp_min: Optional[float] = None
    default_clamp_max: Optional[float] = None
    if isinstance(default_clamp, (list, tuple)) and len(default_clamp) >= 2:
        try:
            default_clamp_min = float(default_clamp[0])
            default_clamp_max = float(default_clamp[1])
        except (TypeError, ValueError):
            pass
    default_channel_rule = ChannelRule(
        combine=str(ch_defaults_raw.get("combine") or "additive"),
        clamp_min=default_clamp_min,
        clamp_max=default_clamp_max,
    )

    # Per-channel overrides
    per_channel: Dict[str, ChannelRule] = {}
    for ch_name, ch_raw in ((raw.get("channels") or {}).get("per_channel") or {}).items():
        if not isinstance(ch_raw, dict):
            continue
        clamp_raw = ch_raw.get("clamp")
        c_min: Optional[float] = None
        c_max: Optional[float] = None
        if isinstance(clamp_raw, (list, tuple)) and len(clamp_raw) >= 2:
            try:
                c_min = float(clamp_raw[0])
                c_max = float(clamp_raw[1])
            except (TypeError, ValueError):
                pass
        elif str(clamp_raw or "").strip() == "none":
            pass  # no clamp
        per_channel[str(ch_name)] = ChannelRule(
            combine=str(ch_raw.get("combine") or default_channel_rule.combine),
            clamp_min=c_min,
            clamp_max=c_max,
        )

    # Edge kind rules
    edge_kinds: Dict[str, EdgeKindRule] = {}
    for ek_name, ek_raw in ((raw.get("edges") or {})).items():
        if not isinstance(ek_raw, dict):
            continue
        edge_kinds[str(ek_name)] = EdgeKindRule(
            propagation=str(ek_raw.get("propagation") or "automatic"),
        )

    return ChakraRules(
        channel_default=default_channel_rule,
        per_channel=per_channel,
        edge_kinds=edge_kinds,
    )


def load_rules(*, force_reload: bool = False) -> ChakraRules:
    """Load and cache ChakraRules from chakra_rules.yaml."""
    global _RULES_INSTANCE
    if _RULES_INSTANCE is None or force_reload:
        raw = chakra_content_system.chakra_rules()
        _RULES_INSTANCE = _parse_rules(raw if isinstance(raw, dict) else {})
    return _RULES_INSTANCE


def clear_rules_cache() -> None:
    global _RULES_INSTANCE
    _RULES_INSTANCE = None


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _build_node_graph(
    comp: chakra_component_state.ChakraComponent,
    *,
    allowed_propagation: frozenset[str],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """Build parent→children and child→parents adjacency from comp.edges.

    Only edges whose kind has a propagation policy in allowed_propagation are
    included.  This ensures the reducer only walks edges that carry channel
    state.

    Returns:
        (children_of, parents_of) — both keyed by node_id.
    """
    children_of: Dict[str, List[str]] = {}
    parents_of: Dict[str, List[str]] = {}
    for edge in (comp.edges or {}).values():
        kind = str(getattr(edge, "kind", "contains") or "contains")
        if kind not in allowed_propagation:
            continue
        src = str(getattr(edge, "src_node_id", "") or "")
        dst = str(getattr(edge, "dst_node_id", "") or "")
        if not src or not dst:
            continue
        children_of.setdefault(src, []).append(dst)
        parents_of.setdefault(dst, []).append(src)
    return children_of, parents_of


def _topological_order(
    nodes: List[str],
    children_of: Dict[str, List[str]],
) -> List[str]:
    """Return nodes in topological (root-first) order via Kahn's algorithm.

    Nodes not reachable from any root come last in an unspecified order.
    Cycles are handled by treating cycle-closing edges as absent.
    """
    in_degree: Dict[str, int] = {n: 0 for n in nodes}
    for src, dsts in children_of.items():
        for dst in dsts:
            if dst in in_degree:
                in_degree[dst] += 1

    queue = sorted(n for n, d in in_degree.items() if d == 0)
    order: List[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for child in sorted(children_of.get(node, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
    # Append any remaining (cycle members) in stable order.
    remaining = sorted(n for n in nodes if n not in set(order))
    order.extend(remaining)
    return order


# ---------------------------------------------------------------------------
# Reduction
# ---------------------------------------------------------------------------

def _apply_clamp(value: float, rule: ChannelRule) -> float:
    if rule.clamp_min is not None:
        value = max(rule.clamp_min, value)
    if rule.clamp_max is not None:
        value = min(rule.clamp_max, value)
    return value


def _combine(parent_val: float, child_val: float, combine: str) -> float:
    if combine == "additive":
        return parent_val + child_val
    if combine == "override":
        # Nearest-parent override: parent value replaces child.
        return parent_val
    if combine == "max":
        return max(parent_val, child_val)
    if combine == "min":
        return min(parent_val, child_val)
    # Unknown combine policy: additive as safe default.
    return parent_val + child_val


def reduce_component(
    comp: chakra_component_state.ChakraComponent,
    rules: Optional[ChakraRules] = None,
    *,
    dirty_node_ids: Optional[set] = None,
) -> Dict[str, Dict[str, float]]:
    """Compute effective channel values for all nodes in comp.

    Propagates channel state from parents to children through edges whose
    kind has propagation="automatic" (or whatever the rules allow).  The
    effective value for a channel on a given node is determined by combining
    its intrinsic value with the inherited value from its nearest active
    ancestor, using the channel's combine policy.

    Args:
        comp: The ChakraComponent to reduce.
        rules: Optional ChakraRules; loaded from chakra_rules.yaml if None.
        dirty_node_ids: Optional set of node_ids that are dirty (R2 integration).
            When provided, only nodes whose node_id appears in this set (or whose
            ancestors are dirty) participate in full reduction; all other nodes
            are still included in the result but use their intrinsic values
            without re-propagation from parents.  Pass None to reduce all nodes
            unconditionally (default, equivalent to the pre-R2 behavior).

    Returns:
        {node_id: {channel_name: effective_value}}
        Only active nodes are included.  Inactive nodes are excluded from
        propagation as well.
    """
    if rules is None:
        rules = load_rules()

    # Build allowed-propagation set.
    allowed: set[str] = set()
    for kind, edge_rule in rules.edge_kinds.items():
        if edge_rule.propagation == "automatic":
            allowed.add(kind)
    # "contains" is automatic by default even if not in rules.
    allowed.add("contains")
    allowed_frozen = frozenset(allowed)

    nodes = comp.nodes or {}
    if not nodes:
        return {}

    active_nodes = {
        nid: node for nid, node in nodes.items()
        if bool(getattr(node, "active", True))
    }
    if not active_nodes:
        return {}

    children_of, parents_of = _build_node_graph(comp, allowed_propagation=allowed_frozen)

    # Restrict adjacency to active nodes only.
    active_children_of: Dict[str, List[str]] = {
        nid: [c for c in children_of.get(nid, []) if c in active_nodes]
        for nid in active_nodes
    }

    order = _topological_order(list(active_nodes.keys()), active_children_of)

    # Seed effective values with intrinsic channel values.
    effective: Dict[str, Dict[str, float]] = {}
    for nid in active_nodes:
        node = active_nodes[nid]
        ch = dict(getattr(node, "channels", {}) or {})
        try:
            effective[nid] = {k: float(v) for k, v in ch.items()}
        except (TypeError, ValueError):
            effective[nid] = {}

    # Propagate parent values to children in topological order.
    # When dirty_node_ids is provided, skip propagation into subtrees that
    # have no dirty nodes — those subtrees are already at their correct
    # effective values from a previous reduction pass.
    for nid in order:
        for child_id in active_children_of.get(nid, []):
            # R2 dirty-flag skip: if the caller supplied a dirty set and
            # neither the parent nor the child is dirty, propagation into
            # this child from this parent would produce the same result as
            # the previous pass — skip it.
            if dirty_node_ids is not None:
                if nid not in dirty_node_ids and child_id not in dirty_node_ids:
                    continue
            for ch_name, parent_val in effective.get(nid, {}).items():
                rule = rules.channel_rule(ch_name)
                child_channels = effective.setdefault(child_id, {})
                if ch_name in child_channels:
                    # Child has its own value — combine with parent.
                    combined = _combine(parent_val, child_channels[ch_name], rule.combine)
                else:
                    # Child inherits parent value.
                    combined = parent_val
                child_channels[ch_name] = _apply_clamp(combined, rule)

    # Apply clamp pass on root nodes (intrinsic values were never clamped).
    for nid in active_nodes:
        for ch_name in list(effective.get(nid, {})):
            rule = rules.channel_rule(ch_name)
            effective[nid][ch_name] = _apply_clamp(effective[nid][ch_name], rule)

    return effective
