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


def test_get_chakra_state_prefers_cached_actor_state() -> None:
    chakra_scene_module = _import_chakra_scene_module()
    cached_state = chakra_scene_module.ChakraState(
        unlocked={"body", "arm.hand"},
        active={"body"},
        pattern_root="body",
    )
    actor = SimpleNamespace(chakra_state=cached_state)

    with patch.object(
        chakra_scene_module.chakra_items_system,
        "ensure_actor_chakra_state",
        side_effect=AssertionError("cached actor state should win before compat bootstrap"),
    ):
        state = chakra_scene_module._get_chakra_state(actor)

    assert state is cached_state


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
