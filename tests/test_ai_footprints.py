from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from edgecaster.systems import ai


def _mk_actor(
    *,
    actor_id: str,
    pos: tuple[int, int],
    actions: tuple[str, ...],
    faction: str = "hostile",
    footprint_local: tuple[float, float, float, float] | None = None,
    tags: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=actor_id,
        name=actor_id,
        pos=pos,
        actions=actions,
        faction=faction,
        alive=True,
        footprint_local=footprint_local,
        tags={} if tags is None else dict(tags),
        statuses={},
        cooldowns={},
    )


def _mk_game_and_level(player: SimpleNamespace, actor: SimpleNamespace):
    level = SimpleNamespace(
        coord=(0, 0, 0),
        actors={player.id: player, actor.id: actor},
        entities={player.id: player, actor.id: actor},
    )
    game = SimpleNamespace(
        _player=lambda: player,
        _level=lambda: level,
        rng=SimpleNamespace(choice=lambda items: items[0], random=lambda: 0.0),
    )
    return game, level


def test_deferred_attacker_uses_footprint_distance_for_attack_range() -> None:
    actor = _mk_actor(
        actor_id="a1",
        pos=(0, 0),
        actions=("fire_breath", "move", "wait"),
        footprint_local=(0.0, 0.0, 6.0, 6.0),
        tags={"fire_breath_range": 2},
    )
    player = _mk_actor(
        actor_id="p1",
        pos=(20, 20),
        actions=("move",),
        faction="player",
        footprint_local=(6.0, 2.0, 7.0, 3.0),  # Adjacent to actor footprint.
    )
    game, level = _mk_game_and_level(player, actor)

    with patch("edgecaster.systems.ai.reputation_system.is_hostile", return_value=True):
        action, params = ai._deferred_attacker(  # noqa: SLF001 - intentional unit test of behavior helper
            game,
            level,
            actor,
            "fire_breath",
            "fire_breath_range",
            5,
        )

    assert action == "fire_breath"
    assert params == {}


def test_rune_sapper_uses_footprint_edge_for_sabotage_targeting() -> None:
    # Pos is far from footprint on purpose: legacy point-based logic picks the wrong direction.
    actor = _mk_actor(
        actor_id="sapper_1",
        pos=(20, 20),
        actions=("move", "wait"),
        footprint_local=(10.0, 10.0, 13.0, 13.0),
        tags={"rune_siege_role": "sapper"},
    )
    player = _mk_actor(
        actor_id="player_1",
        pos=(40, 40),
        actions=("move",),
        faction="player",
        footprint_local=(40.0, 40.0, 41.0, 41.0),
    )
    game, level = _mk_game_and_level(player, actor)
    level.rune_anchor_siege = SimpleNamespace(
        phase="active",
        fractures=[SimpleNamespace(pos=(13, 11), repaired=True, required_channels=2, progress=2)],
        anchor_pos=(0, 0),
    )

    with patch("edgecaster.systems.ai.reputation_system.is_hostile", return_value=True):
        action, params = ai._rune_sapper(game, level, actor)  # noqa: SLF001 - intentional unit test of behavior helper

    assert action == "move"
    assert params == {"dx": 1, "dy": 0}

