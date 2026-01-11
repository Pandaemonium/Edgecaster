"""Factions and Reputation scene for viewing faction standings."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Any, List

import pygame

from .base import PopupMenuScene

if TYPE_CHECKING:
    from edgecaster.game import Game
    from .manager import SceneManager


class FactionsScene(PopupMenuScene):
    """Display factions and current player reputation with each."""

    FOOTER_TEXT = "Up/Down: Navigate  Enter: View Details  Esc: Close"

    def __init__(self, game: Game):
        super().__init__(scale=0.75, dim_background=True)
        self.game = game
        self._faction_ids: List[str] = []

    def get_menu_items(self) -> list[Any]:
        """Build list of factions with reputation indicators."""
        from edgecaster.content.factions import FACTIONS
        from edgecaster.systems.reputation import player_reputation, rep_tier

        player_rep = player_reputation(self.game)

        items = []
        self._faction_ids = []

        # Skip player and neutral factions in the main list
        skip_factions = {"player", "neutral"}

        for fid, fdef in FACTIONS.items():
            if fid in skip_factions:
                continue

            rep_value = player_rep.get(fid, 0)
            tier = rep_tier(rep_value)

            # Format: "Faction Name [tier] (value)"
            tier_indicator = _tier_indicator(tier)
            name = f"{fdef.name} {tier_indicator} ({rep_value:+d})"
            items.append(name)
            self._faction_ids.append(fid)

        if not items:
            return ["No factions discovered"]

        return items

    def get_ascii_art(self) -> Optional[str]:
        """Return the title for the factions menu."""
        return "Factions & Reputation"

    def on_activate(self, index: int, manager: SceneManager) -> bool:
        """Called when user selects a faction from the list."""
        if 0 <= index < len(self._faction_ids):
            faction_id = self._faction_ids[index]
            manager.push_scene(FactionDetailScene(self.game, faction_id))
            return False  # Don't auto-close this menu
        return False

    def on_back(self, manager: SceneManager) -> bool:
        """Called when user presses ESC."""
        manager.pop_scene()
        return True


class FactionDetailScene(PopupMenuScene):
    """Display details for a specific faction."""

    FOOTER_TEXT = "Press Esc to return"

    def __init__(self, game: Game, faction_id: str):
        self.game = game
        self.faction_id = faction_id
        super().__init__(scale=0.85, dim_background=True)

    def get_menu_items(self) -> list[Any]:
        """Build list with a single 'Close' option."""
        return ["Close"]

    def get_ascii_art(self) -> Optional[str]:
        """Return the faction name as title."""
        from edgecaster.content.factions import FACTIONS

        fdef = FACTIONS.get(self.faction_id)
        if fdef:
            return fdef.name
        return "Unknown Faction"

    def get_body_text(self) -> Optional[str]:
        """Build the faction details as body text."""
        from edgecaster.content.factions import FACTIONS
        from edgecaster.systems.reputation import player_reputation, rep_tier

        fdef = FACTIONS.get(self.faction_id)
        if not fdef:
            return "Faction not found."

        player_rep = player_reputation(self.game)
        rep_value = player_rep.get(self.faction_id, 0)
        tier = rep_tier(rep_value)

        lines = []

        # Description
        if fdef.description:
            lines.append(fdef.description)
            lines.append("")

        # Current standing
        lines.append(f"Your Standing: {_tier_label(tier)} ({rep_value:+d})")
        lines.append("")

        # Hostility info
        if fdef.hostile_below >= 125:
            lines.append("This faction is hostile by default.")
        elif rep_value < fdef.hostile_below:
            lines.append("This faction is currently hostile to you.")
        else:
            lines.append("This faction is not hostile to you.")
        lines.append("")

        # Relations with other factions
        if fdef.opinions:
            lines.append("Faction Relations:")
            for other_fid, opinion in sorted(fdef.opinions.items(), key=lambda x: -x[1]):
                if other_fid == self.faction_id:
                    continue  # Skip self-opinion
                if other_fid == "player":
                    continue  # Skip player opinion (shown above)

                other_fdef = FACTIONS.get(other_fid)
                other_name = other_fdef.name if other_fdef else other_fid
                other_tier = rep_tier(opinion)
                lines.append(f"  {other_name}: {_tier_label(other_tier)}")
            lines.append("")

        return "\n".join(lines)

    def on_activate(self, index: int, manager: SceneManager) -> bool:
        """Called when user selects 'Close'."""
        return True  # Auto-close this popup

    def on_back(self, manager: SceneManager) -> bool:
        """Called when user presses ESC."""
        manager.pop_scene()
        return True


def _tier_indicator(tier: str) -> str:
    """Return a compact indicator for reputation tier."""
    indicators = {
        "hated": "[-!!-]",
        "disliked": "[-]",
        "neutral": "[~]",
        "liked": "[+]",
        "loved": "[++!]",
    }
    return indicators.get(tier, "[?]")


def _tier_label(tier: str) -> str:
    """Return a human-readable label for reputation tier."""
    labels = {
        "hated": "Hated",
        "disliked": "Disliked",
        "neutral": "Neutral",
        "liked": "Liked",
        "loved": "Loved",
    }
    return labels.get(tier, "Unknown")
