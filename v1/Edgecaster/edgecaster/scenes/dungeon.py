from __future__ import annotations

from .base import Scene
from edgecaster.game import Game


class DungeonScene(Scene):
    """The main roguelike dungeon scene."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        cfg = manager.cfg
        rng = manager.rng
        renderer = manager.renderer
        char = manager.character

        # Construct the game using the chosen character
        game = Game(cfg, rng, character=char)

        # Use the shared renderer; do NOT tear it down here
        renderer.render(game)

        # Decide what comes next:
        if not game.player_alive:
            # After death, go back to character creation
            from .character_creation_scene import CharacterCreationScene
            manager.set_scene(CharacterCreationScene())
        else:
            # Later, you could branch to a world-map scene, etc.
            manager.set_scene(None)
