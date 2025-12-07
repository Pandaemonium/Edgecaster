from __future__ import annotations

from .base import Scene
from edgecaster.char_creation import run_character_creation
from edgecaster.character import Character

from .dungeon import DungeonScene  # forward reference via import inside or here


class CharacterCreationScene(Scene):
    """Scene that runs the character creation UI and then transitions to the dungeon."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        cfg = manager.cfg

        # This opens its own Pygame window (from char_creation.py),
        # lets the player tweak stats, etc., and returns a Character.
        # Honor fullscreen state from the current renderer
        full = False
        if hasattr(manager, "renderer") and hasattr(manager.renderer, "is_fullscreen"):
            full = manager.renderer.is_fullscreen()
        char: Character = run_character_creation(cfg, fullscreen=full)
            
        if char is None:
            # Return to main menu instead of quitting the game
            from .main_menu import MainMenuScene
            manager.set_scene(MainMenuScene())
            return

        # If you someday want Esc to mean "quit the game from char creation",
        # you could detect that here, but right now run_character_creation
        # always returns a Character.
        manager.character = char

        # Next: dungeon
        manager.set_scene(DungeonScene())
