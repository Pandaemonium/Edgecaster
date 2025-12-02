from __future__ import annotations

import threading

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
        renderer = manager.renderer
        char = manager.character

        # Lazily construct the game the first time this scene runs
        if self.game is None:
            seed = None
            if hasattr(char, "use_random_seed") and char.use_random_seed:
                seed = None  # random
            else:
                seed = getattr(char, "seed", None) or getattr(cfg, "seed", None)
            rng = manager.rng_factory(seed)
            self.game = Game(cfg, rng, character=char)
            # Precompute world map cache in the background
            if not getattr(self.game, "world_map_thread_started", False):
                self.game.world_map_thread_started = True
                self.game.world_map_rendering = True

                def worker(game_ref: Game, width: int, height: int) -> None:
                    try:
                        from .world_map_scene import WorldMapScene

                        wm = WorldMapScene(game_ref, span=16)
                        # stub renderer with width/height only
                        class Stub:
                            def __init__(self, w, h) -> None:
                                self.width = w
                                self.height = h

                        stub = Stub(width, height)
                        surf, view = wm._render_overmap(stub)
                        game_ref.world_map_cache = {"surface": surf, "view": view, "key": (width, height, wm.span)}
                        game_ref.world_map_ready = True
                    except Exception:
                        game_ref.world_map_ready = False
                    finally:
                        game_ref.world_map_rendering = False

                threading.Thread(target=worker, args=(self.game, renderer.width, renderer.height), daemon=True).start()

        game = self.game
        # expose to manager for options display
        manager.current_game = game

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

        # 4) World map requested → replace with world map scene (keep game instance)
        if getattr(game, "map_requested", False):
            game.map_requested = False
            from .world_map_scene import WorldMapScene
            # push world map on top; resume same dungeon scene/game afterward
            manager.push_scene(WorldMapScene(game, span=16))
            return

        # 5) Otherwise, the loop ended for some external reason (like quitting)
        self.game = None
        manager.current_game = None
        manager.set_scene(None)
