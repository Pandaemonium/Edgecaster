"""
Unit tests for the combat system (systems/combat.py).

Tests damage calculation, attack resolution, and death handling.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


from edgecaster.systems.combat import (
    is_hostile,
    _has_class_tag,
    calculate_damage,
    apply_xp_drain,
    apply_damage,
    apply_lifesteal,
    handle_player_death,
    spawn_currency_drop,
    attack,
    kill_actor,
    _free_chained_brutes,
    _spawn_item_drops,
)


class TestIsHostile:
    """Tests for is_hostile function."""

    def test_delegates_to_reputation_system(self):
        """Should delegate to reputation_system.is_hostile."""
        game = MagicMock()
        attacker = MagicMock()
        target = MagicMock()

        with patch("edgecaster.systems.reputation.is_hostile") as mock_rep:
            mock_rep.return_value = True
            result = is_hostile(game, attacker, target)

        assert result is True
        mock_rep.assert_called_once_with(game, attacker, target)


class TestHasClassTag:
    """Tests for _has_class_tag helper."""

    def test_finds_tag_in_dict(self):
        """Should find tag in nested tags dict."""
        actor = MagicMock()
        actor.tags = {"tags": {"xp_drain": True}}

        result = _has_class_tag(actor, "xp_drain")

        assert result is True

    def test_finds_tag_in_list(self):
        """Should find tag in nested tags list."""
        actor = MagicMock()
        actor.tags = {"tags": ["xp_drain", "other"]}

        result = _has_class_tag(actor, "xp_drain")

        assert result is True

    def test_returns_false_for_missing_tag(self):
        """Should return False for missing tag."""
        actor = MagicMock()
        actor.tags = {"tags": {"other": True}}

        result = _has_class_tag(actor, "xp_drain")

        assert result is False

    def test_handles_missing_tags(self):
        """Should handle actors without tags."""
        actor = MagicMock(spec=[])

        result = _has_class_tag(actor, "xp_drain")

        assert result is False


class TestCalculateDamage:
    """Tests for calculate_damage function."""

    def test_basic_damage(self):
        """Should calculate damage as attack - defense."""
        attacker = MagicMock()
        attacker.tags = {"base_attack": 5}

        defender = MagicMock()
        defender.tags = {"base_defense": 2}

        result = calculate_damage(attacker, defender)

        assert result == 3

    def test_minimum_damage_is_one(self):
        """Should return at least 1 damage."""
        attacker = MagicMock()
        attacker.tags = {"base_attack": 1}

        defender = MagicMock()
        defender.tags = {"base_defense": 10}

        result = calculate_damage(attacker, defender)

        assert result == 1

    def test_includes_attack_bonus(self):
        """Should include attack_bonus in damage."""
        attacker = MagicMock()
        attacker.tags = {"base_attack": 3, "attack_bonus": 2}

        defender = MagicMock()
        defender.tags = {"base_defense": 1}

        result = calculate_damage(attacker, defender)

        assert result == 4  # (3 + 2) - 1

    def test_defaults_to_one_attack(self):
        """Should default base_attack to 1."""
        attacker = MagicMock()
        attacker.tags = {}

        defender = MagicMock()
        defender.tags = {}

        result = calculate_damage(attacker, defender)

        assert result == 1


class TestApplyXpDrain:
    """Tests for apply_xp_drain function."""

    def test_drains_xp_from_player(self):
        """Should drain XP when attacker has xp_drain tag."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        attacker = MagicMock()
        attacker.name = "Shadow"
        attacker.tags = {"tags": {"xp_drain": True}, "base_attack": 5}

        defender = MagicMock()
        defender.id = "player"
        defender.stats = MagicMock()
        defender.stats.xp = 100

        result = apply_xp_drain(game, attacker, defender)

        assert result is True
        assert defender.stats.xp == 95
        game.log.add.assert_called()

    def test_returns_false_for_non_player(self):
        """Should return False if defender is not player."""
        game = MagicMock()
        game.player_id = "player"

        attacker = MagicMock()
        attacker.tags = {"tags": {"xp_drain": True}}

        defender = MagicMock()
        defender.id = "enemy"

        result = apply_xp_drain(game, attacker, defender)

        assert result is False

    def test_returns_false_without_tag(self):
        """Should return False if attacker lacks xp_drain tag."""
        game = MagicMock()
        game.player_id = "player"

        attacker = MagicMock()
        attacker.tags = {}

        defender = MagicMock()
        defender.id = "player"

        result = apply_xp_drain(game, attacker, defender)

        assert result is False


class TestApplyDamage:
    """Tests for apply_damage function."""

    def test_reduces_defender_hp(self):
        """Should reduce defender HP by damage amount."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        attacker = MagicMock()
        attacker.id = "enemy"
        attacker.name = "Imp"

        defender = MagicMock()
        defender.id = "player"
        defender.stats = MagicMock()
        defender.stats.hp = 50

        apply_damage(game, attacker, defender, 10)

        assert defender.stats.hp == 40
        defender.stats.clamp.assert_called_once()

    def test_logs_player_attack(self):
        """Should log 'You hit' when player attacks."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        attacker = MagicMock()
        attacker.id = "player"

        defender = MagicMock()
        defender.id = "enemy"
        defender.name = "Goblin"
        defender.stats = MagicMock()
        defender.stats.hp = 50

        apply_damage(game, attacker, defender, 5)

        game.log.add.assert_called_with("You hit Goblin for 5.")

    def test_logs_enemy_attack(self):
        """Should log attacker name when enemy attacks player."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        attacker = MagicMock()
        attacker.id = "enemy"
        attacker.name = "Troll"

        defender = MagicMock()
        defender.id = "player"
        defender.stats = MagicMock()
        defender.stats.hp = 50

        apply_damage(game, attacker, defender, 8)

        game.log.add.assert_called_with("Troll hits you for 8.")


class TestApplyLifesteal:
    """Tests for apply_lifesteal function."""

    def test_heals_attacker_on_lifesteal(self):
        """Should heal attacker based on lifesteal_frac."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        attacker = MagicMock()
        attacker.id = "vampire"
        attacker.name = "Vampire"
        attacker.tags = {"tags": {"lifesteal_frac": 0.5}}
        attacker.stats = MagicMock()
        attacker.stats.hp = 40
        attacker.stats.max_hp = 100

        defender = MagicMock()
        defender.id = "player"

        apply_lifesteal(game, attacker, defender, 10)

        assert attacker.stats.hp == 45  # 40 + ceil(10 * 0.5)

    def test_blood_sipper_uses_default_lifesteal(self):
        """Should use 0.5 lifesteal for blood_sipper tag."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        attacker = MagicMock()
        attacker.id = "sipper"
        attacker.name = "Blood Sipper"
        attacker.tags = {"tags": {"blood_sipper": True}}
        attacker.stats = MagicMock()
        attacker.stats.hp = 30
        attacker.stats.max_hp = 100

        defender = MagicMock()
        defender.id = "player"

        apply_lifesteal(game, attacker, defender, 6)

        assert attacker.stats.hp == 33  # 30 + ceil(6 * 0.5)

    def test_no_lifesteal_without_tag(self):
        """Should not heal without lifesteal tag."""
        game = MagicMock()
        game.player_id = "player"

        attacker = MagicMock()
        attacker.id = "imp"
        attacker.tags = {}
        attacker.stats = MagicMock()
        attacker.stats.hp = 30

        defender = MagicMock()
        defender.id = "player"

        apply_lifesteal(game, attacker, defender, 10)

        assert attacker.stats.hp == 30  # unchanged


class TestHandlePlayerDeath:
    """Tests for handle_player_death function."""

    def test_sets_urgent_message(self):
        """Should set urgent death message."""
        game = MagicMock()

        attacker = MagicMock()
        attacker.name = "Dragon"

        handle_player_death(game, attacker)

        game.set_urgent.assert_called_once_with(
            "This world, now doomed, spirals infinitely toward decay and despair...",
            title="You die, by way of Dragon.",
            choices=["Continue..."],
        )


class TestSpawnCurrencyDrop:
    """Tests for spawn_currency_drop function."""

    def test_spawns_currency_pile(self):
        """Should spawn bismuth pile on walkable tile."""
        game = MagicMock()
        game.rng = MagicMock()
        game.rng.randint.return_value = 15

        level = MagicMock()
        level.world.is_walkable.return_value = True
        level.entities = {}

        defender = MagicMock()
        defender.pos = (5, 5)
        defender.tags = {"currency_min": 10, "currency_max": 20}

        mock_ent = MagicMock()
        mock_ent.id = "pile_1"
        game._spawn_entity_from_template.return_value = mock_ent

        spawn_currency_drop(game, level, defender)

        game._spawn_entity_from_template.assert_called_once()
        assert "pile_1" in level.entities

    def test_reads_nested_currency_tags(self):
        """Should read currency tags from nested tags dict."""
        game = MagicMock()
        game.rng = MagicMock()
        game.rng.randint.return_value = 5

        level = MagicMock()
        level.world.is_walkable.return_value = True
        level.entities = {}

        defender = MagicMock()
        defender.pos = (5, 5)
        defender.tags = {"tags": {"currency_min": 3, "currency_max": 7}}

        mock_ent = MagicMock()
        mock_ent.id = "pile_2"
        game._spawn_entity_from_template.return_value = mock_ent

        spawn_currency_drop(game, level, defender)

        game._spawn_entity_from_template.assert_called_once()


class TestAttack:
    """Tests for main attack function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()
        game.rng = MagicMock()
        return game

    def test_applies_damage_and_death(self, mock_game):
        """Should apply damage and handle death."""
        level = MagicMock()
        level.world.is_walkable.return_value = True
        level.entities = {}

        attacker = MagicMock()
        attacker.id = "player"
        attacker.name = "Hero"
        attacker.tags = {"base_attack": 10}

        defender = MagicMock()
        defender.id = "enemy"
        defender.name = "Goblin"
        defender.tags = {}
        defender.stats = MagicMock()
        defender.stats.hp = 5
        defender.pos = (3, 3)

        attack(mock_game, level, attacker, defender)

        # Should have dealt lethal damage
        assert defender.stats.hp < 5
        mock_game.log.add.assert_called()

    def test_xp_drain_skips_damage(self, mock_game):
        """Should skip normal damage for XP drain attacks."""
        level = MagicMock()

        attacker = MagicMock()
        attacker.id = "shadow"
        attacker.name = "Shadow"
        attacker.tags = {"tags": {"xp_drain": True}, "base_attack": 5}

        defender = MagicMock()
        defender.id = "player"
        defender.name = "Hero"
        defender.stats = MagicMock()
        defender.stats.hp = 100
        defender.stats.xp = 50

        attack(mock_game, level, attacker, defender)

        # HP should be unchanged
        assert defender.stats.hp == 100
        # XP should be drained
        assert defender.stats.xp < 50


class TestKillActor:
    """Tests for kill_actor function."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()
        return game

    def test_removes_from_level(self, mock_game):
        """Should remove actor from level dicts."""
        level = MagicMock()
        level.actors = {"enemy_1": MagicMock()}
        level.entities = {"enemy_1": MagicMock()}

        actor = MagicMock()
        actor.id = "enemy_1"
        actor.pos = (5, 5)
        actor.proto_id = None
        actor.tags = {}

        with patch("edgecaster.systems.reputation.apply_rep_event"):
            kill_actor(mock_game, level, actor, killer_id="player", killer_is_player=True)

        assert "enemy_1" not in level.actors
        assert "enemy_1" not in level.entities

    def test_awards_xp(self, mock_game):
        """Should call _on_enemy_killed for XP."""
        level = MagicMock()
        level.actors = {"enemy_1": MagicMock()}
        level.entities = {}

        actor = MagicMock()
        actor.id = "enemy_1"
        actor.pos = (5, 5)
        actor.proto_id = None
        actor.tags = {}

        with patch("edgecaster.systems.reputation.apply_rep_event"):
            kill_actor(mock_game, level, actor)

        mock_game._on_enemy_killed.assert_called_once_with(actor)

    def test_marks_lineage_actor_dead(self, mock_game):
        """Should persist death for deterministic lineage actors."""
        level = MagicMock()
        level.actors = {"enemy_1": MagicMock()}
        level.entities = {"enemy_1": MagicMock()}

        actor = MagicMock()
        actor.id = "enemy_1"
        actor.pos = (5, 5)
        actor.proto_id = None
        actor.tags = {"lineage_id": "site:a/npc:b"}
        actor.stats = MagicMock()
        actor.stats.hp = 0

        with patch("edgecaster.systems.reputation.apply_rep_event"):
            kill_actor(mock_game, level, actor)

        mock_game.mark_actor_dead.assert_called_once_with(actor, reason="killed")


class TestFreeChainedBrutes:
    """Tests for _free_chained_brutes function."""

    def test_frees_brutes_on_slaver_death(self):
        """Should free brutes chained to a slaver."""
        game = MagicMock()
        game.log = MagicMock()

        brute = MagicMock()
        brute.tags = {"slaver_master_id": "slaver_1"}

        level = MagicMock()
        level.actors = {"brute_1": brute}

        _free_chained_brutes(game, level, "slaver_1", "slaver")

        assert brute.faction == "neutral"
        assert brute.tags.get("freed_from_slaver") is True
        game.log.add.assert_called()

    def test_ignores_non_slavers(self):
        """Should do nothing for non-slaver deaths."""
        game = MagicMock()
        game.log = MagicMock()

        level = MagicMock()
        level.actors = {}

        _free_chained_brutes(game, level, "imp_1", "imp")

        game.log.add.assert_not_called()


class TestSpawnItemDrops:
    """Tests for _spawn_item_drops function."""

    def test_spawns_drop_items(self):
        """Should spawn items from drop_items list."""
        game = MagicMock()
        game.log = MagicMock()

        level = MagicMock()
        level.entities = {}

        actor = MagicMock()
        actor.name = "Boss"

        mock_ent = MagicMock()
        mock_ent.id = "item_1"
        mock_ent.name = "Cool Sword"
        game._spawn_entity_from_template.return_value = mock_ent

        with patch("edgecaster.prototypes.resolve_proto") as mock_proto:
            mock_proto.return_value = {"drop_items": ["cool_sword"]}
            _spawn_item_drops(game, level, actor, "boss", (5, 5))

        game._spawn_entity_from_template.assert_called_once_with("cool_sword", (5, 5))
        assert "item_1" in level.entities
