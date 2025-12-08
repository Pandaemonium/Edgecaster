from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict, Iterable

import pygame


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
        self.bindings: Dict[str, List[int]] = {
            "escape": [pygame.K_ESCAPE],
            "toggle_fullscreen": [pygame.K_F11],
            "show_help": [],  # handled via unicode '?'
            "examine": [pygame.K_x],
            "pickup": [pygame.K_g],
            "possess_nearest": [pygame.K_p],
            "open_inventory": [pygame.K_i],
            "open_abilities": [],  # reserved for ctrl+A below
            "yawp": [pygame.K_y],
            "wait": [pygame.K_KP5],
            "stairs_down": [pygame.K_PERIOD, pygame.K_GREATER],
            "stairs_up_or_map": [pygame.K_COMMA, pygame.K_LESS, pygame.K_m],
            "open_fractal_editor": [pygame.K_PLUS, pygame.K_EQUALS],
            "talk": [pygame.K_t],
            "quick_activate_all": [pygame.K_f],
        }
        if bindings:
            for k, vals in bindings.items():
                self.bindings[k] = list(vals)

        # movement bindings
        default_move = {
            pygame.K_UP: (0, -1),
            pygame.K_DOWN: (0, 1),
            pygame.K_LEFT: (-1, 0),
            pygame.K_RIGHT: (1, 0),
            pygame.K_w: (0, -1),
            pygame.K_s: (0, 1),
            pygame.K_a: (-1, 0),
            pygame.K_d: (1, 0),
            pygame.K_q: (-1, -1),
            pygame.K_e: (1, -1),
            pygame.K_z: (-1, 1),
            pygame.K_c: (1, 1),
            pygame.K_KP1: (-1, 1),
            pygame.K_KP2: (0, 1),
            pygame.K_KP3: (1, 1),
            pygame.K_KP4: (-1, 0),
            pygame.K_KP6: (1, 0),
            pygame.K_KP7: (-1, -1),
            pygame.K_KP8: (0, -1),
            pygame.K_KP9: (1, -1),
        }
        if move_bindings:
            default_move.update(move_bindings)
        self.move_bindings = default_move

    def handle_keydown(self, event: pygame.event.Event) -> List[GameCommand]:
        cmds: List[GameCommand] = []
        key = event.key
        uni = getattr(event, "unicode", "")

        # --- Global-ish keys that should never combine with others ---

        # Escape: close dialog/config/targeting, or pause.
        if key in self.bindings.get("escape", []):
            return [GameCommand("escape", raw_key=key)]

        # Fullscreen toggle
        if key in self.bindings.get("toggle_fullscreen", []):
            return [GameCommand("toggle_fullscreen", raw_key=key)]

        # Help ('?')
        if uni == "?":
            return [GameCommand("show_help", raw_key=key)]

        # Ctrl+A to open abilities manager (without stealing plain 'a' movement)
        if key == pygame.K_a and getattr(event, "mod", 0) & pygame.KMOD_CTRL:
            return [GameCommand("open_abilities", raw_key=key)]

        # --- Ability hotkeys (1-9) ---
        if pygame.K_1 <= key <= pygame.K_9:
            hk = key - pygame.K_0
            return [GameCommand("ability_hotkey", hotkey=hk, raw_key=key)]

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
        ):
            if key in self.bindings.get(kind, []):
                cmds.append(GameCommand(kind, raw_key=key))
                return cmds

        # --- Directional input (movement / cursor movement) ---

        if key in self.move_bindings:
            cmds.append(GameCommand("move", vector=self.move_bindings[key], raw_key=key))

 
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
