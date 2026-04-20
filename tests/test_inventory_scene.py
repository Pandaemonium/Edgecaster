from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _import_inventory_scene_module():
    with patch.dict("sys.modules", {"pygame": MagicMock()}):
        sys.modules.pop("edgecaster.scenes.inventory_scene", None)
        import edgecaster.scenes.inventory_scene as inventory_scene_module

        return importlib.reload(inventory_scene_module)


def test_display_body_node_label_uses_entity_zoomable_metadata_without_schema_resolve() -> None:
    inventory_scene_module = _import_inventory_scene_module()
    node_spec = {
        "zoomable": True,
        "proto": "head_branch",
    }

    with patch.object(
        inventory_scene_module,
        "resolve_body_schema",
        side_effect=AssertionError("entity-backed node labels should not resolve schema"),
    ):
        label = inventory_scene_module._display_body_node_label(
            "arm_hand",
            node_spec=node_spec,
            cur_nodes={"arm_hand": node_spec},
        )

    assert label == "arm hand*"


def test_build_entity_schema_at_zoom_depth_falls_back_to_entity_body_specs_when_graph_is_absent() -> None:
    inventory_scene_module = _import_inventory_scene_module()
    owner = type(
        "Owner",
        (),
        {
            "id": "actor:test_inventory_zoom",
            "entity_id": "actor:test_inventory_zoom",
            "abs_pos": (10.0, 20.0),
        },
    )()
    fake_spec = type(
        "Spec",
        (),
        {
            "local_node_id": "head",
            "schema_proto_id": "humanoid_head",
            "node_proto_id": "head",
            "local_scale": 2.5,
            "abs_pos": (13.0, 27.0),
            "is_schema_root": True,
        },
    )()

    with patch(
        "edgecaster.systems.entity_body.build_body_node_specs",
        return_value={"head": fake_spec},
    ):
        with patch(
            "edgecaster.systems.entity_body.child_specs_for_entity",
            return_value=[fake_spec],
        ):
            with patch(
                "edgecaster.systems.body_view_queries.resolve_body_schema",
                side_effect=AssertionError("should not resolve schema"),
            ):
                schema = inventory_scene_module._build_entity_schema_at_zoom_depth(
                    owner,
                    None,
                    [],
                )

    assert schema == {
        "root": "head",
        "nodes": {
            "head": {
                "layout": {"x": 3.0, "y": 7.0},
                "props": {"size": 2.5},
                "proto": "humanoid_head",
                "zoomable": True,
            }
        },
    }


def test_find_owner_entity_uses_runtime_registry_before_inventory_scans() -> None:
    inventory_scene_module = _import_inventory_scene_module()
    owner = SimpleNamespace(id="bag_1")
    level = SimpleNamespace(entities={}, actors={})
    game = SimpleNamespace(
        player_id="player",
        _level=lambda: level,
        get_inventory=lambda _owner_id: [],
    )

    scene = inventory_scene_module.InventoryScene.__new__(inventory_scene_module.InventoryScene)
    scene.game = game
    scene.owner_id = "bag_1"
    scene.parent_owner_id = None

    with patch(
        "edgecaster.systems.entity_lifecycle.find_runtime_entity",
        return_value=owner,
    ):
        assert scene._find_owner_entity() is owner


def test_find_owner_entity_recurses_inventory_tree_through_get_inventory() -> None:
    inventory_scene_module = _import_inventory_scene_module()
    bag = SimpleNamespace(id="bag_1", tags={"container": True})
    level = SimpleNamespace(entities={}, actors={})
    inventories = {
        "player": [bag],
        "bag_1": [],
    }
    game = SimpleNamespace(
        player_id="player",
        _level=lambda: level,
        get_inventory=lambda owner_id: list(inventories.get(str(owner_id), [])),
    )

    scene = inventory_scene_module.InventoryScene.__new__(inventory_scene_module.InventoryScene)
    scene.game = game
    scene.owner_id = "bag_1"
    scene.parent_owner_id = None

    with patch(
        "edgecaster.systems.entity_lifecycle.find_runtime_entity",
        return_value=None,
    ):
        assert scene._find_owner_entity() is bag


def test_resolve_body_view_for_zoom_path_falls_back_when_entity_zoom_view_is_degenerate() -> None:
    inventory_scene_module = _import_inventory_scene_module()
    owner = SimpleNamespace(id="actor:test_body", entity_id="actor:test_body")
    entity_result = (
        {
            "root": "shoulder",
            "nodes": {
                "shoulder": {
                    "layout": {"x": 0.0, "y": 0.0},
                    "props": {"size": 1.0},
                }
            },
        },
        (0.0, 0.0),
        1.0,
    )
    root_schema = {
        "root": "body",
        "nodes": {
            "arm": {
                "layout": {"x": 0.0, "y": 0.0},
                "props": {"size": 1.0},
                "proto": "arm_proto",
            }
        },
    }
    arm_schema = {
        "root": "shoulder",
        "nodes": {
            "shoulder": {"layout": {"x": 0.0, "y": 0.0}, "props": {"size": 1.0}},
            "elbow": {"layout": {"x": 1.0, "y": 0.0}, "props": {"size": 1.0}},
        },
    }

    with patch(
        "edgecaster.systems.body_view_queries.entity_body_view_for_zoom_path",
        return_value=entity_result,
    ), patch(
        "edgecaster.systems.body_view_queries.resolve_body_schema",
        side_effect=[root_schema, arm_schema],
    ):
        schema, embed_off_u, embed_scale_u = inventory_scene_module._resolve_body_view_for_zoom_path(
            owner,
            ["arm"],
            game=object(),
        )

    assert schema == arm_schema
    assert embed_off_u == (0.0, 0.0)
    assert embed_scale_u == 1.0


def test_resolve_body_view_chain_for_zoom_path_falls_back_when_entity_chain_is_incomplete() -> None:
    inventory_scene_module = _import_inventory_scene_module()
    owner = SimpleNamespace(id="actor:test_body", entity_id="actor:test_body")
    entity_chain = [
        (
            {
                "root": "body",
                "nodes": {
                    "arm": {
                        "layout": {"x": 0.0, "y": 0.0},
                        "props": {"size": 1.0},
                        "proto": "arm_proto",
                    }
                },
            },
            (0.0, 0.0),
            1.0,
        )
    ]
    root_schema = entity_chain[0][0]
    arm_schema = {
        "root": "shoulder",
        "nodes": {
            "shoulder": {"layout": {"x": 0.0, "y": 0.0}, "props": {"size": 1.0}},
            "elbow": {"layout": {"x": 1.0, "y": 0.0}, "props": {"size": 1.0}},
        },
    }

    with patch(
        "edgecaster.systems.body_view_queries.entity_body_view_chain_for_zoom_path",
        return_value=entity_chain,
    ), patch(
        "edgecaster.systems.body_view_queries.resolve_body_schema",
        side_effect=[root_schema, arm_schema],
    ):
        chain = inventory_scene_module._resolve_body_view_chain_for_zoom_path(
            owner,
            ["arm"],
            game=object(),
        )

    assert len(chain) == 2
    assert chain[0][0] == root_schema
    assert chain[-1][0] == arm_schema
