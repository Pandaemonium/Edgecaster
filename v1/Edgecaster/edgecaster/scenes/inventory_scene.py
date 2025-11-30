from __future__ import annotations

from typing import List

import pygame

from .base import Scene


class InventoryScene(Scene):
    """Dummy inventory menu that overlays on top of the dungeon."""

    def __init__(self, game) -> None:
        # Keep a reference to the current Game so we can inspect it later
        self.game = game

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        # For now it's just a dummy shell. Later you can build from self.game.
        options: List[str] = [
            "(inventory is empty)",
            "Close inventory",
        ]
        selected_idx = 1  # default cursor on "Close inventory"

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # Closing the window kills the whole game, as usual.
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    # i / Esc both close inventory and resume dungeon
                    if event.key in (pygame.K_ESCAPE, pygame.K_i):
                        manager.pop_scene()
                        return

                    if event.key in (pygame.K_UP, pygame.K_w):
                        selected_idx = (selected_idx - 1) % len(options)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        selected_idx = (selected_idx + 1) % len(options)

                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        choice = options[selected_idx]
                        if choice == "Close inventory":
                            manager.pop_scene()
                            return
                        # "(inventory is empty)" does nothing for now

            # -------- DRAW --------
            surface.fill(renderer.bg)

            title = renderer.font.render("Inventory", True, renderer.fg)
            surface.blit(
                title,
                ((renderer.width - title.get_width()) // 2, 80),
            )

            y = 160
            for idx, opt in enumerate(options):
                selected = (idx == selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                text = renderer.small_font.render(prefix + opt, True, color)
                surface.blit(text, (renderer.width // 2 - 180, y))
                y += text.get_height() + 10

            hint = renderer.small_font.render(
                "i / Esc to close and return to the dungeon",
                True,
                renderer.dim,
            )
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )

            pygame.display.flip()
            clock.tick(60)
