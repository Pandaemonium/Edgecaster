from __future__ import annotations

from typing import List, Dict

import pygame

from .base import Scene


class OptionsScene(Scene):
    """Dummy options menu with a couple of toggles to test hierarchy."""

    def __init__(self) -> None:
        # Remember which option was selected last time
        self.selected_idx = 0

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        # Use the *shared* options dict from the manager
        toggles: Dict[str, bool] = manager.options
        toggle_keys: List[str] = list(toggles.keys())

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # Pop this sub-scene, resume underlying menu (main menu)
                        manager.pop_scene()
                        return

                    if event.key in (pygame.K_UP, pygame.K_w):
                        self.selected_idx = (self.selected_idx - 1) % (len(toggles) + 1)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self.selected_idx = (self.selected_idx + 1) % (len(toggles) + 1)

                    # Toggle with left/right or enter (if on a toggle)
                    elif event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_RETURN, pygame.K_SPACE):
                        if self.selected_idx < len(toggles):
                            key = toggle_keys[self.selected_idx]
                            toggles[key] = not toggles[key]   # writes back into manager.options
                        else:
                            # Back selected: pop and resume previous scene
                            manager.pop_scene()
                            return

            # -------- DRAW --------
            surface.fill(renderer.bg)

            title_text = renderer.font.render("Options", True, renderer.fg)
            surface.blit(
                title_text,
                ((renderer.width - title_text.get_width()) // 2, 80),
            )

            # World seed display (read-only)
            seed_val = None
            if getattr(manager, "current_game", None):
                seed_val = getattr(manager.current_game, "fractal_seed", None)
            if seed_val is None and getattr(manager, "character", None):
                seed_val = getattr(manager.character, "seed", None)
            seed_line = f"World Seed: {seed_val if seed_val is not None else 'random'}"
            seed_text = renderer.small_font.render(seed_line, True, renderer.fg)
            surface.blit(seed_text, (renderer.width // 2 - 180, 130))

            y = 150
            for i, key in enumerate(toggle_keys):
                val = toggles[key]
                selected = (i == self.selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                status = "ON" if val else "OFF"
                line = f"{prefix}{key}: {status}"
                text = renderer.font.render(line, True, color)
                surface.blit(text, (renderer.width // 2 - 180, y))
                y += text.get_height() + 10

            # Back option
            selected = (self.selected_idx == len(toggles))
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            back_text = renderer.font.render(prefix + "Back to Main Menu", True, color)
            surface.blit(back_text, (renderer.width // 2 - 180, y + 20))

            hint = renderer.small_font.render(
                "↑/↓ or W/S to move, ←/→ or Enter to toggle, Esc/Back to return",
                True,
                renderer.dim,
            )
            surface.blit(
                hint,
                ((renderer.width - hint.get_width()) // 2, renderer.height - 40),
            )

            pygame.display.flip()
            clock.tick(60)
