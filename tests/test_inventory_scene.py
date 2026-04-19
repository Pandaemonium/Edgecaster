from __future__ import annotations

import importlib
import sys
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
