from __future__ import annotations

from typing import Optional

import pygame

from .base import PopupMenuScene


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

    def get_menu_items(self) -> list[str]:
        # Empty for now: just a way to close.
        return ["Back"]

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        # Only one item; treat it as Back.
        return self.on_back(manager)

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        # Since this is a popup over the dungeon, just pop this scene.
        manager.pop_scene()
        return True

    def get_ascii_art(self) -> Optional[str]:
        # Simple title for now; you can replace with fancier ASCII later.
        return "Inventory"
