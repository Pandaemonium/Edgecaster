from __future__ import annotations

from typing import Optional, Callable, List

import pygame

from edgecaster.visuals import VisualProfile, apply_visual_panel
from edgecaster.ui.widgets import _wrap_text_px

from .base import PopupMenuScene

if False:  # type checking only
    from .manager import SceneManager  # pragma: no cover


class UrgentMessageScene(PopupMenuScene):
    """
    Popup-style urgent message dialog.

    Semantics:
    - Shows an optional title and a multi-line message.
    - Presents one or more choices (default: ["Continue..."]).
    - On selection, clears the Game's urgent_* fields and optionally
      invokes a callback.
    - Used both for "real" urgent events (level-up, death, etc.)
      and lightweight context menus (e.g. inventory item actions).
    """
    FOOTER_TEXT = ""

    FOOTER_TEXT = ""

    def __init__(
            self,
            game,
            message: str,
            *,
            title: str = "",
            choices: Optional[List[str]] = None,
            on_choice: Optional[Callable[[int, "SceneManager"], None]] = None,
            window_rect: Optional[pygame.Rect] = None,
            back_confirms: bool = True,
    ) -> None:
        # These must exist BEFORE super().__init__ (GeneralMenuScene builds widgets immediately).
        self.game = game
        self.message = message
        self.title = title

        self.choices = choices or ["Continue..."]
        self.on_choice = on_choice
        self.back_confirms = back_confirms

        # Visual knobs some menus expect to exist
        self.visual_effects: list[str] = []

        # IMPORTANT:
        # Do NOT use scale=0.0; if the snug rect hasn't been computed yet,
        # PopupMenuScene's fallback _ensure_window_rect() would create a 0x0 window.
        super().__init__(window_rect=window_rect, dim_background=True, scale=0.7)

        # We'll compute a snug rect lazily in _ensure_window_rect() once we have renderer fonts.

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Ensure we have a usable popup rect.

        In the new live-loop architecture, SceneManager does NOT call scene.run()
        for PanelScene-derived scenes, so we must do our 'snug sizing' here
        (render/update/input paths will call this).
        """
        if self.window_rect is not None and self.window_rect.width > 0 and self.window_rect.height > 0:
            return

        # Prefer snug measurement using real renderer font metrics.
        try:
            self.window_rect = self._compute_snug_rect(manager)
        except Exception:
            # Fallback: a reasonable scaled popup if measurement fails
            self.window_rect = manager.compute_child_window_rect(scale=self.popup_scale)

        # If we just changed rect/width, rebuild widgets so wrapping matches the new panel width.
        try:
            self._build_widgets(self.get_menu_items())
        except Exception:
            pass






    def get_ascii_art(self) -> str:
        # Title only; body is handled by get_body_text so it can wrap and size nicely.
        return (self.title or "").strip()

    def get_body_text(self) -> Optional[str]:
        return (self.message or "").strip()

    def wants_wrapped_choices(self) -> bool:
        return True


    @staticmethod
    def _wrap_text(text: str, *, width_chars: int = 64) -> list[str]:
        """Simple word-wrap by character count; preserves explicit newlines."""
        out: list[str] = []
        for raw_line in text.splitlines() or [""]:
            words = raw_line.split()
            if not words:
                out.append("")
                continue
            current = words[0]
            for word in words[1:]:
                test = current + " " + word
                if len(test) <= width_chars:
                    current = test
                else:
                    out.append(current)
                    current = word
            out.append(current)
        return out








    # ------------------------------------------------------------------ #
    # Window rect helpers




    def _compute_snug_rect(self, manager: "SceneManager") -> pygame.Rect:  # type: ignore[name-defined]
        r = manager.renderer
        screen_w, screen_h = r.width, r.height

        pad_x = 28
        pad_y = 24
        border = 2
        gap_title_body = 10
        gap_body_choices = 14

        # Fonts: title is "scaled x2" of the normal menu font.
        font = getattr(r, "font", getattr(r, "small_font"))
        title_scale = 2
        title = (self.title or "").strip()
        body = (self.message or "").strip()
        choices = list(self.choices or ["Continue..."])

        # Choose a target text width (clamped to screen)
        max_text_w = min(760, int(screen_w * 0.78))
        max_text_w = max(360, max_text_w)

        # Measure title
        title_w0, title_h0 = font.size(title or " ")
        title_w = title_w0 * title_scale
        title_h = title_h0 * title_scale

        # Wrap and measure body
        body_lines = _wrap_text_px(font, body, max_text_w) if body else []
        body_w = max((font.size(line)[0] for line in body_lines), default=0)
        body_h = len(body_lines) * font.get_height() + max(0, len(body_lines) - 1) * 2

        # Wrap and measure choices (with room for prefix)
        prefix_w = font.size("▶ ")[0]
        choice_lines_total = 0
        choice_w = 0
        for c in choices:
            lines = _wrap_text_px(font, str(c), max(1, max_text_w - prefix_w))
            choice_lines_total += max(1, len(lines))
            for line in lines:
                choice_w = max(choice_w, font.size(line)[0] + prefix_w)
        choice_h = choice_lines_total * (font.get_height() + 2)

        content_w = max(title_w, body_w, choice_w, 280)
        content_h = 0
        if title:
            content_h += title_h
        if body_lines:
            if title:
                content_h += gap_title_body
            content_h += body_h
        if choices:
            if body_lines:
                content_h += gap_body_choices
            content_h += choice_h

        win_w = content_w + pad_x * 2 + border * 2
        win_h = content_h + pad_y * 2 + border * 2

        # Clamp to screen (still “snug” but never off-screen)
        win_w = min(win_w, int(screen_w * 0.92))
        win_h = min(win_h, int(screen_h * 0.92))

        rect = pygame.Rect(0, 0, int(win_w), int(win_h))
        rect.center = (screen_w // 2, screen_h // 2)
        return rect


    # ------------------------------------------------------------------ #

    def _close_self(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Close this popup. Prefer removing *this* scene from the stack, even if
        other scenes were pushed on top of it by the on_choice callback.
        """
        stack = getattr(manager, "scene_stack", None)

        if stack is not None and stack:
            # Normal case: we're on top
            if stack[-1] is self and hasattr(manager, "pop_scene"):
                manager.pop_scene()
            else:
                # on_choice may have pushed a new scene above us (e.g. DialoguePopupScene).
                # In that case, surgically remove *this* scene from the stack and
                # leave whatever got pushed on top.
                if self in stack:
                    stack.remove(self)

                    # If this popup was ever opened as a windowed scene, keep
                    # window_stack in sync too.
                    win_stack = getattr(manager, "window_stack", None)
                    if (
                        win_stack is not None
                        and hasattr(self, "window_rect")
                        and self.window_rect in win_stack
                    ):
                        try:
                            win_stack.remove(self.window_rect)
                        except ValueError:
                            pass
            return

        # Fallback: no stack information, just clear the current scene.
        if hasattr(manager, "set_scene"):
            manager.set_scene(None)


    # MenuScene-style hooks

    def get_menu_items(self) -> list[str]:
        # Called with no args by MenuScene.run()
        return self.choices

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """
        Handle confirm (Enter/Space/Left/Right depending on binding).

        New behavior: close this popup *before* running the callback, so that
        any scenes opened by the callback sit at the same stack level and
        don't snapshot an already-dimmed/background-with-popup screen.
        """
        # Mark urgent as resolved if this came from Game.set_urgent.
        if hasattr(self.game, "urgent_resolved"):
            self.game.urgent_resolved = True
        if hasattr(self.game, "urgent_message"):
            self.game.urgent_message = None

        # Clear structured urgent metadata so it doesn't leak.
        if hasattr(self.game, "urgent_title"):
            self.game.urgent_title = None
        if hasattr(self.game, "urgent_body"):
            self.game.urgent_body = None
        if hasattr(self.game, "urgent_choices"):
            self.game.urgent_choices = None

        # NEW: restore the pre-popup background so follow-up popups
        # (like dialogue trees) snapshot a clean frame.
        if hasattr(manager, "renderer") and getattr(self, "_background", None) is not None:
            manager.renderer.surface.blit(self._background, (0, 0))

        # Close this popup first so any scenes opened by the callback
        # become the new top-of-stack and capture a clean background.
        self._close_self(manager)

        # Optional callback for event choices / dialogue later.
        if self.on_choice is not None:
            self.on_choice(index, manager)

        return True  # tell PopupMenuScene.run to stop



    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """
        Handle 'back' / Esc key behaviour.
        """
        if getattr(self, "back_confirms", True):
            # Same semantics as activate (including closing).
            return self.on_activate(self.selected_idx, manager)
        else:
            # Just close the popup; do NOT call on_activate.
            self._close_self(manager)
            return True

    # ------------------------------------------------------------------ #
    # Text layout helpers

    @staticmethod
    def _wrap_text_px(
            text: str,
            font: pygame.font.Font,
            max_width: int,
    ) -> List[str]:
        """
        Word-wrapping in pixel space using the given font.
        Respects explicit newlines in `text`.
        """
        lines: List[str] = []

        for raw_line in text.splitlines() or [""]:
            words = raw_line.split()
            if not words:
                lines.append("")
                continue

            current = words[0]
            for word in words[1:]:
                test = current + " " + word
                if font.size(test)[0] <= max_width:
                    current = test
                else:
                    lines.append(current)
                    current = word
            lines.append(current)

        return lines



