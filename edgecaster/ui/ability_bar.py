# edgecaster/ui/ability_bar.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, TYPE_CHECKING, Callable, Any

import pygame

from edgecaster.systems.abilities import Ability, build_abilities, compute_abilities_signature

if TYPE_CHECKING:  # avoids import cycles at runtime
    from edgecaster.game import Game

from edgecaster.systems.actions import ACTION_SUB_BUTTONS, SubButtonMeta  # UI metadata for sub-buttons









# ---------------------------------------------------------------------
# AbilityBarState (model/controller)
# ---------------------------------------------------------------------


@dataclass
class AbilityGroup:
    """A named group of actions with one active member."""

    id: str
    label: str
    members: List[str] = field(default_factory=list)
    active: Optional[str] = None


@dataclass
class AbilitySlot:
    """One slot in the ability bar: either a single action or a group."""

    kind: str  # "action" | "group"
    action: Optional[str] = None
    group_id: Optional[str] = None


@dataclass
class AbilitySlotView:
    """A resolved slot ready for rendering / hit-testing."""

    slot_index: int  # index in AbilityBarState.slots
    kind: str  # "action" | "group"
    ability: Ability
    group_id: Optional[str] = None
    group_label: Optional[str] = None
    group_members: List[Ability] = field(default_factory=list)


@dataclass
class AbilityBarState:
    """
    Pure model/controller for the ability bar.

    - Tracks the current set of abilities (from systems/abilities).
    - Maintains a per-character layout of slots + groups.
    - Provides paging + selection and supports grouping operations.

    NOTE: No pygame dependency, no drawing, no input handling.
    """

    abilities: List[Ability] = field(default_factory=list)
    slots: List[AbilitySlot] = field(default_factory=list)
    groups: Dict[str, AbilityGroup] = field(default_factory=dict)

    selected_index: int = 0  # index into `slots` (used by the Abilities menu)
    page: int = 0
    # How many slots are shown per page in the bottom bar.
    page_size: int = 10
    active_action: Optional[str] = None  # active group member action when grouped

    expanded_slot_index: Optional[int] = None  # expanded group popup

    # Abilities menu mode: "order" shows slots list; "group_edit" edits one group's members.
    overlay_mode: str = "order"
    group_edit_id: Optional[str] = None
    group_edit_cursor: int = 0

    _signature: Optional[Tuple] = None  # compute_abilities_signature(game)
    _layout_dirty: bool = False

    # ---- core sync ---------------------------------------------------

    def sync_from_game(self, game: "Game") -> None:
        """Ensure the ability list and layout match the current Game state."""
        sig = compute_abilities_signature(game)
        if sig != self._signature or not self.abilities:
            self._signature = sig
            self.abilities = build_abilities(game)

        actions = [ab.action for ab in self.abilities]
        actions_set = set(actions)

        layout = self._read_layout_from_character(game)
        if not self.slots and layout:
            self._load_layout(layout, actions_set)
        if not self.slots:
            self._build_default_layout(game, actions)
            self._layout_dirty = True

        self._prune_missing(actions_set)
        self._add_new_actions(actions)

        for grp in self.groups.values():
            grp.members = [a for a in grp.members if a in actions_set]
            if grp.active not in grp.members:
                grp.active = grp.members[0] if grp.members else None

        if self.active_action not in actions_set:
            self.active_action = self._first_active_action(actions_set)
            self._layout_dirty = True

        self._sync_selection_to_active(actions_set)
        self._sync_overlay_bounds()

        if self._layout_dirty:
            self._write_layout_to_character(game)
            self._layout_dirty = False

    def invalidate(self) -> None:
        """Force the next sync_from_game() to rebuild the abilities list."""
        self._signature = None

    # ---- selection / paging helpers ---------------------------------

    @property
    def total_pages(self) -> int:
        if not self.slots or self.page_size <= 0:
            return 1
        return max(1, (len(self.slots) + self.page_size - 1) // self.page_size)

    def visible_slots(self) -> List[AbilitySlotView]:
        """Slot views to display on the current page."""
        if not self.slots:
            return []

        by_action: Dict[str, Ability] = {ab.action: ab for ab in self.abilities}
        start = self.page * self.page_size
        end = start + self.page_size
        views: List[AbilitySlotView] = []

        for slot_index, slot in enumerate(self.slots[start:end], start=start):
            if slot.kind == "action" and slot.action:
                ab = by_action.get(slot.action)
                if ab is not None:
                    views.append(AbilitySlotView(slot_index=slot_index, kind="action", ability=ab))
                continue

            if slot.kind == "group" and slot.group_id:
                grp = self.groups.get(slot.group_id)
                if grp is None:
                    continue
                members = [by_action[a] for a in grp.members if a in by_action]
                if not members:
                    continue
                active = grp.active if grp.active in by_action else members[0].action
                grp.active = active
                views.append(
                    AbilitySlotView(
                        slot_index=slot_index,
                        kind="group",
                        ability=by_action[active],
                        group_id=grp.id,
                        group_label=grp.label,
                        group_members=members,
                    )
                )

        return views

    def visible_abilities(self) -> List[Ability]:
        """Compatibility helper used by existing input code."""
        return [v.ability for v in self.visible_slots()]

    def action_at_index(self, index: int) -> Optional[str]:
        """Return the active action for the slot at index (or None)."""
        if not (0 <= index < len(self.slots)):
            return None
        slot = self.slots[index]
        if slot.kind == "action":
            return slot.action
        if slot.kind == "group" and slot.group_id:
            grp = self.groups.get(slot.group_id)
            return grp.active if grp else None
        return None

    def slot_index_for_action(self, action: str) -> Optional[int]:
        """Return the slot index that contains this action (directly or in a group)."""
        if not action:
            return None
        for i, slot in enumerate(self.slots):
            if slot.kind == "action" and slot.action == action:
                return i
            if slot.kind == "group" and slot.group_id:
                grp = self.groups.get(slot.group_id)
                if grp and action in grp.members:
                    return i
        return None

    # ---- activation / navigation API --------------------------------

    def set_active(self, action: str) -> None:
        """Mark an action as active (updates group active if grouped)."""
        idx = self.slot_index_for_action(action)
        if idx is None:
            return
        slot = self.slots[idx]
        if slot.kind == "group" and slot.group_id:
            grp = self.groups.get(slot.group_id)
            if grp and action in grp.members:
                grp.active = action
                self._layout_dirty = True
        self.active_action = action
        self.selected_index = idx
        self._sync_page_from_selection()
        self.expanded_slot_index = None

    def toggle_group_expanded(self, slot_index: int) -> None:
        """Toggle the expanded popup for a group slot."""
        if not (0 <= slot_index < len(self.slots)):
            self.expanded_slot_index = None
            return
        slot = self.slots[slot_index]
        if slot.kind != "group":
            self.expanded_slot_index = None
            return
        self.expanded_slot_index = None if self.expanded_slot_index == slot_index else slot_index

    def collapse_expanded(self) -> None:
        self.expanded_slot_index = None

    def move_selection(self, delta: int) -> None:
        """Move the Abilities menu cursor up/down in the slot list."""
        if not self.slots or not delta:
            return
        self.selected_index = max(0, min(len(self.slots) - 1, self.selected_index + delta))
        self._sync_page_from_selection()

    def move_selected_item(self, dx: int) -> None:
        """Swap the selected slot with its neighbor in the slot list."""
        if not self.slots or dx == 0:
            return
        new_idx = self.selected_index + (1 if dx > 0 else -1)
        if not (0 <= new_idx < len(self.slots)):
            return
        self.slots[self.selected_index], self.slots[new_idx] = self.slots[new_idx], self.slots[self.selected_index]
        self.selected_index = new_idx
        self._layout_dirty = True
        self._sync_page_from_selection()

    def prev_page(self) -> None:
        total = self.total_pages
        if total <= 1:
            return
        self.page = (self.page - 1) % total
        self.expanded_slot_index = None

    def next_page(self) -> None:
        total = self.total_pages
        if total <= 1:
            return
        self.page = (self.page + 1) % total
        self.expanded_slot_index = None

    # ---- internal helpers -------------------------------------------

    def _build_default_layout(self, game: "Game", actions: List[str]) -> None:
        """Create the default grouped layout from the current abilities list."""
        self.groups = {}
        self.slots = []
        self.overlay_mode = "order"
        self.group_edit_id = None
        self.group_edit_cursor = 0

        actions_set = set(actions)

        default_groups: Dict[str, Dict[str, Any]] = {
            "generators": {
                "label": "Generators",
                "members": [a for a in actions if a in {"koch", "zigzag", "branch"} or a.startswith("custom")],
                "active": getattr(getattr(game, "character", None), "generator", None),
            },
            "activators": {
                "label": "Activators",
                "members": [a for a in actions if a in {"activate_all", "activate_seed"}],
                "active": "activate_all"
                if getattr(getattr(game, "character", None), "illuminator", "radius") == "radius"
                else "activate_seed",
            },
            "coloring": {
                "label": "Coloring",
                "members": [a for a in actions if a in {"rainbow_edges", "verdant_edges", "winter_hue"}],
                "active": "rainbow_edges",
            },
            "color_activators": {
                "label": "Color Activators",
                "members": [a for a in actions if a in {"freeze", "ignite", "regrow"}],
                "active": "freeze",
            },
        }

        for gid, spec in default_groups.items():
            members = [a for a in spec["members"] if a in actions_set]
            if not members:
                continue
            active = spec.get("active")
            if active not in members:
                active = members[0]
            self.groups[gid] = AbilityGroup(id=gid, label=spec["label"], members=members, active=active)

        def group_for_action(action: str) -> Optional[str]:
            if (action in {"koch", "zigzag", "branch"} or action.startswith("custom")) and "generators" in self.groups:
                return "generators"
            if action in {"activate_all", "activate_seed"} and "activators" in self.groups:
                return "activators"
            if action in {"rainbow_edges", "verdant_edges", "winter_hue"} and "coloring" in self.groups:
                return "coloring"
            if action in {"freeze", "ignite", "regrow"} and "color_activators" in self.groups:
                return "color_activators"
            return None

        added_groups: set[str] = set()
        for action in actions:
            gid = group_for_action(action)
            if gid:
                if gid not in added_groups:
                    self.slots.append(AbilitySlot(kind="group", group_id=gid))
                    added_groups.add(gid)
                continue
            self.slots.append(AbilitySlot(kind="action", action=action))

        self.active_action = self._first_active_action(actions_set)
        self._sync_selection_to_active(actions_set)

    def _prune_missing(self, actions_set: set[str]) -> None:
        new_slots: List[AbilitySlot] = []
        for slot in self.slots:
            if slot.kind == "action":
                if slot.action and slot.action in actions_set:
                    new_slots.append(slot)
                else:
                    self._layout_dirty = True
                continue
            if slot.kind == "group":
                gid = slot.group_id
                grp = self.groups.get(gid) if gid else None
                if not grp:
                    self._layout_dirty = True
                    continue
                grp.members = [a for a in grp.members if a in actions_set]
                if not grp.members:
                    self.groups.pop(gid, None)
                    self._layout_dirty = True
                    continue
                new_slots.append(slot)
                continue
        self.slots = new_slots

        referenced = {s.group_id for s in self.slots if s.kind == "group" and s.group_id}
        for gid in list(self.groups.keys()):
            if gid not in referenced:
                self.groups.pop(gid, None)
                self._layout_dirty = True

    def _add_new_actions(self, actions: List[str]) -> None:
        present: set[str] = set()
        for slot in self.slots:
            if slot.kind == "action" and slot.action:
                present.add(slot.action)
            elif slot.kind == "group" and slot.group_id:
                grp = self.groups.get(slot.group_id)
                if grp:
                    present.update(grp.members)

        for action in actions:
            if action in present:
                continue
            if (action in {"koch", "zigzag", "branch"} or action.startswith("custom")) and "generators" in self.groups:
                grp = self.groups["generators"]
                grp.members.append(action)
                if grp.active is None:
                    grp.active = action
                self._layout_dirty = True
                continue
            self.slots.append(AbilitySlot(kind="action", action=action))
            self._layout_dirty = True

    def _first_active_action(self, actions_set: set[str]) -> Optional[str]:
        if not self.slots:
            return None
        slot = self.slots[0]
        if slot.kind == "action" and slot.action in actions_set:
            return slot.action
        if slot.kind == "group" and slot.group_id:
            grp = self.groups.get(slot.group_id)
            if grp and grp.active in actions_set:
                return grp.active
            if grp and grp.members:
                return grp.members[0]
        return None

    def _sync_selection_to_active(self, actions_set: set[str]) -> None:
        if self.active_action:
            idx = self.slot_index_for_action(self.active_action)
            if idx is not None:
                self.selected_index = idx
                self._sync_page_from_selection()
                return
        self.selected_index = max(0, min(self.selected_index, max(0, len(self.slots) - 1)))
        act = self.action_at_index(self.selected_index)
        if act in actions_set:
            self.active_action = act
        self._sync_page_from_selection()

    def _sync_page_from_selection(self) -> None:
        if self.page_size > 0 and self.slots:
            self.page = self.selected_index // self.page_size
        else:
            self.page = 0

    def _sync_overlay_bounds(self) -> None:
        if not self.slots:
            self.selected_index = 0
            self.page = 0
            self.overlay_mode = "order"
            self.group_edit_id = None
            self.group_edit_cursor = 0
            self.expanded_slot_index = None
            return
        self.selected_index = max(0, min(len(self.slots) - 1, self.selected_index))
        self.page = max(0, min(self.total_pages - 1, self.page))
        if self.overlay_mode not in {"order", "group_edit"}:
            self.overlay_mode = "order"
        if self.overlay_mode == "group_edit" and self.group_edit_id not in self.groups:
            self.overlay_mode = "order"
            self.group_edit_id = None
            self.group_edit_cursor = 0

    def _read_layout_from_character(self, game: "Game") -> Optional[dict]:
        char = getattr(game, "character", None)
        layout = getattr(char, "ability_layout", None) if char is not None else None
        if not isinstance(layout, dict):
            return None
        if layout.get("version") != 1:
            return None
        return layout

    def _load_layout(self, layout: dict, actions_set: set[str]) -> None:
        self.groups = {}
        self.slots = []

        groups = layout.get("groups", {}) or {}
        if isinstance(groups, dict):
            for gid, g in groups.items():
                if not isinstance(g, dict):
                    continue
                label = str(g.get("label", gid))
                members = [str(a) for a in (g.get("members") or []) if isinstance(a, str)]
                active = g.get("active")
                active = str(active) if isinstance(active, str) else None
                members = [a for a in members if a in actions_set]
                if not members:
                    continue
                if active not in members:
                    active = members[0]
                self.groups[str(gid)] = AbilityGroup(id=str(gid), label=label, members=members, active=active)

        slots = layout.get("slots", []) or []
        if isinstance(slots, list):
            for s in slots:
                if not isinstance(s, dict):
                    continue
                kind = s.get("kind")
                if kind == "action":
                    act = s.get("action")
                    if isinstance(act, str) and act in actions_set:
                        self.slots.append(AbilitySlot(kind="action", action=act))
                elif kind == "group":
                    gid = s.get("id")
                    if isinstance(gid, str) and gid in self.groups:
                        self.slots.append(AbilitySlot(kind="group", group_id=gid))

    def _write_layout_to_character(self, game: "Game") -> None:
        char = getattr(game, "character", None)
        if char is None:
            return
        layout = {
            "version": 1,
            "slots": [
                {"kind": "action", "action": s.action}
                if s.kind == "action"
                else {"kind": "group", "id": s.group_id}
                for s in self.slots
            ],
            "groups": {
                gid: {"label": grp.label, "members": list(grp.members), "active": grp.active}
                for gid, grp in self.groups.items()
            },
        }
        try:
            char.ability_layout = layout  # type: ignore[attr-defined]
        except Exception:
            pass

    # ---- Abilities menu: grouping -----------------------------------

    def begin_group_edit_for_selected(self) -> None:
        """Enter group editing for the selected slot (creates a group if needed)."""
        if not (0 <= self.selected_index < len(self.slots)):
            return
        slot = self.slots[self.selected_index]

        if slot.kind == "group" and slot.group_id and slot.group_id in self.groups:
            self.overlay_mode = "group_edit"
            self.group_edit_id = slot.group_id
            self.group_edit_cursor = 0
            return

        if slot.kind == "action" and slot.action:
            gid = self._new_custom_group_id()
            self.groups[gid] = AbilityGroup(
                id=gid,
                label=f"Group {gid.split('_')[-1]}",
                members=[slot.action],
                active=slot.action,
            )
            self.slots[self.selected_index] = AbilitySlot(kind="group", group_id=gid)
            self.overlay_mode = "group_edit"
            self.group_edit_id = gid
            self.group_edit_cursor = 0
            self._layout_dirty = True

    def end_group_edit(self) -> None:
        self.overlay_mode = "order"
        self.group_edit_id = None
        self.group_edit_cursor = 0

    def dissolve_selected_group(self) -> None:
        """Ungroup the selected group slot into individual action slots."""
        if not (0 <= self.selected_index < len(self.slots)):
            return
        slot = self.slots[self.selected_index]
        if slot.kind != "group" or not slot.group_id:
            return
        grp = self.groups.get(slot.group_id)
        if grp is None:
            return

        members = list(grp.members)
        self.groups.pop(slot.group_id, None)
        self.slots[self.selected_index:self.selected_index + 1] = [
            AbilitySlot(kind="action", action=a) for a in members
        ]
        self.expanded_slot_index = None
        if self.group_edit_id == slot.group_id:
            self.end_group_edit()
        self._layout_dirty = True

    def group_edit_move_cursor(self, delta: int) -> None:
        actions = self._all_actions_in_order()
        if not actions:
            self.group_edit_cursor = 0
            return
        self.group_edit_cursor = max(0, min(len(actions) - 1, self.group_edit_cursor + delta))

    def group_edit_toggle_current(self) -> None:
        """Toggle the highlighted action in/out of the editing group."""
        gid = self.group_edit_id
        if not gid:
            return
        grp = self.groups.get(gid)
        if grp is None:
            return

        actions = self._all_actions_in_order()
        if not actions:
            return
        action = actions[self.group_edit_cursor]

        group_slot_idx = self._slot_index_for_group(gid)
        if group_slot_idx is None:
            group_slot_idx = self.selected_index

        if action in grp.members:
            # Prevent removing the final member.
            if len(grp.members) <= 1:
                return
            grp.members = [a for a in grp.members if a != action]
            if grp.active == action:
                grp.active = grp.members[0] if grp.members else None
            # Reinsert as an ungrouped slot directly after the group.
            if not any(s.kind == "action" and s.action == action for s in self.slots):
                self.slots.insert(group_slot_idx + 1, AbilitySlot(kind="action", action=action))
        else:
            # Remove from any other group, and remove any standalone slot.
            self._remove_action_from_other_groups(action, keep_group_id=gid)
            self.slots = [s for s in self.slots if not (s.kind == "action" and s.action == action)]
            grp.members.append(action)
            if grp.active is None:
                grp.active = action

        # Keep selection anchored on the group slot even if indices shift.
        new_group_idx = self._slot_index_for_group(gid)
        if new_group_idx is not None:
            self.selected_index = new_group_idx
            self._sync_page_from_selection()

        self._layout_dirty = True

    def group_edit_set_active(self) -> None:
        """Set the group's active member to the highlighted action (if present)."""
        gid = self.group_edit_id
        if not gid:
            return
        grp = self.groups.get(gid)
        if grp is None:
            return
        actions = self._all_actions_in_order()
        if not actions:
            return
        action = actions[self.group_edit_cursor]
        if action in grp.members:
            grp.active = action
            self.active_action = action
            self._layout_dirty = True

    def _slot_index_for_group(self, gid: str) -> Optional[int]:
        for i, slot in enumerate(self.slots):
            if slot.kind == "group" and slot.group_id == gid:
                return i
        return None

    def _remove_action_from_other_groups(self, action: str, *, keep_group_id: Optional[str] = None) -> None:
        """Ensure actions are in at most one group by removing from other groups."""
        for gid, grp in list(self.groups.items()):
            if keep_group_id and gid == keep_group_id:
                continue
            if action not in grp.members:
                continue
            grp.members = [a for a in grp.members if a != action]
            if grp.active == action:
                grp.active = grp.members[0] if grp.members else None
            if not grp.members:
                # Remove empty group and its slot.
                self.groups.pop(gid, None)
                self.slots = [s for s in self.slots if not (s.kind == "group" and s.group_id == gid)]
            self._layout_dirty = True

    def _new_custom_group_id(self) -> str:
        i = 1
        while True:
            gid = f"custom_group_{i}"
            if gid not in self.groups:
                return gid
            i += 1

    def _all_actions_in_order(self) -> List[str]:
        # Use the host action order from systems/abilities.
        return [ab.action for ab in self.abilities]


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
        # Multiple arrow hitboxes (e.g., above/below on both sides) that all map to prev/next.
        self.page_prev_rects: List[pygame.Rect] = []
        self.page_next_rects: List[pygame.Rect] = []

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

        # Spacing between ability slots in the bar.
        gap = 6
        total_gap = gap * (count - 1)
        # Use the computed width even if it's small; forcing a minimum can
        # overflow the bar when page_size grows (e.g. 10 slots).
        slot_w = max(1, (inner.w - total_gap) // max(1, count))
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
        icon_drawer: Callable[[pygame.Surface, pygame.Rect, Ability, "Game"], None] | None = None,
    ) -> None:
        """
        Render the bar into bar_rect.

        - `icon_drawer(surface, rect, ability, game)` is provided by the
          renderer (ascii.py) and may be None if you don't want icons.
        """
        # --- sync model from game -------------------------------------
        bar_state.sync_from_game(game)

        # Clear legacy hitboxes
        self.abilities_button_rect = None
        self.page_prev_rect = None
        self.page_next_rect = None
        self.page_prev_rects = []
        self.page_next_rects = []

        for ab in bar_state.abilities:
            # We deliberately only clear the attributes we own.
            for attr in ("rect", "plus_rect", "minus_rect", "gear_rect", "group_arrow_rect"):
                if hasattr(ab, attr):
                    setattr(ab, attr, None)

            # Reset per-frame hit-test maps (safe even if newly created).
            ab.sub_button_rects = {}  # type: ignore[attr-defined]
            ab.group_member_rects = {}  # type: ignore[attr-defined]
            ab.group_member_sub_rects = {}  # type: ignore[attr-defined]

            # Slot/group metadata (renderer-owned, for click routing)
            ab._bar_slot_index = None  # type: ignore[attr-defined]
            ab._bar_slot_kind = None  # type: ignore[attr-defined]
            ab._bar_group_id = None  # type: ignore[attr-defined]


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
        # Faint outline of the clickable area.
        pygame.draw.rect(surface, (80, 80, 110), self.abilities_button_rect, 1)
        # Up/down arrows stacked around the button to page abilities.
        up_surf = small_font.render("^", True, fg)
        down_surf = small_font.render("v", True, fg)
        up_rect = up_surf.get_rect(centerx=self.abilities_button_rect.centerx, bottom=label_rect.top - 2)
        down_rect = down_surf.get_rect(centerx=self.abilities_button_rect.centerx, top=label_rect.bottom + 2)
        surface.blit(up_surf, up_rect)
        surface.blit(down_surf, down_rect)
        # Map to prev/next page hooks, with slightly inflated hitboxes for easier clicking.
        self.page_prev_rects.append(up_rect.inflate(6, 6))
        self.page_next_rects.append(down_rect.inflate(6, 6))

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
            self.page_prev_rect = prev_rect.inflate(6, 6)
            self.page_prev_rects.append(self.page_prev_rect)
        else:
            self.page_prev_rect = None

        # next ">"
        if bar_state.page < bar_state.total_pages - 1:
            next_surf = small_font.render(">", True, fg)
            next_rect = next_surf.get_rect()
            next_rect.left = page_rect.right + 8
            next_rect.centery = arrow_y
            surface.blit(next_surf, next_rect)
            self.page_next_rect = next_rect.inflate(6, 6)
            self.page_next_rects.append(self.page_next_rect)
        else:
            self.page_next_rect = None

        # Extra vertical arrows on the right (above/below the page indicator) using the same hooks.
        up_rect_r = up_surf.get_rect(centerx=page_rect.centerx, bottom=page_rect.top - 2)
        down_rect_r = down_surf.get_rect(centerx=page_rect.centerx, top=page_rect.bottom + 2)
        surface.blit(up_surf, up_rect_r)
        surface.blit(down_surf, down_rect_r)
        self.page_prev_rects.append(up_rect_r.inflate(6, 6))
        self.page_next_rects.append(down_rect_r.inflate(6, 6))

        # --- visible slots --------------------------------------------
        vis_slots = bar_state.visible_slots()
        slot_rects = self._layout_bar(bar_rect, len(vis_slots))

        def blit_fit_text(
            text: str,
            *,
            centerx: int,
            bottom: int,
            max_w: int,
        ) -> None:
            """Render `text` and shrink it (if needed) so it fits max_w.

            We prefer scaling down over truncation so ability names remain readable.
            """
            if not text or max_w <= 0:
                return
            txt_surf = small_font.render(text, True, fg)
            w = txt_surf.get_width()
            if w > max_w:
                scale = max_w / max(1, w)
                new_w = max(1, int(txt_surf.get_width() * scale))
                new_h = max(1, int(txt_surf.get_height() * scale))
                # Smoothscale is plenty fast at this scale (<= ~10 labels per frame).
                txt_surf = pygame.transform.smoothscale(txt_surf, (new_w, new_h))
            txt_rect = txt_surf.get_rect()
            txt_rect.centerx = centerx
            txt_rect.bottom = bottom
            surface.blit(txt_surf, txt_rect)

        for slot_i, (slot_view, rect) in enumerate(zip(vis_slots, slot_rects), start=1):
            ability = slot_view.ability
            is_group = slot_view.kind == "group"

            # Attach slot metadata for hit-testing/click routing
            ability._bar_slot_index = slot_view.slot_index  # type: ignore[attr-defined]
            ability._bar_slot_kind = slot_view.kind  # type: ignore[attr-defined]
            ability._bar_group_id = slot_view.group_id  # type: ignore[attr-defined]

            # Attach the main rect for hit-testing
            ability.rect = rect

            # Background
            is_active = ability.action == bar_state.active_action
            bg_color = (40, 40, 60) if is_active else (25, 25, 35)
            pygame.draw.rect(surface, bg_color, rect)
            pygame.draw.rect(surface, fg, rect, 1)

            # Group expand button ("^") occupies the right-most portion of the slot.
            # Sub-buttons are laid out to the left of this region.
            arrow_rect = None
            content_rect = rect
            if is_group:
                arrow_w = max(24, int(rect.w * 0.20))
                arrow_rect = pygame.Rect(rect.right - arrow_w, rect.y, arrow_w, rect.h)
                content_rect = pygame.Rect(rect.x, rect.y, rect.w - arrow_w, rect.h)
                ability.group_arrow_rect = arrow_rect  # type: ignore[attr-defined]
                pygame.draw.rect(surface, (30, 30, 50), arrow_rect)
                pygame.draw.rect(surface, (90, 90, 120), arrow_rect, 1)
                arrow_surf = small_font.render("^", True, fg)
                surface.blit(arrow_surf, arrow_surf.get_rect(center=arrow_rect.center))

            # Label (with radius hint for activate_all)
            label = ability.name
            if ability.action == "activate_all":
                try:
                    radius = game.get_param_value("activate_all", "radius")
                    label = f"Activate R ({radius})"
                except Exception:
                    label = "Activate R"

            # Sub-buttons (from ACTION_SUB_BUTTONS metadata)
            sub_specs = ACTION_SUB_BUTTONS.get(ability.action, [])
            sub_size = 0
            if sub_specs:
                sub_size = min(rect.height - 10, 22)
                sub_size = max(14, sub_size)
            sub_gap = 4

            # Reserve a strip at the bottom for the label.
            label_h = small_font.get_height()
            label_y = rect.bottom - label_h - 2

            # Main icon uses the remaining area; allow a wide rect (not square) so
            # line-based icons can take advantage of horizontal space.
            icon_top = rect.y + 4
            icon_left = content_rect.x + 4
            icon_right = content_rect.right - 4
            if sub_specs and sub_size > 0:
                # Prefer shrinking the icon area's width so it doesn't sit under the
                # +/-/gear buttons. If there isn't enough horizontal room (notably
                # when the action is also grouped), fall back to pushing the icon
                # below the button row.
                reserved_w = len(sub_specs) * sub_size + (len(sub_specs) - 1) * sub_gap + 4
                candidate_right = content_rect.right - reserved_w - 2
                min_icon_w = max(28, int(content_rect.w * 0.35))
                if candidate_right - icon_left >= min_icon_w:
                    icon_right = candidate_right
                else:
                    icon_top += sub_size + sub_gap

            icon_bottom = label_y - 2
            icon_h = max(0, icon_bottom - icon_top)
            icon_w = max(0, icon_right - icon_left)
            icon_area = pygame.Rect(icon_left, icon_top, icon_w, icon_h)
            if icon_drawer is not None and icon_area.w > 0 and icon_area.h > 0:
                icon_drawer(surface, icon_area, ability, game)
            elif icon_area.w > 0 and icon_area.h > 0:
                pygame.draw.rect(surface, (90, 90, 120), icon_area, 1)

            # Page-local hotkey number in the upper-left corner (drawn after the icon so it's never obscured).
            hotkey_txt = "0" if slot_i == 10 else str(slot_i)
            num_surf = small_font.render(hotkey_txt, True, fg)
            surface.blit(num_surf, (content_rect.x + 4, content_rect.y + 2))

            # Name along the bottom (shrink-to-fit rather than truncating).
            label_max_w = max(0, content_rect.w - 8)
            blit_fit_text(
                label,
                centerx=content_rect.centerx,
                bottom=rect.bottom - 2,
                max_w=label_max_w,
            )

            if sub_specs:
                cur_x = (arrow_rect.left if arrow_rect is not None else rect.right) - 4

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

            # Expanded group popup (stack members vertically above the slot)
            if is_group and bar_state.expanded_slot_index == slot_view.slot_index:
                popup_member_rects: Dict[str, pygame.Rect] = {}
                popup_member_sub_rects: Dict[str, Dict[str, pygame.Rect]] = {}
                popup_h = rect.h
                # Display in the group's member order; first item sits directly above the slot.
                for i, member in enumerate(slot_view.group_members):
                    y = rect.y - (i + 1) * popup_h
                    if y + popup_h < 0:
                        break
                    m_rect = pygame.Rect(rect.x, y, rect.w, popup_h)
                    popup_member_rects[member.action] = m_rect

                    m_active = member.action == ability.action
                    m_bg = (45, 35, 55) if m_active else (20, 20, 30)
                    pygame.draw.rect(surface, m_bg, m_rect)
                    pygame.draw.rect(surface, fg, m_rect, 1)

                    m_label = member.name
                    if member.action == "activate_all":
                        try:
                            radius = game.get_param_value("activate_all", "radius")
                            m_label = f"Activate R ({radius})"
                        except Exception:
                            m_label = "Activate R"

                    # Popup: icon centered, name along bottom, sub-buttons at the top-right.
                    m_label_h = small_font.get_height()
                    m_label_y = m_rect.bottom - m_label_h - 2
                    blit_fit_text(
                        m_label,
                        centerx=m_rect.centerx,
                        bottom=m_rect.bottom - 2,
                        max_w=max(0, m_rect.w - 8),
                    )

                    m_sub_specs = ACTION_SUB_BUTTONS.get(member.action, [])
                    m_sub_size = 0
                    if m_sub_specs:
                        m_sub_size = min(m_rect.height - 10, 22)
                        m_sub_size = max(14, m_sub_size)
                    m_sub_gap = 4

                    m_inner_top = m_rect.y + 4
                    m_inner_bottom = m_label_y - 2
                    m_inner_h = max(0, m_inner_bottom - m_inner_top)
                    m_inner_w = max(0, m_rect.w - 8)
                    m_icon_left = m_rect.x + 4
                    m_icon_right = m_rect.right - 4

                    if m_sub_specs and m_sub_size > 0:
                        reserved_w = len(m_sub_specs) * m_sub_size + (len(m_sub_specs) - 1) * m_sub_gap + 4
                        candidate_right = m_rect.right - reserved_w - 2
                        min_icon_w = max(28, int(m_rect.w * 0.35))
                        if candidate_right - m_icon_left >= min_icon_w:
                            m_icon_right = candidate_right
                        else:
                            m_inner_top += m_sub_size + m_sub_gap
                            m_inner_h = max(0, m_inner_bottom - m_inner_top)

                    m_icon = pygame.Rect(m_icon_left, m_inner_top, max(0, m_icon_right - m_icon_left), m_inner_h)
                    if icon_drawer is not None and m_icon.w > 0 and m_icon.h > 0:
                        icon_drawer(surface, m_icon, member, game)
                    elif m_icon.w > 0 and m_icon.h > 0:
                        pygame.draw.rect(surface, (90, 90, 120), m_icon, 1)

                    if m_sub_specs:
                        cur_x = m_rect.right - 4
                        sub_map: Dict[str, pygame.Rect] = {}
                        for spec in reversed(m_sub_specs):
                            cur_x -= m_sub_size
                            sub_rect = pygame.Rect(cur_x, m_rect.y + 4, m_sub_size, m_sub_size)
                            pygame.draw.rect(surface, (35, 35, 65), sub_rect)
                            pygame.draw.rect(surface, (150, 150, 200), sub_rect, 1)
                            icon_txt = getattr(spec, "icon", getattr(spec, "glyph", "")) or ""
                            if icon_txt:
                                icon_surf = small_font.render(icon_txt, True, fg)
                                surface.blit(icon_surf, icon_surf.get_rect(center=sub_rect.center))
                            sub_map[spec.id] = sub_rect
                            cur_x -= m_sub_gap
                        popup_member_sub_rects[member.action] = sub_map

                ability.group_member_rects = popup_member_rects  # type: ignore[attr-defined]
                ability.group_member_sub_rects = popup_member_sub_rects  # type: ignore[attr-defined]

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

        # Title + instructions
        title = "Abilities"
        if bar_state.overlay_mode == "group_edit" and bar_state.group_edit_id in bar_state.groups:
            title = f"Edit group: {bar_state.groups[bar_state.group_edit_id].label}"
        title_surf = small_font.render(title, True, (255, 255, 210))
        surface.blit(title_surf, (panel.x + 10, panel.y + 8))

        if bar_state.overlay_mode == "group_edit":
            instructions = "Up/Down select, Space toggle, A set active, Enter/Esc back"
        else:
            instructions = "Up/Down select, Left/Right move, G group/edit, U ungroup, Enter close, Esc close"
        instr_surf = small_font.render(instructions, True, (180, 180, 210))
        surface.blit(instr_surf, (panel.x + 10, panel.bottom - instr_surf.get_height() - 8))

        # List area
        list_top = panel.y + 8 + title_surf.get_height() + 8
        line_h = small_font.get_height() + 4
        max_rows = max(1, (panel.bottom - 8 - instr_surf.get_height() - 8 - list_top) // line_h)

        abilities_by_action: Dict[str, Ability] = {ab.action: ab for ab in bar_state.abilities}

        if bar_state.overlay_mode == "group_edit" and bar_state.group_edit_id in bar_state.groups:
            gid = bar_state.group_edit_id
            grp = bar_state.groups[gid]

            # Precompute where actions currently live (for helpful hints).
            action_to_group: Dict[str, str] = {}
            for ogid, og in bar_state.groups.items():
                for a in og.members:
                    action_to_group[a] = ogid

            actions = [ab.action for ab in bar_state.abilities]
            cursor = bar_state.group_edit_cursor
            start_idx = max(0, cursor - max_rows + 1) if cursor >= max_rows else 0

            for row, idx in enumerate(range(start_idx, min(len(actions), start_idx + max_rows))):
                action_name = actions[idx]
                ab = abilities_by_action.get(action_name)
                name = ab.name if ab else action_name

                in_group = action_name in grp.members
                is_active = (action_name == grp.active)
                prefix = "[x]" if in_group else "[ ]"

                hint = ""
                other_gid = action_to_group.get(action_name)
                if other_gid and other_gid != gid and not in_group:
                    hint = f" (in {bar_state.groups.get(other_gid, AbilityGroup(other_gid, other_gid)).label})"

                suffix = " (active)" if is_active else ""
                label = f"{idx + 1}. {prefix} {name}{suffix}{hint}"

                is_selected = (idx == cursor)
                col = (255, 225, 160) if is_selected else (210, 210, 230)
                text = small_font.render(label, True, col)
                y = list_top + row * line_h
                x = panel.x + 24
                surface.blit(text, (x, y))

                if is_selected:
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

            return

        # Slot ordering mode (groups + single actions)
        slots = bar_state.slots
        cursor = bar_state.selected_index
        start_idx = max(0, cursor - max_rows + 1) if cursor >= max_rows else 0

        for row, idx in enumerate(range(start_idx, min(len(slots), start_idx + max_rows))):
            slot = slots[idx]
            label_txt = ""

            if slot.kind == "action":
                action_name = slot.action or ""
                ab = abilities_by_action.get(action_name)
                label_txt = ab.name if ab else action_name
            elif slot.kind == "group":
                grp = bar_state.groups.get(slot.group_id or "")
                if grp is None:
                    label_txt = "[group]"
                else:
                    active_action = grp.active or (grp.members[0] if grp.members else "")
                    ab = abilities_by_action.get(active_action)
                    active_name = ab.name if ab else active_action
                    label_txt = f"[{grp.label}] {active_name} ({len(grp.members)})"

            label = f"{idx + 1}. {label_txt}"

            is_selected = (idx == cursor)
            col = (255, 225, 160) if is_selected else (210, 210, 230)
            text = small_font.render(label, True, col)
            y = list_top + row * line_h
            x = panel.x + 24
            surface.blit(text, (x, y))

            if is_selected:
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


# ---------------------------------------------------------------------
# AbilityBarWidget (pygame-facing controller / hit-testing)
# ---------------------------------------------------------------------

from dataclasses import dataclass
from typing import Optional

@dataclass
class AbilityBarHit:
    """Result of clicking on the ability bar.

    kind:
      - "ability": clicked an ability slot (main body)
      - "sub_button": clicked a sub-button (plus/minus/gear/etc.)
      - "group_arrow": clicked the "^" expand region on a grouped slot
      - "group_pick": clicked a member in an expanded group popup
      - "page_prev" / "page_next": clicked any of the paging arrows
      - "open_reorder": clicked the left "Abilities" label/button
    """
    kind: str
    ability: Optional[Ability] = None
    sub_meta: Optional[SubButtonMeta] = None
    group_action: Optional[str] = None


class AbilityBarWidget:
    """Thin widget wrapper around AbilityBarRenderer.

    - Owns the pygame-facing view object (AbilityBarRenderer).
    - Uses Game.ability_bar_state as the model/controller.
    - Provides click() hit-testing used by DungeonScene.
    """

    def __init__(self) -> None:
        self.rect: pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self.visible: bool = True
        self.enabled: bool = True
        self._renderer = AbilityBarRenderer()

    def layout(self, _ctx) -> None:
        # Layout is driven entirely by self.rect; nothing else to compute.
        return

    def draw(self, ctx) -> None:
        if not self.visible:
            return

        game = ctx.game
        if game is None:
            return

        # Ensure the game has a model/controller for abilities.
        bar_state: AbilityBarState = getattr(game, "ability_bar_state", None)
        if bar_state is None:
            bar_state = AbilityBarState()
            game.ability_bar_state = bar_state

        self._renderer.draw(
            surface=ctx.surface,
            game=game,
            bar_state=bar_state,
            bar_rect=self.rect,
            small_font=getattr(ctx.renderer, "small_font", getattr(ctx.renderer, "font")),
            fg=getattr(ctx.renderer, "fg", (255, 255, 255)),
            width=getattr(ctx.renderer, "width", self.rect.w),
            icon_drawer=getattr(ctx.renderer, "_draw_ability_icon_for_bar", None),
        )

    def hover_action(self, pos: tuple[int, int], ctx) -> Optional[str]:
        """Return the hovered action name for a point, without mutating UI state."""
        if not (self.visible and self.enabled):
            return None

        game = ctx.game
        if game is None:
            return None

        bar_state: AbilityBarState = getattr(game, "ability_bar_state", None)
        if bar_state is None:
            bar_state = AbilityBarState()
            game.ability_bar_state = bar_state

        x, y = pos

        # Expanded group popup members take priority.
        for slot_view in bar_state.visible_slots():
            ability = slot_view.ability
            member_rects = getattr(ability, "group_member_rects", None) or {}
            for action, rect in member_rects.items():
                if rect and rect.collidepoint((x, y)):
                    return str(action)

        # Regular slot area: return the active action for that slot.
        for slot_view in bar_state.visible_slots():
            ability = slot_view.ability
            rect = getattr(ability, "rect", None)
            if rect is not None and rect.collidepoint((x, y)):
                return str(getattr(ability, "action", "") or "")

        return None

    def click(self, pos: tuple[int, int], ctx) -> Optional[AbilityBarHit]:
        """Hit-test a click in logical-surface coordinates."""
        if not (self.visible and self.enabled):
            return None

        game = ctx.game
        if game is None:
            return None

        bar_state: AbilityBarState = getattr(game, "ability_bar_state", None)
        if bar_state is None:
            bar_state = AbilityBarState()
            game.ability_bar_state = bar_state

        x, y = pos

        # "Abilities" label/button -> open reorder overlay
        if (
            self._renderer.abilities_button_rect
            and self._renderer.abilities_button_rect.collidepoint((x, y))
        ):
            return AbilityBarHit(kind="open_reorder")

        # Paging arrows
        for r in getattr(self._renderer, "page_prev_rects", []) or []:
            if r.collidepoint((x, y)):
                return AbilityBarHit(kind="page_prev")
        for r in getattr(self._renderer, "page_next_rects", []) or []:
            if r.collidepoint((x, y)):
                return AbilityBarHit(kind="page_next")

        # Expanded group popup takes precedence (it can overlap the dungeon view).
        for slot_view in bar_state.visible_slots():
            ability = slot_view.ability
            member_rects = getattr(ability, "group_member_rects", None) or {}
            member_sub_rects = getattr(ability, "group_member_sub_rects", None) or {}
            if member_sub_rects:
                for action, sub_map in member_sub_rects.items():
                    for sub_id, sub_rect in sub_map.items():
                        if sub_rect and sub_rect.collidepoint((x, y)):
                            member_ability = None
                            for m in slot_view.group_members:
                                if m.action == action:
                                    member_ability = m
                                    break
                            meta = None
                            for spec in ACTION_SUB_BUTTONS.get(action, []):
                                if getattr(spec, "id", None) == sub_id:
                                    meta = spec
                                    break
                            return AbilityBarHit(
                                kind="sub_button",
                                ability=member_ability or ability,
                                sub_meta=meta,
                            )
            for action, rect in member_rects.items():
                if rect and rect.collidepoint((x, y)):
                    return AbilityBarHit(kind="group_pick", ability=ability, group_action=action)

        # Ability slots + sub-buttons + group arrow
        for slot_view in bar_state.visible_slots():
            ability = slot_view.ability
            rect = getattr(ability, "rect", None)
            if rect is None or not rect.collidepoint((x, y)):
                continue

            arrow_rect = getattr(ability, "group_arrow_rect", None)
            if arrow_rect is not None and arrow_rect.collidepoint((x, y)):
                return AbilityBarHit(kind="group_arrow", ability=ability)

            mapping = getattr(ability, "sub_button_rects", None) or {}
            for sub_id, sub_rect in mapping.items():
                if sub_rect and sub_rect.collidepoint((x, y)):
                    meta = None
                    for spec in ACTION_SUB_BUTTONS.get(ability.action, []):
                        if getattr(spec, "id", None) == sub_id:
                            meta = spec
                            break
                    return AbilityBarHit(kind="sub_button", ability=ability, sub_meta=meta)

            return AbilityBarHit(kind="ability", ability=ability)

        # If a group popup is open, clicking elsewhere collapses it.
        if bar_state.expanded_slot_index is not None:
            bar_state.collapse_expanded()

        return None
