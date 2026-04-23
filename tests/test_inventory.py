"""
Unit tests for the inventory system (systems/inventory.py).

Tests container logic and item manipulation.
"""

import pytest
from unittest.mock import MagicMock, patch

from edgecaster.state.entities import Entity
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems.inventory import (
    add_inventory_item,
    get_inventory,
    get_player_inventory,
    player_pick_up,
    drop_inventory_item,
    drop_inventory_item_qty,
    eat_item_from_inventory,
    eat_inventory_item,
    take_from_container,
    move_item_between_inventories,
    move_item_between_inventories_qty,
    get_equipped_in_slot,
    remove_inventory_item_at,
    unequip_slot,
    unequip_item,
    equip_item_to_slot,
)


class TestGetInventory:
    """Tests for get_inventory."""

    def test_returns_empty_list_if_missing(self):
        """Should return an empty list for unknown owner."""
        game = MagicMock()
        game.entity_graph = None

        result = get_inventory(game, "player")

        assert result == []

    def test_returns_graph_resolved_inventory(self):
        """Should return the graph-resolved inventory list."""
        game = MagicMock()
        item = MagicMock()
        item.id = "item_1"

        class _Graph:
            def get_children(self, owner_id, socket_id=None):
                return ["item_1"]

        game.entity_graph = _Graph()
        with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=item):
            result = get_inventory(game, "player")

        assert result == [item]


class TestGetPlayerInventory:
    """Tests for get_player_inventory."""

    def test_uses_player_id(self):
        """Should use game.player_id for lookup."""
        game = MagicMock()
        game.player_id = "host_123"

        with patch("edgecaster.systems.inventory.get_inventory", return_value=["sword", "shield"]) as mock_get:
            result = get_player_inventory(game)
            mock_get.assert_called_once_with(game, "host_123")

        assert result == ["sword", "shield"]

    def test_follows_body_swap(self):
        """Should return new body's inventory after body swap."""
        game = MagicMock()
        def _mock_get_inv(g, oid):
            if oid == "original": return ["potion"]
            if oid == "new_body": return ["dagger"]
            return []

        with patch("edgecaster.systems.inventory.get_inventory", side_effect=_mock_get_inv):
            game.player_id = "original"
            assert get_player_inventory(game) == ["potion"]

            game.player_id = "new_body"
            assert get_player_inventory(game) == ["dagger"]


class TestGraphBackedInventoryHelpers:
    """Tests for the graph-first add/remove helper layer."""

    def test_add_inventory_item_syncs_cache_and_owner_metadata(self):
        game = MagicMock()
        item = MagicMock()
        item.id = "item_1"
        item.tags = {}

        with patch("edgecaster.systems.inventory.entity_graph_ops_system.attach_entity_to_parent") as mock_attach:
            add_inventory_item(game, "player", item)

        mock_attach.assert_called_once_with(game, item, "player", socket_id="inventory")

    def test_remove_inventory_item_at_detaches_and_marks_reason(self):
        game = MagicMock()
        item = MagicMock()
        item.id = "item_2"
        item.parent_entity_id = "player"
        item.socket_id = "inventory"
        game.mark_entity_removed = MagicMock()

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[item]):
            with patch("edgecaster.systems.inventory.entity_graph_ops_system.detach_entity_from_parent") as mock_detach:
                removed = remove_inventory_item_at(game, "player", 0, reason="consumed")

        assert removed is item
        mock_detach.assert_called_once_with(game, item)
        game.mark_entity_removed.assert_called_once_with(item, reason="consumed")


class TestPlayerPickUp:
    """Tests for player_pick_up."""

    @pytest.fixture
    def mock_game(self):
        """Create a mock game."""
        game = MagicMock()
        game.player_id = "player"
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

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
            with patch('edgecaster.systems.inventory.item_grants') as mock_grants:
                mock_grants.get_item_grants.return_value = []
                player_pick_up(mock_game)

        assert "sword_1" not in level.entities

    def test_marks_entity_removed_on_pickup(self, mock_game):
        """Should persist removal for deterministic items via entity state."""
        item = MagicMock(spec=[])
        item.id = "berry_1"
        item.name = "Blueberry"
        item.kind = "item"
        item.tags = {"lineage_id": "agg:berries:1"}
        mock_game.mark_entity_removed = MagicMock()

        level = mock_game._level()
        level.entities = {"berry_1": item}
        mock_game._entity_at.return_value = item

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
            with patch('edgecaster.systems.inventory.item_grants') as mock_grants:
                mock_grants.get_item_grants.return_value = []
                player_pick_up(mock_game)

        mock_game.mark_entity_removed.assert_called_once()
        args, kwargs = mock_game.mark_entity_removed.call_args
        assert args[0] is item
        assert kwargs.get("reason") == "pickup"
        assert "lineage_only" not in kwargs

    def test_pickup_attaches_item_to_player_owner(self, mock_game):
        """Picked items should retain identity and become inventory children."""
        item = MagicMock(spec=[])
        item.id = "dagger_1"
        item.entity_id = "dagger_1"
        item.name = "Dagger"
        item.kind = "item"
        item.tags = {}

        level = mock_game._level()
        level.entities = {"dagger_1": item}
        mock_game._entity_at.return_value = item

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
            with patch("edgecaster.systems.inventory.entity_graph_ops_system.attach_entity_to_parent") as mock_attach:
                with patch('edgecaster.systems.inventory.item_grants') as mock_grants:
                    mock_grants.get_item_grants.return_value = []
                    player_pick_up(mock_game)

        mock_attach.assert_called_once_with(mock_game, item, "player", socket_id="inventory")

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
        item.parent_entity_id = "player"
        item.socket_id = "inventory"

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

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[item]):
            with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
                mock_equip.is_equipped.return_value = False
                drop_inventory_item(game, 0)

        assert item.pos == (5, 5)
        assert item.id in game._level().entities

    def test_drop_detaches_item_owner_metadata(self, mock_game):
        """Dropped items should no longer be marked as inventory children."""
        game, item = mock_game
        item.tags = {"inventory_owner_id": "player", "in_inventory": True}

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[item]):
            with patch("edgecaster.systems.inventory.entity_graph_ops_system.detach_entity_from_parent") as mock_detach:
                with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
                    mock_equip.is_equipped.return_value = False
                    drop_inventory_item(game, 0)

        mock_detach.assert_called_once_with(game, item)

    def test_invalid_index_does_nothing(self, mock_game):
        """Should silently ignore invalid index."""
        game, _ = mock_game

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
            drop_inventory_item(game, 99)

    def test_unequips_before_dropping(self, mock_game):
        """Should unequip item before dropping."""
        game, item = mock_game
        item.tags = {"equipped_slot": "main_hand"}

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[item]):
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
        berry.parent_entity_id = "player"
        berry.socket_id = "inventory"

        return game, berry

    def test_eats_berry_and_heals(self, mock_game):
        """Should consume berry and heal player."""
        game, berry = mock_game

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[berry]):
            eat_item_from_inventory(game, "player", 0)

        assert game._player().stats.hp == 6

    def test_empty_inventory_logs(self, mock_game):
        """Should log message when inventory empty."""
        game, _ = mock_game

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
            eat_item_from_inventory(game, "player", 0)

        game.log.add.assert_called_with("You have nothing to eat.")

    def test_rejects_non_edible(self, mock_game):
        """Should reject non-edible items."""
        game, _ = mock_game
        sword = MagicMock()
        sword.tags = {}
        sword.name = "Sword"

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[sword]):
            eat_item_from_inventory(game, "player", 0)

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

        level = MagicMock()
        level.entities = {}
        level.actors = {}
        game._level.return_value = level

        with patch("edgecaster.systems.inventory.get_inventory", side_effect=lambda g, oid: [item] if oid == "chest" else []):
            with patch("edgecaster.systems.inventory.entity_graph_ops_system.attach_entity_to_parent") as mock_attach:
                with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
                    mock_equip.is_equipped.return_value = False
                    take_from_container(game, "chest", 0)

        mock_attach.assert_called_once_with(game, item, "player", socket_id="inventory")


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

        level = MagicMock()
        level.entities = {}
        level.actors = {}
        game._level.return_value = level

        return game, item

    def test_moves_item(self, mock_game):
        """Should move item between inventories."""
        game, item = mock_game

        with patch("edgecaster.systems.inventory.get_inventory", side_effect=lambda g, oid: [item] if oid == "chest" else []):
            with patch("edgecaster.systems.inventory.entity_graph_ops_system.attach_entity_to_parent") as mock_attach:
                with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
                    mock_equip.is_equipped.return_value = False
                    move_item_between_inventories(game, "chest", 0, "player")

        mock_attach.assert_called_once_with(game, item, "player", socket_id="inventory")

    def test_same_inventory_noop(self, mock_game):
        """Should do nothing when source equals destination."""
        game, item = mock_game

        with patch("edgecaster.systems.inventory.entity_graph_ops_system.attach_entity_to_parent") as mock_attach:
            move_item_between_inventories(game, "chest", 0, "chest")

        mock_attach.assert_not_called()

    def test_self_removal_prevented(self, mock_game):
        """Should prevent recursive self-removal."""
        game, item = mock_game
        # Item is the container itself
        item.id = "chest"
        item.name = "Bag"

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[item]):
            move_item_between_inventories(game, "chest", 0, "player")

        game.log.add.assert_called_with(
            "You turn the bag inside out, but it remains itself."
        )

    def test_unequips_when_transferring(self, mock_game):
        """Should clear equipped status when moving item."""
        game, item = mock_game
        item.tags = {"equipped_slot": "main_hand"}

        with patch("edgecaster.systems.inventory.get_inventory", side_effect=lambda g, oid: [item] if oid == "chest" else []):
            with patch('edgecaster.systems.inventory.equipment_system') as mock_equip:
                mock_equip.is_equipped.return_value = True
                move_item_between_inventories(game, "chest", 0, "player")

        assert "equipped_slot" not in item.tags


class TestSplitIdentity:
    """Tests for deterministic split identity tags/ids."""

    def test_drop_qty_creates_split_identity(self):
        game = MagicMock()
        game.player_id = "player"
        game.zone_coord = (1, 2, 0)
        game.log = MagicMock()

        level = MagicMock()
        level.entities = {}
        player = MagicMock()
        player.pos = (5, 7)
        level.actors = {"player": player}
        game._level.return_value = level

        item = Entity(
            id="stack_item",
            entity_id="stack_item",
            name="Pebble",
            pos=(2, 3),
            abs_pos=(102, 203),
            glyph="*",
            color=(255, 255, 255),
            kind="item",
        )
        item.tags = {"quantity": 5}

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[item]):
            drop_inventory_item_qty(game, 0, qty=2)

        assert len(level.entities) == 1
        dropped = next(iter(level.entities.values()))
        assert str(getattr(dropped, "id", "")).startswith("split:")
        assert dropped.tags.get("split_from_entity_id") == "stack_item"
        assert dropped.tags.get("split_kind") == "drop"
        assert dropped.tags.get("split_seq") == 1
        assert dropped.tags.get("quantity") == 2
        assert item.tags.get("quantity") == 3

    def test_transfer_qty_creates_split_identity(self):
        game = MagicMock()
        game.player_id = "player"
        game.zone_coord = (0, 0, 0)
        game.log = MagicMock()

        src = Entity(
            id="bag_item",
            entity_id="bag_item",
            name="Shard",
            pos=(1, 1),
            abs_pos=(1, 1),
            glyph="*",
            color=(255, 255, 255),
            kind="item",
        )
        src.tags = {"quantity": 4}

        with patch("edgecaster.systems.inventory.get_inventory", side_effect=lambda g, oid: [src] if oid == "chest" else []):
            with patch("edgecaster.systems.inventory.entity_graph_ops_system.attach_entity_to_parent") as mock_attach:
                move_item_between_inventories_qty(game, "chest", 0, "player", qty=1)

        transferred = mock_attach.call_args[0][1]
        mock_attach.assert_called_once_with(game, transferred, "player", socket_id="inventory")

        assert src.tags.get("quantity") == 3


class TestGetEquippedInSlot:
    """Tests for get_equipped_in_slot."""

    def test_finds_equipped_item(self):
        """Should find item equipped through a slot socket."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {"equipped_slot": "main_hand"}
        game.entity_graph = EntityGraphStore()
        game.entity_graph.register("player", kind="actor")
        game.entity_graph.register("sword_1", parent_entity_id="player", socket_id="main_hand", kind="item")

        with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=sword):
            result = get_equipped_in_slot(game, "player", "main_hand")

        assert result is sword

    def test_returns_none_if_empty(self):
        """Should return None if slot is empty."""
        game = MagicMock()

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
            result = get_equipped_in_slot(game, "player", "main_hand")

        assert result is None

    def test_handles_legacy_equipped_tag(self):
        """Should handle legacy 'equipped' tag."""
        game = MagicMock()
        sword = MagicMock()
        sword.tags = {"equipped": "main_hand"}

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[sword]):
            result = get_equipped_in_slot(game, "player", "main_hand")

        assert result is sword


class TestUnequipSlot:
    """Tests for unequip_slot."""

    def test_clears_equipped_tags(self):
        """Should clear equipped tags from item."""
        game = MagicMock()
        sword = MagicMock()
        sword.tags = {"equipped_slot": "main_hand"}

        class _Graph:
            def get_children(self, owner_id, socket_id=None):
                if socket_id == "main_hand":
                    return ["sword_1"]
                return []
        game.entity_graph = _Graph()

        with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=sword), \
             patch("edgecaster.systems.inventory.entity_graph_ops_system.reparent_entity") as mock_reparent:
            unequip_slot(game, "player", "main_hand")

        assert "equipped_slot" not in sword.tags
        mock_reparent.assert_called_once_with(game, sword, parent_id="player", socket_id="inventory")

    def test_does_nothing_if_empty(self):
        """Should do nothing if slot empty."""
        game = MagicMock()

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[]):
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

        with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=sword), \
             patch("edgecaster.systems.inventory.entity_graph_ops_system.reparent_entity"):
            unequip_item(game, "player", "sword_1")

        assert "equipped_slot" not in sword.tags
        game.refresh_actor_actions.assert_called_with("player")

    def test_ignores_wrong_item_id(self):
        """Should ignore items with different id."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {"equipped_slot": "main_hand"}

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[sword]):
            with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=None):
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

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[sword]):
            with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=sword), \
                 patch("edgecaster.systems.inventory.entity_graph_ops_system.reparent_entity") as mock_reparent, \
                 patch("edgecaster.systems.inventory.equip_rules.can_equip_item_in_slot", return_value=(True, "")):
                equip_item_to_slot(game, "player", "sword_1", "main_hand")

        assert sword.tags["equipped_slot"] == "main_hand"
        game.refresh_actor_actions.assert_called_with("player")
        mock_reparent.assert_called_once_with(game, sword, parent_id="player", socket_id="main_hand")

    def test_replaces_existing_equipped(self):
        """Should unequip existing item in slot first."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {"equipped_slot": "main_hand"}

        dagger = MagicMock()
        dagger.id = "dagger_1"
        dagger.tags = {}

        def mock_find_entity(g, iid):
            if iid == "sword_1": return sword
            if iid == "dagger_1": return dagger
            return None

        class _Graph:
            def get_children(self, owner_id, socket_id=None):
                if socket_id == "main_hand": return ["sword_1"]
                return []
        game.entity_graph = _Graph()

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[sword, dagger]):
            with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", side_effect=mock_find_entity), \
                 patch("edgecaster.systems.inventory.entity_graph_ops_system.reparent_entity"), \
                 patch("edgecaster.systems.inventory.equip_rules.can_equip_item_in_slot", return_value=(True, "")):
                equip_item_to_slot(game, "player", "dagger_1", "main_hand")

        assert "equipped_slot" not in sword.tags
        assert dagger.tags["equipped_slot"] == "main_hand"

    def test_ignores_wrong_item_id(self):
        """Should do nothing if item id not found."""
        game = MagicMock()
        sword = MagicMock()
        sword.id = "sword_1"
        sword.tags = {}

        with patch("edgecaster.systems.inventory.get_inventory", return_value=[sword]):
            with patch("edgecaster.systems.inventory.entity_lifecycle_system.find_runtime_entity", return_value=None):
                equip_item_to_slot(game, "player", "nonexistent", "main_hand")

        assert "equipped_slot" not in sword.tags
