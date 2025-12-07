from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, List

import pygame


@dataclass
class GameCommand:
    """Logical game command produced from raw keyboard input."""
    kind: str
    vector: Optional[Tuple[int, int]] = None   # e.g. movement or cursor step
    hotkey: Optional[int] = None              # ability hotkey (1-9)
    raw_key: Optional[int] = None             # original pygame keycode


class GameInput:
    """
    Scene-level mapper from pygame KEYDOWN events to abstract game commands.

    Important: this class *does not* know about renderer state (dialogs,
    config, targeting, ability bar). It just turns keys into high-level
    intents; DungeonScene decides how and when to apply them.
    """

    def handle_keydown(self, event: pygame.event.Event) -> List[GameCommand]:
        cmds: List[GameCommand] = []
        key = event.key
        uni = getattr(event, "unicode", "")

        # --- Global-ish keys that should never combine with others ---

        # Escape: close dialog/config/targeting, or pause.
        if key == pygame.K_ESCAPE:
            return [GameCommand("escape", raw_key=key)]

        # Fullscreen toggle
        if key == pygame.K_F11:
            return [GameCommand("toggle_fullscreen", raw_key=key)]

        # Help ('?')
        if uni == "?":
            return [GameCommand("show_help", raw_key=key)]

        # --- Ability hotkeys (1-9) ---
        if pygame.K_1 <= key <= pygame.K_9:
            hk = key - pygame.K_0
            return [GameCommand("ability_hotkey", hotkey=hk, raw_key=key)]

        # --- Confirm keys (ENTER / SPACE) ---
        if key in (pygame.K_RETURN, pygame.K_SPACE):
            return [GameCommand("confirm", raw_key=key)]

        # --- Single-key game actions that don't depend on direction ---

        if key == pygame.K_x:
            cmds.append(GameCommand("examine", raw_key=key))
        elif key == pygame.K_g:
            cmds.append(GameCommand("pickup", raw_key=key))
        elif key == pygame.K_p:
            cmds.append(GameCommand("possess_nearest", raw_key=key))
        elif key == pygame.K_i:
            cmds.append(GameCommand("open_inventory", raw_key=key))
        elif key == pygame.K_y:
            cmds.append(GameCommand("yawp", raw_key=key))
        elif key == pygame.K_KP5:
            cmds.append(GameCommand("wait", raw_key=key))
        elif key in (pygame.K_PERIOD, pygame.K_GREATER):
            cmds.append(GameCommand("stairs_down", raw_key=key))
        elif key in (pygame.K_COMMA, pygame.K_LESS, pygame.K_m):
            cmds.append(GameCommand("stairs_up_or_map", raw_key=key))
        elif key in (pygame.K_PLUS, pygame.K_EQUALS):
            cmds.append(GameCommand("open_fractal_editor", raw_key=key))
        elif key == pygame.K_t:
            cmds.append(GameCommand("talk", raw_key=key))
        elif key == pygame.K_f:
            # Quick "activate all" shortcut (same as old 'f' key)
            cmds.append(GameCommand("quick_activate_all", raw_key=key))

        # --- Directional input (movement / cursor movement) ---

        mapping = {
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
        if key in mapping:
            cmds.append(GameCommand("move", vector=mapping[key], raw_key=key))

        return cmds
