from __future__ import annotations

from typing import Optional, Callable, List

import pygame

from edgecaster.visuals import VisualProfile, apply_visual_panel

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
        # Let PopupMenuScene handle snapshot, dimming, generic layout, etc.
        super().__init__(window_rect=window_rect, dim_background=True, scale=0.7)

        self.game = game
        self.message = message
        self.title = title
        self.choices = choices or ["Continue..."]
        self.on_choice = on_choice

        # Controls how Esc behaves:
        # - True  → Esc acts like Activate on the current choice
        # - False → Esc just closes the popup
        self.back_confirms = back_confirms


    # NEW: ensure the Game has a link to the SceneManager while this popup lives
    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Let dialogue helpers see the real SceneManager instead of
        # falling back to the legacy nested-urgent behaviour.
        if getattr(self.game, "scene_manager", None) is None:
            self.game.scene_manager = manager  # type: ignore[attr-defined]
        super().run(manager)


    # ------------------------------------------------------------------ #
    # Window rect helpers

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """
        Override PopupMenuScene's default to integrate with the
        window_stack via manager.compute_child_window_rect, so urgent
        popups stack nicely with other windows.
        """
        if self.window_rect is not None:
            return
        self.window_rect = manager.compute_child_window_rect(scale=self.popup_scale)

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
    def _wrap_text(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> List[str]:
        """
        Simple word-wrapping in pixel space using the given font.
        Respects explicit newlines in `text`.
        """
        lines: List[str] = []

        for raw_line in text.splitlines() or [""]:
            words = raw_line.split()
            if not words:
                # Preserve blank lines
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

    # ------------------------------------------------------------------ #
    # Drawing - override PopupMenuScene's default panel layout

    def _draw_panel(self, manager: "SceneManager", options: list[str]) -> None:  # type: ignore[name-defined]
        """
        Custom panel for urgent messages.

        Layout:
          [Title  (big)]
          [Wrapped body text (small)...]
          [Choices (small)...]
          (no footer)
        """
        assert self.window_rect is not None

        rect = self.window_rect
        renderer = manager.renderer

        # Fonts: title larger, body + choices same smaller size
        title_font = renderer.font
        body_font = renderer.small_font

        padding_x = 24
        padding_y = 16

        panel = pygame.Surface(rect.size, pygame.SRCALPHA)

        # Panel background + border (same visual language as other popups)
        panel.fill((10, 10, 20, 240))
        pygame.draw.rect(
            panel,
            (220, 220, 240, 255),
            panel.get_rect(),
            2,
        )

        y = padding_y

        # Title (biggest font)
        if self.title:
            title_surf = title_font.render(self.title, True, renderer.player_color)
            tx = (panel.get_width() - title_surf.get_width()) // 2
            panel.blit(title_surf, (tx, y))
            y += title_surf.get_height() + 8

        # Body text, re-wrapped to the *actual* panel width
        max_body_width = panel.get_width() - 2 * padding_x
        if self.message:
            body_lines = self._wrap_text(self.message, body_font, max_body_width)
        else:
            body_lines = []

        for line in body_lines:
            line_text = line if line else " "
            body_surf = body_font.render(line_text, True, renderer.fg)
            bx = (panel.get_width() - body_surf.get_width()) // 2
            panel.blit(body_surf, (bx, y))
            y += body_surf.get_height() + 2

        if body_lines:
            y += 12
        else:
            y += 4

        # Choices (same size as body text)
        self._option_rects = []  # panel-local rects used for mouse hit-testing
        for idx, label in enumerate(options):
            selected = (idx == self.selected_idx)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            text_surf = body_font.render(prefix + label, True, color)

            local_x = (panel.get_width() - text_surf.get_width()) // 2
            local_y = y

            local_rect = text_surf.get_rect(topleft=(local_x, local_y))
            panel.blit(text_surf, local_rect.topleft)

            # Store panel-local rects for hit-testing via unprojected mouse coords
            self._option_rects.append(local_rect)

            y += text_surf.get_height() + 4

        # Note: no footer hint for urgent messages.

        visual = self.visual_profile or VisualProfile()
        apply_visual_panel(renderer.surface, panel, rect, visual)
