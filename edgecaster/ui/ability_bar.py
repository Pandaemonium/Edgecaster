# edgecaster/ui/ability_bar.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, TYPE_CHECKING, Callable

import pygame

from edgecaster.systems.abilities import Ability, build_abilities, compute_abilities_signature

if TYPE_CHECKING:  # avoids import cycles at runtime
    from edgecaster.game import Game

from edgecaster.systems.actions import ACTION_SUB_BUTTONS, SubButtonMeta  # UI metadata for sub-buttons









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
    View-only renderer for the ability bar.

    Responsibilities:
    - Lay out ability slots within bar_rect
    - Draw backgrounds, labels, main icon
    - Draw per-action sub-buttons (from action metadata)
    - Attach pygame.Rects to Ability objects for hit-testing

    It does *not* decide what abilities exist or what they do.
    """

    def __init__(self) -> None:
        # Hitboxes for the "Abilities" button and page arrows.
        self.abilities_button_rect: Optional[pygame.Rect] = None
        self.page_prev_rect: Optional[pygame.Rect] = None
        self.page_next_rect: Optional[pygame.Rect] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _layout_bar(
        self,
        bar_rect: pygame.Rect,
        count: int,
    ) -> List[pygame.Rect]:
        """
        Compute a list of equally spaced slot rects inside bar_rect
        for `count` abilities.
        """
        if count <= 0:
            return []

        # leave a little padding on left/right for the "Abilities" button & arrows
        left_margin = 120
        right_margin = 60
        top_margin = 4
        bottom_margin = 4

        inner = pygame.Rect(
            bar_rect.x + left_margin,
            bar_rect.y + top_margin,
            max(0, bar_rect.w - left_margin - right_margin),
            max(0, bar_rect.h - top_margin - bottom_margin),
        )

        gap = 6
        total_gap = gap * (count - 1)
        slot_w = (inner.w - total_gap) // max(1, count)
        slot_w = max(40, slot_w)  # don't collapse too hard
        slot_h = inner.h

        rects: List[pygame.Rect] = []
        x = inner.x
        for _ in range(count):
            rects.append(pygame.Rect(x, inner.y, slot_w, slot_h))
            x += slot_w + gap

        return rects

    # ------------------------------------------------------------------
    # Main draw
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Main draw
    # ------------------------------------------------------------------

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
        icon_drawer: Callable[[pygame.Surface, pygame.Rect, str, "Game"], None] | None = None,
    ) -> None:
        """
        Render the bar into bar_rect.

        - `icon_drawer(surface, rect, action_name, game)` is provided by the
          renderer (ascii.py) and may be None if you don't want icons.
        """
        # --- sync model from game -------------------------------------
        bar_state.sync_from_game(game)

        # Clear legacy hitboxes
        self.abilities_button_rect = None
        self.page_prev_rect = None
        self.page_next_rect = None

        for ab in bar_state.abilities:
            # We deliberately only clear the attributes we own.
            for attr in ("rect", "plus_rect", "minus_rect", "gear_rect"):
                if hasattr(ab, attr):
                    setattr(ab, attr, None)

            # Reset sub-button mapping per frame
            # (even if it didn't exist before, this is safe)
            ab.sub_button_rects = {}  # type: ignore[attr-defined]


        # --- draw bar background --------------------------------------
        pygame.draw.rect(surface, (10, 10, 10), bar_rect)
        pygame.draw.rect(surface, fg, bar_rect, 1)

        # --- "Abilities" button on the left ---------------------------
        label_surf = small_font.render("Abilities", True, fg)
        label_rect = label_surf.get_rect()
        label_rect.left = bar_rect.left + 8
        label_rect.centery = bar_rect.centery
        surface.blit(label_surf, label_rect)
        self.abilities_button_rect = label_rect.inflate(8, 4)

        # --- page arrows on the right ---------------------------------
        page_text = f"{bar_state.page + 1}/{bar_state.total_pages}"
        page_surf = small_font.render(page_text, True, fg)
        page_rect = page_surf.get_rect()
        page_rect.right = bar_rect.right - 8
        page_rect.centery = bar_rect.centery
        surface.blit(page_surf, page_rect)

        arrow_y = bar_rect.centery
        # prev "<"
        if bar_state.page > 0:
            prev_surf = small_font.render("<", True, fg)
            prev_rect = prev_surf.get_rect()
            prev_rect.right = page_rect.left - 8
            prev_rect.centery = arrow_y
            surface.blit(prev_surf, prev_rect)
            self.page_prev_rect = prev_rect
        else:
            self.page_prev_rect = None

        # next ">"
        if bar_state.page < bar_state.total_pages - 1:
            next_surf = small_font.render(">", True, fg)
            next_rect = next_surf.get_rect()
            next_rect.left = page_rect.right + 8
            next_rect.centery = arrow_y
            surface.blit(next_surf, next_rect)
            self.page_next_rect = next_rect
        else:
            self.page_next_rect = None

        # --- visible abilities ----------------------------------------
        vis = bar_state.visible_abilities()
        slot_rects = self._layout_bar(bar_rect, len(vis))

        for ability, rect in zip(vis, slot_rects):
            # Attach the main rect for hit-testing
            ability.rect = rect

            # Background
            is_active = ability.action == bar_state.active_action
            bg_color = (40, 40, 60) if is_active else (25, 25, 35)
            pygame.draw.rect(surface, bg_color, rect)
            pygame.draw.rect(surface, fg, rect, 1)

            # Main icon on the left
            icon_area = pygame.Rect(rect.x + 3, rect.y + 3, rect.height - 6, rect.height - 6)
            if icon_drawer is not None:
                # ascii.AsciiRenderer._draw_ability_icon_for_bar(surface, rect, action, game)
                icon_drawer(surface, icon_area, ability.action, game)
            else:
                # tiny fallback glyph
                pygame.draw.rect(surface, (90, 90, 120), icon_area, 1)

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
            text_x = icon_area.right + 4
            text_y = rect.y + (rect.height - text.get_height()) // 2
            surface.blit(text, (text_x, text_y))

            # Sub-buttons (from ACTION_SUB_BUTTONS metadata)
            sub_specs = ACTION_SUB_BUTTONS.get(ability.action, [])
            if sub_specs:
                sub_size = min(rect.height - 10, 22)
                sub_size = max(14, sub_size)
                sub_gap = 4
                cur_x = rect.right - 4

                # Lay out sub-buttons from right to left
                for spec in reversed(sub_specs):
                    cur_x -= sub_size
                    sub_rect = pygame.Rect(cur_x, rect.y + 4, sub_size, sub_size)

                    # Draw tiny button background + border
                    pygame.draw.rect(surface, (35, 35, 65), sub_rect)
                    pygame.draw.rect(surface, (150, 150, 200), sub_rect, 1)

                    # icon text: support both SubButtonMeta.icon and any legacy "glyph"
                    icon_txt = getattr(spec, "icon", getattr(spec, "glyph", "")) or ""
                    if icon_txt:
                        icon_surf = small_font.render(icon_txt, True, fg)
                        surface.blit(icon_surf, icon_surf.get_rect(center=sub_rect.center))

                    # Generic mapping: id -> rect for future consumers
                    mapping = getattr(ability, "sub_button_rects", None)
                    if mapping is None:
                        mapping = {}
                        ability.sub_button_rects = mapping  # type: ignore[attr-defined]
                    mapping[spec.id] = sub_rect

                    # Backwards-compat: specific attrs used by DungeonScene
                    kind = getattr(spec, "kind", "")
                    if kind == "param_delta":
                        delta = getattr(spec, "delta", None)
                        if delta is not None and delta > 0:
                            ability.plus_rect = sub_rect  # type: ignore[attr-defined]
                        elif delta is not None and delta < 0:
                            ability.minus_rect = sub_rect  # type: ignore[attr-defined]
                    elif kind == "open_config":
                        ability.gear_rect = sub_rect  # type: ignore[attr-defined]

                    cur_x -= sub_gap

        # After drawing the base bar, optionally paint the reorder overlay on top.
        if getattr(game, "ability_reorder_open", False):
            self._draw_reorder_overlay(
                surface=surface,
                bar_state=bar_state,
                small_font=small_font,
                fg=fg,
                width=width,
                bar_rect=bar_rect,
            )






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
