from __future__ import annotations

from typing import List, Dict, Optional

import pygame

from .base import (
    Scene,
    MenuInput,
    MENU_ACTION_UP,
    MENU_ACTION_DOWN,
    MENU_ACTION_LEFT,
    MENU_ACTION_RIGHT,
    MENU_ACTION_ACTIVATE,
    MENU_ACTION_BACK,
    MENU_ACTION_FULLSCREEN,
)
from edgecaster.visuals import VisualProfile, apply_visual_panel
from .keybinds_scene import KeybindsScene
from .game_input import load_bindings_full, save_bindings_file


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

        # Standardized menu input (with key-repeat + numpad)
        self._menu_input = MenuInput()

        # Panel hitboxes (panel-local rects)
        self.item_rects: List[pygame.Rect] = []

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
                    "alpha": 1.0,  # 1.0 = fully solid
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
                "alpha": 1.0,  # 1.0 = fully solid
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
        menu = self._menu_input

        # Snapshot current screen (dungeon, or parent options stack)
        # Only do this once per instance, so popping a child doesn't "bake in" its pixels.
        if self._background is None:
            self._background = surface.copy()

        # Geometry + fonts
        self._ensure_window_rect(renderer)
        self._ensure_fonts(renderer)

        rect = self.window_rect
        assert rect is not None

        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font is not None and small_font is not None

        # Shared boolean options
        toggles: Dict[str, bool] = manager.options
        toggle_keys: List[str] = list(toggles.keys())

        # Menu layout:
        # 0..len(toggles)-1 : boolean toggles
        # len(toggles)      : "Options" (recursive)
        # len(toggles)+1    : "Options Options" (visual submenu)
        # len(toggles)+2    : "Controls" (keybindings)
        # len(toggles)+3    : "Developer mode" (stat editor)
        # len(toggles)+4    : "Back"
        num_toggles = len(toggles)
        options_index = num_toggles
        options_options_index = num_toggles + 1
        controls_index = num_toggles + 2
        dev_index = num_toggles + 3
        back_index = num_toggles + 4
        num_items = num_toggles + 5

        # Logical panel size (used for anisotropic scaling)
        logical_w = int(renderer.width * 0.90)
        logical_h = int(renderer.height * 0.90)
        logical_scale_x = logical_w / renderer.width
        logical_scale_y = logical_h / renderer.height

        # Precompute some static text
        title_str = "Options" if self.depth == 0 else f"Options (depth {self.depth})"
        title_text = ui_font.render(title_str, True, renderer.fg)

        seed_str = "Use child menus to tweak visuals"
        seed_text = small_font.render(seed_str, True, renderer.dim)

        running = True

        def handle_action(action: Optional[str]) -> bool:
            """
            Handle a logical menu action. Returns True if this menu should close.
            """
            nonlocal running

            if action is None:
                return False

            if action == MENU_ACTION_FULLSCREEN:
                renderer.toggle_fullscreen()
                return False

            if action == MENU_ACTION_BACK:
                manager.pop_scene()
                running = False
                return True

            if action == MENU_ACTION_UP:
                self.selected_idx = (self.selected_idx - 1) % num_items
                return False

            if action == MENU_ACTION_DOWN:
                self.selected_idx = (self.selected_idx + 1) % num_items
                return False

            if action in (
                MENU_ACTION_LEFT,
                MENU_ACTION_RIGHT,
                MENU_ACTION_ACTIVATE,
            ):
                idx = self.selected_idx

                # Boolean toggles (manager.options)
                if idx < num_toggles:
                    key = toggle_keys[idx]
                    toggles[key] = not toggles[key]
                    if key.lower() == "fullscreen":
                        renderer.toggle_fullscreen()
                    return False

                # "Options" -> recursive child
                if idx == options_index:
                    if self.depth < self.MAX_DEPTH:
                        child_rect = self._compute_child_rect()

                        child_applied = dict(self.applied_visual)

                        # Angle accumulates: parent angle + step
                        parent_angle = float(
                            self.applied_visual.get("angle", 0.0)
                        )
                        step_angle = float(
                            self.child_visual.get("angle", 0.0)
                        )
                        child_applied["angle"] = parent_angle + step_angle

                        # Opacity accumulates multiplicatively:
                        # child opacity = parent opacity * child factor
                        parent_alpha = float(
                            self.applied_visual.get("alpha", 1.0)
                        )
                        step_alpha = float(
                            self.child_visual.get("alpha", 1.0)
                        )
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

                    running = False
                    return True

                # "Options Options" -> visual submenu (edits child_visual)
                if idx == options_options_index:
                    parent_angle = float(
                        self.applied_visual.get("angle", 0.0)
                    )
                    parent_alpha = float(
                        self.applied_visual.get("alpha", 1.0)
                    )
                    visual_scene = VisualOptionsScene(
                        base_rect=self.window_rect,
                        depth=self.depth + 1,
                        visual=self.child_visual,  # reference
                        parent_angle=parent_angle,
                        parent_alpha=parent_alpha,
                    )
                    manager.push_scene(visual_scene)
                    running = False
                    return True

                # Controls -> keybindings
                if idx == controls_index:
                    child_rect = self._compute_child_rect()
                    kb_scene = KeybindsScene(
                        base_rect=child_rect,
                        depth=self.depth + 1,
                        bindings=dict(manager.keybindings.get("bindings", {})),
                        move_bindings=dict(manager.keybindings.get("move_bindings", {})),
                    )
                    manager.push_scene(kb_scene)
                    running = False
                    return True

                # Developer mode -> stat editor
                if idx == dev_index:
                    target_char = getattr(manager, "character", None)
                    if target_char is None and getattr(manager, "current_game", None):
                        target_char = getattr(manager.current_game, "character", None)
                    if target_char is not None:
                        child_rect = self._compute_child_rect()
                        dev_scene = DeveloperOptionsScene(
                            base_rect=child_rect,
                            depth=self.depth + 1,
                            character=target_char,
                        )
                        manager.push_scene(dev_scene)
                        running = False
                        return True
                    return False

                # "Back"
                manager.pop_scene()
                running = False
                return True

            return False

        while running:
            # ----------------- EVENTS -----------------
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    action = menu.handle_keydown(event.key)
                    if handle_action(action):
                        break

                elif event.type == pygame.KEYUP:
                    menu.handle_keyup(event.key)

            if not running:
                break

            # Key repeat (hold-to-accelerate for navigation)
            repeat_action = menu.update()
            if handle_action(repeat_action):
                break

            # ----------------- DRAW -----------------
            # Restore snapshot (which already contains deeper stack)
            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            overlay = pygame.Surface((renderer.width, renderer.height), pygame.SRCALPHA)
            if self.depth == 0:
                overlay.fill((0, 0, 0, 140))  # dim at root only
            surface.blit(overlay, (0, 0))

            # ---- Draw panel to logical surface (for anisotropic scaling) ----
            logical_surface = pygame.Surface((logical_w, logical_h), pygame.SRCALPHA)

            # Panel background + border
            border_thickness = max(
                1, int(2 * min(logical_scale_x, logical_scale_y))
            )
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

            y = 20
            logical_surface.blit(title_text, (20, y))
            y += title_text.get_height() + 10

            logical_surface.blit(seed_text, (20, y))
            y += seed_text.get_height() + 15

            # Build display options list (labels + values)
            self.item_rects = []
            for idx in range(num_items):
                is_selected = (idx == self.selected_idx)
                color = renderer.player_color if is_selected else renderer.fg

                if idx < num_toggles:
                    label = toggle_keys[idx]
                    value = "On" if toggles[label] else "Off"
                elif idx == options_index:
                    label = "Options"
                    value = ""
                elif idx == options_options_index:
                    label = "Options Options"
                    value = ""
                elif idx == controls_index:
                    label = "Controls"
                    value = ""
                elif idx == dev_index:
                    label = "Developer mode"
                    value = ""
                else:
                    label = "Back"
                    value = ""

                left_text = ui_font.render(label, True, color)
                logical_surface.blit(left_text, (40, y))

                if value:
                    value_text = ui_font.render(str(value), True, color)
                    vx = logical_w - value_text.get_width() - 40
                    logical_surface.blit(value_text, (vx, y))
                else:
                    value_text = None

                # Store panel-local rect
                self.item_rects.append(
                    pygame.Rect(
                        40,
                        y,
                        logical_w - 80,
                        left_text.get_height(),
                    )
                )

                y += left_text.get_height() + 10

            # Footer hint
            hint = small_font.render(
                "↑/↓ to move • ←/→ or Enter to change • Esc to return • F11 fullscreen",
                True,
                renderer.dim,
            )
            hint_y = logical_h - hint.get_height() - 16
            hint_x = (logical_w - hint.get_width()) // 2
            logical_surface.blit(hint, (hint_x, hint_y))

            # ---- Anisotropic scale logical panel to this menu's rect ----
            panel = pygame.transform.smoothscale(
                logical_surface, rect.size
            )

            # ---- Blit via VisualProfile ----
            angle = float(self.applied_visual.get("angle", 0.0))
            alpha = float(self.applied_visual.get("alpha", 1.0))
            visual = self.visual_profile or VisualProfile(angle=angle, alpha=alpha)

            apply_visual_panel(surface, panel, rect, visual)

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
        - alpha

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

        # Standard menu input (with hold-to-accelerate + numpad)
        self._menu_input = MenuInput()

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
        menu = self._menu_input

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
            "offset_x": 25.0,  # wider step
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

        # Add alpha control
        visual_keys.append("alpha")
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

        running = True

        def handle_action(action: Optional[str]) -> bool:
            """
            Handle a logical menu action. Returns True if this menu should close.
            """
            nonlocal running

            if action is None:
                return False

            if action == MENU_ACTION_FULLSCREEN:
                renderer.toggle_fullscreen()
                return False

            if action == MENU_ACTION_BACK:
                manager.pop_scene()
                running = False
                return True

            if action == MENU_ACTION_UP:
                self.selected_idx = (self.selected_idx - 1) % num_items
                return False

            if action == MENU_ACTION_DOWN:
                self.selected_idx = (self.selected_idx + 1) % num_items
                return False

            if action in (
                MENU_ACTION_LEFT,
                MENU_ACTION_RIGHT,
                MENU_ACTION_ACTIVATE,
            ):
                idx = self.selected_idx

                # Back
                if idx == back_index:
                    manager.pop_scene()
                    running = False
                    return True

                # Adjust a visual parameter
                if idx < num_visuals:
                    v_key = visual_keys[idx]
                    step = visual_steps[v_key]
                    v_min, v_max = visual_minmax[v_key]
                    cur = float(self.visual.get(v_key, 0.0))

                    if action == MENU_ACTION_LEFT:
                        cur -= step
                    else:
                        # Right or Activate both move forward
                        cur += step

                    cur = max(v_min, min(v_max, cur))
                    self.visual[v_key] = cur
                    return False

            return False

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if event.type == pygame.KEYDOWN:
                    action = menu.handle_keydown(event.key)
                    if handle_action(action):
                        break

                elif event.type == pygame.KEYUP:
                    menu.handle_keyup(event.key)

            if not running:
                break

            # Key-repeat (for held nav / sliders)
            repeat_action = menu.update()
            if handle_action(repeat_action):
                break

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
                selected = i == self.selected_idx
                color = renderer.player_color if selected else renderer.fg
                prefix = "▶ " if selected else "  "

                val = self.visual.get(v_key, 0.0)
                if "scale" in v_key:
                    val_str = f"{val:.2f}"
                elif v_key == "angle":
                    val_str = f"{val:.0f}°"
                elif v_key == "alpha":
                    val_str = f"{val:.2f}"  # show opacity as 0.00–1.00
                else:
                    val_str = str(int(val))

                label = visual_labels[v_key]
                line = f"{prefix}{label}: {val_str}"
                text = ui_font.render(line, True, color)
                logical_surface.blit(text, (base_x, y))
                y += text.get_height() + line_gap

            # Back
            selected = self.selected_idx == back_index
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            back_text = ui_font.render(prefix + "Back", True, color)
            logical_surface.blit(back_text, (base_x, y))

            # Hint
            hint = small_font.render(
                "↑/↓, W/S, or Numpad 8/2 to move • "
                "←/→, A/D, or Numpad 4/6 to change • "
                "Enter/Space/KP Enter to change • Esc to return • F11 fullscreen",
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


# ---------------------------------------------------------------------------#
# DeveloperOptionsScene (stat editor)
# ---------------------------------------------------------------------------#


class DeveloperOptionsScene(Scene):
    """Simple developer page to tweak base character stats."""

    def __init__(self, base_rect: pygame.Rect, depth: int, character) -> None:
        self.base_rect = base_rect
        self.depth = depth
        self.character = character
        self.selected_idx = 0
        self._background: Optional[pygame.Surface] = None
        self.ui_font: Optional[pygame.font.Font] = None
        self.small_font: Optional[pygame.font.Font] = None
        self._menu_input = MenuInput()

    def _ensure_fonts(self, renderer) -> None:
        if self.ui_font is not None and self.small_font is not None:
            return
        base_ui = renderer.base_tile * 2
        ui_size = max(18, int(base_ui * 0.9))
        small_size = max(12, int(18 * 0.9))
        self.ui_font = pygame.font.SysFont("consolas", ui_size)
        self.small_font = pygame.font.SysFont("consolas", small_size)

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        surface = renderer.surface
        clock = pygame.time.Clock()
        menu = self._menu_input

        if self._background is None:
            self._background = surface.copy()

        self._ensure_fonts(renderer)
        ui_font = self.ui_font
        small_font = self.small_font
        assert ui_font and small_font

        stats_keys = ["con", "res", "int", "agi"]

        def handle_action(action: Optional[str]) -> bool:
            nonlocal stats_keys
            if action is None:
                return False
            if action == MENU_ACTION_FULLSCREEN:
                renderer.toggle_fullscreen()
                return False
            if action == MENU_ACTION_BACK:
                manager.pop_scene()
                return True
            if action == MENU_ACTION_UP:
                self.selected_idx = (self.selected_idx - 1) % (len(stats_keys) + 1)
                return False
            if action == MENU_ACTION_DOWN:
                self.selected_idx = (self.selected_idx + 1) % (len(stats_keys) + 1)
                return False
            if action in (MENU_ACTION_LEFT, MENU_ACTION_RIGHT, MENU_ACTION_ACTIVATE):
                # Back
                if self.selected_idx == len(stats_keys):
                    manager.pop_scene()
                    return True
                key = stats_keys[self.selected_idx]
                delta = -1 if action == MENU_ACTION_LEFT else 1
                self.character.stats[key] = max(0, self.character.stats.get(key, 0) + delta)
                return False
            return False

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return
                if event.type == pygame.KEYDOWN:
                    if handle_action(menu.handle_keydown(event.key)):
                        running = False
                        break
                elif event.type == pygame.KEYUP:
                    menu.handle_keyup(event.key)
            if not running:
                break

            repeat = menu.update()
            if handle_action(repeat):
                break

            if self._background is not None:
                surface.blit(self._background, (0, 0))
            else:
                surface.fill(renderer.bg)

            panel = pygame.Surface((self.base_rect.width, self.base_rect.height), pygame.SRCALPHA)
            pygame.draw.rect(panel, (20, 20, 40, 235), panel.get_rect())
            pygame.draw.rect(panel, (220, 220, 240, 240), panel.get_rect(), 2)

            title = ui_font.render("Developer mode", True, renderer.fg)
            panel.blit(title, ((panel.get_width() - title.get_width()) // 2, 12))

            y = 60
            line_gap = 8
            for idx, key in enumerate(stats_keys):
                selected = self.selected_idx == idx
                prefix = "-> " if selected else "  "
                val = self.character.stats.get(key, 0)
                line = f"{prefix}Base[{key.upper()}]: {val}"
                text = ui_font.render(line, True, renderer.player_color if selected else renderer.fg)
                panel.blit(text, (24, y))
                y += text.get_height() + line_gap

            # Back
            selected = self.selected_idx == len(stats_keys)
            prefix = "-> " if selected else "  "
            back_text = ui_font.render(prefix + "Back", True, renderer.player_color if selected else renderer.fg)
            panel.blit(back_text, (24, y))

            # Hint
            hint = small_font.render("Arrows/Numpad to select, Left/Right to adjust, Esc to return", True, renderer.dim)
            panel.blit(hint, (24, panel.get_height() - hint.get_height() - 12))

            surface.blit(panel, self.base_rect.topleft)
            renderer.present()
            clock.tick(60)
