"""Tests for typed ChakraComponent bootstrap on spawned entities/actors."""

from edgecaster import spawn_factory
from edgecaster.state.chakra_component import ChakraComponent
from edgecaster.systems import chakra_content as chakra_content_system


def test_build_entity_initializes_default_chakra_component() -> None:
    spec = {
        "id": "test_stone",
        "name": "Test Stone",
        "glyph": "*",
        "kind": "item",
    }
    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid="ent:test_stone:1",
        pos=(2, 3),
        abs_pos=(102, 203),
    )

    comp = getattr(ent, "chakra_component", None)
    assert isinstance(comp, ChakraComponent)
    assert comp.root_node_id == "ent:test_stone:1:core"
    assert comp.root_node_id in comp.nodes
    core = comp.nodes[comp.root_node_id]
    assert core.channels.get("mass") == 1.0
    assert core.channels.get("hp") == 1.0


def test_build_actor_initializes_default_chakra_component_with_hp() -> None:
    spec = {
        "id": "test_actor",
        "name": "Test Actor",
        "glyph": "@",
        "faction": "neutral",
        "actions": ["wait"],
        "base_hp": 17,
    }
    actor = spawn_factory.build_actor_from_spec(
        spec=spec,
        aid="actor:test:1",
        pos=(4, 5),
        abs_pos=(44, 55),
    )

    comp = getattr(actor, "chakra_component", None)
    assert isinstance(comp, ChakraComponent)
    assert comp.root_node_id == "actor:test:1:core"
    assert comp.root_node_id in comp.nodes
    core = comp.nodes[comp.root_node_id]
    assert core.channels.get("hp") == 17.0
    assert core.channels.get("mass") == 1.0


def test_existing_chakra_component_dict_is_coerced_and_preserved() -> None:
    spec = {
        "id": "test_relic",
        "name": "Test Relic",
        "glyph": "!",
        "kind": "item",
        "chakra_component": {
            "root_node_id": "relic:root",
            "nodes": {
                "relic:root": {
                    "node_id": "relic:root",
                    "kind": "core",
                    "channels": {"mass": 2.5},
                }
            },
            "edges": {},
        },
    }

    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid="ent:test_relic:1",
        pos=(1, 1),
        abs_pos=(1, 1),
    )
    comp = getattr(ent, "chakra_component", None)
    assert isinstance(comp, ChakraComponent)
    assert comp.root_node_id == "relic:root"
    assert "relic:root" in comp.nodes
    core = comp.nodes["relic:root"]
    assert core.channels.get("mass") == 2.5
    # Coercion fills missing default channels.
    assert core.channels.get("hp") == 1.0


def test_layout_component_loader_returns_known_layouts() -> None:
    chakra_content_system.clear_cache()
    comp_default = chakra_content_system.resolve_layout_component("default_core")
    comp_actor = chakra_content_system.resolve_layout_component("actor_body_seed")
    assert isinstance(comp_default, dict)
    assert isinstance(comp_actor, dict)


def test_build_actor_uses_chakra_layout_template_when_no_component() -> None:
    spec = {
        "id": "layout_actor",
        "name": "Layout Actor",
        "glyph": "@",
        "faction": "neutral",
        "actions": ["wait"],
        "base_hp": 10,
        "chakra_layout_id": "actor_body_seed",
    }
    actor = spawn_factory.build_actor_from_spec(
        spec=spec,
        aid="actor:layout:1",
        pos=(0, 0),
        abs_pos=(0, 0),
    )
    comp = getattr(actor, "chakra_component", None)
    assert isinstance(comp, ChakraComponent)
    assert "body" in comp.nodes


def test_build_entity_falls_back_to_default_when_layout_is_unknown() -> None:
    spec = {
        "id": "broken_layout_entity",
        "name": "Broken Layout Entity",
        "glyph": "*",
        "kind": "item",
        "chakra_layout_id": "does_not_exist",
    }
    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid="ent:broken_layout:1",
        pos=(0, 0),
        abs_pos=(0, 0),
    )
    comp = getattr(ent, "chakra_component", None)
    assert isinstance(comp, ChakraComponent)
    # Unknown layout should not break spawn; default root core is still present.
    assert comp.root_node_id == "ent:broken_layout:1:core"
    assert comp.root_node_id in comp.nodes
