# manager.py
from __future__ import annotations

from typing import Optional, List, Type
import pygame
from pygame import Rect

from edgecaster import config
from edgecaster.render.ascii import AsciiRenderer
from edgecaster.visuals import VisualProfile
from edgecaster.visual_effects import concat_effect_names
from edgecaster.rng import new_rng
from edgecaster.character import Character, default_character
from .game_input import load_bindings_full

from .base import Scene
from .character_creation_scene import CharacterCreationScene
from .main_menu import MainMenuScene
from .world_map_scene import WorldMapScene
from edgecaster.ui.status_header import StatusHeaderWidget
from edgecaster.ui.widgets import WidgetContext
from edgecaster.scenes.audio_manager import AudioManager, MusicRequest
from edgecaster.scenes.spatial_music import SpatialMusicDirector




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
            "Tiles": True,
        }
        # Default rendering mode (tiles on): allow sprite/icon rendering when available.
        try:
            setattr(self.renderer, "prefer_sprite_icons", bool(self.options.get("Tiles", True)))
            setattr(self.renderer, "prefer_terrain_tiles", bool(self.options.get("Tiles", True)))

 
        except Exception:
            pass

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

        self.audio = AudioManager(
            enabled_music=bool(self.options.get("Music", True)),
            enabled_sfx=bool(self.options.get("Sound", True)),
        )

        self.audio.register_music_many({
            "menu": "assets/music/menu.wav",

            # Dungeon ambient loop
            "harmonic": "assets/music/harmonic.wav",
            "aire": "assets/music/aire.wav",
            "majesty": "assets/music/majesty.wav",
            "kemet": "assets/music/kemet.ogg",
            "john_bismuth": "assets/music/john_bismuth.ogg",
            "warp_whistle": "assets/music/warp_whistle.ogg",
            "hornpipe": "assets/music/hornpipe.ogg",
            "real_boy": "assets/music/real_boy.ogg",
            "creche": "assets/music/creche.ogg",
            "ice_cave": "assets/music/ice_cave.ogg",

            # Event stingers
            "imp_cackle": "assets/music/imp_cackle.wav",
            "slot_machine": "assets/music/slot_machine.wav",
            "alligator_sting": "assets/music/alligator_sting.wav",
            "cascade": "assets/music/cascade.wav",
            "arpeggio": "assets/music/arpeggio.wav",
            # Dialogue theme
            "beggarly_vagrant": "assets/music/beggarly_vagrant.wav",
            "polka": "assets/music/polka.wav",
            "shop": "assets/music/shop.wav",
            "sergeant": "assets/music/sergeant.ogg",
            # Location music
            "morituri": "assets/music/morituri.ogg",
            "baal_cycle": "assets/music/baal_cycle.ogg",



        })
        # Context-aware music (spatial triggers, etc.)
        self._context_music_override: MusicRequest | None = None
        self.spatial_music = SpatialMusicDirector()


        # Start on the main menu
        self.set_scene(MainMenuScene())



    def apply_options_now(self) -> None:
        """
        Apply options that affect global systems immediately.
        Call this right after options are changed in the Options menu.
        """
        self.audio.set_music_enabled(bool(self.options.get("Music", True)))

        # (Optional but good to keep consistent for the future)
        if hasattr(self.audio, "set_sfx_enabled"):
            self.audio.set_sfx_enabled(bool(self.options.get("Sound", True)))

        # Rendering mode: tiles vs pure ASCII glyphs
        # (Renderer will still fall back to glyphs when a sprite/icon is missing.)
        try:
            setattr(self.renderer, "prefer_sprite_icons", bool(self.options.get("Tiles", True)))
            setattr(self.renderer, "prefer_terrain_tiles", bool(self.options.get("Tiles", True)))

        except Exception:
            pass

        # If music was re-enabled, re-resolve what should be playing
        if bool(self.options.get("Music", True)):
            self.sync_music_to_scene_stack()


    # ------------------------------------------------------------------ #
    # RNG factory used by scenes (e.g. DungeonScene) to spin up new RNGs.
    # ------------------------------------------------------------------ #
    # Music resolution from scene stack



    def _scene_music_request(self, scene: Scene) -> MusicRequest | None:
        """
        Convention-based music request:
          - scene.music_key: str
          - scene.music_playlist: list[str]
          - scene.music_hard_cut: bool
          - scene.music_fade_out_ms / scene.music_fade_in_ms: int
        """
        key = getattr(scene, "music_key", None)
        playlist = getattr(scene, "music_playlist", None)

        if not key and not playlist:
            return None

        return MusicRequest(
            key=key,
            playlist=playlist,
            loop=bool(getattr(scene, "music_loop", True)),
            hard_cut=bool(getattr(scene, "music_hard_cut", False)),
            fade_out_ms=int(getattr(scene, "music_fade_out_ms", 700)),
            fade_in_ms=int(getattr(scene, "music_fade_in_ms", 700)),
        )

    def _window_music_override(self) -> MusicRequest | None:
        """
        Topmost window scene may declare an override:
          - scene.music_override_key or scene.music_key
        """
        if not self.scene_stack:
            return None

        # Search from top down, but only consider windowed scenes first.
        for sc in reversed(self.scene_stack):
            if getattr(sc, "window_rect", None) is None:
                break
            override_key = getattr(sc, "music_override_key", None) or getattr(sc, "music_key", None)
            override_playlist = getattr(sc, "music_override_playlist", None)
            if override_key or override_playlist:
                return MusicRequest(
                    key=override_key,
                    playlist=override_playlist,
                    loop=bool(getattr(sc, "music_loop", True)),
                    hard_cut=bool(getattr(sc, "music_hard_cut", False)),
                    fade_out_ms=int(getattr(sc, "music_fade_out_ms", 700)),
                    fade_in_ms=int(getattr(sc, "music_fade_in_ms", 700)),
                )

        return None

    def sync_music_to_scene_stack(self) -> None:
        """
        Call this whenever scene stack changes (push/pop/set/open_window_scene),
        or when a high-level music override changes (context music, etc.).
        """
        # Keep audio flags in sync with options (cheap, safe)
        self.audio.set_music_enabled(bool(self.options.get("Music", True)))

        # If no scenes at all -> stop music.
        if not self.scene_stack:
            self.audio.set_music(None)
            return

        req = self.desired_music_request()
        if req is not None:
            self.audio.set_music(req)
        # else: no explicit request -> leave current music alone (sticky)



    def current_music_request(self) -> MusicRequest | None:
        """
        Return the music request that SHOULD be playing given the current scene stack,
        including context-aware overrides (spatial music, etc.).

        Used for event stingers: after the stinger ends, resume this.
        """
        return self.desired_music_request()

    def desired_music_request(self) -> MusicRequest | None:
        """
        Return the music request that should be playing *right now*, given:

        1) Window music override (if any)
        2) Pending transition override (prevents 1-tick flicker)
        3) Context override (spatial music, etc.)
        4) Base scene music request
        """
        override = self._window_music_override()
        if override is not None:
            return override

        # Pending transition override
        game = getattr(self, "current_game", None)
        pending_key = getattr(game, "pending_music_override_key", None) if game is not None else None
        pending_playlist = getattr(game, "pending_music_override_playlist", None) if game is not None else None
        if pending_key or pending_playlist:
            return MusicRequest(
                key=str(pending_key) if pending_key else None,
                playlist=list(pending_playlist) if pending_playlist else None,
                loop=True,
                hard_cut=False,
                fade_out_ms=150,
                fade_in_ms=150,
            )

        # Context override (e.g. Leviathan in-frame loop)
        if self._context_music_override is not None:
            return self._context_music_override

        # Base scene
        base_scene = None
        for sc in reversed(self.scene_stack):
            if getattr(sc, "window_rect", None) is None:
                base_scene = sc
                break
        if base_scene is None:
            return None
        return self._scene_music_request(base_scene)

    def set_context_music_override(self, req: MusicRequest | None) -> None:
        """Set/clear a context-driven music override (spatial triggers, etc.)."""
        prev = self._context_music_override
        self._context_music_override = req
        if prev == req:
            return

        # If we're currently interrupting (stinger), don't stomp it.
        try:
            if getattr(self.audio, "is_interrupting", None) and self.audio.is_interrupting():
                return
        except Exception:
            pass

        self.sync_music_to_scene_stack()




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
        self.sync_music_to_scene_stack()

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
        self.sync_music_to_scene_stack()

    def pop_scene(self, *, force: bool = False) -> None:
        """Pop the top scene off the stack.

        If the top scene supports a graceful pop animation, it may intercept
        the pop request by implementing begin_pop(manager) -> bool and returning True.
        In that case, the scene remains on the stack until it later calls
        manager.pop_scene(force=True).
        """
        if not self.scene_stack:
            return

        top = self.scene_stack[-1]
        if not force:
            begin = getattr(top, "begin_pop", None)
            if callable(begin):
                try:
                    if bool(begin(self)):
                        return
                except Exception:
                    # If the animation hook misbehaves, fall back to an immediate pop.
                    pass

        scene = self.scene_stack.pop()
        self.sync_music_to_scene_stack()

        # If this scene was windowed, pop matching rect too
        if hasattr(scene, "window_rect") and self.window_stack:
            self.window_stack.pop()

    def _force_pop_scene(self) -> None:
        """Internal helper: pop without allowing interception."""
        self.pop_scene(force=True)
    def set_scene(self, scene: Optional[Scene]) -> None:
        if scene is None:
            self.scene_stack.clear()
        else:
            self.scene_stack = [scene]
        self.sync_music_to_scene_stack()

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

                if self.audio.handle_pygame_event(event):
                    continue

                # Normal path: scene-specific handling
                scene.handle_event(event, self)

            # Update
            scene.update(dt, self)

            # Context-aware / spatial music triggers
            try:
                self.spatial_music.update(self, renderer)
            except Exception:
                pass

            self.audio.update()

            # Render
            # Make scene + global visual effects available to the renderer so the
            # entire frame (map + UI) can pick them up consistently.
            # Global effects live in renderer.visual_fx.global_effects (preferred),
            # but keep a fallback to the old attribute in case something stashes it there.
            global_eff = []
            try:
                global_eff = list(getattr(renderer.visual_fx, "global_effects", []) or [])
            except Exception:
                global_eff = list(getattr(renderer, "global_visual_effects", []) or [])
            # Scene effects affect entity rendering + window-local vibes.
            scene_eff = getattr(scene, "visual_effects", []) or []
            renderer.active_visual_effects = list(scene_eff)

            scene.render(renderer, self)

            # If renderer signals quit (legacy escape hatch), honor it.
            if getattr(renderer, "quit_requested", False):
                renderer.quit_requested = False
                return
