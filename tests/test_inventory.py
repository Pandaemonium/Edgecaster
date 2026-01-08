"""
Unit tests for the inventory system (systems/inventory.py).

Tests container logic and item manipulation.
"""

import pytest
from unittest.mock import MagicMock, patch

from edgecaster.systems.inventory import (
    get_inventory,
    get_player_inventory,
    player_pick_up,
    drop_inventory_item,
    eat_item_from_inventory,
    eat_inventory_item,
    take_from_container,
    move_item_between_inventories,
    get_equipped_in_slot,
    unequip_slot,
    unequip_item,
    equip_item_to_slot,
)


class TestGetInventory:
    """Tests for get_inventory."""

    def test_creates_empty_list_if_missing(self):
        """Should create an empty list for unknown owner."""
        game = MagicMock()
        game.inventories = {}

        result = get_inventory(game, "player")

        assert result == []
        assert "player" in game.inventories

    def test_returns_existing_inventory(self):
        """Should return existing inventory list."""
        game = MagicMock()
        item = MagicMock()
        game.inventories = {"player": [item]}

        result = get_inventory(game, "player")

        assert result == [item]

    def test_modifying_returned_list_modifies_inventory(self):
        """Returned list should be the actual inventory (not a copy)."""
        game = MagicMock()
        game.inventories = {}

        inv = get_inventory(game, "player")
        item = MagicMock()
        inv.append(item)

        assert game.inventories["player"] == [item]


class TestGetPlayerInventory:
    """Tests for get_player_inventory."""

    def test_uses_player_id(self):
        """Should use game.player_id for lookup."""
        game = MagicMock()
        game.player_id = "host_123"
        game.inventories = {"host_123": ["sword", "shield"]}

        result = get_player_inventory(game)

        assert result == ["sword", "shield"]

    def test_follows_body_swap(self):
        """Should return new body's inventory after body swap."""
        game = MagicMock()
        game.inventories = {
            "original": ["potion"],
            "new_body": ["dagger"],
        }

        game.player_id = "original"
        assert get_player_inventory(game) == ["potion"]

        game.player_id = "new_body"
        assert get_player_inventory(game) == ["dagger"]


class TestPlayerPickUp:
    """Tests for player_pick_up."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
        game.inventories = {"player": []}
        game.log = MagicMock()

        level = MagicMock()
        player = MagicMock()
        player.pos = (5, 5)
        level.actors = {"player": player}
        level.entities = {}
        game._level.return_value = level
        game._entity_at.return_value = None

        return game

    def test_no_item_logs_message(self, mock_game):
        """Should log message when nothing to pick up."""
        player_pick_up(mock_game)
        mock_game.log.add.assert_called_with("There is nothing here to pick up.")

    def test_currency_auto_absorbed(self, mock_game):
        """Should auto-absorb currency piles."""
        bismuth = MagicMock()
        bismuth.tags = {"currency": "bismuth", "amount": 10}
        bismuth.id = "bismuth_1"
        mock_game._entity_at.return_value = bismuth
        mock_game._level().entities = {"bismuth_1": bismuth}

        player_pick_up(mock_game)

        mock_game.adjust_currency.assert_called_with(10, log=True)
        assert "bismuth_1" not in mock_game._level().entities

    def test_picks_up_item(self, mock_game):
        """Should pick up valid items."""
        # Use spec=[] to prevent MagicMock from reporting hasattr(item, "faction") as True
        item = MagicMock(spec=[])
        item.id = "sword_1"
        item.name = "Sword"
        item.kind = "item"
        item.tags = {}

        # Set up the level with the item
        level = mock_game._level()
        level.entities = {"sword_1": item}
        mock_game._entity_at.return_value = item

        with patch('edgecaster.systems.inventory.item_grants') as mock_grants:
            mock_grants.get_item_grants.return_value = []
            player_pick_up(mock_game)

        assert item in mock_game.inventories["player"]
        assert "sword_1" not in level.entities

    def test_rejects_actors(self, mock_game):
        """Should not pick up actors."""
        enemy = MagicMock()
        enemy.faction = "hostile"
        mock_game._entity_at.return_value = enemy

        player_pick_up(mock_game)

        mock_game.log.add.assert_called_with("You can't pick that up.")


class TestDropInventoryItem:
    """Tests for drop_inventory_item."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with item in inventory."""
        game = MagicMock()
        game.player_id = "player"

        item = MagicMock()
        item.id = "sword_1"
        item.name = "Sword"
        item.tags = {}

        game.inventories = {"player": [item]}
        game.log = MagicMock()

        level = MagicMock()
        player = MagicMock()
        player.pos = (5, 5)
        level.actors = {"player": player}
        level.entities = {}
        game._level.return_value = level

        return game, item

    def test_drops_item_at_player_pos(self, mock_game):
        """Should place dropped item at player position."""
        game, item = mock_game

        with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
            mock_equip.is_equipped.return_value = False
            drop_inventory_item(game, 0)

        assert item.pos == (5, 5)
        assert item.id in game._level().entities
        assert item not in game.inventories["player"]

    def test_invalid_index_does_nothing(self, mock_game):
        """Should silently ignore invalid index."""
        game, _ = mock_game

        drop_inventory_item(game, 99)

        assert len(game.inventories["player"]) == 1

    def test_unequips_before_dropping(self, mock_game):
        """Should unequip item before dropping."""
        game, item = mock_game
        item.tags = {"equipped_slot": "main_hand"}

        with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
            mock_equip.is_equipped.return_value = True
            drop_inventory_item(game, 0)

        # Should have called unequip_item indirectly
        game.refresh_actor_actions.assert_called()


class TestEatItemFromInventory:
    """Tests for eat_item_from_inventory."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with edible item."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        player = MagicMock()
        player.stats = MagicMock()
        player.stats.hp = 5
        player.stats.max_hp = 10
        game._player.return_value = player

        berry = MagicMock()
        berry.tags = {"test_berry": True}
        berry.name = "Blueberry"

        game.inventories = {"player": [berry]}

        return game, berry

    def test_eats_berry_and_heals(self, mock_game):
        """Should consume berry and heal player."""
        game, berry = mock_game

        eat_item_from_inventory(game, "player", 0)

        assert berry not in game.inventories["player"]
        assert game._player().stats.hp == 6

    def test_empty_inventory_logs(self, mock_game):
        """Should log message when inventory empty."""
        game, _ = mock_game
        game.inventories["player"] = []

        eat_item_from_inventory(game, "player", 0)

        game.log.add.assert_called_with("You have nothing to eat.")

    def test_rejects_non_edible(self, mock_game):
        """Should reject non-edible items."""
        game, _ = mock_game
        sword = MagicMock()
        sword.tags = {}
        sword.name = "Sword"
        game.inventories["player"] = [sword]

        eat_item_from_inventory(game, "player", 0)

        assert sword in game.inventories["player"]
        game.log.add.assert_called_with("You can't eat the sword.")


class TestTakeFromContainer:
    """Tests for take_from_container."""

    def test_delegates_to_move_item(self):
        """Should delegate to move_item_between_inventories."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        item = MagicMock()
        item.id = "potion_1"
        item.name = "Potion"
        item.tags = {}

        game.inventories = {"chest": [item], "player": []}

        level = MagicMock()
        level.entities = {}
        level.actors = {}
        game._level.return_value = level

        with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
            mock_equip.is_equipped.return_value = False
            take_from_container(game, "chest", 0)

        # Item should have moved to player
        assert item in game.inventories["player"]
        assert item not in game.inventories["chest"]


class TestMoveItemBetweenInventories:
    """Tests for move_item_between_inventories."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with two inventories."""
        game = MagicMock()
        game.player_id = "player"
        game.log = MagicMock()

        item = MagicMock()
        item.id = "sword_1"
        item.name = "Sword"
        item.tags = {}

        game.inventories = {
            "chest": [item],
            "player": [],
        }

        level = MagicMock()
        level.entities = {}
        level.actors = {}
        game._level.return_value = level

        return game, item

    def test_moves_item(self, mock_game):
        """Should move item between inventories."""
        game, item = mock_game

        with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
            mock_equip.is_equipped.return_value = False
            move_item_between_inventories(game, "chest", 0, "player")

        assert item in game.inventories["player"]
        assert item not in game.inventories["chest"]

    def test_same_inventory_noop(self, mock_game):
        """Should do nothing when source equals destination."""
        game, item = mock_game

        move_item_between_inventories(game, "chest", 0, "chest")

        assert item in game.inventories["chest"]

    def test_self_removal_prevented(self, mock_game):
        """Should prevent recursive self-removal."""
        game, item = mock_game
        # Item is the container itself
        item.id = "chest"
        item.name = "Bag"

        move_item_between_inventories(game, "chest", 0, "player")

        # Item should NOT have moved
        assert item in game.inventories["chest"]
        game.log.add.assert_called_with(
            "You turn the bag inside out, but it remains itself."
        )

    def test_unequips_when_transferring(self, mock_game):
        """Should clear equipped status when moving item."""
        game, item = mock_game
        item.tags = {"equipped_slot": "main_hand"}

        with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
            mock_equip.is_equipped.return_value = True
            move_item_between_inventories(game, "chest", 0, "player")

        assert "equipped_slot" not in item.tags


class TestGetEquippedInSlot:
    """Tests for get_equipped_in_slot."""

    def test_finds_equipped_item(self):
        """Should find item equipped in slot."""
        game = MagicMock()
        sword = MagicMock()
        sword.tags = {"equipped_slot": "main_hand"}

        game.inventories = {"player": [sword]}

        result = get_equipped_in_slot(game, "player", "main_hand")

        assert result is sword

    def test_returns_none_if_empty(self):
        """Should return None if slot is empty."""
        game = MagicMock()
        game.inventories = {"player": []}

        result = get_equipped_in_slot(game, "player", "main_hand")

        assert result is None

    def test_handles_legacy_equipped_tag(self):
        """Should handle legacy 'equipped' tag."""
        game = MagicMock()
        sword = MagicMock()
        sword.tags = {"equipped": "main_hand"}

        game.inventories = {"player": [sword]}

        result = get_equipped_in_slot(game, "player", "main_hand")

        assert result is sword


class TestUnequipSlot:
    """Tests for unequip_slot."""

    def test_clears_equipped_tags(self):
        """Should clear equipped tags from item."""
        game = MagicMock()
        sword = MagicMock()
        sword.tags = {"equipped_slot": "main_hand"}

        game.inventories = {"player": [sword]}

        unequip_slot(game, "player", "main_hand")

        assert "equipped_slot" not in sword.tags

    def test_does_nothing_if_empty(self):
        """Should do nothing if slot empty."""
        game = MagicMock()
        game.inventories = {"player": []}

        unequip_slot(game, "player", "main_hand")

        game.refresh_actor_actions.assert_not_called()


class TestUnequipItem:
    """Tests for unequip_item."""

    def test_clears_equipped_tags_by_id(self):
        """Should clear equipped tags from item by id."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {"equipped_slot": "main_hand"}

        game.inventories = {"player": [sword]}

        unequip_item(game, "player", "sword_1")

        assert "equipped_slot" not in sword.tags
        game.refresh_actor_actions.assert_called_with("player")

    def test_ignores_wrong_item_id(self):
        """Should ignore items with different id."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {"equipped_slot": "main_hand"}

        game.inventories = {"player": [sword]}

        unequip_item(game, "player", "dagger_1")

        assert "equipped_slot" in sword.tags


class TestEquipItemToSlot:
    """Tests for equip_item_to_slot."""

    def test_equips_item(self):
        """Should set equipped_slot tag."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {}

        game.inventories = {"player": [sword]}

        equip_item_to_slot(game, "player", "sword_1", "main_hand")

        assert sword.tags["equipped_slot"] == "main_hand"
        game.refresh_actor_actions.assert_called_with("player")

    def test_replaces_existing_equipped(self):
        """Should unequip existing item in slot first."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {"equipped_slot": "main_hand"}

        dagger = MagicMock()
        dagger.id = "dagger_1"
        dagger.tags = {}

        game.inventories = {"player": [sword, dagger]}

        equip_item_to_slot(game, "player", "dagger_1", "main_hand")

        assert "equipped_slot" not in sword.tags
        assert dagger.tags["equipped_slot"] == "main_hand"

    def test_ignores_wrong_item_id(self):
        """Should do nothing if item id not found."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {}

        game.inventories = {"player": [sword]}

        equip_item_to_slot(game, "player", "nonexistent", "main_hand")

        assert "equipped_slot" not in sword.tags
