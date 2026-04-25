from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems import attention as attention_system
from edgecaster.systems.spatial_index import SpatialIndex


def test_attention_store_uses_footprint_rect_for_queries() -> None:
    spatial_index = SpatialIndex(bin_size=4)
    game = SimpleNamespace(spatial_index=spatial_index)
    obj = SimpleNamespace(
        id="wide_entity",
        abs_pos=(40, 40),
        footprint_abs=(10.0, 10.0, 30.0, 12.0),
    )

    attention_system._attn_store_stage(game, obj, abs_x=40, abs_y=40, zz=0)
    hits = spatial_index.query_rect((12.0, 10.0, 13.0, 11.0), 0)

    assert [entry.obj.id for entry in hits] == ["wide_entity"]


def test_attention_store_deduplicates_entities_spanning_multiple_cells() -> None:
    spatial_index = SpatialIndex(bin_size=4)
    game = SimpleNamespace(spatial_index=spatial_index)
    obj = SimpleNamespace(
        id="giant_entity",
        abs_pos=(32, 32),
        footprint_abs=(0.0, 0.0, 70.0, 70.0),
    )

    attention_system._attn_store_stage(game, obj, abs_x=32, abs_y=32, zz=0)
    hits = spatial_index.query_rect((0.0, 0.0, 80.0, 80.0), 0)

    assert [entry.obj.id for entry in hits] == ["giant_entity"]


def test_attention_store_preserves_point_anchor_queries() -> None:
    spatial_index = SpatialIndex(bin_size=4)
    game = SimpleNamespace(spatial_index=spatial_index)
    obj = SimpleNamespace(
        id="point_entity",
        abs_pos=(9, 11),
    )

    attention_system._attn_store_stage(game, obj, abs_x=9, abs_y=11, zz=0)
    hits = spatial_index.query_rect((8.0, 10.0, 10.0, 12.0), 0)

    assert [entry.obj.id for entry in hits] == ["point_entity"]


def test_attention_store_get_and_ids_can_read_back_spatial_entries() -> None:
    spatial_index = SpatialIndex(bin_size=4)
    game = SimpleNamespace(spatial_index=spatial_index)
    obj = SimpleNamespace(
        id="staged_entity",
        abs_pos=(5, 6),
    )

    attention_system._attn_store_stage(game, obj, abs_x=5, abs_y=6, zz=0)

    assert attention_system._attn_store_get(game, "staged_entity") is obj
    assert attention_system._attn_store_has(game, "staged_entity") is True
    assert attention_system._attn_store_ids(game) == {"staged_entity"}
