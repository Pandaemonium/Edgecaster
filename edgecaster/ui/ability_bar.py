# edgecaster/ui/ability_bar.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, TYPE_CHECKING

import pygame

from edgecaster.systems.abilities import Ability, build_abilities, compute_abilities_signature

if TYPE_CHECKING:  # avoids import cycles at runtime
    from edgecaster.game import Game


# ---------------------------------------------------------------------
# AbilityBarState (model/controller)
# ---------------------------------------------------------------------


@dataclass
class AbilityBarState:
    """
    Pure model/controller for the ability bar.

    Owns:
    - abilities: full list of Ability objects from systems/abilities
    - order: current ordering of actions (for reordering UI)
    - selected_index: index into `order` for the reorder UI
    - page, page_size: paging for both the bar and the reorder UI
    - active_action: which action is considered "selected" in gameplay

    NOTE: No pygame dependency, no drawing, no input handling.
    """
    abilities: List[Ability] = field(default_factory=list)
    order: List[str] = field(default_factory=list)
    selected_index: int = 0        # index into `order`
    page: int = 0                  # which page of the bar is visible
    page_size: int = 8             # logical page size; renderer may still squeeze more/less visually
    active_action: Optional[str] = None

    _signature: Optional[Tuple] = None  # compute_abilities_signature(game)

    # ---- core sync ---------------------------------------------------

    def sync_from_game(self, game: "Game") -> None:
        """
        Ensure the ability list/order match the current Game state.

        Rebuilds when the (generators, illuminator, custom patterns,
        host-visible actions) signature changes.
        """
        sig = compute_abilities_signature(game)
        if sig != self._signature or not self.abilities:
            self._signature = sig
            self.abilities = build_abilities(game)
            new_actions = [ab.action for ab in self.abilities]

            # Preserve existing order where possible; append any new actions.
            if self.order:
                existing = [a for a in self.order if a in new_actions]
                for a in new_actions:
                    if a not in existing:
                        existing.append(a)
                self.order = existing
            else:
                self.order = list(new_actions)

            # Ensure active_action is valid; default to first ability if needed.
            if self.active_action not in self.order:
                self.active_action = self.order[0] if self.order else None

            self._sync_selection_to_active()
        else:
            # Signature hasn't changed, but keep order consistent with abilities
            existing_actions = {ab.action for ab in self.abilities}
            self.order = [a for a in self.order if a in existing_actions]
            for a in [ab.action for ab in self.abilities]:
                if a not in self.order:
                    self.order.append(a)

            if self.active_action not in self.order and self.order:
                self.active_action = self.order[0]

            self._sync_selection_bounds()

    def invalidate(self) -> None:
        """Force the next sync_from_game() to rebuild the abilities list."""
        self._signature = None

    # ---- selection / paging helpers ---------------------------------

    @property
    def total_pages(self) -> int:
        if not self.order or self.page_size <= 0:
            return 1
        return max(1, (len(self.order) + self.page_size - 1) // self.page_size)

    def visible_abilities(self) -> List[Ability]:
        """
        Abilities to display on the current page, in the current order.
        """
        if not self.order:
            return []
        start = self.page * self.page_size
        end = start + self.page_size
        slice_actions = self.order[start:end]
        by_action: Dict[str, Ability] = {ab.action: ab for ab in self.abilities}
        return [by_action[a] for a in slice_actions if a in by_action]

    def active_index_on_page(self) -> Optional[int]:
        """
        Index *within the visible page* for the currently active action, or None.
        """
        if not self.active_action or not self.order:
            return None
        if self.active_action not in self.order:
            return None
        idx = self.order.index(self.active_action)
        start = self.page * self.page_size
        end = start + self.page_size
        if start <= idx < end:
            return idx - start
        return None

    def action_at_index(self, index: int) -> Optional[str]:
        if 0 <= index < len(self.order):
            return self.order[index]
        return None

    # ---- activation / navigation API --------------------------------

    def set_active(self, action: str) -> None:
        """
        Mark an action as the currently active ability, and sync selection/page.
        """
        if action not in self.order:
            return
        self.active_action = action
        self._sync_selection_to_active()

    def select_next(self) -> None:
        if not self.order:
            return
        self.selected_index = (self.selected_index + 1) % len(self.order)
        self.active_action = self.order[self.selected_index]
        self._sync_page_from_selection()

    def select_prev(self) -> None:
        if not self.order:
            return
        self.selected_index = (self.selected_index - 1) % len(self.order)
        self.active_action = self.order[self.selected_index]
        self._sync_page_from_selection()

    def move_selection(self, delta: int) -> None:
        """
        Move the reorder cursor up/down in the order list (used by reorder UI).
        """
        if not self.order or not delta:
            return
        self.selected_index = max(0, min(len(self.order) - 1, self.selected_index + delta))
        self._sync_page_from_selection()

    def move_selected_item(self, dx: int) -> None:
        """
        Swap the selected ability with its neighbor (←/→) in the order list.
        """
        if not self.order or dx == 0:
            return
        new_idx = self.selected_index + (1 if dx > 0 else -1)
        if 0 <= new_idx < len(self.order):
            self.order[self.selected_index], self.order[new_idx] = (
                self.order[new_idx],
                self.order[self.selected_index],
            )
            self.selected_index = new_idx
            self._sync_page_from_selection()

    def prev_page(self) -> None:
        total = self.total_pages
        if total <= 1:
            return
        self.page = (self.page - 1) % total

    def next_page(self) -> None:
        total = self.total_pages
        if total <= 1:
            return
        self.page = (self.page + 1) % total

    # ---- internal helpers -------------------------------------------

    def _sync_selection_to_active(self) -> None:
        if self.active_action in self.order:
            self.selected_index = self.order.index(self.active_action)
        else:
            self.selected_index = min(self.selected_index, max(0, len(self.order) - 1))
        self._sync_page_from_selection()

    def _sync_page_from_selection(self) -> None:
        if self.page_size > 0 and self.order:
            self.page = self.selected_index // self.page_size
        else:
            self.page = 0

    def _sync_selection_bounds(self) -> None:
        if not self.order:
            self.selected_index = 0
            self.page = 0
            return
        if self.selected_index >= len(self.order):
            self.selected_index = len(self.order) - 1
        if self.selected_index < 0:
            self.selected_index = 0
        self._sync_page_from_selection()


# ---------------------------------------------------------------------
# AbilityBarRenderer (view)
# ---------------------------------------------------------------------


class AbilityBarRenderer:
    """
    Pure drawing helper for the ability bar.

    Responsibilities:
    - Draw background bar + "Abilities" button + page arrows
    - Draw ability tiles, labels, and hotkeys
    - Attach hit-test rects to Ability instances:
        ability.rect, ability.gear_rect, ability.plus_rect, ability.minus_rect
    - Draw the ability reorder overlay when game.ability_reorder_open is True

    Contains no game logic: it never calls game.* mutators or queue actions.
    """

    def __init__(self) -> None:
        self.abilities_button_rect: Optional[pygame.Rect] = None
        self.page_prev_rect: Optional[pygame.Rect] = None
        self.page_next_rect: Optional[pygame.Rect] = None

    def draw(
        self,
        surface: pygame.Surface,
        game: "Game",
        bar_state: AbilityBarState,
        bar_rect: pygame.Rect,
        *,
        small_font: pygame.font.Font,
        fg: Tuple[int, int, int],
        width: int,
    ) -> None:
        # --- Background bar is already drawn by caller; just decorate. ---

        vis = bar_state.visible_abilities()
        total_pages = bar_state.total_pages

        # "Abilities" manager button (bottom-left)
        btn_w, btn_h = 88, 26
        self.abilities_button_rect = pygame.Rect(6, bar_rect.top + 6, btn_w, btn_h)
        pygame.draw.rect(surface, (30, 30, 55), self.abilities_button_rect)
        pygame.draw.rect(surface, (120, 120, 170), self.abilities_button_rect, 1)
        label_surf = small_font.render("Abilities", True, fg)
        surface.blit(label_surf, label_surf.get_rect(center=self.abilities_button_rect.center))

        # Page arrows
        self.page_prev_rect = None
        self.page_next_rect = None
        if total_pages > 1:
            btn_h = 22
            self.page_prev_rect = pygame.Rect(self.abilities_button_rect.right + 6, bar_rect.top + 6, 22, btn_h)
            self.page_next_rect = pygame.Rect(self.page_prev_rect.right + 4, bar_rect.top + 6, 22, btn_h)
            for rect, dir_sign in ((self.page_prev_rect, -1), (self.page_next_rect, 1)):
                pygame.draw.rect(surface, (35, 35, 60), rect)
                pygame.draw.rect(surface, (150, 150, 190), rect, 1)
                if dir_sign < 0:
                    pts = [
                        (rect.right - 6, rect.top + 4),
                        (rect.left + 6, rect.centery),
                        (rect.right - 6, rect.bottom - 4),
                    ]
                else:
                    pts = [
                        (rect.left + 6, rect.top + 4),
                        (rect.right - 6, rect.centery),
                        (rect.left + 6, rect.bottom - 4),
                    ]
                pygame.draw.polygon(surface, (200, 200, 230), pts)

        # Ability tiles for current page: reset rects
        for ab in vis:
            # The Ability dataclass itself doesn't have these, but the
            # renderer is allowed to hang dynamic attributes on instances
            # for hit-testing.
            ab.rect = ab.gear_rect = ab.plus_rect = ab.minus_rect = None  # type: ignore[attr-defined]

        margin = 120  # leave room for abilities button/controls
        gap = 6
        n = max(1, len(vis))
        avail_w = width - 2 * margin
        box_w = (avail_w - gap * (n - 1)) / max(1, n)
        x = margin

        for idx_on_page, ability in enumerate(vis):
            rect = pygame.Rect(int(x), bar_rect.top + 8, int(box_w), bar_rect.height - 16)
            ability.rect = rect  # type: ignore[attr-defined]
            ability.plus_rect = None  # type: ignore[attr-defined]
            ability.minus_rect = None  # type: ignore[attr-defined]
            ability.gear_rect = None  # type: ignore[attr-defined]

            is_active = bar_state.active_action == ability.action

            if is_active:
                border = (255, 255, 180)
                fill = (45, 45, 70)
            else:
                border = (120, 120, 160)
                fill = (25, 25, 45)

            pygame.draw.rect(surface, fill, rect)
            pygame.draw.rect(surface, border, rect, 2)

            # Label (with radius hint for activate_all)
            label = ability.name
            if ability.action == "activate_all":
                try:
                    radius = game.get_param_value("activate_all", "radius")
                    label = f"Activate R ({radius})"
                except Exception:
                    label = "Activate R"

            if ability.hotkey:
                label = f"{ability.hotkey}:{label}"

            text = small_font.render(label, True, fg)
            text_x = rect.x + (rect.w - text.get_width()) // 2
            text_y = rect.y + (rect.h - text.get_height()) // 2
            surface.blit(text, (text_x, text_y))

            # (Icons intentionally omitted for now to avoid coupling to AsciiRenderer.)
            x += box_w + gap

        # Ability reorder overlay (centered panel above the bar)
        if getattr(game, "ability_reorder_open", False):
            self._draw_reorder_overlay(surface, bar_state, small_font, fg, width, bar_rect)

    # -----------------------------------------------------------------
    # Reorder overlay
    # -----------------------------------------------------------------

    def _draw_reorder_overlay(
        self,
        surface: pygame.Surface,
        bar_state: AbilityBarState,
        small_font: pygame.font.Font,
        fg: Tuple[int, int, int],
        width: int,
        bar_rect: pygame.Rect,
    ) -> None:
        overlay_w = min(width - 40, 520)
        overlay_h = 260
        overlay_x = (width - overlay_w) // 2
        overlay_y = bar_rect.top - overlay_h - 10
        if overlay_y < 10:
            overlay_y = 10

        panel = pygame.Rect(overlay_x, overlay_y, overlay_w, overlay_h)
        pygame.draw.rect(surface, (10, 10, 25), panel)
        pygame.draw.rect(surface, (200, 200, 240), panel, 2)

        # Title
        title_surf = small_font.render("Ability order", True, (255, 255, 210))
        surface.blit(title_surf, (panel.x + 10, panel.y + 8))

        # Instructions
        instructions = "↑/↓ move, ←/→ swap, ENTER confirm, ESC cancel"
        instr_surf = small_font.render(instructions, True, (180, 180, 210))
        surface.blit(instr_surf, (panel.x + 10, panel.bottom - instr_surf.get_height() - 8))

        # List area
        list_top = panel.y + 8 + title_surf.get_height() + 8
        line_h = small_font.get_height() + 4
        max_rows = max(1, (panel.bottom - 8 - instr_surf.get_height() - 8 - list_top) // line_h)

        # All abilities in current order
        actions = bar_state.order
        abilities_by_action: Dict[str, Ability] = {ab.action: ab for ab in bar_state.abilities}

        start_idx = 0
        if bar_state.selected_index >= max_rows:
            # Simple vertical scrolling so the cursor is always visible
            start_idx = bar_state.selected_index - max_rows + 1

        for row, idx in enumerate(range(start_idx, min(len(actions), start_idx + max_rows))):
            action_name = actions[idx]
            ab = abilities_by_action.get(action_name)
            label = ab.name if ab else action_name
            label = f"{idx + 1}. {label}"

            is_selected = (idx == bar_state.selected_index)
            col = (255, 225, 160) if is_selected else (210, 210, 230)
            text = small_font.render(label, True, col)
            y = list_top + row * line_h
            x = panel.x + 24
            surface.blit(text, (x, y))

            if is_selected:
                # Simple pointer triangle
                tri_y = y + text.get_height() // 2
                pygame.draw.polygon(
                    surface,
                    col,
                    [
                        (panel.x + 10, tri_y),
                        (panel.x + 18, tri_y - 5),
                        (panel.x + 18, tri_y + 5),
                    ],
                )
