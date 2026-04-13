"""
Unit tests for the zones system (systems/zones.py).

Tests zone creation, retrieval, stair usage, and transitions.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


from edgecaster.systems.zones import (
    get_zone,
    get_zone_for_render,
    use_stairs_down,
    use_stairs_up,
    transition_edge,
    fast_travel_to_zone,
)


class TestGetZone:
    """Tests for get_zone function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.levels = {}
        game.allow_render_zone_creation = False

        player = MagicMock()
        player.stats = MagicMock()
        player.stats.coherence = 50
        player.stats.max_coherence = 100
        game._player.return_value = player

        level = MagicMock()
        level.pattern = MagicMock()
        level.pattern_anchor = (5, 5)
        level.activation_points = [(1, 2)]
        level.activation_ttl = 100
        level.pattern_motion = MagicMock()
        level.world = MagicMock()
        level.world.is_lab = False
        level.world.is_lair = False
        game._make_zone.return_value = level

        return game

    def test_creates_new_zone(self, mock_game):
        """Should create zone if not in cache."""
        coord = (1, 2, 0)

        with patch("edgecaster.patterns.builder.Pattern"):
            result = get_zone(mock_game, coord)

        mock_game._make_zone.assert_called_once_with(coord, up_pos=None)
        assert coord in mock_game.levels

    def test_returns_cached_zone(self, mock_game):
        """Should return cached zone without recreation."""
        coord = (1, 2, 0)
        cached_level = MagicMock()
        mock_game.levels[coord] = cached_level

        with patch("edgecaster.patterns.builder.Pattern"):
            result = get_zone(mock_game, coord)

        mock_game._make_zone.assert_not_called()
        assert result is cached_level

    def test_does_not_reset_pattern_state(self, mock_game):
        """get_zone is now pure retrieval and must not reset pattern state."""
        coord = (1, 2, 0)
        level = MagicMock()
        level.world = MagicMock()
        level.world.is_lab = False
        level.pattern_anchor = (5, 5)
        level.activation_points = [(1, 2)]
        level.activation_ttl = 100
        level.pattern_motion = MagicMock()
        mock_game.levels[coord] = level

        get_zone(mock_game, coord)

        assert level.pattern_anchor == (5, 5)
        assert level.activation_points == [(1, 2)]
        assert level.activation_ttl == 100
        assert level.pattern_motion is not None

    def test_does_not_reset_player_coherence(self, mock_game):
        """Chunk lookup must not perform coherence rituals."""
        coord = (1, 2, 0)
        level = MagicMock()
        level.world = MagicMock()
        level.world.is_lab = False
        mock_game.levels[coord] = level

        get_zone(mock_game, coord)

        assert mock_game._player().stats.coherence == 50

    def test_does_not_spawn_enemies_on_get_zone(self, mock_game):
        """Spawning is no longer a side effect of get_zone."""
        coord = (1, 2, 0)

        get_zone(mock_game, coord)

        mock_game._spawn_enemies.assert_not_called()

    def test_skips_enemy_spawn_in_special_zone(self, mock_game):
        """Should not spawn enemies in lab/lair zones."""
        coord = (1, 2, 0)
        level = MagicMock()
        level.world = MagicMock()
        level.world.is_lab = True
        level.world.is_lair = False
        mock_game._make_zone.return_value = level

        with patch("edgecaster.patterns.builder.Pattern"):
            get_zone(mock_game, coord)

        mock_game._spawn_enemies.assert_not_called()

    def test_does_not_discover_pois_on_get_zone(self, mock_game):
        """POI discovery now lives outside raw zone retrieval."""
        coord = (1, 2, 0)
        level = MagicMock()
        level.world = MagicMock()
        level.world.is_lab = False
        mock_game.levels[coord] = level

        get_zone(mock_game, coord)

        mock_game._discover_pois_for_level.assert_not_called()


class TestGetZoneForRender:
    """Tests for get_zone_for_render function."""

    def test_returns_none_without_opt_in_creation(self):
        """Render peeks should not instantiate zones by default."""
        game = MagicMock()
        game.allow_render_zone_creation = False
        game.levels = {}
        level = MagicMock()
        level.pattern_anchor = (5, 5)
        game._make_zone.return_value = level

        coord = (1, 2, 0)
        result = get_zone_for_render(game, coord)

        assert result is None
        game._make_zone.assert_not_called()

    def test_creates_zone_when_opt_in_enabled(self):
        """Explicit opt-in should still allow render-side creation."""
        game = MagicMock()
        game.allow_render_zone_creation = True
        game.levels = {}
        level = MagicMock()
        game._make_zone.return_value = level

        result = get_zone_for_render(game, (1, 2, 0))

        assert result is level
        game._make_zone.assert_called_once_with((1, 2, 0), up_pos=None)

    def test_returns_cached_zone(self):
        """Should return cached zone."""
        game = MagicMock()
        cached = MagicMock()
        game.levels = {(1, 2, 0): cached}

        result = get_zone_for_render(game, (1, 2, 0))

        assert result is cached
        game._make_zone.assert_not_called()


class TestUseStairsDown:
    """Tests for use_stairs_down function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with player on stairs."""
        game = MagicMock()
        game.player_id = "player"
        game.zone_coord = (0, 0, 0)
        game.log = MagicMock()

        player = MagicMock()
        player.pos = (5, 5)
        player.stats = MagicMock()
        player.stats.coherence = 50
        player.stats.max_coherence = 100

        level = MagicMock()
        level.actors = {"player": player}
        level.world = MagicMock()
        tile = MagicMock()
        tile.glyph = ">"
        level.world.get_tile.return_value = tile

        game._level.return_value = level
        game._player.return_value = player

        dest_level = MagicMock()
        dest_level.up_stairs = (3, 3)
        dest_level.actors = {}
        dest_level.world = MagicMock()
        dest_level.world.is_lab = False
        game.levels = {}

        return game, level, dest_level, player

    def test_descends_on_down_stairs(self, mock_game):
        """Should descend when on '>' tile."""
        game, level, dest_level, player = mock_game

        with patch("edgecaster.systems.zones.get_zone") as mock_get:
            mock_get.return_value = dest_level
            use_stairs_down(game)

        assert game.zone_coord == (0, 0, 1)
        assert player.pos == (3, 3)
        assert "player" in dest_level.actors
        game.log.add.assert_called()

    def test_ignores_non_stairs(self, mock_game):
        """Should do nothing on non-stair tiles."""
        game, level, dest_level, player = mock_game
        tile = MagicMock()
        tile.glyph = "."
        level.world.get_tile.return_value = tile

        use_stairs_down(game)

        assert game.zone_coord == (0, 0, 0)


class TestUseStairsUp:
    """Tests for use_stairs_up function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
        game.zone_coord = (0, 0, 1)  # Depth 1
        game.log = MagicMock()

        player = MagicMock()
        player.pos = (5, 5)
        player.stats = MagicMock()
        player.stats.coherence = 50
        player.stats.max_coherence = 100

        level = MagicMock()
        level.actors = {"player": player}
        level.world = MagicMock()
        tile = MagicMock()
        tile.glyph = "<"
        level.world.get_tile.return_value = tile

        game._level.return_value = level
        game._player.return_value = player

        dest_level = MagicMock()
        dest_level.down_stairs = (7, 7)
        dest_level.actors = {}
        dest_level.world = MagicMock()
        dest_level.world.is_lab = False
        game.levels = {}

        return game, level, dest_level, player

    def test_ascends_on_up_stairs(self, mock_game):
        """Should ascend when on '<' tile."""
        game, level, dest_level, player = mock_game

        with patch("edgecaster.systems.zones.get_zone") as mock_get:
            mock_get.return_value = dest_level
            use_stairs_up(game)

        assert game.zone_coord == (0, 0, 0)
        assert player.pos == (7, 7)
        assert "player" in dest_level.actors

    def test_requests_map_at_surface(self, mock_game):
        """Should request world map at surface without up stairs."""
        game, level, dest_level, player = mock_game
        game.zone_coord = (0, 0, 0)  # Surface
        tile = MagicMock()
        tile.glyph = "."  # Not on stairs
        level.world.get_tile.return_value = tile

        use_stairs_up(game)

        assert game.map_requested is True


class TestTransitionEdge:
    """Tests for transition_edge function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
        game.zone_coord = (5, 5, 0)
        game.log = MagicMock()
        game.rng = MagicMock()
        game.rng.random.return_value = 0.6  # No random event

        actor = MagicMock()
        actor.pos = (39, 10)  # Right edge of 40-wide zone
        actor.stats = MagicMock()
        actor.stats.coherence = 50
        actor.stats.max_coherence = 100

        level = MagicMock()
        level.actors = {"player": actor}
        level.world = MagicMock()
        level.world.width = 40
        level.world.height = 40

        game._level.return_value = level
        game._player.return_value = actor

        dest_level = MagicMock()
        dest_level.actors = {}
        dest_level.world = MagicMock()
        dest_level.world.is_lab = False
        game.levels = {}

        return game, level, dest_level, actor

    def test_transitions_east(self, mock_game):
        """Should transition to east zone."""
        game, level, dest_level, actor = mock_game

        with patch("edgecaster.events.pick_random_event") as mock_event:
            mock_event.return_value = None
            with patch("edgecaster.patterns.builder.Pattern"):
                transition_edge(game, actor, 1, 0)

        assert game.zone_coord == (6, 5, 0)
        assert actor.pos == (0, 10)  # Wrapped to west edge

    def test_no_transition_within_bounds(self, mock_game):
        """Should not transition if still in bounds."""
        game, level, dest_level, actor = mock_game
        actor.pos = (20, 20)  # Center of zone

        with patch("edgecaster.events.pick_random_event"):
            with patch("edgecaster.patterns.builder.Pattern"):
                transition_edge(game, actor, 1, 0)

        # Should not have changed zone
        assert game.zone_coord == (5, 5, 0)


class TestFastTravelToZone:
    """Tests for fast_travel_to_zone function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
        game.zone_coord = (0, 0, 0)
        game.log = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_map_screens = 10

        actor = MagicMock()
        actor.pos = (5, 5)
        actor.stats = MagicMock()
        actor.stats.coherence = 50
        actor.stats.max_coherence = 100

        level = MagicMock()
        level.actors = {"player": actor}

        game._level.return_value = level
        game._player.return_value = actor

        dest_level = MagicMock()
        dest_level.actors = {}
        dest_level.world = MagicMock()
        dest_level.world.entry = (20, 20)
        dest_level.world.is_lab = False
        game.levels = {}

        return game, level, dest_level, actor

    def test_travels_to_zone(self, mock_game):
        """Should travel to specified zone."""
        game, level, dest_level, actor = mock_game

        with patch("edgecaster.systems.zones.get_zone") as mock_get:
            mock_get.return_value = dest_level
            fast_travel_to_zone(game, 5, 5)

        assert game.zone_coord == (5, 5, 0)
        assert actor.pos == (20, 20)
        game.log.add.assert_called()

    def test_clamps_to_bounds(self, mock_game):
        """Should clamp coordinates to world bounds."""
        game, level, dest_level, actor = mock_game

        with patch("edgecaster.systems.zones.get_zone") as mock_get:
            mock_get.return_value = dest_level
            fast_travel_to_zone(game, 100, -5)  # Out of bounds

        # Should be clamped to (9, 0) for a 10-screen world
        assert game.zone_coord == (9, 0, 0)
