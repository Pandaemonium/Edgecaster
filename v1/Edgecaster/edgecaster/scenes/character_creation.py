from __future__ import annotations

from edgecaster.char_creation import run_character_creation
from edgecaster.character import Character
from .base import Scene


class CharacterCreationScene(Scene):
    """Use the full-featured character creation (stats, custom fractal, etc.)."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Reuse the existing renderer window; our char creation runs its own loop.
        char: Character = run_character_creation(manager.cfg)
        if char is None:
            manager.set_scene(None)
            return
        manager.character = char
        from .dungeon import DungeonScene  # local import to avoid cycles
        manager.set_scene(DungeonScene())
