"""Regression tests for blade projectile runtime."""

from types import SimpleNamespace

from edgecaster.state.entities import Entity
from edgecaster.systems import blade_runtime as blade_runtime_system


def test_advance_thrown_knives_moves_entity_projectiles_without_legacy_state(monkeypatch) -> None:
    """Projectile movement should be driven by throwing-knife entities alone."""
    knife_entity = Entity(
        id="knife:test:1",
        name="Throwing Knife",
        pos=(3, 4),
        abs_pos=(3, 4),
        glyph="/",
        color=(200, 200, 200),
        kind="throwing_knife",
        tags={"x": 3.5, "y": 4.5},
    )
    level = SimpleNamespace(
        entities={knife_entity.id: knife_entity},
        actors={},
    )
    game = SimpleNamespace()

    calls: list[dict] = []

    def _fake_step(_game, _level, knife):
        calls.append(knife)
        knife["x"] = 4.5
        knife["y"] = 5.5
        return True

    monkeypatch.setattr(blade_runtime_system, "_step_thrown_knife", _fake_step)

    blade_runtime_system.advance_thrown_knives(game, level, 1)

    assert len(calls) == 1
    assert knife_entity.tags["x"] == 4.5
    assert knife_entity.tags["y"] == 5.5


def test_advance_thrown_knives_removes_expired_entity_projectiles_without_legacy_state(monkeypatch) -> None:
    """Expired projectile entities should be removed even without thrown_knives_state."""
    knife_entity = Entity(
        id="knife:test:2",
        name="Throwing Knife",
        pos=(3, 4),
        abs_pos=(3, 4),
        glyph="/",
        color=(200, 200, 200),
        kind="throwing_knife",
        tags={"x": 3.5, "y": 4.5},
    )
    level = SimpleNamespace(
        entities={knife_entity.id: knife_entity},
        actors={},
    )
    game = SimpleNamespace()

    monkeypatch.setattr(
        blade_runtime_system,
        "_step_thrown_knife",
        lambda _game, _level, _knife: False,
    )

    blade_runtime_system.advance_thrown_knives(game, level, 1)

    assert knife_entity.id not in level.entities
