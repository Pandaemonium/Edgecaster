from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Tuple

from edgecaster.systems.abilities import Ability, build_abilities, compute_abilities_signature


@dataclass
class AbilityBarState:
    """
    Scene-owned view-model for the ability bar.

    Keeps ordering, paging, and active selection; renderer consumes this to draw.
    """

    abilities: List[Ability] = field(default_factory=list)
    order: List[str] = field(default_factory=list)  # ordered list of ability.action ids
    page: int = 0
    page_size: int = 10
    active_action: str | None = None
    signature: Tuple | None = None
    selected_index: int = 0  # used by the reordering UI

    def sync_from_game(self, game) -> None:
        """Rebuild abilities when the signature changes; keep ordering stable when possible."""
        sig = compute_abilities_signature(game)
        if sig == self.signature and self.abilities:
            return

        self.abilities = build_abilities(game)
        self.signature = sig

        actions = [ab.action for ab in self.abilities]
        seen = set()
        new_order: List[str] = []

        # keep existing order for still-present actions
        for act in self.order:
            if act in actions and act not in seen:
                new_order.append(act)
                seen.add(act)
        # append any new actions
        for act in actions:
            if act not in seen:
                new_order.append(act)
                seen.add(act)

        self.order = new_order

        # validate active/selected
        if self.active_action not in self.order and self.order:
            self.active_action = self.order[0]
        if self.selected_index >= len(self.order):
            self.selected_index = max(0, len(self.order) - 1)
        self.page = min(self.page, self.total_pages - 1)

    @property
    def total_pages(self) -> int:
        if not self.order:
            return 1
        return max(1, (len(self.order) + self.page_size - 1) // self.page_size)

    def visible_actions(self) -> List[str]:
        start = self.page * self.page_size
        end = start + self.page_size
        return self.order[start:end]

    def visible_abilities(self) -> List[Ability]:
        lookup: Dict[str, Ability] = {ab.action: ab for ab in self.abilities}
        return [lookup[a] for a in self.visible_actions() if a in lookup]

    def set_active(self, action: str | None) -> None:
        if action and action in self.order:
            self.active_action = action

    def move_selection(self, delta: int) -> None:
        if not self.order:
            return
        self.selected_index = max(0, min(len(self.order) - 1, self.selected_index + delta))

    def move_selected_item(self, delta: int) -> None:
        """Move the selected ability up/down in the ordering."""
        if not self.order:
            return
        idx = self.selected_index
        new_idx = max(0, min(len(self.order) - 1, idx + delta))
        if new_idx == idx:
            return
        item = self.order.pop(idx)
        self.order.insert(new_idx, item)
        self.selected_index = new_idx

    def next_page(self) -> None:
        self.page = min(self.total_pages - 1, self.page + 1)

    def prev_page(self) -> None:
        self.page = max(0, self.page - 1)

    def action_at_index(self, idx: int) -> str | None:
        if 0 <= idx < len(self.order):
            return self.order[idx]
        return None

    def active_index_on_page(self) -> int | None:
        if self.active_action is None:
            return None
        try:
            global_idx = self.order.index(self.active_action)
        except ValueError:
            return None
        start = self.page * self.page_size
        end = start + self.page_size
        if start <= global_idx < end:
            return global_idx - start
        return None

