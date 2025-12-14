from __future__ import annotations

import pygame
from typing import Optional
import math
from edgecaster.visuals import VisualProfile, apply_visual_panel, unproject_mouse
from edgecaster.visual_effects import build_visual_profile, apply_entity_color_effects, apply_surface_overlays


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
    # NEW: overlay widget layers requested by this scene
    overlay_layers: set[str] = set()
    visual_effects: list[str] = []


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


def _active_effects(scene, renderer) -> list[str]:
    """Return the effect-name stack that should apply to *everything* in this scene.

    Preference order:
    1) renderer.active_visual_effects (set by SceneManager each frame; includes globals + scene)
    2) scene.visual_effects
    """
    eff = getattr(renderer, "active_visual_effects", None)
    if isinstance(eff, (list, tuple)) and eff:
        return [str(x) for x in eff if x]
    eff = getattr(scene, "visual_effects", None)
    if isinstance(eff, str) and eff:
        return [eff]
    if isinstance(eff, (list, tuple)):
        return [str(x) for x in eff if x]
    return []

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


# ---------------------------------------------------------------------------
# PanelScene: widget-driven panel foundation (fullscreen or popup)
# ---------------------------------------------------------------------------

from edgecaster.ui.widgets import Widget, WidgetContext, VBox, LabelWidget, MultiLineLabelWidget, ListWidget
from edgecaster.visuals import VisualProfile, apply_visual_panel, unproject_mouse
from edgecaster.visual_effects import build_visual_profile, apply_entity_color_effects, apply_surface_overlays

import pygame
from typing import Optional, Any


class PanelScene(Scene):
    """
    New base: a Scene that owns a logical panel surface + rect, and a root widget tree.

    Responsibilities:
      - Create a logical panel surface (rect.size) each frame (or reuse)
      - Route input to widgets FIRST (UI priority)
      - Apply VisualProfile / named VisualEffects consistently via apply_visual_panel
      - Keep renderer draw-only (no UI state inside renderer)
    """

    uses_live_loop: bool = True

    def __init__(self, *, window_rect: Optional[pygame.Rect] = None) -> None:
        super().__init__()
        self.window_rect: Optional[pygame.Rect] = window_rect
        self.root: Widget = Widget()
        self._panel: Optional[pygame.Surface] = None

        # Input mode arbitration:
        # - mouse hover focus is "implicit"
        # - keyboard navigation overrides UNTIL the mouse moves
        self._keyboard_mode: bool = False
        self._last_mouse_pos: Optional[tuple[int, int]] = None

    # ---- sizing ---------------------------------------------------------

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.window_rect is not None:
            return
        r = manager.renderer
        self.window_rect = pygame.Rect(0, 0, r.width, r.height)

    def _get_panel(self) -> pygame.Surface:
        assert self.window_rect is not None
        size = self.window_rect.size
        if self._panel is None or self._panel.get_size() != size:
            self._panel = pygame.Surface(size, pygame.SRCALPHA)
        return self._panel

    # ---- visuals --------------------------------------------------------

    def _current_visual_profile(self) -> VisualProfile:
        base = self.visual_profile or VisualProfile()
        effects = getattr(self, "visual_effects", []) or []
        return build_visual_profile(base, effects)

    def _active_effects(self, renderer) -> list[str]:
        # Prefer the manager-fed renderer.active_visual_effects if present.
        eff = getattr(renderer, "active_visual_effects", None)
        if isinstance(eff, (list, tuple)) and eff:
            return [str(x) for x in eff if x]
        eff = getattr(self, "visual_effects", None)
        if isinstance(eff, str) and eff:
            return [eff]
        if isinstance(eff, (list, tuple)):
            return [str(x) for x in eff if x]
        return []

    # ---- input routing --------------------------------------------------

    def _panel_event(self, event, manager: "SceneManager"):
        """
        Panel-local event hook, called only if widgets didn't consume the event.
        Subclasses can override for non-UI hotkeys, etc.
        """
        return None

    def _to_panel_event(self, event, manager: "SceneManager"):
        """
        Convert event.pos (surface coords) into panel-local coords by inverting
        the current visual transform.
        """
        self._ensure_window_rect(manager)
        assert self.window_rect is not None

        # Convert display -> surface coords if renderer provides helper
        renderer = manager.renderer
        if hasattr(renderer, "_to_surface") and hasattr(event, "pos"):
            sx, sy = renderer._to_surface(event.pos)
        else:
            sx, sy = getattr(event, "pos", (None, None))

        if sx is None:
            return event

        visual = self._current_visual_profile()
        px, py = unproject_mouse((sx, sy), self.window_rect, visual)

        # Clone a minimal event-like object with panel-local pos
        # (pygame events are not meant to be mutated)
        class _E:
            pass
        e2 = _E()
        for k in dir(event):
            if k.startswith("_"):
                continue
            try:
                setattr(e2, k, getattr(event, k))
            except Exception:
                pass
        setattr(e2, "pos", (int(px), int(py)))
        return e2

    def handle_event(self, event, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Track whether keyboard currently "owns" navigation focus
        if event.type == pygame.KEYDOWN:
            self._keyboard_mode = True

        # If mouse moved, mouse takes over hover focus again
        if event.type == pygame.MOUSEMOTION:
            # display/surface position doesn't matter; any movement is enough
            pos = getattr(event, "pos", None)
            if pos is not None and pos != self._last_mouse_pos:
                self._last_mouse_pos = pos
                self._keyboard_mode = False

        # Route mouse events through panel-space before widgets see them
        e_for_widgets = event
        if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEWHEEL):
            if event.type != pygame.MOUSEWHEEL and hasattr(event, "pos"):
                e_for_widgets = self._to_panel_event(event, manager)

        # Build a WidgetContext on the panel surface for widget handling
        self._ensure_window_rect(manager)
        panel = self._get_panel()
        ctx = WidgetContext(surface=panel, game=getattr(manager, "current_game", None), scene=self, renderer=manager.renderer)

        # Widgets first
        handled = False
        try:
            handled = self.root.handle_event(e_for_widgets, ctx)
        except Exception:
            handled = False

        if handled:
            return

        # Fall through to scene-level input
        self._panel_event(event, manager)

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        self._ensure_window_rect(manager)
        panel = self._get_panel()
        ctx = WidgetContext(surface=panel, game=getattr(manager, "current_game", None), scene=self, renderer=manager.renderer)
        self.root.update(dt_ms, ctx)

    # ---- rendering ------------------------------------------------------

    def draw_underlay(self, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Hook: draw behind the panel (e.g. popup background snapshot + dim).
        Default: clear screen to renderer.bg.
        """
        renderer.surface.fill(renderer.bg)

    def draw_panel(self, panel: pygame.Surface, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Hook: draw panel background + widgets into `panel`.
        Default: fill panel to bg + draw widgets.
        """
        eff = self._active_effects(renderer)
        bg = apply_entity_color_effects(self, getattr(renderer, "bg", (0, 0, 0)), eff)
        panel.fill((*bg, 255))

        ctx = WidgetContext(surface=panel, game=getattr(manager, "current_game", None), scene=self, renderer=renderer)
        self.root.layout(ctx)
        self.root.draw(ctx)

        if eff:
            apply_surface_overlays(self, panel, panel.get_rect(), eff)

    def render(self, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        self._ensure_window_rect(manager)
        assert self.window_rect is not None

        # Underlay on real surface
        self.draw_underlay(renderer, manager)

        # Draw into logical panel
        panel = self._get_panel()
        self.draw_panel(panel, renderer, manager)

        # Apply visual transform and blit panel -> surface
        visual = self._current_visual_profile()
        apply_visual_panel(renderer.surface, panel, self.window_rect, visual)

        # NEW: actually show it on the display
        if hasattr(renderer, "present"):
            renderer.present()
        else:
            pygame.display.flip()



# ---------------------------------------------------------------------------
# GeneralMenuScene: widget-driven selection + confirm/cancel + scrolling
# ---------------------------------------------------------------------------

class GeneralMenuScene(PanelScene):
    """
    Widget-driven general menu base:
      - selection model
      - confirm/cancel behavior
      - scrolling via ListWidget
      - keyboard + mouse with arbitration:
          keyboard nav overrides until mouse moves
    """

    FOOTER_TEXT = MENU_FOOTER_HELP

    def __init__(self, *, window_rect: Optional[pygame.Rect] = None) -> None:
        super().__init__(window_rect=window_rect)

        self.selected_idx: int = 0
        self._list: Optional[ListWidget] = None
        self._banner: Optional[LabelWidget] = None
        self._footer: Optional[LabelWidget] = None

        self._menu_input = MenuInput()
        self._last_items: list[Any] = []
        self._last_banner_text: str = ""

        self._build_widgets([])

    # ---- hooks for subclasses ------------------------------------------

    def get_menu_items(self) -> list[Any]:
        raise NotImplementedError

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        raise NotImplementedError

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        manager.set_scene(None)
        return True

    def get_ascii_art(self) -> Optional[str]:
        return None

    def draw_extra(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        return None

    # ---- widget tree ----------------------------------------------------

    def _build_widgets(self, items: list[Any]) -> None:
        root = VBox(spacing=10, padding=16, align="center")
        root.rect = pygame.Rect(0, 0, 0, 0)

        ascii_art = self.get_ascii_art()
        if ascii_art:
            # Legacy banners/popup text are often multi-line.
            if "\n" in ascii_art:
                self._banner = MultiLineLabelWidget(ascii_art, align="center", line_spacing=0)
            else:
                self._banner = LabelWidget(ascii_art, align="center")
            root.add_child(self._banner)
        else:
            self._banner = None

        self._list = ListWidget(
            items,
            selected_index=self.selected_idx,
            on_activate=self._on_list_activate,
            line_spacing=6,
            padding=6,
        )
        # Default width; height will be set in draw_panel based on panel size.
        self._list.rect = pygame.Rect(0, 0, 520, 0)
        root.add_child(self._list)

        if self.FOOTER_TEXT:
            self._footer = LabelWidget(self.FOOTER_TEXT, align="center")
            root.add_child(self._footer)
        else:
            self._footer = None

        self.root = root
        self._last_banner_text = ascii_art or ""


    def _on_list_activate(self, idx: int, item: Any) -> None:
        self.selected_idx = idx
        # Activation is handled by scene-level path (so it can close/pop cleanly).
        # We intentionally do nothing here; mouse click will still be consumed.

    # ---- input ----------------------------------------------------------

    def _panel_event(self, event, manager: "SceneManager"):
        # Keyboard path (with repeat)
        if event.type == pygame.KEYDOWN:
            action = self._menu_input.handle_keydown(event.key)
            if action is not None:
                self._handle_action(action, manager)
                return

        if event.type == pygame.KEYUP:
            self._menu_input.handle_keyup(event.key)
            return

        # Mouse click: if ListWidget already handled it, great.
        # If not handled (e.g. click outside), ignore.

    def _handle_action(self, action: str, manager: "SceneManager") -> None:
        items = self.get_menu_items()
        n = max(1, len(items))

        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        if action == MENU_ACTION_UP:
            self.selected_idx = (self.selected_idx - 1) % n
        elif action == MENU_ACTION_DOWN:
            self.selected_idx = (self.selected_idx + 1) % n
        elif action == MENU_ACTION_BACK:
            # Let legacy code decide what to do. Only auto-close if we're still active.
            before = manager.scene_stack[-1] if getattr(manager, "scene_stack", None) else None
            self.on_back(manager)
            after = manager.scene_stack[-1] if getattr(manager, "scene_stack", None) else None
            # If on_back didn't change scenes and we're still on top, close.
            if after is before and after is self:
                if hasattr(manager, "pop_scene"):
                    manager.pop_scene()
                else:
                    manager.set_scene(None)
            return

        elif action == MENU_ACTION_ACTIVATE:
            # Legacy menus often call manager.set_scene(...) inside on_activate().
            # Only auto-close if we're still the active top scene afterward.
            before = manager.scene_stack[-1] if getattr(manager, "scene_stack", None) else None
            should_close = self.on_activate(self.selected_idx, manager)
            after = manager.scene_stack[-1] if getattr(manager, "scene_stack", None) else None

            if should_close and after is before and after is self:
                if hasattr(manager, "pop_scene"):
                    manager.pop_scene()
                else:
                    manager.set_scene(None)
            return


        # Sync selection into list widget and keep it visible
        if self._list is not None:
            self._list.selected_index = self.selected_idx
            self._list.ensure_visible(self.selected_idx)

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Key-repeat ticks
        repeat_action = self._menu_input.update()
        if repeat_action is not None:
            self._handle_action(repeat_action, manager)

        # Rebuild list items if changed
        items = self.get_menu_items()
        if items != self._last_items:
            self._last_items = list(items)
            if self._list is None:
                self._build_widgets(items)
            else:
                self._list.set_items(items)
                self._list.selected_index = max(0, min(self.selected_idx, max(0, len(items) - 1)))
                self.selected_idx = self._list.selected_index

        # Banner text may change even when the item list does not (e.g. dialogue nodes).
        banner_text = self.get_ascii_art() or ""
        if banner_text != self._last_banner_text:
            self._last_banner_text = banner_text

            if banner_text:
                if self._banner is None:
                    # simplest: rebuild the widget tree to include a banner
                    self._build_widgets(list(self._last_items))
                else:
                    wants_multiline = ("\n" in banner_text)
                    is_multiline = isinstance(self._banner, MultiLineLabelWidget)
                    if wants_multiline != is_multiline:
                        self._build_widgets(list(self._last_items))
                    else:
                        self._banner.text = banner_text
            else:
                if self._banner is not None:
                    # banner removed
                    self._build_widgets(list(self._last_items))


        super().update(dt_ms, manager)

    def draw_panel(self, panel: pygame.Surface, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Allow subclasses to draw extra decorations (panel-local)
        self.draw_extra(manager)

        # Background
        eff = self._active_effects(renderer)
        bg = apply_entity_color_effects(self, getattr(renderer, "bg", (0, 0, 0)), eff)
        panel.fill((*bg, 255))

        # Layout sizing: make list height fill most of the panel
        if self._list is not None:
            # Reserve room for banner/footer
            top_pad = 16
            bottom_pad = 16
            banner_h = 0
            footer_h = 0

            # Rough measurement via font heights; if banner is multi-line, scale accordingly.
            if self._banner is not None:
                font = getattr(renderer, "small_font", getattr(renderer, "font", None))
                h_line = font.get_height() if font else 16
                text = getattr(self._banner, "text", "") or ""
                n_lines = max(1, len(text.splitlines())) if text else 1
                line_spacing = getattr(self._banner, "line_spacing", 0)
                padding = getattr(self._banner, "padding", 0)
                banner_h = n_lines * h_line + max(0, n_lines - 1) * line_spacing + 2 * padding + 10

            if self._footer is not None:
                font = getattr(renderer, "small_font", getattr(renderer, "font", None))
                footer_h = (font.get_height() if font else 16) + 8


            list_h = max(120, panel.get_height() - (top_pad + bottom_pad + banner_h + footer_h + 40))
            self._list.rect.height = list_h

        # Widget draw
        ctx = WidgetContext(surface=panel, game=getattr(manager, "current_game", None), scene=self, renderer=renderer)

        # IMPORTANT: prevent VBox from auto-sizing to giant ASCII banner width,
        # which can push centered children (the list) off-screen.
        try:
            self.root.rect = pygame.Rect(0, 0, panel.get_width(), panel.get_height())
        except Exception:
            pass

        self.root.layout(ctx)


        # Keep selection consistent (mouse hover may have updated ListWidget)
        if self._list is not None:
            # If mouse is driving (not keyboard mode), accept hover selection
            if not self._keyboard_mode:
                self.selected_idx = self._list.selected_index
            else:
                self._list.selected_index = self.selected_idx
            self._list.ensure_visible(self.selected_idx)

        self.root.draw(ctx)

        # Overlays (particles, etc)
        if eff:
            apply_surface_overlays(self, panel, panel.get_rect(), eff)


# ---------------------------------------------------------------------------
# Legacy wrappers: MenuScene / PopupMenuScene now subclass GeneralMenuScene
# ---------------------------------------------------------------------------

class MenuScene(GeneralMenuScene):
    """
    Legacy fullscreen menu wrapper.
    Keeps the old subclass API:
      - get_menu_items()
      - on_activate()
      - on_back()
      - get_ascii_art()
      - draw_extra()
    """

    def __init__(self) -> None:
        super().__init__(window_rect=None)


class PopupMenuScene(GeneralMenuScene):
    """
    Legacy popup menu wrapper:
      - snapshot background once
      - optional dim overlay
      - fixed/preset sizing for now (scale), window_rect can be provided
    """

    def __init__(
        self,
        window_rect: Optional[pygame.Rect] = None,
        *,
        dim_background: bool = True,
        scale: float = 0.7,
    ) -> None:
        super().__init__(window_rect=window_rect)
        self.dim_background = dim_background
        self.popup_scale = scale
        self._background: Optional[pygame.Surface] = None

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.window_rect is not None:
            return

        renderer = manager.renderer
        base = pygame.Rect(0, 0, renderer.width, renderer.height)
        w = int(base.width * self.popup_scale)
        h = int(base.height * self.popup_scale)
        x = base.x + (base.width - w) // 2
        y = base.y + (base.height - h) // 2
        self.window_rect = pygame.Rect(x, y, w, h)

    def draw_underlay(self, renderer, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Snapshot beneath the popup once.
        if self._background is None:
            self._background = renderer.surface.copy()

        # Restore background snapshot
        if self._background is not None:
            renderer.surface.blit(self._background, (0, 0))
        else:
            renderer.surface.fill(renderer.bg)

        # Optional dim overlay
        if self.dim_background:
            overlay = pygame.Surface((renderer.width, renderer.height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 140))
            renderer.surface.blit(overlay, (0, 0))

        # Keep your existing “overlay widget layers” behavior (HUD over dim)
        layers = getattr(self, "overlay_layers", set())
        for layer in layers:
            if hasattr(manager, "draw_widget_layer"):
                manager.draw_widget_layer(layer, surface=renderer.surface, game=getattr(self, "game", None), scene=self)
