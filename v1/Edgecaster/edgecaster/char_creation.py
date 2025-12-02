import pygame
from typing import List, Tuple
from typing import Optional

from edgecaster.character import Character, default_character


# --- Character classes & descriptions (from the old scene) ---

CHAR_CLASSES: List[str] = [
    "Kochbender",
    "Automoton",
    "Strange Attractor",
    "Weaver",
]

CLASS_DESCRIPTIONS = {
    "Kochbender": "Who commands the icy lash of runes carved infinitely sharp? Who obeys?",
    "Automoton": "A common misconception that machines cannot perform magic. Quite the contrary.",
    "Strange Attractor": "Cursed to dance among weird energies, or they among her.",
    "Weaver": "Everyone wears clothing, but few make their own. So too with destiny.",
}

DEFAULT_NAME = "Pandaemonium"


class CharCreation:
    def __init__(self, width: int, height: int, cfg=None, fullscreen: bool = False) -> None:
        pygame.init()
        self.width = width
        self.height = height
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.surface = pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption("Edgecaster - Character Creation")
        self.font = pygame.font.SysFont("consolas", 22)
        self.big = pygame.font.SysFont("consolas", 28, bold=True)
        self.bg = (12, 12, 20)
        self.fg = (220, 230, 240)
        self.sel = (255, 230, 120)

        self.running = True
        self.click_targets = {}  # name -> Rect

        # Fields in the order they appear visually:
        self.fields = [
            "name",
            "class",
            "generator",
            "illuminator",
            "seed_mode",
            "seed_value",
            "con",
            "agi",
            "int",
            "res",
            "done",
        ]
        self.idx = 0

        self.generators = ["custom", "koch", "branch", "zigzag"]
        self.illuminators = ["radius", "neighbors"]

        self.char = default_character()
        # seed handling
        self.seed_mode = "fixed"
        default_seed = getattr(cfg, "seed", None) if cfg else None
        if default_seed is None:
            default_seed = getattr(self.char, "seed", None)
        self.seed_text = str(default_seed) if default_seed is not None else ""

        # Safeguards in case default_character is missing attributes
        if not hasattr(self.char, "name") or self.char.name is None:
            self.char.name = DEFAULT_NAME
        elif self.char.name == "":
            self.char.name = DEFAULT_NAME

        if not hasattr(self.char, "generator"):
            self.char.generator = "koch"
        if not hasattr(self.char, "illuminator"):
            self.char.illuminator = "radius"
        if not hasattr(self.char, "stats"):
            self.char.stats = {"con": 0, "agi": 0, "int": 0, "res": 0}
        if not hasattr(self.char, "point_pool"):
            self.char.point_pool = 10

        # Class selection state
        self.class_idx = 0
        self.char_class = CHAR_CLASSES[self.class_idx]

    def run(self) -> Optional[Character]:
        clock = pygame.time.Clock()
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    return None
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.handle_click(event.pos)

                if event.type == pygame.KEYDOWN:
                    # ESC: bail out, keep default char
                    if event.key == pygame.K_ESCAPE:
                        self.running = False
                        return None

                    # ENTER: either advance to next field, or confirm on "done"
                    if event.key == pygame.K_RETURN:
                        current = self.fields[self.idx]

                        # leaving name field via Enter: restore default if blank
                        if current == "name" and not self.char.name.strip():
                            self.char.name = DEFAULT_NAME

                        if current == "done":
                            # apply seed selection before exiting
                            if self.seed_mode == "random":
                                self.char.use_random_seed = True
                                self.char.seed = None
                            else:
                                self.char.use_random_seed = False
                                try:
                                    self.char.seed = int(self.seed_text)
                                except ValueError:
                                    self.char.seed = None
                            self.running = False
                            break
                        else:
                            # move to next field
                            self.idx = (self.idx + 1) % len(self.fields)
                            continue

                    # Otherwise, normal key handling
                    self.handle_key(event.key)

            self.draw()
            pygame.display.flip()
            clock.tick(60)

        # Attach the chosen class to the character before returning
        setattr(self.char, "char_class", self.char_class)
        setattr(self.char, "player_class", self.char_class)
        return self.char

    def handle_key(self, key: int) -> None:
        # Move between fields
        if key in (pygame.K_DOWN, pygame.K_TAB):
            current = self.fields[self.idx]
            # leaving name field via Down/Tab: restore default if blank
            if current == "name" and not self.char.name.strip():
                self.char.name = DEFAULT_NAME
            self.idx = (self.idx + 1) % len(self.fields)
            return

        if key == pygame.K_UP:
            current = self.fields[self.idx]
            # leaving name field via Up: restore default if blank
            if current == "name" and not self.char.name.strip():
                self.char.name = DEFAULT_NAME
            self.idx = (self.idx - 1) % len(self.fields)
            return

        field = self.fields[self.idx]

        # --- Name input ---
        if field == "name":
            if key == pygame.K_BACKSPACE:
                # If we're still on the default name, wipe it in one go
                if self.char.name == DEFAULT_NAME:
                    self.char.name = ""
                else:
                    self.char.name = self.char.name[:-1]
            elif 32 <= key <= 126:
                # Produce a character, respecting Shift / Caps Lock
                ch = chr(key)
                mods = pygame.key.get_mods()
                if mods & (pygame.KMOD_SHIFT | pygame.KMOD_CAPS):
                    ch = ch.upper()
                # If we're on the untouched default name, start fresh
                if self.char.name == DEFAULT_NAME:
                    self.char.name = ""
                self.char.name += ch
            return

        # --- Seed mode / value ---
        if field == "seed_mode":
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                self.seed_mode = "random" if self.seed_mode == "fixed" else "fixed"
            return
        if field == "seed_value":
            if self.seed_mode != "fixed":
                return
            if key == pygame.K_BACKSPACE:
                self.seed_text = self.seed_text[:-1]
            elif key == pygame.K_MINUS and not self.seed_text:
                self.seed_text = "-"
            elif 48 <= key <= 57:  # digits
                self.seed_text += chr(key)
            return

        # --- Generator selection ---
        if field == "generator":
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = 1 if key == pygame.K_RIGHT else -1
                try:
                    idx = self.generators.index(self.char.generator)
                except ValueError:
                    idx = 0
                    self.char.generator = self.generators[0]
                self.char.generator = self.generators[(idx + delta) % len(self.generators)]
            if key == pygame.K_d and self.char.generator == "custom":
                self.char.custom_pattern = self.draw_custom_pattern()
            return

        # --- Illuminator selection ---
        if field == "illuminator":
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = 1 if key == pygame.K_RIGHT else -1
                try:
                    idx = self.illuminators.index(self.char.illuminator)
                except ValueError:
                    idx = 0
                    self.char.illuminator = self.illuminators[0]
                self.char.illuminator = self.illuminators[(idx + delta) % len(self.illuminators)]
            return

        # --- Stat adjustments ---
        if field in ("con", "agi", "int", "res"):
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                self.adjust_stat(field, 1 if key == pygame.K_RIGHT else -1)
            return
        # --- Class selection ---
        if field == "class":
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = 1 if key == pygame.K_RIGHT else -1
                self.class_idx = (self.class_idx + delta) % len(CHAR_CLASSES)
                self.char_class = CHAR_CLASSES[self.class_idx]
            return

    def adjust_stat(self, stat: str, delta: int) -> None:
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

        # Title
        self.draw_title("Character Creation", y)
        y += 50

        # Name – no more "Edgecaster", just current name
        self.draw_field(
            "Name",
            self.char.name,
            y,
            selected=self.fields[self.idx] == "name",
        )
        y += 40

        # Class selection
        self.draw_field(
            "Class",
            self.char_class,
            y,
            selected=self.fields[self.idx] == "class",
        )
        y += 36

        # Generator
        gen_label = self.char.generator
        if self.char.generator == "custom":
            gen_label = "custom (press D to draw; 4 verts, X0-10, Y-5..5)"
        self.draw_field(
            "Generator",
            gen_label,
            y,
            selected=self.fields[self.idx] == "generator",
        )
        y += 40

        # Illuminator
        self.draw_field(
            "Illuminator",
            self.char.illuminator,
            y,
            selected=self.fields[self.idx] == "illuminator",
        )
        y += 40

        # Seed mode/value with text box
        seed_sel = self.fields[self.idx] in ("seed_mode", "seed_value")
        y = self.draw_seed_controls(y, selected=seed_sel)

        # Stats
        self.draw_stats(y)
        # This y is approximate; draw_stats uses its own local y.
        y += 190  # slightly larger than before for more spacing

        # Class description
        y = self.draw_class_section(y)
        y += 30

        # Footer ("done" field)
        self.draw_field(
            "Press Enter to start (Esc=quit)",
            "",
            y,
            selected=self.fields[self.idx] == "done",
        )

    def draw_class_section(self, y: int) -> int:
        """Draw the class selector + wrapped description."""
        selected = self.fields[self.idx] == "class"

        # Display selected class
        self.draw_field("Class", self.char_class, y, selected=selected)
        y += 36

        desc = CLASS_DESCRIPTIONS.get(self.char_class, "")
        if not desc:
            return y

        # simple word wrap
        max_width = self.width - 160
        dx = 100
        words = desc.split()
        line_words: List[str] = []

        while words:
            word = words.pop(0)
            test_line = (" ".join(line_words + [word])).strip()
            surf = self.font.render(test_line, True, (180, 190, 210))
            if surf.get_width() > max_width and line_words:
                line_surf = self.font.render(" ".join(line_words), True, (180, 190, 210))
                self.surface.blit(line_surf, (dx, y))
                y += 26
                line_words = [word]
            else:
                line_words.append(word)

        if line_words:
            line_surf = self.font.render(" ".join(line_words), True, (180, 190, 210))
            self.surface.blit(line_surf, (dx, y))
            y += 26

        return y

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
                pygame.draw.line(
                    self.surface,
                    (60, 60, 90),
                    (px, origin[1] - 5 * scale),
                    (px, origin[1] + 5 * scale),
                )
            for yi in range(-5, 6):
                py = origin[1] + yi * scale
                pygame.draw.line(
                    self.surface,
                    (60, 60, 90),
                    (origin[0], py),
                    (origin[0] + 10 * scale, py),
                )

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
            take_btn = pygame.Rect(
                self.width - btn_w - 60, self.height - btn_h - 30, btn_w, btn_h
            )
            redo_btn = pygame.Rect(60, self.height - btn_h - 30, btn_w, btn_h)
            pygame.draw.rect(
                self.surface, (30, 90, 40) if done_ready else (40, 40, 40), take_btn
            )
            pygame.draw.rect(
                self.surface, (80, 150, 90) if done_ready else (90, 90, 90), take_btn, 2
            )
            pygame.draw.rect(self.surface, (90, 40, 40), redo_btn)
            pygame.draw.rect(self.surface, (150, 90, 90), redo_btn, 2)
            ttxt = self.font.render("Take Power", True, (220, 240, 220))
            rtxt = self.font.render("Redo", True, (240, 220, 220))
            self.surface.blit(
                ttxt,
                (
                    take_btn.centerx - ttxt.get_width() // 2,
                    take_btn.centery - ttxt.get_height() // 2,
                ),
            )
            self.surface.blit(
                rtxt,
                (
                    redo_btn.centerx - rtxt.get_width() // 2,
                    redo_btn.centery - rtxt.get_height() // 2,
                ),
            )

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
        label = self.big.render(
            f"Stats (Points left: {self.char.point_pool})", True, self.fg
        )
        self.surface.blit(label, (80, y))
        y += 36
        for key, lbl in (
            ("con", "Constitution"),
            ("agi", "Agility"),
            ("int", "Intelligence"),
            ("res", "Resonance"),
        ):
            selected = self.fields[self.idx] == key
            col = self.sel if selected else self.fg
            val = self.char.stats.get(key, 0)
            surf = self.font.render(f"{lbl}: {val}", True, col)
            self.surface.blit(surf, (100, y))
            y += 32

    def draw_seed_controls(self, y: int, selected: bool) -> int:
        """Draw seed fixed/random radios and an input box."""
        self.click_targets.clear()
        label = self.big.render("World Seed", True, self.fg)
        self.surface.blit(label, (80, y))
        y += 32

        # radio buttons
        def radio(x, y, checked):
            pygame.draw.circle(self.surface, self.fg, (x, y), 10, 2)
            if checked:
                pygame.draw.circle(self.surface, self.sel, (x, y), 6)

        fixed_y = y + 4
        random_y = y + 36
        radio_x = 100
        radio(radio_x, fixed_y, self.seed_mode == "fixed")
        self.click_targets["seed_fixed"] = pygame.Rect(radio_x - 12, fixed_y - 12, 24, 24)
        txt = self.font.render("Fixed seed:", True, self.sel if self.seed_mode == "fixed" else self.fg)
        self.surface.blit(txt, (radio_x + 18, fixed_y - 12))

        # input box
        box_rect = pygame.Rect(radio_x + 140, fixed_y - 18, 280, 32)
        self.click_targets["seed_box"] = box_rect
        pygame.draw.rect(self.surface, (40, 40, 60), box_rect, border_radius=4)
        pygame.draw.rect(self.surface, self.sel if selected and self.seed_mode == "fixed" else self.fg, box_rect, 2, border_radius=4)
        shown = self.seed_text if self.seed_mode == "fixed" else ""
        if shown == "":
            shown = "enter number"
        text_col = self.sel if (selected and self.seed_mode == "fixed") else self.fg
        txt_val = self.font.render(shown, True, text_col)
        self.surface.blit(txt_val, (box_rect.x + 8, box_rect.y + 6))

        # random option
        radio(radio_x, random_y, self.seed_mode == "random")
        self.click_targets["seed_random"] = pygame.Rect(radio_x - 12, random_y - 12, 24, 24)
        rtxt = self.font.render("Random seed each run", True, self.sel if self.seed_mode == "random" else self.fg)
        self.surface.blit(rtxt, (radio_x + 18, random_y - 12))

        return y + 64

    def handle_click(self, pos: tuple[int, int]) -> None:
        """Mouse support to focus seed entry / radios."""
        if "seed_fixed" in self.click_targets and self.click_targets["seed_fixed"].collidepoint(pos):
            self.seed_mode = "fixed"
            self.idx = self.fields.index("seed_value")
            return
        if "seed_random" in self.click_targets and self.click_targets["seed_random"].collidepoint(pos):
            self.seed_mode = "random"
            self.idx = self.fields.index("seed_mode")
            return
        if "seed_box" in self.click_targets and self.click_targets["seed_box"].collidepoint(pos):
            self.seed_mode = "fixed"
            self.idx = self.fields.index("seed_value")
            return


def run_character_creation(cfg, fullscreen: bool = False) -> Optional[Character]:
    screen = CharCreation(cfg.view_width, cfg.view_height, fullscreen=fullscreen)
    char = screen.run()
    # Do NOT pygame.display.quit() here; we’re keeping the window alive.
    return char
