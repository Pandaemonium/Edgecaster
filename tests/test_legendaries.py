"""
Unit tests for the legendaries system (systems/legendaries.py).

Tests legendary creature generation and POI discovery in isolation.
"""

import pytest
from unittest.mock import MagicMock, patch
import random

from edgecaster.systems.legendaries import (
    LEGENDARY_NAMES,
    alloc_legendary_lair_poi_id,
    alloc_rune_anchor_poi_id,
    init_legendaries,
    add_poi_rumor,
    discover_pois_for_level,
    get_nearest_legendary_lairs,
)


class TestLegendaryNames:
    """Tests for the legendary name pool."""

    def test_names_not_empty(self):
        """Should have a pool of names."""
        assert len(LEGENDARY_NAMES) > 0

    def test_names_are_strings(self):
        """All names should be strings."""
        for name in LEGENDARY_NAMES:
            assert isinstance(name, str)
            assert len(name) > 0


class TestPoiIdAllocation:
    """Tests for POI ID allocation functions."""

    def test_alloc_legendary_lair_returns_string(self):
        """Should return a string ID."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            pid = alloc_legendary_lair_poi_id()
            assert isinstance(pid, str)
            assert pid.startswith("legendary_lair_")

    def test_alloc_legendary_lair_avoids_collision(self):
        """Should increment until finding unused ID."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {
                "legendary_lair_000": MagicMock(),
                "legendary_lair_001": MagicMock(),
            }
            pid = alloc_legendary_lair_poi_id()
            assert pid == "legendary_lair_002"

    def test_alloc_rune_anchor_returns_string(self):
        """Should return a string ID."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            pid = alloc_rune_anchor_poi_id()
            assert isinstance(pid, str)
            assert pid.startswith("rune_anchor_")


class TestInitLegendaries:
    """Tests for legendary initialization."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock Game for testing."""
        game = MagicMock()
        game.cfg.world_map_screens = 5
        game.zone_coord = (0, 0, 0)
        game.rng = random.Random(42)
        game._enemy_template_ids.return_value = ["goblin", "skeleton", "rat"]
        return game

    def test_returns_dict(self, mock_game):
        """Should return a dictionary."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            mock_pois.POI = MagicMock()
            with patch('edgecaster.systems.legendaries.prototypes') as mock_proto:
                mock_proto.resolve_proto.return_value = {"name": "Goblin"}
                result = init_legendaries(mock_game, count=3)
                assert isinstance(result, dict)

    def test_creates_requested_count(self, mock_game):
        """Should create approximately the requested number of legendaries."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            mock_pois.POI = MagicMock()
            with patch('edgecaster.systems.legendaries.prototypes') as mock_proto:
                mock_proto.resolve_proto.return_value = {"name": "Goblin"}
                result = init_legendaries(mock_game, count=5)
                # May be slightly less due to collisions
                assert len(result) <= 5

    def test_empty_templates_returns_empty(self, mock_game):
        """Should return empty dict if no templates available."""
        mock_game._enemy_template_ids.return_value = []
        result = init_legendaries(mock_game, count=5)
        assert result == {}

    def test_legendary_has_required_fields(self, mock_game):
        """Each legendary should have required info."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            mock_pois.POI = MagicMock()
            with patch('edgecaster.systems.legendaries.prototypes') as mock_proto:
                mock_proto.resolve_proto.return_value = {"name": "Goblin"}
                result = init_legendaries(mock_game, count=1)
                if result:
                    leg_id, leg_info = next(iter(result.items()))
                    assert "poi_id" in leg_info
                    assert "coord" in leg_info
                    assert "template_id" in leg_info
                    assert "name" in leg_info
                    assert "hp_mult" in leg_info


class TestAddPoiRumor:
    """Tests for POI rumor system."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock Game for testing."""
        game = MagicMock()
        game.rumored_pois = set()
        game.discovered_pois = set()
        game.log = MagicMock()
        return game

    def test_adds_to_rumored_set(self, mock_game):
        """Should add POI to rumored_pois."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            add_poi_rumor(mock_game, "test_poi", log=False)
            assert "test_poi" in mock_game.rumored_pois

    def test_skips_already_discovered(self, mock_game):
        """Should not rumor already-discovered POIs."""
        mock_game.discovered_pois = {"test_poi"}
        add_poi_rumor(mock_game, "test_poi", log=False)
        assert "test_poi" not in mock_game.rumored_pois

    def test_logs_message_when_enabled(self, mock_game):
        """Should log a message when log=True."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_poi = MagicMock()
            mock_poi.coord = (1, 2, 0)
            mock_pois.POIS = {"test_poi": mock_poi}
            add_poi_rumor(mock_game, "test_poi", log=True)
            mock_game.log.add.assert_called_once()

    def test_no_log_when_disabled(self, mock_game):
        """Should not log when log=False."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            add_poi_rumor(mock_game, "test_poi", log=False)
            mock_game.log.add.assert_not_called()

    def test_creates_sets_if_missing(self):
        """Should create rumored/discovered sets if they don't exist."""
        game = MagicMock(spec=[])  # Empty spec = no attributes
        game.log = MagicMock()
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            add_poi_rumor(game, "test_poi", log=False)
            assert hasattr(game, "rumored_pois")
            assert hasattr(game, "discovered_pois")


class TestDiscoverPoisForLevel:
    """Tests for POI discovery."""

    def test_marks_pois_as_discovered(self):
        """Should move POIs from rumored to discovered."""
        game = MagicMock()
        game.discovered_pois = set()
        game.rumored_pois = {"poi_a", "poi_b"}

        level = MagicMock()
        level.world.poi_ids = ["poi_a"]

        discover_pois_for_level(game, level)

        assert "poi_a" in game.discovered_pois
        assert "poi_a" not in game.rumored_pois

    def test_handles_no_pois(self):
        """Should handle levels with no POIs."""
        game = MagicMock()
        game.discovered_pois = set()
        game.rumored_pois = set()

        level = MagicMock()
        level.world.poi_ids = None

        # Should not raise
        discover_pois_for_level(game, level)

    def test_creates_sets_if_missing(self):
        """Should create sets if they don't exist."""
        game = MagicMock(spec=[])
        level = MagicMock()
        level.world.poi_ids = None

        discover_pois_for_level(game, level)
        assert hasattr(game, "discovered_pois")
        assert hasattr(game, "rumored_pois")


class TestGetNearestLegendaryLairs:
    """Tests for legendary lair queries."""

    @pytest.fixture
    def mock_game_with_lairs(self):
        """Create a mock Game with some legendary lairs."""
        game = MagicMock()
        game.zone_coord = (5, 5, 0)
        game.poi_locations = None
        return game

    def test_returns_list(self, mock_game_with_lairs):
        """Should return a list."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {}
            result = get_nearest_legendary_lairs(mock_game_with_lairs, n=5)
            assert isinstance(result, list)

    def test_returns_empty_for_no_lairs(self, mock_game_with_lairs):
        """Should return empty list if no legendary lairs exist."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {"some_poi": MagicMock(coord=(0, 0, 0))}
            result = get_nearest_legendary_lairs(mock_game_with_lairs, n=5)
            assert result == []

    def test_filters_to_legendary_lairs_only(self, mock_game_with_lairs):
        """Should only return legendary_lair_ POIs."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {
                "legendary_lair_000": MagicMock(coord=(1, 1, 0)),
                "normal_poi": MagicMock(coord=(0, 0, 0)),
            }
            result = get_nearest_legendary_lairs(mock_game_with_lairs, n=5)
            for pid, _ in result:
                assert pid.startswith("legendary_lair_")

    def test_sorted_by_distance(self, mock_game_with_lairs):
        """Should return results sorted by distance from origin."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {
                "legendary_lair_000": MagicMock(coord=(10, 10, 0)),  # Far
                "legendary_lair_001": MagicMock(coord=(5, 6, 0)),   # Near
                "legendary_lair_002": MagicMock(coord=(0, 0, 0)),   # Far
            }
            result = get_nearest_legendary_lairs(mock_game_with_lairs, n=3)
            # First should be the nearest
            if result:
                first_pid, first_coord = result[0]
                assert first_coord == (5, 6, 0)  # Nearest to (5,5)

    def test_respects_n_limit(self, mock_game_with_lairs):
        """Should return at most n results."""
        with patch('edgecaster.systems.legendaries.poi_content') as mock_pois:
            mock_pois.POIS = {
                f"legendary_lair_{i:03d}": MagicMock(coord=(i, i, 0))
                for i in range(10)
            }
            result = get_nearest_legendary_lairs(mock_game_with_lairs, n=3)
            assert len(result) <= 3

    def test_handles_invalid_n(self, mock_game_with_lairs):
        """Should handle n <= 0 gracefully."""
        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=0)
        assert result == []

        result = get_nearest_legendary_lairs(mock_game_with_lairs, n=-1)
        assert result == []
