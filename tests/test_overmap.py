"""
Unit tests for the overmap system (systems/overmap.py).

Tests Julia grid building, corruption management, and zone coordinate mapping.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

from edgecaster.state.pois import ABSRect, POISpec, StructureSpec
from edgecaster.systems.poi_registry import POIRegistry

from edgecaster.systems.overmap import (
    build_tile_julia_grid,
    init_overmap_params_and_grid,
    init_rune_anchors,
    start_world_map_thread,
    ensure_overmap_ready,
    jx_jy_slices_for_zone,
    set_corruption_level,
    set_corruption_spline_weight,
    add_corruption_hotspot,
    alloc_rune_anchor_poi_id,
    add_corruption_anchor,
    refresh_corruption_visuals,
)


def _make_poi_registry(zone_w: int = 40, zone_h: int = 40) -> POIRegistry:
    return POIRegistry(zone_w=zone_w, zone_h=zone_h)


def _add_registry_poi(
    registry: POIRegistry,
    poi_id: str,
    *,
    kind: str,
    coord: tuple[int, int, int],
) -> None:
    zx, zy, zz = coord
    footprint = ABSRect.from_zone_coord(zx, zy, registry.zone_w, registry.zone_h)
    registry.add(
        POISpec(
            id=poi_id,
            kind=kind,
            name=poi_id,
            footprint=footprint,
            depth=zz,
            anchor_abs=footprint.center,
            npc_specs=[],
            structure_specs=[StructureSpec(kind=kind, relative_offset=(0, 0), tags={})],
            seed=0,
            tags={},
        )
    )


class TestBuildTileJuliaGrid:
    """Tests for build_tile_julia_grid function."""

    def test_builds_grid_from_overmap_params(self):
        """Should build grid from overmap_params extents."""
        game = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_map_screens = 2
        game.cfg.world_width = 40
        game.cfg.world_height = 40
        game.overmap_params = {
            "view_min_jx": -1.0,
            "view_max_jx": 1.0,
            "view_min_jy": -1.0,
            "view_max_jy": 1.0,
        }
        game.tile_julia_grid = None

        build_tile_julia_grid(game)

        assert game.tile_julia_grid is not None
        assert "x" in game.tile_julia_grid
        assert "y" in game.tile_julia_grid
        assert len(game.tile_julia_grid["x"]) == 80  # 2 screens * 40 width
        assert len(game.tile_julia_grid["y"]) == 80

    def test_skips_without_overmap_params(self):
        """Should not build grid without overmap_params."""
        game = MagicMock()
        game.overmap_params = None
        game.tile_julia_grid = None

        build_tile_julia_grid(game)

        assert game.tile_julia_grid is None

    def test_skips_without_julia_extents(self):
        """Should not build grid if julia extents missing."""
        game = MagicMock()
        game.overmap_params = {"other": 123}
        game.tile_julia_grid = None

        build_tile_julia_grid(game)

        assert game.tile_julia_grid is None

    def test_grid_step_calculation(self):
        """Should calculate correct step values."""
        game = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_map_screens = 1
        game.cfg.world_width = 10
        game.cfg.world_height = 10
        game.overmap_params = {
            "view_min_jx": 0.0,
            "view_max_jx": 9.0,
            "view_min_jy": 0.0,
            "view_max_jy": 9.0,
        }
        game.tile_julia_grid = None

        build_tile_julia_grid(game)

        assert game.tile_julia_grid["step_x"] == 1.0
        assert game.tile_julia_grid["step_y"] == 1.0


class TestInitOvermapParamsAndGrid:
    """Tests for init_overmap_params_and_grid function."""

    def test_skips_if_already_initialized(self):
        """Should not reinitialize if params and grid exist."""
        game = MagicMock()
        game.overmap_params = {"existing": True}
        game.tile_julia_grid = {"x": [], "y": []}

        init_overmap_params_and_grid(game)

        # Should not have modified existing params
        assert game.overmap_params == {"existing": True}

    def test_initializes_params_from_world_map_scene(self):
        """Should initialize from WorldMapScene entry."""
        game = MagicMock()
        game.overmap_params = None
        game.tile_julia_grid = None
        game.cfg = MagicMock()
        game.cfg.world_map_screens = 2
        game.cfg.world_width = 40
        game.cfg.world_height = 40
        game.corruption_level = 1.0
        game.corruption_seed = 12345
        game.corruption_hotspots = []
        game.corruption_anchors = []
        game.corruption_spline_weight = 0.0

        mock_entry = {
            "c": complex(0.0, 0.0),
            "x_min": -1.5,
            "x_max": 1.5,
            "y_min": -1.5,
            "y_max": 1.5,
        }

        with patch("edgecaster.scenes.world_map_scene.WorldMapScene") as mock_wm_class:
            mock_wm = MagicMock()
            mock_wm._pick_visual_entry.return_value = mock_entry
            mock_wm_class.return_value = mock_wm

            with patch("edgecaster.systems.overmap.init_rune_anchors"):
                with patch("edgecaster.systems.overmap.start_world_map_thread"):
                    init_overmap_params_and_grid(game)

        assert game.overmap_params is not None
        assert game.overmap_params["visual_c"] == mock_entry["c"]
        assert game.overmap_params["view_min_jx"] == -1.5


class TestInitRuneAnchors:
    """Tests for init_rune_anchors function."""

    def test_skips_if_anchors_exist(self):
        """Should not reinitialize if anchors already exist."""
        game = MagicMock()
        game.corruption_anchors = [(0.0, 0.0, 0.1, 1.0)]
        game.poi_registry = _make_poi_registry()

        init_rune_anchors(game)

        # Should not have been modified
        assert len(game.corruption_anchors) == 1

    def test_skips_without_grid(self):
        """Should not initialize without tile_julia_grid."""
        game = MagicMock()
        game.corruption_anchors = []
        game.tile_julia_grid = None
        game.poi_registry = _make_poi_registry()

        init_rune_anchors(game)

        assert len(game.corruption_anchors) == 0

    def test_creates_anchors_with_valid_grid(self):
        """Should create rune anchors from grid."""
        game = MagicMock()
        game.corruption_anchors = []
        game.corruption_seed = 12345
        game.cfg = MagicMock()
        game.cfg.world_map_screens = 10
        game.cfg.world_width = 40
        game.cfg.world_height = 40
        game.overmap_params = {}
        game.poi_registry = _make_poi_registry()

        # Build valid grid
        total_x = 10 * 40
        total_y = 10 * 40
        game.tile_julia_grid = {
            "x": [i * 0.01 for i in range(total_x)],
            "y": [i * 0.01 for i in range(total_y)],
        }

        init_rune_anchors(game)

        # Should have created some anchors (target is max(12, screens * 0.6))
        assert len(game.corruption_anchors) >= 6


class TestStartWorldMapThread:
    """Tests for start_world_map_thread function."""

    def test_updates_render_params(self):
        """Should update game render parameters."""
        game = MagicMock()
        game.world_map_render_width = 0
        game.world_map_render_height = 0
        game.world_map_render_span = 0
        game.world_map_view = None
        game.world_map_view_token = 0
        game.world_map_rendering = False
        game.world_map_cache_dict = {}
        game.corruption_version = 0
        game.world_map_version = 0
        game._debug = MagicMock()

        with patch("edgecaster.systems.overmap.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            start_world_map_thread(game, width=800, height=600, span=16, view_token=1)

        assert game.world_map_render_width == 800
        assert game.world_map_render_height == 600
        assert game.world_map_render_span == 16
        assert game.world_map_view_token == 1

    def test_queues_request_if_already_rendering(self):
        """Should queue request if render in progress."""
        game = MagicMock()
        game.world_map_render_width = 800
        game.world_map_render_height = 600
        game.world_map_render_span = 16
        game.world_map_view = None
        game.world_map_view_token = 0
        game.world_map_rendering = True  # Already rendering
        game.world_map_cache_dict = {}
        game.corruption_version = 0
        game._debug = MagicMock()

        start_world_map_thread(game, view_token=2)

        assert game._world_map_pending_request is not None
        assert game._world_map_pending_request["view_token"] == 2

    def test_skips_if_cache_hit(self):
        """Should skip render if already cached."""
        game = MagicMock()
        game.world_map_render_width = 800
        game.world_map_render_height = 600
        game.world_map_render_span = 16
        game.world_map_view = None
        game.world_map_view_token = 1
        game.world_map_rendering = False
        game.corruption_version = 0
        game._debug = MagicMock()

        key = (800, 600, 16, 1, 0)
        game.world_map_cache_dict = {key: {"surface": MagicMock()}}

        with patch("edgecaster.systems.overmap.threading.Thread") as mock_thread:
            start_world_map_thread(game, view_token=1)
            mock_thread.assert_not_called()


class TestEnsureOvermapReady:
    """Tests for ensure_overmap_ready function."""

    def test_skips_if_ready(self):
        """Should not reinitialize if already ready."""
        game = MagicMock()
        game.overmap_params = {"ready": True}
        game.tile_julia_grid = {"x": [], "y": []}

        with patch("edgecaster.systems.overmap.init_overmap_params_and_grid") as mock_init:
            ensure_overmap_ready(game)
            mock_init.assert_not_called()

    def test_initializes_if_not_ready(self):
        """Should initialize if params/grid missing."""
        game = MagicMock()
        game.overmap_params = None
        game.tile_julia_grid = None

        with patch("edgecaster.systems.overmap.init_overmap_params_and_grid") as mock_init:
            ensure_overmap_ready(game)
            mock_init.assert_called_once_with(game)


class TestJxJySlicesForZone:
    """Tests for jx_jy_slices_for_zone function."""

    def test_returns_none_without_grid(self):
        """Should return (None, None) without grid."""
        game = MagicMock()
        game.tile_julia_grid = None

        jx, jy = jx_jy_slices_for_zone(game, (0, 0, 0))

        assert jx is None
        assert jy is None

    def test_returns_none_for_underground(self):
        """Should return (None, None) for non-surface zones."""
        game = MagicMock()
        game.tile_julia_grid = {"x": [0.0], "y": [0.0]}

        jx, jy = jx_jy_slices_for_zone(game, (0, 0, 1))

        assert jx is None
        assert jy is None

    def test_returns_correct_slices(self):
        """Should return correct Julia coordinate slices."""
        game = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_width = 4
        game.cfg.world_height = 4

        # Grid for 2x2 zones with 4x4 tiles each = 8x8 total
        game.tile_julia_grid = {
            "x": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
            "y": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        }

        # Zone (1, 0, 0) should get tiles 4-7 in x, 0-3 in y
        jx, jy = jx_jy_slices_for_zone(game, (1, 0, 0))

        assert jx == [0.4, 0.5, 0.6, 0.7]
        assert jy == [0.0, 0.1, 0.2, 0.3]


class TestSetCorruptionLevel:
    """Tests for set_corruption_level function."""

    def test_updates_corruption_level(self):
        """Should update corruption level and version."""
        game = MagicMock()
        game.corruption_level = 1.0
        game.corruption_version = 0
        game.overmap_params = {}
        game.levels = {}

        with patch("edgecaster.systems.overmap.start_world_map_thread"):
            set_corruption_level(game, 2.5)

        assert game.corruption_level == 2.5
        assert game.corruption_version == 1

    def test_skips_if_unchanged(self):
        """Should not update if value unchanged."""
        game = MagicMock()
        game.corruption_level = 1.0
        game.corruption_version = 0

        with patch("edgecaster.systems.overmap.refresh_corruption_visuals") as mock_refresh:
            set_corruption_level(game, 1.0)
            mock_refresh.assert_not_called()

    def test_clamps_to_zero(self):
        """Should clamp negative values to 0."""
        game = MagicMock()
        game.corruption_level = 1.0
        game.corruption_version = 0
        game.overmap_params = {}
        game.levels = {}

        with patch("edgecaster.systems.overmap.start_world_map_thread"):
            set_corruption_level(game, -5.0)

        assert game.corruption_level == 0.0


class TestSetCorruptionSplineWeight:
    """Tests for set_corruption_spline_weight function."""

    def test_updates_spline_weight(self):
        """Should update spline weight and version."""
        game = MagicMock()
        game.corruption_spline_weight = 0.0
        game.corruption_version = 0
        game.overmap_params = {}
        game.levels = {}

        with patch("edgecaster.systems.overmap.start_world_map_thread"):
            set_corruption_spline_weight(game, 0.5)

        assert game.corruption_spline_weight == 0.5
        assert game.corruption_version == 1


class TestAddCorruptionHotspot:
    """Tests for add_corruption_hotspot function."""

    def test_adds_hotspot(self):
        """Should add hotspot to list."""
        game = MagicMock()
        game.corruption_hotspots = []
        game.corruption_version = 0
        game.overmap_params = {}
        game.levels = {}

        with patch("edgecaster.systems.overmap.start_world_map_thread"):
            add_corruption_hotspot(game, 0.5, 0.5, 1.0, 0.1)

        assert len(game.corruption_hotspots) == 1
        assert game.corruption_hotspots[0] == (0.5, 0.5, 1.0, 0.1)
        assert game.corruption_version == 1


class TestAllocRuneAnchorPoiId:
    """Tests for alloc_rune_anchor_poi_id function."""

    def test_allocates_unique_id(self):
        """Should allocate unique POI id."""
        game = MagicMock()
        game.poi_registry = _make_poi_registry()
        pid = alloc_rune_anchor_poi_id(game)

        assert pid == "rune_anchor_000"

    def test_skips_existing_ids(self):
        """Should skip existing POI ids."""
        game = MagicMock()
        game.poi_registry = _make_poi_registry()
        _add_registry_poi(game.poi_registry, "rune_anchor_000", kind="rune_anchor", coord=(0, 0, 0))
        _add_registry_poi(game.poi_registry, "rune_anchor_001", kind="rune_anchor", coord=(1, 0, 0))
        pid = alloc_rune_anchor_poi_id(game)

        assert pid == "rune_anchor_002"


class TestAddCorruptionAnchor:
    """Tests for add_corruption_anchor function."""

    def test_adds_anchor_to_list(self):
        """Should add anchor to corruption_anchors."""
        game = MagicMock()
        game.corruption_anchors = []
        game.corruption_version = 0
        game.overmap_params = {}
        game.levels = {}
        game.poi_registry = _make_poi_registry()

        with patch("edgecaster.systems.overmap.start_world_map_thread"):
            add_corruption_anchor(game, 0.5, 0.5, sigma=0.1, strength=1.0)

        assert len(game.corruption_anchors) == 1
        assert game.corruption_anchors[0] == (0.5, 0.5, 0.1, 1.0)

    def test_creates_poi_if_coord_provided(self):
        """Should create POI when coord provided."""
        game = MagicMock()
        game.corruption_anchors = []
        game.corruption_version = 0
        game.overmap_params = {}
        game.levels = {}
        game.poi_locations = {}
        game.cfg = SimpleNamespace(world_width=40, world_height=40)
        game.poi_registry = _make_poi_registry()
        game.refresh_poi_locations = MagicMock()

        with patch("edgecaster.systems.overmap.start_world_map_thread"):
            pid = add_corruption_anchor(game, 0.5, 0.5, sigma=0.1, coord=(5, 5, 0))

        assert pid == "rune_anchor_000"
        poi = game.poi_registry.get(pid)
        assert poi is not None
        assert poi.kind == "rune_anchor"

    def test_returns_none_for_zero_strength(self):
        """Should return None for zero strength."""
        game = MagicMock()
        game.corruption_anchors = []

        result = add_corruption_anchor(game, 0.5, 0.5, sigma=0.1, strength=0.0)

        assert result is None
        assert len(game.corruption_anchors) == 0


class TestRefreshCorruptionVisuals:
    """Tests for refresh_corruption_visuals function."""

    def test_updates_overmap_params(self):
        """Should update corruption values in overmap_params."""
        game = MagicMock()
        # Note: Use a non-empty dict since empty dict is falsy in Python
        game.overmap_params = {"initialized": True}
        game.corruption_level = 2.0
        game.corruption_seed = 12345
        game.corruption_hotspots = [(0.5, 0.5, 1.0, 0.1)]
        game.corruption_anchors = [(0.0, 0.0, 0.1, 1.0)]
        game.corruption_spline_weight = 0.5
        game.levels = {}
        game.world_map_ready = True

        with patch("edgecaster.mapgen") as mock_mapgen:
            with patch("edgecaster.systems.overmap.start_world_map_thread"):
                refresh_corruption_visuals(game)

        assert game.overmap_params["corruption_level"] == 2.0
        assert game.overmap_params["corruption_seed"] == 12345
        assert game.overmap_params["corruption_hotspots"] == [(0.5, 0.5, 1.0, 0.1)]
        assert game.world_map_ready is False

    def test_refreshes_surface_zones(self):
        """Should refresh fractal visuals for surface zones."""
        game = MagicMock()
        game.overmap_params = {}
        game.corruption_level = 1.0
        game.corruption_seed = 12345
        game.corruption_hotspots = []
        game.corruption_anchors = []
        game.corruption_spline_weight = 0.0
        game.cfg = MagicMock()
        game.cfg.world_width = 40
        game.cfg.world_height = 40

        # Set up grid
        game.tile_julia_grid = {
            "x": [i * 0.01 for i in range(40)],
            "y": [i * 0.01 for i in range(40)],
        }

        # Mock level
        level = MagicMock()
        game.levels = {(0, 0, 0): level}

        with patch("edgecaster.mapgen") as mock_mapgen:
            with patch("edgecaster.systems.overmap.start_world_map_thread"):
                refresh_corruption_visuals(game)

        mock_mapgen.refresh_fractal_overworld_visuals.assert_called_once()

    def test_skips_underground_zones(self):
        """Should not refresh underground zones."""
        game = MagicMock()
        game.overmap_params = {}
        game.corruption_level = 1.0
        game.corruption_seed = 12345
        game.corruption_hotspots = []
        game.corruption_anchors = []
        game.corruption_spline_weight = 0.0
        game.tile_julia_grid = {"x": [], "y": []}

        # Mock underground level
        level = MagicMock()
        game.levels = {(0, 0, 1): level}  # depth 1

        with patch("edgecaster.mapgen") as mock_mapgen:
            with patch("edgecaster.systems.overmap.start_world_map_thread"):
                refresh_corruption_visuals(game)

        mock_mapgen.refresh_fractal_overworld_visuals.assert_not_called()
