from __future__ import annotations

from typing import List

from .base import MenuScene



class SavedGamesScene(MenuScene):
    """Dummy saved-games menu to test scene transitions."""

    # Use the standard footer controls from MenuScene (FOOTER_TEXT default)

    def get_menu_items(self) -> List[str]:
        # For now, just a single 'Back' option; saves are empty.
        return ["Back to Main Menu"]

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """
        Esc from saved games: just pop back to whatever pushed us (main menu).
        """
        manager.pop_scene()
        return True

    def on_activate(
        self,
        index: int,
        manager: "SceneManager",  # type: ignore[name-defined]
    ) -> bool:
        """
        Selecting the only option = go back.
        """
        # In future if you add more options, you can branch on index here.
        manager.pop_scene()
        return True

    def draw_extra(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Draw the 'Saved Games' title and the 'no saves' text above the menu.
        """
        renderer = manager.renderer
        surface = renderer.surface

        title_text = renderer.font.render("Saved Games", True, renderer.fg)
        surface.blit(
            title_text,
            ((renderer.width - title_text.get_width()) // 2, 80),
        )

        # Dummy "no saves" info
        info_text = renderer.small_font.render(
            "No saved games found.", True, renderer.dim
        )
        surface.blit(
            info_text,
            ((renderer.width - info_text.get_width()) // 2, 140),
        )
