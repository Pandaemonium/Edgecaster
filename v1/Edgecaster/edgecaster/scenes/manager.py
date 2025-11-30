from __future__ import annotations

from typing import Optional

from edgecaster import config
from edgecaster.rng import new_rng
from edgecaster.render.ascii import AsciiRenderer

from .base import Scene, CharacterInfo
from .character_creation import CharacterCreationScene


class SceneManager:
    """Controls which high-level scene is currently running."""

    def __init__(self, cfg: config.GameConfig, renderer: AsciiRenderer) -> None:
        self.cfg = cfg
        self.renderer = renderer
        # One RNG stream for the whole run.
        self.rng = new_rng(cfg.seed)

        self.character: Optional[CharacterInfo] = None
        self.current_scene: Optional[Scene] = CharacterCreationScene()

    def run(self) -> None:
        """Run scenes sequentially until there are no more."""
        while self.current_scene is not None:
            scene = self.current_scene
            self.current_scene = None
            scene.run(self)

    def set_scene(self, scene: Optional[Scene]) -> None:
        self.current_scene = scene
