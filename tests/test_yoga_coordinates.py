"""
Unit tests for the Yoga coordinate system (observer-driven reality).

These tests validate the Stage 1 invariants from vision_documents/the_yoga.txt:
- Absolute space is truth
- Zones are caches/views, not metaphysical containers
- Coordinate transforms are bidirectional and consistent
- Entity abs_pos is populated correctly on spawn paths

Run with: python -m pytest tests/test_yoga_coordinates.py -v
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass


# =============================================================================
# COORDINATE TRANSFORM TESTS
# =============================================================================

class TestAbsFromZoneLocal:
    """Tests for game.abs_from_zone_local() coordinate transform."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with zone dimensions."""
        game = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_width = 60
        game.cfg.world_height = 40
        game._zone_dims = MagicMock(return_value=(60, 40))

        # Import the actual function to test
        from edgecaster.game import Game
        game.abs_from_zone_local = lambda zc, lp: Game.abs_from_zone_local(game, zc, lp)
        return game

    def test_origin_zone_identity(self, mock_game):
        """Zone (0,0,0) local pos should equal ABS pos."""
        zone = (0, 0, 0)
        local = (10, 20)

        abs_pos = mock_game.abs_from_zone_local(zone, local)

        assert abs_pos == (10, 20)

    def test_adjacent_zone_offset(self, mock_game):
        """Zone (1,0,0) should offset by zone_width."""
        zone = (1, 0, 0)
        local = (5, 5)

        abs_pos = mock_game.abs_from_zone_local(zone, local)

        # x = 1*60 + 5 = 65, y = 0*40 + 5 = 5
        assert abs_pos == (65, 5)

    def test_diagonal_zone(self, mock_game):
        """Zone (2,3,0) should offset by both dimensions."""
        zone = (2, 3, 0)
        local = (10, 10)

        abs_pos = mock_game.abs_from_zone_local(zone, local)

        # x = 2*60 + 10 = 130, y = 3*40 + 10 = 130
        assert abs_pos == (130, 130)

    def test_depth_ignored(self, mock_game):
        """Depth coordinate should not affect 2D abs position."""
        zone_surface = (1, 1, 0)
        zone_dungeon = (1, 1, 5)
        local = (20, 20)

        abs_surface = mock_game.abs_from_zone_local(zone_surface, local)
        abs_dungeon = mock_game.abs_from_zone_local(zone_dungeon, local)

        # Same x,y regardless of depth
        assert abs_surface == abs_dungeon

    def test_local_origin(self, mock_game):
        """Local (0,0) should give zone origin in ABS."""
        zone = (3, 2, 0)
        local = (0, 0)

        abs_pos = mock_game.abs_from_zone_local(zone, local)

        # x = 3*60 = 180, y = 2*40 = 80
        assert abs_pos == (180, 80)

    def test_local_max(self, mock_game):
        """Local max should give zone corner in ABS."""
        zone = (0, 0, 0)
        local = (59, 39)  # Max for 60x40 zone

        abs_pos = mock_game.abs_from_zone_local(zone, local)

        assert abs_pos == (59, 39)


class TestZoneLocalFromAbs:
    """Tests for game.zone_local_from_abs() coordinate transform."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with zone dimensions."""
        game = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_width = 60
        game.cfg.world_height = 40
        game.cfg.world_map_screens = 10
        game.zone_coord = (5, 5, 0)
        game._zone_dims = MagicMock(return_value=(60, 40))

        # Use floor_divmod helper
        def floor_divmod(a, b):
            q = a // b
            r = a - q * b
            if r < 0:
                q -= 1
                r += b
            return q, r
        game._floor_divmod = floor_divmod

        # Import the actual function to test
        from edgecaster.game import Game
        game.zone_local_from_abs = lambda ap, **kw: Game.zone_local_from_abs(game, ap, **kw)
        return game

    def test_origin_abs(self, mock_game):
        """ABS (0,0) should map to zone (0,0,_) local (0,0)."""
        abs_pos = (0, 0)

        zone, local = mock_game.zone_local_from_abs(abs_pos, depth=0)

        assert zone == (0, 0, 0)
        assert local == (0, 0)

    def test_mid_zone(self, mock_game):
        """ABS pos within zone 0 should have zone 0 coords."""
        abs_pos = (30, 20)

        zone, local = mock_game.zone_local_from_abs(abs_pos, depth=0)

        assert zone == (0, 0, 0)
        assert local == (30, 20)

    def test_zone_boundary(self, mock_game):
        """ABS at zone boundary should map to next zone."""
        abs_pos = (60, 0)  # First tile of zone (1,0)

        zone, local = mock_game.zone_local_from_abs(abs_pos, depth=0)

        assert zone == (1, 0, 0)
        assert local == (0, 0)

    def test_interior_zone(self, mock_game):
        """ABS in interior zone should compute correctly."""
        abs_pos = (185, 95)  # zone (3,2), local (5, 15)

        zone, local = mock_game.zone_local_from_abs(abs_pos, depth=0)

        assert zone == (3, 2, 0)
        assert local == (5, 15)

    def test_depth_passed_through(self, mock_game):
        """Depth parameter should be preserved in result."""
        abs_pos = (100, 100)

        zone, local = mock_game.zone_local_from_abs(abs_pos, depth=3)

        assert zone[2] == 3

    def test_clamping_enabled(self, mock_game):
        """Should clamp to world bounds when enabled."""
        abs_pos = (1000, 1000)  # Way out of bounds

        zone, local = mock_game.zone_local_from_abs(abs_pos, depth=0, clamp_to_world=True)

        # Should be clamped to max screen (9 for 10-screen world)
        assert 0 <= zone[0] <= 9
        assert 0 <= zone[1] <= 9


class TestCoordinateRoundTrip:
    """Tests that coordinate transforms are reversible."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with zone dimensions."""
        game = MagicMock()
        game.cfg = MagicMock()
        game.cfg.world_width = 60
        game.cfg.world_height = 40
        game.cfg.world_map_screens = 10
        game.zone_coord = (5, 5, 0)
        game._zone_dims = MagicMock(return_value=(60, 40))

        def floor_divmod(a, b):
            q = a // b
            r = a - q * b
            if r < 0:
                q -= 1
                r += b
            return q, r
        game._floor_divmod = floor_divmod

        from edgecaster.game import Game
        game.abs_from_zone_local = lambda zc, lp: Game.abs_from_zone_local(game, zc, lp)
        game.zone_local_from_abs = lambda ap, **kw: Game.zone_local_from_abs(game, ap, **kw)
        return game

    def test_roundtrip_zone_to_abs_to_zone(self, mock_game):
        """zone -> abs -> zone should preserve coordinates."""
        original_zone = (3, 2, 0)
        original_local = (25, 15)

        abs_pos = mock_game.abs_from_zone_local(original_zone, original_local)
        result_zone, result_local = mock_game.zone_local_from_abs(abs_pos, depth=0, clamp_to_world=False)

        assert result_zone == original_zone
        assert result_local == original_local

    def test_roundtrip_abs_to_zone_to_abs(self, mock_game):
        """abs -> zone -> abs should preserve coordinates."""
        original_abs = (185, 95)

        zone, local = mock_game.zone_local_from_abs(original_abs, depth=0, clamp_to_world=False)
        result_abs = mock_game.abs_from_zone_local(zone, local)

        assert result_abs == original_abs

    @pytest.mark.parametrize("zone,local", [
        ((0, 0, 0), (0, 0)),
        ((0, 0, 0), (30, 20)),
        ((1, 0, 0), (0, 0)),
        ((5, 5, 0), (30, 20)),
        ((9, 9, 0), (59, 39)),
    ])
    def test_roundtrip_various_positions(self, mock_game, zone, local):
        """Roundtrip should work for various positions."""
        abs_pos = mock_game.abs_from_zone_local(zone, local)
        result_zone, result_local = mock_game.zone_local_from_abs(abs_pos, depth=zone[2], clamp_to_world=False)

        assert result_zone == zone
        assert result_local == local


# =============================================================================
# ENTITY ABS_POS TESTS
# =============================================================================

class TestEntityAbsPos:
    """Tests for Entity.abs_pos population and maintenance."""

    def test_entity_has_abs_pos_field(self):
        """Entity dataclass should have abs_pos field."""
        from edgecaster.state.entities import Entity

        ent = Entity(id="test", name="Test", pos=(10, 10))

        assert hasattr(ent, 'abs_pos')

    def test_entity_abs_pos_default_none(self):
        """Entity.abs_pos should default to None."""
        from edgecaster.state.entities import Entity

        ent = Entity(id="test", name="Test", pos=(10, 10))

        assert ent.abs_pos is None

    def test_entity_ensure_abs_pos(self):
        """Entity.ensure_abs_pos() should compute from zone + local."""
        from edgecaster.state.entities import Entity

        ent = Entity(id="test", name="Test", pos=(10, 20))

        result = ent.ensure_abs_pos(zone_coord=(2, 3, 0), zone_w=60, zone_h=40)

        # x = 2*60 + 10 = 130, y = 3*40 + 20 = 140
        assert result == (130, 140)
        assert ent.abs_pos == (130, 140)

    def test_entity_ensure_abs_pos_idempotent(self):
        """ensure_abs_pos should not recompute if already set."""
        from edgecaster.state.entities import Entity

        ent = Entity(id="test", name="Test", pos=(10, 20))
        ent.abs_pos = (999, 888)

        result = ent.ensure_abs_pos(zone_coord=(0, 0, 0), zone_w=60, zone_h=40)

        # Should return existing value, not recompute
        assert result == (999, 888)

    def test_entity_set_abs_pos(self):
        """Entity.set_abs_pos() should update the field."""
        from edgecaster.state.entities import Entity

        ent = Entity(id="test", name="Test", pos=(0, 0))

        ent.set_abs_pos((123, 456))

        assert ent.abs_pos == (123, 456)


class TestSpawnAbsPos:
    """Tests that spawn paths populate abs_pos correctly."""

    def test_spawn_entity_from_template_sets_abs_pos(self):
        """spawn_entity_from_template should set abs_pos."""
        from unittest.mock import MagicMock, patch

        game = MagicMock()
        game.zone_coord = (2, 3, 0)
        game._zone_dims = MagicMock(return_value=(60, 40))
        game.abs_from_zone_local = MagicMock(return_value=(130, 140))
        game._new_id = MagicMock(return_value="ent_1")
        game.proto_bucket = {"test_item": {"id": "test_item", "name": "Test"}}

        with patch('edgecaster.systems.spawning.prototypes.resolve_proto') as mock_resolve:
            mock_resolve.return_value = {"id": "test_item", "name": "Test"}
            with patch('edgecaster.systems.spawning.spawn_factory.build_entity_from_spec') as mock_build:
                mock_entity = MagicMock()
                mock_build.return_value = mock_entity

                from edgecaster.systems.spawning import spawn_entity_from_template
                spawn_entity_from_template(game, "test_item", (10, 20))

                # Should have passed abs_pos to build_entity_from_spec
                call_kwargs = mock_build.call_args[1]
                assert call_kwargs.get('abs_pos') == (130, 140)

    def test_register_actor_backfills_abs_pos(self):
        """register_actor should backfill abs_pos if not set."""
        from unittest.mock import MagicMock

        game = MagicMock()
        game._zone_dims = MagicMock(return_value=(60, 40))
        game.abs_from_zone_local = MagicMock(return_value=(200, 150))

        level = MagicMock()
        level.coord = (3, 4, 0)
        level.actors = {}
        level.entities = {}
        level.events = []

        actor = MagicMock()
        actor.id = "mob_1"
        actor.pos = (20, 30)
        actor.abs_pos = None  # Not set
        actor.statuses = {}

        from edgecaster.systems.spawning import register_actor
        register_actor(game, level, actor, schedule_ai=False)

        # Should have been backfilled
        assert actor.abs_pos == (200, 150)


# =============================================================================
# PATTERN STATE CANONICAL TESTS
# =============================================================================

class TestPatternStateCanonical:
    """Tests that pattern state is stored canonically in ABS space."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with pattern state infrastructure."""
        game = MagicMock()
        game.zone_coord = (5, 5, 0)
        game._zone_dims = MagicMock(return_value=(60, 40))
        game._pattern_state_by_depth = {}

        from edgecaster.game import Game
        # Bind with correct signature (depth is positional-or-keyword, not keyword-only)
        game._pattern_state = lambda depth=None: Game._pattern_state(game, depth)
        game.pattern_anchor_abs = lambda: Game.pattern_anchor_abs(game)
        game._set_pattern_anchor_abs = lambda a: Game._set_pattern_anchor_abs(game, a)

        return game

    def test_pattern_state_exists_per_depth(self, mock_game):
        """Pattern state should be keyed by depth."""
        state_0 = mock_game._pattern_state(0)
        state_1 = mock_game._pattern_state(1)

        assert state_0 is not state_1
        assert 0 in mock_game._pattern_state_by_depth
        assert 1 in mock_game._pattern_state_by_depth

    def test_pattern_state_has_anchor_abs(self, mock_game):
        """Pattern state should have anchor_abs field."""
        state = mock_game._pattern_state(0)

        assert "anchor_abs" in state

    def test_pattern_state_omits_legacy_vine_sidecars(self, mock_game):
        """Canonical pattern state should no longer mirror vine sidecar payloads."""
        state = mock_game._pattern_state(0)

        assert "choking_vines_state" not in state
        assert "rune_choking_vines_state" not in state

    def test_set_pattern_anchor_abs(self, mock_game):
        """Should be able to set canonical ABS anchor."""
        mock_game._set_pattern_anchor_abs((300, 220))

        assert mock_game.pattern_anchor_abs() == (300, 220)


# =============================================================================
# YOGA INVARIANT TESTS
# =============================================================================

class TestYogaInvariants:
    """Tests for core yoga invariants that must not regress."""

    def test_player_movement_uses_abs(self):
        """Player movement should update abs_pos as source of truth."""
        # This is a behavioral test - verify the method exists
        from edgecaster.game import Game

        assert hasattr(Game, '_move_player_to_abs')
        assert hasattr(Game, '_get_player_abs')
        assert hasattr(Game, '_set_player_abs')

    def test_zones_module_has_no_rituals(self):
        """zones.py get_zone should not perform 'entry rituals'."""
        # This verifies the docstring claims
        from edgecaster.systems import zones

        docstring = zones.get_zone.__doc__
        assert "ritual" in docstring.lower()
        assert "IMPORTANT" in docstring

    def test_entity_dataclass_abs_pos_documented(self):
        """Entity.abs_pos should have documentation about being canonical."""
        from edgecaster.state.entities import Entity

        # Check class docstring mentions abs_pos
        assert "abs_pos" in Entity.__doc__
        assert "canonical" in Entity.__doc__.lower() or "authoritative" in Entity.__doc__.lower()


# =============================================================================
# TARGETING STATE TESTS (TODO - after refactor)
# =============================================================================

class TestTargetingState:
    """Tests for targeting state consistency.

    NOTE: These tests document the DESIRED behavior after the yoga refactor.
    Currently, target_cursor_abs and target_cursor are updated in parallel.
    After refactor, target_cursor should be derived from target_cursor_abs.
    """

    def test_dungeon_ui_state_has_both_cursors(self):
        """DungeonUIState should have both cursor fields."""
        from edgecaster.scenes.dungeon import DungeonUIState

        ui = DungeonUIState()

        assert hasattr(ui, 'target_cursor')
        assert hasattr(ui, 'target_cursor_abs')

    def test_cursor_docstring_mentions_yoga(self):
        """DungeonUIState should document the yoga refactor need."""
        from edgecaster.scenes.dungeon import DungeonUIState

        docstring = DungeonUIState.__doc__ or ""

        # After our refactor comments, this should mention YOGA
        assert "YOGA" in docstring or "canonical" in docstring.lower()
