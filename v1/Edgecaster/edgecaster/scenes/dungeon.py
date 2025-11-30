from __future__ import annotations

from .base import Scene
from edgecaster.character import Character

from edgecaster.game import Game


class DungeonScene(Scene):
    """The main roguelike dungeon run."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if manager.character is None:
            # Shouldn't happen, but be defensive.
            from edgecaster.character import default_character
            manager.character = default_character()

        char = manager.character
        cfg = manager.cfg
        rng = manager.rng
        renderer = manager.renderer

        game = Game(
            cfg,
            rng,
            character=char,
        )

        # This call owns its own loop (QUIT/ESC leave the loop).
        renderer.render(game)

        # After the dungeon run, decide what comes next.
        if not game.player_alive:
            # Player died: go back to character creation.
            from .character_creation import CharacterCreationScene
            manager.set_scene(CharacterCreationScene())
        else:
            # Later you can branch to a world map or victory screen here.
            manager.set_scene(None)
