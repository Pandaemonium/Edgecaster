from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import pygame

from .base import PopupMenuScene
from .urgent_message_scene import UrgentMessageScene

if TYPE_CHECKING:
    from .manager import SceneManager  # only for type hints


class InventoryScene(PopupMenuScene):
    """
    Minimal inventory popup using the standardized MenuScene / MenuInput
    controls, now generalized to browse any entity's inventory.

    - Opens as a popup over the dungeon (snapshot + dimmed background).
    - Uses numpad / arrow / WASD navigation and key-repeat.
    - Esc OR 'i' both close the inventory (handled in PopupMenuScene).
    """

    # Override footer to mention 'i' as an extra back key.
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
    ) -> None:
        # window_rect is optional; manager.open_window_scene can pass one,
        # otherwise PopupMenuScene will compute a centered popup rect.
        super().__init__(window_rect=window_rect, dim_background=True, scale=0.7)
        self.game = game
        # If owner_id is None, we default to the current host (player).
        self.owner_id: Optional[str] = owner_id
        # Parent inventory: where items "pop out" to when you Take.
        # Top-level player inventory has parent_owner_id = None.
        self.parent_owner_id: Optional[str] = parent_owner_id

        # NEW: explicit title for this inventory popup (e.g. "smoky Inventory")
        self.explicit_title: Optional[str] = title

    def _owner_id(self) -> str:
        """Which entity's inventory are we viewing?

        If no explicit owner is set, default to the current player.
        """
        return self.owner_id or self.game.player_id


    def _find_container_targets(self, exclude_id: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Return a list of (owner_id, label) container inventories in the *same
        inventory space* as the currently viewed items.

        Here, "inventory space" = the owner whose inventory we are browsing
        (self._owner_id()).
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






    # ---- MenuScene hooks -----------------------------------------------------

    def get_menu_items(self) -> list[str]:
        """Return a simple list of inventory item names plus a Back option.

        This can show any entity's inventory (player, bag, chest, etc.),
        depending on the owner_id passed to the scene.
        """
        items: list[str] = []
        owner_id = self._owner_id()
        inv = self.game.get_inventory(owner_id)

        # We don't assume any particular type beyond having a .name attribute.
        if inv:
            for ent in inv:
                name = getattr(ent, "name", None) or "(unnamed item)"
                items.append(str(name))
        else:
            items.append("(Empty)")
        items.append("Back")
        return items

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        """Handle selection in the inventory menu.

        - Selecting 'Back' closes the inventory popup.
        - Selecting a real item opens a tiny, context-aware submenu:
          * Player's own inventory: Drop / Eat / Open (if container)
          * Other inventories (bags, chests, rocks): Take / Open (if container)
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

        # Berry check (same as before)
        is_berry = bool(tags.get("test_berry")) or tags.get("item_type") in {
            "blueberry",
            "raspberry",
            "strawberry",
        }

        # Container check: any entity with container=True in tags is a container.
        is_container = bool(tags.get("container"))

        # Build a minimal context-menu list of actions.
        choices: list[str] = []

        # Potential container targets always come from the player's inventory.
        container_targets = self._find_container_targets(exclude_id=ent.id)

        if owner_id == self.game.player_id:
            # Browsing your own inventory.
            choices.append("Drop")
            if is_berry:
                choices.append("Eat")
            if container_targets:
                choices.append("Put into...")
        else:
            # Browsing some other inventory (bag, chest, rock, demon, etc.).
            # You can always Take things out.
            choices.append("Take")
            # You can also eat berries directly from here.
            if is_berry:
                choices.append("Eat")
            # And you can stash them into other bags you carry.
            if container_targets:
                choices.append("Put into...")

        if is_container:
            choices.append("Open")



        # If somehow no actions are available, just bail.
        if not choices:
            return False

        # Very small child window – no title or body text, just choices.
        window_rect = manager.compute_child_window_rect(scale=0.4)

        def handle_choice(choice_idx: int, mgr: "SceneManager") -> None:
            if choice_idx < 0 or choice_idx >= len(choices):
                return
            choice = choices[choice_idx]

            # Re-fetch inventory in case it changed while the popup was open.
            current_owner_id = self._owner_id()
            cur_inv = self.game.get_inventory(current_owner_id)
            if index < 0 or index >= len(cur_inv):
                return

            # We need the up-to-date entity in case tags changed.
            cur_ent = cur_inv[index]
            cur_tags = getattr(cur_ent, "tags", {}) or {}
            cur_is_container = bool(cur_tags.get("container"))

            if choice == "Drop" and current_owner_id == self.game.player_id:
                if hasattr(self.game, "drop_inventory_item"):
                    self.game.drop_inventory_item(index)

            elif choice == "Eat":
                # Eat from whatever inventory we're browsing; player gets healed.
                if hasattr(self.game, "eat_item_from_inventory"):
                    self.game.eat_item_from_inventory(current_owner_id, index)
                else:
                    # Fallback for older builds: only correct for player inventory.
                    if current_owner_id == self.game.player_id and hasattr(self.game, "eat_inventory_item"):
                        self.game.eat_inventory_item(index)

            elif choice == "Take" and current_owner_id != self.game.player_id:
                # Pop item out one level: from this inventory space into its parent.
                # If there is no parent (top-level non-player space), fall back to player.
                dest_owner_id = self.parent_owner_id or self.game.player_id

                if hasattr(self.game, "move_item_between_inventories"):
                    self.game.move_item_between_inventories(
                        current_owner_id,
                        index,
                        dest_owner_id,
                    )


            elif choice == "Put into...":
                # Second-level popup: pick which container to use.
                targets = self._find_container_targets(exclude_id=cur_ent.id)
                if not targets:
                    # No valid containers anymore; silently bail.
                    return

                target_labels = [label for (_oid, label) in targets]
                target_rect = mgr.compute_child_window_rect(scale=0.4)

                def on_target_choice(target_idx: int, mgr2: "SceneManager") -> None:
                    if target_idx < 0 or target_idx >= len(targets):
                        return

                    dest_owner_id, _dest_label = targets[target_idx]

                    # Re-fetch source inventory in case something changed.
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
                # Open a nested inventory for this container entity.
                nested_owner_id = cur_ent.id

                # Compute a child rect that is visibly smaller than this inventory
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

                # Title for this nested inventory popup: use the container's own name.
                popup_title = getattr(cur_ent, "name", None) or "Inventory"

                mgr.push_scene(
                    InventoryScene(
                        self.game,
                        owner_id=nested_owner_id,
                        window_rect=nested_rect,
                        # This container lives inside the *current* inventory space.
                        parent_owner_id=self._owner_id(),
                        title=popup_title,
                    )
                )





        # Push the lightweight context popup; keep the inventory itself open.
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
        # Since this is a popup over the dungeon, just pop this scene.
        manager.pop_scene()
        return True

    def get_ascii_art(self) -> Optional[str]:
        """Show the name of the inventory's owner as the popup title."""
        # If we were given an explicit title (e.g. when opening a container),
        # use that first.
        if getattr(self, "explicit_title", None):
            return self.explicit_title

        owner_id = self._owner_id()

        # Player inventory → simple standardized title
        if owner_id == self.game.player_id:
            return "Inventory"

        # Otherwise: look up the entity's name (bags, crates, demons, etc.)
        level = self.game._level()
        ent = level.entities.get(owner_id) or level.actors.get(owner_id)

        if ent is not None:
            # This will be your random-colored, adjective-laced name,
            # e.g. "lavender-scented octonionic Inventory"
            return getattr(ent, "name", "Inventory")

        # Fallback if something went weird
        return "Inventory"

