from __future__ import annotations

from .base import Scene
from edgecaster.game import Game


class DungeonScene(Scene):
    """The main roguelike dungeon scene."""

    def __init__(self) -> None:
        # Keep the Game instance across pauses/inventory.
        self.game: Game | None = None

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        from .main_menu import MainMenuScene
        from .pause_menu_scene import PauseMenuScene
        from .inventory_scene import InventoryScene

        cfg = manager.cfg
        rng = manager.rng
        renderer = manager.renderer
        char = manager.character

        # Lazily construct the game the first time this scene runs
        if self.game is None:
            self.game = Game(cfg, rng, character=char)

        game = self.game

        # Clear flags before rendering
        renderer.quit_requested = False
        if hasattr(renderer, "pause_requested"):
            renderer.pause_requested = False
        # Clear inventory flag from any previous run
        if hasattr(game, "inventory_requested"):
            game.inventory_requested = False

        # Run the dungeon loop (this will exit on ESC, i, death, or window-close)
        renderer.render(game)

        # ---- Decide what comes next ------------------------------------

        # 1) Death → go back to main menu, discard the run
        if not getattr(game, "player_alive", True):
            self.game = None
            manager.set_scene(MainMenuScene())
            return

        # 2) Inventory requested → push overlay, keep dungeon scene on stack
        if getattr(game, "inventory_requested", False):
            game.inventory_requested = False
            manager.push_scene(InventoryScene(game))
            return

        # 3) Pause requested → push pause menu overlay
        if getattr(renderer, "pause_requested", False):
            renderer.pause_requested = False
            manager.push_scene(PauseMenuScene())
            return

        # 4) Otherwise, the loop ended for some external reason (like quitting)
        self.game = None
        manager.set_scene(None)
