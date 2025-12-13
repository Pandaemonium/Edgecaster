# manager.py
from __future__ import annotations

from typing import Optional, List, Type
import pygame
from pygame import Rect

from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.visuals import VisualProfile
from edgecaster.rng import new_rng
from edgecaster.character import Character, default_character
from .game_input import load_bindings_full

from .base import Scene
from .character_creation_scene import CharacterCreationScene
from .main_menu import MainMenuScene
from .world_map_scene import WorldMapScene
from edgecaster.ui.status_header import StatusHeaderWidget
from edgecaster.ui.widgets import WidgetContext
from edgecaster.visual_effects import concat_effect_names


class SceneManager:
    def __init__(self, cfg: config.GameConfig, renderer: AsciiRenderer) -> None:
        # Store config + renderer from main.py
        self.cfg = cfg
        self.renderer = renderer

        # Scene stack + window rects (for overlay scenes like recursive options)
        self.scene_stack: List[Scene] = []
        self.window_stack: List[Rect] = []

        self.widget_layers = {
            "hud": [StatusHeaderWidget()],
        }


        # Shared options state (persists across scenes)
        # Merge "real" game options from the main branch with the newer test flags.
        self.options = {
            "Music": True,
            "Sound": True,
            "Fullscreen": False,
            "Vicious dog trigger warning": False,
            "Show FPS": False,
            "Big Text": False,
        }
        # Keybindings (persisted to disk): {"bindings": ..., "move_bindings": ...}
        binds, moves = load_bindings_full()
        self.keybindings = {"bindings": binds, "move_bindings": moves}

        # RNG + game state
        self.rng = new_rng()
        # Use a default character like the original manager(1) did, so things
        # like the Options -> World Seed display have something to look at.
        self.character: Character = default_character()
        self.current_game = None
        # Optional global visual profile (e.g. world-level curses/blessings).
        # This is a high-level hint; renderers may choose how to apply it.
        self.global_visual_profile: VisualProfile | None = None
        # Start on the main menu
        self.set_scene(MainMenuScene())

    # ------------------------------------------------------------------ #
    # RNG factory used by scenes (e.g. DungeonScene) to spin up new RNGs.

    def rng_factory(self, seed=None):
        """
        Factory used by scenes to make a new RNG.

        new_rng() already handles seeding when seed is None.
        """
        return new_rng(seed)

    # ------------------------------------------------------------------ #
    # Window helpers

    def _root_window_rect(self) -> Rect:
        """Full-screen rect."""
        return Rect(0, 0, self.renderer.width, self.renderer.height)

    def compute_child_window_rect(
        self,
        scale: float,
        parent: Optional[Rect] = None,
        offset: int = 0,
    ) -> Rect:
        """
        Compute a child window rect:
        - If parent is None, use the top of window_stack or full screen.
        - Size = parent.size * scale, centered in parent, plus offset.
        """
        if parent is None:
            base = self.window_stack[-1] if self.window_stack else self._root_window_rect()
        else:
            base = parent

        w = int(base.width * scale)
        h = int(base.height * scale)
        x = base.x + (base.width - w) // 2 + offset
        y = base.y + (base.height - h) // 2 + offset
        return Rect(x, y, w, h)

    def open_window_scene(
        self,
        scene_cls: Type[Scene],
        *,
        scale: float = 0.6,
        parent: Optional[Rect] = None,
        offset: int = 0,
        visual: VisualProfile | None = None,
        **kwargs,
    ) -> Scene:
        """
        General helper: open a scene as a window at a given scale.

        - Computes window_rect
        - Instantiates scene_cls(window_rect=..., **kwargs)
        - Pushes onto scene_stack
        """
        window_rect = self.compute_child_window_rect(scale, parent, offset)
        scene = scene_cls(window_rect=window_rect, **kwargs)  # type: ignore[arg-type]
        if visual is not None:
            scene.visual_profile = visual
        # Tag the scene so we know it's windowed
        scene.window_rect = window_rect  # type: ignore[attr-defined]
        self.window_stack.append(window_rect)
        self.scene_stack.append(scene)
        return scene


    def set_global_visual_profile(self, profile: VisualProfile | None) -> None:
        """
        Set or clear a global visual profile that should affect the whole game.
        For example, a 'cursed' world might flip everything horizontally.
        """
        self.global_visual_profile = profile

        # Best-effort: if the renderer knows how to use a global profile,
        # hand it off. Otherwise this is a harmless no-op.
        if hasattr(self.renderer, "set_global_visual_profile"):
            self.renderer.set_global_visual_profile(profile)
        else:
            # As a fallback, stash it directly on the renderer so the
            # present() code can read it if you wire it up later.
            setattr(self.renderer, "global_visual_profile", profile)

    def set_global_visual_effects(self, names: list[str] | None) -> None:
        """
        Set or clear global visual effects (named, stackable).
        This is the preferred modern path for world-level curses/blessings.
        """
        # Best-effort: forward to renderer's effect manager.
        if hasattr(self.renderer, "set_global_visual_effects"):
            self.renderer.set_global_visual_effects(names or [])
        else:
            # Fallback: stash for later; harmless if renderer doesn't read it yet.
            setattr(self.renderer, "global_visual_effects", names or [])



    def draw_widget_layer(self, layer: str, *, surface, game=None, scene=None) -> None:
        widgets = self.widget_layers.get(layer)
        if not widgets:
            return
        # Prefer explicit game, else fall back to current_game if present.
        if game is None:
            game = self.current_game
        if game is None:
            return

        ctx = WidgetContext(surface=surface, game=game, scene=scene, renderer=self.renderer)
        for w in widgets:
            w.layout(ctx)
            w.draw(ctx)



    # ------------------------------------------------------------------ #
    # Stack operations

    def push_scene(self, scene: Scene) -> None:
        """For non-windowed scenes, or when you handle window_rect manually."""
        self.scene_stack.append(scene)

    def pop_scene(self) -> None:
        if not self.scene_stack:
            return
        scene = self.scene_stack.pop()

        # If this scene was windowed, pop matching rect too
        if hasattr(scene, "window_rect") and self.window_stack:
            self.window_stack.pop()

    def set_scene(self, scene: Optional[Scene]) -> None:
        if scene is None:
            self.scene_stack.clear()
        else:
            self.scene_stack = [scene]

    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """
        Main loop: if a scene opts into the live-loop hooks, drive it from
        here. Otherwise fall back to the scene's legacy run().
        """
        while self.scene_stack:
            scene = self.scene_stack[-1]
            if getattr(scene, "uses_live_loop", False):
                self._run_live_scene(scene)
            else:
                scene.run(self)

    # ------------------------------------------------------------------ #
    # Live-loop driver for scenes that set uses_live_loop = True.

    # ------------------------------------------------------------------ #
    # Live-loop driver for scenes that set uses_live_loop = True.

    def _run_live_scene(self, scene: Scene) -> None:
        renderer = self.renderer
        clock = pygame.time.Clock()

        # Drive events/update/render until the scene stack changes or the
        # app is quit.
        while self.scene_stack and self.scene_stack[-1] is scene:
            dt = clock.tick(60)

            # Events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.set_scene(None)
                    return

                # Window resize: update the renderer's display surface
                if event.type == pygame.VIDEORESIZE:
                    # Only call if the renderer actually has this helper
                    if hasattr(renderer, "handle_resize"):
                        renderer.handle_resize(event.w, event.h)
                    else:
                        # Fallback: just resize the display surface
                        pygame.display.set_mode((event.w, event.h), renderer.surface_flags)
                    # Don't forward this to scenes; it's purely a view concern.
                    continue

                # Global fullscreen toggle
                if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                    renderer.toggle_fullscreen()
                    # Do not forward to scene; handled globally.
                    continue

                # Normal path: scene-specific handling
                scene.handle_event(event, self)

            # Update
            scene.update(dt, self)

            # Render
            # Let scenes globally tint everything by setting visual_effects; also include any world-level effects.
            if hasattr(renderer, "active_visual_effects"):
                global_eff = getattr(renderer, "global_visual_effects", []) or []
                scene_eff = getattr(scene, "visual_effects", []) or []
                renderer.active_visual_effects = concat_effect_names(global_eff, scene_eff)

            scene.render(renderer, self)

            # If renderer signals quit (legacy escape hatch), honor it.
            if getattr(renderer, "quit_requested", False):
                renderer.quit_requested = False
                return

