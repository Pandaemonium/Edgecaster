from __future__ import annotations

from typing import Optional, Any, Callable
import pygame

from edgecaster.ui.widgets import _wrap_text_px, PortraitWidget
from .base import PopupMenuScene, MenuFrameWidget


class DialoguePopupScene(PopupMenuScene):
    """
    Windowed dialogue scene (popup) that walks a DialogueTree node-by-node.

    IMPORTANT: events.start_dialogue(...) calls this with kw `start_node=...`,
    so we accept that kw here to avoid falling back to the legacy urgent-popup
    recursion path.
    """
    FOOTER_TEXT = ""
    PORTRAIT_SIZE_PX = 480 
    PORTRAIT_PADDING = 12



    # Dialogue should not use logical-surface scaling.
    def get_logical_panel_size(self, manager):
        return None

    def __init__(
        self,
        game: Any,
        tree: Any,
        *,
        start_node: Optional[str] = None,   # <-- IMPORTANT: matches events.py
        node_id: Optional[str] = None,      # alias for convenience
        window_rect: Optional[pygame.Rect] = None,
        dim_background: bool = True,
        scale: float = 0.7,
    ) -> None:
        self.game = game
        self.tree = tree
        # If the DialogueTree declares a theme, treat it as a window override.
        self.music_override_key = getattr(tree, "music_key", None)

        # Choose starting node
        self.node_id: str = (
            node_id
            or start_node
            or getattr(tree, "start_id", None)
            or getattr(tree, "root_id", None)
            or "start"
        )

        # visual knobs some menus expect to exist
        self.visual_effects: list[str] = []

        # We want to snug-fit once we have access to manager/renderer.
        self._needs_initial_refit: bool = True

        super().__init__(window_rect=window_rect, dim_background=dim_background, scale=scale)

    # ------------------------------------------------------------------ #
    # Tree helpers (duck-typed to events.DialogueTree / DialogueNode)
    # ------------------------------------------------------------------ #

    def _get_node(self) -> Any:
        nodes = getattr(self.tree, "nodes", None)
        if nodes is None:
            raise KeyError("Dialogue tree has no .nodes")
        return nodes[self.node_id]

    def _choice_text(self, choice: Any) -> str:
        if isinstance(choice, str):
            return choice
        return str(getattr(choice, "text", getattr(choice, "label", str(choice))))

    def _choice_next_id(self, choice: Any) -> Optional[str]:
        if isinstance(choice, str):
            return None
        return getattr(choice, "next_id", None)

    def _choice_effect(self, choice: Any) -> Optional[Callable[[Any], None]]:
        if isinstance(choice, str):
            return None
        eff = getattr(choice, "effect", None)
        return eff if callable(eff) else None

    # ------------------------------------------------------------------ #
    # GeneralMenuScene hooks
    # ------------------------------------------------------------------ #

    def get_ascii_art(self) -> str:
        try:
            node = self._get_node()
        except Exception:
            return ""
        return str(getattr(node, "title", "") or "")

    def get_body_text(self) -> Optional[str]:
        try:
            node = self._get_node()
        except Exception:
            return None
        body = str(getattr(node, "body", "") or "")
        return body if body.strip() else None

    def wants_wrapped_choices(self) -> bool:
        return True

    def get_menu_items(self) -> list[str]:
        try:
            node = self._get_node()
            choices = getattr(node, "choices", []) or []
        except Exception:
            choices = []
        return [self._choice_text(c) for c in choices] or ["(End)"]



    # ------------------------------------------------------------------ #
    # Speaker portrait (cosmetic)
    # ------------------------------------------------------------------ #

    def _get_portrait_entity(self) -> Any | None:
        """Resolve a speaker entity for portrait purposes.

        DialogueTree ids are typically "npc:<npc_id>" where <npc_id> matches
        actor.tags["npc_id"]. We search the current level's actors for a match.

        If no match is found, we simply omit the portrait (cosmetic only).
        """
        try:
            tree_id = str(getattr(self.tree, "id", "") or "")
            npc_id = ""
            if tree_id.startswith("npc:"):
                npc_id = tree_id.split("npc:", 1)[1].strip()

            # Optional explicit hint set by a builder (not required).
            ent = getattr(self.tree, "speaker", None)
            if ent is not None:
                return ent

            lvl = getattr(self.game, "_level", lambda: None)()
            actors = getattr(lvl, "actors", None)
            if isinstance(actors, dict):
                if not npc_id:
                    return None
                for a in actors.values():
                    try:
                        tags = getattr(a, "tags", None) or {}
                        if str(tags.get("npc_id") or "") == npc_id:
                            return a
                    except Exception:
                        continue
        except Exception:
            pass
        return None


    def _build_widgets(self, items: list[str]) -> None:
        """Build the standard popup UI, then inject an optional portrait.

        Critical: this must never raise, or events.py may fall back into legacy
        recursion paths. Any portrait failure should degrade to 'no portrait'.
        """
        try:
            super()._build_widgets(items)
        except Exception:
            # If the base build fails, let the normal error handling happen.
            raise

        try:
            root = getattr(self, "root", None)
            if not isinstance(root, MenuFrameWidget):
                return

            ent = self._get_portrait_entity()
            if ent is None:
                root.art = None
                return

            g = "?"
            try:
                g = str(getattr(ent, "glyph", "?") or "?")[0]
            except Exception:
                pass

            root.art = PortraitWidget(entity=ent, glyph=g, size_px=self.PORTRAIT_SIZE_PX, padding=self.PORTRAIT_PADDING, prefer_sprite=True)
            # Ensure it is registered as a child so it draws/layouts.
            try:
                root.add_child(root.art)
            except Exception:
                pass
        except Exception:
            # Portrait must never crash dialogue opening.
            try:
                if isinstance(getattr(self, "root", None), MenuFrameWidget):
                    self.root.art = None
            except Exception:
                pass
            return
    # ------------------------------------------------------------------ #
    # Snug sizing (safe)
    # ------------------------------------------------------------------ #

    def _ensure_window_rect(self, manager) -> None:
        """
        Must be idempotent. No widget rebuilding loops.

        If window_rect is missing, create a reasonable popup rect using the
        manager helper, then we can refit snugly on first update.
        """
        if self.window_rect is not None and self.window_rect.width > 0 and self.window_rect.height > 0:
            return
        try:
            self.window_rect = manager.compute_child_window_rect(scale=self.popup_scale)
        except Exception:
            r = manager.renderer
            self.window_rect = pygame.Rect(0, 0, int(r.width * self.popup_scale), int(r.height * self.popup_scale))
            self.window_rect.center = (r.width // 2, r.height // 2)

    def _compute_snug_rect(self, manager) -> pygame.Rect:
        r = manager.renderer
        screen_w, screen_h = r.width, r.height

        pad_x = 28
        pad_y = 22
        border = 2
        gap_title_body = 10
        gap_body_choices = 14
        gap_title_art = 10
        gap_art_body = 10
        art_size_px = self.PORTRAIT_SIZE_PX
        art_padding = self.PORTRAIT_PADDING
        art_h = art_size_px + 2 * art_padding

        font = getattr(r, "menu_font", getattr(r, "small_font", getattr(r, "font")))
        title_font = getattr(r, "menu_title_font", font)
        title_scale = 2

        title = (self.get_ascii_art() or "").strip()
        body_text = (self.get_body_text() or "").strip() if self.get_body_text() else ""
        choices = self.get_menu_items()

        max_text_w = min(760, int(screen_w * 0.78))
        max_text_w = max(360, max_text_w)

        # Title (scaled)
        t_w0, t_h0 = title_font.size(title or " ")
        title_w = t_w0 * title_scale
        title_h = t_h0 * title_scale

        has_portrait = (self._get_portrait_entity() is not None)


        # Body (wrapped)
        body_lines = _wrap_text_px(font, body_text, max_text_w) if body_text else []
        body_w = max((font.size(line)[0] for line in body_lines), default=0)
        body_h = len(body_lines) * font.get_height() + max(0, len(body_lines) - 1) * 2

        # Choices (wrapped)
        prefix_w = font.size("▶ ")[0]
        choice_lines_total = 0
        choice_w = 0
        for c in choices:
            lines = _wrap_text_px(font, str(c), max(1, max_text_w - prefix_w))
            choice_lines_total += max(1, len(lines))
            for line in lines:
                choice_w = max(choice_w, font.size(line)[0] + prefix_w)
        choice_h = choice_lines_total * (font.get_height() + 2)

        content_w = max(title_w if title else 0, body_w, choice_w, 320)

        content_h = 0
        if title:
            content_h += title_h
            if has_portrait:
                content_h += gap_title_art
                content_h += art_h
                if body_lines or choices:
                    content_h += gap_art_body
            elif body_lines or choices:
                content_h += gap_title_body
        elif has_portrait:
            # (Unlikely, but handle missing title gracefully)
            content_h += art_h
            if body_lines or choices:
                content_h += gap_art_body


        if body_lines:
            content_h += body_h
            if choices:
                content_h += gap_body_choices

        if choices:
            content_h += choice_h

        win_w = content_w + pad_x * 2 + border * 2
        win_h = content_h + pad_y * 2 + border * 2

        win_w = min(win_w, int(screen_w * 0.92))
        win_h = min(win_h, int(screen_h * 0.92))

        rect = pygame.Rect(0, 0, int(win_w), int(win_h))
        rect.center = (screen_w // 2, screen_h // 2)
        return rect

    def _refit_to_current_node(self, manager) -> None:
        """
        Recompute snug rect and rebuild widgets for this node.
        MUTATE the existing Rect in place so manager.window_stack stays consistent.
        """
        new_rect = self._compute_snug_rect(manager)
        if self.window_rect is None:
            self.window_rect = new_rect
        else:
            self.window_rect.update(new_rect)

        # Rebuild widgets so wrapping matches new node + new rect.
        self._build_widgets(self.get_menu_items())

    # ------------------------------------------------------------------ #
    # Lifecycle / behavior
    # ------------------------------------------------------------------ #

    def update(self, dt_ms: int, manager) -> None:
        # One-time snug fit after we have manager/renderer.
        if self._needs_initial_refit:
            self._needs_initial_refit = False
            try:
                self._refit_to_current_node(manager)
            except Exception:
                pass

        super().update(dt_ms, manager)

    def on_activate(self, index: int, manager) -> bool:
        # Resolve choice
        try:
            node = self._get_node()
            raw_choices = getattr(node, "choices", []) or []
        except Exception:
            raw_choices = []

        if not raw_choices:
            return True  # close

        if index < 0 or index >= len(raw_choices):
            return False

        choice = raw_choices[index]

        # Run optional effect(game)
        eff = self._choice_effect(choice)
        if eff is not None:
            try:
                eff(self.game)
            except Exception:
                pass

        next_id = self._choice_next_id(choice)
        if not next_id:
            return True  # close dialogue

        # Advance and refit
        self.node_id = str(next_id)
        self.selected_idx = 0
        self._refit_to_current_node(manager)
        return False  # keep open

    def on_back(self, manager) -> bool:
        # Esc closes dialogue.
        return True
