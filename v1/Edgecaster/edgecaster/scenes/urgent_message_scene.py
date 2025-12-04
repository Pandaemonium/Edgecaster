from __future__ import annotations

from typing import Optional, Callable, List

import pygame

from .base import (
    Scene,
    MenuInput,
    MENU_ACTION_UP,
    MENU_ACTION_DOWN,
    MENU_ACTION_ACTIVATE,
    MENU_ACTION_BACK,
    MENU_ACTION_FULLSCREEN,
)

if False:  # type checking only
    from .manager import SceneManager  # pragma: no cover


class UrgentMessageScene(Scene):
    """
    Simple popup for urgent messages / events.

    Semantics:
    - Shows a title + multi-line message.
    - Presents one or more choices (default: ["Continue"]).
    - On selection, runs an optional callback and pops itself.

    For current use, we’ll mostly use it as:
        UrgentMessageScene(game, game.urgent_message)
    with a single "Continue" option.
    """

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
        self.game = game
        self.message = message
        self.title = title
        self.choices = choices or ["Continue"]
        self.on_choice = on_choice

        self.window_rect = window_rect
        self.selected_idx = 0

        self._background: Optional[pygame.Surface] = None
        self._menu_input = MenuInput()

        self.ui_font: Optional[pygame.font.Font] = None
        self.small_font: Optional[pygame.font.Font] = None

        # NEW: controls how Esc behaves.
        # - True  (default)  → Esc acts like Activate on current choice
        # - False (context)  → Esc just closes this popup
        self.back_confirms = back_confirms



    # ------------------------------------------------------------------ #
    # Helpers

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        If no window_rect was provided, use manager.compute_child_window_rect
        so we stack nicely with other popups.
        """
        if self.window_rect is not None:
            return
        self.window_rect = manager.compute_child_window_rect(scale=0.7)

    def _ensure_fonts(self, renderer) -> None:
        if self.ui_font is not None and self.small_font is not None:
            return

        # Slightly larger font for the header, smaller for body/choices.
        base_tile = getattr(renderer, "base_tile", 16)
        header_size = max(18, int(base_tile * 1.4))
        body_size = max(12, int(base_tile * 1.0))

        self.ui_font = pygame.font.SysFont("consolas", header_size)
        self.small_font = pygame.font.SysFont("consolas", body_size)


    def _compute_window_rect(
        self,
        renderer,
        ui_font: pygame.font.Font,
        small_font: pygame.font.Font,
        wrapped_lines: list[str],
    ) -> pygame.Rect:
        """Compute a centered window rect sized to the title, body, and choices."""
        # Width: max of title, body lines, choices, and hint
        max_width = 0

        if self.title:
            w, _ = ui_font.size(self.title)
            max_width = max(max_width, w)

        for line in wrapped_lines:
            w, _ = small_font.size(line if line else " ")
            max_width = max(max_width, w)

        for label in self.choices:
            w, _ = small_font.size("▶ " + label)
            max_width = max(max_width, w)

        hint_text = "Enter/Space to confirm • Esc to dismiss"
        w, _ = small_font.size(hint_text)
        max_width = max(max_width, w)

        margin_x = 24
        panel_w = max_width + 2 * margin_x

        min_w = int(renderer.width * 0.3)
        max_w = int(renderer.width * 0.8)
        panel_w = max(min_w, min(max_w, panel_w))

        # Height: title + body + choices + hint + margins
        y = 16
        if self.title:
            y += ui_font.get_height() + 12

        line_h = small_font.get_height() + 2
        num_body = max(1, len(wrapped_lines))  # ensure some vertical space
        y += num_body * line_h

        y += 16  # gap before choices
        choice_h = ui_font.get_height() + 6
        y += max(1, len(self.choices)) * choice_h

        y += small_font.get_height() + 20  # hint + bottom margin
        panel_h = y

        min_h = int(renderer.height * 0.2)
        max_h = int(renderer.height * 0.8)
        panel_h = max(min_h, min(max_h, panel_h))

        x = (renderer.width - panel_w) // 2
        y0 = (renderer.height - panel_h) // 2
        return pygame.Rect(x, y0, panel_w, panel_h)


    # ------------------------------------------------------------------ #
    # MenuScene-style hooks (for MenuInput)

    def get_menu_items(self, manager: "SceneManager") -> list[str]:  # type: ignore[name-defined]
        # Exactly the list of choices
        return self.choices

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        # Mark urgent as resolved if this came from Game.set_urgent.
        if hasattr(self.game, "urgent_resolved"):
            self.game.urgent_resolved = True
        if hasattr(self.game, "urgent_message"):
            self.game.urgent_message = None
        # Clear structured urgent metadata so it doesn't leak to future popups
        if hasattr(self.game, "urgent_title"):
            self.game.urgent_title = None
        if hasattr(self.game, "urgent_body"):
            self.game.urgent_body = None
        if hasattr(self.game, "urgent_choices"):
            self.game.urgent_choices = None

        # Optional callback for event choices / dialogue later
        if self.on_choice is not None:
            self.on_choice(index, manager)

        # Pop ourselves and return to whatever was underneath
        manager.pop_scene()
        return True



    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        """Handle 'back' / Esc key behaviour.

        By default, this behaves like confirming the currently selected
        choice (appropriate for classic urgent popups).

        For lightweight context menus (like the inventory item submenu),
        we can pass back_confirms=False to make Esc simply dismiss the
        popup without triggering any choice.
        """
        if getattr(self, "back_confirms", True):
            return self.on_activate(self.selected_idx, manager)

        # Just close the popup; do not fire any choice callback.
        manager.pop_scene()
        return True


    # ------------------------------------------------------------------ #
    # Main loop

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()
        menu = self._menu_input

        # Snapshot once so we don't accidentally include ourselves on re-entry
        if self._background is None:
            self._background = surface.copy()

        self._ensure_fonts(renderer)

        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font is not None and small_font is not None

        # Simple text wrapping (by character count; good enough for now)
        import textwrap

        wrap_width = 60
        wrapped_lines: list[str] = []
        for paragraph in self.message.splitlines():
            paragraph = paragraph.rstrip()
            if not paragraph:
                wrapped_lines.append("")
                continue
            wrapped_lines.extend(textwrap.wrap(paragraph, wrap_width))

        # If no rect was provided, compute a snug one based on content.
        if self.window_rect is None:
            self.window_rect = self._compute_window_rect(renderer, ui_font, small_font, wrapped_lines)

        rect = self.window_rect
        assert rect is not None


        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font is not None and small_font is not None

        # Simple text wrapping (by character count; good enough for now)
        import textwrap

        wrap_width = 60
        wrapped_lines = []
        for paragraph in self.message.splitlines():
            paragraph = paragraph.rstrip()
            if not paragraph:
                wrapped_lines.append("")
                continue
            wrapped_lines.extend(textwrap.wrap(paragraph, wrap_width))

        running = True

        def handle_action(action: Optional[str]) -> bool:
            if action is None:
                return False

            if action == MENU_ACTION_FULLSCREEN:
                renderer.toggle_fullscreen()
                return False

            if action == MENU_ACTION_UP:
                if self.choices:
                    self.selected_idx = (self.selected_idx - 1) % len(self.choices)
                return False

            if action == MENU_ACTION_DOWN:
                if self.choices:
                    self.selected_idx = (self.selected_idx + 1) % len(self.choices)
                return False

            if action == MENU_ACTION_BACK:
                return self.on_back(manager)

            if action == MENU_ACTION_ACTIVATE:
                return self.on_activate(self.selected_idx, manager)

            return False

        while running:
            # ----------------- EVENTS -----------------
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    action = menu.handle_keydown(event.key)
                    if handle_action(action):
                        running = False
                        break

                elif event.type == pygame.KEYUP:
                    menu.handle_keyup(event.key)

            if not running:
                break

            # Key repeat
            repeat_action = menu.update()
            if handle_action(repeat_action):
                break

            # ----------------- DRAW -----------------

            # Restore snapshot (includes dungeon + any other overlays)
            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            overlay = pygame.Surface((renderer.width, renderer.height), pygame.SRCALPHA)
            # Always dim the world for urgent messages
            overlay.fill((0, 0, 0, 180))

            panel_x, panel_y, panel_w, panel_h = rect
            logical_w, logical_h = panel_w, panel_h

            logical_surface = pygame.Surface((logical_w, logical_h), pygame.SRCALPHA)
            border_thickness = max(1, int(2 * (renderer.base_tile / 16)))

            # Panel background + border
            pygame.draw.rect(
                logical_surface,
                (20, 20, 40, 235),
                (0, 0, logical_w, logical_h),
            )
            pygame.draw.rect(
                logical_surface,
                (220, 220, 240, 240),
                (0, 0, logical_w, logical_h),
                border_thickness,
            )

            # Title (optional)
            title_y = 16
            y = title_y
            margin_x = 24

            if self.title:
                title_text = ui_font.render(self.title, True, renderer.fg)
                logical_surface.blit(
                    title_text,
                    ((logical_w - title_text.get_width()) // 2, title_y),
                )
                y = title_y + title_text.get_height() + 12

            # Message body
            for line in wrapped_lines:
                if not line:
                    y += small_font.get_height()
                    continue
                text = small_font.render(line, True, renderer.fg)
                logical_surface.blit(text, (margin_x, y))
                y += text.get_height() + 2

            # Choices
            y += 16
            for i, label in enumerate(self.choices):
                selected = i == self.selected_idx
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                text = small_font.render(prefix + label, True, color)
                logical_surface.blit(text, (margin_x, y))
                y += text.get_height() + 6


            # Hint
            hint = small_font.render(
                "",
                True,
                renderer.dim,
            )
            hint_y = logical_h - hint.get_height() - 10
            hint_x = (logical_w - hint.get_width()) // 2
            logical_surface.blit(hint, (hint_x, hint_y))

            overlay.blit(logical_surface, (panel_x, panel_y))
            surface.blit(overlay, (0, 0))
            renderer.present()
            clock.tick(60)
