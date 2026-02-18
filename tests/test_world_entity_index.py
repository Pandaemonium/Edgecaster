from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems.world_entity_index import WorldEntityIndex


def test_large_entity_is_queryable_away_from_center_zone() -> None:
    idx = WorldEntityIndex(zone_w=10, zone_h=10)
    ent = SimpleNamespace(id="continent_a", abs_pos=(50, 50), abs_size=60.0, tags={})
    idx.add(ent, zone_coord=(5, 5, 0), local_pos=(0, 0))

    # Center is at (50,50), but size=60 means footprint should cover roughly [20,80)^2.
    refs = idx.query_abs_rect((22.0, 24.0, 24.0, 26.0), z=0)
    ids = {str(getattr(r.ent, "id", "")) for r in refs}
    assert "continent_a" in ids


def test_query_deduplicates_entities_indexed_into_multiple_zone_cells() -> None:
    idx = WorldEntityIndex(zone_w=10, zone_h=10)
    ent = SimpleNamespace(id="continent_a", abs_pos=(50, 50), abs_size=60.0, tags={})
    idx.add(ent, zone_coord=(5, 5, 0), local_pos=(0, 0))

    refs = idx.query_abs_rect((0.0, 0.0, 100.0, 100.0), z=0)
    ids = [str(getattr(r.ent, "id", "")) for r in refs]
    assert ids.count("continent_a") == 1


def test_explicit_footprint_abs_takes_precedence_over_abs_size() -> None:
    idx = WorldEntityIndex(zone_w=10, zone_h=10)
    ent = SimpleNamespace(
        id="river_a",
        abs_pos=(50, 50),
        abs_size=4.0,
        footprint_abs=(10.0, 10.0, 30.0, 12.0),
        tags={},
    )
    idx.add(ent, zone_coord=(5, 5, 0), local_pos=(0, 0))

    inside = idx.query_abs_rect((12.0, 10.0, 13.0, 11.0), z=0)
    inside_ids = {str(getattr(r.ent, "id", "")) for r in inside}
    assert "river_a" in inside_ids

    outside = idx.query_abs_rect((49.0, 49.0, 51.0, 51.0), z=0)
    outside_ids = {str(getattr(r.ent, "id", "")) for r in outside}
    assert "river_a" not in outside_ids
