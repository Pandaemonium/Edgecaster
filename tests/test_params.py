"""
Unit tests for the param system (systems/params.py).

Tests the stat-gated action parameter system in isolation.
"""

import pytest
from edgecaster.systems.params import ParamManager, default_param_defs


class TestDefaultParamDefs:
    """Tests for the default parameter definitions."""

    def test_default_defs_not_empty(self):
        """Default param defs should contain entries."""
        defs = default_param_defs()
        assert len(defs) > 0

    def test_branch_action_exists(self):
        """Branch action should be defined."""
        defs = default_param_defs()
        assert "branch" in defs
        assert "angle" in defs["branch"]
        assert "count" in defs["branch"]

    def test_activate_all_has_radius_and_damage(self):
        """activate_all should have radius and damage params."""
        defs = default_param_defs()
        assert "activate_all" in defs
        assert "radius" in defs["activate_all"]
        assert "damage" in defs["activate_all"]

    def test_param_spec_has_required_keys(self):
        """Each param spec should have values, thresholds, stat, label."""
        defs = default_param_defs()
        for action, params in defs.items():
            for key, spec in params.items():
                assert "values" in spec, f"{action}/{key} missing 'values'"
                assert "thresholds" in spec, f"{action}/{key} missing 'thresholds'"
                assert "stat" in spec, f"{action}/{key} missing 'stat'"
                assert "label" in spec, f"{action}/{key} missing 'label'"
                assert len(spec["values"]) == len(spec["thresholds"]), \
                    f"{action}/{key}: values/thresholds length mismatch"


class TestParamManagerInit:
    """Tests for ParamManager initialization."""

    def test_init_with_defaults(self):
        """ParamManager should initialize with default defs."""
        pm = ParamManager()
        assert pm.param_defs is not None
        assert len(pm.param_defs) > 0

    def test_init_with_custom_defs(self):
        """ParamManager should accept custom param defs."""
        custom = {
            "test_action": {
                "test_param": {
                    "values": [1, 2, 3],
                    "thresholds": [0, 5, 10],
                    "stat": "int",
                    "label": "Test",
                }
            }
        }
        pm = ParamManager(param_defs=custom)
        assert "test_action" in pm.param_defs
        assert pm.param_state[("test_action", "test_param")] == 0

    def test_state_initialized_to_zero(self):
        """All params should start at index 0."""
        pm = ParamManager()
        for action, params in pm.param_defs.items():
            for key in params:
                assert pm.param_state[(action, key)] == 0


class TestParamManagerStatGating:
    """Tests for stat-gated parameter access."""

    @pytest.fixture
    def pm_with_stats(self):
        """ParamManager with a controllable stat getter."""
        stats = {"int": 5, "res": 3}

        def getter():
            return stats

        pm = ParamManager(stat_getter=getter)
        pm._test_stats = stats  # For test manipulation
        return pm

    def test_allowed_index_with_zero_stat(self):
        """With stat=0, only tier 0 should be allowed."""
        pm = ParamManager(stat_getter=lambda: {"int": 0, "res": 0})
        # branch/angle has thresholds [0,1,2,3,4,5,6,7,8,9,10,11,12]
        allowed = pm.allowed_index("branch", "angle")
        assert allowed == 0

    def test_allowed_index_with_high_stat(self):
        """With high stat, all tiers should be allowed."""
        pm = ParamManager(stat_getter=lambda: {"int": 20, "res": 20})
        # branch/angle has 13 values (indices 0-12)
        allowed = pm.allowed_index("branch", "angle")
        assert allowed == 12

    def test_allowed_index_partial_unlock(self):
        """With medium stat, some tiers should be locked."""
        pm = ParamManager(stat_getter=lambda: {"int": 5, "res": 0})
        # branch/angle thresholds: [0,1,2,3,4,5,6,...]
        # With int=5, should allow up to index 5
        allowed = pm.allowed_index("branch", "angle")
        assert allowed == 5

    def test_allowed_index_unknown_action(self):
        """Unknown action should return -1."""
        pm = ParamManager()
        assert pm.allowed_index("nonexistent", "param") == -1

    def test_allowed_index_unknown_param(self):
        """Unknown param should return -1."""
        pm = ParamManager()
        assert pm.allowed_index("branch", "nonexistent") == -1


class TestParamManagerGetValue:
    """Tests for getting parameter values."""

    def test_get_value_default(self):
        """Default value should be index 0."""
        pm = ParamManager(stat_getter=lambda: {"int": 10, "res": 10})
        # branch/angle values: [30,35,40,45,50,55,60,65,70,75,80,85,90]
        assert pm.get_value("branch", "angle") == 30

    def test_get_value_respects_stat_cap(self):
        """Value should be capped by allowed index even if state is higher."""
        pm = ParamManager(stat_getter=lambda: {"int": 2, "res": 0})
        # Manually set state beyond allowed
        pm.param_state[("branch", "angle")] = 10
        # Should be capped to allowed index (2)
        value = pm.get_value("branch", "angle")
        # branch/angle values at index 2 is 40
        assert value == 40

    def test_get_value_activate_all_damage_auto_max(self):
        """activate_all/damage should auto-max to allowed tier."""
        pm = ParamManager(stat_getter=lambda: {"res": 8})
        # damage thresholds: [0, 4, 8], values: [1, 2, 3]
        # With res=8, allowed index is 2, so value should be 3
        value = pm.get_value("activate_all", "damage")
        assert value == 3

    def test_get_value_unknown_returns_none(self):
        """Unknown param should return None."""
        pm = ParamManager()
        assert pm.get_value("nonexistent", "param") is None


class TestParamManagerAdjust:
    """Tests for adjusting parameter values."""

    @pytest.fixture
    def pm(self):
        return ParamManager(stat_getter=lambda: {"int": 5, "res": 5})

    def test_adjust_up_success(self, pm):
        """Adjusting up within allowed range should succeed."""
        success, msg = pm.adjust("branch", "angle", 1)
        assert success is True
        assert msg == ""
        assert pm.param_state[("branch", "angle")] == 1

    def test_adjust_down_success(self, pm):
        """Adjusting down should succeed."""
        pm.param_state[("branch", "angle")] = 3
        success, msg = pm.adjust("branch", "angle", -1)
        assert success is True
        assert pm.param_state[("branch", "angle")] == 2

    def test_adjust_beyond_allowed_fails(self, pm):
        """Adjusting beyond allowed index should fail."""
        pm.param_state[("branch", "angle")] = 5  # Max allowed with int=5
        success, msg = pm.adjust("branch", "angle", 1)
        assert success is False
        assert "Requires" in msg
        assert "INT" in msg

    def test_adjust_below_zero_clamped(self, pm):
        """Adjusting below 0 should clamp to 0."""
        pm.param_state[("branch", "angle")] = 0
        success, msg = pm.adjust("branch", "angle", -1)
        assert success is False  # No change occurred
        assert pm.param_state[("branch", "angle")] == 0

    def test_adjust_unknown_param_fails(self, pm):
        """Adjusting unknown param should fail gracefully."""
        success, msg = pm.adjust("nonexistent", "param", 1)
        assert success is False
        assert msg == "Unknown parameter"

    def test_adjust_clamps_if_stats_dropped(self):
        """If stats dropped, adjust should clamp state first."""
        stats = {"int": 10}
        pm = ParamManager(stat_getter=lambda: stats)
        pm.param_state[("branch", "angle")] = 8

        # Now drop stats
        stats["int"] = 3
        # Try to adjust - should first clamp to allowed (3)
        success, msg = pm.adjust("branch", "angle", -1)
        # Should succeed, going from 3 to 2
        assert success is True
        assert pm.param_state[("branch", "angle")] == 2


class TestParamManagerView:
    """Tests for the UI view generation."""

    def test_view_returns_list(self):
        """view() should return a list."""
        pm = ParamManager(stat_getter=lambda: {"int": 5, "res": 5})
        result = pm.view("branch")
        assert isinstance(result, list)

    def test_view_contains_expected_keys(self):
        """Each view entry should have required keys."""
        pm = ParamManager(stat_getter=lambda: {"int": 5, "res": 5})
        result = pm.view("branch")
        for entry in result:
            assert "key" in entry
            assert "label" in entry
            assert "value" in entry
            assert "allowed_idx" in entry
            assert "current_idx" in entry
            assert "next_req" in entry

    def test_view_hides_activate_all_damage(self):
        """activate_all damage should be hidden from view."""
        pm = ParamManager(stat_getter=lambda: {"res": 5})
        result = pm.view("activate_all")
        keys = [e["key"] for e in result]
        assert "damage" not in keys
        assert "radius" in keys

    def test_view_shows_next_requirement(self):
        """next_req should show requirement for next tier if locked."""
        pm = ParamManager(stat_getter=lambda: {"int": 0, "res": 0})
        result = pm.view("branch")
        angle_entry = next(e for e in result if e["key"] == "angle")
        # With int=0, next tier (index 1) requires int=1
        assert "INT" in angle_entry["next_req"]

    def test_view_empty_for_unknown_action(self):
        """view() should return empty list for unknown action."""
        pm = ParamManager()
        result = pm.view("nonexistent")
        assert result == []


class TestParamManagerRecalcMax:
    """Tests for recalc_max behavior."""

    def test_recalc_max_sets_to_allowed(self):
        """recalc_max should set params to max allowed tier."""
        pm = ParamManager(stat_getter=lambda: {"int": 5, "res": 5})
        pm.recalc_max()
        # branch/angle with int=5 should be at index 5
        assert pm.param_state[("branch", "angle")] == 5

    def test_recalc_max_skips_custom_amplitude(self):
        """custom/amplitude should not be auto-maxed."""
        pm = ParamManager(stat_getter=lambda: {"int": 10, "res": 10})
        pm.param_state[("custom", "amplitude")] = 1  # User set
        pm.recalc_max()
        # Should remain at user choice, not maxed
        assert pm.param_state[("custom", "amplitude")] == 1

    def test_recalc_max_skips_rune_anchor(self):
        """place_rune_anchor params should not be auto-maxed."""
        pm = ParamManager(stat_getter=lambda: {"res": 10})
        pm.param_state[("place_rune_anchor", "range")] = 1
        pm.recalc_max()
        # Should remain at user choice
        assert pm.param_state[("place_rune_anchor", "range")] == 1


class TestParamManagerSerialization:
    """Tests for state save/restore."""

    def test_get_state_returns_copy(self):
        """get_state should return a copy of state."""
        pm = ParamManager()
        pm.param_state[("branch", "angle")] = 5
        state = pm.get_state()
        state[("branch", "angle")] = 99
        # Original should be unchanged
        assert pm.param_state[("branch", "angle")] == 5

    def test_set_state_restores(self):
        """set_state should restore state from dict."""
        pm = ParamManager()
        saved = {("branch", "angle"): 7, ("branch", "count"): 2}
        pm.set_state(saved)
        assert pm.param_state[("branch", "angle")] == 7
        assert pm.param_state[("branch", "count")] == 2
