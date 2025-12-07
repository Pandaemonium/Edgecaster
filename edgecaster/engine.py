from __future__ import annotations

"""
Engine entry point: owns the high-level loop orchestration.

Note: For now this delegates to SceneManager.run(), which still contains
scene-owned loops (e.g., DungeonScene). The next refactor step will pull
the event/update/render loop up into this Engine to make renderers fully
view-only and scenes command-driven.
"""

import pygame

from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.scenes import SceneManager
from edgecaster.rng import new_rng


class Engine:
    def __init__(self, cfg: config.GameConfig) -> None:
        pygame.init()
        self.cfg = cfg
        self.rng_factory = new_rng
        self.renderer = AsciiRenderer(cfg.view_width, cfg.view_height, cfg.tile_size)
        self.manager = SceneManager(cfg, self.renderer)

    def run(self) -> None:
        """
        Run the game. Currently defers to SceneManager.run(), which still
        owns per-scene loops. Future work: unify the loop here and convert
        scenes to pure command/update handlers plus view models.
        """
        try:
            self.manager.run()
        finally:
            self.renderer.teardown()

