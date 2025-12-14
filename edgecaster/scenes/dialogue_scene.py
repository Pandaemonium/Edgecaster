from __future__ import annotations

from typing import Optional, Any
import pygame

from .base import PopupMenuScene


class DialoguePopupScene(PopupMenuScene):
    """
    Popup menu that walks a dialogue tree node-by-node.

    This scene is intentionally "duck-typed":
    - tree.nodes is a dict-like mapping node_id -> node
    - tree.start_id is the default node id (optional)
    - node has: title, body, choices
    - each choice has: text, next_id (optional), effect (optional callable(game))

    This avoids importing edgecaster.events at module import time, which can
    create circular imports (ascii -> game -> events -> dialogue_scene -> scenes -> manager -> ascii).
    """

    FOOTER_TEXT = ""  # dialogues don't need the noisy menu footer

    def __init__(
        self,
        window_rect: Optional[pygame.Rect] = None,
        *,
        game: Any,
        tree: Any,
        start_node: Optional[str] = None,
        scale: float = 0.7,
        dim_background: bool = True,
    ) -> None:
        self.game = game
        self.tree = tree

        # Determine entry node id
        entry = start_node
        if entry is None:
            entry = getattr(tree, "start_id", None)
        if entry is None:
            # fall back to the first key in nodes if present
            try:
                entry = next(iter(tree.nodes.keys()))
            except Exception:
                entry = None

        self.node_id = entry
        super().__init__(
            window_rect=window_rect,
            dim_background=dim_background,
            scale=scale,
        )

    def _node(self):
        if self.node_id is None:
            return None
        try:
            return self.tree.nodes[self.node_id]
        except Exception:
            return None

    def get_ascii_art(self) -> Optional[str]:
        node = self._node()
        if node is None:
            return "Dialogue error: missing node."
        title = getattr(node, "title", "") or ""
        body = getattr(node, "body", "") or ""
        if title and body:
            return f"{title}\n\n{body}"
        return title or body

    def get_menu_items(self):
        node = self._node()
        if node is None:
            return ["Continue..."]
        choices = getattr(node, "choices", None) or []
        # menu items are just the choice text
        out = []
        for c in choices:
            out.append(getattr(c, "text", "Continue..."))
        return out or ["Continue..."]

    def on_activate(self, index: int, manager) -> bool:
        node = self._node()
        if node is None:
            # just close
            return True

        choices = getattr(node, "choices", None) or []
        if not choices or index < 0 or index >= len(choices):
            return True

        choice = choices[index]

        # optional side-effect
        eff = getattr(choice, "effect", None)
        if callable(eff):
            try:
                eff(self.game)
            except Exception as e:
                try:
                    self.game.log.add(f"(Dialogue effect error: {e!r})")
                except Exception:
                    pass

        # advance
        next_id = getattr(choice, "next_id", None)
        if next_id is None:
            return True  # end dialogue -> close popup

        self.node_id = next_id

        # Rebuild widgets so the text/choices update immediately
        try:
            items = self.get_menu_items()
            self._build_widgets(items)
        except Exception:
            pass

        return False  # keep open

    def on_back(self, manager) -> bool:
        # Esc closes dialogue
        return True
