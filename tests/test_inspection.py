from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems.inspection import describe_abs_tile_at
from edgecaster.systems.spatial_index import SpatialIndex


def test_describe_abs_tile_uses_spatial_index_entry() -> None:
    game = SimpleNamespace()
    game.zone_coord = (0, 0, 0)
    game.entity_lod_delta_min = -5.0
    game.entity_lod_delta_max = 5.0
    game.entity_lod_fade_width = 0.0
    game.look_lod_tolerance = 0.75
    game.spatial_index = SpatialIndex()
    game.world_entity_index = None
    game.attn_store = None
    game.zone_local_from_abs = lambda abs_pos, depth=0, clamp_to_world=True: ((1, 0, depth), (0, 0))
    game.get_zone_for_render = lambda zone: None
    game._size_for_render = lambda obj: 1.0

    obj = SimpleNamespace(
        id="distant_tower",
        kind="site",
        name="Distant Tower",
        glyph="T",
        description="A tower glints on the horizon.",
        abs_pos=(10, 10),
    )
    game.spatial_index.add_or_update(obj, (10.0, 10.0, 11.0, 11.0), 0, "proxy")

    text = describe_abs_tile_at(game, (10, 10), cam_lod=0.0)

    assert "T" in text
    assert "A tower glints on the horizon." in text
