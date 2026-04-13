"""
Unit tests for the pattern operations system (systems/pattern_ops.py).

Tests pattern projection, vertex queries, and placement.
"""

import pytest
from unittest.mock import MagicMock, patch

from edgecaster.systems.pattern_ops import (
    projected_vertices,
    nearest_vertex,
    neighbors_of,
    neighbor_set_depth,
    begin_place_mode,
    try_place_terminus,
    reset_pattern,
    push_pattern,
    activation_origin,
    _build_adjacency,
)


class TestProjectedVertices:
    """Tests for projected_vertices."""

    def test_empty_without_anchor(self):
        """Should return empty list if no pattern anchor."""
        game = MagicMock()
        level = MagicMock()
        level.pattern_anchor = None
        game._level.return_value = level

        result = projected_vertices(game)

        assert result == []

    def test_calls_project_vertices(self):
        """Should call project_vertices with pattern and anchor."""
        game = MagicMock()
        level = MagicMock()
        level.pattern_anchor = (5, 5)
        level.pattern = MagicMock()
        game._level.return_value = level

        with patch('edgecaster.systems.pattern_ops.project_vertices') as mock_proj:
            mock_proj.return_value = [(5.0, 5.0), (6.0, 6.0)]
            result = projected_vertices(game)

        mock_proj.assert_called_once_with(level.pattern, (5, 5))
        assert result == [(5.0, 5.0), (6.0, 6.0)]


class TestNearestVertex:
    """Tests for nearest_vertex."""

    def test_returns_none_for_empty(self):
        """Should return None if no vertices."""
        game = MagicMock()
        level = MagicMock()
        level.pattern_anchor = None
        game._level.return_value = level

        result = nearest_vertex(game, (5.0, 5.0))

        assert result is None

    def test_finds_nearest(self):
        """Should find the nearest vertex by distance."""
        game = MagicMock()
        level = MagicMock()
        level.pattern_anchor = (0, 0)
        game._level.return_value = level

        with patch('edgecaster.systems.pattern_ops.project_vertices') as mock_proj:
            mock_proj.return_value = [(0.0, 0.0), (10.0, 0.0), (5.0, 0.0)]
            result = nearest_vertex(game, (4.0, 0.0))

        # Vertex at (5.0, 0.0) is index 2, closest to (4.0, 0.0)
        assert result == 2

    def test_handles_tie(self):
        """Should handle equidistant vertices (first wins)."""
        game = MagicMock()
        level = MagicMock()
        level.pattern_anchor = (0, 0)
        game._level.return_value = level

        with patch('edgecaster.systems.pattern_ops.project_vertices') as mock_proj:
            mock_proj.return_value = [(0.0, 1.0), (0.0, -1.0)]
            result = nearest_vertex(game, (0.0, 0.0))

        # Both equidistant, first one (index 0) wins
        assert result == 0


class TestBuildAdjacency:
    """Tests for _build_adjacency helper."""

    def test_empty_pattern(self):
        """Should return empty dict for pattern with no edges."""
        pattern = MagicMock()
        pattern.edges = []

        result = _build_adjacency(pattern)

        assert result == {}

    def test_single_edge(self):
        """Should build bidirectional adjacency for single edge."""
        pattern = MagicMock()
        edge = MagicMock()
        edge.a = 0
        edge.b = 1
        pattern.edges = [edge]

        result = _build_adjacency(pattern)

        assert 0 in result
        assert 1 in result
        assert 1 in result[0]
        assert 0 in result[1]

    def test_multiple_edges(self):
        """Should handle multiple edges forming a graph."""
        pattern = MagicMock()
        e1 = MagicMock()
        e1.a, e1.b = 0, 1
        e2 = MagicMock()
        e2.a, e2.b = 1, 2
        e3 = MagicMock()
        e3.a, e3.b = 0, 2
        pattern.edges = [e1, e2, e3]

        result = _build_adjacency(pattern)

        # Triangle graph: each vertex connected to two others
        assert set(result[0]) == {1, 2}
        assert set(result[1]) == {0, 2}
        assert set(result[2]) == {1, 0}


class TestNeighborsOf:
    """Tests for neighbors_of."""

    def test_returns_adjacent_vertices(self):
        """Should return vertices connected by edges."""
        game = MagicMock()
        level = MagicMock()
        edge = MagicMock()
        edge.a, edge.b = 0, 1
        level.pattern.edges = [edge]
        game._level.return_value = level

        result = neighbors_of(game, 0)

        assert 1 in result

    def test_returns_empty_for_isolated(self):
        """Should return empty list for vertex with no edges."""
        game = MagicMock()
        level = MagicMock()
        level.pattern.edges = []
        game._level.return_value = level

        result = neighbors_of(game, 0)

        assert result == []


class TestNeighborSetDepth:
    """Tests for neighbor_set_depth."""

    def test_depth_zero(self):
        """Should return only seed for depth 0."""
        game = MagicMock()
        level = MagicMock()
        level.pattern.edges = []
        game._level.return_value = level

        result = neighbor_set_depth(game, 5, 0)

        assert result == [5]

    def test_depth_one_expands(self):
        """Should include immediate neighbors at depth 1."""
        game = MagicMock()
        level = MagicMock()
        # Linear graph: 0 - 1 - 2 - 3
        e1 = MagicMock()
        e1.a, e1.b = 0, 1
        e2 = MagicMock()
        e2.a, e2.b = 1, 2
        e3 = MagicMock()
        e3.a, e3.b = 2, 3
        level.pattern.edges = [e1, e2, e3]
        game._level.return_value = level

        result = neighbor_set_depth(game, 1, 1)

        # From vertex 1, depth 1 reaches 0 and 2
        assert set(result) == {0, 1, 2}

    def test_depth_two_expands_further(self):
        """Should expand to 2 hops at depth 2."""
        game = MagicMock()
        level = MagicMock()
        # Linear graph: 0 - 1 - 2 - 3
        e1 = MagicMock()
        e1.a, e1.b = 0, 1
        e2 = MagicMock()
        e2.a, e2.b = 1, 2
        e3 = MagicMock()
        e3.a, e3.b = 2, 3
        level.pattern.edges = [e1, e2, e3]
        game._level.return_value = level

        result = neighbor_set_depth(game, 1, 2)

        # From vertex 1, depth 2 reaches 0, 1, 2, 3
        assert set(result) == {0, 1, 2, 3}


class TestBeginPlaceMode:
    """Tests for begin_place_mode."""

    def test_sets_awaiting_terminus(self):
        """Should set awaiting_terminus flag."""
        game = MagicMock()
        game.place_range = 10
        level = MagicMock()
        level.awaiting_terminus = False
        game._level.return_value = level

        begin_place_mode(game)

        assert level.awaiting_terminus is True
        game.log.add.assert_called()


class TestTryPlaceTerminus:
    """Tests for try_place_terminus."""

    def test_ignores_if_not_awaiting(self):
        """Should do nothing if not awaiting terminus."""
        game = MagicMock()
        level = MagicMock()
        level.awaiting_terminus = False
        game._level.return_value = level

        try_place_terminus(game, (5, 5))

        game._schedule.assert_not_called()

    def test_rejects_out_of_range(self):
        """Should reject targets outside place_range."""
        game = MagicMock()
        game.place_range = 3
        game.zone_coord = (0, 0, 0)
        game.abs_from_zone_local.side_effect = lambda coord, pos: (int(pos[0]), int(pos[1]))
        level = MagicMock()
        level.awaiting_terminus = True
        game._level.return_value = level

        player = MagicMock()
        player.pos = (0, 0)
        game._player.return_value = player

        try_place_terminus(game, (10, 10))  # Way beyond range 3

        game.log.add.assert_called_with("Out of range.")
        assert level.awaiting_terminus is True

    def test_schedules_placement(self):
        """Should schedule pattern placement for valid target."""
        game = MagicMock()
        game.place_range = 10
        game.cfg.place_time_ticks = 5
        game.zone_coord = (0, 0, 0)
        game.abs_from_zone_local.side_effect = lambda coord, pos: (int(pos[0]), int(pos[1]))
        level = MagicMock()
        level.awaiting_terminus = True
        game._level.return_value = level

        player = MagicMock()
        player.pos = (5, 5)
        game._player.return_value = player

        try_place_terminus(game, (8, 5))  # Within range

        game._schedule.assert_called_once()
        game._advance_time.assert_called_once_with(level, 5)
        assert level.awaiting_terminus is False


class TestResetPattern:
    """Tests for reset_pattern."""

    def test_clears_pattern_state(self):
        """Should clear pattern, anchor, and activation state."""
        game = MagicMock()
        game.zone_coord = (0, 0, 0)
        level = MagicMock()
        level.pattern_anchor = (5, 5)
        level.activation_points = [(1, 2)]
        level.activation_ttl = 50
        game._level.return_value = level
        state = {}
        game._pattern_state.return_value = state

        def _sync_level_pattern_view(lvl):
            lvl.pattern = state.get("pattern")
            lvl.pattern_anchor = state.get("anchor_abs")
            lvl.activation_points = list(state.get("activation_points", []))
            lvl.activation_ttl = state.get("activation_ttl", 0)

        game._sync_level_pattern_view.side_effect = _sync_level_pattern_view

        player = MagicMock()
        player.stats.coherence = 50
        player.stats.max_coherence = 100
        game._player.return_value = player

        with patch('edgecaster.systems.pattern_ops.builder') as mock_builder:
            mock_builder.Pattern.return_value = MagicMock()
            reset_pattern(game)

        assert state["anchor_abs"] is None
        assert level.pattern_anchor is None
        assert level.activation_points == []
        assert level.activation_ttl == 0
        assert player.stats.coherence == 100
        game.log.add.assert_called_with("Rune reset.")


class TestPushPattern:
    """Tests for push_pattern."""

    def test_does_nothing_without_pattern(self):
        """Should return early if no pattern."""
        game = MagicMock()
        level = MagicMock()
        level.pattern = None

        push_pattern(game, level)

        # Should not call pattern_motion

    def test_starts_motion(self):
        """Should start pattern motion towards target."""
        game = MagicMock()
        level = MagicMock()
        level.pattern_anchor = (5, 5)

        pattern = MagicMock()
        pattern.vertices = [MagicMock()]
        level.pattern = pattern

        with patch('edgecaster.systems.pattern_ops.pattern_motion') as mock_motion:
            mock_motion.center_of_mass.return_value = (0.0, 0.0)
            push_pattern(game, level, target_pos=(10.0, 5.0))

        mock_motion.start_motion.assert_called_once()


class TestActivationOrigin:
    """Tests for activation_origin."""

    def test_returns_anchor(self):
        """Should return pattern anchor."""
        level = MagicMock()
        level.pattern_anchor = (10, 20)

        result = activation_origin(level)

        assert result == (10, 20)

    def test_returns_none_without_anchor(self):
        """Should return None if no anchor."""
        level = MagicMock()
        level.pattern_anchor = None

        result = activation_origin(level)

        assert result is None
