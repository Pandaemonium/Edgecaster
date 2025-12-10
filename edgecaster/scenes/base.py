from __future__ import annotations

import pygame
from typing import Optional

from edgecaster.visuals import VisualProfile


# ---------------------------------------------------------------------------
# Base Scene
# ---------------------------------------------------------------------------


class Scene:
    """
    Abstract base for all scenes.

    Legacy scenes still implement run(); newer scenes can opt-in to the
    unified engine loop by setting uses_live_loop = True and overriding
    handle_event / update / render. The SceneManager will call the live
    hooks when available and otherwise fall back to run().
    """

    # Opt-in flag for the new engine-driven loop.
    uses_live_loop: bool = False
    visual_profile: VisualProfile | None = None

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Legacy entry point. When done, call manager.set_scene(...) to choose
        what comes next. New scenes should prefer the live-loop hooks.
        """
        raise NotImplementedError("Scene subclasses must implement run()")

    # ---- Live-loop hooks (optional) ------------------------------------ #
    def handle_event(self, event, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """Process a single pygame event. Override if uses_live_loop=True."""
        return None

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """Advance scene state by dt_ms. Override if uses_live_loop=True."""
        return None

    def render(self, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """Draw the scene. Override if uses_live_loop=True."""
        return None


# ---------------------------------------------------------------------------
# Standardized menu input helpers
# ---------------------------------------------------------------------------

# High-level logical actions for menus
MENU_ACTION_UP = "up"
MENU_ACTION_DOWN = "down"
MENU_ACTION_LEFT = "left"
MENU_ACTION_RIGHT = "right"
MENU_ACTION_ACTIVATE = "activate"
MENU_ACTION_BACK = "back"
MENU_ACTION_FULLSCREEN = "fullscreen"
# Shared footer hint for standard menus
# Shared footer hint for standard menus
MENU_FOOTER_HELP = (
    "W/S or ↑/↓ (numpad) to move, Enter/Space or click to select, Esc to go back, F11 fullscreen"
)



# Map raw Pygame keycodes to logical actions
_MENU_KEYMAP = {
    # Up
    pygame.K_UP: MENU_ACTION_UP,
    pygame.K_w: MENU_ACTION_UP,
    pygame.K_KP8: MENU_ACTION_UP,

    # Down
    pygame.K_DOWN: MENU_ACTION_DOWN,
    pygame.K_s: MENU_ACTION_DOWN,
    pygame.K_KP2: MENU_ACTION_DOWN,

    # Left
    pygame.K_LEFT: MENU_ACTION_LEFT,
    pygame.K_a: MENU_ACTION_LEFT,
    pygame.K_KP4: MENU_ACTION_LEFT,

    # Right
    pygame.K_RIGHT: MENU_ACTION_RIGHT,
    pygame.K_d: MENU_ACTION_RIGHT,
    pygame.K_KP6: MENU_ACTION_RIGHT,

    # Activate / confirm
    pygame.K_RETURN: MENU_ACTION_ACTIVATE,
    pygame.K_SPACE: MENU_ACTION_ACTIVATE,
    pygame.K_KP_ENTER: MENU_ACTION_ACTIVATE,

    # Back / cancel
    pygame.K_ESCAPE: MENU_ACTION_BACK,
}


class MenuInput:
    """
    Helper for standardized menu input with key-repeat.

    Typical usage inside a Scene:

        from .base import (
            Scene, MenuInput,
            MENU_ACTION_UP, MENU_ACTION_DOWN,
            MENU_ACTION_LEFT, MENU_ACTION_RIGHT,
            MENU_ACTION_ACTIVATE, MENU_ACTION_BACK,
            MENU_ACTION_FULLSCREEN,
        )

        class MyMenuScene(Scene):
            def run(self, manager):
                renderer = manager.renderer
                clock = pygame.time.Clock()
                menu = MenuInput()
                running = True

                def handle_action(action: str) -> None:
                    nonlocal running
                    ...

                while running:
                    for event in pygame.event.get():
                        if event.type == pygame.KEYDOWN:
                            action = menu.handle_keydown(event.key)
                            if action is not None:
                                handle_action(action)

                    repeat_action = menu.update()
                    if repeat_action is not None:
                        handle_action(repeat_action)

                    ...
    """

    def __init__(
        self,
        *,
        initial_delay: int = 300,
        slow_interval: int = 120,
        fast_interval: int = 40,
        fast_threshold: int = 900,
    ) -> None:
        self.repeat_key: Optional[int] = None
        self.repeat_start_ms = 0
        self.last_repeat_ms = 0

        self.initial_delay = initial_delay
        self.slow_interval = slow_interval
        self.fast_interval = fast_interval
        self.fast_threshold = fast_threshold

    @staticmethod
    def map_key(key: int) -> Optional[str]:
        if key == pygame.K_F11:
            return MENU_ACTION_FULLSCREEN
        return _MENU_KEYMAP.get(key)

    def handle_keydown(self, key: int) -> Optional[str]:
        """Call from your KEYDOWN handler. Returns a MENU_ACTION_* or None."""
        action = self.map_key(key)

        # Start repeating for directional keys
        if action in (
            MENU_ACTION_UP,
            MENU_ACTION_DOWN,
            MENU_ACTION_LEFT,
            MENU_ACTION_RIGHT,
        ):
            now = pygame.time.get_ticks()
            self.repeat_key = key
            self.repeat_start_ms = now
            self.last_repeat_ms = now
        else:
            # Non-directional key: stop repeating
            self.repeat_key = None

        return action

    def handle_keyup(self, key: int) -> None:
        """
        Call from your KEYUP handler so that repeats only happen while keys
        are actually held down.
        """
        if self.repeat_key == key:
            self.cancel_repeat()

    def update(self) -> Optional[str]:
        """Call once per frame; returns a repeated MENU_ACTION_* or None."""
        if self.repeat_key is None:
            return None

        now = pygame.time.get_ticks()
        action = self.map_key(self.repeat_key)

        if action not in (
            MENU_ACTION_UP,
            MENU_ACTION_DOWN,
            MENU_ACTION_LEFT,
            MENU_ACTION_RIGHT,
        ):
            # Only repeat directional actions
            self.repeat_key = None
            return None

        elapsed_since_start = now - self.repeat_start_ms
        if elapsed_since_start < self.initial_delay:
            return None

        elapsed_since_last = now - self.last_repeat_ms
        interval = (
            self.fast_interval
            if elapsed_since_start >= self.fast_threshold
            else self.slow_interval
        )

        if elapsed_since_last >= interval:
            self.last_repeat_ms = now
            return action

        return None

    def cancel_repeat(self) -> None:
        self.repeat_key = None


class MenuScene(Scene):
    """
    Generic menu scene with standard controls, key-repeat, and footer.

    Subclasses should override:
      - get_menu_items(self) -> list[str]
      - on_activate(self, index, manager) -> bool  (return True to close menu)
      - optionally on_back(self, manager) -> bool   (default: exit game)
      - optionally get_ascii_art(self) -> Optional[str]
      - optionally draw_extra(self, manager) -> None
    """

    FOOTER_TEXT = MENU_FOOTER_HELP

    def __init__(self) -> None:
        self.selected_idx = 0
        self._menu_input = MenuInput()
        # NEW: list of rects for hit-testing menu options with the mouse
        self._option_rects: list[pygame.Rect] = []

    # ---- hooks for subclasses ------------------------------------------------

    def get_menu_items(self) -> list[str]:
        raise NotImplementedError

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """
        Handle selecting an item. Return True if this menu should close.
        """
        raise NotImplementedError

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """
        Handle Esc / back. Default: exit the game loop entirely.
        Override in submenus to pop/close instead.
        """
        manager.set_scene(None)
        return True

    def get_ascii_art(self) -> Optional[str]:
        """Optional big title/banner above the menu."""
        return None

    def draw_extra(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Optional extra drawing hook (background decorations, etc.).
        Called before menu options and footer.
        """
        return None

    # ---- mouse helpers -------------------------------------------------------

    def _index_from_mouse_pos(self, pos: tuple[int, int]) -> int | None:
        """Return index of option under this mouse position, or None."""
        mx, my = pos
        for i, rect in enumerate(self._option_rects):
            if rect.collidepoint(mx, my):
                return i
        return None

    def _update_hover_from_mouse(self, pos: tuple[int, int]) -> None:
        idx = self._index_from_mouse_pos(pos)
        if idx is not None:
            self.selected_idx = idx


    # ---- main loop -----------------------------------------------------------

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()
        menu = self._menu_input
        running = True

        while running:
            options = self.get_menu_items()
            renderer = manager.renderer

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                elif event.type == pygame.KEYDOWN:
                    action = menu.handle_keydown(event.key)
                    if action is not None:
                        if self._handle_action(action, manager, options):
                            running = False
                            break

                elif event.type == pygame.KEYUP:
                    menu.handle_keyup(event.key)

                elif event.type == pygame.MOUSEMOTION:
                    # Convert from display coords to surface coords if needed
                    if hasattr(renderer, "_to_surface"):
                        mx, my = renderer._to_surface(event.pos)
                    else:
                        mx, my = event.pos
                    self._update_hover_from_mouse((mx, my))

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if hasattr(renderer, "_to_surface"):
                        mx, my = renderer._to_surface(event.pos)
                    else:
                        mx, my = event.pos
                    idx = self._index_from_mouse_pos((mx, my))
                    if idx is not None:
                        self.selected_idx = idx
                        # Behave like pressing Enter on this option
                        if self.on_activate(idx, manager):
                            running = False
                            break


            # Key-repeat
            repeat_action = menu.update()
            if repeat_action is not None:
                if self._handle_action(repeat_action, manager, options):
                    break

            # ---- drawing ----
            surface.fill(renderer.bg)
            self._draw_contents(manager, options)
            renderer.present()
            clock.tick(60)

    # ---- internal helpers ----------------------------------------------------

    def _handle_action(
        self,
        action: str,
        manager: "SceneManager",  # type: ignore[name-defined]
        options: list[str],
    ) -> bool:
        """
        Handle a logical menu action. Returns True if menu should close.
        """
        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return False

        if action == MENU_ACTION_UP:
            self.selected_idx = (self.selected_idx - 1) % len(options)
            return False

        if action == MENU_ACTION_DOWN:
            self.selected_idx = (self.selected_idx + 1) % len(options)
            return False

        if action == MENU_ACTION_BACK:
            return self.on_back(manager)

        if action == MENU_ACTION_ACTIVATE:
            return self.on_activate(self.selected_idx, manager)

        # Left/right or future extensions can go here if needed
        return False

    def _draw_contents(
        self,
        manager: "SceneManager",  # type: ignore[name-defined]
        options: list[str],
    ) -> None:
        renderer = manager.renderer
        surface = renderer.surface

        # Custom background/extra stuff
        self.draw_extra(manager)

        # Banner / ASCII art
        ascii_art = self.get_ascii_art()
        y = 80
        if ascii_art:
            for line in ascii_art.splitlines():
                if not line.strip():
                    y += renderer.font.get_height()
                    continue
                surf = renderer.font.render(line, True, renderer.fg)
                surface.blit(surf, ((renderer.width - surf.get_width()) // 2, y))
                y += renderer.font.get_height()

            # A little gap after banner before menu
            y += 20
            # Don't let a huge banner push the menu to the very bottom
            max_menu_start = renderer.height // 2 + 30
            if y > max_menu_start:
                y = max_menu_start
        else:
            # Fallback: put menu around vertical center
            y = renderer.height // 3

        # Menu options
        self._option_rects = []  # <-- rebuild each frame
        for idx, opt in enumerate(options):
            selected = idx == self.selected_idx
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            text_surf = renderer.font.render(prefix + opt, True, color)
            x = renderer.width // 2 - 110

            rect = text_surf.get_rect(topleft=(x, y))
            surface.blit(text_surf, rect.topleft)
            self._option_rects.append(rect)

            y += text_surf.get_height() + 10

        # Footer hint
        if self.FOOTER_TEXT:
            hint = renderer.small_font.render(self.FOOTER_TEXT, True, renderer.dim)
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )



class PopupMenuScene(MenuScene):
    """
    MenuScene variant that renders as a centered popup over a snapshot
    of the underlying screen.

    Features:
      - Uses the standard MenuInput (with numpad + key repeat).
      - Dimmed background (configurable).
      - Optional window_rect: if provided (e.g. via manager.open_window_scene),
        that rect is used; otherwise a centered rect is computed.
      - Esc or 'i' both trigger on_back().
    """

    def __init__(
        self,
        window_rect: Optional[pygame.Rect] = None,
        *,
        dim_background: bool = True,
        scale: float = 0.7,
    ) -> None:
        super().__init__()
        self.window_rect: Optional[pygame.Rect] = window_rect
        self.dim_background = dim_background
        self.popup_scale = scale
        self._background: Optional[pygame.Surface] = None

    # ------------------------------------------------------------------ #
    # Helpers

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        If no window_rect was provided, compute a centered rect at popup_scale
        relative to the full screen. If you want stack-aware rects, call
        manager.open_window_scene(...) so it passes one in.
        """
        if self.window_rect is not None:
            return

        renderer = manager.renderer
        base = pygame.Rect(0, 0, renderer.width, renderer.height)
        w = int(base.width * self.popup_scale)
        h = int(base.height * self.popup_scale)
        x = base.x + (base.width - w) // 2
        y = base.y + (base.height - h) // 2
        self.window_rect = pygame.Rect(x, y, w, h)

    # ------------------------------------------------------------------ #
    # Main loop

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()
        menu = self._menu_input
        running = True

        # Snapshot the screen beneath this popup once.
        if self._background is None:
            self._background = surface.copy()

        # Ensure we have a window rect
        self._ensure_window_rect(manager)
        assert self.window_rect is not None

        def handle_action(action: Optional[str]) -> bool:
            """
            Handle a logical menu action. Return True if this menu should close.
            """
            nonlocal running
            if action is None:
                return False

            if action == MENU_ACTION_FULLSCREEN:
                renderer.toggle_fullscreen()
                return False

            options = self.get_menu_items()
            num_items = max(1, len(options))

            if action == MENU_ACTION_UP:
                self.selected_idx = (self.selected_idx - 1) % num_items
                return False

            if action == MENU_ACTION_DOWN:
                self.selected_idx = (self.selected_idx + 1) % num_items
                return False

            if action == MENU_ACTION_BACK:
                if self.on_back(manager):
                    running = False
                    return True
                return False

            if action == MENU_ACTION_ACTIVATE:
                if self.on_activate(self.selected_idx, manager):
                    running = False
                    return True
                return False

            return False

        while running:
            options = self.get_menu_items()
            renderer = manager.renderer

            # ----------------- EVENTS -----------------
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    # Direct handling of confirm keys so we never depend
                    # on any platform-specific quirks in MenuInput.
                    if event.key in (
                        pygame.K_RETURN,
                        pygame.K_KP_ENTER,
                        pygame.K_SPACE,
                    ):
                        if self.on_activate(self.selected_idx, manager):
                            running = False
                            break
                        continue

                    # Direct handling of Esc as a back key.
                    if event.key == pygame.K_ESCAPE:
                        if self.on_back(manager):
                            running = False
                            break
                        continue

                    # Allow 'i' as an extra "back" key for inventory-like popups.
                    if event.key == pygame.K_i:
                        if self.on_back(manager):
                            running = False
                            break
                        continue

                    # Everything else goes through the standard menu keymap.
                    action = menu.handle_keydown(event.key)
                    if handle_action(action):
                        break

                elif event.type == pygame.KEYUP:
                    menu.handle_keyup(event.key)

                elif event.type == pygame.MOUSEMOTION:
                    if hasattr(renderer, "_to_surface"):
                        mx, my = renderer._to_surface(event.pos)
                    else:
                        mx, my = event.pos
                    self._update_hover_from_mouse((mx, my))

                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if hasattr(renderer, "_to_surface"):
                        mx, my = renderer._to_surface(event.pos)
                    else:
                        mx, my = event.pos
                    idx = self._index_from_mouse_pos((mx, my))
                    if idx is not None:
                        self.selected_idx = idx
                        # Same semantics as activate
                        if self.on_activate(idx, manager):
                            running = False
                            break
            # 💡 NEW: if we have been told to stop, don't draw another frame
            if not running:
                break


            # ----------------- DRAW -----------------

            # Restore background snapshot
            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            # Optional dim overlay
            if self.dim_background:
                overlay = pygame.Surface(
                    (renderer.width, renderer.height), pygame.SRCALPHA
                )
                overlay.fill((0, 0, 0, 140))
                surface.blit(overlay, (0, 0))

            # Draw panel within window_rect
            self._draw_panel(manager, options)

            renderer.present()
            clock.tick(60)

    # ------------------------------------------------------------------ #

    def _draw_panel(self, manager: "SceneManager", options: list[str]) -> None:  # type: ignore[name-defined]
        """
        Default popup panel drawing: simple framed box, ascii art title,
        menu options, and a standard footer inside window_rect.
        """
        renderer = manager.renderer
        surface = renderer.surface
        assert self.window_rect is not None

        rect = self.window_rect
        panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)

        # Panel background + border
        panel.fill((10, 10, 20, 240))
        pygame.draw.rect(
            panel,
            (220, 220, 240, 255),
            panel.get_rect(),
            2,
        )

        font = renderer.font
        small_font = renderer.small_font

        y = 16

        # ASCII art / title, if any
        ascii_art = self.get_ascii_art()
        if ascii_art:
            for line in ascii_art.splitlines():
                text = font.render(line, True, renderer.fg)
                x = (panel.get_width() - text.get_width()) // 2
                panel.blit(text, (x, y))
                y += text.get_height()
            y += 8

        # Menu items
        self._option_rects = []  # global screen-space rects
        for idx, label in enumerate(options):
            selected = (idx == self.selected_idx)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            text = font.render(prefix + label, True, color)
            local_x = (panel.get_width() - text.get_width()) // 2
            local_y = y

            # Local rect on the panel
            local_rect = text.get_rect(topleft=(local_x, local_y))
            panel.blit(text, local_rect.topleft)

            # Convert to global coords for hit-testing
            global_rect = local_rect.move(rect.left, rect.top)
            self._option_rects.append(global_rect)

            y += text.get_height() + 4

        # Footer
        footer = getattr(self, "FOOTER_TEXT", MENU_FOOTER_HELP)
        footer_text = small_font.render(footer, True, renderer.dim)
        fx = (panel.get_width() - footer_text.get_width()) // 2
        fy = panel.get_height() - footer_text.get_height() - 8
        panel.blit(footer_text, (fx, fy))

        surface.blit(panel, rect.topleft)
