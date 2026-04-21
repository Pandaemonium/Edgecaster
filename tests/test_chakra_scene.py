from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _import_chakra_scene_module():
    with patch.dict("sys.modules", {"pygame": MagicMock()}):
        sys.modules.pop("edgecaster.scenes.chakra_scene", None)
        import edgecaster.scenes.chakra_scene as chakra_scene_module

        return importlib.reload(chakra_scene_module)


def test_effective_chakra_view_returns_view_type() -> None:
    """effective_chakra_view returns a ChakraViewState (or None) for any actor.

    The concrete routing (cached vs component-backed) is tested in
    test_chakra_items_state_bridge.py; this test just confirms chakra_scene
    imports and calls through correctly without blowing up.
    """
    chakra_scene_module = _import_chakra_scene_module()
    from edgecaster.state.chakra_component import ChakraComponent, ChakraNode

    node = ChakraNode(node_id="body", kind="compat", active=True)
    comp = ChakraComponent(root_node_id="body", nodes={"body": node})
    actor = SimpleNamespace(
        chakra_component=comp,
        body_schema={"root": "body"},
        chakra_state=None,
    )

    view = chakra_scene_module.chakra_items_system.effective_chakra_view(None, actor)
    assert view is not None
    assert "body" in view.active


def test_pattern_preview_widget_uses_game_context_for_entity_path() -> None:
    chakra_scene_module = _import_chakra_scene_module()
    actor = SimpleNamespace(id="actor:test_preview", entity_id="actor:test_preview")
    game = SimpleNamespace(name="dummy_game")
    widget = chakra_scene_module.PatternPreviewWidget(
        actor=actor,
        game=game,
        state_provider=lambda: chakra_scene_module.ChakraState(
            unlocked={"body"},
            active={"body"},
            pattern_root="body",
        ),
    )
    widget.rect = SimpleNamespace(width=200, height=200)

    captured: dict[str, object] = {}

    def _fake_seed(chakra_state, *, body_schema=None, **kwargs):
        captured["body_schema"] = dict(body_schema or {})
        captured["chakra_state"] = chakra_state
        captured["actor"] = kwargs.get("actor")
        captured["game"] = kwargs.get("game")
        return SimpleNamespace(
            verts=[(0.0, 0.0), (1.0, 0.0)],
            edges=[(0, 1)],
            node_order=["body", "hand"],
            root_id="body",
            terminus_id="hand",
            base_len=1.0,
        )

    with patch(
        "edgecaster.systems.chakras.build_chakra_generator_seed",
        side_effect=_fake_seed,
    ):
        widget._regenerate_pattern()

    assert captured["body_schema"] == {}
    assert captured["actor"] is actor
    assert captured["game"] is game
    assert widget._pattern_surface is not None


def test_pattern_preview_widget_uses_query_view_for_live_runtime_state() -> None:
    chakra_scene_module = _import_chakra_scene_module()
    actor = SimpleNamespace(id="actor:test_preview_projection", entity_id="actor:test_preview_projection")
    game = SimpleNamespace(name="dummy_game")
    widget = chakra_scene_module.PatternPreviewWidget(
        actor=actor,
        game=game,
        state_provider=None,
    )
    widget.rect = SimpleNamespace(width=200, height=200)

    captured: dict[str, object] = {}

    with patch.object(
        chakra_scene_module.chakra_items_system,
        "effective_chakra_view",
        return_value=chakra_scene_module.chakra_items_system.ChakraViewState(
            unlocked={"body"},
            active={"body"},
            charges={},
            alignments={},
            generators={},
            pattern_root="body",
        ),
    ):
        with patch(
            "edgecaster.systems.chakras.build_chakra_generator_seed",
            side_effect=lambda chakra_state, *, body_schema=None, **kwargs: captured.update(
                {
                    "body_schema": dict(body_schema or {}),
                    "active": set(getattr(chakra_state, "active", set()) or set()),
                    "pattern_root": getattr(chakra_state, "pattern_root", None),
                    "actor": kwargs.get("actor"),
                    "game": kwargs.get("game"),
                }
            )
            or SimpleNamespace(
                verts=[(0.0, 0.0), (1.0, 0.0)],
                edges=[(0, 1)],
                node_order=["body", "hand"],
                root_id="body",
                terminus_id="hand",
                base_len=1.0,
            ),
        ):
            widget._regenerate_pattern()

    assert captured["body_schema"] == {}
    assert captured["active"] == {"body"}
    assert captured["pattern_root"] == "body"
    assert captured["actor"] is actor
    assert captured["game"] is game
    assert widget._pattern_surface is not None


def test_body_nodes_for_actor_falls_back_to_entity_body_specs_when_graph_is_absent() -> None:
    chakra_scene_module = _import_chakra_scene_module()
    actor = SimpleNamespace(
        id="actor:test_body_nodes",
        entity_id="actor:test_body_nodes",
        abs_pos=(10.0, 20.0),
    )
    fake_spec = SimpleNamespace(
        full_id="body.head",
        parent_full_id="body",
        abs_pos=(13.5, 24.0),
        local_scale=2.0,
        node_proto_id="head",
        schema_proto_id="humanoid",
        is_schema_root=False,
    )

    with patch(
        "edgecaster.systems.entity_body.build_body_node_specs",
        return_value={"body.head": fake_spec},
    ):
        rows = chakra_scene_module._body_nodes_for_actor(None, actor)

    assert rows == [
        {
            "full_id": "body.head",
            "parent_full_id": "body",
            "schema_rel_pos": (3.5, 4.0),
            "local_scale": 2.0,
            "node_proto_id": "head",
            "schema_proto_id": "humanoid",
            "is_schema_root": False,
        }
    ]


def test_set_pattern_root_routes_through_shared_chakra_write_helper() -> None:
    chakra_scene_module = _import_chakra_scene_module()
    scene = chakra_scene_module.ChakraSelectionScene.__new__(chakra_scene_module.ChakraSelectionScene)
    scene._mode = "activate"
    scene._actor = object()
    scene.game = SimpleNamespace(log=SimpleNamespace(add=MagicMock()))
    scene._silhouette = SimpleNamespace(get_selected_chakra=lambda: "arm.hand")
    scene._push_undo = MagicMock()
    scene._preview = SimpleNamespace(mark_dirty=MagicMock())
    scene._refresh_list_items = MagicMock()
    scene._update_info_for_chakra = MagicMock()

    runtime_view = chakra_scene_module.chakra_items_system.ChakraViewState(
        unlocked={"body", "arm.hand"},
        active={"body", "arm.hand"},
        alignments={},
        generators={},
        charges={},
        pattern_root="body",
    )

    with patch.object(chakra_scene_module, "_runtime_chakra_view", return_value=runtime_view):
        with patch.object(
            chakra_scene_module.chakra_items_system,
            "set_actor_chakra_pattern_root",
            return_value="arm.hand",
        ) as set_root:
            scene._set_pattern_root()

    scene._push_undo.assert_called_once()
    set_root.assert_called_once_with(scene._actor, "arm.hand", game=scene.game)


def test_exit_realign_mode_commit_routes_through_shared_alignment_write_helper() -> None:
    chakra_scene_module = _import_chakra_scene_module()
    scene = chakra_scene_module.ChakraSelectionScene.__new__(chakra_scene_module.ChakraSelectionScene)
    scene._mode = "realign"
    scene._actor = object()
    scene.game = SimpleNamespace()
    scene._pending_alignments = {"arm.hand": (0.1, -0.2)}
    scene._original_alignments = {}
    scene._working_session = object()
    scene._push_undo = MagicMock()
    scene._apply_realign_time_cost = MagicMock()
    scene._silhouette = SimpleNamespace(
        set_state_override=MagicMock(),
        set_realign_mode=MagicMock(),
        refresh_points=MagicMock(),
    )
    scene._preview = SimpleNamespace(
        set_state_override=MagicMock(),
        mark_dirty=MagicMock(),
    )

    with patch.object(
        chakra_scene_module.chakra_items_system,
        "set_actor_chakra_alignments",
        return_value={"arm.hand": (0.1, -0.2)},
    ) as set_alignments:
        scene._exit_realign_mode(commit=True)

    scene._push_undo.assert_called_once()
    set_alignments.assert_called_once_with(
        scene._actor,
        {"arm.hand": (0.1, -0.2)},
        game=scene.game,
    )
    scene._apply_realign_time_cost.assert_called_once()
    assert scene._mode == "activate"
