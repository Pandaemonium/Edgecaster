"""Quest menu scenes for viewing active quests and quest details."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Any
import pygame

from .base import PopupMenuScene

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.quests import Quest
    from .manager import SceneManager


class QuestScene(PopupMenuScene):
    """Display active quests in a menu."""

    FOOTER_TEXT = "↑↓: Navigate  Enter: View Details  Esc: Close"

    def __init__(self, game: Game):
        super().__init__(scale=0.7, dim_background=True)
        self.game = game

    def get_menu_items(self) -> list[Any]:
        """Build list of quest names."""
        if not self.game.active_quests:
            return ["No active quests"]

        items = []
        for quest in self.game.active_quests.values():
            # Show quest name with optional status indicator
            name = quest.name
            if quest.current_substep():
                name += " ●"  # Indicator for active substep
            items.append(name)
        return items

    def get_ascii_art(self) -> Optional[str]:
        """Return the title for the quest menu."""
        return "Active Quests"

    def on_activate(self, index: int, manager: SceneManager) -> bool:
        """Called when user selects a quest from the list."""
        if not self.game.active_quests:
            return False

        # Get quest by index
        quests = list(self.game.active_quests.values())
        if 0 <= index < len(quests):
            quest = quests[index]
            # Push detail scene
            manager.push_scene(QuestDetailScene(self.game, quest))
            return False  # Don't auto-close this menu

        return False

    def on_back(self, manager: SceneManager) -> bool:
        """Called when user presses ESC."""
        manager.pop_scene()
        return True


class QuestDetailScene(PopupMenuScene):
    """Display details for a specific quest."""

    FOOTER_TEXT = "Press Esc to return"

    def __init__(self, game: Game, quest: Quest):
        self.game = game
        self.quest = quest
        super().__init__(scale=0.85, dim_background=True)

    def get_menu_items(self) -> list[Any]:
        """Build list with a single 'Close' option."""
        return ["Close"]

    def get_ascii_art(self) -> Optional[str]:
        """Return the quest name as title."""
        return self.quest.name

    def get_body_text(self) -> Optional[str]:
        """Build the quest details as body text."""
        lines = []

        # Quest description
        if self.quest.description:
            lines.append(self.quest.description)
            lines.append("")

        # Current substep (if any)
        current_substep = self.quest.current_substep()
        if current_substep:
            lines.append(f"Current: {current_substep.name}")
            if current_substep.description:
                lines.append(f"  {current_substep.description}")
            lines.append("")

            # Substep objectives
            for obj in current_substep.objectives:
                checkbox = "☑" if obj.completed else "☐"
                obj_text = f"{checkbox} {obj.description}"
                if obj.target and obj.target > 1:
                    obj_text += f" ({obj.progress}/{obj.target})"
                lines.append(obj_text)
            lines.append("")

        # Main objectives (if any remain active)
        active_main_objectives = [obj for obj in self.quest.objectives if not obj.completed]
        if active_main_objectives:
            lines.append("Objectives:")
            for obj in self.quest.objectives:
                checkbox = "☑" if obj.completed else "☐"
                obj_text = f"{checkbox} {obj.description}"
                if obj.target and obj.target > 1:
                    obj_text += f" ({obj.progress}/{obj.target})"
                lines.append(obj_text)
            lines.append("")

        # Known locations
        if self.quest.known_locations:
            lines.append("Known Locations:")
            for loc in self.quest.known_locations:
                lines.append(f"  → ({loc[0]}, {loc[1]})")
            lines.append("")

        # Dialogue history
        if self.quest.dialogue_history:
            lines.append("Dialogue:")
            for dialogue in self.quest.dialogue_history[-3:]:  # Show last 3
                lines.append(f'  "{dialogue}"')
            lines.append("")

        # Notes
        if self.quest.notes:
            lines.append("Notes:")
            for note in self.quest.notes:
                lines.append(f"  • {note}")
            lines.append("")

        return "\n".join(lines)

    def on_activate(self, index: int, manager: SceneManager) -> bool:
        """Called when user selects 'Close'."""
        return True  # Auto-close this popup

    def on_back(self, manager: SceneManager) -> bool:
        """Called when user presses ESC."""
        manager.pop_scene()
        return True
