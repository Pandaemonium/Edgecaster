"""Tests for the channel reducer pipeline (Slice R1).

Verifies:
- Rules are loaded and parsed from chakra_rules.yaml.
- Channels propagate from parent nodes to children through automatic edges.
- Combine policies (additive, override, max, min) are applied correctly.
- Clamp bounds are enforced.
- Inactive nodes are excluded from propagation.
- Nodes with no edges (isolated) return their intrinsic channel values.
- Topological ordering handles chains and branching trees correctly.
"""
from __future__ import annotations

from edgecaster.state import chakra_component as chakra_component_state
from edgecaster.systems import chakra_reducer as chakra_reducer_system
from edgecaster.systems.chakra_reducer import (
    ChannelRule,
    ChakraRules,
    EdgeKindRule,
    reduce_component,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_comp(
    root_id: str,
    nodes: dict,  # {node_id: {active, channels}}
    edges: dict | None = None,  # {edge_id: {src, dst, kind}}
) -> chakra_component_state.ChakraComponent:
    comp_nodes = {}
    for nid, spec in nodes.items():
        comp_nodes[nid] = chakra_component_state.ChakraNode(
            node_id=nid,
            kind=spec.get("kind", "core"),
            active=bool(spec.get("active", True)),
            channels=dict(spec.get("channels", {})),
            tags={},
        )
    comp_edges = {}
    for eid, espec in (edges or {}).items():
        comp_edges[eid] = chakra_component_state.ChakraEdge(
            edge_id=eid,
            src_node_id=str(espec["src"]),
            dst_node_id=str(espec["dst"]),
            kind=str(espec.get("kind", "contains")),
            propagation="automatic",
        )
    return chakra_component_state.ChakraComponent(
        root_node_id=root_id,
        nodes=comp_nodes,
        edges=comp_edges,
    )


def _additive_rules() -> ChakraRules:
    """Rules with additive combine, no clamp."""
    return ChakraRules(
        channel_default=ChannelRule(combine="additive", clamp_min=None, clamp_max=None),
        per_channel={},
        edge_kinds={"contains": EdgeKindRule(propagation="automatic")},
    )


def _override_rules() -> ChakraRules:
    """Rules with override combine for 'coherence', additive default."""
    return ChakraRules(
        channel_default=ChannelRule(combine="additive"),
        per_channel={
            "coherence": ChannelRule(combine="override", clamp_min=0.0, clamp_max=1.0)
        },
        edge_kinds={"contains": EdgeKindRule(propagation="automatic")},
    )


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

def test_load_rules_returns_chakra_rules_instance() -> None:
    chakra_reducer_system.clear_rules_cache()
    rules = chakra_reducer_system.load_rules()
    assert isinstance(rules, ChakraRules)


def test_loaded_rules_have_known_channel_defaults() -> None:
    chakra_reducer_system.clear_rules_cache()
    rules = chakra_reducer_system.load_rules()
    # Default combine from chakra_rules.yaml is additive.
    assert rules.channel_default.combine == "additive"


def test_loaded_rules_parse_per_channel_overrides() -> None:
    chakra_reducer_system.clear_rules_cache()
    rules = chakra_reducer_system.load_rules()
    # coherence is override-combine per chakra_rules.yaml.
    coherence = rules.channel_rule("coherence")
    assert coherence.combine == "override"
    assert coherence.clamp_min == 0.0
    assert coherence.clamp_max == 1.0


def test_loaded_rules_parse_edge_kinds() -> None:
    chakra_reducer_system.clear_rules_cache()
    rules = chakra_reducer_system.load_rules()
    assert rules.edge_rule("contains").propagation == "automatic"
    assert rules.edge_rule("owns").propagation == "none"


# ---------------------------------------------------------------------------
# Isolated node (no edges)
# ---------------------------------------------------------------------------

def test_isolated_node_returns_intrinsic_channels() -> None:
    comp = _make_comp("root", {"root": {"channels": {"mass": 2.0, "hp": 10.0}}})
    result = reduce_component(comp, _additive_rules())
    assert result["root"] == {"mass": 2.0, "hp": 10.0}


def test_empty_component_returns_empty_dict() -> None:
    comp = _make_comp("root", {})
    result = reduce_component(comp, _additive_rules())
    assert result == {}


# ---------------------------------------------------------------------------
# Additive propagation
# ---------------------------------------------------------------------------

def test_additive_parent_value_flows_to_child() -> None:
    """Parent mass=3.0, child mass=1.0 → effective child mass = 4.0."""
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"mass": 3.0}},
            "child": {"channels": {"mass": 1.0}},
        },
        {"e1": {"src": "parent", "dst": "child", "kind": "contains"}},
    )
    result = reduce_component(comp, _additive_rules())
    assert result["parent"]["mass"] == 3.0
    assert result["child"]["mass"] == 4.0


def test_additive_chain_accumulates() -> None:
    """root→mid→leaf with mass=1.0 each → leaf effective = 3.0."""
    comp = _make_comp(
        "root",
        {
            "root": {"channels": {"mass": 1.0}},
            "mid": {"channels": {"mass": 1.0}},
            "leaf": {"channels": {"mass": 1.0}},
        },
        {
            "e1": {"src": "root", "dst": "mid"},
            "e2": {"src": "mid", "dst": "leaf"},
        },
    )
    result = reduce_component(comp, _additive_rules())
    assert result["root"]["mass"] == 1.0
    assert result["mid"]["mass"] == 2.0
    assert result["leaf"]["mass"] == 3.0


def test_additive_branching_tree() -> None:
    """root→left and root→right; each branch independent."""
    comp = _make_comp(
        "root",
        {
            "root": {"channels": {"mass": 5.0}},
            "left": {"channels": {"mass": 1.0}},
            "right": {"channels": {"mass": 2.0}},
        },
        {
            "e1": {"src": "root", "dst": "left"},
            "e2": {"src": "root", "dst": "right"},
        },
    )
    result = reduce_component(comp, _additive_rules())
    assert result["root"]["mass"] == 5.0
    assert result["left"]["mass"] == 6.0
    assert result["right"]["mass"] == 7.0


def test_parent_channel_not_in_child_is_inherited() -> None:
    """Parent has 'flow', child has none → child inherits flow value."""
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"flow": 0.8}},
            "child": {"channels": {}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, _additive_rules())
    assert result["child"]["flow"] == 0.8


# ---------------------------------------------------------------------------
# Override combine
# ---------------------------------------------------------------------------

def test_override_parent_replaces_child_value() -> None:
    """With override: parent coherence=0.7, child coherence=0.3 → child effective=0.7."""
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"coherence": 0.7}},
            "child": {"channels": {"coherence": 0.3}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, _override_rules())
    assert result["parent"]["coherence"] == 0.7
    assert result["child"]["coherence"] == 0.7


# ---------------------------------------------------------------------------
# Max combine
# ---------------------------------------------------------------------------

def test_max_combine_picks_higher_value() -> None:
    rules = ChakraRules(
        channel_default=ChannelRule(combine="additive"),
        per_channel={"resonance": ChannelRule(combine="max", clamp_min=0.0, clamp_max=1.0)},
        edge_kinds={"contains": EdgeKindRule(propagation="automatic")},
    )
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"resonance": 0.9}},
            "child": {"channels": {"resonance": 0.4}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, rules)
    assert result["child"]["resonance"] == 0.9


def test_max_combine_child_higher_keeps_child_value() -> None:
    rules = ChakraRules(
        channel_default=ChannelRule(combine="max"),
        per_channel={},
        edge_kinds={"contains": EdgeKindRule(propagation="automatic")},
    )
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"resonance": 0.2}},
            "child": {"channels": {"resonance": 0.8}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, rules)
    assert result["child"]["resonance"] == 0.8


# ---------------------------------------------------------------------------
# Clamp
# ---------------------------------------------------------------------------

def test_clamp_enforces_upper_bound() -> None:
    rules = ChakraRules(
        channel_default=ChannelRule(combine="additive", clamp_min=0.0, clamp_max=1.0),
        per_channel={},
        edge_kinds={"contains": EdgeKindRule(propagation="automatic")},
    )
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"corruption": 0.8}},
            "child": {"channels": {"corruption": 0.5}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, rules)
    # 0.8 + 0.5 = 1.3, clamped to 1.0.
    assert result["child"]["corruption"] == 1.0


def test_clamp_enforces_lower_bound() -> None:
    rules = ChakraRules(
        channel_default=ChannelRule(combine="additive", clamp_min=0.0, clamp_max=None),
        per_channel={},
        edge_kinds={"contains": EdgeKindRule(propagation="automatic")},
    )
    comp = _make_comp("root", {"root": {"channels": {"hp": -5.0}}})
    result = reduce_component(comp, rules)
    assert result["root"]["hp"] == 0.0


# ---------------------------------------------------------------------------
# Inactive nodes
# ---------------------------------------------------------------------------

def test_inactive_node_excluded_from_result() -> None:
    comp = _make_comp(
        "root",
        {
            "root": {"channels": {"mass": 1.0}},
            "dead": {"active": False, "channels": {"mass": 1.0}},
        },
        {"e1": {"src": "root", "dst": "dead"}},
    )
    result = reduce_component(comp, _additive_rules())
    assert "dead" not in result


def test_inactive_node_does_not_propagate_to_children() -> None:
    """root → inactive_mid → leaf; inactive_mid blocks propagation."""
    comp = _make_comp(
        "root",
        {
            "root": {"channels": {"mass": 5.0}},
            "inactive_mid": {"active": False, "channels": {"mass": 2.0}},
            "leaf": {"channels": {"mass": 1.0}},
        },
        {
            "e1": {"src": "root", "dst": "inactive_mid"},
            "e2": {"src": "inactive_mid", "dst": "leaf"},
        },
    )
    result = reduce_component(comp, _additive_rules())
    # leaf should only have its own mass since mid is inactive.
    assert result["leaf"]["mass"] == 1.0


# ---------------------------------------------------------------------------
# Edge propagation policies
# ---------------------------------------------------------------------------

def test_none_propagation_edge_does_not_carry_channels() -> None:
    rules = ChakraRules(
        channel_default=ChannelRule(combine="additive"),
        per_channel={},
        edge_kinds={
            "contains": EdgeKindRule(propagation="automatic"),
            "owns": EdgeKindRule(propagation="none"),
        },
    )
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"mass": 10.0}},
            "child": {"channels": {"mass": 1.0}},
        },
        {"e1": {"src": "parent", "dst": "child", "kind": "owns"}},
    )
    result = reduce_component(comp, rules)
    # "owns" edge does not propagate, so child keeps only its own mass.
    assert result["child"]["mass"] == 1.0


# ---------------------------------------------------------------------------
# Loaded rules integration
# ---------------------------------------------------------------------------

def test_reduce_with_loaded_rules_is_deterministic() -> None:
    chakra_reducer_system.clear_rules_cache()
    rules = chakra_reducer_system.load_rules()
    comp = _make_comp(
        "root",
        {
            "root": {"channels": {"mass": 1.0, "hp": 10.0}},
            "child": {"channels": {"mass": 0.5}},
        },
        {"e1": {"src": "root", "dst": "child"}},
    )
    result_a = reduce_component(comp, rules)
    result_b = reduce_component(comp, rules)
    assert result_a == result_b


# ---------------------------------------------------------------------------
# R2: dirty_node_ids skip
# ---------------------------------------------------------------------------

def test_dirty_node_ids_none_reduces_all_nodes() -> None:
    """Passing dirty_node_ids=None is equivalent to reducing all nodes."""
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"mass": 3.0}},
            "child": {"channels": {"mass": 1.0}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result_full = reduce_component(comp, _additive_rules(), dirty_node_ids=None)
    assert result_full["child"]["mass"] == 4.0


def test_dirty_node_ids_skips_clean_edge() -> None:
    """When neither parent nor child is in dirty_node_ids, propagation is skipped.

    The child's effective value stays at its intrinsic value (no parent
    contribution) because the edge is considered already settled.
    """
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"mass": 3.0}},
            "child": {"channels": {"mass": 1.0}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    # Neither parent nor child is dirty — propagation must be skipped.
    result = reduce_component(comp, _additive_rules(), dirty_node_ids=set())
    assert result["child"]["mass"] == 1.0


def test_dirty_node_ids_propagates_when_parent_dirty() -> None:
    """When parent is dirty, propagation into its children must still occur."""
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"mass": 3.0}},
            "child": {"channels": {"mass": 1.0}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, _additive_rules(), dirty_node_ids={"parent"})
    assert result["child"]["mass"] == 4.0


def test_dirty_node_ids_propagates_when_child_dirty() -> None:
    """When child is dirty, propagation from its parents must still occur."""
    comp = _make_comp(
        "parent",
        {
            "parent": {"channels": {"mass": 3.0}},
            "child": {"channels": {"mass": 1.0}},
        },
        {"e1": {"src": "parent", "dst": "child"}},
    )
    result = reduce_component(comp, _additive_rules(), dirty_node_ids={"child"})
    assert result["child"]["mass"] == 4.0
