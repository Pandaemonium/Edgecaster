from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Any, List, Tuple

import pygame

from .base import (
    PopupMenuScene,
    MENU_ACTION_LEFT,
    MENU_ACTION_RIGHT,
    MENU_ACTION_ACTIVATE,
    MENU_ACTION_UP,
    MENU_ACTION_DOWN,
    MENU_ACTION_BACK,
    MENU_ACTION_FULLSCREEN,
)

from edgecaster.visuals import VisualProfile

from .keybinds_scene import KeybindsScene
from .base import MenuFrameWidget
from edgecaster.ui.widgets import TwoColumnListWidget


# ---------------------------------------------------------------------------#
# Small menu item model (keeps get_menu_items() simple & debuggable)
# ---------------------------------------------------------------------------#

@dataclass(frozen=True)
class OptionItem:
    kind: str  # "toggle" | "submenu" | "action" | "back" | "label"
    label: str
    key: Optional[str] = None
    value: Optional[str] = None

    def __str__(self) -> str:
        # IMPORTANT: For TwoColumnListWidget we want the left column to just be the label.
        return self.label


# ---------------------------------------------------------------------------#
# OptionsScene (widget-driven, recursive "artichoke" popups)
# ---------------------------------------------------------------------------#

class OptionsScene(PopupMenuScene):
    """
    Recursive options popup (now PanelScene/Widget-based).

    Preserves legacy semantics:
      - Left column: option name
      - Right column: current value (On/Off)
      - Left/Right/Enter all "activate/adjust" like the old Options menu
    """

    MAX_DEPTH = 99

    FOOTER_TEXT = (
        "↑/↓ to move • ←/→ or Enter/Space to change • Esc to return • F11 fullscreen"
    )

    def __init__(
        self,
        *,
        window_rect: Optional[pygame.Rect] = None,
        depth: int = 0,
        applied_visual: Optional[Dict[str, float]] = None,
        child_visual: Optional[Dict[str, float]] = None,
    ) -> None:
        self.depth = int(depth)
        self._items: List[OptionItem] = []

        # How THIS menu is drawn
        if applied_visual is None:
            if self.depth == 0:
                self.applied_visual: Dict[str, float] = {
                    "scale_x": 0.90,
                    "scale_y": 0.90,
                    "offset_x": 0.0,
                    "offset_y": 0.0,
                    "angle": 0.0,
                    "alpha": 1.0,
                }
            else:
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

        # How CHILD menus are drawn (editable by VisualOptionsScene)
        if child_visual is None:
            self.child_visual: Dict[str, float] = {
                "scale_x": 0.92,
                "scale_y": 0.92,
                "offset_x": 0.0,
                "offset_y": 0.0,
                "angle": 0.0,
                "alpha": 0.92,
            }
        else:
            self.child_visual = dict(child_visual)
            self.child_visual.setdefault("alpha", 1.0)

        super().__init__(
            window_rect=window_rect,
            dim_background=(self.depth == 0),
            scale=0.7,
        )

        self.visual_profile = VisualProfile(
            angle=float(self.applied_visual.get("angle", 0.0)),
            alpha=float(self.applied_visual.get("alpha", 1.0)),
        )
        self.visual_effects = []

    # ------------------------------------------------------------------ #
    # Geometry helpers
    # ------------------------------------------------------------------ #

    def _ensure_window_rect(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if self.window_rect is not None:
            return

        r = manager.renderer
        base = pygame.Rect(0, 0, r.width, r.height)

        sx = float(self.applied_visual.get("scale_x", 0.9))
        sy = float(self.applied_visual.get("scale_y", 0.9))
        ox = float(self.applied_visual.get("offset_x", 0.0))
        oy = float(self.applied_visual.get("offset_y", 0.0))

        w = max(40, int(base.width * sx))
        h = max(40, int(base.height * sy))

        x = base.x + (base.width - w) // 2 + int(ox)
        y = base.y + (base.height - h) // 2 + int(oy)

        self.window_rect = pygame.Rect(x, y, w, h)

    def _compute_child_rect(self) -> pygame.Rect:
        assert self.window_rect is not None
        base = self.window_rect

        sx = float(self.child_visual.get("scale_x", 0.92))
        sy = float(self.child_visual.get("scale_y", 0.92))
        ox = float(self.child_visual.get("offset_x", 0.0))
        oy = float(self.child_visual.get("offset_y", 0.0))

        w = max(30, int(base.width * sx))
        h = max(30, int(base.height * sy))

        x = base.x + (base.width - w) // 2 + int(ox)
        y = base.y + (base.height - h) // 2 + int(oy)

        return pygame.Rect(x, y, w, h)

    # ------------------------------------------------------------------ #
    # GeneralMenuScene hooks
    # ------------------------------------------------------------------ #

    def get_ascii_art(self) -> Optional[str]:
        return "Options"

    def get_body_text(self) -> Optional[str]:
        if self.depth == 0:
            return "Use child menus to tweak visuals"
        return None

    def get_menu_items(self) -> list[Any]:
        items: List[OptionItem] = []

        mgr = getattr(self, "_last_manager", None)
        toggles = getattr(mgr, "options", None) if mgr is not None else None

        if isinstance(toggles, dict) and toggles:
            for k in list(toggles.keys()):
                v = "On" if bool(toggles.get(k)) else "Off"
                items.append(OptionItem("toggle", str(k), key=str(k), value=v))

        items.append(OptionItem("submenu", "Options"))
        items.append(OptionItem("submenu", "Options Options"))
        items.append(OptionItem("submenu", "Controls"))
        items.append(OptionItem("submenu", "Developer mode"))
        items.append(OptionItem("back", "Back"))

        self._items = items
        return list(items)

    def _build_widgets(self, items: list[Any]) -> None:
        """
        Override standard menu build to use a two-column list (label + value).
        """
        # Build banner/body/footer, but we will replace the list widget.
        super()._build_widgets(items)

        self._list = TwoColumnListWidget(
            items,
            selected_index=self.selected_idx,
            on_activate=self._on_list_activate,
            line_spacing=4,
            padding=4,
            value_gap=24,
        )
        self._list.rect = pygame.Rect(0, 0, 0, 0)

        ascii_art = self.get_ascii_art() or ""
        banner_is_background = bool(ascii_art) and self.wants_banner_background(ascii_art)

        self.root = MenuFrameWidget(
            banner=self._banner,
            body=self._body,
            list_widget=self._list,
            footer=self._footer,
            top_pad=16,
            bottom_pad=8,
            gap_after_banner=10,
            gap_after_body=12,
            min_list_height=120,
            max_list_width=700,
            max_body_width=600,
            banner_is_background=banner_is_background,
        )

        self.root.rect = pygame.Rect(0, 0, 0, 0)

    def update(self, dt_ms: int, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        # Let get_menu_items show live toggle values.
        self._last_manager = manager  # type: ignore[attr-defined]
        super().update(dt_ms, manager)

    # ------------------------------------------------------------------ #
    # Input behavior (faithful to legacy)
    # ------------------------------------------------------------------ #

    def _handle_action(self, action: Optional[str], manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if action is None:
            return

        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        # Legacy: left/right/activate all "adjust/enter"
        if action in (MENU_ACTION_LEFT, MENU_ACTION_RIGHT, MENU_ACTION_ACTIVATE):
            self._activate_or_adjust(manager)
            return

        super()._handle_action(action, manager)

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        # IMPORTANT: this prevents the base NotImplementedError and makes mouse-click activation work.
        self.selected_idx = index
        self._activate_or_adjust(manager)
        return False

    def _activate_or_adjust(self, manager: "SceneManager") -> None:
        if not self._items:
            return

        idx = max(0, min(self.selected_idx, len(self._items) - 1))
        item = self._items[idx]

        # Boolean toggle
        if item.kind == "toggle" and item.key:
            toggles = getattr(manager, "options", None)
            if isinstance(toggles, dict):
                toggles[item.key] = not bool(toggles.get(item.key))
                if item.key.lower() == "fullscreen":
                    manager.renderer.toggle_fullscreen()
            return

        # Submenus
        if item.label == "Options":
            if self.depth >= self.MAX_DEPTH:
                return

            child_rect = self._compute_child_rect()

            child_applied = dict(self.applied_visual)
            child_applied["angle"] = float(self.applied_visual.get("angle", 0.0)) + float(self.child_visual.get("angle", 0.0))

            parent_alpha = float(self.applied_visual.get("alpha", 1.0))
            step_alpha = float(self.child_visual.get("alpha", 1.0))
            new_alpha = max(0.05, min(1.0, parent_alpha * step_alpha))
            child_applied["alpha"] = new_alpha

            manager.push_scene(
                OptionsScene(
                    window_rect=child_rect,
                    depth=self.depth + 1,
                    applied_visual=child_applied,
                    child_visual=dict(self.child_visual),
                )
            )
            return

        if item.label == "Options Options":
            if self.window_rect is None:
                return
            manager.push_scene(
                VisualOptionsScene(
                    base_rect=self.window_rect,
                    depth=self.depth + 1,
                    visual=self.child_visual,  # reference
                    parent_angle=float(self.applied_visual.get("angle", 0.0)),
                    parent_alpha=float(self.applied_visual.get("alpha", 1.0)),
                )
            )
            return

        if item.label == "Controls":
            child_rect = self._compute_child_rect()
            kb = getattr(manager, "keybindings", None)
            bindings = dict(getattr(kb, "bindings", {})) if kb is not None else dict(getattr(kb, "get", lambda *_: {})("bindings", {}))
            move_bindings = dict(getattr(kb, "move_bindings", {})) if kb is not None else dict(getattr(kb, "get", lambda *_: {})("move_bindings", {}))

            manager.push_scene(
                KeybindsScene(
                    base_rect=child_rect,
                    depth=self.depth + 1,
                    bindings=bindings,
                    move_bindings=move_bindings,
                )
            )
            return

        if item.label == "Developer mode":
            target_char = getattr(manager, "character", None)
            if target_char is None and getattr(manager, "current_game", None):
                target_char = getattr(manager.current_game, "character", None)
            if target_char is None:
                return

            manager.push_scene(
                DeveloperOptionsScene(
                    base_rect=self._compute_child_rect(),
                    depth=self.depth + 1,
                    character=target_char,
                )
            )
            return

        if item.kind == "back":
            self.on_back(manager)
            return

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        manager.pop_scene()
        return True


# ---------------------------------------------------------------------------#
# VisualOptionsScene + DeveloperOptionsScene
 

class VisualOptionsScene(PopupMenuScene):
    """
    Submenu for tweaking the visual properties for CHILD Options menus.

    Edits a `visual` dict with keys:
        - scale_x, scale_y
        - offset_x, offset_y
        - angle
        - alpha

    This scene is drawn as a PREVIEW of the child Options menu (so it uses
    the child rect and accumulates parent_angle/parent_alpha for rendering).
    """

    FOOTER_TEXT = (
        "↑/↓ select • ←/→ adjust • Enter/Space bump • Esc back • F11 fullscreen"
    )

    _FIELDS: List[Tuple[str, float, float, float]] = [
        # (key, step, min, max)
        ("scale_x", 0.02, 0.20, 1.20),
        ("scale_y", 0.02, 0.20, 1.20),
        ("offset_x", 6.0, -600.0, 600.0),
        ("offset_y", 6.0, -600.0, 600.0),
        ("angle", 3.0, -180.0, 180.0),
        ("alpha", 0.05, 0.05, 1.00),
    ]

    def __init__(
        self,
        *,
        base_rect: pygame.Rect,
        depth: int,
        visual: Dict[str, float],
        parent_angle: float,
        parent_alpha: float,
    ) -> None:
        self.base_rect = base_rect
        self.depth = int(depth)
        self.visual = visual  # reference (edits persist)
        self.parent_angle = float(parent_angle)
        self.parent_alpha = float(parent_alpha)

        self._items: List[OptionItem] = []

        preview_rect = self._compute_preview_rect()

        super().__init__(
            window_rect=preview_rect,
            dim_background=False,
            scale=0.7,
        )

        angle = self.parent_angle + float(self.visual.get("angle", 0.0))
        alpha = self.parent_alpha * float(self.visual.get("alpha", 1.0))
        alpha = max(0.05, min(1.0, alpha))

        self.visual_profile = VisualProfile(angle=angle, alpha=alpha)
        self.visual_effects = []

    def _compute_preview_rect(self) -> pygame.Rect:
        base = self.base_rect
        sx = float(self.visual.get("scale_x", 0.92))
        sy = float(self.visual.get("scale_y", 0.92))
        ox = float(self.visual.get("offset_x", 0.0))
        oy = float(self.visual.get("offset_y", 0.0))

        w = max(30, int(base.width * sx))
        h = max(30, int(base.height * sy))
        x = base.x + (base.width - w) // 2 + int(ox)
        y = base.y + (base.height - h) // 2 + int(oy)
        return pygame.Rect(x, y, w, h)

    def _refresh_preview_geometry(self) -> None:
        self.window_rect = self._compute_preview_rect()

        angle = self.parent_angle + float(self.visual.get("angle", 0.0))
        alpha = self.parent_alpha * float(self.visual.get("alpha", 1.0))
        alpha = max(0.05, min(1.0, alpha))
        self.visual_profile = VisualProfile(angle=angle, alpha=alpha)

    def get_ascii_art(self) -> Optional[str]:
        return "Options Options"

    def get_body_text(self) -> Optional[str]:
        return "Edits how the *next* Options popup will look. This menu is a live preview."

    def get_menu_items(self) -> list[Any]:
        items: List[OptionItem] = []
        for key, step, lo, hi in self._FIELDS:
            v = float(self.visual.get(key, 0.0))
            if key.startswith("scale") or key == "alpha":
                sval = f"{v:.2f}"
            elif key in ("offset_x", "offset_y"):
                sval = f"{int(v):d}"
            else:
                sval = f"{v:.1f}"
            items.append(OptionItem("label", f"{key}: {sval}", key=key))

        items.append(OptionItem("back", "Back"))
        self._items = items
        return list(items)

    def _handle_action(self, action: Optional[str], manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if action is None:
            return

        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        if action == MENU_ACTION_BACK:
            self.on_back(manager)
            return

        n = len(self._items) if self._items else 0
        if n <= 0:
            return

        if action == MENU_ACTION_UP:
            self.selected_idx = (self.selected_idx - 1) % n
            if self._list is not None:
                self._list.selected_index = self.selected_idx
                self._list.ensure_visible(self.selected_idx)
            return
        if action == MENU_ACTION_DOWN:
            self.selected_idx = (self.selected_idx + 1) % n
            if self._list is not None:
                self._list.selected_index = self.selected_idx
                self._list.ensure_visible(self.selected_idx)
            return

        if action in (MENU_ACTION_LEFT, MENU_ACTION_RIGHT, MENU_ACTION_ACTIVATE):
            idx = max(0, min(self.selected_idx, n - 1))
            item = self._items[idx]
            if item.kind == "back":
                self.on_back(manager)
                return

            field_idx = idx
            if field_idx < 0 or field_idx >= len(self._FIELDS):
                return

            key, step, lo, hi = self._FIELDS[field_idx]
            cur = float(self.visual.get(key, 0.0))

            if action == MENU_ACTION_LEFT:
                cur -= step
            else:
                cur += step

            cur = max(lo, min(hi, cur))
            self.visual[key] = float(cur)

            self._refresh_preview_geometry()
            return

        super()._handle_action(action, manager)

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        return False

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        manager.pop_scene()
        return True


# ---------------------------------------------------------------------------#
# DeveloperOptionsScene (widget-driven stat editor)
# ---------------------------------------------------------------------------#

class DeveloperOptionsScene(PopupMenuScene):
    """
    Minimal dev stat editor (ported to PopupMenuScene).

    Preserves the old behavior:
    - Select a stat, use left/right to adjust, enter to bump, esc to return.
    """

    FOOTER_TEXT = (
        "↑/↓ select • ←/→ adjust • Enter/Space bump • Esc back • F11 fullscreen"
    )

    _FIELDS: List[Tuple[str, float]] = [
        ("hp", 1.0),
        ("max_hp", 1.0),
        ("mp", 1.0),
        ("max_mp", 1.0),
        ("strength", 1.0),
        ("dexterity", 1.0),
        ("intelligence", 1.0),
    ]

    def __init__(self, *, base_rect: pygame.Rect, depth: int, character: Any) -> None:
        self.base_rect = base_rect
        self.depth = int(depth)
        self.character = character
        self._items: List[OptionItem] = []

        super().__init__(window_rect=base_rect, dim_background=False, scale=0.7)
        self.visual_profile = VisualProfile(angle=0.0, alpha=1.0)
        self.visual_effects = []

    def get_ascii_art(self) -> Optional[str]:
        return "Developer mode"

    def get_body_text(self) -> Optional[str]:
        name = getattr(self.character, "name", None) or "character"
        return f"Editing stats for {name}."

    def get_menu_items(self) -> list[Any]:
        items: List[OptionItem] = []
        for key, step in self._FIELDS:
            v = getattr(self.character, key, None)
            items.append(OptionItem("label", f"{key}: {v}", key=key))
        items.append(OptionItem("back", "Back"))
        self._items = items
        return list(items)

    def _adjust(self, key: str, delta: float) -> None:
        if not hasattr(self.character, key):
            return
        cur = getattr(self.character, key, 0)
        try:
            cur_f = float(cur)
        except Exception:
            return
        new = cur_f + float(delta)
        if key in ("hp", "max_hp", "mp", "max_mp"):
            new = max(0.0, new)
        setattr(self.character, key, int(new) if float(new).is_integer() else new)

    def _handle_action(self, action: Optional[str], manager: "SceneManager") -> None:  # type: ignore[name-defined]
        if action is None:
            return

        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        if action == MENU_ACTION_BACK:
            self.on_back(manager)
            return

        n = len(self._items) if self._items else 0
        if n <= 0:
            return

        if action == MENU_ACTION_UP:
            self.selected_idx = (self.selected_idx - 1) % n
            if self._list is not None:
                self._list.selected_index = self.selected_idx
                self._list.ensure_visible(self.selected_idx)
            return
        if action == MENU_ACTION_DOWN:
            self.selected_idx = (self.selected_idx + 1) % n
            if self._list is not None:
                self._list.selected_index = self.selected_idx
                self._list.ensure_visible(self.selected_idx)
            return

        if action in (MENU_ACTION_LEFT, MENU_ACTION_RIGHT, MENU_ACTION_ACTIVATE):
            idx = max(0, min(self.selected_idx, n - 1))
            item = self._items[idx]
            if item.kind == "back":
                self.on_back(manager)
                return
            if not item.key:
                return

            step = 1.0
            for k, s in self._FIELDS:
                if k == item.key:
                    step = float(s)
                    break

            if action == MENU_ACTION_LEFT:
                self._adjust(item.key, -step)
            else:
                self._adjust(item.key, step)
            return

        super()._handle_action(action, manager)

    def on_activate(self, index: int, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        return False

    def on_back(self, manager: "SceneManager") -> bool:  # type: ignore[name-defined]
        manager.pop_scene()
        return True
