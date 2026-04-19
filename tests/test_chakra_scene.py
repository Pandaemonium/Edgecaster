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


def test_effective_chakra_state_returns_chakra_state_type() -> None:
    """effective_chakra_state returns a ChakraState (or None) for any actor.

    The concrete routing (cached vs component-backed) is tested in
    test_chakra_items_state_bridge.py; this test just confirms chakra_scene
    imports and calls through correctly without blowing up.
    """
    chakra_scene_module = _import_chakra_scene_module()
    from edgecaster.state.chakra_component import ChakraComponent, ChakraNode
    from edgecaster.state.chakra_component import coerce_chakra_component

    node = ChakraNode(node_id="body", kind="compat", active=True)
    comp = ChakraComponent(root_node_id="body", nodes={"body": node})
    actor = SimpleNamespace(
        chakra_component=comp,
        body_schema={"root": "body"},
        chakra_state=None,
    )

    state = chakra_scene_module.chakra_items_system.effective_chakra_state(None, actor)
    assert state is not None
    assert "body" in state.active


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

    def _fake_seed(body_schema, chakra_state, **kwargs):
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

    with patch.object(
        chakra_scene_module,
        "_get_body_schema",
        side_effect=AssertionError("game-backed preview should not resolve body schema"),
    ):
        with patch(
            "edgecaster.systems.chakras.build_chakra_generator_seed",
            side_effect=_fake_seed,
        ):
            widget._regenerate_pattern()

    assert captured["body_schema"] == {}
    assert captured["actor"] is actor
    assert captured["game"] is game
    assert widget._pattern_surface is not None
