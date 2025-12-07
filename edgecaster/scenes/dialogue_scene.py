from __future__ import annotations

from typing import Optional, List
import pygame

from .base import PopupMenuScene
from edgecaster import events  # for DialogueTree, DialogueNode


class DialoguePopupScene(PopupMenuScene):
    """
    Popup menu that walks a DialogueTree node-by-node.

    - Title = node.title
    - Body text = node.body
    - Menu options = node.choices[*].text
    - Selecting a choice can:
        * run choice.effect(game)
        * jump to another node (choice.next_id)
        * end the dialogue (next_id is None) -> popup closes
    """

    # No noisy footer for dialogue, keep it clean.
    FOOTER_TEXT = ""

    def __init__(
        self,
        window_rect: Optional[pygame.Rect] = None,
        *,
        game,
        tree: events.DialogueTree,
        node_id: str,
        dim_background: bool = True,
        # kept for API completeness; SceneManager consumes it
        scale: float = 0.7,
    ) -> None:
        # Call PopupMenuScene with *only* the window_rect; avoid guessing
        # about its parameter list.
        super().__init__(window_rect=window_rect)

        # Respect dim_background if the base class uses it
        if hasattr(self, "dim_background"):
            self.dim_background = dim_background

        self.game = game
        self.tree = tree
        self.node_id = node_id

    # --- helpers --------------------------------------------------------

    @property
    def _node(self) -> events.DialogueNode:
        return self.tree.nodes[self.node_id]

    def _close_self(self, manager) -> None:
        """
        Remove this dialogue popup from the scene stack, keeping the
        underlying dungeon / parent scene intact.
        """
        stack = getattr(manager, "scene_stack", None)

        if stack is not None and stack:
            # Normal case: we're on top
            if stack[-1] is self and hasattr(manager, "pop_scene"):
                manager.pop_scene()
            else:
                # Just in case something got pushed above us
                if self in stack:
                    stack.remove(self)
                    # Keep window_stack in sync if this was opened as a window
                    win_stack = getattr(manager, "window_stack", None)
                    if (
                        win_stack is not None
                        and hasattr(self, "window_rect")
                        and self.window_rect in win_stack
                    ):
                        try:
                            win_stack.remove(self.window_rect)
                        except ValueError:
                            pass
            return

        # Fallback: no stack info; nuke current scene.
        if hasattr(manager, "set_scene"):
            manager.set_scene(None)

    # --- MenuScene hooks ------------------------------------------------

    def get_menu_items(self) -> list[str]:
        # Show the choices text; if no choices, a simple "Continue" is shown.
        if not self._node.choices:
            return ["Continue..."]
        return [c.text for c in self._node.choices]

    def get_ascii_art(self) -> str:
        """
        We abuse the 'ascii_art' hook to render title + body above the options.
        It's just regular text; PopupMenuScene will center it.
        """
        node = self._node
        lines: list[str] = []
        if node.title:
            lines.append(node.title)
            lines.append("")  # blank line between title and body
        if node.body:
            lines.extend(node.body.splitlines())
        return "\n".join(lines)

    def on_activate(self, index: int, manager) -> bool:
        """
        Handle picking a dialogue option.

        Return True to close the popup, False to stay open on the *new* node.
        """
        node = self._node

        # No choices: just close on activate
        if not node.choices:
            self._close_self(manager)
            return True

        # Clamp index defensively
        if index < 0 or index >= len(node.choices):
            self._close_self(manager)
            return True

        choice = node.choices[index]

        # Default next node from the dialogue data
        next_id = choice.next_id

        # Apply any side-effect; it may optionally return an override next_id
        if choice.effect is not None:
            override = choice.effect(self.game)
            if isinstance(override, str):
                next_id = override

        # Decide where to go next (use possibly-overridden next_id)
        if next_id is None:
            # End of dialogue
            self._close_self(manager)
            return True

        if next_id not in self.tree.nodes:
            # Bad next_id; better to log and bail quietly than explode.
            self.game.log.add(f"(Broken dialogue: unknown node '{next_id}')")
            self._close_self(manager)
            return True

        # Move to the next node and stay in this scene.
        self.node_id = next_id
        return False


    def on_back(self, manager) -> bool:
        """Esc = bail out of the dialogue."""
        self._close_self(manager)
        return True

    @staticmethod
    def _wrap_text(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> List[str]:
        """
        Simple word-wrapping in pixel space using the given font.
        Respects explicit newlines in `text`.
        """
        lines: List[str] = []

        for raw_line in text.splitlines() or [""]:
            words = raw_line.split()
            if not words:
                # Preserve blank lines
                lines.append("")
                continue

            current = words[0]
            for word in words[1:]:
                test = current + " " + word
                if font.size(test)[0] <= max_width:
                    current = test
                else:
                    lines.append(current)
                    current = word
            lines.append(current)

        return lines


        
    # inside DialoguePopupScene
    def _draw_panel(self, manager, options):
        renderer = manager.renderer
        surface = renderer.surface

        title_font = renderer.font
        body_font = renderer.small_font

        padding_x = 24
        padding_y = 16

        # ----- First pass: measure content -----
        node = self._node
        title = node.title or ""
        body_text = node.body or ""

        # Wrap body text
        max_body_width_guess = int(renderer.width * 0.6)
        body_lines_for_size = self._wrap_text(
            body_text, body_font, max_body_width_guess
        )

        widths = []
        total_height = padding_y

        # Title
        if title:
            tw, th = title_font.size(title)
            widths.append(tw)
            total_height += th + 8

        # Body
        for line in body_lines_for_size:
            txt = line if line else " "
            w, h = body_font.size(txt)
            widths.append(w)
            total_height += h + 2

        if body_lines_for_size:
            total_height += 12
        else:
            total_height += 4

        # Choices (small font)
        for label in options:
            w, h = body_font.size("▶ " + label)
            widths.append(w)
            total_height += h + 4

        total_height += padding_y
        content_width = max(widths) if widths else 100

        # --- Clamp / center panel ---
        max_w = renderer.width - 40
        max_h = renderer.height - 40
        panel_width = min(content_width + 2 * padding_x, max_w)
        panel_height = min(total_height, max_h)

        x = (renderer.width - panel_width) // 2
        y = (renderer.height - panel_height) // 2
        rect = pygame.Rect(x, y, panel_width, panel_height)
        self.window_rect = rect

        # --- Render actual panel ---
        panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        panel.fill((10, 10, 20, 240))
        pygame.draw.rect(panel, (220, 220, 240, 255), panel.get_rect(), 2)

        y = padding_y

        # Title
        if title:
            surf = title_font.render(title, True, renderer.player_color)
            tx = (panel.get_width() - surf.get_width()) // 2
            panel.blit(surf, (tx, y))
            y += surf.get_height() + 8

        # Body (wrapped using actual width)
        max_body_width = panel.get_width() - 2 * padding_x
        body_lines = self._wrap_text(body_text, body_font, max_body_width)

        for line in body_lines:
            surf = body_font.render(line if line else " ", True, renderer.fg)
            bx = (panel.get_width() - surf.get_width()) // 2
            panel.blit(surf, (bx, y))
            y += surf.get_height() + 2

        y += 12 if body_lines else 4

        # Choices — identical style to urgent messages
        self._option_rects = []
        for idx, label in enumerate(options):
            selected = (idx == self.selected_idx)
            color = renderer.player_color if selected else renderer.fg
            prefix = "▶ " if selected else "  "
            surf = body_font.render(prefix + label, True, color)

            lx = (panel.get_width() - surf.get_width()) // 2
            ly = y
            local_rect = surf.get_rect(topleft=(lx, ly))
            panel.blit(surf, local_rect.topleft)

            self._option_rects.append(local_rect.move(rect.left, rect.top))
            y += surf.get_height() + 4

        surface.blit(panel, rect.topleft)

