from __future__ import annotations

from typing import Optional

import pygame

from .base import PopupMenuScene
from .urgent_message_scene import UrgentMessageScene


class InventoryScene(PopupMenuScene):
    """
    Minimal inventory popup using the standardized MenuScene / MenuInput
    controls. For now, it's basically an empty container with a single
    'Back' option, ready for real items later.

    - Opens as a popup over the dungeon (snapshot + dimmed background).
    - Uses numpad / arrow / WASD navigation and key-repeat.
    - Esc OR 'i' both close the inventory (handled in PopupMenuScene).
    """

    # Override footer to mention 'i' as an extra back key.
    FOOTER_TEXT = (
        "↑/↓ Numpad or W/S to move, Enter/Space to select, "
        "Esc or i to go back, F11 to toggle fullscreen"
    )

    def __init__(
        self,
        game,
        *,
        window_rect: Optional[pygame.Rect] = None,
    ) -> None:
        # window_rect is optional; manager.open_window_scene can pass one,
        # otherwise PopupMenuScene will compute a centered popup rect.
        super().__init__(window_rect=window_rect, dim_background=True, scale=0.7)
        self.game = game

    # ---- MenuScene hooks -----------------------------------------------------

    # ---- MenuScene hooks -----------------------------------------------------

    def get_menu_items(self) -> list[str]:
        """Return a simple list of inventory item names plus a Back option.

        For now, we just show one line per carried Entity, by its name. The last
        entry is always 'Back'. When the inventory is empty, we show an
        '(Empty)' placeholder plus 'Back'.
        """
        items: list[str] = []
        inv = getattr(self.game, "inventory", [])
        # We don't assume any particular type beyond having a .name attribute.
        if inv:
            for ent in inv:
                name = getattr(ent, "name", None) or "(unnamed item)"
                items.append(str(name))
        else:
            items.append("(Empty)")
        items.append("Back")
        return items

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """Handle selection in the inventory menu.

        - Selecting 'Back' closes the inventory popup.
        - Selecting a real item opens a tiny, context-aware submenu
          (e.g. Drop / Eat) using the UrgentMessageScene popup shell.
        """
        inv = getattr(self.game, "inventory", [])
        back_index = len(inv) if inv else 1  # '(Empty)' + 'Back' when empty

        # 'Back' (or the '(Empty)' row) → close inventory
        if index >= back_index:
            return self.on_back(manager)

        # No inventory, nothing to do
        if not inv or index < 0 or index >= len(inv):
            return False

        ent = inv[index]

        # Build a minimal context-menu list of actions.
        choices: list[str] = ["Drop"]

        tags = getattr(ent, "tags", {}) or {}
        is_berry = bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }
        if is_berry:
            choices.append("Eat")

        # Very small child window – no title or body text, just choices.
        # We base the rect off the existing popup chain so recursive
        # inventories / menus still stack nicely.
        window_rect = manager.compute_child_window_rect(scale=0.4)

        def handle_choice(choice_idx: int, mgr: "SceneManager") -> None:  # type: ignore[name-defined]
            if choice_idx < 0 or choice_idx >= len(choices):
                return
            choice = choices[choice_idx]

            # Re-fetch inventory in case it changed while the popup was open.
            cur_inv = getattr(self.game, "inventory", [])
            if index < 0 or index >= len(cur_inv):
                return

            if choice == "Drop":
                if hasattr(self.game, "drop_inventory_item"):
                    self.game.drop_inventory_item(index)
            elif choice == "Eat":
                if hasattr(self.game, "eat_inventory_item"):
                    self.game.eat_inventory_item(index)

        # Push the lightweight context popup; keep the inventory itself open.
        manager.push_scene(
            UrgentMessageScene(
                self.game,
                "",  # no body text
                title="",
                choices=choices,
                on_choice=handle_choice,
                window_rect=window_rect,
                back_confirms=False,
            )
        )
        return True



    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        # Since this is a popup over the dungeon, just pop this scene.
        manager.pop_scene()
        return True

    def get_ascii_art(self) -> Optional[str]:
        # Simple title for now; you can replace with fancier ASCII later.
        return "Inventory"
