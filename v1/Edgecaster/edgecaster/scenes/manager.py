from __future__ import annotations

from typing import Optional

from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.rng import new_rng
from edgecaster.character import Character, default_character

from .base import Scene
from .character_creation_scene import CharacterCreationScene


class SceneManager:
    """Controls which high-level scene is currently running."""

    def __init__(self, cfg: config.GameConfig, renderer: AsciiRenderer) -> None:
        self.cfg = cfg
        self.renderer = renderer
        self.rng = new_rng(cfg.seed)

        # Last-chosen character (default until player customizes one)
        self.character: Character = default_character()

        # Start in character creation scene
        self.current_scene: Optional[Scene] = CharacterCreationScene()

    def run(self) -> None:
        """Run scenes until there are none left."""
        while self.current_scene is not None:
            scene = self.current_scene
            self.current_scene = None
            scene.run(self)

    def set_scene(self, scene: Optional[Scene]) -> None:
        """Switch to the next scene (or end the game if None)."""
        self.current_scene = scene
