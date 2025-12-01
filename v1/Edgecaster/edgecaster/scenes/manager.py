from __future__ import annotations

from typing import Optional, List

from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.rng import new_rng
from edgecaster.character import Character, default_character

from .base import Scene
from .character_creation_scene import CharacterCreationScene
from .main_menu import MainMenuScene
from .world_map_scene import WorldMapScene


class SceneManager:
    """Controls which high-level scene is currently running.

    Now uses a scene *stack*:

        - Top of stack = currently active scene.
        - push_scene()  -> overlay a new scene (pause menu, options, map, shop, etc.)
        - pop_scene()   -> return to whatever was underneath.
        - set_scene()   -> backwards-compatible "hard switch":
                           replace the entire stack, or quit if None.
    """

    def __init__(self, cfg: config.GameConfig, renderer: AsciiRenderer) -> None:
        self.cfg = cfg
        self.renderer = renderer
        self.rng_factory = lambda seed=None: new_rng(seed if seed is not None else None)

        # Last-chosen character (default until player customizes one)
        self.character: Character = default_character()

        # Shared options state (persists across scenes)
        self.options = {
            "Music": True,
            "Sound": True,
            "Fullscreen": False,
            "Vicious dog trigger warning": False,
        }
        # Keep a handle to the currently running Game (set by DungeonScene)
        self.current_game = None

        # New: stack of scenes, top is active
        self.scene_stack: List[Scene] = []

        # Start at the main menu
        self.push_scene(MainMenuScene())

    # ---- Stack helpers -------------------------------------------------

    @property
    def current_scene(self) -> Optional[Scene]:
        """Convenience accessor for the scene on top of the stack."""
        return self.scene_stack[-1] if self.scene_stack else None

    def push_scene(self, scene: Scene) -> None:
        """Overlay a new scene on top of the stack (pause menu, options, etc.)."""
        self.scene_stack.append(scene)

    def pop_scene(self) -> None:
        """Remove the topmost scene and resume the one beneath it."""
        if self.scene_stack:
            self.scene_stack.pop()

    def replace_scene(self, scene: Scene) -> None:
        """Replace only the top scene (leave the rest of the stack intact)."""
        if self.scene_stack:
            self.scene_stack[-1] = scene
        else:
            self.scene_stack.append(scene)

    # ---- Backwards-compatible API --------------------------------------

    def set_scene(self, scene: Optional[Scene]) -> None:
        """
        Backwards-compatible "hard switch":

        - scene is None  -> clear the entire stack (exit game loop).
        - scene not None -> replace the entire stack with just this scene.

        This preserves the semantics of the old manager, where set_scene()
        meant "go to this scene next" or "quit".
        """
        if scene is None:
            self.scene_stack.clear()
        else:
            self.scene_stack = [scene]

    # ---- Main loop ------------------------------------------------------

    def run(self) -> None:
        """Run scenes until the stack is empty."""
        while self.scene_stack:
            scene = self.scene_stack[-1]
            scene.run(self)
            # When scene.run() returns, we just loop again:
            # - the scene may have pushed/popped/replaced during its run,
            # - or the stack may be empty (then we exit),
            # - or it's still on top (and will be run again).
