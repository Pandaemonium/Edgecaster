"""
Unit tests for the gods system (systems/gods.py and systems/god_abilities.py).

Tests god loading, pattern matching, favor system, invocation, and abilities.
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Dict, Set, Any

from edgecaster.systems.gods import (
    GodDef,
    GodFavorState,
    matching_gods,
    grant_favor,
    get_favor,
    get_favor_tier,
    available_abilities,
    get_active_gods,
    on_kill_trigger,
    on_damage_taken_trigger,
    decay_favor,
    tick_gods,
    load_gods,
    _ensure_favor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_god(
    god_id: str = "test_god",
    name: str = "Test God",
    signature: frozenset[str] | None = None,
    domain: str = "test",
    rival_domains: list[str] | None = None,
    favor_decay_rate: float = 0.5,
    thresholds: dict[str, int] | None = None,
    abilities: list[dict] | None = None,
    triggers: dict[str, int] | None = None,
) -> GodDef:
    return GodDef(
        id=god_id,
        name=name,
        glyph="T",
        color=(255, 255, 255),
        description="A test god.",
        chakra_signature=signature or frozenset({"body", "arm", "hand"}),
        domain=domain,
        rival_domains=rival_domains or [],
        favor_decay_rate=favor_decay_rate,
        favor_thresholds=thresholds or {"noticed": 10, "blessed": 50, "exalted": 100},
        abilities=abilities or [
            {"id": "test_ability", "name": "Test", "min_favor": 10},
        ],
        favor_triggers=triggers or {"kill_hostile": 3, "kill_any": 1},
    )


def _make_game() -> MagicMock:
    game = MagicMock()
    game.god_favor = {}
    game.player_id = "player_1"
    game.log = MagicMock()

    # Mock _level with actors dict
    level = MagicMock()
    level.actors = {}
    level.current_tick = 100
    game._level.return_value = level

    return game


def _make_player(actor_id: str = "player_1") -> MagicMock:
    player = MagicMock()
    player.id = actor_id
    player.alive = True
    player.actions = ("move", "wait")
    player.tags = {}
    player.statuses = {}
    player.stats = MagicMock()
    player.stats.hp = 20
    player.stats.max_hp = 30
    player.stats.mana = 10
    player.stats.max_mana = 20
    player.faction = "player"
    player.pos = (5, 5)
    return player


# ---------------------------------------------------------------------------
# Pattern Matching Tests
# ---------------------------------------------------------------------------

class TestMatchingGods:
    def test_exact_match(self):
        god = _make_god(signature=frozenset({"body", "arm", "hand"}))
        registry = {god.id: god}
        matches = matching_gods(registry, {"body", "arm", "hand"})
        assert len(matches) == 1
        assert matches[0].id == "test_god"

    def test_superset_match(self):
        god = _make_god(signature=frozenset({"body", "arm"}))
        registry = {god.id: god}
        matches = matching_gods(registry, {"body", "arm", "hand", "head"})
        assert len(matches) == 1

    def test_no_match_subset(self):
        god = _make_god(signature=frozenset({"body", "arm", "hand"}))
        registry = {god.id: god}
        matches = matching_gods(registry, {"body", "arm"})
        assert len(matches) == 0

    def test_no_match_disjoint(self):
        god = _make_god(signature=frozenset({"body", "arm", "hand"}))
        registry = {god.id: god}
        matches = matching_gods(registry, {"head", "skull"})
        assert len(matches) == 0

    def test_multiple_matches_sorted_by_specificity(self):
        god_a = _make_god("god_a", signature=frozenset({"body"}))
        god_b = _make_god("god_b", signature=frozenset({"body", "arm"}))
        god_c = _make_god("god_c", signature=frozenset({"body", "arm", "hand"}))
        registry = {g.id: g for g in [god_a, god_b, god_c]}
        matches = matching_gods(registry, {"body", "arm", "hand"})
        assert len(matches) == 3
        # Most specific first
        assert matches[0].id == "god_c"
        assert matches[1].id == "god_b"
        assert matches[2].id == "god_a"

    def test_empty_registry(self):
        matches = matching_gods({}, {"body", "arm"})
        assert len(matches) == 0

    def test_empty_active_chakras(self):
        god = _make_god(signature=frozenset({"body"}))
        registry = {god.id: god}
        matches = matching_gods(registry, set())
        assert len(matches) == 0


# ---------------------------------------------------------------------------
# Favor System Tests
# ---------------------------------------------------------------------------

class TestFavorSystem:
    def test_initial_favor_is_zero(self):
        game = _make_game()
        assert get_favor(game, "test_god") == 0.0

    def test_grant_favor(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            grant_favor(game, "test_god", 15.0)
        assert get_favor(game, "test_god") == 15.0

    def test_grant_favor_tracks_peak(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            grant_favor(game, "test_god", 20.0)
            state = _ensure_favor(game, "test_god")
            assert state.peak_favor == 20.0

    def test_grant_favor_negative_ignored(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            grant_favor(game, "test_god", -5.0)
        assert get_favor(game, "test_god") == 0.0

    def test_favor_decay(self):
        game = _make_game()
        god = _make_god(favor_decay_rate=1.0)  # 1.0 per 100 ticks
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 50.0
            decay_favor(game, 100)  # 100 ticks -> 1.0 decay
            assert state.current_favor == 49.0

    def test_favor_decay_floors_at_zero(self):
        game = _make_game()
        god = _make_god(favor_decay_rate=100.0)
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 0.5
            decay_favor(game, 100)
            assert state.current_favor == 0.0

    def test_jealousy_reduces_rival_favor(self):
        game = _make_game()
        god_a = _make_god("god_a", domain="death", rival_domains=["growth"])
        god_b = _make_god("god_b", domain="growth", rival_domains=["death"])
        registry = {god_a.id: god_a, god_b.id: god_b}
        with patch("edgecaster.systems.gods._god_registry", registry):
            # Give god_b some favor first
            state_b = _ensure_favor(game, "god_b")
            state_b.current_favor = 20.0

            # Granting favor to god_a should reduce god_b's favor
            grant_favor(game, "god_a", 10.0)

            assert get_favor(game, "god_a") == 10.0
            # god_b should lose 10 * 0.3 = 3.0
            assert get_favor(game, "god_b") == 17.0


class TestFavorTiers:
    def test_no_tier_at_zero(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            assert get_favor_tier(game, "test_god") is None

    def test_noticed_tier(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 10.0
            assert get_favor_tier(game, "test_god") == "noticed"

    def test_blessed_tier(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 50.0
            assert get_favor_tier(game, "test_god") == "blessed"

    def test_exalted_tier(self):
        game = _make_game()
        god = _make_god()
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 100.0
            assert get_favor_tier(game, "test_god") == "exalted"


class TestAvailableAbilities:
    def test_no_abilities_at_zero(self):
        game = _make_game()
        god = _make_god(abilities=[
            {"id": "a1", "name": "A1", "min_favor": 10},
        ])
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            assert available_abilities(game, "test_god") == []

    def test_ability_unlocked_at_threshold(self):
        game = _make_game()
        god = _make_god(abilities=[
            {"id": "a1", "name": "A1", "min_favor": 10},
            {"id": "a2", "name": "A2", "min_favor": 50},
        ])
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 15.0
            abilities = available_abilities(game, "test_god")
            assert len(abilities) == 1
            assert abilities[0]["id"] == "a1"

    def test_all_abilities_at_high_favor(self):
        game = _make_game()
        god = _make_god(abilities=[
            {"id": "a1", "name": "A1", "min_favor": 10},
            {"id": "a2", "name": "A2", "min_favor": 50},
        ])
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.current_favor = 100.0
            abilities = available_abilities(game, "test_god")
            assert len(abilities) == 2


# ---------------------------------------------------------------------------
# Pattern-Based Access Tests
# ---------------------------------------------------------------------------

class TestPatternAccess:
    def test_pattern_active_when_chakras_match(self):
        game = _make_game()
        god = _make_god(signature=frozenset({"body", "arm", "hand"}))
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.pattern_active = True
            assert state.pattern_active is True

    def test_pattern_inactive_when_chakras_mismatch(self):
        game = _make_game()
        god = _make_god(signature=frozenset({"body", "arm", "hand"}))
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            assert state.pattern_active is False

    def test_multiple_gods_active_simultaneously(self):
        game = _make_game()
        god_a = _make_god("god_a", signature=frozenset({"body", "arm"}))
        god_b = _make_god("god_b", signature=frozenset({"body", "head"}))
        registry = {god_a.id: god_a, god_b.id: god_b}
        with patch("edgecaster.systems.gods._god_registry", registry):
            state_a = _ensure_favor(game, "god_a")
            state_b = _ensure_favor(game, "god_b")
            state_a.pattern_active = True
            state_b.pattern_active = True
            active = get_active_gods(game)
            assert "god_a" in active
            assert "god_b" in active

    def test_get_active_gods_empty_when_none_active(self):
        game = _make_game()
        assert get_active_gods(game) == []

    def test_abilities_gated_by_favor_with_pattern(self):
        game = _make_game()
        god = _make_god(abilities=[
            {"id": "a1", "name": "A1", "min_favor": 10},
            {"id": "a2", "name": "A2", "min_favor": 50},
        ])
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            state = _ensure_favor(game, "test_god")
            state.pattern_active = True
            state.current_favor = 30.0
            abilities = available_abilities(game, "test_god")
            assert len(abilities) == 1
            assert abilities[0]["id"] == "a1"


# ---------------------------------------------------------------------------
# Kill Trigger Tests
# ---------------------------------------------------------------------------

class TestKillTrigger:
    def test_hostile_kill_grants_favor(self):
        game = _make_game()
        god = _make_god(triggers={"kill_hostile": 3, "kill_any": 1})
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            enemy = MagicMock()
            enemy.faction = "hostile"

            on_kill_trigger(game, enemy)

        # kill_hostile (3) + kill_any (1) = 4
        assert get_favor(game, "test_god") == 4.0

    def test_non_hostile_kill_grants_kill_any(self):
        game = _make_game()
        god = _make_god(triggers={"kill_hostile": 3, "kill_any": 1})
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            neutral = MagicMock()
            neutral.faction = "neutral"

            on_kill_trigger(game, neutral)

        assert get_favor(game, "test_god") == 1.0

    def test_kill_grants_favor_to_all_gods(self):
        from edgecaster.systems.gods import on_kill_trigger
        game = _make_game()
        god_a = _make_god("god_a", triggers={"kill_hostile": 3})
        god_b = _make_god("god_b", triggers={"kill_hostile": 1, "kill_any": 2})
        registry = {"god_a": god_a, "god_b": god_b}
        with patch("edgecaster.systems.gods._god_registry", registry):
            enemy = MagicMock()
            enemy.faction = "hostile"
            on_kill_trigger(game, enemy)

        assert get_favor(game, "god_a") == 3.0
        assert get_favor(game, "god_b") == 3.0  # 1 + 2

    def test_god_without_kill_trigger_gets_nothing(self):
        game = _make_game()
        god = _make_god(triggers={"explore_new_tile": 1})
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            enemy = MagicMock()
            enemy.faction = "hostile"
            on_kill_trigger(game, enemy)

        assert get_favor(game, "test_god") == 0.0


class TestDamageTakenTrigger:
    def test_damage_taken_grants_favor(self):
        game = _make_game()
        god = _make_god(triggers={"take_damage": 2})
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            on_damage_taken_trigger(game)

        assert get_favor(game, "test_god") == 2.0


class TestExploreTrigger:
    def test_explore_grants_favor(self):
        from edgecaster.systems.gods import on_explore_trigger
        game = _make_game()
        god = _make_god(triggers={"explore_new_tile": 1})
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            on_explore_trigger(game)

        assert get_favor(game, "test_god") == 1.0

    def test_explore_no_effect_without_trigger(self):
        from edgecaster.systems.gods import on_explore_trigger
        game = _make_game()
        god = _make_god(triggers={"kill_hostile": 3})
        with patch("edgecaster.systems.gods._god_registry", {god.id: god}):
            on_explore_trigger(game)

        assert get_favor(game, "test_god") == 0.0


# ---------------------------------------------------------------------------
# YAML Loading Tests
# ---------------------------------------------------------------------------

class TestLoadGods:
    def test_load_gods_returns_dict(self):
        registry = load_gods()
        # Should load from the actual gods.yaml
        assert isinstance(registry, dict)
        assert "dark_knife" in registry
        assert "verdant_mother" in registry
        assert "hollow_eye" in registry
        assert "iron_spine" in registry

    def test_dark_knife_signature(self):
        registry = load_gods()
        dk = registry["dark_knife"]
        assert dk.chakra_signature == frozenset({"body", "arm", "arm.hand"})
        assert dk.domain == "death"
        assert "growth" in dk.rival_domains

    def test_god_abilities_loaded(self):
        registry = load_gods()
        dk = registry["dark_knife"]
        ability_ids = [a["id"] for a in dk.abilities]
        assert "knife_rune" in ability_ids
        assert "reaper_mark" in ability_ids

    def test_favor_triggers_loaded(self):
        registry = load_gods()
        dk = registry["dark_knife"]
        assert dk.favor_triggers.get("kill_hostile") == 3


# ---------------------------------------------------------------------------
# God Abilities Tests
# ---------------------------------------------------------------------------

def _make_pattern_with_vertices(chakra_nodes, anchor=(0, 0)):
    """Create a mock pattern with vertices tagged with chakra_node IDs."""
    vertices = []
    for i, (node_id, pos) in enumerate(chakra_nodes):
        v = MagicMock()
        v.pos = pos
        v.tags = {"chakra_node": node_id}
        vertices.append(v)
    pattern = MagicMock()
    pattern.vertices = vertices
    return pattern


class TestKnifeRune:
    def test_knife_rune_damages_nearby_enemy(self):
        from edgecaster.systems.god_abilities import act_knife_rune

        game = _make_game()
        player = _make_player()
        game._level.return_value.actors = {"player_1": player}

        # Set up pattern with Dark Knife vertices at (5, 5)
        game.pattern = _make_pattern_with_vertices([
            ("body", (0, 0)),
            ("arm", (1, 0)),
            ("arm.hand", (2, 0)),
        ])
        game.pattern_anchor = (5, 5)

        # Enemy at (6, 5) — distance 1 from arm vertex at (6, 5)
        enemy = MagicMock()
        enemy.alive = True
        enemy.faction = "hostile"
        enemy.pos = (6, 5)
        enemy.stats = MagicMock()
        enemy.stats.hp = 50
        enemy.stats.max_hp = 50
        enemy.name = "Imp"
        game._level.return_value.actors["enemy_1"] = enemy

        god = _make_god("dark_knife", signature=frozenset({"body", "arm", "arm.hand"}))
        with patch("edgecaster.systems.gods._god_registry", {"dark_knife": god}):
            state = _ensure_favor(game, "dark_knife")
            state.pattern_active = True
            state.current_favor = 20.0

            act_knife_rune(game, "player_1")

        # Enemy should have taken damage
        assert enemy.stats.hp < 50

    def test_knife_rune_closer_means_more_damage(self):
        from edgecaster.systems.god_abilities import act_knife_rune

        # Test two enemies at different distances
        game1 = _make_game()
        player1 = _make_player()
        game1._level.return_value.actors = {"player_1": player1}
        game1.pattern = _make_pattern_with_vertices([("arm", (0, 0))])
        game1.pattern_anchor = (5, 5)

        close_enemy = MagicMock()
        close_enemy.alive = True
        close_enemy.faction = "hostile"
        close_enemy.pos = (6, 5)  # dist 1 from (5,5)
        close_enemy.stats = MagicMock()
        close_enemy.stats.hp = 100
        close_enemy.stats.max_hp = 100
        close_enemy.name = "Close"
        game1._level.return_value.actors["e1"] = close_enemy

        game2 = _make_game()
        player2 = _make_player()
        game2._level.return_value.actors = {"player_1": player2}
        game2.pattern = _make_pattern_with_vertices([("arm", (0, 0))])
        game2.pattern_anchor = (5, 5)

        far_enemy = MagicMock()
        far_enemy.alive = True
        far_enemy.faction = "hostile"
        far_enemy.pos = (9, 5)  # dist 4 from (5,5)
        far_enemy.stats = MagicMock()
        far_enemy.stats.hp = 100
        far_enemy.stats.max_hp = 100
        far_enemy.name = "Far"
        game2._level.return_value.actors["e1"] = far_enemy

        god = _make_god("dark_knife", signature=frozenset({"body", "arm", "arm.hand"}))

        with patch("edgecaster.systems.gods._god_registry", {"dark_knife": god}):
            state1 = _ensure_favor(game1, "dark_knife")
            state1.pattern_active = True
            state1.current_favor = 20.0
            act_knife_rune(game1, "player_1")

            state2 = _ensure_favor(game2, "dark_knife")
            state2.pattern_active = True
            state2.current_favor = 20.0
            act_knife_rune(game2, "player_1")

        close_dmg = 100 - close_enemy.stats.hp
        far_dmg = 100 - far_enemy.stats.hp
        assert close_dmg > far_dmg

    def test_knife_rune_execute_threshold(self):
        from edgecaster.systems.god_abilities import act_knife_rune

        game = _make_game()
        player = _make_player()
        game._level.return_value.actors = {"player_1": player}
        game.pattern = _make_pattern_with_vertices([("body", (0, 0))])
        game.pattern_anchor = (5, 5)

        # Enemy at distance 4 (near edge of range) so hit damage is low,
        # but remaining HP after hit should be below execute threshold.
        # At 0 favor: base_damage=5, dist=4/5 -> dmg=max(1,int(5*0.2))=1
        # execute_hp = 5 + int(0*0.35) = 5
        # Enemy at 6 HP takes 1 dmg -> 5 HP remaining, which is NOT < 5
        # Enemy at 5 HP takes 1 dmg -> 4 HP remaining, which IS < 5 -> executed
        enemy = MagicMock()
        enemy.alive = True
        enemy.faction = "hostile"
        enemy.pos = (9, 5)  # distance 4 from vertex at (5,5)
        enemy.stats = MagicMock()
        enemy.stats.hp = 5
        enemy.stats.max_hp = 100
        enemy.name = "Weakling"
        game._level.return_value.actors["e1"] = enemy

        god = _make_god("dark_knife", signature=frozenset({"body", "arm", "arm.hand"}))
        with patch("edgecaster.systems.gods._god_registry", {"dark_knife": god}):
            state = _ensure_favor(game, "dark_knife")
            state.pattern_active = True
            state.current_favor = 0.0  # execute_hp = 5

            act_knife_rune(game, "player_1")

        # After 1 dmg: 4 HP remaining < execute_hp (5) -> executed
        assert enemy.stats.hp <= 0
        assert enemy.alive is False

    def test_knife_rune_execute_scales_with_favor(self):
        from edgecaster.systems.god_abilities import act_knife_rune

        game = _make_game()
        player = _make_player()
        game._level.return_value.actors = {"player_1": player}
        game.pattern = _make_pattern_with_vertices([("body", (0, 0))])
        game.pattern_anchor = (5, 5)

        # At 100 favor: execute_hp = 5 + int(100*0.35) = 40
        # Enemy far away (dist 4), base_damage=max(2,int(5+100*0.15))=20
        # dmg = max(1, int(20 * (1 - 4/5))) = 3 (float truncation)
        # Enemy at 42 HP takes 3 -> 39 HP remaining, which IS < 40 -> executed
        enemy = MagicMock()
        enemy.alive = True
        enemy.faction = "hostile"
        enemy.pos = (9, 5)
        enemy.stats = MagicMock()
        enemy.stats.hp = 42
        enemy.stats.max_hp = 100
        enemy.name = "Tough"
        game._level.return_value.actors["e1"] = enemy

        god = _make_god("dark_knife", signature=frozenset({"body", "arm", "arm.hand"}))
        with patch("edgecaster.systems.gods._god_registry", {"dark_knife": god}):
            state = _ensure_favor(game, "dark_knife")
            state.pattern_active = True
            state.current_favor = 100.0

            act_knife_rune(game, "player_1")

        assert enemy.stats.hp <= 0
        assert enemy.alive is False

    def test_knife_rune_no_pattern_active(self):
        from edgecaster.systems.god_abilities import act_knife_rune

        game = _make_game()
        player = _make_player()
        game._level.return_value.actors = {"player_1": player}

        god = _make_god("dark_knife", signature=frozenset({"body", "arm", "arm.hand"}))
        with patch("edgecaster.systems.gods._god_registry", {"dark_knife": god}):
            state = _ensure_favor(game, "dark_knife")
            state.pattern_active = False

            act_knife_rune(game, "player_1")

        game.log.add.assert_called_with("The Dark Knife's symbol is not active.")

    def test_knife_rune_out_of_range_no_damage(self):
        from edgecaster.systems.god_abilities import act_knife_rune

        game = _make_game()
        player = _make_player()
        game._level.return_value.actors = {"player_1": player}
        game.pattern = _make_pattern_with_vertices([("body", (0, 0))])
        game.pattern_anchor = (5, 5)

        # Enemy far away (distance 10 > max_range 5)
        enemy = MagicMock()
        enemy.alive = True
        enemy.faction = "hostile"
        enemy.pos = (15, 5)
        enemy.stats = MagicMock()
        enemy.stats.hp = 50
        enemy.stats.max_hp = 50
        enemy.name = "Distant"
        game._level.return_value.actors["e1"] = enemy

        god = _make_god("dark_knife", signature=frozenset({"body", "arm", "arm.hand"}))
        with patch("edgecaster.systems.gods._god_registry", {"dark_knife": god}):
            state = _ensure_favor(game, "dark_knife")
            state.pattern_active = True
            state.current_favor = 20.0

            act_knife_rune(game, "player_1")

        # Enemy should not have taken damage
        assert enemy.stats.hp == 50


class TestVerdantMend:
    def test_verdant_mend_heals(self):
        from edgecaster.systems.god_abilities import act_verdant_mend

        game = _make_game()
        player = _make_player()
        player.stats.hp = 10
        player.stats.max_hp = 30
        game._level.return_value.actors = {"player_1": player}

        god = _make_god("verdant_mother", domain="growth",
                        signature=frozenset({"body", "torso", "chest"}))
        with patch("edgecaster.systems.gods._god_registry", {"verdant_mother": god}):
            state = _ensure_favor(game, "verdant_mother")
            state.pattern_active = True
            state.current_favor = 20.0

            act_verdant_mend(game, "player_1")

        # heal = max(1, int(30 * 0.15 + 20 * 0.1)) = max(1, int(4.5 + 2.0)) = 6
        assert player.stats.hp == 16


class TestUnbreakable:
    def test_unbreakable_check_survives_lethal(self):
        from edgecaster.systems.god_abilities import unbreakable_check

        game = _make_game()
        player = _make_player()
        player.stats.hp = 5
        player.statuses["unbreakable"] = 999

        result = unbreakable_check(game, player, 10)

        # Should survive with 1 HP, so only 4 damage applied
        assert result == 4
        assert "unbreakable" not in player.statuses

    def test_unbreakable_check_no_effect_non_lethal(self):
        from edgecaster.systems.god_abilities import unbreakable_check

        game = _make_game()
        player = _make_player()
        player.stats.hp = 20
        player.statuses["unbreakable"] = 999

        result = unbreakable_check(game, player, 5)

        # Non-lethal, unbreakable not consumed
        assert result == 5
        assert "unbreakable" in player.statuses

    def test_unbreakable_check_no_status(self):
        from edgecaster.systems.god_abilities import unbreakable_check

        game = _make_game()
        player = _make_player()
        player.stats.hp = 5

        result = unbreakable_check(game, player, 10)
        assert result == 10


class TestGodIronSkin:
    def test_god_iron_skin_adds_defense(self):
        from edgecaster.systems.god_abilities import act_god_iron_skin

        game = _make_game()
        player = _make_player()
        player.tags["base_defense"] = 1
        game._level.return_value.actors = {"player_1": player}

        god = _make_god("iron_spine", domain="fortification",
                        signature=frozenset({"body", "torso", "back"}))
        with patch("edgecaster.systems.gods._god_registry", {"iron_spine": god}):
            state = _ensure_favor(game, "iron_spine")
            state.pattern_active = True
            state.current_favor = 50.0

            act_god_iron_skin(game, "player_1")

        # bonus = 2 + floor(50 * 0.02) = 2 + 1 = 3
        assert player.tags["base_defense"] == 4  # 1 + 3
        assert "god_iron_skin" in player.statuses

    def test_god_iron_skin_expire_removes_defense(self):
        from edgecaster.systems.god_abilities import god_iron_skin_expire

        player = _make_player()
        player.tags["base_defense"] = 5
        player.tags["_god_iron_skin_bonus"] = 3

        game = _make_game()
        god_iron_skin_expire(game, player)

        assert player.tags["base_defense"] == 2
        assert "_god_iron_skin_bonus" not in player.tags
