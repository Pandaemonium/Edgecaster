"""
Unit tests for the action runner system (systems/action_runner.py).

Tests the unified action execution path for players and AI.
"""

import pytest
from unittest.mock import MagicMock, patch
import math

from edgecaster.systems.action_runner import (
    ActionResult,
    find_action_origin,
    get_cooldown,
    get_charges,
    slow_mult,
    apply_tick_offset,
    calculate_final_delay,
    find_charge_item,
    consume_charge,
    apply_cooldown,
    check_cooldown_gate,
    check_charge_gate,
    run_action,
    run_ai_action,
)


class TestActionResult:
    """Tests for the ActionResult dataclass."""

    def test_basic_result(self):
        """Should store execution status and delay."""
        result = ActionResult(executed=True, delay=10)
        assert result.executed is True
        assert result.delay == 10
        assert result.message is None
        assert result.pending_confirm is False

    def test_failed_result_with_message(self):
        """Should store failure message."""
        result = ActionResult(executed=False, delay=0, message="On cooldown")
        assert result.executed is False
        assert result.message == "On cooldown"

    def test_pending_confirm(self):
        """Should indicate pending confirmation."""
        result = ActionResult(executed=False, delay=0, pending_confirm=True)
        assert result.pending_confirm is True


class TestFindActionOrigin:
    """Tests for action origin resolution."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        return game

    @pytest.fixture
    def mock_actor(self):
        """Create a mock actor."""
        actor = MagicMock()
        actor.id = "player"
        actor.tags = {}
        actor.actions = ["move", "attack"]
        return actor

    def test_intrinsic_action_from_actions_list(self, mock_game, mock_actor):
        """Should recognize actions in actor.actions as intrinsic."""
        origin, is_intrinsic = find_action_origin(mock_game, mock_actor, "move")
        assert origin is mock_actor
        assert is_intrinsic is True

    def test_intrinsic_action_from_tags(self, mock_game, mock_actor):
        """Should recognize actions in intrinsic_actions tag."""
        mock_actor.tags = {"intrinsic_actions": ["move", "special"]}
        origin, is_intrinsic = find_action_origin(mock_game, mock_actor, "special")
        assert origin is mock_actor
        assert is_intrinsic is True

    def test_item_granted_action(self, mock_game, mock_actor):
        """Should find item origin for granted actions."""
        wand = MagicMock()

        with patch('edgecaster.systems.action_runner.inventory_system.get_inventory', return_value=[wand]):
            with patch('edgecaster.systems.action_runner.item_grants') as mock_grants:
                mock_grants.find_grant_origin.return_value = wand
                origin, is_intrinsic = find_action_origin(mock_game, mock_actor, "fireball")

        assert origin is wand
        assert is_intrinsic is False

    def test_unknown_action_defaults_to_actor(self, mock_game, mock_actor):
        """Should default to actor for unknown actions."""

        with patch('edgecaster.systems.action_runner.inventory_system.get_inventory', return_value=[]):
            with patch('edgecaster.systems.action_runner.item_grants') as mock_grants:
                mock_grants.find_grant_origin.return_value = None
                origin, is_intrinsic = find_action_origin(mock_game, mock_actor, "unknown")

        assert origin is mock_actor
        assert is_intrinsic is True


class TestGetCooldown:
    """Tests for cooldown retrieval."""

    def test_no_cooldown(self):
        """Should return 0 for no cooldown."""
        origin = MagicMock()
        origin.cooldowns = {}
        assert get_cooldown(origin, "attack") == 0

    def test_active_cooldown(self):
        """Should return remaining cooldown."""
        origin = MagicMock()
        origin.cooldowns = {"attack": 5}
        assert get_cooldown(origin, "attack") == 5

    def test_no_cooldowns_attr(self):
        """Should handle missing cooldowns attribute."""
        origin = MagicMock(spec=[])
        assert get_cooldown(origin, "attack") == 0


class TestGetCharges:
    """Tests for charge retrieval."""

    def test_no_charges_tag(self):
        """Should return None for non-charged items."""
        item = MagicMock()
        item.tags = {}
        assert get_charges(item) is None

    def test_has_charges(self):
        """Should return remaining charges."""
        item = MagicMock()
        item.tags = {"charges": 3}
        assert get_charges(item) == 3

    def test_empty_charges(self):
        """Should return 0 for spent items."""
        item = MagicMock()
        item.tags = {"charges": 0}
        assert get_charges(item) == 0


class TestSlowMult:
    """Tests for slow multiplier calculation."""

    def test_no_slow(self):
        """Should return 1.0 for no slow effect."""
        actor = MagicMock()
        actor.tags = {}
        assert slow_mult(actor) == 1.0

    def test_frozen_slow(self):
        """Should return frozen_slow value."""
        actor = MagicMock()
        actor.tags = {"frozen_slow": 2.0}
        assert slow_mult(actor) == 2.0

    def test_minimum_1(self):
        """Should clamp to minimum of 1.0."""
        actor = MagicMock()
        actor.tags = {"frozen_slow": 0.5}
        assert slow_mult(actor) == 1.0


class TestApplyTickOffset:
    """Tests for tick offset application."""

    def test_no_offset(self):
        """Should return original delay with no offset."""
        actor = MagicMock()
        actor.tags = {}
        assert apply_tick_offset(actor, 10) == 10

    def test_positive_offset(self):
        """Should add positive offset (slowing)."""
        actor = MagicMock()
        actor.tags = {"action_tick_offset": 5}
        assert apply_tick_offset(actor, 10) == 15

    def test_negative_offset(self):
        """Should subtract negative offset (haste)."""
        actor = MagicMock()
        actor.tags = {"action_tick_offset": -3}
        assert apply_tick_offset(actor, 10) == 7

    def test_clamps_to_1(self):
        """Should clamp non-instant actions to 1 minimum."""
        actor = MagicMock()
        actor.tags = {"action_tick_offset": -20}
        assert apply_tick_offset(actor, 10) == 1

    def test_instant_unchanged(self):
        """Should not modify instant actions (delay 0)."""
        actor = MagicMock()
        actor.tags = {"action_tick_offset": -5}
        assert apply_tick_offset(actor, 0) == 0


class TestCalculateFinalDelay:
    """Tests for full delay calculation."""

    def test_combines_slow_and_offset(self):
        """Should apply both slow and offset."""
        actor = MagicMock()
        actor.tags = {"frozen_slow": 2.0, "action_tick_offset": -5}
        # 10 * 2.0 = 20, then 20 - 5 = 15
        assert calculate_final_delay(actor, 10) == 15

    def test_no_modifiers(self):
        """Should return base delay with no modifiers."""
        actor = MagicMock()
        actor.tags = {}
        assert calculate_final_delay(actor, 10) == 10


class TestFindChargeItem:
    """Tests for finding charged items."""

    def test_actor_origin_returns_none(self):
        """Should return None if origin is the actor."""
        game = MagicMock()
        actor = MagicMock()
        actor.id = "player"
        assert find_charge_item(game, actor, actor) is None

    def test_none_origin_returns_none(self):
        """Should return None for None origin."""
        game = MagicMock()
        actor = MagicMock()
        assert find_charge_item(game, actor, None) is None

    def test_item_in_inventory(self):
        """Should return item if in inventory."""
        game = MagicMock()
        actor = MagicMock()
        actor.id = "player"
        wand = MagicMock()

        with patch("edgecaster.systems.action_runner.inventory_system.get_inventory", return_value=[wand]):
            assert find_charge_item(game, actor, wand) is wand

    def test_item_not_in_inventory(self):
        """Should return None if item not in inventory."""
        game = MagicMock()
        actor = MagicMock()
        actor.id = "player"
        wand = MagicMock()

        with patch("edgecaster.systems.action_runner.inventory_system.get_inventory", return_value=[]):
            assert find_charge_item(game, actor, wand) is None


class TestConsumeCharge:
    """Tests for charge consumption."""

    def test_consumes_charge(self):
        """Should decrement charge count."""
        game = MagicMock()
        game.player_id = "other"
        actor = MagicMock()
        actor.id = "player"

        item = MagicMock()
        item.tags = {"charges": 3}

        consume_charge(game, actor, item)

        assert item.tags["charges"] == 2

    def test_sets_max_charges(self):
        """Should track max_charges on first consumption."""
        game = MagicMock()
        game.player_id = "other"
        actor = MagicMock()
        actor.id = "player"

        item = MagicMock()
        item.tags = {"charges": 3}

        consume_charge(game, actor, item)

        assert item.tags["max_charges"] == 3

    def test_logs_spent_message_for_player(self):
        """Should log message when player's item is spent."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        actor = MagicMock()
        actor.id = "player"

        item = MagicMock()
        item.name = "Fire Wand"
        item.tags = {"charges": 1}

        with patch('edgecaster.systems.action_runner.equipment_system') as mock_equip:
            mock_equip.is_equipped.return_value = False
            consume_charge(game, actor, item)

        game.log.add.assert_called_once()
        assert "spent" in game.log.add.call_args[0][0].lower()


class TestApplyCooldown:
    """Tests for cooldown application."""

    def test_applies_cooldown(self):
        """Should set cooldown on origin."""
        origin = MagicMock()
        origin.cooldowns = {}
        apply_cooldown(origin, "fireball", 5)
        assert origin.cooldowns["fireball"] == 5

    def test_skips_zero_cooldown(self):
        """Should not set cooldown if 0."""
        origin = MagicMock()
        origin.cooldowns = {}
        apply_cooldown(origin, "fireball", 0)
        assert "fireball" not in origin.cooldowns

    def test_handles_none_origin(self):
        """Should not crash on None origin."""
        apply_cooldown(None, "fireball", 5)  # Should not raise


class TestCheckCooldownGate:
    """Tests for cooldown gating."""

    def test_passes_when_clear(self):
        """Should return None when no cooldown."""
        game = MagicMock()
        actor = MagicMock()
        origin = MagicMock()
        origin.cooldowns = {}

        result = check_cooldown_gate(game, actor, origin, "attack", is_player=True)
        assert result is None

    def test_blocks_when_on_cooldown(self):
        """Should return message when on cooldown."""
        game = MagicMock()
        actor = MagicMock()
        origin = MagicMock()
        origin.cooldowns = {"attack": 5}

        result = check_cooldown_gate(game, actor, origin, "attack", is_player=True)
        assert result is not None
        assert "recharging" in result.lower()


class TestCheckChargeGate:
    """Tests for charge gating."""

    def test_passes_for_non_charged(self):
        """Should return None for non-charged items."""
        game = MagicMock()
        actor = MagicMock()

        result = check_charge_gate(game, actor, None, is_player=True)
        assert result is None

    def test_blocks_when_empty(self):
        """Should return message when out of charges."""
        game = MagicMock()
        actor = MagicMock()
        item = MagicMock()
        item.tags = {"charges": 0}

        result = check_charge_gate(game, actor, item, is_player=True)
        assert result is not None
        assert "out of charges" in result.lower()


class TestRunAction:
    """Tests for the main run_action function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with necessary attributes."""
        game = MagicMock()
        game.player_id = "player"
        game.cfg = MagicMock()
        game.cfg.action_time_fast = 10
        game.log = MagicMock()

        level = MagicMock()
        actor = MagicMock()
        actor.id = "player"
        actor.alive = True
        actor.tags = {"intrinsic_actions": ["move"]}
        actor.cooldowns = {}
        level.actors = {"player": actor}
        game._level.return_value = level

        return game

    def test_executes_action(self, mock_game):
        """Should execute the action function."""
        with patch('edgecaster.systems.action_runner.get_action') as mock_get:
            action_def = MagicMock()
            action_def.func = MagicMock()
            action_def.cooldown_ticks = 0
            action_def.confirm = None
            mock_get.return_value = action_def

            with patch('edgecaster.systems.action_runner.action_delay') as mock_delay:
                mock_delay.return_value = 10

                result = run_action(mock_game, "player", "move")

        assert result.executed is True
        assert result.delay == 10
        action_def.func.assert_called_once()

    def test_blocks_on_cooldown(self, mock_game):
        """Should block action when on cooldown."""
        actor = mock_game._level().actors["player"]
        actor.cooldowns = {"move": 5}

        with patch('edgecaster.systems.action_runner.get_action') as mock_get:
            action_def = MagicMock()
            action_def.cooldown_ticks = 10
            mock_get.return_value = action_def

            with patch('edgecaster.systems.action_runner.action_delay') as mock_delay:
                mock_delay.return_value = 10

                result = run_action(mock_game, "player", "move")

        assert result.executed is False
        mock_game.log.add.assert_called()

    def test_handles_unknown_action(self, mock_game):
        """Should return error for unknown action."""
        with patch('edgecaster.systems.action_runner.get_action') as mock_get:
            mock_get.side_effect = KeyError("Unknown action")

            result = run_action(mock_game, "player", "nonexistent")

        assert result.executed is False
        assert "unknown" in result.message.lower()

    def test_handles_dead_actor(self, mock_game):
        """Should return error for dead actor."""
        mock_game._level().actors["player"].alive = False

        result = run_action(mock_game, "player", "move")

        assert result.executed is False

    def test_applies_delay_modifiers(self, mock_game):
        """Should apply slow and offset to delay."""
        actor = mock_game._level().actors["player"]
        actor.tags = {"intrinsic_actions": ["move"], "frozen_slow": 2.0}

        with patch('edgecaster.systems.action_runner.get_action') as mock_get:
            action_def = MagicMock()
            action_def.func = MagicMock()
            action_def.cooldown_ticks = 0
            action_def.confirm = None
            mock_get.return_value = action_def

            with patch('edgecaster.systems.action_runner.action_delay') as mock_delay:
                mock_delay.return_value = 10

                result = run_action(mock_game, "player", "move")

        assert result.delay == 20  # 10 * 2.0 slow


class TestRunAiAction:
    """Tests for AI action execution."""

    def test_skips_confirmations(self):
        """Should skip all confirmation prompts for AI."""
        game = MagicMock()
        game.player_id = "player"
        game.cfg = MagicMock()
        game.cfg.action_time_fast = 10

        level = MagicMock()
        actor = MagicMock()
        actor.id = "enemy"
        actor.alive = True
        actor.tags = {"intrinsic_actions": ["attack"]}
        actor.cooldowns = {}
        level.actors = {"enemy": actor}
        game._level.return_value = level

        with patch('edgecaster.systems.action_runner.get_action') as mock_get:
            action_def = MagicMock()
            action_def.func = MagicMock()
            action_def.cooldown_ticks = 0
            action_def.confirm = MagicMock(return_value=MagicMock())  # Has confirm
            mock_get.return_value = action_def

            with patch('edgecaster.systems.action_runner.action_delay') as mock_delay:
                mock_delay.return_value = 10

                result = run_ai_action(game, "enemy", "attack")

        # Should execute despite confirm being defined
        assert result.executed is True
        action_def.func.assert_called_once()
