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
        Run the game. Scenes that opt into uses_live_loop are driven from
        here via SceneManager._run_live_scene; legacy scenes continue to
        use their run() method. This is the first step toward a single
        engine-owned loop.
        """
        try:
            while self.manager.scene_stack:
                scene = self.manager.scene_stack[-1]
                if getattr(scene, "uses_live_loop", False):
                    self.manager._run_live_scene(scene)
                else:
                    scene.run(self.manager)
        finally:
            self.renderer.teardown()
