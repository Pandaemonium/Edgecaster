from __future__ import annotations

from types import SimpleNamespace

from edgecaster.state import chakra_component as chakra_component_state
from edgecaster.state.entities import Entity
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems import attention
from edgecaster.systems import chakras as chakra_system
from edgecaster.systems import entity_geometry as entity_geometry_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system


class _DummyGame:
    def __init__(self) -> None:
        self.cfg = SimpleNamespace(world_width=64, world_height=64, seed=456)
        self.entity_graph = EntityGraphStore()
        self.entity_state: dict[str, dict] = {}
        self.attn_store = attention.AttentionCellStore(bin_size=16)
        self.levels: dict[tuple[int, int, int], object] = {}
        self._expanded_entity_children: dict[str, set[str]] = {}

    def patch_entity_state(self, entity_or_id, patch=None, *, lineage_id=None, **fields) -> None:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if isinstance(patch, dict):
            state.update(dict(patch))
        if fields:
            state.update(dict(fields))
        if lineage_id:
            state["lineage_id"] = str(lineage_id)
        self.entity_state[key] = state

    def get_effective_entity_state(self, entity_or_id, *, lineage_id=None) -> dict:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if lineage_id:
            fallback = self.entity_state.get(str(lineage_id), {})
            if isinstance(fallback, dict):
                merged = dict(fallback)
                merged.update(state)
                return merged
        return state

    def get_zone_for_render(self, coord):  # noqa: ANN001
        return None


def test_query_geometry_marks_collapsed_resolver_subtree_as_approximate() -> None:
    game = _DummyGame()
    parent = Entity(
        id="site:test_geom",
        entity_id="site:test_geom",
        name="Geom Parent",
        pos=(20, 20),
        abs_pos=(20, 20),
        kind="site",
        tags={
            "world_entity": True,
            "resolve": [
                {
                    "kind": "children_fixed",
                    "children": ["wall"],
                    "placement": {"pattern": "cluster", "radius": 2, "salt": "geom"},
                }
            ],
        },
    )
    entity_graph_ops_system.register_entity(game, parent, lod_state="collapsed")
    game.attn_store.stage(parent, abs_x=20, abs_y=20, zz=0)

    result_forbid = entity_geometry_system.query_geometry(game, parent.id, realize_policy="forbid")
    assert result_forbid["precision"] == "approximate"
    assert "collapsed_subtree" in result_forbid["warnings"]

    result_require = entity_geometry_system.query_geometry(game, parent.id, realize_policy="require")
    assert result_require["precision"] == "exact"
    assert any(edge["edge_type"] == "relation" and edge["dst"].startswith("entity:world:") for edge in result_require["edges"])


def test_query_normalized_pattern_is_deterministic_and_seed_builder_uses_it() -> None:
    game = _DummyGame()
    comp = chakra_component_state.ChakraComponent(
        root_node_id="body",
        nodes={
            "body": chakra_component_state.ChakraNode("body", kind="body_root", active=True, abs_pos=(10.0, 10.0)),
            "arm": chakra_component_state.ChakraNode("arm", kind="limb", active=True, abs_pos=(12.0, 10.0)),
            "hand": chakra_component_state.ChakraNode("hand", kind="limb", active=True, abs_pos=(14.0, 11.0)),
        },
        edges={
            "body-arm": chakra_component_state.ChakraEdge("body-arm", "body", "arm"),
            "arm-hand": chakra_component_state.ChakraEdge("arm-hand", "arm", "hand"),
        },
        tags={"layout_id": "test_component"},
    )
    actor = Entity(
        id="actor:test_pattern",
        entity_id="actor:test_pattern",
        name="Pattern Actor",
        pos=(10, 10),
        abs_pos=(10, 10),
        kind="actor",
        chakra_component=comp,
        tags={},
    )
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")
    game.attn_store.stage(actor, abs_x=10, abs_y=10, zz=0)

    pattern_a = entity_geometry_system.query_normalized_pattern(game, actor.id)
    pattern_b = entity_geometry_system.query_normalized_pattern(game, actor.id)

    assert pattern_a["warnings"] == pattern_b["warnings"]
    assert pattern_a["node_order"] == pattern_b["node_order"]
    assert pattern_a["verts"] == pattern_b["verts"]
    assert pattern_a["edges"] == pattern_b["edges"]
    assert pattern_a["base_len"] == pattern_b["base_len"]

    seed = chakra_system.build_chakra_generator_seed(
        body_schema={},
        chakra_state=chakra_system.ChakraState(),
        actor=actor,
        game=game,
        require_root=True,
    )
    assert seed.node_order == pattern_a["node_order"]
    assert seed.verts == pattern_a["verts"]
    assert seed.edges == pattern_a["edges"]


def test_external_child_root_active_state_follows_chakra_state() -> None:
    """A4 invariant: _link_parent_child_chakra respects owner ChakraState active flags.

    After A3 (active-state sync in _materialize_body_child), external_child_root
    nodes in the parent's chakra_component must have their `active` field set from
    the owner's ChakraState.  This test verifies that query_normalized_pattern
    returns only the chakra-state-active nodes — not all body nodes — so the entity
    path produces the same active-node set as the legacy body-schema traversal.
    """
    from edgecaster.state import chakra_component as cc_state
    from edgecaster.systems import entity_lifecycle as elc_system

    game = _DummyGame()

    # Build an actor whose chakra_component already mirrors a post-expansion state:
    # root "body" node (active) plus two external_child_root nodes ("head" active,
    # "tail" inactive).  This directly tests the query layer; the _materialize_body_child
    # sync is tested separately via the spawning integration.
    body_node = cc_state.ChakraNode(node_id="body", kind="body_root", active=True, abs_pos=(10.0, 10.0))
    head_node = cc_state.ChakraNode(node_id="head_core", kind="external_child_root", active=True, abs_pos=(10.0, 11.5))
    tail_node = cc_state.ChakraNode(node_id="tail_core", kind="external_child_root", active=False, abs_pos=(10.0, 8.5))
    comp = cc_state.ChakraComponent(
        root_node_id="body",
        nodes={
            "body": body_node,
            "head_core": head_node,
            "tail_core": tail_node,
        },
        edges={
            "body->head": cc_state.ChakraEdge("body->head", "body", "head_core"),
            "body->tail": cc_state.ChakraEdge("body->tail", "body", "tail_core"),
        },
    )
    actor = Entity(
        id="actor:a3_test",
        entity_id="actor:a3_test",
        name="A3 Test Actor",
        pos=(10, 10),
        abs_pos=(10, 10),
        kind="actor",
        chakra_component=comp,
        tags={},
    )
    entity_graph_ops_system.register_entity(game, actor, lod_state="expanded")
    game.attn_store.stage(actor, abs_x=10, abs_y=10, zz=0)

    result = entity_geometry_system.query_normalized_pattern(game, actor.id)

    # Pattern should succeed (body + head active → 2 nodes)
    assert "insufficient_active_nodes" not in result.get("warnings", []), (
        "Expected a usable pattern from body+head; got: " + str(result.get("warnings"))
    )
    assert result.get("verts"), "Expected non-empty verts from body+head active nodes"

    # Inactive tail node must NOT appear in the active node set
    assert "tail_core" not in result.get("node_order", []), (
        "tail_core is inactive; it must not appear in node_order"
    )

    # Active head node MUST appear
    assert "head_core" in result.get("node_order", []), (
        "head_core is active; it must appear in node_order"
    )
