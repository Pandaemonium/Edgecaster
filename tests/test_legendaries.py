"""
Unit tests for the legendaries system (systems/legendaries.py).

These tests follow the current yoga-era POI registry path rather than the
older module-global POI dictionaries.
"""

import random
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from edgecaster.state.pois import ABSRect, POISpec, StructureSpec
from edgecaster.systems.legendaries import (
    LEGENDARY_NAMES,
    add_poi_rumor,
    alloc_legendary_lair_poi_id,
    alloc_rune_anchor_poi_id,
    discover_pois_for_level,
    get_nearest_legendary_lairs,
    init_legendaries,
)
from edgecaster.systems.poi_registry import POIRegistry
from edgecaster.systems.spatial_index import SpatialIndex


def _make_registry(zone_w: int = 60, zone_h: int = 40) -> POIRegistry:
    registry = POIRegistry(zone_w=zone_w, zone_h=zone_h)
    registry.attach_spatial_index(SpatialIndex(bin_size=32))
    return registry


def _add_registry_poi(
    registry: POIRegistry,
    poi_id: str,
    *,
    kind: str,
    coord: tuple[int, int, int],
    name: str | None = None,
) -> None:
    zx, zy, zz = coord
    footprint = ABSRect.from_zone_coord(zx, zy, registry.zone_w, registry.zone_h)
    registry.add(
        POISpec(
            id=poi_id,
            kind=kind,
            name=name or poi_id,
            footprint=footprint,
            depth=zz,
            anchor_abs=footprint.center,
            npc_specs=[],
            structure_specs=[StructureSpec(kind=kind, relative_offset=(0, 0), tags={})],
            seed=0,
            tags={},
        )
    )


class TestLegendaryNames:
    def test_names_not_empty(self):
        assert len(LEGENDARY_NAMES) > 0

    def test_names_are_strings(self):
        for name in LEGENDARY_NAMES:
            assert isinstance(name, str)
            assert len(name) > 0


class TestPoiIdAllocation:
    def test_alloc_legendary_lair_returns_string(self):
        game = MagicMock()
        game.poi_registry = _make_registry()

        pid = alloc_legendary_lair_poi_id(game)

        assert isinstance(pid, str)
        assert pid.startswith("legendary_lair_")

    def test_alloc_legendary_lair_avoids_collision(self):
        game = MagicMock()
        game.poi_registry = _make_registry()
        _add_registry_poi(game.poi_registry, "legendary_lair_000", kind="legendary_lair", coord=(0, 0, 0))
        _add_registry_poi(game.poi_registry, "legendary_lair_001", kind="legendary_lair", coord=(1, 0, 0))

        pid = alloc_legendary_lair_poi_id(game)

        assert pid == "legendary_lair_002"

    def test_alloc_legendary_lair_avoids_spatial_index_collision(self):
        game = SimpleNamespace(poi_registry=None, spatial_index=SpatialIndex())
        footprint = ABSRect.from_zone_coord(0, 0, 60, 40)
        poi = POISpec(
            id="legendary_lair_000",
            kind="legendary_lair",
            name="Lair",
            footprint=footprint,
            depth=0,
            anchor_abs=footprint.center,
            structure_specs=[StructureSpec(kind="legendary_lair")],
        )
        game.spatial_index.add_or_update(
            poi,
            (float(footprint.x0), float(footprint.y0), float(footprint.x1), float(footprint.y1)),
            0,
            "collapsed",
            kind="legendary_lair",
            source="poi_registry",
        )

        pid = alloc_legendary_lair_poi_id(game)

        assert pid == "legendary_lair_001"

    def test_alloc_rune_anchor_returns_string(self):
        game = MagicMock()
        game.poi_registry = _make_registry()

        pid = alloc_rune_anchor_poi_id(game)

        assert isinstance(pid, str)
        assert pid.startswith("rune_anchor_")


class TestInitLegendaries:
    @pytest.fixture
    def mock_game(self):
        game = MagicMock()
        game.cfg = SimpleNamespace(world_map_screens=5, world_width=60, world_height=40, seed=12345)
        game.zone_coord = (0, 0, 0)
        game.rng = random.Random(42)
        game.fractal_seed = 12345
        game.poi_registry = _make_registry()
        game.refresh_poi_locations = MagicMock()
        game._enemy_template_ids.return_value = ["goblin", "skeleton", "rat"]
        return game

    def test_returns_dict(self, mock_game):
        with patch("edgecaster.systems.legendaries.prototypes.resolve_proto") as mock_proto:
            mock_proto.return_value = {"name": "Goblin"}
            result = init_legendaries(mock_game, count=3)
        assert isinstance(result, dict)

    def test_creates_requested_count(self, mock_game):
        with patch("edgecaster.systems.legendaries.prototypes.resolve_proto") as mock_proto:
            mock_proto.return_value = {"name": "Goblin"}
            result = init_legendaries(mock_game, count=5)
        assert len(result) <= 5
        assert len(mock_game.poi_registry.get_by_kind("legendary_lair")) == len(result)

    def test_empty_templates_returns_empty(self, mock_game):
        mock_game._enemy_template_ids.return_value = []
        result = init_legendaries(mock_game, count=5)
        assert result == {}

    def test_legendary_has_required_fields(self, mock_game):
        with patch("edgecaster.systems.legendaries.prototypes.resolve_proto") as mock_proto:
            mock_proto.return_value = {"name": "Goblin"}
            result = init_legendaries(mock_game, count=1)
        if result:
            _, leg_info = next(iter(result.items()))
            assert "poi_id" in leg_info
            assert "coord" in leg_info
            assert "template_id" in leg_info
            assert "name" in leg_info
            assert "hp_mult" in leg_info


class TestAddPoiRumor:
    @pytest.fixture
    def mock_game(self):
        game = MagicMock()
        game.rumored_pois = set()
        game.discovered_pois = set()
        game.log = MagicMock()
        game.refresh_poi_locations = MagicMock()
        game.cfg = SimpleNamespace(world_width=60, world_height=40)
        game.poi_registry = _make_registry()
        return game

    def test_adds_to_rumored_set(self, mock_game):
        add_poi_rumor(mock_game, "test_poi", log=False)
        assert "test_poi" in mock_game.rumored_pois

    def test_skips_already_discovered(self, mock_game):
        mock_game.discovered_pois = {"test_poi"}
        add_poi_rumor(mock_game, "test_poi", log=False)
        assert "test_poi" not in mock_game.rumored_pois

    def test_logs_message_when_enabled(self, mock_game):
        _add_registry_poi(mock_game.poi_registry, "test_poi", kind="legendary_lair", coord=(1, 2, 0))
        add_poi_rumor(mock_game, "test_poi", log=True)
        mock_game.log.add.assert_called_once()

    def test_no_log_when_disabled(self, mock_game):
        add_poi_rumor(mock_game, "test_poi", log=False)
        mock_game.log.add.assert_not_called()

    def test_creates_sets_if_missing(self):
        game = MagicMock(spec=[])
        game.log = MagicMock()
        game.poi_registry = _make_registry()
        add_poi_rumor(game, "test_poi", log=False)
        assert hasattr(game, "rumored_pois")
        assert hasattr(game, "discovered_pois")


class TestDiscoverPoisForLevel:
    def test_marks_pois_as_discovered(self):
        game = MagicMock()
        game.discovered_pois = set()
        game.rumored_pois = {"poi_a", "poi_b"}

        level = MagicMock()
        level.world.poi_ids = ["poi_a"]

        discover_pois_for_level(game, level)

        assert "poi_a" in game.discovered_pois
        assert "poi_a" not in game.rumored_pois

    def test_handles_no_pois(self):
        game = MagicMock()
        game.discovered_pois = set()
        game.rumored_pois = set()

        level = MagicMock()
        level.world.poi_ids = None

        discover_pois_for_level(game, level)

    def test_creates_sets_if_missing(self):
        game = MagicMock(spec=[])
        level = MagicMock()
        level.world.poi_ids = None

        discover_pois_for_level(game, level)
        assert hasattr(game, "discovered_pois")
        assert hasattr(game, "rumored_pois")


class TestGetNearestLegendaryLairs:
    @pytest.fixture
    def mock_game_with_lairs(self):
        game = MagicMock()
        game.zone_coord = (5, 5, 0)
        game.cfg = SimpleNamespace(world_width=60, world_height=40)
        game.poi_registry = _make_registry()
        return game

    def test_returns_list(self, mock_game_with_lairs):
        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=5)
        assert isinstance(result, list)

    def test_returns_empty_for_no_lairs(self, mock_game_with_lairs):
        _add_registry_poi(mock_game_with_lairs.poi_registry, "some_poi", kind="city", coord=(0, 0, 0))
        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=5)
        assert result == []

    def test_filters_to_legendary_lairs_only(self, mock_game_with_lairs):
        _add_registry_poi(mock_game_with_lairs.poi_registry, "legendary_lair_000", kind="legendary_lair", coord=(1, 1, 0))
        _add_registry_poi(mock_game_with_lairs.poi_registry, "normal_poi", kind="city", coord=(0, 0, 0))
        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=5)
        for pid, _coord in result:
            assert pid.startswith("legendary_lair_")

    def test_sorted_by_distance(self, mock_game_with_lairs):
        _add_registry_poi(mock_game_with_lairs.poi_registry, "legendary_lair_000", kind="legendary_lair", coord=(10, 10, 0))
        _add_registry_poi(mock_game_with_lairs.poi_registry, "legendary_lair_001", kind="legendary_lair", coord=(5, 6, 0))
        _add_registry_poi(mock_game_with_lairs.poi_registry, "legendary_lair_002", kind="legendary_lair", coord=(0, 0, 0))
        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=3)
        assert result[0][1] == (5, 6, 0)

    def test_respects_n_limit(self, mock_game_with_lairs):
        for i in range(10):
            _add_registry_poi(
                mock_game_with_lairs.poi_registry,
                f"legendary_lair_{i:03d}",
                kind="legendary_lair",
                coord=(i, i, 0),
            )
        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=3)
        assert len(result) <= 3

    def test_handles_invalid_n(self, mock_game_with_lairs):
        assert get_nearest_legendary_lairs(mock_game_with_lairs, n=0) == []
        assert get_nearest_legendary_lairs(mock_game_with_lairs, n=-1) == []

    def test_reads_spatial_index_without_registry(self):
        game = SimpleNamespace(
            cfg=SimpleNamespace(world_width=60, world_height=40),
            zone_coord=(0, 0, 0),
            poi_registry=None,
            spatial_index=SpatialIndex(),
        )
        for poi_id, coord in (
            ("legendary_lair_000", (3, 0, 0)),
            ("legendary_lair_001", (1, 0, 0)),
            ("legendary_lair_002", (2, 0, 0)),
        ):
            footprint = ABSRect.from_zone_coord(coord[0], coord[1], 60, 40)
            poi = POISpec(
                id=poi_id,
                kind="legendary_lair",
                name=poi_id,
                footprint=footprint,
                depth=coord[2],
                anchor_abs=footprint.center,
                structure_specs=[StructureSpec(kind="legendary_lair")],
            )
            game.spatial_index.add_or_update(
                poi,
                (float(footprint.x0), float(footprint.y0), float(footprint.x1), float(footprint.y1)),
                coord[2],
                "collapsed",
                kind="legendary_lair",
                source="poi_registry",
            )

        result = get_nearest_legendary_lairs(game, n=2, from_coord=(0, 0, 0))

        assert result == [
            ("legendary_lair_001", (1, 0, 0)),
            ("legendary_lair_002", (2, 0, 0)),
        ]
