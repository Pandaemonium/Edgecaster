from __future__ import annotations

from typing import Any, Optional

import pygame

from .base import PanelScene

from edgecaster import wish


class WishScene(PanelScene):
    """Dev-only text prompt to grant currency/items (Ctrl+W in dungeon)."""

    def __init__(
        self,
        game: Any,
        *,
        popup_scale: float = 0.55,
        dim_background: bool = True,
    ) -> None:
        super().__init__(window_rect=None)
        self.game = game
        self.popup_scale = float(popup_scale)
        self.dim_background = bool(dim_background)

        self.text: str = ""
        self.status: str = ""

        self._background: Optional[pygame.Surface] = None
        self._dim_surf: Optional[pygame.Surface] = None

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    def _panel_event(self, event, manager) -> None:
        if event.type != pygame.KEYDOWN:
            return

        if event.key == pygame.K_ESCAPE:
            manager.pop_scene()
            return

        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            ok, msg = wish.apply_wish(self.game, self.text)
            if ok:
                manager.pop_scene()
            else:
                self.status = msg
            return

        if event.key == pygame.K_BACKSPACE:
            if self.text:
                self.text = self.text[:-1]
            return

        if event.key == pygame.K_DELETE:
            self.text = ""
            return

        ch = getattr(event, "unicode", "")
        if isinstance(ch, str) and ch and ch.isprintable() and ch not in ("\r", "\n"):
            self.text += ch
            self.status = ""

    # ------------------------------------------------------------------ #
    # Popup underlay (dim current view)
    # ------------------------------------------------------------------ #

    def draw_underlay(self, renderer, manager) -> None:
        # Snapshot beneath the popup once (build it from a fresh render of stack below).
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

    def draw_panel(self, panel: pygame.Surface, renderer, manager) -> None:
        panel.fill((0, 0, 0, 0))

        w = int(panel.get_width() * self.popup_scale)
        h = max(160, int(panel.get_height() * 0.22))
        x = (panel.get_width() - w) // 2
        y = (panel.get_height() - h) // 2
        box = pygame.Rect(x, y, w, h)

        pygame.draw.rect(panel, (10, 10, 20, 245), box)
        pygame.draw.rect(panel, (220, 220, 240, 255), box, 2)

        title_font = getattr(renderer, "menu_title_font", getattr(renderer, "menu_font", getattr(renderer, "font")))
        font = getattr(renderer, "menu_font", getattr(renderer, "font", None))
        if font is None:
            font = title_font

        fg = getattr(renderer, "fg", (220, 220, 240))

        title_surf = title_font.render("Wish", True, fg)
        panel.blit(title_surf, (box.x + 14, box.y + 10))

        help_text = "Enter a wish (e.g. '500 bismuth' or 'Destabilizer')"
        help_surf = font.render(help_text, True, (180, 180, 200))
        panel.blit(help_surf, (box.x + 14, box.y + 54))

        entry = pygame.Rect(box.x + 14, box.y + 82, box.w - 28, 34)
        pygame.draw.rect(panel, (25, 25, 40, 255), entry)
        pygame.draw.rect(panel, (160, 160, 190, 255), entry, 1)

        cursor = "|" if (pygame.time.get_ticks() // 450) % 2 == 0 else " "
        shown = self.text if self.text else ""
        text_surf = font.render(shown + cursor, True, fg)
        panel.blit(text_surf, (entry.x + 8, entry.y + 6))

        if self.status:
            status_surf = font.render(self.status, True, (255, 120, 120))
            panel.blit(status_surf, (box.x + 14, entry.bottom + 10))

        footer = "Enter: grant  |  Esc: cancel"
        foot_surf = font.render(footer, True, (160, 160, 190))
        panel.blit(foot_surf, (box.x + 14, box.bottom - 34))

