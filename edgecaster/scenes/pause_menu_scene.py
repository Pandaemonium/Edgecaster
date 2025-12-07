from __future__ import annotations

from typing import List

from .base import MenuScene
from .options_scene import OptionsScene
from .main_menu import MainMenuScene


class PauseMenuScene(MenuScene):
    """Pause overlay: Resume, Options, Quit to Main Menu, Quit Game."""

    # Slightly customized footer so it says "Esc to resume"
    FOOTER_TEXT = (
        "↑/↓ Numpad or W/S to move, Enter/Space to select, "
        "Esc to resume, F11 to toggle fullscreen"
    )

    # ---- MenuScene hooks -----------------------------------------------------

    def get_menu_items(self) -> List[str]:
        return [
            "Resume",
            "Options",
            "Quit to Main Menu",
            "Quit Game",
        ]

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """
        Esc from pause = quick resume (pop this overlay).
        """
        manager.pop_scene()
        return True

    def on_activate(
        self,
        index: int,
        manager: "SceneManager",  # type: ignore[name-defined]
    ) -> bool:
        """
        Handle selecting a pause menu option.
        Return True to close this menu after handling.
        """
        choice = self.get_menu_items()[index]

        if choice == "Resume":
            manager.pop_scene()

        elif choice == "Options":
            # Stack Options on top of Pause; when Options pops,
            # we land back on the Pause menu.
            manager.push_scene(OptionsScene())

        elif choice == "Quit to Main Menu":
            manager.set_scene(MainMenuScene())

        elif choice == "Quit Game":
            manager.set_scene(None)

        # In all cases, we've finished with this Pause menu.
        return True

    def draw_extra(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Draw the 'Paused' title above the menu options.
        """
        renderer = manager.renderer
        surface = renderer.surface

        title = renderer.font.render("Paused", True, renderer.fg)
        surface.blit(
            title,
            ((renderer.width - title.get_width()) // 2, 80),
        )
