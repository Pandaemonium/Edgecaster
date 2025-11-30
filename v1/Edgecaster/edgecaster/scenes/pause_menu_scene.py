from __future__ import annotations

from typing import List

import pygame

from .base import Scene
from .options_scene import OptionsScene
from .main_menu import MainMenuScene


class PauseMenuScene(Scene):
    """Pause overlay: Resume, Options, Quit to Main Menu, Quit Game."""

    def __init__(self) -> None:
        self.selected_idx = 0

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        options: List[str] = [
            "Resume",
            "Options",
            "Quit to Main Menu",
            "Quit Game",
        ]

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # ESC = quick resume
                        manager.pop_scene()
                        return

                    if event.key in (pygame.K_UP, pygame.K_w):
                        self.selected_idx = (self.selected_idx - 1) % len(options)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self.selected_idx = (self.selected_idx + 1) % len(options)
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        choice = options[self.selected_idx]
                        if choice == "Resume":
                            manager.pop_scene()
                            return
                        elif choice == "Options":
                            # Stack Options on top of Pause; when Options pops,
                            # we land back on the Pause menu.
                            manager.push_scene(OptionsScene())
                            return
                        elif choice == "Quit to Main Menu":
                            manager.set_scene(MainMenuScene())
                            return
                        elif choice == "Quit Game":
                            manager.set_scene(None)
                            return

            # -------- DRAW --------
            surface.fill(renderer.bg)

            title = renderer.font.render("Paused", True, renderer.fg)
            surface.blit(
                title,
                ((renderer.width - title.get_width()) // 2, 80),
            )

            y = 150
            for idx, opt in enumerate(options):
                selected = (idx == self.selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                text = renderer.font.render(prefix + opt, True, color)
                surface.blit(text, (renderer.width // 2 - 180, y))
                y += text.get_height() + 10

            hint = renderer.small_font.render(
                "↑/↓ or W/S to move • Enter/Space to select • Esc to resume",
                True,
                renderer.dim,
            )
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )

            pygame.display.flip()
            clock.tick(60)
