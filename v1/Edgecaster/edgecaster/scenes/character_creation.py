from __future__ import annotations

from typing import List

import pygame

from .base import Scene, CharacterInfo


CHAR_CLASSES: List[str] = [
    "Kochbender",
    "Sierpinski Caster",
    "Strange Attractor",
    "Weaver",
]
CLASS_DESCRIPTIONS = {
    "Kochbender": "Fractal line mage. Bends and subdivides runes into sharp, angular spell patterns.",
    "Sierpinski Caster": "Cellular automaton hexer. Grows lace-like beams and cascading triangular fields.",
    "Strange Attractor": "Orb dancer. Commands chaotic, orbiting projectiles that swirl unpredictably.",
    "Weaver": "Spatial crafter. Weaves recursive carpets and mazes to control terrain and enemy movement.",
}

class CharacterCreationScene(Scene):
    """In-window character creation using the existing ASCII renderer."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        name = ""
        selected_idx = 0
        stage = "name"  # or "class"
        running = True

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    # Exit the whole game.
                    manager.set_scene(None)
                    return
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # Exit the whole game from the menu.
                        manager.set_scene(None)
                        return

                    if stage == "name":
                        # Handle typing the name.
                        if event.key == pygame.K_RETURN:
                            if not name:
                                name = "Edgecaster"
                            stage = "class"
                        elif event.key == pygame.K_BACKSPACE:
                            name = name[:-1]
                        else:
                            ch = event.unicode
                            # Simple printable-char filter; you can refine this.
                            if ch.isprintable() and not ch.isspace():
                                if len(name) < 16:
                                    name += ch

                    elif stage == "class":
                        # Navigate class list and confirm.
                        if event.key in (pygame.K_UP, pygame.K_w):
                            selected_idx = (selected_idx - 1) % len(CHAR_CLASSES)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            selected_idx = (selected_idx + 1) % len(CHAR_CLASSES)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            chosen_class = CHAR_CLASSES[selected_idx]
                            manager.character = CharacterInfo(name=name or "Edgecaster",
                                                              char_class=chosen_class)
                            from .dungeon import DungeonScene  # local import to avoid cycles
                            manager.set_scene(DungeonScene())
                            return

            # --- Draw menu frame ---
            surface.fill(renderer.bg)

            # Title
            title_text = renderer.font.render("EDGECASTER", True, renderer.fg)
            surface.blit(
                title_text,
                (
                    (renderer.width - title_text.get_width()) // 2,
                    40,
                ),
            )

            # Name prompt
            name_label = renderer.small_font.render("Name:", True, renderer.fg)
            surface.blit(name_label, (80, 140))
            name_display = name if stage == "name" or name else "Edgecaster"
            name_text = renderer.small_font.render(name_display, True, renderer.player_color)
            surface.blit(name_text, (80 + name_label.get_width() + 12, 140))

            if stage == "name":
                hint = renderer.small_font.render("Type your name, Enter to continue", True, renderer.dim)
                surface.blit(hint, (80, 170))
            else:
                hint = renderer.small_font.render("Choose your class (↑/↓, Enter)", True, renderer.dim)
                surface.blit(hint, (80, 170))

            # Class list
            y = 220
            for idx, cls in enumerate(CHAR_CLASSES):
                selected = (idx == selected_idx and stage == "class")
                color = renderer.fg if not selected else renderer.player_color
                prefix = "▶ " if selected else "   "
                text = renderer.small_font.render(prefix + cls, True, color)
                surface.blit(text, (100, y))
                y += text.get_height() + 8
                
            # Class description for the currently selected class
            if stage == "class":
                desc = CLASS_DESCRIPTIONS.get(CHAR_CLASSES[selected_idx], "")
                if desc:
                    max_width = renderer.width - 160
                    dx = 80
                    dy = y + 10

                    # Simple word-wrap
                    words = desc.split()
                    line_words: List[str] = []
                    for word in words:
                        test_line = (" ".join(line_words + [word])).strip()
                        surf = renderer.small_font.render(test_line, True, renderer.dim)
                        if surf.get_width() > max_width and line_words:
                            # Draw current line and start a new one
                            line_surf = renderer.small_font.render(" ".join(line_words), True, renderer.dim)
                            surface.blit(line_surf, (dx, dy))
                            dy += line_surf.get_height() + 4
                            line_words = [word]
                        else:
                            line_words.append(word)

                    if line_words:
                        line_surf = renderer.small_font.render(" ".join(line_words), True, renderer.dim)
                        surface.blit(line_surf, (dx, dy))


            # Footer
            footer = renderer.small_font.render("ESC to quit", True, renderer.dim)
            surface.blit(footer, (renderer.width - footer.get_width() - 12, renderer.height - 24))

            pygame.display.flip()
            clock.tick(60)
