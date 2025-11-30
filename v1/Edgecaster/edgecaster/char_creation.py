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
        self.generators = ["custom", "koch", "branch", "zigzag"]
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
            if key == pygame.K_d and self.char.generator == "custom":
                self.char.custom_pattern = self.draw_custom_pattern()
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
        gen_label = self.char.generator
        if self.char.generator == "custom":
            gen_label = "custom (press D to draw; 4 verts, X0-10, Y-5..5)"
        self.draw_field("Generator", gen_label, y, selected=self.fields[self.idx] == "generator")
        y += 40
        self.draw_field("Illuminator", self.char.illuminator, y, selected=self.fields[self.idx] == "illuminator")
        y += 50
        self.draw_stats(y)
        y += 150
        self.draw_field("Press Enter to start (Esc=default)", "", y, selected=self.fields[self.idx] == "done")

    def draw_custom_pattern(self) -> list | None:
        """
        Grid-limited custom polyline:
        - Root fixed at (0,0), terminus fixed at (10,0)
        - Up to 4 intermediate vertices
        - X snap: 0..10, Y snap: -5..5
        - On 4th point you can review; Enter/Take Power saves, Redo clears, Esc cancels.
        """
        root = (0, 0)
        terminus = (10, 0)
        max_pts = 4
        midpoints: list[Tuple[int, int]] = []
        scale = 40  # pixels per grid step
        origin = (self.width // 2 - int(5 * scale), self.height // 2)
        clock = pygame.time.Clock()
        done_ready = False

        def clamp_grid(x: int, y: int) -> Tuple[int, int]:
            return max(0, min(10, x)), max(-5, min(5, y))

        def finalize() -> list[Tuple[float, float]]:
            pts = [root] + [(float(x), float(y)) for (x, y) in midpoints] + [terminus]
            return pts

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return None
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return None
                    if event.key == pygame.K_BACKSPACE and midpoints:
                        midpoints.pop()
                        done_ready = len(midpoints) >= 1
                    if event.key == pygame.K_RETURN and done_ready:
                        return finalize()
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = event.pos
                    # buttons first
                    if done_ready:
                        if take_btn.collidepoint(mx, my):
                            return finalize()
                        if redo_btn.collidepoint(mx, my):
                            midpoints.clear()
                            done_ready = False
                            continue
                    gx = round((mx - origin[0]) / scale)
                    gy = round((my - origin[1]) / scale)
                    gx, gy = clamp_grid(gx, gy)
                    if len(midpoints) < max_pts:
                        midpoints.append((gx, gy))
                        done_ready = len(midpoints) >= 1

            # draw
            self.surface.fill((8, 8, 14))
            instr = [
                "Custom Fractal (grid snap, 4 verts). Root=(0,0), Terminus=(10,0).",
                "Click to place points. Backspace=undo, Enter or Take Power=finish, Redo=clear, Esc=cancel.",
            ]
            y = 24
            for line in instr:
                txt = self.font.render(line, True, self.fg)
                self.surface.blit(txt, (40, y))
                y += 26
            # grid
            for xi in range(0, 11):
                px = origin[0] + xi * scale
                pygame.draw.line(self.surface, (60, 60, 90), (px, origin[1] - 5 * scale), (px, origin[1] + 5 * scale))
            for yi in range(-5, 6):
                py = origin[1] + yi * scale
                pygame.draw.line(self.surface, (60, 60, 90), (origin[0], py), (origin[0] + 10 * scale, py))

            def draw_point(pt, color, radius=6):
                px = int(origin[0] + pt[0] * scale)
                py = int(origin[1] + pt[1] * scale)
                pygame.draw.circle(self.surface, color, (px, py), radius)

            draw_point(root, (255, 230, 140))
            draw_point(terminus, (140, 230, 255))

            # polyline
            all_pts = [root] + midpoints
            if len(midpoints) == max_pts:
                all_pts.append(terminus)
            if len(all_pts) >= 2:
                for i in range(1, len(all_pts)):
                    a = all_pts[i - 1]
                    b = all_pts[i]
                    pa = (int(origin[0] + a[0] * scale), int(origin[1] + a[1] * scale))
                    pb = (int(origin[0] + b[0] * scale), int(origin[1] + b[1] * scale))
                    pygame.draw.line(self.surface, (180, 220, 255), pa, pb, 3)
            # midpoints dots
            for gx, gy in midpoints:
                px = int(origin[0] + gx * scale)
                py = int(origin[1] + gy * scale)
                pygame.draw.circle(self.surface, (120, 200, 255), (px, py), 5)

            # buttons
            btn_w, btn_h = 140, 34
            take_btn = pygame.Rect(self.width - btn_w - 60, self.height - btn_h - 30, btn_w, btn_h)
            redo_btn = pygame.Rect(60, self.height - btn_h - 30, btn_w, btn_h)
            pygame.draw.rect(self.surface, (30, 90, 40) if done_ready else (40, 40, 40), take_btn)
            pygame.draw.rect(self.surface, (80, 150, 90) if done_ready else (90, 90, 90), take_btn, 2)
            pygame.draw.rect(self.surface, (90, 40, 40), redo_btn)
            pygame.draw.rect(self.surface, (150, 90, 90), redo_btn, 2)
            ttxt = self.font.render("Take Power", True, (220, 240, 220))
            rtxt = self.font.render("Redo", True, (240, 220, 220))
            self.surface.blit(ttxt, (take_btn.centerx - ttxt.get_width() // 2, take_btn.centery - ttxt.get_height() // 2))
            self.surface.blit(rtxt, (redo_btn.centerx - rtxt.get_width() // 2, redo_btn.centery - rtxt.get_height() // 2))

            pygame.display.flip()
            clock.tick(60)

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
