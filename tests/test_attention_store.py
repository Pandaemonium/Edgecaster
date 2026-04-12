from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems.attention import AttentionCellStore


def test_attention_store_uses_footprint_rect_for_queries() -> None:
    store = AttentionCellStore(bin_size=16)
    obj = SimpleNamespace(
        id="wide_entity",
        abs_pos=(40, 40),
        footprint_abs=(10.0, 10.0, 30.0, 12.0),
    )

    store.stage(obj, abs_x=40, abs_y=40, zz=0)
    hits = store.query_abs_rect((12.0, 10.0, 13.0, 11.0), zz=0)

    assert [item[0].id for item in hits] == ["wide_entity"]


def test_attention_store_deduplicates_entities_spanning_multiple_cells() -> None:
    store = AttentionCellStore(bin_size=16)
    obj = SimpleNamespace(
        id="giant_entity",
        abs_pos=(32, 32),
        footprint_abs=(0.0, 0.0, 70.0, 70.0),
    )

    store.stage(obj, abs_x=32, abs_y=32, zz=0)
    hits = store.query_abs_rect((0.0, 0.0, 80.0, 80.0), zz=0)

    assert [item[0].id for item in hits] == ["giant_entity"]


def test_attention_store_preserves_point_anchor_queries() -> None:
    store = AttentionCellStore(bin_size=16)
    obj = SimpleNamespace(
        id="point_entity",
        abs_pos=(9, 11),
    )

    store.stage(obj, abs_x=9, abs_y=11, zz=0)
    hits = store.query_abs_rect((8.0, 10.0, 10.0, 12.0), zz=0)

    assert [item[0].id for item in hits] == ["point_entity"]
