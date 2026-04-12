"""Tests for EntityGraphStore and entity_graph_ops integration.

[ENTITY_CHAKRA][PHASE_2C]
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from edgecaster.state.entity_graph import EntityGraphNode, EntityGraphStore
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system


# ---------------------------------------------------------------------------
# EntityGraphStore unit tests
# ---------------------------------------------------------------------------

class TestEntityGraphStoreRegister:
    def test_register_adds_node(self):
        store = EntityGraphStore()
        node = store.register("e1", kind="item")
        assert store.get_node("e1") is node
        assert node.entity_id == "e1"
        assert node.kind == "item"

    def test_register_adds_parent_index(self):
        store = EntityGraphStore()
        store.register("parent1", kind="actor")
        store.register("child1", parent_entity_id="parent1", socket_id="inventory")
        assert "child1" in store.get_children("parent1")

    def test_register_adds_semantic_index(self):
        store = EntityGraphStore()
        store.register("e1", semantic_id="starttsgard:tavern:1")
        assert store.get_by_semantic_id("starttsgard:tavern:1") == "e1"

    def test_register_update_changes_parent(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("p2")
        store.register("item1", parent_entity_id="p1", socket_id="inventory")
        # Re-register with new parent.
        store.register("item1", parent_entity_id="p2", socket_id="inventory")
        assert "item1" not in store.get_children("p1")
        assert "item1" in store.get_children("p2")

    def test_register_update_changes_semantic_id(self):
        store = EntityGraphStore()
        store.register("e1", semantic_id="old:sem")
        store.register("e1", semantic_id="new:sem")
        assert store.get_by_semantic_id("old:sem") is None
        assert store.get_by_semantic_id("new:sem") == "e1"

    def test_register_idempotent_same_parent(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("child1", parent_entity_id="p1")
        store.register("child1", parent_entity_id="p1")
        # Should not duplicate in children set.
        children = store.get_children("p1")
        assert children.count("child1") == 1

    def test_register_persists_lifecycle_metadata(self):
        store = EntityGraphStore()
        node = store.register(
            "building1",
            kind="structure",
            lod_state="collapsed",
            layout_id="building_shell",
            rule_id="building_rules",
        )
        assert node.lod_state == "collapsed"
        assert node.layout_id == "building_shell"
        assert node.rule_id == "building_rules"

    def test_register_preserves_existing_metadata_when_reparenting_without_overrides(self):
        store = EntityGraphStore()
        store.register(
            "site1",
            kind="feature",
            lod_state="collapsed",
            layout_id="site_layout",
            rule_id="site_rules",
        )
        store.register("parent2")
        node = store.register("site1", parent_entity_id="parent2", socket_id="resolve")
        assert node.parent_entity_id == "parent2"
        assert node.socket_id == "resolve"
        assert node.lod_state == "collapsed"
        assert node.layout_id == "site_layout"
        assert node.rule_id == "site_rules"


class TestEntityGraphStoreDeregister:
    def test_deregister_removes_node(self):
        store = EntityGraphStore()
        store.register("e1")
        store.deregister("e1")
        assert store.get_node("e1") is None

    def test_deregister_cleans_parent_index(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("c1", parent_entity_id="p1")
        store.deregister("c1")
        assert "c1" not in store.get_children("p1")

    def test_deregister_cleans_semantic_index(self):
        store = EntityGraphStore()
        store.register("e1", semantic_id="sword:x")
        store.deregister("e1")
        assert store.get_by_semantic_id("sword:x") is None

    def test_deregister_orphans_children(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("c1", parent_entity_id="p1")
        store.deregister("p1")
        child = store.get_node("c1")
        assert child is not None
        assert child.parent_entity_id is None
        assert child.socket_id is None

    def test_deregister_noop_when_absent(self):
        store = EntityGraphStore()
        store.deregister("nonexistent")  # should not raise


class TestEntityGraphStoreReparent:
    def test_reparent_moves_to_new_parent(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("p2")
        store.register("item1", parent_entity_id="p1", socket_id="inventory")
        store.reparent("item1", "p2", "equipment")
        assert "item1" not in store.get_children("p1")
        assert "item1" in store.get_children("p2")
        node = store.get_node("item1")
        assert node.socket_id == "equipment"

    def test_reparent_to_none_detaches(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("item1", parent_entity_id="p1")
        store.reparent("item1", None)
        assert "item1" not in store.get_children("p1")
        node = store.get_node("item1")
        assert node.parent_entity_id is None

    def test_reparent_noop_when_absent(self):
        store = EntityGraphStore()
        store.reparent("ghost", "p1")  # should not raise


class TestEntityGraphStoreQuery:
    def test_get_children_by_socket(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("c1", parent_entity_id="p1", socket_id="inventory")
        store.register("c2", parent_entity_id="p1", socket_id="equipment")
        inv = store.get_children("p1", socket_id="inventory")
        equip = store.get_children("p1", socket_id="equipment")
        assert "c1" in inv and "c2" not in inv
        assert "c2" in equip and "c1" not in equip

    def test_get_children_all_sockets(self):
        store = EntityGraphStore()
        store.register("p1")
        store.register("c1", parent_entity_id="p1", socket_id="inventory")
        store.register("c2", parent_entity_id="p1", socket_id="equipment")
        all_children = store.get_children("p1")
        assert "c1" in all_children
        assert "c2" in all_children

    def test_get_children_empty_when_no_children(self):
        store = EntityGraphStore()
        store.register("p1")
        assert store.get_children("p1") == []

    def test_get_node_returns_none_for_unknown(self):
        store = EntityGraphStore()
        assert store.get_node("nobody") is None

    def test_get_by_semantic_id_returns_none_for_unknown(self):
        store = EntityGraphStore()
        assert store.get_by_semantic_id("missing:sem") is None


# ---------------------------------------------------------------------------
# entity_graph_ops integration tests
# ---------------------------------------------------------------------------

def _make_game_with_graph() -> Any:
    """Minimal game-like object with entity_graph and entity_state support."""
    game = SimpleNamespace()
    game.entity_graph = EntityGraphStore()
    game.entity_state = {}

    def patch_entity_state(entity_or_id: Any, **fields: Any) -> None:
        eid = str(getattr(entity_or_id, "id", "") or entity_or_id or "")
        game.entity_state.setdefault(eid, {}).update(fields)

    game.patch_entity_state = patch_entity_state
    return game


def _make_entity(eid: str, kind: str = "item", semantic_id: str = "") -> Any:
    ent = SimpleNamespace()
    ent.id = eid
    ent.entity_id = eid
    ent.semantic_id = semantic_id or None
    ent.kind = kind
    ent.tags = {}
    ent.parent_entity_id = None
    ent.socket_id = None
    return ent


class TestAttachEntityToParent:
    def test_registers_in_graph(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        node = game.entity_graph.get_node("item1")
        assert node is not None
        assert node.parent_entity_id == "player1"
        assert node.socket_id == "inventory"

    def test_registers_in_parent_children_index(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        assert "item1" in game.entity_graph.get_children("player1")

    def test_still_patches_entity_attributes(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        assert ent.parent_entity_id == "player1"
        assert ent.socket_id == "inventory"
        assert ent.tags.get("inventory_owner_id") == "player1"

    def test_custom_socket_id(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(
            game, ent, "building1", socket_id="fixture"
        )
        node = game.entity_graph.get_node("item1")
        assert node.socket_id == "fixture"

    def test_registers_graph_metadata_from_entity(self):
        game = _make_game_with_graph()
        ent = _make_entity("site:1", kind="feature")
        ent.chakra_layout_id = "site_layout"
        ent.tags.update({"rule_id": "site_rules", "world_entity": True, "site": True, "resolve": [{"kind": "children_fixed"}]})
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "world:root", socket_id="resolve")
        node = game.entity_graph.get_node("site:1")
        assert node is not None
        assert node.layout_id == "site_layout"
        assert node.rule_id == "site_rules"
        assert node.lod_state == "collapsed"

    def test_no_graph_does_not_raise(self):
        # If entity_graph is absent, fall through to legacy patching only.
        game = SimpleNamespace()
        game.entity_state = {}
        game.patch_entity_state = lambda *a, **kw: None
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        assert ent.parent_entity_id == "player1"


class TestDetachEntityFromParent:
    def test_reparents_to_none_in_graph(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        entity_graph_ops_system.detach_entity_from_parent(game, ent)
        node = game.entity_graph.get_node("item1")
        assert node is not None
        assert node.parent_entity_id is None

    def test_removes_from_children_index(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        entity_graph_ops_system.detach_entity_from_parent(game, ent)
        assert "item1" not in game.entity_graph.get_children("player1")

    def test_still_clears_entity_attributes(self):
        game = _make_game_with_graph()
        ent = _make_entity("item1")
        entity_graph_ops_system.attach_entity_to_parent(game, ent, "player1")
        entity_graph_ops_system.detach_entity_from_parent(game, ent)
        assert ent.parent_entity_id is None
        assert ent.socket_id is None


class TestRegisterEntity:
    def test_registers_fresh_entity(self):
        game = _make_game_with_graph()
        ent = _make_entity("npc1", kind="actor", semantic_id="goblin:cave:7")
        entity_graph_ops_system.register_entity(game, ent)
        node = game.entity_graph.get_node("npc1")
        assert node is not None
        assert node.kind == "actor"
        assert node.semantic_id == "goblin:cave:7"

    def test_register_entity_idempotent(self):
        game = _make_game_with_graph()
        ent = _make_entity("npc1")
        entity_graph_ops_system.register_entity(game, ent)
        entity_graph_ops_system.register_entity(game, ent)
        assert len([k for k in game.entity_graph.nodes if k == "npc1"]) == 1

    def test_register_entity_no_graph_does_not_raise(self):
        game = SimpleNamespace()  # no entity_graph attribute
        ent = _make_entity("npc1")
        entity_graph_ops_system.register_entity(game, ent)  # should not raise

    def test_register_entity_accepts_explicit_metadata_overrides(self):
        game = _make_game_with_graph()
        ent = _make_entity("world:site:1", kind="feature")
        entity_graph_ops_system.register_entity(
            game,
            ent,
            lod_state="collapsed",
            layout_id="site_layout",
            rule_id="site_rules",
        )
        node = game.entity_graph.get_node("world:site:1")
        assert node is not None
        assert node.lod_state == "collapsed"
        assert node.layout_id == "site_layout"
        assert node.rule_id == "site_rules"
