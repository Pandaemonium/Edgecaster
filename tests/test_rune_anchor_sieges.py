"""Unit tests for rune-anchor siege runtime (systems/rune_anchor_sieges.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edgecaster.systems import rune_anchor_sieges


class DummyWorld:
    def __init__(self, width: int = 40, height: int = 30, entry=(20, 15)) -> None:
        self.width = width
        self.height = height
        self.entry = entry

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= int(x) < self.width and 0 <= int(y) < self.height

    def is_walkable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y)


class DummyStats:
    def __init__(self, hp: int = 20, max_hp: int = 20) -> None:
        self.hp = hp
        self.max_hp = max_hp

    def clamp(self) -> None:
        self.hp = max(0, min(int(self.hp), int(self.max_hp)))


def _make_game_and_level():
    level = SimpleNamespace(
        world=DummyWorld(),
        actors={},
        entities={},
        coord=(2, 3, 0),
        current_tick=0,
    )

    player = SimpleNamespace(
        id="player_1",
        pos=(20, 15),
        actions=("move", "wait"),
        tags={},
        faction="player",
        alive=True,
        name="Player",
        stats=DummyStats(30, 30),
    )
    level.actors[player.id] = player

    game = SimpleNamespace()
    game.player_id = player.id
    game.levels = {level.coord: level}
    game.rng = SimpleNamespace(
        randint=lambda a, b: a,
        random=lambda: 0.0,
    )
    game.log = SimpleNamespace(add=MagicMock())
    game.inventories = {}
    game._level = lambda: level
    game._player = lambda: player
    game._debug = lambda _msg: None
    game.refresh_actor_actions = MagicMock()
    game._entity_at = lambda lvl, pos: next((e for e in lvl.entities.values() if getattr(e, "pos", None) == pos), None)
    game._actor_at = lambda lvl, pos: next((a for a in lvl.actors.values() if getattr(a, "pos", None) == pos), None)
    game._spawn_entity_from_template = lambda _tid, pos, overrides=None: SimpleNamespace(
        id=f"ent_{len(level.entities) + 1}",
        pos=pos,
        tags=(overrides or {}).get("tags", {}),
    )
    game.is_hostile = lambda attacker, target: bool(
        getattr(attacker, "faction", "") == "player" and getattr(target, "faction", "") == "hostile"
    ) or bool(
        getattr(attacker, "faction", "") == "hostile" and getattr(target, "faction", "") == "player"
    )
    game._kill_actor = lambda lvl, actor, **kwargs: (
        setattr(actor, "alive", False),
        lvl.actors.pop(actor.id, None),
        lvl.entities.pop(actor.id, None),
    )
    game.add_corruption_anchor = MagicMock(return_value="rune_anchor_test")
    game._ensure_overmap_ready = MagicMock()
    game.build_tile_julia_grid = MagicMock()
    game.cfg = SimpleNamespace(world_width=40, world_height=30)
    game.tile_julia_grid = {
        "x": [0.0] * (game.cfg.world_width * 8),
        "y": [0.0] * (game.cfg.world_height * 8),
        "step_x": 0.01,
        "step_y": 0.01,
    }
    return game, level, player


def test_attach_siege_to_level_creates_runtime_state():
    game, level, _player = _make_game_and_level()

    rune_anchor_sieges.attach_siege_to_level(game, level, "starter_anchor")

    siege = getattr(level, "rune_anchor_siege", None)
    assert siege is not None
    assert siege.siege_id == "starter_anchor"
    assert len(siege.fractures) >= 1
    assert any(getattr(e, "tags", {}).get("rune_anchor_siege_id") == "starter_anchor" for e in level.entities.values())


def test_channel_fracture_consumes_crystal_and_advances_state():
    game, level, player = _make_game_and_level()
    rune_anchor_sieges.attach_siege_to_level(game, level, "starter_anchor")
    siege = level.rune_anchor_siege
    assert siege is not None

    # Pre-complete all but one fracture so a single channel can enter stabilize phase.
    target = siege.fractures[0]
    for fracture in siege.fractures[1:]:
        fracture.repaired = True
        fracture.progress = fracture.required_channels

    player.pos = target.pos
    crystal = SimpleNamespace(id="c1", tags={"item_type": "coherence_crystal", "quantity": 2})
    game.inventories[player.id] = [crystal]

    with patch("edgecaster.systems.rune_anchor_sieges._spawn_wave") as mock_wave:
        rune_anchor_sieges.channel_fracture(game, player.id)

    assert target.repaired is True
    assert int(crystal.tags.get("quantity", 0)) == 1
    assert siege.phase == "stabilize"
    assert mock_wave.called


def test_sync_zone_siege_auto_grants_and_revokes_actions():
    game, level, player = _make_game_and_level()
    rune_anchor_sieges.attach_siege_to_level(game, level, "starter_anchor")
    siege = level.rune_anchor_siege
    assert siege is not None

    rune_anchor_sieges.sync_zone_siege(game, level, level.coord)
    tags = player.tags
    intrinsic = tags.get("intrinsic_actions", [])
    assert "anchor_channel" in intrinsic
    assert "anchor_stabilize" in intrinsic
    assert "anchor_purge" in intrinsic
    assert siege.active is True

    # Leave the zone: current level has no siege, old siege should pause and grants should revoke.
    next_level = SimpleNamespace(world=DummyWorld(), actors=level.actors, entities={}, coord=(9, 9, 0), current_tick=0)
    game.levels[next_level.coord] = next_level
    game._level = lambda: next_level

    rune_anchor_sieges.sync_zone_siege(game, next_level, next_level.coord)

    intrinsic_after = player.tags.get("intrinsic_actions", [])
    assert "anchor_channel" not in intrinsic_after
    assert "anchor_stabilize" not in intrinsic_after
    assert "anchor_purge" not in intrinsic_after
    assert siege.active is False


def test_catastrophe_pulse_telegraph_and_resolve_hits_player():
    game, level, player = _make_game_and_level()
    rune_anchor_sieges.attach_siege_to_level(game, level, "starter_anchor")
    siege = level.rune_anchor_siege
    assert siege is not None

    rune_anchor_sieges._begin_catastrophe_telegraph(game, level, siege, tick=0)
    assert siege.pulse_warning_left > 0
    assert siege.pulse_tiles

    player.pos = tuple(siege.pulse_tiles[0])
    hp_before = int(player.stats.hp)
    rune_anchor_sieges._resolve_catastrophe_pulse(game, level, siege, tick=1)

    assert int(player.stats.hp) < hp_before
    assert siege.pulse_count >= 1


def test_sapper_reopens_repaired_fracture():
    game, level, _player = _make_game_and_level()
    rune_anchor_sieges.attach_siege_to_level(game, level, "starter_anchor")
    siege = level.rune_anchor_siege
    assert siege is not None

    fracture = siege.fractures[0]
    fracture.repaired = True
    fracture.progress = fracture.required_channels
    before_req = int(fracture.required_channels)

    sapper = SimpleNamespace(
        id="enemy_sapper",
        name="Sapper Imp",
        pos=fracture.pos,
        actions=("move", "wait"),
        tags={
            "rune_siege_role": "sapper",
            "rune_siege_id": siege.siege_id,
            "rune_siege_sabotage_cd": 0,
        },
        faction="hostile",
        alive=True,
        stats=DummyStats(8, 8),
    )
    level.actors[sapper.id] = sapper
    level.entities[sapper.id] = sapper

    rune_anchor_sieges._tick_sapper_pressure(game, level, siege, tick=3)

    assert fracture.repaired is False
    assert int(fracture.required_channels) >= before_req


def test_anchor_purge_consumes_crystals_and_hits_hostiles():
    game, level, player = _make_game_and_level()
    rune_anchor_sieges.attach_siege_to_level(game, level, "starter_anchor")
    siege = level.rune_anchor_siege
    assert siege is not None

    player.pos = siege.anchor_pos
    crystal = SimpleNamespace(id="c1", tags={"item_type": "coherence_crystal", "quantity": 4})
    game.inventories[player.id] = [crystal]

    enemy = SimpleNamespace(
        id="enemy_1",
        name="Imp",
        pos=(int(siege.anchor_pos[0]) + 1, int(siege.anchor_pos[1])),
        actions=("move", "wait"),
        tags={},
        faction="hostile",
        alive=True,
        stats=DummyStats(16, 16),
    )
    level.actors[enemy.id] = enemy
    level.entities[enemy.id] = enemy

    hp_before = int(enemy.stats.hp)
    with patch("edgecaster.systems.rune_anchor_sieges._spawn_wave"):
        rune_anchor_sieges.purge_anchor(game, player.id)

    assert int(crystal.tags.get("quantity", 0)) == 2
    assert int(enemy.stats.hp) < hp_before
