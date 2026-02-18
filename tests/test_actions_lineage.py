from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edgecaster import spawn_factory
from edgecaster.systems.actions import _setup_deferred_aoe
from edgecaster.systems.damage_policy import DamagePolicy


class _Stats:
    def __init__(self, hp: int) -> None:
        self.hp = int(hp)

    def clamp(self) -> None:
        if self.hp < 0:
            self.hp = 0


def test_remove_entity_marks_lineage_and_deletes_from_level() -> None:
    # Import lazily so the test remains cheap and does not require real pygame init.
    with patch.dict("sys.modules", {"pygame": MagicMock()}):
        from edgecaster.game import Game

    ent = SimpleNamespace(id="site:start/building:bureau:0:wall:10,10", tags={"lineage_id": "site:start/building:bureau:0:wall:10,10"})
    level = SimpleNamespace(
        entities={ent.id: ent},
        actors={},
        spatial_dirty=False,
    )
    dummy_game = SimpleNamespace(
        mark_entity_removed=MagicMock(),
    )

    Game._remove_entity(dummy_game, level, ent, reason="destroyed_wall")

    assert ent.id not in level.entities
    assert level.spatial_dirty is True
    dummy_game.mark_entity_removed.assert_called_once_with(ent, reason="destroyed_wall")


def test_deferred_aoe_uses_remove_entity_for_destroyed_environment_target() -> None:
    caster = SimpleNamespace(
        id="caster",
        alive=True,
        faction="hostile",
        pos=(0, 0),
        stats=_Stats(10),
        name="Caster",
    )
    target = SimpleNamespace(
        id="site:start/building:bureau:0:door:10,9",
        pos=(1, 1),
        stats=_Stats(1),
        name="Door",
        tags={"lineage_id": "site:start/building:bureau:0:door:10,9"},
    )
    level = SimpleNamespace(
        actors={"caster": caster},
        entities={target.id: target},
        deferred_actions=[],
        current_tick=0,
    )

    game = SimpleNamespace(
        _level=MagicMock(return_value=level),
        player_id="player",
        log=MagicMock(),
        _remove_entity=MagicMock(),
        _kill_actor=MagicMock(),
    )

    policy = DamagePolicy(
        include_self=False,
        include_hostile=False,
        include_neutral=False,
        include_friendly=False,
        include_environment=True,
    )

    with patch("edgecaster.systems.scheduling.schedule", side_effect=lambda _g, _l, _d, fn: fn()):
        _setup_deferred_aoe(
            game,
            "caster",
            action_name="fire_breath",
            label="Fire Breath",
            tiles=[(1, 1)],
            damage=5,
            prep_ticks=1,
            color=(255, 110, 40),
            log_prep="{name} inhales!",
            log_resolve="Flames erupt!",
            damage_policy=policy,
            include_entities=True,
        )

    game._remove_entity.assert_called_once_with(level, target, reason="destroyed_fire_breath")
    game._kill_actor.assert_not_called()


def test_deferred_aoe_hits_entity_by_footprint_overlap_not_pos() -> None:
    caster = SimpleNamespace(
        id="caster",
        alive=True,
        faction="hostile",
        pos=(0, 0),
        stats=_Stats(10),
        name="Caster",
    )
    target = SimpleNamespace(
        id="site:start/building:bureau:0:door:20,20",
        pos=(4, 4),  # outside tile telegraph by point
        footprint_local=(1.0, 1.0, 5.0, 5.0),  # overlaps (1,1)
        stats=_Stats(1),
        name="Large Door",
        tags={"lineage_id": "site:start/building:bureau:0:door:20,20"},
    )
    level = SimpleNamespace(
        actors={"caster": caster},
        entities={target.id: target},
        deferred_actions=[],
        current_tick=0,
    )

    game = SimpleNamespace(
        _level=MagicMock(return_value=level),
        player_id="player",
        log=MagicMock(),
        _remove_entity=MagicMock(),
        _kill_actor=MagicMock(),
    )

    policy = DamagePolicy(
        include_self=False,
        include_hostile=False,
        include_neutral=False,
        include_friendly=False,
        include_environment=True,
    )

    with patch("edgecaster.systems.scheduling.schedule", side_effect=lambda _g, _l, _d, fn: fn()):
        _setup_deferred_aoe(
            game,
            "caster",
            action_name="fire_breath",
            label="Fire Breath",
            tiles=[(1, 1)],
            damage=5,
            prep_ticks=1,
            color=(255, 110, 40),
            log_prep="{name} inhales!",
            log_resolve="Flames erupt!",
            damage_policy=policy,
            include_entities=True,
        )

    game._remove_entity.assert_called_once_with(level, target, reason="destroyed_fire_breath")
    game._kill_actor.assert_not_called()


def test_build_entity_from_spec_attaches_stats_for_destructibles() -> None:
    spec = {
        "id": "test_wall",
        "name": "Test Wall",
        "glyph": "#",
        "color": [120, 100, 80],
        "kind": "feature",
        "hp": 9,
        "max_hp": 12,
        "tags": {"wall": True},
    }

    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid="test_wall_1",
        pos=(4, 5),
        abs_pos=(4, 5),
    )

    assert hasattr(ent, "stats")
    assert int(ent.stats.hp) == 9
    assert int(ent.stats.max_hp) == 12
