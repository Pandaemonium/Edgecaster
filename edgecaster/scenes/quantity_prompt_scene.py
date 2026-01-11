"""Quantity prompt scene for selecting how many items to transfer/drop."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

import pygame

from .base import PanelScene

if TYPE_CHECKING:
    from edgecaster.game import Game
    from .manager import SceneManager


class QuantityPromptScene(PanelScene):
    """Popup for selecting quantity from a stack.

    Shows options:
    - All (N)
    - Some...
    - Cancel

    If "Some" is selected, switches to numeric input mode.
    Calls callback(qty) on confirmation, or callback(0) on cancel.
    """

    def __init__(
        self,
        game: "Game",
        total: int,
        callback: Callable[[int], None],
        *,
        title: str = "How many?",
        popup_scale: float = 0.45,
        dim_background: bool = True,
    ) -> None:
        super().__init__(window_rect=None)
        self.game = game
        self.total = max(1, int(total))
        self.callback = callback
        self.title = title
        self.popup_scale = float(popup_scale)
        self.dim_background = bool(dim_background)

        # Menu state
        self._selected = 0  # 0=All, 1=Some, 2=Cancel
        self._options = [f"All ({self.total})", "Some...", "Cancel"]

        # Numeric input state (when "Some" selected)
        self._input_mode = False
        self._input_text = ""
        self._input_error = ""

        self._background: Optional[pygame.Surface] = None
        self._dim_surf: Optional[pygame.Surface] = None

        # Track option rects for mouse clicks (set during draw)
        self._option_rects: list[pygame.Rect] = []
        self._box_rect: Optional[pygame.Rect] = None
        self._input_entry_rect: Optional[pygame.Rect] = None

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    def _panel_event(self, event: pygame.event.Event, manager: "SceneManager") -> None:
        # Handle mouse clicks
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = getattr(event, "pos", None)
            if pos is not None:
                self._handle_mouse_click(pos, manager)
            return

        if event.type != pygame.KEYDOWN:
            return

        if self._input_mode:
            self._handle_input_mode(event, manager)
        else:
            self._handle_menu_mode(event, manager)

    def _handle_mouse_click(self, pos: tuple[int, int], manager: "SceneManager") -> None:
        """Handle mouse click at the given position."""
        if self._input_mode:
            # In input mode, clicking outside the box goes back to menu
            if self._box_rect and not self._box_rect.collidepoint(pos):
                self._input_mode = False
                self._input_text = ""
                self._input_error = ""
            return

        # In menu mode, check if click is on an option
        for i, rect in enumerate(self._option_rects):
            if rect.collidepoint(pos):
                self._selected = i
                # Activate the selection
                if i == 0:
                    # All
                    self._finish(manager, self.total)
                elif i == 1:
                    # Some - switch to input mode
                    self._input_mode = True
                    self._input_text = ""
                    self._input_error = ""
                else:
                    # Cancel
                    self._finish(manager, 0)
                return

    def _handle_menu_mode(self, event: pygame.event.Event, manager: "SceneManager") -> None:
        """Handle keypresses in menu selection mode."""
        if event.key == pygame.K_ESCAPE:
            self._finish(manager, 0)
            return

        if event.key in (pygame.K_UP, pygame.K_w):
            self._selected = (self._selected - 1) % len(self._options)
            return

        if event.key in (pygame.K_DOWN, pygame.K_s):
            self._selected = (self._selected + 1) % len(self._options)
            return

        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            if self._selected == 0:
                # All
                self._finish(manager, self.total)
            elif self._selected == 1:
                # Some - switch to input mode
                self._input_mode = True
                self._input_text = ""
                self._input_error = ""
            else:
                # Cancel
                self._finish(manager, 0)
            return

    def _handle_input_mode(self, event: pygame.event.Event, manager: "SceneManager") -> None:
        """Handle keypresses in numeric input mode."""
        if event.key == pygame.K_ESCAPE:
            # Go back to menu mode
            self._input_mode = False
            self._input_text = ""
            self._input_error = ""
            return

        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            # Parse and validate
            txt = self._input_text.strip()
            if not txt:
                self._input_error = "Enter a number"
                return
            try:
                qty = int(txt)
            except ValueError:
                self._input_error = "Invalid number"
                return
            if qty < 1:
                self._input_error = "Must be at least 1"
                return
            if qty > self.total:
                self._input_error = f"Max is {self.total}"
                return
            self._finish(manager, qty)
            return

        if event.key == pygame.K_BACKSPACE:
            if self._input_text:
                self._input_text = self._input_text[:-1]
                self._input_error = ""
            return

        if event.key == pygame.K_DELETE:
            self._input_text = ""
            self._input_error = ""
            return

        # Accept digit characters
        ch = getattr(event, "unicode", "")
        if isinstance(ch, str) and ch.isdigit():
            # Limit length to prevent huge numbers
            if len(self._input_text) < 6:
                self._input_text += ch
                self._input_error = ""

    def _finish(self, manager: "SceneManager", qty: int) -> None:
        """Pop scene and invoke callback with selected quantity."""
        manager.pop_scene()
        if self.callback:
            self.callback(qty)

    # ------------------------------------------------------------------ #
    # Popup underlay (dim current view)
    # ------------------------------------------------------------------ #

    def draw_underlay(self, renderer: Any, manager: "SceneManager") -> None:
        # Snapshot beneath the popup once
        if self._background is None:
            stack = getattr(manager, "scene_stack", None) or []
            try:
                idx = stack.index(self)
            except ValueError:
                idx = len(stack) - 1

            renderer.surface.fill(renderer.bg)

            prev_suspend = getattr(renderer, "suspend_present", False)
            prev_present = getattr(renderer, "present", None)
            prev__present = getattr(renderer, "_present", None)

            renderer.suspend_present = True

            if callable(prev_present):
                renderer.present = (lambda: None)  # type: ignore[assignment]
            if callable(prev__present):
                renderer._present = (lambda: None)  # type: ignore[assignment]

            try:
                for sc in stack[:idx]:
                    if getattr(sc, "uses_live_loop", False):
                        try:
                            sc.render(renderer, manager)
                        except Exception:
                            pass
            finally:
                renderer.suspend_present = prev_suspend
                if callable(prev_present):
                    renderer.present = prev_present  # type: ignore[assignment]
                if callable(prev__present):
                    renderer._present = prev__present  # type: ignore[assignment]

            self._background = renderer.surface.copy()

        renderer.surface.blit(self._background, (0, 0))

        if self.dim_background:
            if self._dim_surf is None or self._dim_surf.get_size() != renderer.surface.get_size():
                self._dim_surf = pygame.Surface(renderer.surface.get_size(), pygame.SRCALPHA)
            self._dim_surf.fill((0, 0, 0, 170))
            renderer.surface.blit(self._dim_surf, (0, 0))

    # ------------------------------------------------------------------ #
    # Draw
    # ------------------------------------------------------------------ #

    def draw_panel(self, panel: pygame.Surface, renderer: Any, manager: "SceneManager") -> None:
        panel.fill((0, 0, 0, 0))

        w = int(panel.get_width() * self.popup_scale)
        h = max(180, int(panel.get_height() * 0.28))
        x = (panel.get_width() - w) // 2
        y = (panel.get_height() - h) // 2
        box = pygame.Rect(x, y, w, h)

        # Store box rect for mouse click handling
        self._box_rect = box

        # Background
        pygame.draw.rect(panel, (10, 10, 20, 245), box)
        pygame.draw.rect(panel, (220, 220, 240, 255), box, 2)

        title_font = getattr(renderer, "menu_title_font", getattr(renderer, "menu_font", getattr(renderer, "font")))
        font = getattr(renderer, "menu_font", getattr(renderer, "font", None))
        if font is None:
            font = title_font

        fg = getattr(renderer, "fg", (220, 220, 240))
        dim_fg = (160, 160, 190)
        highlight = (255, 255, 100)

        # Title
        title_surf = title_font.render(self.title, True, fg)
        panel.blit(title_surf, (box.x + 14, box.y + 10))

        if self._input_mode:
            self._draw_input_mode(panel, box, font, fg, dim_fg)
        else:
            self._draw_menu_mode(panel, box, font, fg, dim_fg, highlight)

    def _draw_menu_mode(
        self,
        panel: pygame.Surface,
        box: pygame.Rect,
        font: Any,
        fg: tuple,
        dim_fg: tuple,
        highlight: tuple,
    ) -> None:
        """Draw the menu selection UI."""
        y_offset = box.y + 50
        line_height = font.get_height() + 8

        # Clear and rebuild option rects for mouse hit testing
        self._option_rects = []

        for i, opt in enumerate(self._options):
            if i == self._selected:
                prefix = "> "
                color = highlight
            else:
                prefix = "  "
                color = fg

            text_surf = font.render(prefix + opt, True, color)
            text_x = box.x + 20
            text_y = y_offset

            # Store the clickable rect for this option (wider than text for easier clicking)
            opt_rect = pygame.Rect(box.x + 10, text_y - 2, box.width - 20, line_height)
            self._option_rects.append(opt_rect)

            panel.blit(text_surf, (text_x, text_y))
            y_offset += line_height

        # Footer
        footer = "Up/Down or Click: Select  |  Enter: Confirm  |  Esc: Cancel"
        foot_surf = font.render(footer, True, dim_fg)
        panel.blit(foot_surf, (box.x + 14, box.bottom - 30))

    def _draw_input_mode(
        self,
        panel: pygame.Surface,
        box: pygame.Rect,
        font: Any,
        fg: tuple,
        dim_fg: tuple,
    ) -> None:
        """Draw the numeric input UI."""
        # Prompt
        prompt = f"Enter amount (1-{self.total}):"
        prompt_surf = font.render(prompt, True, fg)
        panel.blit(prompt_surf, (box.x + 14, box.y + 50))

        # Input field
        entry = pygame.Rect(box.x + 14, box.y + 82, box.w - 28, 34)
        pygame.draw.rect(panel, (25, 25, 40, 255), entry)
        pygame.draw.rect(panel, (160, 160, 190, 255), entry, 1)

        cursor = "|" if (pygame.time.get_ticks() // 450) % 2 == 0 else " "
        shown = self._input_text if self._input_text else ""
        text_surf = font.render(shown + cursor, True, fg)
        panel.blit(text_surf, (entry.x + 8, entry.y + 6))

        # Error message
        if self._input_error:
            err_surf = font.render(self._input_error, True, (255, 120, 120))
            panel.blit(err_surf, (box.x + 14, entry.bottom + 8))

        # Footer
        footer = "Enter: Confirm  |  Esc: Back to menu"
        foot_surf = font.render(footer, True, dim_fg)
        panel.blit(foot_surf, (box.x + 14, box.bottom - 30))
