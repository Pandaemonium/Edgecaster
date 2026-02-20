"""Gods menu scene — view gods, favor, and abilities."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Any, List

import pygame

from .base import PopupMenuScene

if TYPE_CHECKING:
    from edgecaster.game import Game
    from .manager import SceneManager


class GodsScene(PopupMenuScene):
    """Display known gods and current favor with each."""

    FOOTER_TEXT = "Up/Down: Navigate  Enter: View Details  Esc: Close"
    MAX_BODY_WIDTH: int = 700
    MAX_LIST_WIDTH: int = 700

    def __init__(self, game: "Game"):
        self.game = game
        self._god_ids: List[str] = []
        super().__init__(scale=0.80, dim_background=True)

    def get_menu_items(self) -> list[Any]:
        """Build list of gods with favor indicators."""
        from edgecaster.systems.gods import get_god_registry, get_favor, get_favor_tier, get_invoked_god

        registry = get_god_registry()
        if not registry:
            return ["No gods are known"]

        invoked_id = get_invoked_god(self.game)
        items = []
        self._god_ids = []

        for god_id, god in sorted(registry.items(), key=lambda kv: kv[1].name):
            favor = get_favor(self.game, god_id)
            tier = get_favor_tier(self.game, god_id)

            # Build display line
            tier_str = f" [{tier}]" if tier else ""
            favor_str = f" ({int(favor)} favor)" if favor > 0 else ""
            invoked_str = " *INVOKED*" if god_id == invoked_id else ""
            sig_str = ", ".join(sorted(god.chakra_signature))

            name = f"{god.glyph} {god.name}{tier_str}{favor_str}{invoked_str}"
            items.append(name)
            self._god_ids.append(god_id)

        return items

    def get_ascii_art(self) -> Optional[str]:
        return "The Gods"

    def get_body_text(self) -> Optional[str]:
        from edgecaster.systems.gods import get_invoked_god

        invoked = get_invoked_god(self.game)
        if invoked:
            from edgecaster.systems.gods import get_god
            god = get_god(invoked)
            name = god.name if god else invoked
            return f"Currently invoking: {name}\n"
        return "No god is currently invoked.\n"

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        if 0 <= index < len(self._god_ids):
            god_id = self._god_ids[index]
            manager.push_scene(GodDetailScene(self.game, god_id))
            return False  # Don't close this menu
        return False

    def on_back(self, manager: "SceneManager") -> bool:
        manager.pop_scene()
        return True


class GodDetailScene(PopupMenuScene):
    """Display detailed information about a specific god."""

    FOOTER_TEXT = "Press Esc to return"
    MAX_BODY_WIDTH: int = 700
    MAX_LIST_WIDTH: int = 700

    def __init__(self, game: "Game", god_id: str):
        self.game = game
        self.god_id = god_id
        super().__init__(scale=0.85, dim_background=True)


    def get_menu_items(self) -> list[Any]:
        return ["Close"]

    def get_ascii_art(self) -> Optional[str]:
        from edgecaster.systems.gods import get_god
        god = get_god(self.god_id)
        if god:
            return f"{god.glyph}  {god.name}"
        return "Unknown God"

    def get_body_text(self) -> Optional[str]:
        from edgecaster.systems.gods import (
            get_god, get_favor, get_favor_tier, get_invoked_god,
            available_abilities, GodFavorState, _ensure_favor,
        )

        god = get_god(self.god_id)
        if god is None:
            return "God not found."

        favor = get_favor(self.game, self.god_id)
        tier = get_favor_tier(self.game, self.god_id)
        invoked = get_invoked_god(self.game) == self.god_id
        state = _ensure_favor(self.game, self.god_id)

        lines: list[str] = []

        # Description
        if god.description:
            lines.append(god.description)
            lines.append("")

        # Domain
        lines.append(f"Domain: {god.domain.capitalize()}")
        if god.rival_domains:
            rivals = ", ".join(d.capitalize() for d in god.rival_domains)
            lines.append(f"Rival Domains: {rivals}")
        lines.append("")

        # Chakra pattern
        lines.append("Chakra Pattern:")
        sig_nodes = sorted(god.chakra_signature)
        sig_labels = [n.split(".")[-1].replace("_", " ").title() for n in sig_nodes]
        lines.append(f"  {' -> '.join(sig_labels)}")
        lines.append("")

        # Current favor
        lines.append(f"Current Favor: {int(favor)}")
        if tier:
            lines.append(f"Standing: {tier.capitalize()}")
        else:
            lines.append("Standing: Unknown")
        if invoked:
            lines.append("Status: INVOKED")
        lines.append(f"Peak Favor: {int(state.peak_favor)}")
        lines.append(f"Total Invocations: {state.total_invocations}")
        lines.append(f"Decay Rate: {god.favor_decay_rate} per 100 heartbeats")
        lines.append("")

        # Favor thresholds
        lines.append("Favor Thresholds:")
        for tier_name, threshold in sorted(god.favor_thresholds.items(), key=lambda t: t[1]):
            marker = " <--" if favor >= threshold else ""
            lines.append(f"  {tier_name.capitalize():>10}: {threshold}{marker}")
        lines.append("")

        # Favor triggers
        if god.favor_triggers:
            lines.append("Favor Triggers:")
            trigger_labels = {
                "kill_hostile": "Kill hostile enemy",
                "kill_any": "Kill any creature",
                "invoke": "Invoke this god",
                "take_damage": "Take damage",
                "explore_new_tile": "Explore new territory",
            }
            for trigger, amount in god.favor_triggers.items():
                label = trigger_labels.get(trigger, trigger.replace("_", " ").capitalize())
                lines.append(f"  {label}: +{amount} favor")
            lines.append("")

        # Abilities
        lines.append("Abilities:")
        known_abilities = available_abilities(self.game, self.god_id)
        known_ids = {a["id"] for a in known_abilities}

        for i, ability in enumerate(god.abilities):
            aid = ability.get("id", "")
            name = ability.get("name", "???")
            desc = ability.get("description", "")
            min_fav = ability.get("min_favor", 0)

            if i == 0 or aid in known_ids:
                # First ability is always shown; others only if unlocked
                status = "UNLOCKED" if aid in known_ids else f"Requires {min_fav} favor"
                lines.append(f"  {name} ({status})")
                lines.append(f"    {desc}")
            else:
                # Hidden until discovered
                lines.append(f"  ??? (Requires {min_fav} favor)")
                lines.append(f"    Reach higher favor to learn more.")
            lines.append("")

        return "\n".join(lines)

    def on_activate(self, index: int, manager: "SceneManager") -> bool:
        return True  # Close on Enter

    def on_back(self, manager: "SceneManager") -> bool:
        manager.pop_scene()
        return True
