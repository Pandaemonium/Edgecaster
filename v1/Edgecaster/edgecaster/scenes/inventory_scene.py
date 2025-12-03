from __future__ import annotations

from typing import List, Dict, Optional

import pygame

from .base import Scene


class InventoryScene(Scene):
    """
    Recursive inventory popup.

    This is intentionally a *stub* that mirrors the recursive Options UI
    style, so that later you (or your friend) can graft a real item /
    container model onto it.

    Semantics (for now):

    - Each InventoryScene has:
        * game: reference to the current Game (for future real logic).
        * depth: recursion depth (0 = top inventory opened from dungeon).
        * applied_visual: how THIS window is drawn (scale, offset, angle, alpha).
        * child_visual: how CHILD Inventory windows will be drawn.

    - The contents are hard-coded:
        0: "Blueberry (does nothing)"
        1: "Inventory (inside inventory)"
        2: "Back"

    - Selecting "Inventory (inside inventory)" opens another InventoryScene
      as a child popup (up to MAX_DEPTH).

    - Pressing Esc or i at any depth pops just that inventory layer.
    """

    MAX_DEPTH = 99

    def __init__(
        self,
        game,
        *,
        window_rect: Optional[pygame.Rect] = None,
        depth: int = 0,
        applied_visual: Optional[Dict[str, float]] = None,
        child_visual: Optional[Dict[str, float]] = None,
    ) -> None:
        # Reference to the current Game so we can poke the log later, and
        # eventually wire in real inventory data.
        self.game = game

        self.window_rect = window_rect
        self.depth = depth
        self.selected_idx = 0

        # Background snapshot (so this window overlays whatever was beneath it)
        self._background: Optional[pygame.Surface] = None

        # Fonts local to this scene (chunkier, panel-relative)
        self.ui_font: Optional[pygame.font.Font] = None
        self.small_font: Optional[pygame.font.Font] = None

        # --- Visuals for THIS window -----------------------------------
        if applied_visual is None:
            if depth == 0:
                # Root inventory appearance: large, centered, untwisted, solid.
                self.applied_visual = {
                    "scale_x": 0.90,
                    "scale_y": 0.90,
                    "offset_x": 0.0,
                    "offset_y": 0.0,
                    "angle": 0.0,
                    "alpha": 1.0,
                }
            else:
                # Children usually get this explicitly from parent; this is a
                # safe fallback if they don't.
                self.applied_visual = {
                    "scale_x": 0.90,
                    "scale_y": 0.90,
                    "offset_x": 0.0,
                    "offset_y": 0.0,
                    "angle": 0.0,
                    "alpha": 1.0,
                }
        else:
            self.applied_visual = dict(applied_visual)
        self.applied_visual.setdefault("alpha", 1.0)

        # --- Visuals for CHILD inventory windows -----------------------
        if child_visual is None:
            # Slightly smaller, offset, rotated, and faded: gives a nice
            # spiraling stack of inventories.
            self.child_visual = {
                "scale_x": 0.80,
                "scale_y": 0.80,
                "offset_x": 30.0,
                "offset_y": 20.0,
                "angle": 12.0,
                "alpha": 0.85,
            }
        else:
            self.child_visual = dict(child_visual)
        self.child_visual.setdefault("alpha", 1.0)

    # ------------------------------------------------------------------ #
    # Geometry / font helpers

    def _ensure_window_rect(self, renderer) -> None:
        """
        Establish the logical, unrotated rect for layout & hitboxes.

        - For depth 0, use applied_visual.scale_x/y vs full screen if
          window_rect wasn't passed in.
        - For depth > 0, respect any window_rect passed by the parent;
          otherwise fall back to a 60% centered window.
        """
        if self.depth == 0:
            if self.window_rect is not None:
                return

            scale_x = float(self.applied_visual.get("scale_x", 0.90))
            scale_y = float(self.applied_visual.get("scale_y", 0.90))
            off_x = float(self.applied_visual.get("offset_x", 0.0))
            off_y = float(self.applied_visual.get("offset_y", 0.0))

            base_w, base_h = renderer.width, renderer.height
            w = int(base_w * scale_x)
            h = int(base_h * scale_y)
            x = (base_w - w) // 2 + int(off_x)
            y = (base_h - h) // 2 + int(off_y)
            self.window_rect = pygame.Rect(x, y, w, h)
        else:
            if self.window_rect is not None:
                return
            base_w, base_h = renderer.width, renderer.height
            w = int(base_w * 0.6)
            h = int(base_h * 0.6)
            x = (base_w - w) // 2
            y = (base_h - h) // 2
            self.window_rect = pygame.Rect(x, y, w, h)

    def _ensure_fonts(self, renderer) -> None:
        """Make fonts sized relative to a fixed logical panel."""
        if self.ui_font is not None and self.small_font is not None:
            return

        logical_scale = 0.90
        base_ui = renderer.base_tile * 2
        base_small = 18

        ui_size = max(18, int(base_ui * logical_scale))
        small_size = max(12, int(base_small * logical_scale))

        self.ui_font = pygame.font.SysFont("consolas", ui_size)
        self.small_font = pygame.font.SysFont("consolas", small_size)

    def _compute_child_rect(self) -> pygame.Rect:
        """
        Compute the rect for a CHILD Inventory window (depth+1) using child_visual.
        """
        parent = self.window_rect
        assert parent is not None

        scale_x = float(self.child_visual.get("scale_x", 0.80))
        scale_y = float(self.child_visual.get("scale_y", 0.80))
        off_x = float(self.child_visual.get("offset_x", 30.0))
        off_y = float(self.child_visual.get("offset_y", 20.0))

        w = int(parent.width * scale_x)
        h = int(parent.height * scale_y)
        x = parent.x + (parent.width - w) // 2 + int(off_x)
        y = parent.y + (parent.height - h) // 2 + int(off_y)
        return pygame.Rect(x, y, w, h)

    # ------------------------------------------------------------------ #

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        # Snapshot underlying screen (dungeon + any existing overlays).
        if self._background is None:
            self._background = surface.copy()

        # Set up geometry + fonts
        self._ensure_window_rect(renderer)
        self._ensure_fonts(renderer)

        rect = self.window_rect
        assert rect is not None

        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font is not None and small_font is not None

        # Hard-coded "inventory" contents for now
        entries: List[str] = [
            "Blueberry (the time is not right to eat this)",
            "Inventory",
            "Back",
        ]
        num_items = len(entries)

        # Logical panel size for drawing / anisotropic scaling
        logical_w = int(renderer.width * 0.90)
        logical_h = int(renderer.height * 0.90)
        logical_scale_x = logical_w / renderer.width
        logical_scale_y = logical_h / renderer.height

        running = True
        while running:
            # ----------------- EVENTS -----------------
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE or event.key == pygame.K_i:
                        # Close this inventory layer
                        manager.pop_scene()
                        return

                    if event.key == pygame.K_F11:
                        renderer.toggle_fullscreen()
                        continue

                    if event.key in (pygame.K_UP, pygame.K_w):
                        self.selected_idx = (self.selected_idx - 1) % num_items
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self.selected_idx = (self.selected_idx + 1) % num_items

                    elif event.key in (
                        pygame.K_LEFT,
                        pygame.K_RIGHT,
                        pygame.K_RETURN,
                        pygame.K_SPACE,
                    ):
                        idx = self.selected_idx
                        choice = entries[idx]

                        # 0: Blueberry (stub)
                        if idx == 0:
                            if hasattr(self.game, "log"):
                                self.game.log.add("You inspect the blueberry, but notice nothing out of the ordinary.")

                        # 1: Nested Inventory
                        elif idx == 1:
                            if self.depth < self.MAX_DEPTH:
                                child_rect = self._compute_child_rect()

                                # Child appearance: accumulate angle +
                                # multiplicative alpha, like OptionsScene.
                                child_applied = dict(self.applied_visual)
                                parent_angle = float(self.applied_visual.get("angle", 0.0))
                                step_angle = float(self.child_visual.get("angle", 0.0))
                                child_applied["angle"] = parent_angle + step_angle

                                parent_alpha = float(self.applied_visual.get("alpha", 1.0))
                                step_alpha = float(self.child_visual.get("alpha", 1.0))
                                new_alpha = parent_alpha * step_alpha
                                if new_alpha < 0.05:
                                    new_alpha = 0.05
                                if new_alpha > 1.0:
                                    new_alpha = 1.0
                                child_applied["alpha"] = new_alpha

                                child_child = dict(self.child_visual)

                                child = InventoryScene(
                                    self.game,
                                    window_rect=child_rect,
                                    depth=self.depth + 1,
                                    applied_visual=child_applied,
                                    child_visual=child_child,
                                )
                                manager.push_scene(child)
                            return

                        # 2: Back
                        else:
                            manager.pop_scene()
                            return

            # ----------------- DRAW -----------------
            # Restore snapshot (already includes whatever was underneath).
            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            overlay = pygame.Surface((renderer.width, renderer.height), pygame.SRCALPHA)
            if self.depth == 0:
                # Dim the world only for the root inventory
                overlay.fill((0, 0, 0, 140))

            panel_x, panel_y, panel_w, panel_h = rect

            # Draw panel into logical surface
            logical_surface = pygame.Surface((logical_w, logical_h), pygame.SRCALPHA)
            border_thickness = max(1, int(2 * min(logical_scale_x, logical_scale_y)))

            pygame.draw.rect(
                logical_surface,
                (20, 20, 40, 235),
                (0, 0, logical_w, logical_h),
            )
            pygame.draw.rect(
                logical_surface,
                (220, 220, 240, 240),
                (0, 0, logical_w, logical_h),
                border_thickness,
            )

            # Title
            title_str = "Inventory"
            if self.depth > 0:
                title_str += f" (depth {self.depth})"
            title_text = ui_font.render(title_str, True, renderer.fg)
            title_y = int(16 * logical_scale_y)
            logical_surface.blit(
                title_text,
                ((logical_w - title_text.get_width()) // 2, title_y),
            )

            # Contents list
            y = int(90 * logical_scale_y)
            line_gap = max(1, int(6 * logical_scale_y))
            base_x = int(32 * logical_scale_x)

            for i, label in enumerate(entries):
                selected = (i == self.selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                text = ui_font.render(prefix + label, True, color)
                logical_surface.blit(text, (base_x, y))
                y += text.get_height() + line_gap

            # Hint line
            hint = small_font.render(
                "↑/↓ or W/S to move • Enter/Space to select • Esc/i to return",
                True,
                renderer.dim,
            )
            hint_y = logical_h - int(32 * logical_scale_y)
            hint_x = (logical_w - hint.get_width()) // 2
            logical_surface.blit(hint, (hint_x, hint_y))

            # Anisotropic-scale logical panel into this window's rect
            scaled_panel = pygame.transform.smoothscale(
                logical_surface, (panel_w, panel_h)
            )

            # Apply rotation + alpha from applied_visual
            angle = float(self.applied_visual.get("angle", 0.0))
            rotated = pygame.transform.rotozoom(scaled_panel, angle, 1.0)
            rot_rect = rotated.get_rect(
                center=(panel_x + panel_w // 2, panel_y + panel_h // 2)
            )

            applied_alpha = float(self.applied_visual.get("alpha", 1.0))
            applied_alpha = max(0.05, min(1.0, applied_alpha))
            rotated.set_alpha(int(applied_alpha * 255))

            overlay.blit(rotated, rot_rect.topleft)

            surface.blit(overlay, (0, 0))
            renderer.present()
            clock.tick(60)
