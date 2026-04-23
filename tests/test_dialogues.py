from __future__ import annotations

from types import SimpleNamespace

from edgecaster.content import dialogues
from edgecaster.systems.spatial_index import SpatialIndex


def test_find_site_zonecoord_reads_spatial_index_before_legacy_world_index() -> None:
    class _LegacyWorldIndex:
        def __getattribute__(self, name: str):
            if name == "_by_zone":
                raise AssertionError("SpatialIndex-backed dialogue lookup should not scan WorldEntityIndex")
            return super().__getattribute__(name)

    spatial_index = SpatialIndex(bin_size=8)
    site = SimpleNamespace(
        id="site:starttsgard",
        tags={"site_kind": "starttsgard"},
        abs_pos=(24, 35),
        zone_coord=(2, 3, 0),
        local_pos=(4, 5),
    )
    spatial_index.add_or_update(
        site,
        (24.0, 35.0, 25.0, 36.0),
        0,
        "proxy",
        source="world_entity_index",
    )
    game = SimpleNamespace(
        cfg=SimpleNamespace(world_width=10, world_height=10),
        spatial_index=spatial_index,
        world_entity_index=_LegacyWorldIndex(),
    )

    assert dialogues._find_site_zonecoord(game, "starttsgard") == (2, 3, 0, 4, 5)


def test_find_site_zonecoord_works_without_world_entity_index() -> None:
    spatial_index = SpatialIndex(bin_size=8)
    site = SimpleNamespace(
        id="site:starttsgard",
        tags={"site_kind": "starttsgard"},
        abs_pos=(24, 35),
    )
    spatial_index.add_or_update(
        site,
        (24.0, 35.0, 25.0, 36.0),
        0,
        "collapsed",
    )
    game = SimpleNamespace(
        cfg=SimpleNamespace(world_width=10, world_height=10),
        spatial_index=spatial_index,
        world_entity_index=None,
    )

    assert dialogues._find_site_zonecoord(game, "starttsgard") == (2, 3, 0, 4, 5)
