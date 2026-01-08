"""
Unit tests for the spawning system (systems/spawning.py).

Tests entity and actor spawning, template loading, and position finding.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import random

from edgecaster.systems.spawning import (
    get_enemy_template_ids,
    get_entity_templates,
    clear_template_caches,
    find_spawn_position,
    find_nearest_walkable,
    spawn_entity_from_template,
    register_actor,
    spawn_entities_near,
    spawn_enemies,
    spawn_imps_near,
    spawn_echoes_near,
    spawn_berries_near,
    spawn_mentor,
    spawn_intro_npcs,
    spawn_npcs,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear template caches before each test."""
    clear_template_caches()
    yield
    clear_template_caches()


class TestGetEnemyTemplateIds:
    """Tests for enemy template loading."""

    def test_returns_list(self):
        """Should return a list of template IDs."""
        game = MagicMock()
        game._enemy_ids_cache = None

        with patch('edgecaster.systems.spawning.yaml') as mock_yaml:
            mock_yaml.safe_load.return_value = [
                {"id": "imp", "faction": "hostile"},
                {"id": "goblin", "faction": "hostile"},
            ]
            with patch('builtins.open', MagicMock()):
                result = get_enemy_template_ids(game)

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_filters_non_hostile(self):
        """Should filter out non-hostile factions."""
        game = MagicMock()
        game._enemy_ids_cache = None

        with patch('edgecaster.systems.spawning.yaml') as mock_yaml:
            mock_yaml.safe_load.return_value = [
                {"id": "imp", "faction": "hostile"},
                {"id": "merchant", "faction": "npc"},
            ]
            with patch('builtins.open', MagicMock()):
                result = get_enemy_template_ids(game)

        assert "imp" in result
        assert "merchant" not in result

    def test_filters_player_only(self):
        """Should filter out player_only templates."""
        game = MagicMock()
        game._enemy_ids_cache = None

        with patch('edgecaster.systems.spawning.yaml') as mock_yaml:
            mock_yaml.safe_load.return_value = [
                {"id": "imp", "faction": "hostile"},
                {"id": "player_ghost", "faction": "hostile", "tags": ["player_only"]},
            ]
            with patch('builtins.open', MagicMock()):
                result = get_enemy_template_ids(game)

        assert "imp" in result
        assert "player_ghost" not in result

    def test_uses_cache(self):
        """Should use cached result on subsequent calls."""
        game = MagicMock()
        game._enemy_ids_cache = ["cached_enemy"]

        result = get_enemy_template_ids(game)

        assert result == ["cached_enemy"]

    def test_fallback_to_imp(self):
        """Should fall back to ['imp'] if no templates found."""
        game = MagicMock()
        game._enemy_ids_cache = None

        with patch('edgecaster.systems.spawning.yaml') as mock_yaml:
            mock_yaml.safe_load.return_value = []
            with patch('builtins.open', MagicMock()):
                result = get_enemy_template_ids(game)

        assert result == ["imp"]


class TestGetEntityTemplates:
    """Tests for entity template loading."""

    def test_returns_dict(self):
        """Should return a dict mapping ID to template."""
        game = MagicMock()
        game._entity_templates_cache = None

        with patch('edgecaster.systems.spawning.yaml') as mock_yaml:
            mock_yaml.safe_load.return_value = [
                {"id": "berry", "name": "Berry"},
                {"id": "rock", "name": "Rock"},
            ]
            with patch('builtins.open', MagicMock()):
                result = get_entity_templates(game)

        assert isinstance(result, dict)
        assert "berry" in result
        assert "rock" in result

    def test_uses_cache(self):
        """Should use cached result."""
        game = MagicMock()
        game._entity_templates_cache = {"cached": {"id": "cached"}}

        result = get_entity_templates(game)

        assert result == {"cached": {"id": "cached"}}


class TestFindSpawnPosition:
    """Tests for spawn position finding."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.rng = random.Random(42)
        game._actor_at.return_value = None
        game._entity_at.return_value = None
        return game

    @pytest.fixture
    def mock_level(self):
        """Create a mock level with a world."""
        level = MagicMock()
        level.world.width = 20
        level.world.height = 20
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True
        return level

    def test_returns_position(self, mock_game, mock_level):
        """Should return a valid position."""
        result = find_spawn_position(mock_game, mock_level)

        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_respects_near_and_radius(self, mock_game, mock_level):
        """Should search near the specified position."""
        result = find_spawn_position(mock_game, mock_level, near=(10, 10), radius=2)

        assert result is not None
        x, y = result
        assert abs(x - 10) <= 2
        assert abs(y - 10) <= 2

    def test_avoids_actors(self, mock_game, mock_level):
        """Should skip tiles with actors."""
        mock_game._actor_at.return_value = MagicMock()  # Actor present

        result = find_spawn_position(mock_game, mock_level, max_attempts=5)

        assert result is None

    def test_returns_none_if_no_valid(self, mock_game, mock_level):
        """Should return None if no valid position found."""
        mock_level.world.is_walkable.return_value = False

        result = find_spawn_position(mock_game, mock_level, max_attempts=5)

        assert result is None


class TestFindNearestWalkable:
    """Tests for nearest walkable tile finding."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game._actor_at.return_value = None
        return game

    @pytest.fixture
    def mock_level(self):
        """Create a mock level."""
        level = MagicMock()
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True
        return level

    def test_returns_origin_if_valid(self, mock_game, mock_level):
        """Should return origin if it's walkable."""
        result = find_nearest_walkable(mock_game, mock_level, (5, 5))

        assert result == (5, 5)

    def test_searches_outward(self, mock_game, mock_level):
        """Should search outward if origin blocked."""
        call_count = [0]

        def mock_walkable(x, y):
            call_count[0] += 1
            return call_count[0] > 1  # First call fails

        mock_level.world.is_walkable.side_effect = mock_walkable

        result = find_nearest_walkable(mock_game, mock_level, (5, 5), max_radius=1)

        assert result is not None

    def test_returns_none_if_blocked(self, mock_game, mock_level):
        """Should return None if all tiles blocked."""
        mock_level.world.is_walkable.return_value = False

        result = find_nearest_walkable(mock_game, mock_level, (5, 5), max_radius=1)

        assert result is None


class TestSpawnEntityFromTemplate:
    """Tests for entity spawning from templates."""

    def test_creates_entity(self):
        """Should create an entity from prototype."""
        game = MagicMock()
        game._new_id.return_value = "ent_1"
        game.rng = random.Random(42)

        with patch('edgecaster.systems.spawning.prototypes') as mock_proto:
            mock_proto.resolve_proto.return_value = {"id": "berry", "name": "Berry"}
            with patch('edgecaster.systems.spawning.spawn_factory') as mock_factory:
                mock_ent = MagicMock()
                mock_ent.tags = {}
                mock_factory.build_entity_from_spec.return_value = mock_ent

                result = spawn_entity_from_template(game, "berry", (5, 5))

        assert result is mock_ent

    def test_raises_on_unknown_template(self):
        """Should raise KeyError for unknown template."""
        game = MagicMock()

        with patch('edgecaster.systems.spawning.prototypes') as mock_proto:
            mock_proto.resolve_proto.return_value = None

            with pytest.raises(KeyError):
                spawn_entity_from_template(game, "nonexistent", (0, 0))

    def test_initializes_charges(self):
        """Should initialize charges from charges_min/max."""
        game = MagicMock()
        game._new_id.return_value = "ent_1"
        game.rng = random.Random(42)
        game.rng.randint = MagicMock(return_value=5)

        with patch('edgecaster.systems.spawning.prototypes') as mock_proto:
            mock_proto.resolve_proto.return_value = {"id": "wand"}
            with patch('edgecaster.systems.spawning.spawn_factory') as mock_factory:
                mock_ent = MagicMock()
                mock_ent.tags = {"charges_min": 3, "charges_max": 7}
                mock_factory.build_entity_from_spec.return_value = mock_ent

                spawn_entity_from_template(game, "wand", (5, 5))

        assert mock_ent.tags["charges"] == 5


class TestRegisterActor:
    """Tests for actor registration."""

    def test_adds_to_dicts(self):
        """Should add actor to actors and entities dicts."""
        game = MagicMock()
        level = MagicMock()
        level.actors = {}
        level.entities = {}

        actor = MagicMock()
        actor.id = "actor_1"

        register_actor(game, level, actor, schedule_ai=False)

        assert "actor_1" in level.actors
        assert "actor_1" in level.entities

    def test_schedules_ai(self):
        """Should schedule AI turn when requested."""
        game = MagicMock()
        level = MagicMock()
        level.actors = {}
        level.entities = {}

        actor = MagicMock()
        actor.id = "actor_1"

        register_actor(game, level, actor, schedule_ai=True)

        game._schedule.assert_called_once()


class TestSpawnEntitiesNear:
    """Tests for generic entity spawning."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.rng = random.Random(42)
        game._actor_at.return_value = None
        game._entity_at.return_value = None
        return game

    @pytest.fixture
    def mock_level(self):
        """Create a mock level."""
        level = MagicMock()
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True
        return level

    def test_spawns_requested_count(self, mock_game, mock_level):
        """Should spawn the requested number of entities."""
        placer = MagicMock()

        result = spawn_entities_near(mock_game, mock_level, (10, 10), 5, placer)

        assert result == 5
        assert placer.call_count == 5

    def test_respects_radius(self, mock_game, mock_level):
        """Should spawn within radius."""
        positions = []

        def capture_pos(pos):
            positions.append(pos)

        spawn_entities_near(mock_game, mock_level, (10, 10), 3, capture_pos, radius=2)

        for x, y in positions:
            assert abs(x - 10) <= 2
            assert abs(y - 10) <= 2


class TestSpawnEnemies:
    """Tests for enemy spawning."""

    def test_spawns_enemies(self):
        """Should spawn the requested number of enemies."""
        game = MagicMock()
        game.rng = random.Random(42)
        game._actor_at.return_value = None
        game._enemy_ids_cache = ["imp"]

        level = MagicMock()
        level.world.width = 20
        level.world.height = 20
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True
        level.actors = {}
        level.entities = {}

        with patch('edgecaster.systems.spawning.enemy_factory') as mock_factory:
            mock_mob = MagicMock()
            mock_mob.id = "mob_1"
            mock_mob.name = "Imp"
            mock_mob.tags = None
            mock_factory.spawn_enemy.return_value = mock_mob

            result = spawn_enemies(game, level, 1)

        assert result >= 1


class TestSpawnMentor:
    """Tests for mentor NPC spawning."""

    def test_spawns_near_entry(self):
        """Should spawn mentor near level entry."""
        game = MagicMock()
        game._new_id.return_value = "npc_1"
        game._actor_at.return_value = None

        level = MagicMock()
        level.world.entry = (10, 10)
        level.world.width = 20
        level.world.height = 20
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True
        level.actors = {}
        level.entities = {}

        result = spawn_mentor(game, level)

        assert result is not None
        assert result.name == "Mentor"
        assert "npc_1" in level.actors

    def test_returns_none_if_no_space(self):
        """Should return None if no valid position."""
        game = MagicMock()
        game._actor_at.return_value = MagicMock()  # Always blocked

        level = MagicMock()
        level.world.entry = (10, 10)
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True

        result = spawn_mentor(game, level)

        assert result is None


class TestSpawnIntroNpcs:
    """Tests for intro NPC spawning."""

    def test_spawns_hexmage_and_cartographer(self):
        """Should spawn both intro NPCs."""
        game = MagicMock()
        game._new_id.side_effect = ["npc_1", "npc_2"]
        game._actor_at.return_value = None

        level = MagicMock()
        level.world.entry = (10, 10)
        level.world.in_bounds.return_value = True
        level.world.is_walkable.return_value = True
        level.actors = {}
        level.entities = {}

        result = spawn_intro_npcs(game, level)

        assert result == 2
        assert len(level.actors) == 2


class TestSpawnNpcs:
    """Tests for generic NPC spawning."""

    def test_spawns_from_npc_defs(self):
        """Should spawn NPCs from NPC_DEFS."""
        game = MagicMock()
        game._new_id.return_value = "npc_1"
        game._actor_at.return_value = None

        level = MagicMock()
        level.world.entry = (10, 10)
        level.world.width = 20
        level.world.height = 20
        level.world.is_walkable.return_value = True
        level.actors = {}
        level.entities = {}

        with patch('edgecaster.systems.spawning.npcs') as mock_npcs:
            mock_npcs.NPC_DEFS = {"test_npc": {"name": "Test NPC"}}

            result = spawn_npcs(game, level, count=1)

        assert result == 1

    def test_zero_count_returns_zero(self):
        """Should return 0 for count=0."""
        game = MagicMock()
        level = MagicMock()

        result = spawn_npcs(game, level, count=0)

        assert result == 0
