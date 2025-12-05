from __future__ import annotations

import threading

from .base import Scene
from edgecaster.game import Game
from .urgent_message_scene import UrgentMessageScene


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
        setattr(game, "scene_manager", manager)
        # If a fractal edit result is waiting, absorb it into custom patterns
        if getattr(manager, "fractal_edit_result", None):
            res = manager.fractal_edit_result
            manager.fractal_edit_result = None
            pts = res.get("vertices") if isinstance(res, dict) else None
            if pts and len(pts) >= 2:
                game.custom_patterns.append(pts)
                game.character.custom_pattern = pts
                # force ability bar rebuild
                renderer.abilities_signature = None

        # Save any previous hook (in case we ever call DungeonScene from another scene)
        old_cb = getattr(game, "urgent_callback", None)

        def show_urgent(text: str) -> None:
            # Build popup from game's structured urgent fields.
            title = getattr(game, "urgent_title", "") or ""
            body = getattr(game, "urgent_body", text)
            choices = getattr(game, "urgent_choices", None) or ["Continue..."]

            def handle_choice(idx, _manager) -> None:
                # Look up any effect the Game attached to this urgent event.
                effect = getattr(game, "urgent_choice_effect", None)
                if effect is not None:
                    effect(idx, game)
                    # Clear it so it doesn't leak to the next popup.
                    game.urgent_choice_effect = None

            manager.push_scene(
                UrgentMessageScene(
                    game,
                    body,
                    title=title,
                    choices=choices,
                    on_choice=handle_choice,
                )
            )
            # Tell the ASCII renderer to exit its loop ASAP so DungeonScene.run()
            # can yield to the new scene.
            renderer.quit_requested = True




        game.urgent_callback = show_urgent

        # Clear flags before rendering
        renderer.quit_requested = False
        if hasattr(renderer, "pause_requested"):
            renderer.pause_requested = False
        # Clear inventory flag from any previous run
        if hasattr(game, "inventory_requested"):
            game.inventory_requested = False

        # Run the dungeon loop with the urgent callback installed
        try:
            renderer.render(game)
        finally:
            # Restore previous callback so other scenes can reuse the hook.
            game.urgent_callback = old_cb

        # ---- Decide what comes next ------------------------------------

        # 0) If an UrgentMessageScene was pushed from inside the game logic,
        #    it will already be on top of the stack. In that case, just
        #    yield control so SceneManager.run() can run it next.
        
        if manager.scene_stack and isinstance(manager.scene_stack[-1], UrgentMessageScene):
            return

        # 1) Fallback / generalisation for legacy urgent_message flag:
        #    If there's an unresolved urgent_message, show it as a popup.
        if getattr(game, "urgent_message", None) and not getattr(game, "urgent_resolved", True):
            body = getattr(game, "urgent_body", None) or game.urgent_message or ""
            title = getattr(game, "urgent_title", "") or ""
            choices = getattr(game, "urgent_choices", None) or ["Continue..."]

            manager.push_scene(
                UrgentMessageScene(
                    game,
                    body,
                    title=title,
                    choices=choices,
                )
            )
            return


        # 2) Death -> go back to main menu, discard the run
        if not getattr(game, "player_alive", True):
            self.game = None
            manager.current_game = None
            manager.set_scene(MainMenuScene())
            return

        # 3) Inventory requested -> push overlay, keep dungeon scene on stack
        if getattr(game, "inventory_requested", False):
            game.inventory_requested = False
            manager.push_scene(InventoryScene(game))
            return

        # 4) Fractal editor requested -> open editor scene
        if getattr(game, "fractal_editor_requested", False):
            game.fractal_editor_requested = False
            from .fractal_editor_scene import FractalEditorScene, FractalEditorState
            state = getattr(game, "fractal_editor_state", None) or FractalEditorState()
            # Launch full-screen editor so clicks/render align to the same origin
            manager.push_scene(FractalEditorScene(state=state, window_rect=None))
            return

        # 5) Pause requested -> push pause menu overlay
        if getattr(renderer, "pause_requested", False):
            renderer.pause_requested = False
            manager.push_scene(PauseMenuScene())
            return

        # 6) World map requested -> push world map scene (keep game instance)
        if getattr(game, "map_requested", False):
            game.map_requested = False
            from .world_map_scene import WorldMapScene
            manager.push_scene(WorldMapScene(game, span=16))
            return

        # Otherwise, the loop ended for some external reason (like quitting)
        self.game = None
        manager.current_game = None
        manager.set_scene(None)
