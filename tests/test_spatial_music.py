from __future__ import annotations

from types import SimpleNamespace

from edgecaster.scenes.spatial_music import SpatialMusicDirector
from edgecaster.state.pois import ABSRect, POISpec, StructureSpec
from edgecaster.systems.spatial_index import SpatialIndex


def test_spatial_music_tagged_world_entity_prefers_spatial_index() -> None:
    director = SpatialMusicDirector()
    game = SimpleNamespace(
        zone_coord=(0, 0, 0),
        spatial_index=SpatialIndex(),
        world_entity_index=None,
    )
    leviathan = SimpleNamespace(
        id="leviathan_1",
        kind="site",
        abs_pos=(12, 8),
        tags={"leviathan_music": True, "abs_size": 9},
    )
    game.spatial_index.add_or_update(leviathan, (8.0, 4.0, 16.0, 12.0), 0, "proxy")

    found = director._find_tagged_world_entity(game, (0.0, 0.0, 20.0, 20.0), tag="leviathan_music")

    assert found == (12.0, 8.0, 9.0)


def test_spatial_music_colosseum_check_reads_poi_from_spatial_index_without_registry() -> None:
    director = SpatialMusicDirector()
    footprint = ABSRect.from_zone_coord(1, 1, 10, 10)
    poi = POISpec(
        id="poi_colosseum",
        kind="colosseum",
        name="Colosseum",
        footprint=footprint,
        depth=0,
        anchor_abs=footprint.center,
        structure_specs=[StructureSpec(kind="colosseum_arena")],
    )
    game = SimpleNamespace(
        zone_coord=(1, 1, 0),
        spatial_index=SpatialIndex(),
        poi_registry=None,
    )
    game.spatial_index.add_or_update(
        poi,
        (float(footprint.x0), float(footprint.y0), float(footprint.x1), float(footprint.y1)),
        0,
        "collapsed",
        kind="colosseum",
        source="poi_registry",
    )

    assert director._player_in_colosseum(game, footprint.center) is True
