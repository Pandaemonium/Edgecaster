from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from edgecaster.state.actors import Actor
from edgecaster.state.entities import Entity
from edgecaster.state.world import World
from edgecaster import spawn_factory
from edgecaster.systems import entity_ops
from edgecaster.systems import footprints


def _level(*, entities: dict[str, object] | None = None, actors: dict[str, object] | None = None) -> object:
    return SimpleNamespace(
        entities=dict(entities or {}),
        actors=dict(actors or {}),
    )


def test_rect_overlaps_basic() -> None:
    assert footprints.rect_overlaps((0, 0, 2, 2), (1, 1, 3, 3))
    assert not footprints.rect_overlaps((0, 0, 1, 1), (1, 1, 2, 2))


def test_entity_footprint_default_is_single_tile() -> None:
    ent = Entity(id="e1", name="Stone", pos=(4, 5))
    assert footprints.entity_footprint_local(ent) == (4.0, 5.0, 5.0, 6.0)
    assert footprints.entity_overlaps_tile(ent, (4, 5))
    assert not footprints.entity_overlaps_tile(ent, (5, 5))


def test_entity_footprint_from_explicit_rect() -> None:
    ent = Entity(id="e2", name="LargeThing", pos=(0, 0))
    ent.footprint = (2.0, 3.0, 6.0, 7.0)
    assert footprints.entity_overlaps_tile(ent, (2, 3))
    assert footprints.entity_overlaps_tile(ent, (5, 6))
    assert not footprints.entity_overlaps_tile(ent, (6, 7))


def test_blocking_entity_at_uses_footprint_overlap() -> None:
    wall = Entity(id="wall_1", name="Wall", pos=(1, 1), blocks_movement=True)
    wall.footprint = (3.0, 3.0, 6.0, 6.0)
    lvl = _level(entities={wall.id: wall})
    got = entity_ops.blocking_entity_at(lvl, (5, 5))
    assert got is wall


def test_blocking_rule_tag_can_disable_blocking_for_kind() -> None:
    city = Entity(id="city_1", name="City", pos=(8, 8), blocks_movement=True)
    city.footprint = (8.0, 8.0, 12.0, 12.0)
    city.tags["blocking_rule"] = "never"
    lvl = _level(entities={city.id: city})
    assert entity_ops.blocking_entity_at(lvl, (9, 9)) is None


def test_entity_at_prefers_non_actor_when_both_overlap_tile() -> None:
    actor = Actor(id="a1", name="Guard", pos=(10, 10))
    actor.footprint = (10.0, 10.0, 12.0, 12.0)
    item = Entity(id="i1", name="Dagger", pos=(11, 11), kind="item")
    item.footprint = (11.0, 11.0, 12.0, 12.0)

    lvl = _level(
        entities={actor.id: actor, item.id: item},
        actors={actor.id: actor},
    )
    got = entity_ops.entity_at(lvl, (11, 11))
    assert got is item


def test_spawn_factory_initializes_explicit_runtime_footprint_fields() -> None:
    spec = {
        "id": "test_city",
        "name": "Test City",
        "glyph": "C",
        "kind": "structure",
        "blocks_movement": False,
        "footprint": {"x0": 4.0, "y0": 6.0, "x1": 8.0, "y1": 10.0},
    }
    ent = spawn_factory.build_entity_from_spec(
        spec=spec,
        eid="city_1",
        pos=(4, 6),
        abs_pos=(44, 66),
    )
    assert ent.footprint_local == (4.0, 6.0, 8.0, 10.0)
    assert ent.footprint_abs == (44.0, 66.0, 48.0, 70.0)


def test_set_pos_and_set_abs_pos_translate_explicit_footprints() -> None:
    ent = Entity(id="e_move", name="Mover", pos=(2, 2))
    ent.footprint_local = (2.0, 2.0, 4.0, 4.0)
    ent.footprint_abs = (20.0, 20.0, 22.0, 22.0)
    ent.abs_pos = (20, 20)
    ent.footprint_follows_pos = True
    ent._footprint_anchor_local = (2, 2)
    ent._footprint_anchor_abs = (20, 20)

    ent.set_pos((5, 3))
    ent.set_abs_pos((23, 21))

    assert ent.footprint_local == (5.0, 3.0, 7.0, 5.0)
    assert ent.footprint_abs == (23.0, 21.0, 25.0, 23.0)


def test_world_walkable_for_rect_checks_all_overlapped_tiles() -> None:
    world = World(width=5, height=5)
    world.get_tile(1, 1).walkable = False

    assert not footprints.world_walkable_for_rect(world, (0.0, 0.0, 2.0, 2.0))
    assert footprints.world_walkable_for_rect(world, (2.0, 2.0, 3.0, 3.0))


def test_footprint_local_rect_from_spec_uses_anchor_for_w_h() -> None:
    spec = {"id": "giant", "tags": {"footprint_w": 3, "footprint_h": 2}}
    rect = footprints.footprint_local_rect_from_spec(spec, (10, 20))
    assert rect == (10.0, 20.0, 13.0, 22.0)


def test_entity_blocks_movement_in_rect_respects_rule_override() -> None:
    city = Entity(id="city_2", name="City", pos=(0, 0), blocks_movement=True)
    city.footprint_local = (0.0, 0.0, 4.0, 4.0)
    city.tags["blocking_rule"] = "never"
    assert not footprints.entity_blocks_movement_in_rect(city, (1.0, 1.0, 2.0, 2.0))

    wall = Entity(id="wall_2", name="Wall", pos=(0, 0), blocks_movement=False)
    wall.footprint_local = (0.0, 0.0, 4.0, 4.0)
    wall.tags["blocking_rule"] = "always"
    assert footprints.entity_blocks_movement_in_rect(wall, (1.0, 1.0, 2.0, 2.0))


def test_first_actor_overlapping_rect_uses_footprints() -> None:
    mover = Actor(id="a_move", name="Mover", pos=(0, 0))
    mover.footprint_local = (0.0, 0.0, 1.0, 1.0)

    guard = Actor(id="a_guard", name="Guard", pos=(3, 3))
    guard.footprint_local = (3.0, 3.0, 5.0, 5.0)

    lvl = _level(actors={mover.id: mover, guard.id: guard})
    got = entity_ops.first_actor_overlapping_rect(
        lvl,
        (4.0, 4.0, 5.0, 5.0),
        exclude_id=mover.id,
    )
    assert got is guard


def test_blocking_entity_overlapping_rect_ignores_actor_entities() -> None:
    actor = Actor(id="a_block", name="Blocker", pos=(2, 2))
    actor.blocks_movement = True
    actor.footprint_local = (2.0, 2.0, 3.0, 3.0)

    wall = Entity(id="wall_3", name="Wall", pos=(2, 2), blocks_movement=True)
    wall.footprint_local = (2.0, 2.0, 4.0, 4.0)

    lvl = _level(
        entities={actor.id: actor, wall.id: wall},
        actors={actor.id: actor},
    )
    got = entity_ops.blocking_entity_overlapping_rect(lvl, (2.0, 2.0, 3.0, 3.0))
    assert got is wall


def test_toggle_door_updates_runtime_and_persists_open_state() -> None:
    world = World(width=4, height=4)
    tile = world.get_tile(1, 1)
    tile.walkable = False
    tile.glyph = "+"
    door = Entity(id="runtime:door:1", name="Door", pos=(1, 1), blocks_movement=True)
    door.glyph = "+"
    door.tags["lineage_id"] = "site:test:door:1"
    door.tags["door_state"] = "closed"
    level = SimpleNamespace(world=world, need_fov=False)
    game = SimpleNamespace(log=MagicMock(), patch_entity_state=MagicMock(), _update_fov=MagicMock())

    entity_ops.toggle_door(game, door, level, notify=True)

    assert door.tags["door_state"] == "open"
    assert door.blocks_movement is False
    assert door.glyph == "/"
    assert tile.walkable is True
    assert tile.glyph == "."
    assert level.need_fov is True
    game.patch_entity_state.assert_called_once_with(
        door,
        {
            "last_known_tags": {"door_state": "open"},
            "last_known_glyph": "/",
            "last_known_blocks_movement": False,
        },
    )
    game._update_fov.assert_called_once_with(level)


def test_toggle_door_updates_runtime_and_persists_closed_state() -> None:
    world = World(width=4, height=4)
    tile = world.get_tile(1, 1)
    tile.walkable = True
    tile.glyph = "."
    door = Entity(id="runtime:door:2", name="Door", pos=(1, 1), blocks_movement=False)
    door.glyph = "/"
    door.tags["lineage_id"] = "site:test:door:2"
    door.tags["door_state"] = "open"
    level = SimpleNamespace(world=world, need_fov=False)
    game = SimpleNamespace(log=MagicMock(), patch_entity_state=MagicMock(), _update_fov=MagicMock())

    entity_ops.toggle_door(game, door, level, notify=True)

    assert door.tags["door_state"] == "closed"
    assert door.blocks_movement is True
    assert door.glyph == "+"
    assert tile.walkable is False
    assert tile.glyph == "+"
    assert level.need_fov is True
    game.patch_entity_state.assert_called_once_with(
        door,
        {
            "last_known_tags": {"door_state": "closed"},
            "last_known_glyph": "+",
            "last_known_blocks_movement": True,
        },
    )
    game._update_fov.assert_called_once_with(level)
