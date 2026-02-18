from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edgecaster.systems import poi_spawning


def _level_with_pois(poi_ids: list[str]) -> object:
    world = SimpleNamespace(
        poi_ids=list(poi_ids),
        entry=(10, 10),
        width=64,
        height=64,
    )
    return SimpleNamespace(world=world, actors={}, entities={})


def test_starting_zone_skipped_when_starttsgard_cutover_enabled() -> None:
    level = _level_with_pois(["starting_zone"])
    registry = SimpleNamespace(get=MagicMock(side_effect=AssertionError("starting_zone should be filtered")))
    game = SimpleNamespace(
        starttsgard_cutover_enabled=True,
        poi_registry=registry,
        _debug=lambda _msg: None,
    )

    poi_spawning.spawn_poi_contents(game, level, (0, 0, 0))

    registry.get.assert_not_called()


def test_non_starting_pois_still_processed_with_cutover_enabled() -> None:
    level = _level_with_pois(["starting_zone", "inventor_workshop"])
    registry = SimpleNamespace(get=MagicMock(return_value=None))
    game = SimpleNamespace(
        starttsgard_cutover_enabled=True,
        poi_registry=registry,
        _debug=lambda _msg: None,
    )

    poi_spawning.spawn_poi_contents(game, level, (0, 0, 0))

    registry.get.assert_called_once_with("inventor_workshop")


def test_starting_zone_processed_when_cutover_disabled() -> None:
    level = _level_with_pois(["starting_zone"])
    registry = SimpleNamespace(get=MagicMock(return_value=None))
    game = SimpleNamespace(
        starttsgard_cutover_enabled=False,
        poi_registry=registry,
        _debug=lambda _msg: None,
    )

    poi_spawning.spawn_poi_contents(game, level, (0, 0, 0))

    registry.get.assert_called_once_with("starting_zone")


def test_starting_zone_bismuth_spawn_uses_template_footprint_rect() -> None:
    level = _level_with_pois(["starting_zone"])
    spec = SimpleNamespace(structure_specs=[], npc_specs=[])
    registry = SimpleNamespace(get=MagicMock(return_value=spec))
    game = SimpleNamespace(
        starttsgard_cutover_enabled=False,
        poi_registry=registry,
        _debug=lambda _msg: None,
        rng=SimpleNamespace(randint=lambda a, b: a, shuffle=lambda _xs: None),
        _spawn_entity_from_template=lambda tid, pos, overrides=None: SimpleNamespace(
            id=f"{tid}:{int(pos[0])},{int(pos[1])}",
            pos=pos,
            tags=(overrides or {}).get("tags", {}),
        ),
    )

    seen_rects: list[tuple[float, float, float, float] | None] = []

    def _check_spawnable(*_args, **kwargs):
        seen_rects.append(kwargs.get("footprint_rect"))
        return True

    with patch(
        "edgecaster.systems.poi_spawning.prototypes.resolve_proto",
        return_value={"id": "bismuth_pile", "tags": {"footprint_w": 2, "footprint_h": 2}},
    ), patch(
        "edgecaster.systems.poi_spawning.spawning_system._is_tile_spawnable",
        side_effect=_check_spawnable,
    ):
        poi_spawning.spawn_poi_contents(game, level, (0, 0, 0))

    assert seen_rects
    assert seen_rects[0] == (0.0, 0.0, 2.0, 2.0)


def test_caged_demon_npc_spawn_uses_spawn_spec_footprint() -> None:
    world = SimpleNamespace(
        poi_ids=["demo_poi"],
        entry=(10, 10),
        width=64,
        height=64,
    )
    level = SimpleNamespace(world=world, actors={}, entities={})

    npc_spec = SimpleNamespace(
        npc_id="caged_demon",
        name=None,
        glyph=None,
        color=None,
        abs_positions=[],
        offsets=[(0, 0)],
        description=None,
    )
    spec = SimpleNamespace(structure_specs=[], npc_specs=[npc_spec])
    registry = SimpleNamespace(
        get=MagicMock(return_value=spec),
        is_npc_spawned=MagicMock(return_value=False),
        is_npc_dead=MagicMock(return_value=False),
        mark_npc_spawned=MagicMock(),
    )

    actor = SimpleNamespace(
        id="cd_1",
        name="Caged Demon",
        pos=(10, 10),
        tags={},
        faction="hostile",
        actions=(),
        ai="idle",
        description="",
    )

    game = SimpleNamespace(
        starttsgard_cutover_enabled=False,
        poi_registry=registry,
        _debug=lambda _msg: None,
        _new_id=lambda: "npc_1",
        _start_regen=MagicMock(),
        abs_from_zone_local=lambda _coord, p: (int(p[0]), int(p[1])),
        rng=SimpleNamespace(randint=lambda a, b: a, shuffle=lambda _xs: None),
    )

    seen_rects: list[tuple[float, float, float, float] | None] = []

    def _check_spawnable(*_args, **kwargs):
        seen_rects.append(kwargs.get("footprint_rect"))
        return True

    with patch(
        "edgecaster.systems.poi_spawning.prototypes.resolve_proto",
        side_effect=lambda tid: {"id": tid, "tags": {"footprint_w": 3, "footprint_h": 2}}
        if tid == "caged_demon"
        else {"id": tid},
    ), patch(
        "edgecaster.systems.poi_spawning.enemy_factory.spawn_enemy",
        return_value=actor,
    ), patch(
        "edgecaster.systems.poi_spawning.spawning_system._is_tile_spawnable",
        side_effect=_check_spawnable,
    ):
        poi_spawning.spawn_poi_contents(game, level, (0, 0, 0))

    assert seen_rects
    assert seen_rects[0] == (10.0, 10.0, 13.0, 12.0)
    assert registry.mark_npc_spawned.called
