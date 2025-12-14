from __future__ import annotations

from typing import Dict, List, Tuple, Optional, Any

import pygame
import time
from pathlib import Path

from .base import (
    PanelScene,
    WidgetContext,
    MENU_ACTION_UP,
    MENU_ACTION_DOWN,
    MENU_ACTION_LEFT,
    MENU_ACTION_RIGHT,
    MENU_ACTION_ACTIVATE,
    MENU_ACTION_BACK,
    MENU_ACTION_FULLSCREEN,
    MenuInput,
)
from edgecaster.ui.widgets import Widget, LabelWidget, ButtonWidget, ScaledLabelWidget

from .game_input import (
    save_bindings_file,
    load_bindings_full,
    DEFAULT_BINDINGS,
    DEFAULT_MOVE_BINDINGS,
    format_keybinding,
    encode_keybinding,
)

DEBUG_LOG = Path("C:\\Games\\Edgecaster\\debug.log")


def _dbg(msg: str) -> None:
    try:
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG.open("a", encoding="utf-8") as f:
            ts = time.strftime("%H:%M:%S")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# Simple context map for conflict resolution: commands sharing a context
# cannot reuse the same key; we clear the existing binding.
COMMAND_CONTEXTS = {
    # Movement (dungeon gameplay)
    "move_up": {"dungeon"},
    "move_down": {"dungeon"},
    "move_left": {"dungeon"},
    "move_right": {"dungeon"},
    "move_upleft": {"dungeon"},
    "move_upright": {"dungeon"},
    "move_downleft": {"dungeon"},
    "move_downright": {"dungeon"},
    # Gameplay
    "quick_activate_all": {"dungeon"},
    "open_fractal_editor": {"dungeon"},
    "talk": {"dungeon"},
    "pickup": {"dungeon"},
    "possess_nearest": {"dungeon"},
    "look_action": {"dungeon"},
    "wait": {"dungeon"},
    "toggle_door": {"dungeon"},
    # Navigation / map
    "stairs_up_or_map": {"dungeon"},
    "stairs_down": {"dungeon"},
    "ability_page_prev": {"dungeon"},
    "ability_page_next": {"dungeon"},
    # UI / Meta
    "open_inventory": {"dungeon"},
    "examine": {"dungeon"},
    "toggle_fullscreen": {"global"},
    "escape": {"global"},
    "show_help": {"global"},
    "open_abilities": {"dungeon"},
    "yawp": {"dungeon"},
}


# Base categories of commands shown in the keybinding menu. We’ll append any
# commands that exist in DEFAULT_BINDINGS but aren’t explicitly listed here so
# new bindings automatically surface in the UI.
BASE_KEYBIND_CATEGORIES: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("Movement (Dungeon)", [
        ("move_up", "Move Up"),
        ("move_down", "Move Down"),
        ("move_left", "Move Left"),
        ("move_right", "Move Right"),
        ("move_upleft", "Move Up-Left"),
        ("move_upright", "Move Up-Right"),
        ("move_downleft", "Move Down-Left"),
        ("move_downright", "Move Down-Right"),
    ]),
    ("Gameplay", [
        ("quick_activate_all", "Quick Activate (F)"),
        ("open_fractal_editor", "Open Fractal Editor (+/=)"),
        ("talk", "Talk"),
        ("pickup", "Pick Up"),
        ("possess_nearest", "Possess Nearest"),
        ("look_action", "Look Mode"),
        ("wait", "Wait"),
        ("toggle_door", "Open / Close Door"),
    ]),
    ("Navigation / Map", [
        ("stairs_up_or_map", "Stairs Up / World Map"),
        ("stairs_down", "Stairs Down"),
        ("ability_page_prev", "Abilities Prev Page"),
        ("ability_page_next", "Abilities Next Page"),
    ]),
    ("UI / Meta", [
        ("open_inventory", "Inventory"),
        ("examine", "Examine"),
        ("toggle_fullscreen", "Toggle Fullscreen"),
        ("escape", "Back / Cancel"),
        ("show_help", "Help"),
        ("open_abilities", "Abilities Menu"),
        ("yawp", "Yawp / Debug Shout"),
    ]),
]


_EXTRA_LABELS = {
    "toggle_door": "Open / Close Door",
}


def _build_categories() -> List[Tuple[str, List[Tuple[str, str]]]]:
    listed = {cmd for _, entries in BASE_KEYBIND_CATEGORIES for cmd, _ in entries}
    extras: List[Tuple[str, str]] = []
    for cmd in DEFAULT_BINDINGS.keys():
        if cmd in listed:
            continue
        label = _EXTRA_LABELS.get(cmd, cmd.replace("_", " ").title())
        extras.append((cmd, label))
    cats = list(BASE_KEYBIND_CATEGORIES)
    if extras:
        cats.append(("Other", extras))
    return cats


# Categories used by the scene (includes any auto-added extras).
KEYBIND_CATEGORIES: List[Tuple[str, List[Tuple[str, str]]]] = _build_categories()


_VEC_MAP: Dict[str, Tuple[int, int]] = {
    "move_up": (0, -1),
    "move_down": (0, 1),
    "move_left": (-1, 0),
    "move_right": (1, 0),
    "move_upleft": (-1, -1),
    "move_upright": (1, -1),
    "move_downleft": (-1, 1),
    "move_downright": (1, 1),
}


# -----------------------------------------------------------------------------
# Widgets
# -----------------------------------------------------------------------------

class KeybindGridWidget(Widget):
    """
    Draws and manages interaction with the keybinding grid:
      - category headers
      - each command row has 3 binding slots ("cells")
      - click cell to start capture
      - click 'x' to clear that slot
      - hover selects row/slot
      - wheel scrolls
    """

    def __init__(self, scene: "KeybindsScene") -> None:
        super().__init__()
        self.scene = scene

        # Pixel scroll (panel-local)
        self.scroll_px: int = 0

        # Cached layout info (rebuilt every draw/layout)
        self._rows: List[Dict[str, Any]] = []
        self._content_h: int = 0

        # Hover tracking
        self._hover_row: Optional[int] = None
        self._hover_slot: Optional[int] = None
        self._hover_over_x: bool = False

    def _font(self, ctx: WidgetContext):
        # The keybind UI should use the "menu-ish" font (map_font looks good in your current renderer).
        return getattr(ctx.renderer, "map_font", getattr(ctx.renderer, "font", getattr(ctx.renderer, "small_font")))

    def _hint_font(self, ctx: WidgetContext):
        return getattr(ctx.renderer, "small_font", getattr(ctx.renderer, "font"))

    def clamp_scroll(self) -> None:
        max_off = max(0, self._content_h - self.rect.height)
        self.scroll_px = max(0, min(self.scroll_px, max_off))

    def ensure_row_visible(self, row_idx: int) -> None:
        # Ensure selected flattened-row (scene.selected_idx) stays visible.
        if row_idx < 0:
            return

        # Find the matching visual row entry (headers are interleaved in self._rows)
        target_rect: Optional[pygame.Rect] = None
        for r in self._rows:
            if r.get("kind") == "row" and r.get("row_idx") == row_idx:
                rr = r.get("row_rect")
                if isinstance(rr, pygame.Rect):
                    target_rect = rr
                break

        if target_rect is None:
            return

        top = target_rect.top
        bottom = target_rect.bottom
        view_top = self.rect.top
        view_bottom = self.rect.bottom

        if top < view_top:
            self.scroll_px = max(0, self.scroll_px - (view_top - top))
        elif bottom > view_bottom:
            self.scroll_px = self.scroll_px + (bottom - view_bottom)

        self.clamp_scroll()


    def _build_rows(self, ctx: WidgetContext) -> None:
        """
        Build a list of draw+hit-test rectangles for each visible row/cell.
        Stores panel-local rects, already accounting for scroll.
        """
        font = self._font(ctx)

        # Layout constants
        pad_x = 16
        pad_y = 10
        gap_y = 8
        header_gap = 4

        cell_h = 32
        col_w = 118
        col_gap = 10
        x_glyph_w = font.size("x")[0]

        # Compute max label width so columns align nicely.
        flat = self.scene._flatten_entries()
        max_label_w = 0
        for _, _cmd, label in flat:
            max_label_w = max(max_label_w, font.size(label)[0])
        max_label_w = min(max_label_w, max(120, self.rect.width // 2))

        # Start drawing inside our rect
        x0 = self.rect.x + pad_x
        y = self.rect.y + pad_y - self.scroll_px

        self._rows = []
        current_cat = -1

        for idx, (cat_idx, cmd, label) in enumerate(flat):
            if cat_idx != current_cat:
                current_cat = cat_idx
                header = KEYBIND_CATEGORIES[cat_idx][0]
                h_surf = font.render(header, True, getattr(ctx.renderer, "dim", (150, 150, 150)))
                h_rect = h_surf.get_rect(topleft=(x0, y))
                self._rows.append({
                    "kind": "header",
                    "cat_idx": cat_idx,
                    "cmd": None,
                    "label": header,
                    "row_rect": h_rect,
                })
                y += h_rect.height + header_gap

            # Row label rect (for alignment only)
            label_w = min(font.size(label)[0], max_label_w)
            label_rect = pygame.Rect(x0, y, label_w, cell_h)

            # Cells start after label with a small gap.
            cells_x = x0 + max_label_w + 16

            # 3 cell rects + their 'x' rects
            cell_rects: List[pygame.Rect] = []
            x_rects: List[pygame.Rect] = []
            for slot in range(3):
                cx = cells_x + slot * (col_w + col_gap)
                cell = pygame.Rect(cx, y, col_w, cell_h)
                cell_rects.append(cell)
                x_rects.append(pygame.Rect(cell.right - x_glyph_w - 8, cell.y + 4, x_glyph_w + 2, cell_h - 8))

            row_rect = pygame.Rect(self.rect.x, y, self.rect.width, cell_h)

            self._rows.append({
                "kind": "row",
                "row_idx": idx,              # index into flattened list
                "cat_idx": cat_idx,
                "cmd": cmd,
                "label": label,
                "row_rect": row_rect,
                "label_rect": label_rect,
                "cell_rects": cell_rects,
                "x_rects": x_rects,
            })

            y += cell_h + gap_y

        # Content height from first y to last y, in widget coordinates.
        top_y = self.rect.y
        bottom_y = y + self.scroll_px
        self._content_h = max(self.rect.height, bottom_y - top_y)
        self.clamp_scroll()

    def layout(self, ctx: WidgetContext) -> None:
        # We assume parent set rect. Just refresh cached geometry.
        self._build_rows(ctx)

    def draw(self, ctx: WidgetContext) -> None:
        if not self.visible:
            return

        font = self._font(ctx)
        fg = getattr(ctx.renderer, "fg", (220, 220, 220))
        dim = getattr(ctx.renderer, "dim", (150, 150, 150))
        sel = getattr(ctx.renderer, "player_color", getattr(ctx.renderer, "sel", (255, 255, 0)))

        # Refresh row cache (safe even if layout already called; keeps hover accurate)
        self._build_rows(ctx)

        flat = self.scene._flatten_entries()

        # Draw rows
        for r in self._rows:
            kind = r.get("kind")
            if kind == "header":
                rr: pygame.Rect = r["row_rect"]
                if rr.bottom < self.rect.top or rr.top > self.rect.bottom:
                    continue
                text = r["label"]
                surf = font.render(text, True, dim)
                ctx.surface.blit(surf, rr.topleft)
                continue

            if kind != "row":
                continue

            rr: pygame.Rect = r["row_rect"]
            if rr.bottom < self.rect.top or rr.top > self.rect.bottom:
                continue

            cmd: str = r["cmd"]
            label: str = r["label"]
            row_idx: int = r["row_idx"]

            selected_row = (row_idx == self.scene.selected_idx)

            # Label
            label_rect: pygame.Rect = r["label_rect"]
            label_surf = font.render(label, True, fg)
            ctx.surface.blit(label_surf, (label_rect.x, label_rect.y + (label_rect.height - label_surf.get_height()) // 2))

            # Determine keys for this row
            keys = self.scene._get_keys_for_cmd(cmd)

            # Draw 3 binding cells
            for slot, cell_rect in enumerate(r["cell_rects"]):
                is_selected = selected_row and slot == self.scene.selected_slot
                border_col = sel if is_selected else dim

                pygame.draw.rect(ctx.surface, (25, 25, 35), cell_rect)
                pygame.draw.rect(ctx.surface, border_col, cell_rect, 1)

                keycode = keys[slot] if slot < len(keys) else None
                key_name = self.scene._format_key_for_display(cmd, keycode)

                # If capturing *this* cmd + slot, show draft/prompt styling
                if self.scene._capturing is not None:
                    cap_kind, cap_id = self.scene._capturing
                    if cap_id == cmd and self.scene.selected_slot == slot:
                        if self.scene._draft_code is None:
                            key_name = "…press key…"
                        else:
                            key_name = format_keybinding(self.scene._draft_code)

                t = font.render(key_name, True, fg)
                ctx.surface.blit(t, (cell_rect.x + 8, cell_rect.y + (cell_rect.height - t.get_height()) // 2))

                # Clear 'x'
                x_rect = r["x_rects"][slot]
                x_surf = font.render("x", True, dim)
                ctx.surface.blit(x_surf, (x_rect.x, x_rect.y))

        super().draw(ctx)

    def _hit_test(self, pos: Tuple[int, int]) -> Tuple[Optional[int], Optional[int], bool, Optional[str]]:
        """
        Returns (row_index, slot_index, over_x, cmd) for panel-local pos.
        row_index is index into flattened list (self.scene.selected_idx space).
        """
        for r in self._rows:
            if r.get("kind") != "row":
                continue
            rr: pygame.Rect = r["row_rect"]
            if not rr.collidepoint(pos):
                continue

            cmd: str = r["cmd"]
            # Check cells
            for slot, cell_rect in enumerate(r["cell_rects"]):
                if cell_rect.collidepoint(pos):
                    over_x = r["x_rects"][slot].collidepoint(pos)
                    return int(r["row_idx"]), int(slot), bool(over_x), cmd

            # If clicked row but not a cell, still return row selection
            return int(r["row_idx"]), None, False, cmd

        return None, None, False, None

    def handle_event(self, event, ctx: WidgetContext) -> bool:
        if not (self.visible and self.enabled):
            return False

        # Hover selection
        if event.type == pygame.MOUSEMOTION:
            row, slot, over_x, _cmd = self._hit_test(event.pos)
            self._hover_row = row
            self._hover_slot = slot
            self._hover_over_x = over_x

            if row is not None:
                # Mouse moves => mouse owns selection (PanelScene already flips _keyboard_mode)
                self.scene.selected_idx = row
                if slot is not None:
                    self.scene.selected_slot = slot
                self.scene._grid_ensure_selection_visible()

        # Click actions
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            row, slot, over_x, cmd = self._hit_test(event.pos)
            if row is None:
                return False

            self.scene.selected_idx = row
            if slot is not None:
                self.scene.selected_slot = slot

            # Click on clear 'x'
            if (slot is not None) and over_x and cmd is not None:
                self.scene._clear_slot_for_cmd(cmd, slot)
                return True

            # Click on cell => start capture for that cmd+slot
            if (slot is not None) and (cmd is not None):
                self.scene._begin_capture_for_cmd(cmd, slot)
                return True

        # Wheel scrolling: handled at scene-level (we want consistent “wheel anywhere in grid”)
        return super().handle_event(event, ctx)


class KeybindsRootWidget(Widget):
    """
    Layout:
      Big Title (top-center) + Reset button (top-right)
      Grid (fills middle)
      Footer hints (BOTTOM)
    """
    def __init__(self, scene: "KeybindsScene") -> None:
        super().__init__()
        self.scene = scene

        # Big menu-style title
        self.title = ScaledLabelWidget("Controls", scale=2, align="center")

        self.reset_btn = ButtonWidget("[Reset Defaults]", on_click=lambda _b: scene.reset_defaults())
        self.grid = KeybindGridWidget(scene)

        # Footer stays at the bottom
        self.footer = LabelWidget("", align="center")

        self.add_child(self.title)
        self.add_child(self.reset_btn)
        self.add_child(self.grid)
        self.add_child(self.footer)

    def layout(self, ctx: WidgetContext) -> None:
        self.rect = ctx.surface.get_rect()

        pad = 16
        gap = 36

        # --- TITLE + RESET ROW (TOP) ---
        self.title.layout(ctx)
        self.reset_btn.layout(ctx)

        self.title.rect.midtop = (self.rect.centerx, self.rect.y + pad)

        self.reset_btn.rect.right = self.rect.right - pad
        self.reset_btn.rect.centery = self.title.rect.centery

        header_bottom = max(self.title.rect.bottom, self.reset_btn.rect.bottom)

        # --- FOOTER (BOTTOM) ---
        self.footer.text = self.scene._footer_text()
        self.footer.font = getattr(ctx.renderer, "small_font", getattr(ctx.renderer, "font", None))
        self.footer.layout(ctx)
        self.footer.rect.midbottom = (self.rect.centerx, self.rect.bottom - pad)

        # --- GRID (MIDDLE) ---
        top = header_bottom + gap
        bottom = self.footer.rect.top - gap
        h = max(120, bottom - top)

        self.grid.rect = pygame.Rect(
            self.rect.x + pad,
            top,
            self.rect.width - 2 * pad,
            h,
        )

        self.grid.layout(ctx)




# -----------------------------------------------------------------------------
# Scene
# -----------------------------------------------------------------------------

class KeybindsScene(PanelScene):
    """
    Keybinding editor implemented as a widget-driven PanelScene.

    Keyboard:
      Up/Down selects row
      Left/Right selects slot
      Enter begins capture (or confirms draft while capturing)
      Esc cancels capture / goes back (if not capturing)
      F11 fullscreen

    Mouse:
      Hover selects
      Click cell to capture
      Click 'x' to clear slot
      Wheel scrolls grid
    """

    def __init__(
        self,
        base_rect: Optional[pygame.Rect] = None,
        depth: int = 0,
        bindings: Optional[Dict[str, List[int]]] = None,
        move_bindings: Optional[Dict[int, Tuple[int, int]]] = None,
    ) -> None:
        super().__init__(window_rect=base_rect)
        self.depth = depth

        self.selected_idx = 0          # index into flattened list of all entries
        self.selected_slot = 0         # which of the 3 binding slots is selected (0-2)

        self._menu_input = MenuInput()

        self._capturing: Optional[Tuple[str, str]] = None  # ("cmd"/"move", cmd_id)
        self._pending_mods: Optional[int] = None
        self._pending_mod_key: Optional[int] = None
        self._draft_code: Optional[int] = None

        loaded_binds, loaded_moves = load_bindings_full()
        self._bindings: Dict[str, List[int]] = bindings if bindings is not None else loaded_binds
        self._move_bindings: Dict[int, Tuple[int, int]] = (
            {int(k): (int(v[0]), int(v[1])) for k, v in move_bindings.items()}
            if move_bindings is not None
            else loaded_moves
        )

        # Widgets
        self.root = KeybindsRootWidget(self)

        # Track last mouse position (panel-local) for wheel scrolling heuristics
        self._last_panel_mouse_pos: Optional[Tuple[int, int]] = None


    # ---- data model helpers --------------------------------------------

    def _flatten_entries(self) -> List[Tuple[int, str, str]]:
        flat: List[Tuple[int, str, str]] = []
        for ci, (_, entries) in enumerate(KEYBIND_CATEGORIES):
            for cmd, label in entries:
                flat.append((ci, cmd, label))
        return flat

    def _keys_for_move(self, vec: Tuple[int, int]) -> List[int]:
        return [k for k, v in self._move_bindings.items() if v == vec]

    def _get_keys_for_cmd(self, cmd: str) -> List[Optional[int]]:
        if cmd.startswith("move_"):
            vec = _VEC_MAP.get(cmd, (0, 0))
            keys = self._keys_for_move(vec)
            defaults = [k for k, v in DEFAULT_MOVE_BINDINGS.items() if v == vec]
            if not keys:
                keys = defaults
        else:
            keys = self._bindings.get(cmd, DEFAULT_BINDINGS.get(cmd, []))

        keys = list(keys)[:3]
        while len(keys) < 3:
            keys.append(None)
        return keys

    def _format_key_for_display(self, cmd: str, keycode: Optional[int]) -> str:
        if keycode is None:
            return "(unbound)"
        return format_keybinding(keycode)

    def _clear_conflicts(self, keycode: int, target_cmd: str) -> None:
        target_ctx = COMMAND_CONTEXTS.get(target_cmd, {"dungeon"})
        # Clear from command bindings
        for cmd, keys in list(self._bindings.items()):
            if cmd == target_cmd:
                continue
            if COMMAND_CONTEXTS.get(cmd, {"dungeon"}).intersection(target_ctx):
                self._bindings[cmd] = [k for k in keys if k != keycode]
        # Clear from movement bindings if contexts overlap
        if "dungeon" in target_ctx:
            if keycode in self._move_bindings:
                del self._move_bindings[keycode]

    def _apply_binding(self, command: str, slot: int, keycode: int, manager) -> None:
        self._clear_conflicts(keycode, command)

        keys = list(self._bindings.get(command, []))
        while len(keys) <= slot:
            keys.append(None)
        keys[slot] = keycode

        # remove duplicates / cleanup None
        cleaned: List[Optional[int]] = []
        for k in keys:
            if k is None:
                cleaned.append(None)
            elif k not in cleaned:
                cleaned.append(k)

        self._bindings[command] = [k for k in cleaned if k is not None][:3]
        manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
        save_bindings_file(self._bindings, self._move_bindings)

    def _apply_move_binding(self, cmd: str, slot: int, keycode: int, manager) -> None:
        vec = _VEC_MAP.get(cmd)
        if vec is None:
            return

        self._clear_conflicts(keycode, cmd)

        # build slot list for this vec
        keys = self._keys_for_move(vec)
        while len(keys) <= slot:
            keys.append(None)
        keys[slot] = keycode

        # Remove any existing mapping of this key to other vectors.
        to_remove = [k for k, v in self._move_bindings.items() if k == keycode and v != vec]
        for k in to_remove:
            del self._move_bindings[k]

        # Clear existing keys for this vec
        for k, v in list(self._move_bindings.items()):
            if v == vec:
                del self._move_bindings[k]

        # Reassign
        for k in keys:
            if k is not None:
                self._move_bindings[int(k)] = vec

        manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
        save_bindings_file(self._bindings, self._move_bindings)

    def _clear_slot_for_cmd(self, cmd: str, slot: int) -> None:
        # Called from widget (mouse). Needs manager at commit time, so we do an immediate save via manager in update().
        # We store intent as a small action and execute in update() where manager is available.
        self._pending_action = ("clear", cmd, slot)  # type: ignore[attr-defined]
        self._cancel_capture()

    def _begin_capture_for_cmd(self, cmd: str, slot: int) -> None:
        self.selected_slot = slot
        self._capturing = ("move" if cmd.startswith("move_") else "cmd", cmd)
        self._draft_code = None
        self._pending_mods = None
        self._pending_mod_key = None
        _dbg(f"Begin capture for {cmd} slot={slot}")

    def _commit_draft(self, manager) -> None:
        if self._capturing is None or self._draft_code is None:
            return
        cap_kind, cap_id = self._capturing
        code = self._draft_code
        slot = self.selected_slot

        _dbg(f"Commit draft: kind={cap_kind} cmd={cap_id} slot={slot} code={code}")

        if cap_kind == "cmd":
            self._apply_binding(cap_id, slot, code, manager)
        else:
            self._apply_move_binding(cap_id, slot, code, manager)

        self._capturing = None
        self._draft_code = None
        self._pending_mods = None
        self._pending_mod_key = None

    def _cancel_capture(self) -> None:
        if self._capturing or self._draft_code is not None:
            _dbg("Cancel capture")
        self._capturing = None
        self._draft_code = None
        self._pending_mods = None
        self._pending_mod_key = None

    def reset_defaults(self) -> None:
        self._pending_action = ("reset", None, None)  # type: ignore[attr-defined]
        self._cancel_capture()

    # ---- UI helpers -----------------------------------------------------

    def _footer_text(self) -> str:
        if self._capturing:
            _k, cmd = self._capturing
            return f"Press a key for {cmd} (Enter confirm, Esc cancel)"
        return "Up/Down select • Left/Right slot • Enter rebind • Click cell / x • Wheel scroll • Esc back • F11 fullscreen"

    def _grid_ensure_selection_visible(self) -> None:
        # Ask grid to keep selected row visible (safe even if not laid out yet)
        try:
            root: KeybindsRootWidget = self.root  # type: ignore[assignment]
            root.grid.ensure_row_visible(self.selected_idx)
        except Exception:
            pass

    # ---- PanelScene hooks ----------------------------------------------

    def handle_event(self, event, manager) -> None:
        # Capture last panel-local mouse pos for wheel handling.
        if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and hasattr(event, "pos"):
            try:
                # PanelScene will pass panel-local pos to widgets, but here we may see original event.
                # Root/grid logic only needs a best-effort value.
                self._last_panel_mouse_pos = event.pos
            except Exception:
                pass

        # While capturing, we intercept KEYDOWN / KEYUP before widgets.
        if self._capturing:
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self._cancel_capture()
                    return
                if event.key == pygame.K_RETURN:
                    if self._draft_code is not None:
                        self._commit_draft(manager)
                    return

                # pure modifier: wait for next key or bind on keyup
                if event.key in (
                    pygame.K_LSHIFT, pygame.K_RSHIFT,
                    pygame.K_LCTRL, pygame.K_RCTRL,
                    pygame.K_LALT, pygame.K_RALT,
                ):
                    self._pending_mods = event.mod
                    self._pending_mod_key = event.key
                    _dbg(f"Capture modifier down: {event.key} mods={event.mod}")
                    return

                mods = event.mod
                if self._pending_mods:
                    mods |= self._pending_mods
                code = encode_keybinding(event.key, mods)
                self._draft_code = code
                _dbg(f"Draft updated: code={code} mods={mods}")
                self._pending_mods = None
                self._pending_mod_key = None
                return

            if event.type == pygame.KEYUP and self._pending_mod_key is not None and event.key == self._pending_mod_key:
                # Modifier released without another key: bind modifier alone.
                self._draft_code = encode_keybinding(event.key, 0)
                self._pending_mods = None
                self._pending_mod_key = None
                return

            # ignore other events while capturing
            return

        # Normal (non-capture) input: let PanelScene route to widgets first,
        # then we handle any remaining keyboard navigation / back/fullscreen.
        super().handle_event(event, manager)

        # Wheel scroll: if it wasn't consumed by widgets, scroll the grid (anywhere inside it).
        if event.type == pygame.MOUSEWHEEL:
            try:
                root: KeybindsRootWidget = self.root  # type: ignore[assignment]
                # Only scroll if mouse is over grid area; wheel event has no pos, so use last known mouse.
                mp = self._last_panel_mouse_pos or pygame.mouse.get_pos()
                if root.grid.rect.collidepoint(mp):
                    root.grid.scroll_px = max(0, root.grid.scroll_px - int(event.y) * 40)
                    root.grid.clamp_scroll()
                    self._grid_ensure_selection_visible()
                    return
            except Exception:
                pass

        if event.type == pygame.KEYDOWN:
            action = self._menu_input.handle_keydown(event.key)
            if action is not None:
                self._handle_action(action, manager)

        elif event.type == pygame.KEYUP:
            self._menu_input.handle_keyup(event.key)

    def _handle_action(self, action: Optional[str], manager) -> None:
        if action is None:
            return

        if action == MENU_ACTION_FULLSCREEN:
            manager.renderer.toggle_fullscreen()
            return

        flat = self._flatten_entries()
        n = max(1, len(flat))

        if action == MENU_ACTION_BACK:
            # In keeping with other menus, Esc pops this scene if stack-based.
            if hasattr(manager, "pop_scene"):
                manager.pop_scene()
            else:
                manager.set_scene(None)
            return

        if action == MENU_ACTION_UP:
            self.selected_idx = (self.selected_idx - 1) % n
            self._cancel_capture()
            self._grid_ensure_selection_visible()
            return

        if action == MENU_ACTION_DOWN:
            self.selected_idx = (self.selected_idx + 1) % n
            self._cancel_capture()
            self._grid_ensure_selection_visible()
            return

        if action == MENU_ACTION_LEFT:
            self.selected_slot = (self.selected_slot - 1) % 3
            self._cancel_capture()
            self._grid_ensure_selection_visible()
            return

        if action == MENU_ACTION_RIGHT:
            self.selected_slot = (self.selected_slot + 1) % 3
            self._cancel_capture()
            self._grid_ensure_selection_visible()
            return

        if action == MENU_ACTION_ACTIVATE:
            if not flat:
                return
            _ci, cmd, _label = flat[self.selected_idx]
            self._begin_capture_for_cmd(cmd, self.selected_slot)
            self._grid_ensure_selection_visible()
            return

    def update(self, dt_ms: int, manager) -> None:
        # key-repeat for navigation
        repeat_action = self._menu_input.update()
        if repeat_action is not None:
            self._handle_action(repeat_action, manager)

        # Apply any pending actions that required manager (save/persist)
        act = getattr(self, "_pending_action", None)
        if act is not None:
            kind, cmd, slot = act
            self._pending_action = None  # type: ignore[attr-defined]

            if kind == "reset":
                _dbg("Reset to defaults")
                self._bindings = {k: list(v) for k, v in DEFAULT_BINDINGS.items()}
                self._move_bindings = {k: v for k, v in DEFAULT_MOVE_BINDINGS.items()}
                manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
                save_bindings_file(self._bindings, self._move_bindings)

            elif kind == "clear" and isinstance(cmd, str) and isinstance(slot, int):
                if cmd.startswith("move_"):
                    vec = _VEC_MAP.get(cmd)
                    if vec is not None:
                        keys = self._keys_for_move(vec)
                        if slot < len(keys):
                            key = keys[slot]
                            if key in self._move_bindings:
                                del self._move_bindings[key]
                            manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
                            save_bindings_file(self._bindings, self._move_bindings)
                else:
                    keys = list(self._bindings.get(cmd, []))
                    if slot < len(keys):
                        del keys[slot]
                        self._bindings[cmd] = [k for k in keys if k is not None]
                        manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
                        save_bindings_file(self._bindings, self._move_bindings)

        super().update(dt_ms, manager)
