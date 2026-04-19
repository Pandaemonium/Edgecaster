"""
Unit tests for the scheduling system (systems/scheduling.py).

Tests event scheduling, time advancement, regen, and cooldown decay.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from edgecaster.state.chakra_component import ChakraComponent, ChakraEdge, ChakraNode
from edgecaster.state.entity_graph import EntityGraphStore

from edgecaster.systems.scheduling import (
    schedule,
    advance_time,
    start_regen,
    chakra_reducer_tick,
    coherence_tick,
    cooldown_tick,
    _tick_frozen_slow,
    _tick_attack_bonus,
    _tick_action_offset,
    slow_mult,
    apply_action_tick_offset,
)


class TestSchedule:
    """Tests for schedule function."""

    def test_adds_event_to_queue(self):
        """Should add scheduled event to level.events."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 100
        level.order = 0

        action = MagicMock()
        schedule(game, level, 50, action)

        assert len(level.events) == 1
        assert level.events[0][0] == 150  # at_tick = current + delay

    def test_increments_order(self):
        """Should increment level.order for FIFO ordering."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.order = 5

        schedule(game, level, 10, MagicMock())

        assert level.order == 6

    def test_multiple_events_ordered(self):
        """Should maintain heap order for multiple events."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.order = 0

        # Schedule events in reverse order
        schedule(game, level, 30, MagicMock())
        schedule(game, level, 10, MagicMock())
        schedule(game, level, 20, MagicMock())

        # Heap property: first element should be soonest
        assert level.events[0][0] == 10


class TestAdvanceTime:
    """Tests for advance_time function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game._player.return_value = MagicMock()
        game._player().stats = MagicMock()
        game._player().stats.coherence = 100
        game.character = MagicMock()
        game.character.stats = {"int": 5}
        game.inventories = {}
        return game

    @pytest.fixture
    def mock_level(self):
        """Create a mock level."""
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.activation_ttl = 0
        level.activation_points = []
        level.need_fov = False
        level.pattern = None
        level.actors = {}
        level.entities = {}
        return level

    def test_advances_current_tick(self, mock_game, mock_level):
        """Should advance level.current_tick by delta."""
        with patch("edgecaster.patterns.motion.step_motion"):
            advance_time(mock_game, mock_level, 50)

        assert mock_level.current_tick == 50

    def test_executes_scheduled_events(self, mock_game, mock_level):
        """Should execute events scheduled before target tick."""
        action = MagicMock()
        mock_level.events = [(25, 1, action)]
        mock_level.order = 1

        with patch("edgecaster.patterns.motion.step_motion"):
            advance_time(mock_game, mock_level, 50)

        action.assert_called_once()

    def test_skips_future_events(self, mock_game, mock_level):
        """Should not execute events after target tick."""
        action = MagicMock()
        mock_level.events = [(100, 1, action)]

        with patch("edgecaster.patterns.motion.step_motion"):
            advance_time(mock_game, mock_level, 50)

        action.assert_not_called()

    def test_decays_activation_ttl(self, mock_game, mock_level):
        """Should decay activation TTL."""
        mock_level.activation_ttl = 100
        mock_level.activation_points = [(1, 2)]

        with patch("edgecaster.patterns.motion.step_motion"):
            advance_time(mock_game, mock_level, 30)

        assert mock_level.activation_ttl == 70

    def test_clears_activation_when_ttl_zero(self, mock_game, mock_level):
        """Should clear activation points when TTL reaches zero."""
        mock_level.activation_ttl = 20
        mock_level.activation_points = [(1, 2), (3, 4)]

        with patch("edgecaster.patterns.motion.step_motion"):
            advance_time(mock_game, mock_level, 30)

        assert mock_level.activation_ttl == 0
        assert mock_level.activation_points == []

    def test_updates_fov_if_needed(self, mock_game, mock_level):
        """Should call _update_fov if level.need_fov is True."""
        mock_level.need_fov = True

        with patch("edgecaster.patterns.motion.step_motion"):
            advance_time(mock_game, mock_level, 10)

        mock_game._update_fov.assert_called_once_with(mock_level)


class TestStartRegen:
    """Tests for start_regen function."""

    def test_schedules_regen_tick(self):
        """Should schedule a regen tick."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.order = 0

        start_regen(game, level, "player", 5, 100)

        assert len(level.events) == 1
        assert level.events[0][0] == 100

    def test_regen_heals_actor(self):
        """Should heal actor when regen ticks."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.order = 0

        actor = MagicMock()
        actor.alive = True
        actor.stats = MagicMock()
        actor.stats.hp = 50
        actor.stats.max_hp = 100
        level.actors = {"player": actor}

        start_regen(game, level, "player", 10, 100)

        # Execute the scheduled tick
        _, _, tick_action = level.events[0]
        tick_action()

        assert actor.stats.hp == 60

    def test_regen_caps_at_max(self):
        """Should not heal beyond max_hp."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.order = 0

        actor = MagicMock()
        actor.alive = True
        actor.stats = MagicMock()
        actor.stats.hp = 95
        actor.stats.max_hp = 100
        level.actors = {"player": actor}

        start_regen(game, level, "player", 10, 100)

        # Execute the scheduled tick
        _, _, tick_action = level.events[0]
        tick_action()

        assert actor.stats.hp == 100

    def test_regen_stops_for_dead_actor(self):
        """Should not heal dead actors."""
        game = MagicMock()
        level = MagicMock()
        level.events = []
        level.current_tick = 0
        level.order = 0

        actor = MagicMock()
        actor.alive = False
        actor.stats = MagicMock()
        actor.stats.hp = 50
        actor.stats.max_hp = 100
        level.actors = {"player": actor}

        start_regen(game, level, "player", 10, 100)

        # Execute the scheduled tick
        _, _, tick_action = level.events[0]
        tick_action()

        # HP should remain unchanged
        assert actor.stats.hp == 50


class TestCoherenceTick:
    """Tests for coherence_tick function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        player = MagicMock()
        player.stats = MagicMock()
        player.stats.coherence = 100
        player.stats.max_coherence = 100
        game._player.return_value = player
        game.character = MagicMock()
        game.character.stats = {"int": 5}
        game.log = MagicMock()
        return game

    def test_no_drain_under_threshold(self, mock_game):
        """Should not drain coherence if vertices under INT*4."""
        level = MagicMock()
        level.pattern = MagicMock()
        level.pattern.vertices = [1, 2, 3]  # 3 < 5*4 = 20

        coherence_tick(mock_game, level, 10)

        assert mock_game._player().stats.coherence == 100

    def test_drains_when_over_threshold(self, mock_game):
        """Should drain coherence when vertices exceed INT*4."""
        level = MagicMock()
        level.pattern = MagicMock()
        level.pattern.vertices = list(range(30))  # 30 > 5*4 = 20, over by 10

        coherence_tick(mock_game, level, 10)

        # Drain = (30 - 20) * 10 / 10.0 = 10
        assert mock_game._player().stats.coherence == 90

    def test_unravels_at_zero(self, mock_game):
        """Should unravel pattern when coherence hits zero."""
        mock_game._player().stats.coherence = 5
        level = MagicMock()
        level.pattern = MagicMock()
        level.pattern.vertices = list(range(30))
        level.pattern_anchor = (5, 5)
        level.activation_points = [(1, 2)]
        level.activation_ttl = 50

        with patch("edgecaster.patterns.builder.Pattern") as mock_pattern:
            mock_pattern.return_value = MagicMock()
            coherence_tick(mock_game, level, 10)

        # Coherence should be restored to max
        assert mock_game._player().stats.coherence == 100
        assert level.pattern_anchor is None
        assert level.activation_points == []
        mock_game.log.add.assert_called_with("Your pattern loses coherence and unravels.")

    def test_skips_when_player_missing_from_level(self, mock_game):
        """Missing player membership should not crash coherence ticking."""
        mock_game.player_id = "player"
        mock_game._player.side_effect = AssertionError("coherence_tick should not call game._player() here")
        level = MagicMock()
        level.actors = {}
        level.entities = {}
        level.pattern = MagicMock()
        level.pattern.vertices = list(range(40))

        coherence_tick(mock_game, level, 10)

        assert mock_game.log.add.call_count == 0


class TestCooldownTick:
    """Tests for cooldown_tick function."""

    def test_decrements_cooldowns(self):
        """Should decrement entity cooldowns by delta."""
        game = MagicMock()
        game.inventories = {}

        actor = MagicMock()
        actor.id = "actor_1"
        actor.cooldowns = {"fireball": 50}
        actor.tags = {}

        level = MagicMock()
        level.actors = {"actor_1": actor}
        level.entities = {}

        cooldown_tick(game, level, 20)

        assert actor.cooldowns["fireball"] == 30

    def test_removes_expired_cooldowns(self):
        """Should remove cooldowns that reach zero."""
        game = MagicMock()
        game.inventories = {}

        actor = MagicMock()
        actor.id = "actor_1"
        actor.cooldowns = {"fireball": 20}
        actor.tags = {}

        level = MagicMock()
        level.actors = {"actor_1": actor}
        level.entities = {}

        cooldown_tick(game, level, 30)

        assert "fireball" not in actor.cooldowns

    def test_ticks_inventory_items(self):
        """Should tick cooldowns on inventory items."""
        game = MagicMock()

        wand = MagicMock()
        wand.id = "wand_1"
        wand.cooldowns = {"zap": 40}

        game.inventories = {"player": [wand]}

        level = MagicMock()
        level.actors = {}
        level.entities = {}

        cooldown_tick(game, level, 15)

        assert wand.cooldowns["zap"] == 25


class TestTickFrozenSlow:
    """Tests for _tick_frozen_slow function."""

    def test_decays_frozen_slow(self):
        """Should decay frozen_slow multiplier over time."""
        actor = MagicMock()
        actor.tags = {"frozen_slow": 2.0, "frozen_slow_timer": 0.0}

        level = MagicMock()
        level.actors = {"actor_1": actor}

        _tick_frozen_slow(level, 15)

        # Timer accumulates, triggers decay step
        assert actor.tags["frozen_slow"] == 1.9

    def test_removes_at_1_0(self):
        """Should remove frozen_slow when it reaches 1.0."""
        actor = MagicMock()
        actor.tags = {"frozen_slow": 1.05, "frozen_slow_timer": 0.0}

        level = MagicMock()
        level.actors = {"actor_1": actor}

        _tick_frozen_slow(level, 10)

        assert "frozen_slow" not in actor.tags
        assert "frozen_slow_timer" not in actor.tags


class TestTickAttackBonus:
    """Tests for _tick_attack_bonus function."""

    def test_decays_attack_bonus(self):
        """Should decay attack bonus ticks."""
        actor = MagicMock()
        actor.tags = {"attack_bonus": 5, "attack_bonus_ticks": 100}

        level = MagicMock()
        level.actors = {"actor_1": actor}

        _tick_attack_bonus(level, 30)

        assert actor.tags["attack_bonus_ticks"] == 70

    def test_removes_expired_bonus(self):
        """Should remove attack bonus when ticks expire."""
        actor = MagicMock()
        actor.tags = {"attack_bonus": 5, "attack_bonus_ticks": 20}

        level = MagicMock()
        level.actors = {"actor_1": actor}

        _tick_attack_bonus(level, 30)

        assert "attack_bonus" not in actor.tags
        assert "attack_bonus_ticks" not in actor.tags


class TestTickActionOffset:
    """Tests for _tick_action_offset function."""

    def test_decays_action_offset(self):
        """Should decay action offset ticks."""
        actor = MagicMock()
        actor.tags = {"action_tick_offset": -5, "action_tick_offset_ticks": 100}

        level = MagicMock()
        level.actors = {"actor_1": actor}

        _tick_action_offset(level, 30)

        assert actor.tags["action_tick_offset_ticks"] == 70

    def test_removes_expired_offset(self):
        """Should remove action offset when ticks expire."""
        actor = MagicMock()
        actor.tags = {"action_tick_offset": -5, "action_tick_offset_ticks": 20}

        level = MagicMock()
        level.actors = {"actor_1": actor}

        _tick_action_offset(level, 30)

        assert "action_tick_offset" not in actor.tags
        assert "action_tick_offset_ticks" not in actor.tags


class TestSlowMult:
    """Tests for slow_mult delegation."""

    def test_delegates_to_action_runner(self):
        """Should delegate to action_runner.slow_mult."""
        actor = MagicMock()

        with patch("edgecaster.systems.action_runner.slow_mult") as mock_slow:
            mock_slow.return_value = 1.5
            result = slow_mult(actor)

        assert result == 1.5
        mock_slow.assert_called_once_with(actor)


class TestApplyActionTickOffset:
    """Tests for apply_action_tick_offset delegation."""

    def test_delegates_to_action_runner(self):
        """Should delegate to action_runner.apply_tick_offset."""
        actor = MagicMock()

        with patch("edgecaster.systems.action_runner.apply_tick_offset") as mock_offset:
            mock_offset.return_value = 45
            result = apply_action_tick_offset(actor, 50)

        assert result == 45
        mock_offset.assert_called_once_with(actor, 50)


def _make_reducer_actor() -> SimpleNamespace:
    chakra_component = ChakraComponent(
        root_node_id="body",
        nodes={
            "body": ChakraNode(node_id="body", active=True, channels={"charge": 0.2}),
            "head": ChakraNode(node_id="head", active=True, channels={"charge": 0.1}),
        },
        edges={
            "body->head": ChakraEdge(
                edge_id="body->head",
                src_node_id="body",
                dst_node_id="head",
                kind="contains",
            )
        },
        tags={},
    )
    return SimpleNamespace(
        id="actor:test:reducer",
        entity_id="actor:test:reducer",
        chakra_component=chakra_component,
    )


class TestChakraReducerTick:
    def test_reduces_dirty_actor_and_marks_graph_clean(self):
        actor = _make_reducer_actor()
        game = SimpleNamespace(entity_graph=EntityGraphStore())
        level = SimpleNamespace(actors={actor.id: actor})
        game.entity_graph.register(actor.entity_id, kind="actor")

        chakra_reducer_tick(game, level)

        assert actor._chakra_effective_channels["head"]["charge"] == pytest.approx(0.3)
        assert game.entity_graph.get_node(actor.entity_id).dirty is False

    def test_skips_clean_cached_actor_until_dirty_again(self):
        actor = _make_reducer_actor()
        game = SimpleNamespace(entity_graph=EntityGraphStore())
        level = SimpleNamespace(actors={actor.id: actor})
        game.entity_graph.register(actor.entity_id, kind="actor")

        chakra_reducer_tick(game, level)
        first_head_charge = actor._chakra_effective_channels["head"]["charge"]

        actor.chakra_component.nodes["head"].channels["charge"] = 0.9
        chakra_reducer_tick(game, level)
        assert actor._chakra_effective_channels["head"]["charge"] == pytest.approx(first_head_charge)

        game.entity_graph.mark_dirty_up(actor.entity_id)
        chakra_reducer_tick(game, level)

        assert actor._chakra_effective_channels["head"]["charge"] == pytest.approx(1.1)
        assert game.entity_graph.get_node(actor.entity_id).dirty is False

    def test_reduces_clean_cached_player_while_pattern_is_active(self):
        actor = _make_reducer_actor()
        game = SimpleNamespace(
            entity_graph=EntityGraphStore(),
            player_id=actor.id,
        )
        level = SimpleNamespace(
            actors={actor.id: actor},
            pattern=None,
        )
        game.entity_graph.register(actor.entity_id, kind="actor")

        chakra_reducer_tick(game, level)
        assert game.entity_graph.get_node(actor.entity_id).dirty is False

        level.pattern = SimpleNamespace(vertices=[(0.0, 0.0)])
        actor.chakra_component.nodes["head"].channels["charge"] = 0.9
        chakra_reducer_tick(game, level)

        assert actor._chakra_effective_channels["head"]["charge"] == pytest.approx(1.1)
        assert game.entity_graph.get_node(actor.entity_id).dirty is False
