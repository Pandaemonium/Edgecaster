import pygame
from typing import List, Tuple

from edgecaster.character import Character, default_character


class CharCreation:
    def __init__(self, width: int, height: int) -> None:
        pygame.init()
        self.width = width
        self.height = height
        self.surface = pygame.display.set_mode((width, height))
        pygame.display.set_caption("Edgecaster - Character Creation")
        self.font = pygame.font.SysFont("consolas", 22)
        self.big = pygame.font.SysFont("consolas", 28, bold=True)
        self.bg = (12, 12, 20)
        self.fg = (220, 230, 240)
        self.sel = (255, 230, 120)
        self.running = True
        self.fields = ["name", "generator", "illuminator", "con", "agi", "int", "res", "done"]
        self.idx = 0
        self.generators = ["koch", "branch", "zigzag"]
        self.illuminators = ["radius", "neighbors"]
        self.char = default_character()

    def run(self) -> Character:
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    return self.char
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_RETURN:
                        self.running = False
                        break
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                        break
                    self.handle_key(event.key)
            self.draw()
            pygame.display.flip()
            clock.tick(60)
        return self.char

    def handle_key(self, key: int) -> None:
        if key in (pygame.K_DOWN, pygame.K_TAB):
            self.idx = (self.idx + 1) % len(self.fields)
            return
        if key == pygame.K_UP:
            self.idx = (self.idx - 1) % len(self.fields)
            return

        field = self.fields[self.idx]
        if field == "name":
            if key == pygame.K_BACKSPACE:
                self.char.name = self.char.name[:-1]
            elif 32 <= key <= 126:
                self.char.name += chr(key)
            return
        if field == "generator":
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = 1 if key == pygame.K_RIGHT else -1
                idx = self.generators.index(self.char.generator)
                self.char.generator = self.generators[(idx + delta) % len(self.generators)]
            return
        if field == "illuminator":
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = 1 if key == pygame.K_RIGHT else -1
                idx = self.illuminators.index(self.char.illuminator)
                self.char.illuminator = self.illuminators[(idx + delta) % len(self.illuminators)]
            return
        if field in ("con", "agi", "int", "res"):
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                self.adjust_stat(field, 1 if key == pygame.K_RIGHT else -1)
            return

    def adjust_stat(self, stat: str, delta: int) -> None:
        # simple point buy with pool
        current = self.char.stats.get(stat, 0)
        if delta > 0 and self.char.point_pool <= 0:
            return
        new_val = current + delta
        if new_val < 0:
            return
        self.char.stats[stat] = new_val
        self.char.point_pool -= delta

    def draw(self) -> None:
        self.surface.fill(self.bg)
        y = 60
        self.draw_title("Character Creation", y)
        y += 50
        self.draw_field("Name", self.char.name, y, selected=self.fields[self.idx] == "name")
        y += 40
        self.draw_field("Generator", self.char.generator, y, selected=self.fields[self.idx] == "generator")
        y += 40
        self.draw_field("Illuminator", self.char.illuminator, y, selected=self.fields[self.idx] == "illuminator")
        y += 50
        self.draw_stats(y)
        y += 150
        self.draw_field("Press Enter to start (Esc=default)", "", y, selected=self.fields[self.idx] == "done")

    def draw_title(self, text: str, y: int) -> None:
        surf = self.big.render(text, True, self.fg)
        self.surface.blit(surf, ((self.width - surf.get_width()) // 2, y))

    def draw_field(self, label: str, value: str, y: int, selected: bool = False) -> None:
        col = self.sel if selected else self.fg
        text = self.font.render(f"{label}: {value}", True, col)
        self.surface.blit(text, (80, y))

    def draw_stats(self, y: int) -> None:
        label = self.big.render(f"Stats (Points left: {self.char.point_pool})", True, self.fg)
        self.surface.blit(label, (80, y))
        y += 36
        for key, lbl in (("con", "Constitution"), ("agi", "Agility"), ("int", "Intelligence"), ("res", "Resonance")):
            selected = self.fields[self.idx] == key
            col = self.sel if selected else self.fg
            val = self.char.stats.get(key, 0)
            surf = self.font.render(f"{lbl}: {val}", True, col)
            self.surface.blit(surf, (100, y))
            y += 32


def run_character_creation(cfg) -> Character:
    screen = CharCreation(cfg.view_width, cfg.view_height)
    char = screen.run()
    pygame.display.quit()
    return char
