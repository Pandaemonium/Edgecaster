"""Tests for the ChakraComponent-backed chakra runtime in chakra_items.

Covers effective_chakra_view, apply_chakra_state_snapshot,
restore_actor_chakra_component_snapshot, and related helpers.
"""

from types import SimpleNamespace

from edgecaster.state.chakra_component import ChakraComponent, ChakraNode
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems.chakras import ChakraState
from edgecaster.systems import chakra_items as chakra_items_system


def test_effective_chakra_view_uses_body_root_when_no_component() -> None:
    """effective_chakra_view derives root from body_schema when no component present."""
    actor = SimpleNamespace(
        id="actor:test:1",
        body_schema={"root": "body", "nodes": {"body": {}}},
        chakra_component=None,
        chakra_state=None,
    )
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    view = chakra_items_system.effective_chakra_view(game, actor)
    assert view is not None
    assert "body" in view.unlocked
    assert "body" in view.active


def test_effective_chakra_view_reads_component_root_node_id() -> None:
    """effective_chakra_view uses component.root_node_id as baseline root."""
    comp = ChakraComponent(
        root_node_id="ent:test:core",
        nodes={"ent:test:core": ChakraNode(node_id="ent:test:core", active=True)},
        tags={},
    )
    actor = SimpleNamespace(
        id="actor:test:2",
        body_schema=None,
        chakra_component=comp,
        chakra_state=None,
    )
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    view = chakra_items_system.effective_chakra_view(game, actor)
    assert view is not None
    # normalization converts ':' to '.'
    assert "ent.test.core" in view.unlocked
    assert "ent.test.core" in view.active


def test_effective_active_nodes_works_without_cached_state_bootstrap() -> None:
    actor = SimpleNamespace(
        id="actor:test:3",
        body_schema={"root": "body", "nodes": {"body": {}}},
        chakra_component=None,
        chakra_state=None,
    )
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    active = chakra_items_system.effective_active_nodes(game, actor)
    assert "body" in active
    assert getattr(actor, "chakra_state", None) is None


def test_effective_chakra_view_reads_component_nodes() -> None:
    """effective_chakra_view populates unlocked/active from component node active flags."""
    comp = ChakraComponent(
        root_node_id="body",
        nodes={
            "body": ChakraNode(node_id="body", active=True),
            "arm.hand": ChakraNode(node_id="arm.hand", active=True),
            "arm.elbow": ChakraNode(node_id="arm.elbow", active=False),
        },
        tags={},
    )
    actor = SimpleNamespace(
        id="actor:test:4",
        body_schema={"root": "body"},
        chakra_component=comp,
        chakra_state=None,
    )
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    view = chakra_items_system.effective_chakra_view(game, actor)
    assert view is not None
    assert "arm.hand" in view.unlocked
    assert "arm.elbow" in view.unlocked
    assert "arm.hand" in view.active
    assert "arm.elbow" not in view.active
    assert "body" in view.active


def test_effective_chakra_view_reads_component_compat_payload() -> None:
    """effective_chakra_view reads charges, pattern_root, alignments, generators from component."""
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
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    view = chakra_items_system.effective_chakra_view(game, actor)
    assert view is not None
    assert view.charges.get("body") == 0.25
    assert view.charges.get("arm.hand") == 0.75
    assert view.pattern_root == "body"
    assert view.alignments.get("arm.hand") == (0.1, -0.2)
    assert view.generators.get("arm.hand") == "koch"


def test_effective_chakra_projection_reads_component_payload_without_chakra_state_facade() -> None:
    """effective_chakra_projection exposes rich runtime fields without ChakraState."""
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
        id="actor:test:5a",
        body_schema={"root": "body"},
        chakra_component=comp,
        chakra_state=None,
    )
    game = SimpleNamespace(get_inventory=lambda _aid: [])

    projection = chakra_items_system.effective_chakra_projection(game, actor)

    assert projection is not None
    assert projection["active"] == {"body"}
    assert projection["unlocked"] == {"body", "arm.hand"}
    assert projection["charges"].get("body") == 0.25
    assert projection["charges"].get("arm.hand") == 0.75
    assert projection["pattern_root"] == "body"
    assert projection["alignments"].get("arm.hand") == (0.1, -0.2)
    assert projection["generators"].get("arm.hand") == "koch"


def test_effective_chakra_view_prefers_component_view_over_stale_cached_state() -> None:
    actor = SimpleNamespace(
        id="actor:test:5b",
        entity_id="actor:test:5b",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
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
        ),
        chakra_state=ChakraState(
            unlocked={"body", "stale.node"},
            active={"body", "stale.node"},
            charges={"stale.node": 1.0},
            pattern_root="stale.node",
        ),
    )
    game = SimpleNamespace(get_inventory=lambda _actor_id: [])

    view = chakra_items_system.effective_chakra_view(game, actor)

    assert view is not None
    assert "arm.hand" in view.unlocked
    assert "arm.hand" not in view.active
    assert "stale.node" not in view.unlocked
    assert view.charges.get("body") == 0.25
    assert view.charges.get("arm.hand") == 0.75
    assert view.pattern_root == "body"
    assert view.alignments.get("arm.hand") == (0.1, -0.2)
    assert view.generators.get("arm.hand") == "koch"


def test_apply_chakra_state_snapshot_mirrors_to_component_nodes_and_tags() -> None:
    """apply_chakra_state_snapshot writes a full ChakraState snapshot to ChakraComponent."""
    state = ChakraState(
        unlocked={"body", "arm.hand"},
        active={"body", "arm.hand"},
        charges={"arm.hand": 1.0},
        alignments={"arm.hand": (0.2, -0.3)},
        generators={"arm.hand": "branch"},
        pattern_root="arm.hand",
    )
    actor = SimpleNamespace(
        id="actor:test:6",
        entity_id="actor:test:6",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=state,
    )
    chakra_items_system.apply_chakra_state_snapshot(actor, state)

    comp = actor.chakra_component
    assert comp is not None
    assert "arm.hand" in comp.nodes
    hand = comp.nodes["arm.hand"]
    assert bool(getattr(hand, "active", False)) is True
    assert comp.tags.get("compat_pattern_root") == "arm.hand"
    assert "arm.hand" in set(comp.tags.get("compat_active_nodes", []))


def test_apply_chakra_state_snapshot_prunes_stale_compat_nodes_and_charge() -> None:
    state = ChakraState(
        unlocked={"body"},
        active={"body"},
        charges={},
    )
    actor = SimpleNamespace(
        id="actor:test:6b",
        entity_id="actor:test:6b",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={
                "body": ChakraNode(node_id="body", kind="core", active=True, channels={"charge": 0.5}),
                "arm.hand": ChakraNode(
                    node_id="arm.hand",
                    kind="compat",
                    active=True,
                    channels={"charge": 1.0},
                    tags={"compat_unlocked": True},
                ),
            },
            tags={"compat_unlocked_nodes": ["body", "arm.hand"]},
        ),
        chakra_state=state,
    )

    chakra_items_system.apply_chakra_state_snapshot(actor, state)

    comp = actor.chakra_component
    assert comp is not None
    assert "arm.hand" not in comp.nodes
    assert comp.nodes["body"].channels.get("charge") is None
    assert comp.tags.get("compat_unlocked_nodes") == ["body"]


def test_apply_chakra_state_snapshot_accepts_dict_payload_and_rebuilds_cache() -> None:
    actor = SimpleNamespace(
        id="actor:test:6c",
        entity_id="actor:test:6c",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=None,
    )
    snapshot = {
        "unlocked": ["body", "arm.hand"],
        "active": ["body", "arm.hand"],
        "charges": {"arm.hand": 0.5},
        "alignments": {"arm.hand": [0.1, -0.2]},
        "generators": {"arm.hand": "koch"},
        "pattern_root": "arm.hand",
    }

    chakra_items_system.apply_chakra_state_snapshot(actor, snapshot)

    assert actor.chakra_state is not None
    assert "arm.hand" in set(getattr(actor.chakra_state, "active", set()))
    assert getattr(actor.chakra_state, "pattern_root", None) == "arm.hand"
    assert actor.chakra_component.tags.get("compat_pattern_root") == "arm.hand"


def test_set_actor_chakra_charge_writes_to_component() -> None:
    """set_actor_chakra_charge writes to ChakraComponent (the sole authority)."""
    actor = SimpleNamespace(
        id="actor:test:7",
        entity_id="actor:test:7",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={})},
            tags={},
        ),
        chakra_state=None,
    )
    chakra_items_system.set_actor_chakra_charge(actor, "arm.hand", 0.6)
    comp = actor.chakra_component
    assert "arm.hand" in comp.nodes
    assert float(comp.nodes["arm.hand"].channels.get("charge", 0.0)) == 0.6


def test_tick_actor_chakra_charge_skips_dirty_churn_while_charging() -> None:
    actor = SimpleNamespace(
        id="actor:test:charge:1",
        entity_id="actor:test:charge:1",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={"charge": 0.0})},
            tags={},
        ),
        chakra_state=None,
    )
    game = SimpleNamespace(
        entity_graph=EntityGraphStore(),
        get_inventory=lambda _aid: [],
    )
    game.entity_graph.register(actor.entity_id, kind="actor")
    game.entity_graph.mark_subtree_clean(actor.entity_id)

    chakra_items_system.tick_actor_chakra_charge(actor, game, 1, charging=True)

    assert float(actor.chakra_component.nodes["body"].channels.get("charge", 0.0)) > 0.0
    assert game.entity_graph.get_node(actor.entity_id).dirty is False


def test_tick_actor_chakra_charge_marks_graph_dirty_when_only_decaying() -> None:
    actor = SimpleNamespace(
        id="actor:test:charge:2",
        entity_id="actor:test:charge:2",
        body_schema={"root": "body"},
        chakra_component=ChakraComponent(
            root_node_id="body",
            nodes={"body": ChakraNode(node_id="body", active=True, channels={"charge": 1.0})},
            tags={},
        ),
        chakra_state=None,
    )
    game = SimpleNamespace(
        entity_graph=EntityGraphStore(),
        get_inventory=lambda _aid: [],
    )
    game.entity_graph.register(actor.entity_id, kind="actor")
    game.entity_graph.mark_subtree_clean(actor.entity_id)

    chakra_items_system.tick_actor_chakra_charge(actor, game, 1, charging=False)

    assert float(actor.chakra_component.nodes["body"].channels.get("charge", 0.0)) < 1.0
    assert game.entity_graph.get_node(actor.entity_id).dirty is True


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


def test_restore_actor_chakra_component_snapshot_restores_component_and_state() -> None:
    """restore_actor_chakra_component_snapshot round-trips ChakraComponent to/from dict."""
    comp = ChakraComponent(
        root_node_id="body",
        nodes={
            "body": ChakraNode(node_id="body", active=True, channels={"charge": 0.5}),
            "arm.hand": ChakraNode(node_id="arm.hand", active=True, channels={"charge": 0.8}),
        },
        tags={"compat_pattern_root": "body"},
    )
    actor = SimpleNamespace(
        id="actor:undo:1",
        entity_id="actor:undo:1",
        body_schema={"root": "body"},
        chakra_component=comp,
        chakra_state=None,
    )
    # Take snapshot before modification.
    snap = comp.to_dict()

    # Mutate the component (simulating an activate toggle).
    from edgecaster.state.chakra_component import coerce_chakra_component as _coerce
    actor.chakra_component.nodes["arm.hand"].active = False

    # Restore from snapshot.
    chakra_items_system.restore_actor_chakra_component_snapshot(actor, snap)

    restored_comp = actor.chakra_component
    assert bool(restored_comp.nodes["arm.hand"].active) is True
    assert float(restored_comp.nodes["body"].channels.get("charge", 0.0)) == 0.5
    assert float(restored_comp.nodes["arm.hand"].channels.get("charge", 0.0)) == 0.8

    # chakra_state should be rebuilt from the restored component.
    assert actor.chakra_state is not None
    assert "arm.hand" in actor.chakra_state.active


def test_restore_actor_chakra_component_snapshot_fires_dirty_marks() -> None:
    """restore_actor_chakra_component_snapshot marks all restored nodes dirty."""
    comp = ChakraComponent(
        root_node_id="body",
        nodes={
            "body": ChakraNode(node_id="body", active=True),
            "arm.hand": ChakraNode(node_id="arm.hand", active=True),
        },
        tags={},
    )
    actor = SimpleNamespace(
        id="actor:undo:2",
        entity_id="actor:undo:2",
        body_schema={"root": "body"},
        chakra_component=comp,
        chakra_state=None,
    )
    game = SimpleNamespace(entity_graph=EntityGraphStore())
    game.entity_graph.register(actor.entity_id, kind="actor")
    game.entity_graph.mark_subtree_clean(actor.entity_id)

    snap = comp.to_dict()
    chakra_items_system.restore_actor_chakra_component_snapshot(actor, snap, game=game)

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
