"""Tests for chakra-state compatibility bridge in chakra_items."""

from types import SimpleNamespace

from edgecaster.state.chakra_component import ChakraComponent, ChakraNode
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems.chakras import ChakraState
from edgecaster.systems import chakra_items as chakra_items_system


def test_ensure_actor_chakra_state_uses_body_root_and_persists() -> None:
    actor = SimpleNamespace(
        id="actor:test:1",
        body_schema={"root": "body", "nodes": {"body": {}}},
        chakra_component=None,
        chakra_state=None,
    )

    state = chakra_items_system.ensure_actor_chakra_state(actor)
    assert state is not None
    assert "body" in state.unlocked
    assert "body" in state.active
    assert getattr(actor, "chakra_state", None) is state


def test_ensure_actor_chakra_state_falls_back_to_component_root() -> None:
    comp = SimpleNamespace(root_node_id="ent:test:core")
    actor = SimpleNamespace(
        id="actor:test:2",
        body_schema=None,
        chakra_component=comp,
        chakra_state=None,
    )

    state = chakra_items_system.ensure_actor_chakra_state(actor)
    assert state is not None
    # normalization converts ':' to '.'
    assert "ent.test.core" in state.unlocked
    assert "ent.test.core" in state.active


def test_effective_active_nodes_bootstraps_when_missing_state() -> None:
    actor = SimpleNamespace(
        id="actor:test:3",
        body_schema={"root": "body", "nodes": {"body": {}}},
        chakra_component=None,
        chakra_state=None,
    )
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    active = chakra_items_system.effective_active_nodes(game, actor)
    assert "body" in active
    assert getattr(actor, "chakra_state", None) is not None


def test_ensure_actor_chakra_state_reads_component_nodes() -> None:
    comp = SimpleNamespace(
        root_node_id="entity:root",
        nodes={
            "n1": {"node_id": "arm.hand", "active": True},
            "n2": {"node_id": "arm.elbow", "active": False},
        },
    )
    actor = SimpleNamespace(
        id="actor:test:4",
        body_schema={"root": "body"},
        chakra_component=comp,
        chakra_state=None,
    )

    state = chakra_items_system.ensure_actor_chakra_state(actor)
    assert state is not None
    assert "arm.hand" in state.unlocked
    assert "arm.elbow" in state.unlocked
    assert "arm.hand" in state.active
    assert "arm.elbow" not in state.active
    # body root is forced active for compatibility with legacy chakra flows.
    assert "body" in state.active


def test_ensure_actor_chakra_state_reads_component_compat_payload() -> None:
    comp = ChakraComponent(
        root_node_id="body",
        nodes={
            "body": ChakraNode(node_id="body", active=True, channels={"charge": 0.25}),
            "arm.hand": ChakraNode(node_id="arm.hand", active=False, channels={"charge": 0.75}),
        },
        tags={
            "compat_pattern_root": "body",
            "compat_alignments": {"arm.hand": [0.1, -0.2]},
            "compat_generators": {"arm.hand": "koch"},
        },
    )
    actor = SimpleNamespace(
        id="actor:test:5",
        body_schema={"root": "body"},
        chakra_component=comp,
        chakra_state=None,
    )
    state = chakra_items_system.ensure_actor_chakra_state(actor)
    assert state is not None
    assert state.charges.get("body") == 0.25
    assert state.charges.get("arm.hand") == 0.75
    assert state.pattern_root == "body"
    assert state.alignments.get("arm.hand") == (0.1, -0.2)
    assert state.generators.get("arm.hand") == "koch"


def test_sync_actor_chakra_state_mirrors_to_component_nodes_and_tags() -> None:
    actor = SimpleNamespace(
        id="actor:test:6",
        entity_id="actor:test:6",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=ChakraState(
            unlocked={"body", "arm.hand"},
            active={"body", "arm.hand"},
            charges={"arm.hand": 1.0},
            alignments={"arm.hand": (0.2, -0.3)},
            generators={"arm.hand": "branch"},
            pattern_root="arm.hand",
        ),
    )
    chakra_items_system.sync_actor_chakra_state(actor)

    comp = actor.chakra_component
    assert comp is not None
    assert "arm.hand" in comp.nodes
    hand = comp.nodes["arm.hand"]
    assert bool(getattr(hand, "active", False)) is True
    assert float(getattr(hand, "channels", {}).get("charge", 0.0)) == 1.0
    assert comp.tags.get("compat_pattern_root") == "arm.hand"
    assert "arm.hand" in set(comp.tags.get("compat_active_nodes", []))


def test_set_actor_chakra_charge_updates_state_and_component() -> None:
    actor = SimpleNamespace(
        id="actor:test:7",
        entity_id="actor:test:7",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=ChakraState(unlocked={"body", "arm.hand"}, active={"body"}),
    )
    chakra_items_system.set_actor_chakra_charge(actor, "arm.hand", 0.6)
    state = actor.chakra_state
    assert float(state.charges.get("arm.hand", 0.0)) == 0.6
    comp = actor.chakra_component
    assert "arm.hand" in comp.nodes
    assert float(comp.nodes["arm.hand"].channels.get("charge", 0.0)) == 0.6


def test_unlock_actor_chakra_syncs_component() -> None:
    actor = SimpleNamespace(
        id="actor:test:8",
        entity_id="actor:test:8",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=ChakraState(unlocked={"body"}, active={"body"}),
    )
    ok = chakra_items_system.unlock_actor_chakra(actor, "arm.hand", auto_activate=True)
    assert ok is True
    assert "arm.hand" in actor.chakra_state.unlocked
    assert "arm.hand" in actor.chakra_state.active
    assert "arm.hand" in actor.chakra_component.nodes
    assert bool(actor.chakra_component.nodes["arm.hand"].active) is True


def test_toggle_actor_chakra_syncs_component() -> None:
    actor = SimpleNamespace(
        id="actor:test:9",
        entity_id="actor:test:9",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=ChakraState(unlocked={"body", "arm.hand"}, active={"body", "arm.hand"}),
    )
    now_active = chakra_items_system.toggle_actor_chakra(actor, "arm.hand", active=False)
    assert now_active is False
    assert "arm.hand" not in actor.chakra_state.active
    assert "arm.hand" in actor.chakra_component.nodes
    assert bool(actor.chakra_component.nodes["arm.hand"].active) is False


def test_flush_charges_to_component_marks_actor_graph_dirty_when_game_provided() -> None:
    actor = SimpleNamespace(
        id="actor:test:10",
        entity_id="actor:test:10",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=ChakraState(
            unlocked={"body", "arm.hand"},
            active={"body", "arm.hand"},
            charges={"arm.hand": 0.45},
        ),
    )
    game = SimpleNamespace(entity_graph=EntityGraphStore())
    game.entity_graph.register(actor.entity_id, kind="actor")
    game.entity_graph.mark_subtree_clean(actor.entity_id)

    chakra_items_system.flush_charges_to_component(actor, game=game)

    assert float(actor.chakra_component.nodes["arm.hand"].channels.get("charge", 0.0)) == 0.45
    assert game.entity_graph.get_node(actor.entity_id).dirty is True


def test_toggle_actor_chakra_marks_realized_body_node_dirty_when_game_provided() -> None:
    actor = SimpleNamespace(
        id="actor:test:11",
        entity_id="actor:test:11",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={
                "body": ChakraNode(node_id="body", active=True, channels={}),
                "arm.hand": ChakraNode(node_id="arm.hand", active=True, channels={}),
            },
            tags={},
        ),
        chakra_state=ChakraState(unlocked={"body", "arm.hand"}, active={"body", "arm.hand"}),
    )
    game = SimpleNamespace(entity_graph=EntityGraphStore())
    game.entity_graph.register(actor.entity_id, kind="actor")
    game.entity_graph.register(
        "actor:test:11:body:arm.hand",
        parent_entity_id=actor.entity_id,
        socket_id="body",
        kind="body_node",
    )
    game.entity_graph.mark_subtree_clean(actor.entity_id)

    chakra_items_system.toggle_actor_chakra(actor, "arm.hand", active=False, game=game)

    assert game.entity_graph.get_node("actor:test:11:body:arm.hand").dirty is True
    assert game.entity_graph.get_node(actor.entity_id).dirty is True
