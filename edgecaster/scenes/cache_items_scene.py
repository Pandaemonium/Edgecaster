"""Cache items scene for selecting items from ground/chest/container."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

import pygame

from .base import PopupMenuScene

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.entities import Entity
    from .manager import SceneManager


class CacheItemsScene(PopupMenuScene):
    """Popup for selecting items from an item cache (ground, chest, etc.).

    Shows a list of items with options to:
    - Select individual items to pick up
    - Take All (via 'A' key or menu option)
    - Cancel (Escape)
    """

    FOOTER_TEXT = "Enter: Take  |  A: Take All  |  Esc: Cancel"

    def __init__(
        self,
        game: "Game",
        items: List["Entity"],
        *,
        title: str = "Items Here",
        scale: float = 0.6,
        dim_background: bool = True,
    ) -> None:
        # Set attributes BEFORE super().__init__() because it calls get_ascii_art()
        self.game = game
        self.items = list(items)  # Copy so we can modify
        self.title = title
        self._item_ids: List[str] = []
        super().__init__(scale=scale, dim_background=dim_background)

    def get_menu_items(self) -> List[str]:
        """Build list of items with quantity/charges display."""
        from edgecaster.systems.inventory import get_quantity

        menu = []
        self._item_ids = []

        for item in self.items:
            name = getattr(item, "name", "item")
            glyph = getattr(item, "glyph", "?")
            tags = getattr(item, "tags", {}) or {}

            # Show charges for wands, quantity for stacks
            suffix = ""
            if "charges" in tags:
                charges = tags.get("charges", 0)
                max_charges = tags.get("max_charges", charges)
                suffix = f" ({charges}/{max_charges})"
            else:
                qty = get_quantity(item)
                if qty > 1:
                    suffix = f" ({qty})"

            menu.append(f"{glyph}  {name}{suffix}")
            self._item_ids.append(getattr(item, "id", ""))

        menu.append("Take All")
        menu.append("Cancel")
        return menu

    def get_ascii_art(self) -> str:
        return self.title

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        """Handle selection of an item or action."""
        num_items = len(self.items)

        if index == num_items:
            # "Take All"
            self._take_all(manager)
            return True
        elif index == num_items + 1:
            # "Cancel"
            return True
        elif 0 <= index < num_items:
            # Pick up specific item
            self._take_item(index, manager)
            # Refresh the list if items remain
            if self.items:
                # Update selection to stay in bounds
                self.selected_idx = min(self.selected_idx, len(self.items) + 1)
                return False  # Stay open
            return True  # Close if no items left

        return False

    def on_back(self, manager: "SceneManager") -> bool:
        """Handle escape/cancel."""
        manager.pop_scene()
        return True

    def _panel_event(self, event: pygame.event.Event, manager: "SceneManager") -> None:
        """Handle keyboard shortcuts."""
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_a:
                # 'A' for Take All
                self._take_all(manager)
                manager.pop_scene()
                return

        # Let parent handle normal menu navigation
        super()._panel_event(event, manager)

    def _take_item(self, index: int, manager: "SceneManager") -> None:
        """Pick up the item at the given index."""
        from edgecaster.systems import inventory as inv_system

        if not (0 <= index < len(self.items)):
            return

        item = self.items[index]
        item_id = getattr(item, "id", None)

        # Use the new specific pickup function
        success = inv_system.player_pick_up_item(self.game, item)

        if success:
            # Remove from our local list
            self.items.pop(index)

    def _take_all(self, manager: "SceneManager") -> None:
        """Pick up all items in the cache."""
        from edgecaster.systems import inventory as inv_system

        for item in list(self.items):
            inv_system.player_pick_up_item(self.game, item)

        self.items.clear()
