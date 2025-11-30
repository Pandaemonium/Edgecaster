from __future__ import annotations

from typing import List

import pygame

from .base import Scene


class SavedGamesScene(Scene):
    """Dummy saved-games menu to test scene transitions."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        # For now, just a single 'Back' option; saves are empty.
        options: List[str] = ["Back to Main Menu"]
        selected_idx = 0

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE,):
                        # Back to main menu
                        from .main_menu import MainMenuScene
                        manager.set_scene(MainMenuScene())
                        return

                    if event.key in (pygame.K_UP, pygame.K_w, pygame.K_DOWN, pygame.K_s):
                        # Only one option, but keep structure for future extension
                        selected_idx = 0

                    if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        # Only option is Back
                        from .main_menu import MainMenuScene
                        manager.set_scene(MainMenuScene())
                        return

            # -------- DRAW --------
            surface.fill(renderer.bg)

            title_text = renderer.font.render("Saved Games", True, renderer.fg)
            surface.blit(
                title_text,
                ((renderer.width - title_text.get_width()) // 2, 80),
            )

            # Dummy "no saves" info
            info_text = renderer.small_font.render(
                "No saved games found.", True, renderer.dim
            )
            surface.blit(
                info_text,
                ((renderer.width - info_text.get_width()) // 2, 140),
            )

            # Back option
            y = 220
            selected = (selected_idx == 0)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            text = renderer.font.render(prefix + "Back to Main Menu", True, color)
            surface.blit(text, (renderer.width // 2 - 160, y))

            hint = renderer.small_font.render(
                "Enter/Space to go back, Esc also returns to main menu",
                True,
                renderer.dim,
            )
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )

            pygame.display.flip()
            clock.tick(60)
