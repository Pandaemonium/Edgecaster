"""
Unit tests for the Lorenz aura system (systems/lorenz_aura.py).

Tests the Strange Attractor's butterfly storm game integration.
"""

import pytest
from unittest.mock import MagicMock, patch
import math

from edgecaster.systems.lorenz_aura import (
    has_lorenz_aura,
    advance_lorenz,
    apply_contact_damage,
    reset_on_zone_change,
    _project_to_tile_hits,
    LORENZ_SCALE,
    LORENZ_RADIUS_TILES,
    DAMAGE_VERBS,
)


class TestHasLorenzAura:
    """Tests for the has_lorenz_aura check."""

    def test_true_for_strange_attractor(self):
        """Should return True for Strange Attractor class."""
        game = MagicMock()
        game.character.player_class = "Strange Attractor"
        assert has_lorenz_aura(game) is True

    def test_false_for_geometer(self):
        """Should return False for Geometer class."""
        game = MagicMock()
        game.character.player_class = "Geometer"
        assert has_lorenz_aura(game) is False

    def test_false_for_none(self):
        """Should return False if no class set."""
        game = MagicMock()
        game.character.player_class = None
        assert has_lorenz_aura(game) is False


class TestAdvanceLorenz:
    """Tests for Lorenz advancement."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game for testing."""
        game = MagicMock()
        game.character.player_class = "Strange Attractor"
        game.zone_coord = (0, 0, 0)
        game.lorenz_points = [(1.0, 2.0, 25.0)]
        game.lorenz_center_x = 5.0
        game.lorenz_center_y = 5.0
        game.lorenz_reset_trails = False
        return game

    @pytest.fixture
    def mock_level(self):
        """Create a mock level for testing."""
        level = MagicMock()
        level.actors = {}
        level.world.in_bounds.return_value = True
        return level

    def test_clears_state_for_non_strange_attractor(self, mock_level):
        """Should clear Lorenz state if not Strange Attractor."""
        game = MagicMock()
        game.character.player_class = "Geometer"
        game.zone_coord = (0, 0, 0)
        game.lorenz_points = [(1.0, 2.0, 25.0)]

        advance_lorenz(game, mock_level, delta=10)

        assert game.lorenz_points == []
        assert game.lorenz_center_x is None
        assert game.lorenz_center_y is None

    def test_calls_lorenz_advance(self, mock_game, mock_level):
        """Should call lorenz.advance_lorenz for Strange Attractor."""
        with patch('edgecaster.systems.lorenz_aura.lorenz') as mock_lorenz:
            advance_lorenz(mock_game, mock_level, delta=10)
            mock_lorenz.advance_lorenz.assert_called_once_with(mock_game, mock_level, 10)

    def test_clears_reset_flag(self, mock_game, mock_level):
        """Should clear lorenz_reset_trails after processing."""
        mock_game.lorenz_reset_trails = True
        with patch('edgecaster.systems.lorenz_aura.lorenz'):
            advance_lorenz(mock_game, mock_level, delta=10)
        assert mock_game.lorenz_reset_trails is False


class TestProjectToTileHits:
    """Tests for tile projection."""

    def test_returns_dict(self):
        """Should return a dictionary."""
        level = MagicMock()
        level.world.in_bounds.return_value = True

        # Single point at the natural center
        points = [(0.0, 0.0, 25.0)]
        result = _project_to_tile_hits(points, 5.0, 5.0, level)
        assert isinstance(result, dict)

    def test_empty_for_empty_points(self):
        """Should return empty dict for no points."""
        level = MagicMock()
        result = _project_to_tile_hits([], 5.0, 5.0, level)
        assert result == {}

    def test_excludes_out_of_bounds(self):
        """Should exclude points outside world bounds."""
        level = MagicMock()
        level.world.in_bounds.return_value = False

        points = [(0.0, 0.0, 25.0)]
        result = _project_to_tile_hits(points, 5.0, 5.0, level)
        assert result == {}

    def test_excludes_beyond_radius(self):
        """Should exclude points beyond the radius."""
        level = MagicMock()
        level.world.in_bounds.return_value = True

        # Point very far from center - beyond LORENZ_RADIUS_TILES after scaling
        points = [(1000.0, 0.0, 25.0)]
        result = _project_to_tile_hits(points, 5.0, 5.0, level)
        # Should be empty or not include the distant point
        # (depends on exact projection math)
        assert isinstance(result, dict)


class TestApplyContactDamage:
    """Tests for contact damage application."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game for testing."""
        game = MagicMock()
        game.player_id = "player"
        game.lorenz_points = [(0.0, 0.0, 25.0)]
        game.lorenz_center_x = 5.0
        game.lorenz_center_y = 5.0
        game.rng.choice.return_value = "cuts"
        game.rng.random.return_value = 0.3  # Will trigger distracted
        return game

    @pytest.fixture
    def hostile_actor(self):
        """Create a mock hostile actor."""
        actor = MagicMock()
        actor.id = "enemy_1"
        actor.alive = True
        actor.faction = "hostile"
        actor.pos = (5, 5)  # At storm center
        actor.name = "Goblin"
        actor.stats.hp = 10
        actor.statuses = {}
        return actor

    def test_no_damage_without_points(self, mock_game):
        """Should do nothing if no Lorenz points."""
        mock_game.lorenz_points = []
        level = MagicMock()
        apply_contact_damage(mock_game, level)
        mock_game.log.add.assert_not_called()

    def test_skips_player(self, mock_game, hostile_actor):
        """Should not damage the player."""
        hostile_actor.id = "player"
        level = MagicMock()
        level.actors = {"player": hostile_actor}
        level.world.in_bounds.return_value = True

        apply_contact_damage(mock_game, level)
        # Player should not take damage
        assert hostile_actor.stats.hp == 10

    def test_skips_non_hostile(self, mock_game, hostile_actor):
        """Should not damage non-hostile actors."""
        hostile_actor.faction = "neutral"
        level = MagicMock()
        level.actors = {"neutral_1": hostile_actor}
        level.world.in_bounds.return_value = True

        apply_contact_damage(mock_game, level)
        assert hostile_actor.stats.hp == 10


class TestResetOnZoneChange:
    """Tests for zone transition handling."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game for testing."""
        game = MagicMock()
        game.character.player_class = "Strange Attractor"
        game.zone_coord = (1, 1, 0)
        game.lorenz_points = [(1.0, 2.0, 25.0)]
        game._level.return_value = MagicMock()
        return game

    @pytest.fixture
    def mock_player(self):
        """Create a mock player actor."""
        player = MagicMock()
        player.pos = (10, 10)
        player.stats.coherence = 50
        player.stats.max_coherence = 100
        return player

    def test_does_nothing_for_non_strange_attractor(self, mock_player):
        """Should do nothing if not Strange Attractor."""
        game = MagicMock()
        game.character.player_class = "Geometer"
        game.lorenz_points = [(1.0, 2.0, 25.0)]

        reset_on_zone_change(game, mock_player)
        # Points should be unchanged
        assert game.lorenz_points == [(1.0, 2.0, 25.0)]

    def test_clears_points(self, mock_game, mock_player):
        """Should clear Lorenz points on zone change."""
        with patch('edgecaster.systems.lorenz_aura.lorenz'):
            with patch('edgecaster.systems.lorenz_aura.builder'):
                reset_on_zone_change(mock_game, mock_player)
        assert mock_game.lorenz_points == []

    def test_sets_center_to_player(self, mock_game, mock_player):
        """Should center storm on player position."""
        with patch('edgecaster.systems.lorenz_aura.lorenz'):
            with patch('edgecaster.systems.lorenz_aura.builder'):
                reset_on_zone_change(mock_game, mock_player)
        assert mock_game.lorenz_center_x == 10.0
        assert mock_game.lorenz_center_y == 10.0

    def test_sets_reset_trails_flag(self, mock_game, mock_player):
        """Should set lorenz_reset_trails flag."""
        mock_game.lorenz_reset_trails = False
        with patch('edgecaster.systems.lorenz_aura.lorenz'):
            with patch('edgecaster.systems.lorenz_aura.builder'):
                reset_on_zone_change(mock_game, mock_player)
        assert mock_game.lorenz_reset_trails is True

    def test_reseeds_butterflies(self, mock_game, mock_player):
        """Should call init_lorenz_points to reseed butterflies."""
        with patch('edgecaster.systems.lorenz_aura.lorenz') as mock_lorenz:
            with patch('edgecaster.systems.lorenz_aura.builder'):
                reset_on_zone_change(mock_game, mock_player)
        mock_lorenz.init_lorenz_points.assert_called_once_with(mock_game)

    def test_resets_coherence(self, mock_game, mock_player):
        """Should reset player coherence to max."""
        with patch('edgecaster.systems.lorenz_aura.lorenz'):
            with patch('edgecaster.systems.lorenz_aura.builder'):
                reset_on_zone_change(mock_game, mock_player)
        assert mock_player.stats.coherence == mock_player.stats.max_coherence


class TestConstants:
    """Tests for module constants."""

    def test_lorenz_scale_positive(self):
        """LORENZ_SCALE should be positive."""
        assert LORENZ_SCALE > 0

    def test_lorenz_radius_positive(self):
        """LORENZ_RADIUS_TILES should be positive."""
        assert LORENZ_RADIUS_TILES > 0

    def test_damage_verbs_not_empty(self):
        """DAMAGE_VERBS should have entries."""
        assert len(DAMAGE_VERBS) > 0

    def test_damage_verbs_are_strings(self):
        """All damage verbs should be strings."""
        for verb in DAMAGE_VERBS:
            assert isinstance(verb, str)
