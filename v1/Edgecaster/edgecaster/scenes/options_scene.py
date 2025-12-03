from __future__ import annotations

from typing import List, Dict, Optional

import pygame

from .base import Scene


# ---------------------------------------------------------------------------#
# OptionsScene
# ---------------------------------------------------------------------------#


class OptionsScene(Scene):
    """
    Recursive options popup.

    Semantics:

    - Each OptionsScene has:
        * applied_visual: how THIS menu is drawn (rotation, etc.)
        * child_visual: how its CHILD Options menus will be drawn.

    - "Options Options" edits child_visual only. It does NOT change this
      menu's own appearance; it previews what the NEXT depth will look like.

    - Root Options (depth 0) has a fixed applied_visual so it always looks
      the same, regardless of player tweaks.
    """

    MAX_DEPTH = 99  # we can go deep

    def __init__(
        self,
        window_rect: Optional[pygame.Rect] = None,
        depth: int = 0,
        applied_visual: Optional[Dict[str, float]] = None,
        child_visual: Optional[Dict[str, float]] = None,
    ) -> None:
        self.window_rect = window_rect
        self.depth = depth
        self.selected_idx = 0

        # Background snapshot (for the artichoke / CRT effect)
        self._background: Optional[pygame.Surface] = None

        # Fonts
        self.ui_font: Optional[pygame.font.Font] = None
        self.small_font: Optional[pygame.font.Font] = None

        # How THIS menu is drawn
        if applied_visual is None:
            if depth == 0:
                # Root appearance: big, centered, no twist, fully opaque.
                self.applied_visual: Dict[str, float] = {
                    "scale_x": 0.90,
                    "scale_y": 0.90,
                    "offset_x": 0.0,
                    "offset_y": 0.0,
                    "angle": 0.0,
                    "alpha": 1.0,   # 1.0 = fully solid
                }
            else:
                # Child menus normally get this explicitly from parent,
                # but fall back to a neutral look if needed.
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
            # Ensure alpha is always present
            self.applied_visual.setdefault("alpha", 1.0)


        # How CHILD menus will be drawn (edited by Options Options)
        if child_visual is None:
            # Defaults consistent with root: 0.90, 0.90, 0, 0, 0°, fully opaque.
            self.child_visual: Dict[str, float] = {
                "scale_x": 0.90,
                "scale_y": 0.90,
                "offset_x": 0.0,
                "offset_y": 0.0,
                "angle": 0.0,
                "alpha": 1.0,   # 1.0 = fully solid
            }
        else:
            self.child_visual = dict(child_visual)
            # Ensure we always have an alpha key
            self.child_visual.setdefault("alpha", 1.0)


    # ------------------------------------------------------------------ #
    # Geometry / font helpers

    def _ensure_window_rect(self, renderer) -> None:
        """
        Establish the logical, unrotated rect for layout & hitboxes.

        - For depth 0, size is based on applied_visual.scale_x/y vs full screen.
        - For depth > 0, we respect whatever rect the parent/manager passed.
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
            # Child rects should usually be passed in by parent.
            if self.window_rect is None:
                base_w, base_h = renderer.width, renderer.height
                w = int(base_w * 0.6)
                h = int(base_h * 0.6)
                x = (base_w - w) // 2
                y = (base_h - h) // 2
                self.window_rect = pygame.Rect(x, y, w, h)

    def _ensure_fonts(self, renderer) -> None:
        """Make fonts sized relative to a fixed logical panel (chunky)."""
        if self.ui_font is not None and self.small_font is not None:
            return

        # Logical panel is 90% of the screen
        logical_scale = 0.90

        # Chunkier base sizes
        base_ui = renderer.base_tile * 2
        base_small = 18

        ui_size = max(18, int(base_ui * logical_scale))
        small_size = max(12, int(base_small * logical_scale))

        self.ui_font = pygame.font.SysFont("consolas", ui_size)
        self.small_font = pygame.font.SysFont("consolas", small_size)

    def _compute_child_rect(self) -> pygame.Rect:
        """
        Compute the rect for a CHILD Options window (depth+1) using child_visual.

        Uses THIS menu's window_rect as the parent frame.
        """
        parent = self.window_rect
        assert parent is not None

        scale_x = float(self.child_visual.get("scale_x", 0.90))
        scale_y = float(self.child_visual.get("scale_y", 0.90))
        off_x = float(self.child_visual.get("offset_x", 0.0))
        off_y = float(self.child_visual.get("offset_y", 0.0))

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

        # Snapshot current screen (dungeon, or parent options stack)
        # Only do this once per instance, so popping a child doesn't "bake in" its pixels.
        if self._background is None:
            self._background = surface.copy()


        # Geometry + fonts
        self._ensure_window_rect(renderer)
        self._ensure_fonts(renderer)

        rect = self.window_rect
        assert rect is not None

        # Shared boolean options
        toggles: Dict[str, bool] = manager.options
        toggle_keys: List[str] = list(toggles.keys())

        # Menu layout:
        # 0..len(toggles)-1 : boolean toggles
        # len(toggles)      : "Options" (recursive)
        # len(toggles)+1    : "Options Options" (visual submenu)
        # len(toggles)+2    : "Back"
        num_toggles = len(toggles)
        options_index = num_toggles
        options_options_index = num_toggles + 1
        back_index = num_toggles + 2
        num_items = num_toggles + 3

        # Logical panel size (used for anisotropic scaling)
        logical_w = int(renderer.width * 0.90)
        logical_h = int(renderer.height * 0.90)
        logical_scale_x = logical_w / renderer.width
        logical_scale_y = logical_h / renderer.height

        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font is not None and small_font is not None

        running = True
        while running:
            # ----------------- EVENTS -----------------
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
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

                        # Boolean toggles (manager.options)
                        if idx < num_toggles:
                            key = toggle_keys[idx]
                            if event.key in (
                                pygame.K_LEFT,
                                pygame.K_RIGHT,
                                pygame.K_RETURN,
                                pygame.K_SPACE,
                            ):
                                toggles[key] = not toggles[key]
                                if key.lower() == "fullscreen":
                                    renderer.toggle_fullscreen()

                        # "Options" -> recursive child
                        elif idx == options_index:
                            if self.depth < self.MAX_DEPTH:
                                child_rect = self._compute_child_rect()

                                child_applied = dict(self.applied_visual)

                                # Angle accumulates: parent angle + step
                                parent_angle = float(self.applied_visual.get("angle", 0.0))
                                step_angle = float(self.child_visual.get("angle", 0.0))
                                child_applied["angle"] = parent_angle + step_angle

                                # Opacity accumulates multiplicatively:
                                # child opacity = parent opacity * child factor
                                parent_alpha = float(self.applied_visual.get("alpha", 1.0))
                                step_alpha = float(self.child_visual.get("alpha", 1.0))
                                new_alpha = parent_alpha * step_alpha
                                # Clamp to a sane visible range
                                if new_alpha < 0.05:
                                    new_alpha = 0.05
                                if new_alpha > 1.0:
                                    new_alpha = 1.0
                                child_applied["alpha"] = new_alpha

                                # Child's child_visual starts as our current child_visual
                                child_child = dict(self.child_visual)

                                child = OptionsScene(
                                    window_rect=child_rect,
                                    depth=self.depth + 1,
                                    applied_visual=child_applied,
                                    child_visual=child_child,
                                )
                                manager.push_scene(child)
                            return



                        # "Options Options" -> visual submenu (edits child_visual)
                        elif idx == options_options_index:
                            parent_angle = float(self.applied_visual.get("angle", 0.0))
                            parent_alpha = float(self.applied_visual.get("alpha", 1.0))
                            visual_scene = VisualOptionsScene(
                                base_rect=self.window_rect,
                                depth=self.depth + 1,
                                visual=self.child_visual,  # reference
                                parent_angle=parent_angle,
                                parent_alpha=parent_alpha,
                            )
                            manager.push_scene(visual_scene)
                            return


                        # "Back"
                        else:
                            manager.pop_scene()
                            return

            # ----------------- DRAW -----------------

            # Restore snapshot (which already contains deeper stack)
            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            overlay = pygame.Surface((renderer.width, renderer.height), pygame.SRCALPHA)
            if self.depth == 0:
                overlay.fill((0, 0, 0, 140))  # dim at root only

            panel_x, panel_y, panel_w, panel_h = rect

            # ---- Draw panel to logical surface (for anisotropic scaling + rotation) ----
            logical_surface = pygame.Surface((logical_w, logical_h), pygame.SRCALPHA)
            border_thickness = max(1, int(2 * min(logical_scale_x, logical_scale_y)))

            # Panel background + border
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
            title_str = "Options"
            title_text = ui_font.render(title_str, True, renderer.fg)
            title_y = int(16 * logical_scale_y)
            logical_surface.blit(
                title_text,
                ((logical_w - title_text.get_width()) // 2, title_y),
            )

            # World seed display
            seed_val = None
            if getattr(manager, "current_game", None):
                seed_val = getattr(manager.current_game, "fractal_seed", None)
            if seed_val is None and getattr(manager, "character", None):
                seed_val = getattr(manager.character, "seed", None)

            seed_line = f"World Seed: {seed_val if seed_val is not None else 'random'}"
            seed_text = small_font.render(seed_line, True, renderer.fg)
            seed_y = int(54 * logical_scale_y)
            seed_x = int(24 * logical_scale_x)
            logical_surface.blit(seed_text, (seed_x, seed_y))

            # Options list
            y = int(90 * logical_scale_y)
            line_gap = max(1, int(6 * logical_scale_y))
            base_x = int(32 * logical_scale_x)

            # Toggles
            for i, key in enumerate(toggle_keys):
                val = toggles[key]
                selected = (i == self.selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "
                status = "ON" if val else "OFF"
                line = f"{prefix}{key}: {status}"
                text = ui_font.render(line, True, color)
                logical_surface.blit(text, (base_x, y))
                y += text.get_height() + line_gap

            # "Options"
            selected = (self.selected_idx == options_index)
            if self.depth < self.MAX_DEPTH:
                label = "Options"
                color = renderer.player_color if selected else renderer.fg
            else:
                label = "Options (max depth)"
                color = renderer.dim
            prefix = "▶ " if selected else "  "
            opt_text = ui_font.render(prefix + label, True, color)
            logical_surface.blit(opt_text, (base_x, y))
            y += opt_text.get_height() + line_gap

            # "Options Options"
            selected = (self.selected_idx == options_options_index)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            oo_text = ui_font.render(prefix + "Options Options", True, color)
            logical_surface.blit(oo_text, (base_x, y))
            y += oo_text.get_height() + line_gap + line_gap

            # "Back"
            selected = (self.selected_idx == back_index)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            back_label = "Back to Main Menu" if self.depth == 0 else "Back"
            back_text = ui_font.render(prefix + back_label, True, color)
            logical_surface.blit(back_text, (base_x, y))

            # Hint
            hint = small_font.render(
                "↑/↓ or W/S to move • ←/→ or Enter/Space to change • Esc to return",
                True,
                renderer.dim,
            )
            hint_y = logical_h - int(32 * logical_scale_y)
            hint_x = (logical_w - hint.get_width()) // 2
            logical_surface.blit(hint, (hint_x, hint_y))

            # Anisotropic scale logical panel to this menu's rect
            scaled_panel = pygame.transform.smoothscale(
                logical_surface, (panel_w, panel_h)
            )

            # Rotation for THIS menu uses applied_visual.angle
            angle = float(self.applied_visual.get("angle", 0.0))
            rotated = pygame.transform.rotozoom(scaled_panel, angle, 1.0)
            rot_rect = rotated.get_rect(
                center=(panel_x + panel_w // 2, panel_y + panel_h // 2)
            )

            # Opacity for THIS menu: 1.0 = solid, 0.05 = still faintly visible.
            applied_alpha = float(self.applied_visual.get("alpha", 1.0))
            applied_alpha = max(0.05, min(1.0, applied_alpha))
            rotated.set_alpha(int(applied_alpha * 255))

            overlay.blit(rotated, rot_rect.topleft)


            surface.blit(overlay, (0, 0))
            renderer.present()
            clock.tick(60)


# ---------------------------------------------------------------------------#
# VisualOptionsScene
# ---------------------------------------------------------------------------#


class VisualOptionsScene(Scene):
    """
    Submenu for tweaking the visual properties for CHILD Options menus:

    Edits a `visual` dict with keys:
        - scale_x
        - scale_y
        - offset_x
        - offset_y
        - angle

    It uses the *current* Options window rect as a base, and draws itself
    as a preview of what the CHILD Options menu will look like. So as you
    change angle / scales / offsets, this menu twists and slides in realtime.

    The actual rotation used is (parent_angle + visual['angle']), matching
    how the real child Options menu will be rendered.
    """

    def __init__(
        self,
        base_rect: pygame.Rect,
        depth: int,
        visual: Dict[str, float],
        parent_angle: float,
        parent_alpha: float,
    ) -> None:
        # base_rect is the parent Options window rect; we preview the child.
        self.base_rect = base_rect
        self.depth = depth
        self.visual = visual  # reference, not copy
        self.parent_angle = parent_angle
        self.parent_alpha = parent_alpha
        self.selected_idx = 0

        self._background: Optional[pygame.Surface] = None
        self.ui_font: Optional[pygame.font.Font] = None
        self.small_font: Optional[pygame.font.Font] = None

    def _ensure_fonts(self, renderer) -> None:
        if self.ui_font is not None and self.small_font is not None:
            return

        logical_scale = 0.90
        base_ui = renderer.base_tile * 2
        base_small = 18

        ui_size = max(18, int(base_ui * logical_scale))
        small_size = max(12, int(base_small * logical_scale))

        self.ui_font = pygame.font.SysFont("consolas", ui_size)
        self.small_font = pygame.font.SysFont("consolas", small_size)

    def _compute_preview_rect(self) -> pygame.Rect:
        """
        Compute where the CHILD window would be, relative to base_rect,
        using the current visual (scale_x/y + offset_x/y).
        """
        parent = self.base_rect

        scale_x = float(self.visual.get("scale_x", 0.90))
        scale_y = float(self.visual.get("scale_y", 0.90))
        off_x = float(self.visual.get("offset_x", 0.0))
        off_y = float(self.visual.get("offset_y", 0.0))

        w = int(parent.width * scale_x)
        h = int(parent.height * scale_y)
        x = parent.x + (parent.width - w) // 2 + int(off_x)
        y = parent.y + (parent.height - h) // 2 + int(off_y)
        return pygame.Rect(x, y, w, h)

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()

        # Snapshot (includes parent Options + deeper stack)
        # Only capture once so we don't accidentally include any children on re-entry.
        if self._background is None:
            self._background = surface.copy()

        self._ensure_fonts(renderer)


        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font is not None and small_font is not None

        visual_keys: List[str] = [
            "scale_x",
            "scale_y",
            "offset_x",
            "offset_y",
            "angle",
        ]
        visual_labels: Dict[str, str] = {
            "scale_x": "Child scale X",
            "scale_y": "Child scale Y",
            "offset_x": "Child X offset",
            "offset_y": "Child Y offset",
            "angle": "Child angle",
        }
        visual_steps: Dict[str, float] = {
            "scale_x": 0.05,
            "scale_y": 0.05,
            "offset_x": 25.0,   # wider step
            "offset_y": 25.0,
            "angle": 5.0,
        }
        visual_minmax: Dict[str, tuple[float, float]] = {
            # Very wide scale range
            "scale_x": (0.05, 20.0),
            "scale_y": (0.05, 20.0),
            # Much larger translation range
            "offset_x": (-250.0, 250.0),
            "offset_y": (-250.0, 250.0),
            # Full spins both ways
            "angle": (-360.0, 360.0),
        }
        visual_keys.append("alpha")

        # 1.0 = fully solid, dial down toward transparency.
        visual_labels["alpha"] = "Child opacity"
        visual_steps["alpha"] = 0.05
        # Never let it go fully invisible; minimum 0.05-ish.
        visual_minmax["alpha"] = (0.05, 1.0)




        num_visuals = len(visual_keys)
        back_index = num_visuals
        num_items = num_visuals + 1

        # Logical panel for preview
        logical_w = int(renderer.width * 0.90)
        logical_h = int(renderer.height * 0.90)
        logical_scale_x = logical_w / renderer.width
        logical_scale_y = logical_h / renderer.height

        # --- Key repeat state for smooth parameter scrolling ---
        repeat_key: Optional[int] = None
        repeat_start_ms = 0
        last_repeat_ms = 0
        initial_delay = 300   # ms before repeat starts
        slow_interval = 120   # ms between repeats at first
        fast_interval = 40    # ms between repeats once "ramped up"
        fast_threshold = 900  # ms after which we switch to fast mode


        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        manager.pop_scene()
                        return

                    if event.key in (pygame.K_UP, pygame.K_w):
                        self.selected_idx = (self.selected_idx - 1) % num_items
                        # arm repeat for nav up
                        repeat_key = event.key
                        t = pygame.time.get_ticks()
                        repeat_start_ms = t
                        last_repeat_ms = t

                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        self.selected_idx = (self.selected_idx + 1) % num_items
                        # arm repeat for nav down
                        repeat_key = event.key
                        t = pygame.time.get_ticks()
                        repeat_start_ms = t
                        last_repeat_ms = t

                    elif event.key in (
                        pygame.K_LEFT,
                        pygame.K_RIGHT,
                        pygame.K_RETURN,
                        pygame.K_SPACE,
                    ):
                        idx = self.selected_idx

                        if idx < num_visuals:
                            v_key = visual_keys[idx]
                            step = visual_steps[v_key]
                            v_min, v_max = visual_minmax[v_key]
                            cur = float(self.visual.get(v_key, 0.0))

                            if event.key == pygame.K_LEFT:
                                cur -= step
                            elif event.key in (
                                pygame.K_RIGHT,
                                pygame.K_RETURN,
                                pygame.K_SPACE,
                            ):
                                cur += step

                            cur = max(v_min, min(v_max, cur))
                            self.visual[v_key] = cur

                            # arm repeat only for LEFT/RIGHT (not Enter/Space)
                            if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                                repeat_key = event.key
                                t = pygame.time.get_ticks()
                                repeat_start_ms = t
                                last_repeat_ms = t
                        else:
                            # Back
                            manager.pop_scene()
                            return

                elif event.type == pygame.KEYUP:
                    # stop repeating when key is released
                    if event.key == repeat_key:
                        repeat_key = None



            # --- Synthetic key repeat for held nav / slider keys ---
            if repeat_key is not None:
                now = pygame.time.get_ticks()
                held_ms = now - repeat_start_ms

                if held_ms >= initial_delay:
                    interval = fast_interval if held_ms >= fast_threshold else slow_interval
                    if now - last_repeat_ms >= interval:
                        last_repeat_ms = now

                        if repeat_key in (pygame.K_UP, pygame.K_w):
                            self.selected_idx = (self.selected_idx - 1) % num_items

                        elif repeat_key in (pygame.K_DOWN, pygame.K_s):
                            self.selected_idx = (self.selected_idx + 1) % num_items

                        elif repeat_key in (pygame.K_LEFT, pygame.K_RIGHT):
                            idx = self.selected_idx
                            if idx < num_visuals:
                                v_key = visual_keys[idx]
                                step = visual_steps[v_key]
                                v_min, v_max = visual_minmax[v_key]
                                cur = float(self.visual.get(v_key, 0.0))

                                if repeat_key == pygame.K_LEFT:
                                    cur -= step
                                else:  # RIGHT
                                    cur += step

                                cur = max(v_min, min(v_max, cur))
                                self.visual[v_key] = cur



            # ----------------- DRAW -----------------
            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            overlay = pygame.Surface((renderer.width, renderer.height), pygame.SRCALPHA)

            # Preview rect for the child window
            panel_rect = self._compute_preview_rect()
            panel_x, panel_y, panel_w, panel_h = panel_rect

            border_thickness = max(
                1, int(2 * min(logical_scale_x, logical_scale_y))
            )

            # Draw to logical panel surface
            logical_surface = pygame.Surface((logical_w, logical_h), pygame.SRCALPHA)
            pygame.draw.rect(
                logical_surface, (20, 20, 40, 235), (0, 0, logical_w, logical_h)
            )
            pygame.draw.rect(
                logical_surface,
                (220, 220, 240, 240),
                (0, 0, logical_w, logical_h),
                border_thickness,
            )

            # Title
            title_str = "Options Options"
            title_text = ui_font.render(title_str, True, renderer.fg)
            title_y = int(16 * logical_scale_y)
            logical_surface.blit(
                title_text,
                ((logical_w - title_text.get_width()) // 2, title_y),
            )

            # Entries
            y = int(90 * logical_scale_y)
            line_gap = max(1, int(6 * logical_scale_y))
            base_x = int(32 * logical_scale_x)

            for i, v_key in enumerate(visual_keys):
                selected = (i == self.selected_idx)
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "

                val = self.visual.get(v_key, 0.0)
                if "scale" in v_key:
                    val_str = f"{val:.2f}"
                elif v_key == "angle":
                    val_str = f"{val:.0f}°"
                elif v_key == "alpha":
                    val_str = f"{val:.2f}"   # show opacity as 0.00–1.00
                else:
                    val_str = str(int(val))


                label = visual_labels[v_key]
                line = f"{prefix}{label}: {val_str}"
                text = ui_font.render(line, True, color)
                logical_surface.blit(text, (base_x, y))
                y += text.get_height() + line_gap

            # Back
            selected = (self.selected_idx == back_index)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            back_text = ui_font.render(prefix + "Back", True, color)
            logical_surface.blit(back_text, (base_x, y))

            # Hint
            hint = small_font.render(
                "↑/↓ or W/S to move • ←/→ or Enter/Space to change • Esc to return",
                True,
                renderer.dim,
            )
            hint_y = logical_h - int(32 * logical_scale_y)
            hint_x = (logical_w - hint.get_width()) // 2
            logical_surface.blit(hint, (hint_x, hint_y))

            # Anisotropic scale logical panel to preview rect
            scaled_panel = pygame.transform.smoothscale(
                logical_surface, (panel_w, panel_h)
            )

            # Rotate preview with total angle (parent + child step)
            step_angle = float(self.visual.get("angle", 0.0))
            angle_total = self.parent_angle + step_angle
            rotated = pygame.transform.rotozoom(scaled_panel, angle_total, 1.0)
            rot_rect = rotated.get_rect(
                center=(panel_x + panel_w // 2, panel_y + panel_h // 2)
            )

            # Opacity factor (preview): multiplicative, like the real child.
            # total_alpha = parent_alpha * child_factor
            child_factor = float(self.visual.get("alpha", 1.0))
            child_factor = max(0.05, min(1.0, child_factor))

            total_alpha = self.parent_alpha * child_factor
            total_alpha = max(0.05, min(1.0, total_alpha))

            rotated.set_alpha(int(total_alpha * 255))
            overlay.blit(rotated, rot_rect.topleft)



            surface.blit(overlay, (0, 0))
            renderer.present()
            clock.tick(60)
