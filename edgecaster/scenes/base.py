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

from edgecaster.ui.widgets import (
    Widget,
    WidgetContext,
    VBox,
    LabelWidget,
    MultiLineLabelWidget,
    ScaledLabelWidget,
    ScaledMultiLineLabelWidget,
    ListWidget,
    WrappedMultiLineLabelWidget,
    WrappedListWidget,
)

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

        # IMPORTANT: if we're doing an offscreen stack rebuild for a popup background,
        # DO NOT present intermediate frames (prevents dungeon/inventory "blink").
        if getattr(renderer, "suspend_present", False):
            return

        if hasattr(renderer, "present"):
            renderer.present()
        else:
            pygame.display.flip()


class MenuFrameWidget(Widget):
    """
    Frame-style layout for menus:
      - banner at the top (optionally background)
      - optional body text block beneath banner
      - option list beneath body
      - footer pinned to the bottom
    """

    def __init__(
        self,
        *,
        banner: Widget | None,
        body: Widget | None,
        list_widget: ListWidget,
        footer: Widget | None,
        top_pad: int = 16,
        bottom_pad: int = 8,
        gap_after_banner: int = 8,
        gap_after_body: int = 10,
        min_list_height: int = 120,
        max_list_width: int = 520,
        max_body_width: int = 600,
        banner_is_background: bool = False,
    ) -> None:
        super().__init__()

        self.banner = banner
        self.body = body
        self.list_widget = list_widget
        self.footer = footer

        self.top_pad = top_pad
        self.bottom_pad = bottom_pad
        self.gap_after_banner = gap_after_banner
        self.gap_after_body = gap_after_body
        self.min_list_height = min_list_height
        self.max_list_width = max_list_width
        self.max_body_width = max_body_width
        self.banner_is_background = banner_is_background

        if self.banner is not None:
            self.add_child(self.banner)
        if self.body is not None:
            self.add_child(self.body)
        self.add_child(self.list_widget)
        if self.footer is not None:
            self.add_child(self.footer)

    def layout(self, ctx: WidgetContext) -> None:
        if self.rect.width == 0 or self.rect.height == 0:
            self.rect = ctx.surface.get_rect()

        # Pre-layout children so they have intrinsic sizes
        if self.banner:
            self.banner.layout(ctx)
        if self.body:
            self.body.layout(ctx)
        self.list_widget.layout(ctx)
        if self.footer:
            self.footer.layout(ctx)

        y = self.rect.y + self.top_pad

        # Inner usable width for centered content (prevents negative x centering in snug popups)
        inner_w = max(1, self.rect.width - 2 * self.top_pad)

        # Banner
        if self.banner and self.banner.visible:
            bx = self.rect.x + (self.rect.width - self.banner.rect.width) // 2

            if self.banner_is_background:
                by = self.rect.y + self.top_pad
                self.banner.rect.topleft = (bx, by)
                # y unchanged
            else:
                self.banner.rect.topleft = (bx, y)
                y = self.banner.rect.bottom + self.gap_after_banner

        # Body (centered, clamped width)
        if self.body and self.body.visible:
            # Give the body a usable width hint before layout for wrapping widgets
            body_w = min(self.max_body_width, inner_w)
            if body_w > 0:
                self.body.rect.width = body_w
            self.body.layout(ctx)

            bx = self.rect.x + (self.rect.width - self.body.rect.width) // 2
            self.body.rect.topleft = (bx, y)
            y = self.body.rect.bottom + self.gap_after_body

        # Footer
        footer_h = 0
        if self.footer and self.footer.visible:
            footer_h = self.footer.rect.height
            fx = self.rect.x + (self.rect.width - self.footer.rect.width) // 2
            fy = self.rect.bottom - self.bottom_pad - footer_h
            self.footer.rect.topleft = (fx, fy)

        # List fills remaining space
        avail_h = (self.rect.bottom - self.bottom_pad - footer_h) - y
        self.list_widget.rect.height = max(self.min_list_height, avail_h)

        # CRITICAL: clamp list width to panel inner width so it can't center offscreen
        desired_w = self.list_widget.rect.width or self.max_list_width
        desired_w = min(desired_w, self.max_list_width)
        desired_w = min(desired_w, inner_w)
        self.list_widget.rect.width = max(1, desired_w)

        lx = self.rect.x + (self.rect.width - self.list_widget.rect.width) // 2
        self.list_widget.rect.topleft = (lx, y)
        self.list_widget.layout(ctx)




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

    def get_body_text(self) -> Optional[str]:
        """Optional body text block shown between title/banner and the option list."""
        return None

    def wants_wrapped_choices(self) -> bool:
        """If True, use WrappedListWidget so long choices wrap instead of running off."""
        return False



    def wants_banner_background(self, ascii_art: str) -> bool:
        """
        If True, the banner does NOT consume vertical layout space and the option list
        may overlap it. Default heuristic: very tall ASCII banners become background.
        Scenes can override (e.g. Main Menu wants the title above options).
        """
        try:
            return ("\n" in ascii_art) and (len(ascii_art.splitlines()) >= 12)
        except Exception:
            return False


    # ---- widget tree ----------------------------------------------------

    def _build_widgets(self, items: list[Any]) -> None:
        ascii_art = self.get_ascii_art() or ""
        body_text = self.get_body_text() or ""

        # -----------------
        # Banner / Title
        # -----------------
        if ascii_art:
            if "\n" in ascii_art:
                lines = ascii_art.splitlines()
                if len(lines) >= 12:
                    self._banner = ScaledMultiLineLabelWidget(
                        ascii_art,
                        align="center",
                        line_spacing=0,
                        scale=2,
                    )
                else:
                    self._banner = MultiLineLabelWidget(
                        ascii_art,
                        align="center",
                        line_spacing=0,
                    )
            else:
                self._banner = ScaledLabelWidget(
                    ascii_art,
                    align="center",
                    scale=2,
                )
        else:
            self._banner = None

        # -----------------
        # Body (wrapped)
        # -----------------
        if body_text:
            # Wrap in pixel space; width hint is applied by MenuFrameWidget during layout.
            self._body = WrappedMultiLineLabelWidget(
                body_text,
                align="left",
                line_spacing=2,
                max_width_px=600,
            )
        else:
            self._body = None

        # -----------------
        # Choice list
        # -----------------
        if self.wants_wrapped_choices():
            self._list = WrappedListWidget(
                items,
                selected_index=self.selected_idx,
                on_activate=self._on_list_activate,
                padding=4,
                line_spacing=2,
                wrap_width_px=520,
            )
        else:
            self._list = ListWidget(
                items,
                selected_index=self.selected_idx,
                on_activate=self._on_list_activate,
                line_spacing=4,
                padding=4,
            )
        self._list.rect = pygame.Rect(0, 0, 0, 0)

        # -----------------
        # Footer
        # -----------------
        if self.FOOTER_TEXT:
            self._footer = LabelWidget(self.FOOTER_TEXT, align="center")
        else:
            self._footer = None

        banner_is_background = False
        if ascii_art:
            banner_is_background = self.wants_banner_background(ascii_art)

        self.root = MenuFrameWidget(
            banner=self._banner,
            body=self._body,
            list_widget=self._list,
            footer=self._footer,
            top_pad=16,
            bottom_pad=8,
            gap_after_banner=10,
            gap_after_body=12,
            min_list_height=120,
            max_list_width=520,
            max_body_width=600,
            banner_is_background=banner_is_background,
        )

        self.root.rect = pygame.Rect(0, 0, 0, 0)
        self._last_banner_text = ascii_art
        self._last_body_text = body_text


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

        body_text = self.get_body_text() or ""
        if getattr(self, "_last_body_text", "") != body_text:
            self._last_body_text = body_text
            # simplest and safest: rebuild
            self._build_widgets(list(self._last_items))

        super().update(dt_ms, manager)

    def draw_panel(self, panel: pygame.Surface, renderer, manager) -> None:
        eff = self._active_effects(renderer)

        # Background + border (legacy look)
        bg_rgb = apply_entity_color_effects(self, (10, 10, 20), eff)
        border_rgb = apply_entity_color_effects(self, (220, 220, 240), eff)

        panel.fill((*bg_rgb, 240))
        pygame.draw.rect(panel, (*border_rgb, 255), panel.get_rect(), 2)

        # Make draw_extra() panel-local
        try:
            orig_surface = renderer.surface
            orig_w = getattr(renderer, "width", None)
            orig_h = getattr(renderer, "height", None)

            renderer.surface = panel
            renderer.width = panel.get_width()
            renderer.height = panel.get_height()

            self.draw_extra(manager)
        finally:
            renderer.surface = orig_surface
            if orig_w is not None:
                renderer.width = orig_w
            if orig_h is not None:
                renderer.height = orig_h

        ctx = WidgetContext(
            surface=panel,
            game=getattr(manager, "current_game", None),
            scene=self,
            renderer=renderer,
        )

        self.root.rect = panel.get_rect()
        self.root.layout(ctx)

        # Sync selection state
        if self._list:
            if not self._keyboard_mode:
                self.selected_idx = self._list.selected_index
            else:
                self._list.selected_index = self.selected_idx
            self._list.ensure_visible(self.selected_idx)

        self.root.draw(ctx)

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
        """
        Draw whatever is "beneath" this popup.

        CLEAN FIX:
        Instead of snapshotting whatever happened to be on renderer.surface from the
        previous frame (which can include another popup), re-render the scene stack
        BELOW this popup into the surface once, then snapshot that.
        """
        # Snapshot beneath the popup once (but build it from a fresh render of stack below).
        if self._background is None:
            stack = getattr(manager, "scene_stack", None) or []
            try:
                idx = stack.index(self)
            except ValueError:
                idx = len(stack) - 1  # fallback: assume top

            renderer.surface.fill(renderer.bg)

            # Prevent intermediate stack renders from presenting to screen (avoids flicker).
            #
            # IMPORTANT: not all scenes are PanelScene-based (DungeonScene is not),
            # and DungeonScene.render() may call renderer._present() directly.
            # So we suppress BOTH renderer.present() and renderer._present() during
            # this offscreen stack rebuild.
            prev_suspend = getattr(renderer, "suspend_present", False)
            prev_present = getattr(renderer, "present", None)
            prev__present = getattr(renderer, "_present", None)

            renderer.suspend_present = True

            # Monkey-patch presenters to no-ops for the duration of snapshot rendering.
            if callable(prev_present):
                renderer.present = lambda *a, **k: None  # type: ignore[assignment]
            if callable(prev__present):
                renderer._present = lambda *a, **k: None  # type: ignore[assignment]

            try:
                for s in stack[:idx]:
                    try:
                        renderer.active_visual_effects = list(getattr(s, "visual_effects", []) or [])
                    except Exception:
                        pass
                    s.render(renderer, manager)
            finally:
                # Restore presentation behavior
                renderer.suspend_present = prev_suspend
                if prev_present is not None:
                    renderer.present = prev_present  # type: ignore[assignment]
                else:
                    try:
                        delattr(renderer, "present")
                    except Exception:
                        pass

                if prev__present is not None:
                    renderer._present = prev__present  # type: ignore[assignment]
                else:
                    try:
                        delattr(renderer, "_present")
                    except Exception:
                        pass


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
                manager.draw_widget_layer(
                    layer,
                    surface=renderer.surface,
                    game=getattr(self, "game", None),
                    scene=self,
                )

