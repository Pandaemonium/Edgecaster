"""Quest system data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Tuple, Optional


@dataclass
class QuestObjective:
    """A single objective within a quest or substep."""
    id: str
    description: str
    completed: bool = False
    progress: int = 0
    target: int | None = None
    optional: bool = False

    # Objective type and parameters
    obj_type: str = "custom"  # dialogue, collect_item, kill_enemy, visit_location, pattern_placement, custom
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QuestSubstep:
    """A substep within a quest - acts as a mini-quest with its own objectives."""
    id: str
    name: str
    description: str
    completed: bool = False
    objectives: List[QuestObjective] = field(default_factory=list)

    # Context
    dialogue_snippet: str | None = None  # Key dialogue that triggered this substep
    notes: List[str] = field(default_factory=list)


@dataclass
class Quest:
    """A quest instance with progress tracking."""
    id: str                           # Unique runtime quest ID
    proto_id: str                     # Links to YAML prototype
    name: str
    description: str
    status: Literal["active", "completed", "failed"] = "active"

    # Progress tracking
    objectives: List[QuestObjective] = field(default_factory=list)
    substeps: List[QuestSubstep] = field(default_factory=list)
    stage: int = 0                    # Current stage/phase

    # Context/flavor
    dialogue_history: List[str] = field(default_factory=list)  # Key dialogue snippets
    notes: List[str] = field(default_factory=list)             # Player or auto-generated notes

    # World state
    known_locations: List[Tuple[int, int]] = field(default_factory=list)  # Map markers
    related_npcs: List[str] = field(default_factory=list)      # NPC IDs involved

    # Metadata
    acquired_turn: int = 0            # When quest was accepted
    tags: Dict[str, Any] = field(default_factory=dict)         # Flexible metadata

    def all_objectives_complete(self) -> bool:
        """Check if all non-optional main objectives are complete."""
        return all(
            obj.completed or obj.optional
            for obj in self.objectives
        )

    def all_substeps_complete(self) -> bool:
        """Check if all substeps are complete."""
        return all(substep.completed for substep in self.substeps)

    def current_substep(self) -> QuestSubstep | None:
        """Get the first incomplete substep, if any."""
        for substep in self.substeps:
            if not substep.completed:
                return substep
        return None

    def get_active_objectives(self) -> List[QuestObjective]:
        """Get all incomplete objectives from main quest and current substep."""
        active = [obj for obj in self.objectives if not obj.completed]

        current = self.current_substep()
        if current:
            active.extend([obj for obj in current.objectives if not obj.completed])

        return active
