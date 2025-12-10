from __future__ import annotations

from typing import Dict, List, Tuple, Optional

import pygame
import time
from pathlib import Path

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


# Categories of commands shown in the keybinding menu.
KEYBIND_CATEGORIES: List[Tuple[str, List[Tuple[str, str]]]] = [
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


class KeybindsScene(Scene):
    """Simple keybinding editor."""

    def __init__(
        self,
        base_rect: Optional[pygame.Rect] = None,
        depth: int = 0,
        bindings: Optional[Dict[str, List[int]]] = None,
        move_bindings: Optional[Dict[int, Tuple[int, int]]] = None,
    ) -> None:
        self.window_rect = base_rect
        self.depth = depth
        self.selected_idx = 0  # index into flattened list of all entries
        self.selected_slot = 0  # which of the 3 binding slots is selected (0-2)
        self.reset_rect: Optional[pygame.Rect] = None
        self.scroll_offset: float = 0.0
        self._menu_input = MenuInput()
        self._capturing: Optional[Tuple[str, str]] = None  # ("cmd"/"move", key)
        self._pending_mods: Optional[int] = None
        self._pending_mod_key: Optional[int] = None
        self._draft_code: Optional[int] = None  # pending binding before confirm
        loaded_binds, loaded_moves = load_bindings_full()
        self._bindings: Dict[str, List[int]] = bindings if bindings is not None else loaded_binds
        self._move_bindings: Dict[int, Tuple[int, int]] = (
            {int(k): (int(v[0]), int(v[1])) for k, v in move_bindings.items()}
            if move_bindings is not None
            else loaded_moves
        )

    def _ensure_rect(self, renderer) -> None:
        if self.window_rect is None:
            w = int(renderer.width * 0.7)
            h = int(renderer.height * 0.7)
            x = (renderer.width - w) // 2
            y = (renderer.height - h) // 2
            self.window_rect = pygame.Rect(x, y, w, h)

    def _flatten_entries(self) -> List[Tuple[int, str, str]]:
        """
        Flatten categories into a list of (category_index, command, label).
        """
        flat: List[Tuple[int, str, str]] = []
        for ci, (_, entries) in enumerate(KEYBIND_CATEGORIES):
            for cmd, label in entries:
                flat.append((ci, cmd, label))
        return flat

    def _keys_for_move(self, vec: Tuple[int, int]) -> List[int]:
        return [k for k, v in self._move_bindings.items() if v == vec]

    def _clear_conflicts(self, keycode: int, target_cmd: str) -> None:
        """Remove this key from other commands sharing a context with target_cmd."""
        target_ctx = COMMAND_CONTEXTS.get(target_cmd, {"dungeon"})
        # Clear from command bindings
        for cmd, keys in list(self._bindings.items()):
            if cmd == target_cmd:
                continue
            if COMMAND_CONTEXTS.get(cmd, {"dungeon"}).intersection(target_ctx):
                self._bindings[cmd] = [k for k in keys if k != keycode]
        # Clear from movement bindings if contexts overlap
        if "dungeon" in target_ctx:
            to_delete = [k for k, v in self._move_bindings.items() if k == keycode]
            for k in to_delete:
                del self._move_bindings[k]

    def _set_binding_slot(self, command: str, slot: int, keycode: int, manager) -> None:
        self._clear_conflicts(keycode, command)
        keys = list(self._bindings.get(command, []))
        while len(keys) <= slot:
            keys.append(None)
        keys[slot] = keycode
        # remove duplicates/None cleanup
        cleaned = []
        for k in keys:
            if k is None:
                cleaned.append(None)
            elif k not in cleaned:
                cleaned.append(k)
        self._bindings[command] = [k for k in cleaned if k is not None][:3]
        manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
        save_bindings_file(self._bindings, self._move_bindings)

    def _set_draft(self, cmd: str, slot: int, code: int, is_move: bool) -> None:
        """Store a draft binding without committing; displayed until confirmed."""
        self._capturing = ("move" if is_move else "cmd", cmd)
        self._draft_code = code
        self.selected_slot = slot
        _dbg(f"Draft set for {cmd} slot {slot}: code={code}")

    def _commit_draft(self, manager) -> None:
        if self._capturing is None or self._draft_code is None:
            return
        cap_kind, cap_id = self._capturing
        _dbg(f"Commit draft for {cap_id} kind={cap_kind} slot={self.selected_slot} code={self._draft_code}")
        if cap_kind == "cmd":
            self._apply_binding(cap_id, self._draft_code, manager)
        else:
            vec_map = {
                "move_up": (0, -1),
                "move_down": (0, 1),
                "move_left": (-1, 0),
                "move_right": (1, 0),
                "move_upleft": (-1, -1),
                "move_upright": (1, -1),
                "move_downleft": (-1, 1),
                "move_downright": (1, 1),
            }
            vec = vec_map.get(cap_id)
            if vec is not None:
                self._apply_move_binding(vec, self._draft_code, manager)
        self._capturing = None
        self._draft_code = None
        self._pending_mods = None
        self._pending_mod_key = None

    def _cancel_draft(self) -> None:
        if self._capturing or self._draft_code is not None:
            _dbg("Cancel draft")
        self._capturing = None
        self._draft_code = None
        self._pending_mods = None
        self._pending_mod_key = None

    def _clear_binding_slot(self, command: str, slot: int, manager) -> None:
        keys = list(self._bindings.get(command, []))
        if slot < len(keys):
            del keys[slot]
            self._bindings[command] = [k for k in keys if k is not None]
            manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
            save_bindings_file(self._bindings, self._move_bindings)

    def _apply_binding(self, command: str, keycode: int, manager) -> None:
        self._set_binding_slot(command, self.selected_slot, keycode, manager)

    def _apply_move_binding(self, vec: Tuple[int, int], keycode: int, manager) -> None:
        target_cmd = next((cmd for cmd, v in {
            "move_up": (0, -1),
            "move_down": (0, 1),
            "move_left": (-1, 0),
            "move_right": (1, 0),
            "move_upleft": (-1, -1),
            "move_upright": (1, -1),
            "move_downleft": (-1, 1),
            "move_downright": (1, 1),
        }.items() if v == vec), "move")
        self._clear_conflicts(keycode, target_cmd)
        # To set a specific slot for movement, we need to map existing keys for this vec.
        keys = self._keys_for_move(vec)
        while len(keys) <= self.selected_slot:
            keys.append(None)
        keys[self.selected_slot] = keycode
        # Remove any existing mapping of this key to other vectors.
        to_remove = [k for k, v in self._move_bindings.items() if k == keycode and v != vec]
        for k in to_remove:
            del self._move_bindings[k]
        # Reassign the list back into the dict
        # First clear existing keys for this vec
        for k, v in list(self._move_bindings.items()):
            if v == vec:
                del self._move_bindings[k]
        for k in keys:
            if k is not None:
                self._move_bindings[int(k)] = vec
        manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
        save_bindings_file(self._bindings, self._move_bindings)

    def _clear_move_slot(self, vec: Tuple[int, int], slot: int, manager) -> None:
        keys = self._keys_for_move(vec)
        if slot < len(keys):
            key = keys[slot]
            if key in self._move_bindings:
                del self._move_bindings[key]
            manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
            save_bindings_file(self._bindings, self._move_bindings)

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        renderer = manager.renderer
        clock = pygame.time.Clock()
        surface = renderer.surface
        self._ensure_rect(renderer)
        rect = self.window_rect
        assert rect is not None

        ui_font = renderer.small_font
        big_font = renderer.map_font

        running = True

        def handle_action(action: Optional[str]) -> bool:
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
                self.selected_idx = (self.selected_idx - 1) % max(1, len(self._flatten_entries()))
                self._cancel_draft()
                return False
            if action == MENU_ACTION_DOWN:
                self.selected_idx = (self.selected_idx + 1) % max(1, len(self._flatten_entries()))
                self._cancel_draft()
                return False
            if action == MENU_ACTION_LEFT:
                self.selected_slot = (self.selected_slot - 1) % 3
                self._cancel_draft()
                return False
            if action == MENU_ACTION_RIGHT:
                self.selected_slot = (self.selected_slot + 1) % 3
                self._cancel_draft()
                return False
            if action == MENU_ACTION_ACTIVATE:
                flat = self._flatten_entries()
                if not flat:
                    return False
                _, cmd, _label = flat[self.selected_idx]
                self._capturing = ("move" if cmd.startswith("move_") else "cmd", cmd)
                self._draft_code = None
                return False
            return False

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    manager.set_scene(None)
                    return

                if self._capturing:
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            self._cancel_draft()
                            continue
                        if event.key == pygame.K_RETURN:
                            # Confirm current draft (if any)
                            if self._draft_code is not None:
                                self._commit_draft(manager)
                            continue
                        # If it's a pure modifier, wait for next key or its keyup.
                        if event.key in (pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LALT, pygame.K_RALT):
                            self._pending_mods = event.mod
                            self._pending_mod_key = event.key
                            _dbg(f"Capture modifier down: {event.key} mods={event.mod}")
                            continue
                        cap_kind, cap_id = self._capturing
                        mods = event.mod
                        if self._pending_mods:
                            mods |= self._pending_mods
                        code = encode_keybinding(event.key, mods)
                        self._draft_code = code
                        _dbg(f"Draft code updated for {cap_id}: code={code} mods={mods}")
                        self._pending_mods = None
                        self._pending_mod_key = None
                        continue
                    if event.type == pygame.KEYUP and self._pending_mod_key is not None and event.key == self._pending_mod_key:
                        # Modifier released without another key: bind the modifier alone.
                        self._draft_code = encode_keybinding(event.key, 0)
                        self._pending_mods = None
                        self._pending_mod_key = None
                        continue
                    # While capturing, ignore other events.
                    continue

                if event.type == pygame.KEYDOWN:
                    action = self._menu_input.handle_keydown(event.key)
                    if action == MENU_ACTION_ACTIVATE and self._draft_code is not None:
                        self._commit_draft(manager)
                        continue
                    handle_action(action)
                elif event.type == pygame.KEYUP:
                    self._menu_input.handle_keyup(event.key)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mx, my = event.pos
                    # Reset button hit-test
                    if self.reset_rect and self.reset_rect.collidepoint(mx, my):
                        _dbg("Reset to defaults clicked")
                        self._bindings = {k: list(v) for k, v in DEFAULT_BINDINGS.items()}
                        self._move_bindings = {k: v for k, v in DEFAULT_MOVE_BINDINGS.items()}
                        manager.keybindings = {"bindings": dict(self._bindings), "move_bindings": dict(self._move_bindings)}
                        save_bindings_file(self._bindings, self._move_bindings)
                        self._cancel_draft()
                        continue
                    flat = self._flatten_entries()
                    row_y = 60 - self.scroll_offset
                    line_gap = 8
                    current_cat = -1
                    cell_h = 32
                    col_w = 110
                    for idx, (cat_idx, cmd, label) in enumerate(flat):
                        if cat_idx != current_cat:
                            current_cat = cat_idx
                            header_text = ui_font.render(KEYBIND_CATEGORIES[cat_idx][0], True, renderer.dim)
                            row_y += header_text.get_height() + 4
                        label_text = renderer.map_font.render(label, True, renderer.fg)
                        label_w = label_text.get_width()
                        cell_start_x = 32 + label_w
                        for slot in range(3):
                            cx = cell_start_x + slot * (col_w + 8)
                            cy = row_y
                            cell = pygame.Rect(rect.x + cx, rect.y + cy, col_w, cell_h)
                            x_rect = pygame.Rect(cell.right - 12, cell.y + 4, 10, 10)
                            if x_rect.collidepoint(mx, my):
                                if cmd.startswith("move_"):
                                    vec_map = {
                                        "move_up": (0, -1),
                                        "move_down": (0, 1),
                                        "move_left": (-1, 0),
                                        "move_right": (1, 0),
                                        "move_upleft": (-1, -1),
                                        "move_upright": (1, -1),
                                        "move_downleft": (-1, 1),
                                        "move_downright": (1, 1),
                                    }
                                    vec = vec_map.get(cmd)
                                    if vec is not None:
                                        self._clear_move_slot(vec, slot, manager)
                                else:
                                    self._clear_binding_slot(cmd, slot, manager)
                                self.selected_idx = idx
                                self.selected_slot = slot
                                self._cancel_draft()
                                break
                            if cell.collidepoint(mx, my):
                                # Click-to-activate for rebinding this slot
                                self.selected_idx = idx
                                self.selected_slot = slot
                                self._capturing = ("move" if cmd.startswith("move_") else "cmd", cmd)
                                self._draft_code = None
                                self._pending_mods = None
                                self._pending_mod_key = None
                        row_y += cell_h + line_gap
                elif event.type == pygame.MOUSEWHEEL:
                    # Scroll the page (not just selection)
                    scroll_step = 40
                    self.scroll_offset = max(0.0, self.scroll_offset - event.y * scroll_step)
                    # Clamp to content height
                    flat = self._flatten_entries()
                    ui_font = manager.renderer.map_font
                    header_h = ui_font.get_height() + 4
                    content_h = 60 + len(flat) * (32 + 8) + len(KEYBIND_CATEGORIES) * header_h + 40
                    rect = self.window_rect
                    if rect:
                        max_off = max(0.0, content_h - rect.height)
                        if self.scroll_offset > max_off:
                            self.scroll_offset = max_off
                    self._cancel_draft()

            # Draw panel
            surface.fill(renderer.bg)
            panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            panel.fill((10, 10, 20, 230))
            pygame.draw.rect(panel, renderer.fg, panel.get_rect(), 2)

            # Larger fonts for readability
            ui_font = renderer.map_font
            hint_font = renderer.small_font

            title = renderer.big_label("Controls")
            panel.blit(title, ((rect.width - title.get_width()) // 2, 16))

            # Reset button in top-right
            reset_label = hint_font.render("[Reset Defaults]", True, renderer.sel)
            reset_rect_local = reset_label.get_rect()
            reset_rect_local.top = 20
            reset_rect_local.right = rect.width - 12
            panel.blit(reset_label, reset_rect_local)
            self.reset_rect = pygame.Rect(rect.x + reset_rect_local.x, rect.y + reset_rect_local.y, reset_rect_local.width, reset_rect_local.height)

            y = 60 - self.scroll_offset
            line_gap = 8
            flat = self._flatten_entries()
            current_cat = -1
            cell_h = 32
            col_w = 110
            for idx, (cat_idx, cmd, label) in enumerate(flat):
                # Header when category changes
                if cat_idx != current_cat:
                    current_cat = cat_idx
                    header = KEYBIND_CATEGORIES[cat_idx][0]
                    header_text = ui_font.render(header, True, renderer.dim)
                    panel.blit(header_text, (16, y))
                    y += header_text.get_height() + 4

                selected_row = idx == self.selected_idx
                if cmd.startswith("move_"):
                    vec_map = {
                        "move_up": (0, -1),
                        "move_down": (0, 1),
                        "move_left": (-1, 0),
                        "move_right": (1, 0),
                        "move_upleft": (-1, -1),
                        "move_upright": (1, -1),
                        "move_downleft": (-1, 1),
                        "move_downright": (1, 1),
                    }
                    vec = vec_map.get(cmd, (0, 0))
                    keys = self._keys_for_move(vec)
                    defaults = [k for k, v in DEFAULT_MOVE_BINDINGS.items() if v == vec]
                    if not keys:
                        keys = defaults
                else:
                    keys = self._bindings.get(cmd, DEFAULT_BINDINGS.get(cmd, []))
                keys = keys[:3] + [None] * (3 - len(keys)) if len(keys) < 3 else keys[:3]

                # Label
                label_text = ui_font.render(label, True, renderer.fg)
                panel.blit(label_text, (24, y + (cell_h - label_text.get_height()) // 2))

                # Cells for 3 bindings
                label_w = label_text.get_width()
                cell_start_x = 32 + label_w
                for slot in range(3):
                    cx = cell_start_x + slot * (col_w + 8)
                    cy = y
                    cell_rect = pygame.Rect(cx, cy, col_w, cell_h)
                    is_selected = selected_row and slot == self.selected_slot
                    border_col = renderer.player_color if is_selected else renderer.dim
                    pygame.draw.rect(panel, (25, 25, 35), cell_rect)
                    pygame.draw.rect(panel, border_col, cell_rect, 1)
                    keycode = keys[slot] if slot < len(keys) else None
                    key_name = format_keybinding(keycode) if keycode is not None else "(unbound)"
                    key_text = ui_font.render(key_name, True, renderer.fg)
                    panel.blit(key_text, (cell_rect.x + 8, cell_rect.y + (cell_h - key_text.get_height()) // 2))
                    # Clear 'X'
                    x_text = ui_font.render("x", True, renderer.dim)
                    panel.blit(x_text, (cell_rect.right - x_text.get_width() - 6, cell_rect.y + 2))

                y += cell_h + line_gap

            hint_lines = [
                "Up/Down: select action   Left/Right: select slot   Enter: rebind   Click 'x' to clear   Esc: back",
            ]
            if self._capturing:
                _kind, cap_id = self._capturing
                hint_lines = [f"Press a key for {cap_id} (Esc to cancel)"]
            for i, line in enumerate(hint_lines):
                hint = hint_font.render(line, True, renderer.dim)
                panel.blit(hint, (24, rect.height - 30 - i * 18))

            surface.blit(panel, rect.topleft)
            renderer.present()
            clock.tick(60)
