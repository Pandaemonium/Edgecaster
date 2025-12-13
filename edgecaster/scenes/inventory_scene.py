from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import pygame

from .base import PopupMenuScene
from .urgent_message_scene import UrgentMessageScene

from edgecaster.visual_effects import effect_names_from_obj, concat_effect_names

if TYPE_CHECKING:
    from .manager import SceneManager  # only for type hints


class InventoryScene(PopupMenuScene):
    """
    Inventory popup using standardized MenuScene / MenuInput controls.
    Generalized to browse any entity's inventory.

    DESIGN NOTE (new system):
    - InventoryScene no longer interprets tags like 'clockwise_inventory' into
      direct VisualProfile transforms.
    - Instead, it carries a list of effect NAMES (self.visual_effects).
    - Renderer / base scene code is responsible for turning effect names into
      VisualProfiles + styling via visual_effects.py.
    """

    FOOTER_TEXT = (
        "↑/↓ Numpad or W/S to move, Enter/Space to select, "
        "Esc or i to go back, F11 to toggle fullscreen"
    )

    def __init__(
        self,
        game,
        *,
        owner_id: Optional[str] = None,
        window_rect: Optional[pygame.Rect] = None,
        parent_owner_id: Optional[str] = None,
        title: Optional[str] = None,
        base_effects: Optional[list[str]] = None,
    ) -> None:
        super().__init__(window_rect=window_rect, dim_background=True, scale=0.7)
        self.game = game

        self.owner_id: Optional[str] = owner_id
        self.parent_owner_id: Optional[str] = parent_owner_id
        self.explicit_title: Optional[str] = title

        self.overlay_layers = {"hud"}

        # Effects: inherit from parent + add owner/container declared effects
        self.visual_effects: list[str] = list(base_effects or [])
        self._inherit_owner_visual_effects()

    # ---------------------------------------------------------------------
    # Effects inheritance (no inventory-specific math here)
    # ---------------------------------------------------------------------

    def _find_owner_entity(self):
        owner_id = self._owner_id()

        level = self.game._level()
        if level is not None:
            ent = level.entities.get(owner_id) or level.actors.get(owner_id)
            if ent is not None:
                return ent

        for cand in getattr(self.game, "player_inventory", []):
            if getattr(cand, "id", None) == owner_id:
                return cand

        for inv_list in getattr(self.game, "inventories", {}).values():
            for cand in inv_list:
                if getattr(cand, "id", None) == owner_id:
                    return cand

        return None

    def _inherit_owner_visual_effects(self) -> None:
        owner_id = self._owner_id()

        # Player inventory never gets twisted; only special containers do.
        if owner_id == self.game.player_id:
            return

        ent = self._find_owner_entity()
        if ent is None:
            return

        self.visual_effects = concat_effect_names(self.visual_effects, effect_names_from_obj(ent))

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    def _owner_id(self) -> str:
        return self.owner_id or self.game.player_id

    def _find_container_targets(self, exclude_id: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Return a list of (owner_id, label) container inventories in the *same
        inventory space* as the currently viewed items.
        """
        space_owner_id = self._owner_id()
        inv = self.game.get_inventory(space_owner_id)
        candidates: list[tuple[str, str]] = []

        for ent in inv:
            tags = getattr(ent, "tags", {}) or {}
            if not tags.get("container"):
                continue
            ent_id = getattr(ent, "id", None)
            if exclude_id is not None and ent_id == exclude_id:
                continue
            name = getattr(ent, "name", None) or "Container"
            if ent_id is not None:
                candidates.append((ent_id, name))

        return candidates

    # ---------------------------------------------------------------------
    # MenuScene hooks
    # ---------------------------------------------------------------------

    def get_menu_items(self) -> list[str]:
        """
        Return a simple list of inventory item names plus a Back option.
        (Restores stable behavior: no '[Take]' prefixes on the main list.)
        """
        items: list[str] = []
        owner_id = self._owner_id()
        inv = self.game.get_inventory(owner_id)

        if inv:
            for ent in inv:
                name = getattr(ent, "name", None) or "(unnamed item)"
                items.append(str(name))
        else:
            items.append("(Empty)")
        items.append("Back")
        return items

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        """
        Stable behavior: selecting an item opens a context submenu:
        - Player inventory: Drop / Eat / Put into...
        - Other inventories: Take / Eat / Put into...
        - If item is a container: Open
        """
        owner_id = self._owner_id()
        inv = self.game.get_inventory(owner_id)
        back_index = len(inv) if inv else 1

        # 'Back' (or the '(Empty)' row) → close inventory
        if index >= back_index:
            return self.on_back(manager)

        # No inventory, nothing to do
        if not inv or index < 0 or index >= len(inv):
            return False

        ent = inv[index]
        tags = getattr(ent, "tags", {}) or {}

        # Berry check (same as stable build)
        is_berry = bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }

        # Container check
        is_container = bool(tags.get("container"))

        choices: list[str] = []

        container_targets = self._find_container_targets(exclude_id=getattr(ent, "id", None))

        if owner_id == self.game.player_id:
            choices.append("Drop")
            if is_berry:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")
        else:
            choices.append("Take")
            if is_berry:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")

        if is_container:
            choices.append("Open")

        if not choices:
            return False

        window_rect = manager.compute_child_window_rect(scale=0.4)

        def handle_choice(choice_idx: int, mgr: "SceneManager") -> None:
            if choice_idx < 0 or choice_idx >= len(choices):
                return
            choice = choices[choice_idx]

            # Re-fetch inventory in case it changed while popup was open.
            current_owner_id = self._owner_id()
            cur_inv = self.game.get_inventory(current_owner_id)
            if index < 0 or index >= len(cur_inv):
                return

            cur_ent = cur_inv[index]
            cur_tags = getattr(cur_ent, "tags", {}) or {}
            cur_is_container = bool(cur_tags.get("container"))

            if choice == "Drop" and current_owner_id == self.game.player_id:
                if hasattr(self.game, "drop_inventory_item"):
                    self.game.drop_inventory_item(index)

            elif choice == "Eat":
                if hasattr(self.game, "eat_item_from_inventory"):
                    self.game.eat_item_from_inventory(current_owner_id, index)
                else:
                    if current_owner_id == self.game.player_id and hasattr(self.game, "eat_inventory_item"):
                        self.game.eat_inventory_item(index)

            elif choice == "Take" and current_owner_id != self.game.player_id:
                dest_owner_id = self.parent_owner_id or self.game.player_id
                if hasattr(self.game, "move_item_between_inventories"):
                    self.game.move_item_between_inventories(
                        current_owner_id,
                        index,
                        dest_owner_id,
                    )

            elif choice == "Put into...":
                targets = self._find_container_targets(exclude_id=getattr(cur_ent, "id", None))
                if not targets:
                    return

                target_labels = [label for (_oid, label) in targets]
                target_rect = mgr.compute_child_window_rect(scale=0.4)

                def on_target_choice(target_idx: int, mgr2: "SceneManager") -> None:
                    if target_idx < 0 or target_idx >= len(targets):
                        return

                    dest_owner_id, _dest_label = targets[target_idx]

                    src_owner_id = self._owner_id()
                    src_inv = self.game.get_inventory(src_owner_id)
                    if not (0 <= index < len(src_inv)):
                        return

                    if hasattr(self.game, "move_item_between_inventories"):
                        self.game.move_item_between_inventories(
                            src_owner_id,
                            index,
                            dest_owner_id,
                        )

                mgr.push_scene(
                    UrgentMessageScene(
                        self.game,
                        "",
                        title="Put into which container?",
                        choices=target_labels,
                        on_choice=on_target_choice,
                        window_rect=target_rect,
                        back_confirms=False,
                    )
                )

            elif choice == "Open" and cur_is_container:
                nested_owner_id = getattr(cur_ent, "id", None)
                if nested_owner_id is None:
                    return

                parent_rect = self.window_rect
                if parent_rect is not None:
                    child_scale = 0.80
                    w = int(parent_rect.width * child_scale)
                    h = int(parent_rect.height * child_scale)
                    x = parent_rect.x + (parent_rect.width - w) // 2
                    y = parent_rect.y + (parent_rect.height - h) // 2
                    nested_rect = pygame.Rect(x, y, w, h)
                else:
                    nested_rect = mgr.compute_child_window_rect(scale=0.7)

                popup_title = getattr(cur_ent, "name", None) or "Inventory"

                # NEW: inherit effect names, not a copied VisualProfile.
                mgr.push_scene(
                    InventoryScene(
                        self.game,
                        owner_id=nested_owner_id,
                        window_rect=nested_rect,
                        parent_owner_id=self._owner_id(),
                        title=popup_title,
                        base_effects=self.visual_effects,
                    )
                )

        manager.push_scene(
            UrgentMessageScene(
                self.game,
                "",  # no body text
                title="",
                choices=choices,
                on_choice=handle_choice,
                window_rect=window_rect,
                back_confirms=False,
            )
        )
        return True

    def on_back(self, manager: "SceneManager") -> bool:
        manager.pop_scene()
        return True

    def get_ascii_art(self) -> Optional[str]:
        """Show the name of the inventory's owner as the popup title."""
        if getattr(self, "explicit_title", None):
            return self.explicit_title

        owner_id = self._owner_id()

        if owner_id == self.game.player_id:
            return "Inventory"

        level = self.game._level()
        ent = None
        if level is not None:
            ent = level.entities.get(owner_id) or level.actors.get(owner_id)

        if ent is not None:
            return getattr(ent, "name", "Inventory")

        return "Inventory"
