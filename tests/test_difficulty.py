from __future__ import annotations

from types import SimpleNamespace

from edgecaster.state.pois import ABSRect, POISpec, StructureSpec
from edgecaster.systems.difficulty import DifficultyConfig, compute_zone_difficulty
from edgecaster.systems.spatial_index import SpatialIndex


def test_compute_zone_difficulty_reads_poi_bonus_from_spatial_index_without_registry() -> None:
    idx = SpatialIndex(bin_size=8)
    footprint = ABSRect.from_zone_coord(4, 2, 10, 10)
    poi = POISpec(
        id="poi_lab",
        kind="lab_site",
        name="Lab",
        footprint=footprint,
        depth=0,
        anchor_abs=footprint.center,
        structure_specs=[StructureSpec(kind="lab")],
    )
    idx.add_or_update(
        poi,
        (float(footprint.x0), float(footprint.y0), float(footprint.x1), float(footprint.y1)),
        0,
        "collapsed",
        source="poi_registry",
        kind="lab_site",
    )

    game = SimpleNamespace(
        cfg=SimpleNamespace(world_map_screens=8, seed=1, world_width=10, world_height=10),
        spatial_index=idx,
        poi_registry=None,
        site_registry=None,
        corruption_seed=1337,
        corruption_hotspots=[],
        corruption_anchors=[],
        corruption_spline_weight=0.0,
        overmap_params=None,
        tile_julia_grid=None,
        zone_difficulty_overrides={},
        zone_coord=(99, 99, 0),
    )
    config = DifficultyConfig(
        gradient_start=2.0,
        gradient_end=3.0,
        noise_scale=0.0,
        safe_radius=-1,
        safe_cap=1.0,
        corruption_weight=0.0,
        site_bonuses={},
        poi_structure_bonuses={"lab": 0.35},
    )

    danger, sources = compute_zone_difficulty(game, (4, 2, 0), config=config)

    assert sources["poi_bonus"] == 0.35
    assert abs(danger - 0.35) < 1e-9
