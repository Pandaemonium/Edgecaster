from __future__ import annotations

from typing import List

import pygame

from .base import Scene
from edgecaster.game import Game


class WorldMapScene(Scene):
    """Placeholder world map scene.

    Shows a simple menu and hands control back to the dungeon with
    the same Game instance. Real map rendering can be plugged in later.
    """

    def __init__(self, game: Game, span: int = 16) -> None:
        # Keep a reference to the current Game so we can resume the same run.
        self.game = game
        self.span = span  # reserved for whatever your friend wants to do later

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Local import to avoid circular dependency at module import time
        from .dungeon import DungeonScene

        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        # Expose game so options / other overlays can still see it if needed
        manager.current_game = self.game

        # Dummy shell: one “do nothing” option and one “return” option
        options: List[str] = [
            "(insert world map logic here)",
            "Return to the dungeon",
        ]
        selected_idx = 1  # default cursor on “Return to the dungeon”

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # Closing the window kills the whole game, as usual.
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    # Esc / M: go back to the dungeon
                    if event.key in (pygame.K_ESCAPE, pygame.K_COMMA):
                        ds = DungeonScene()
                        ds.game = self.game      # reuse existing Game
                        manager.replace_scene(ds)
                        return

                    if event.key in (pygame.K_UP, pygame.K_w):
                        selected_idx = (selected_idx - 1) % len(options)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        selected_idx = (selected_idx + 1) % len(options)
                    elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        choice = options[selected_idx]
                        if choice == "Return to the dungeon":
                            ds = DungeonScene()
                            ds.game = self.game
                            manager.replace_scene(ds)
                            return
                        # The placeholder option doesn’t do anything yet.

            # -------- DRAW --------
            surface.fill(renderer.bg)

            title = renderer.font.render("World Map (placeholder)", True, renderer.fg)
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
                surface.blit(text, (renderer.width // 2 - 220, y))
                y += text.get_height() + 10

            hint = renderer.small_font.render(
                ", / Esc to return to the dungeon",
                True,
                renderer.dim,
            )
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )

            pygame.display.flip()
            clock.tick(60)
