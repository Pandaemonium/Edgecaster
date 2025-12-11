from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict, Iterable
import json
from pathlib import Path
import pygame

MOD_MASK = pygame.KMOD_SHIFT | pygame.KMOD_CTRL | pygame.KMOD_ALT


def encode_keybinding(keycode: int, mods: int = 0) -> int:
    """
    Encode a key + modifiers into a single int so bindings can distinguish combos.
    """
    return int(keycode) | ((int(mods) & MOD_MASK) << 16)


def decode_keybinding(code: int) -> tuple[int, int]:
    """
    Decode a stored binding into (keycode, mods).
    """
    mods = (code >> 16) & MOD_MASK
    key = code & 0xFFFF
    return key, mods


def format_keybinding(code: int) -> str:
    key, mods = decode_keybinding(code)
    parts = []
    if mods & pygame.KMOD_CTRL:
        parts.append("Ctrl")
    if mods & pygame.KMOD_SHIFT:
        parts.append("Shift")
    if mods & pygame.KMOD_ALT:
        parts.append("Alt")
    parts.append(pygame.key.name(key))
    return "+".join(parts)

import pygame
from copy import deepcopy

# Default keymap for single-key commands (non-movement).
DEFAULT_BINDINGS: Dict[str, List[int]] = {
    "escape": [encode_keybinding(pygame.K_ESCAPE)],
    "toggle_fullscreen": [encode_keybinding(pygame.K_F11)],
    "toggle_door": [encode_keybinding(pygame.K_o)],
    "show_help": [],  # handled via unicode '?'
    "examine": [encode_keybinding(pygame.K_x)],
    "pickup": [encode_keybinding(pygame.K_g)],
    "possess_nearest": [encode_keybinding(pygame.K_p)],
    "open_inventory": [encode_keybinding(pygame.K_i)],
    "open_abilities": [],  # reserved for ctrl+A below
    "yawp": [encode_keybinding(pygame.K_y)],
    "wait": [encode_keybinding(pygame.K_KP5)],
    "stairs_down": [encode_keybinding(pygame.K_PERIOD), encode_keybinding(pygame.K_GREATER)],
    "stairs_up_or_map": [encode_keybinding(pygame.K_COMMA), encode_keybinding(pygame.K_LESS), encode_keybinding(pygame.K_m)],
    "open_fractal_editor": [encode_keybinding(pygame.K_PLUS), encode_keybinding(pygame.K_EQUALS)],
    "talk": [encode_keybinding(pygame.K_t)],
    "quick_activate_all": [encode_keybinding(pygame.K_f)],
    "look_action": [encode_keybinding(pygame.K_l)],
    # ability bar page cycling
    "ability_page_prev": [encode_keybinding(pygame.K_PAGEUP)],
    "ability_page_next": [encode_keybinding(pygame.K_PAGEDOWN), encode_keybinding(pygame.K_TAB)],
}

# Default movement bindings (keycode -> (dx, dy))
DEFAULT_MOVE_BINDINGS: Dict[int, Tuple[int, int]] = {
    encode_keybinding(pygame.K_UP): (0, -1),
    encode_keybinding(pygame.K_DOWN): (0, 1),
    encode_keybinding(pygame.K_LEFT): (-1, 0),
    encode_keybinding(pygame.K_RIGHT): (1, 0),
    encode_keybinding(pygame.K_w): (0, -1),
    encode_keybinding(pygame.K_s): (0, 1),
    encode_keybinding(pygame.K_a): (-1, 0),
    encode_keybinding(pygame.K_d): (1, 0),
    encode_keybinding(pygame.K_q): (-1, -1),
    encode_keybinding(pygame.K_e): (1, -1),
    encode_keybinding(pygame.K_z): (-1, 1),
    encode_keybinding(pygame.K_c): (1, 1),
    encode_keybinding(pygame.K_KP1): (-1, 1),
    encode_keybinding(pygame.K_KP2): (0, 1),
    encode_keybinding(pygame.K_KP3): (1, 1),
    encode_keybinding(pygame.K_KP4): (-1, 0),
    encode_keybinding(pygame.K_KP6): (1, 0),
    encode_keybinding(pygame.K_KP7): (-1, -1),
    encode_keybinding(pygame.K_KP8): (0, -1),
    encode_keybinding(pygame.K_KP9): (1, -1),
}


def _merge_default_bindings(binds: Dict[str, Iterable[int]]) -> Dict[str, List[int]]:
    merged = deepcopy(DEFAULT_BINDINGS)
    for k, vals in binds.items():
        merged[k] = list(vals)
    return merged


def _merge_default_moves(moves: Dict[int, Tuple[int, int]]) -> Dict[int, Tuple[int, int]]:
    merged = deepcopy(DEFAULT_MOVE_BINDINGS)
    for k, v in moves.items():
        merged[int(k)] = (int(v[0]), int(v[1]))
    return merged


def _bindings_path() -> Path:
    """Path to persisted bindings file."""
    return Path(__file__).resolve().parent.parent / "keybindings.json"


def _load_bindings_file() -> Tuple[Dict[str, List[int]], Dict[int, Tuple[int, int]]]:
    """
    Load bindings (commands + movement) from disk; fall back to defaults on error.
    """
    path = _bindings_path()
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict) and "bindings" in data and "move_bindings" in data:
                binds = {k: [int(v) for v in vals] for k, vals in data.get("bindings", {}).items()}
                moves = {int(k): tuple(v) for k, v in data.get("move_bindings", {}).items()}
                return binds, {k: (int(val[0]), int(val[1])) for k, val in moves.items()}
            # Legacy: plain dict of bindings
            if isinstance(data, dict):
                binds = {k: [int(v) for v in vals] for k, vals in data.items()}
                return _merge_default_bindings(binds), deepcopy(DEFAULT_MOVE_BINDINGS)
    except Exception:
        pass
    return deepcopy(DEFAULT_BINDINGS), deepcopy(DEFAULT_MOVE_BINDINGS)


def save_bindings_file(bindings: Dict[str, Iterable[int]], move_bindings: Dict[int, Tuple[int, int]]) -> None:
    """Persist bindings to disk."""
    path = _bindings_path()
    serial = {
        "bindings": {k: [int(v) for v in vals] for k, vals in bindings.items()},
        "move_bindings": {int(k): [int(v[0]), int(v[1])] for k, v in move_bindings.items()},
    }
    try:
        path.write_text(json.dumps(serial, indent=2))
    except Exception:
        pass


def load_bindings() -> Dict[str, List[int]]:
    """Public helper for scenes/manager to load current bindings (commands only)."""
    b, _ = _load_bindings_file()
    return b


def load_bindings_full() -> Tuple[Dict[str, List[int]], Dict[int, Tuple[int, int]]]:
    """Public helper to load command + movement bindings."""
    return _load_bindings_file()


@dataclass
class GameCommand:
    """Logical game command produced from raw keyboard / mouse input."""
    kind: str
    vector: Optional[Tuple[int, int]] = None   # movement or cursor step
    hotkey: Optional[int] = None              # ability hotkey (1-9)
    raw_key: Optional[int] = None             # original pygame keycode

    # Mouse-specific fields (used for click / hover / wheel)
    mouse_pos: Optional[Tuple[int, int]] = None
    mouse_button: Optional[int] = None
    wheel_y: Optional[int] = None



class GameInput:
    """
    Scene-level mapper from pygame KEYDOWN events to abstract game commands.

    Important: this class *does not* know about renderer state (dialogs,
    config, targeting, ability bar). It just turns keys into high-level
    intents; DungeonScene decides how and when to apply them.

    Keybindings are data-driven: override the dicts in __init__ to customize.
    """

    def __init__(
        self,
        *,
        bindings: Optional[Dict[str, Iterable[int]]] = None,
        move_bindings: Optional[Dict[int, Tuple[int, int]]] = None,
    ) -> None:
        # single-key bindings (non-movement, non-hotkey)
        loaded_bindings, loaded_moves = _load_bindings_file()
        self.bindings: Dict[str, List[int]] = _merge_default_bindings(loaded_bindings)
        self.move_bindings: Dict[int, Tuple[int, int]] = _merge_default_moves(loaded_moves)

        if bindings:
            self.set_bindings(bindings)
        if move_bindings:
            self.set_move_bindings(move_bindings)

    def set_bindings(self, bindings: Dict[str, Iterable[int]]) -> None:
        """Replace current bindings (used when reloading from options)."""
        self.bindings = _merge_default_bindings({k: list(v) for k, v in bindings.items()})

    def set_move_bindings(self, move_bindings: Dict[int, Tuple[int, int]]) -> None:
        """Replace current movement bindings."""
        self.move_bindings = _merge_default_moves({int(k): (int(v[0]), int(v[1])) for k, v in move_bindings.items()})

    def handle_keydown(self, event: pygame.event.Event) -> List[GameCommand]:
        cmds: List[GameCommand] = []
        key = event.key
        combined = encode_keybinding(key, event.mod)
        uni = getattr(event, "unicode", "")

        # --- Global-ish keys that should never combine with others ---

        # Escape: close dialog/config/targeting, or pause.
        if combined in self.bindings.get("escape", []):
            return [GameCommand("escape", raw_key=key)]

        # Fullscreen toggle
        if combined in self.bindings.get("toggle_fullscreen", []):
            return [GameCommand("toggle_fullscreen", raw_key=key)]

        # Door toggle
        if combined in self.bindings.get("toggle_door", []):
            return [GameCommand("toggle_door", raw_key=key)]

        # Ability bar page cycling
        if combined in self.bindings.get("ability_page_prev", []):
            return [GameCommand("ability_page_prev", raw_key=key)]
        if combined in self.bindings.get("ability_page_next", []):
            return [GameCommand("ability_page_next", raw_key=key)]



        # Help ('?')
        if uni == "?":
            return [GameCommand("show_help", raw_key=key)]

        # Ctrl+A to open abilities manager (without stealing plain 'a' movement)
        if key == pygame.K_a and getattr(event, "mod", 0) & pygame.KMOD_CTRL:
            return [GameCommand("open_abilities", raw_key=key)]

        # --- Ability hotkeys (1–10; '0' => 10) ---
        if pygame.K_1 <= key <= pygame.K_9:
            hk = key - pygame.K_0
            return [GameCommand("ability_hotkey", hotkey=hk, raw_key=key)]
        if key == pygame.K_0:
            return [GameCommand("ability_hotkey", hotkey=10, raw_key=key)]


        # --- Confirm keys (ENTER / SPACE) ---
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            return [GameCommand("confirm", raw_key=key)]

        # --- Single-key game actions that don't depend on direction ---

        for kind in (
            "examine",
            "pickup",
            "possess_nearest",
            "open_inventory",
            "yawp",
            "wait",
            "stairs_down",
            "stairs_up_or_map",
            "open_fractal_editor",
            "talk",
            "quick_activate_all",
            "look_action",

        ):
            if combined in self.bindings.get(kind, []):
                cmds.append(GameCommand(kind, raw_key=key))
                return cmds

        # --- Directional input (movement / cursor movement) ---

        if combined in self.move_bindings:
            cmds.append(GameCommand("move", vector=self.move_bindings[combined], raw_key=key))

 
        return cmds
       
       
       
       
    def handle_mousebutton(self, event: pygame.event.Event) -> List[GameCommand]:
        """Map a raw mouse button event to a click command."""
        return [
            GameCommand(
                kind="mouse_click",
                mouse_pos=getattr(event, "pos", None),
                mouse_button=getattr(event, "button", None),
            )
        ]

    def handle_mousemotion(self, event: pygame.event.Event) -> List[GameCommand]:
        """Map mouse motion to a hover/aim update command."""
        return [
            GameCommand(
                kind="mouse_move",
                mouse_pos=getattr(event, "pos", None),
            )
        ]

    def handle_mousewheel(self, event: pygame.event.Event) -> List[GameCommand]:
        """Map mouse wheel scrolling to a zoom command."""
        return [
            GameCommand(
                kind="mouse_wheel",
                wheel_y=getattr(event, "y", 0),
            )
        ]
