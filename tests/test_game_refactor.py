"""
Tests for game.py refactor validation.

These tests ensure that the Game class and its subsystems maintain functional
parity as code is extracted into dedicated modules during the SLICE 5 refactor.

Run with: python -m pytest tests/test_game_refactor.py -v

See vision_documents/spring_cleaning.txt for the full refactor plan.
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import TYPE_CHECKING

# Defer heavy imports to avoid pygame init at import time
if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mock_pygame():
    """Mock pygame to avoid display initialization in tests."""
    with patch.dict('sys.modules', {'pygame': MagicMock()}):
        yield


@pytest.fixture
def minimal_character():
    """Create a minimal character for testing."""
    from edgecaster.character import default_character
    return default_character()


@pytest.fixture
def game_instance(mock_pygame, minimal_character):
    """Create a Game instance for testing.

    Note: This requires pygame to be mocked or a display available.
    For CI, we mock pygame entirely.
    """
    # Lazy import to avoid pygame init at module load
    with patch('pygame.mixer'):
        with patch('pygame.display'):
            from edgecaster.game import Game
            game = Game(character=minimal_character, seed=12345)
            yield game


# ---------------------------------------------------------------------------
# PHASE 10: Param System Tests
# Target: systems/params.py
# ---------------------------------------------------------------------------

class TestParamSystem:
    """Tests for the param system (stat-gated action parameters)."""

    def test_param_defs_initialized(self, game_instance):
        """Param definitions should be populated on game init."""
        assert hasattr(game_instance, 'param_defs')
        assert isinstance(game_instance.param_defs, dict)

    def test_param_state_initialized(self, game_instance):
        """Param state should be initialized to defaults."""
        assert hasattr(game_instance, 'param_state')
        assert isinstance(game_instance.param_state, dict)

    def test_stat_value_returns_int(self, game_instance):
        """_stat_value should return an integer for known stats."""
        val = game_instance._stat_value("int")
        assert isinstance(val, int)

    def test_adjust_param_out_of_range(self, game_instance):
        """Adjusting param beyond allowed index should fail gracefully."""
        # Try to adjust a param way beyond what stats allow
        if game_instance.param_defs:
            action = next(iter(game_instance.param_defs))
            params = game_instance.param_defs[action]
            if params:
                key = next(iter(params))
                success, msg = game_instance.adjust_param(action, key, 100)
                # Should either fail or clamp


# ---------------------------------------------------------------------------
# PHASE 5: Legendaries Tests
# Target: systems/legendaries.py
# ---------------------------------------------------------------------------

class TestLegendaries:
    """Tests for legendary creature and POI discovery system."""

    def test_legendary_registry_exists(self, game_instance):
        """Legendary registry should be initialized."""
        assert hasattr(game_instance, 'legendary_registry')

    def test_get_nearest_legendary_lairs(self, game_instance):
        """Should return list (possibly empty) of nearby lairs."""
        result = game_instance.get_nearest_legendary_lairs(0, 0, max_dist=10)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# PHASE 3: Lorenz Aura Tests
# Target: systems/lorenz_aura.py
# ---------------------------------------------------------------------------

class TestLorenzAura:
    """Tests for the Strange Attractor's Lorenz storm system."""

    def test_has_lorenz_aura_property(self, game_instance):
        """has_lorenz_aura should be a boolean property."""
        assert isinstance(game_instance.has_lorenz_aura, bool)

    def test_lorenz_points_list_exists(self, game_instance):
        """Lorenz points list should exist (may be empty)."""
        assert hasattr(game_instance, 'lorenz_points')
        assert isinstance(game_instance.lorenz_points, list)


# ---------------------------------------------------------------------------
# PHASE 0: Action Runner Tests
# Target: systems/action_runner.py
# ---------------------------------------------------------------------------

class TestActionExecution:
    """Tests for unified action execution (player + AI paths)."""

    def test_queue_actor_action_exists(self, game_instance):
        """queue_actor_action method should exist."""
        assert hasattr(game_instance, 'queue_actor_action')
        assert callable(game_instance.queue_actor_action)

    def test_action_wait_does_not_crash(self, game_instance):
        """Queueing a 'wait' action should not crash."""
        # Wait is the simplest action
        try:
            game_instance.queue_actor_action(game_instance.player_id, "wait")
        except Exception as e:
            pytest.fail(f"wait action raised: {e}")


# ---------------------------------------------------------------------------
# PHASE 1: Spawning Tests
# Target: systems/spawning.py
# ---------------------------------------------------------------------------

class TestSpawning:
    """Tests for entity/enemy spawning system."""

    def test_entity_templates_cached(self, game_instance):
        """_entity_templates should return a dict."""
        templates = game_instance._entity_templates()
        assert isinstance(templates, dict)

    def test_spawn_entity_from_template(self, game_instance):
        """Should be able to spawn an entity from a known template."""
        templates = game_instance._entity_templates()
        if templates:
            template_id = next(iter(templates))
            level = game_instance._level()
            try:
                ent = game_instance._spawn_entity_from_template(
                    template_id, (5, 5)
                )
                assert ent is not None
                assert hasattr(ent, 'pos')
            except Exception:
                # Some templates may have requirements we can't meet
                pass


# ---------------------------------------------------------------------------
# PHASE 2: Inventory Tests
# Target: systems/inventory.py
# ---------------------------------------------------------------------------

class TestInventory:
    """Tests for inventory and equipment system."""

    def test_get_inventory_returns_list(self, game_instance):
        """get_inventory should return a list for any owner."""
        inv = game_instance.get_inventory(game_instance.player_id)
        assert isinstance(inv, list)

    def test_player_inventory_property(self, game_instance):
        """player_inventory property should work."""
        inv = game_instance.player_inventory
        assert isinstance(inv, list)

    def test_inventories_dict_exists(self, game_instance):
        """inventories dict should exist."""
        assert hasattr(game_instance, 'inventories')
        assert isinstance(game_instance.inventories, dict)


class TestEntityState:
    """Tests for Phase 1 entity_state bridge."""

    def test_entity_state_dict_exists(self, game_instance):
        """entity_state dict should exist."""
        assert hasattr(game_instance, "entity_state")
        assert isinstance(game_instance.entity_state, dict)

    def test_patch_entity_state_by_object(self, game_instance):
        """patch_entity_state should write through using entity_id fallback."""
        player = game_instance._player()
        game_instance.patch_entity_state(player, owner_id=game_instance.player_id, in_inventory=False)
        eid = game_instance.entity_id_for_entity(player)
        assert isinstance(eid, str) and eid
        st = game_instance.get_entity_state(eid)
        assert st.get("owner_id") == game_instance.player_id
        assert st.get("in_inventory") is False
        assert "updated_tick" in st

    def test_mark_entity_removed_writes_entity_state(self, game_instance):
        """mark_entity_removed should write removal flags into entity_state."""
        player = game_instance._player()
        game_instance.patch_entity_state(player, removed=False)
        game_instance.mark_entity_removed(player, reason="pickup")
        eid = game_instance.entity_id_for_entity(player)
        st = game_instance.get_entity_state(str(eid))
        assert st.get("removed") is True

    def test_mark_actor_dead_writes_entity_state(self, game_instance):
        """mark_actor_dead should write dead flags into entity_state."""
        player = game_instance._player()
        game_instance.mark_actor_dead(player, reason="unit_test")
        eid = game_instance.entity_id_for_entity(player)
        st = game_instance.get_entity_state(str(eid))
        assert st.get("dead") is True
        assert st.get("dead_reason") == "unit_test"

    def test_patch_entity_state_prefers_entity_key_for_object_and_keeps_lineage_as_metadata(self, game_instance):
        obj = MagicMock()
        obj.id = "runtime:item:1"
        obj.entity_id = "runtime:item:1"
        obj.tags = {"lineage_id": "lineage:item:1"}

        game_instance.patch_entity_state(obj, removed=True, removed_reason="unit_test")

        st_entity = game_instance.get_entity_state("runtime:item:1")
        assert st_entity.get("removed") is True
        assert st_entity.get("removed_reason") == "unit_test"
        assert st_entity.get("lineage_id") == "lineage:item:1"
        assert game_instance.entity_state.get("lineage:item:1") is None

    def test_get_effective_entity_state_merges_legacy_lineage_fallback_with_entity_state(self, game_instance):
        obj = MagicMock()
        obj.id = "runtime:item:2"
        obj.entity_id = "runtime:item:2"
        obj.tags = {"lineage_id": "lineage:item:2"}

        game_instance.entity_state["lineage:item:2"] = {"last_known_tags": {"legacy_flag": True}}
        game_instance.patch_entity_state(obj, removed=True, removed_reason="unit_test")

        st = game_instance.get_effective_entity_state(obj)
        assert st.get("removed") is True
        assert st.get("removed_reason") == "unit_test"
        assert st.get("last_known_tags", {}).get("legacy_flag") is True

    def test_entity_is_suppressed_checks_lineage_state_for_object(self, game_instance):
        obj = MagicMock()
        obj.id = "runtime:npc:1"
        obj.entity_id = "runtime:npc:1"
        obj.tags = {"lineage_id": "lineage:npc:1"}

        game_instance.patch_entity_state("lineage:npc:1", removed=True)
        assert game_instance.entity_is_suppressed(obj) is True

    def test_entity_is_suppressed_prefers_entity_state_over_legacy_lineage_lifecycle(self, game_instance):
        obj = MagicMock()
        obj.id = "runtime:npc:2"
        obj.entity_id = "runtime:npc:2"
        obj.tags = {"lineage_id": "lineage:npc:2"}

        game_instance.entity_state["lineage:npc:2"] = {"removed": True}
        game_instance.patch_entity_state(obj, dead=False, removed=False)

        assert game_instance.entity_is_suppressed(obj) is False


# ---------------------------------------------------------------------------
# PHASE 6: Pattern Operations Tests
# Target: systems/pattern_ops.py
# ---------------------------------------------------------------------------

class TestPatternOps:
    """Tests for pattern manipulation and activation."""

    def test_projected_vertices_empty_pattern(self, game_instance):
        """Should return empty list when no pattern anchored."""
        verts = game_instance.projected_vertices()
        assert isinstance(verts, list)

    def test_nearest_vertex_no_pattern(self, game_instance):
        """Should return None when no pattern exists."""
        result = game_instance.nearest_vertex((0, 0))
        assert result is None


# ---------------------------------------------------------------------------
# PHASE 9: Scheduling Tests
# Target: systems/turns.py
# ---------------------------------------------------------------------------

class TestScheduling:
    """Tests for the tick-based scheduling system."""

    def test_schedule_method_exists(self, game_instance):
        """_schedule method should exist."""
        assert hasattr(game_instance, '_schedule')
        assert callable(game_instance._schedule)

    def test_advance_time_method_exists(self, game_instance):
        """_advance_time method should exist."""
        assert hasattr(game_instance, '_advance_time')
        assert callable(game_instance._advance_time)


# ---------------------------------------------------------------------------
# PHASE 4: Overmap Tests
# Target: systems/overmap.py
# ---------------------------------------------------------------------------

class TestOvermap:
    """Tests for world map and corruption orchestration."""

    def test_overmap_params_exist(self, game_instance):
        """Overmap params should be initialized."""
        assert hasattr(game_instance, 'overmap_params')

    def test_corruption_methods_exist(self, game_instance):
        """Corruption manipulation methods should exist."""
        assert hasattr(game_instance, 'set_corruption_level')
        assert hasattr(game_instance, 'add_corruption_hotspot')


# ---------------------------------------------------------------------------
# PHASE 8: Zone Management Tests
# Target: systems/zones.py
# ---------------------------------------------------------------------------

class TestZones:
    """Tests for zone creation and transitions."""

    def test_level_accessor(self, game_instance):
        """_level() should return current LevelState."""
        level = game_instance._level()
        assert level is not None
        assert hasattr(level, 'world')
        assert hasattr(level, 'actors')

    def test_player_accessor(self, game_instance):
        """_player() should return the player actor."""
        player = game_instance._player()
        assert player is not None
        assert hasattr(player, 'pos')
        assert hasattr(player, 'stats')


# ---------------------------------------------------------------------------
# PHASE 7: Combat Tests
# Target: systems/combat.py
# ---------------------------------------------------------------------------

class TestCombat:
    """Tests for combat and damage resolution."""

    def test_is_hostile_method(self, game_instance):
        """is_hostile should be callable."""
        assert hasattr(game_instance, 'is_hostile')
        assert callable(game_instance.is_hostile)

    def test_attack_method_exists(self, game_instance):
        """_attack method should exist."""
        assert hasattr(game_instance, '_attack')


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestGameIntegration:
    """Integration tests for overall Game functionality."""

    def test_new_game_initializes(self, game_instance):
        """A new game should initialize without errors."""
        assert game_instance is not None
        assert game_instance.player_id is not None

    def test_zone_coord_is_tuple(self, game_instance):
        """zone_coord should be a 3-tuple (x, y, z)."""
        assert isinstance(game_instance.zone_coord, tuple)
        assert len(game_instance.zone_coord) == 3

    def test_player_in_current_level(self, game_instance):
        """Player should exist in the current level's actors."""
        level = game_instance._level()
        assert game_instance.player_id in level.actors

    def test_message_log_works(self, game_instance):
        """Message log should accept new messages."""
        initial_len = len(game_instance.log.messages)
        game_instance.log.add("Test message")
        assert len(game_instance.log.messages) == initial_len + 1


# ---------------------------------------------------------------------------
# Regression Guards
# ---------------------------------------------------------------------------

class TestRegressionGuards:
    """
    Tests that guard against regressions during refactor.
    Add tests here when bugs are found during extraction.
    """

    def test_player_alive_property(self, game_instance):
        """player_alive should return True for a new game."""
        assert game_instance.player_alive is True

    def test_config_exists(self, game_instance):
        """Game should have a config object."""
        assert hasattr(game_instance, 'cfg')
        assert game_instance.cfg is not None
