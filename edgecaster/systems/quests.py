"""Quest system logic and progression."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional
from edgecaster.state.quests import Quest, QuestObjective, QuestSubstep
from edgecaster import prototypes

if TYPE_CHECKING:
    from edgecaster.game import Game


def create_quest_from_proto(proto_id: str, acquired_turn: int = 0) -> Quest:
    """
    Create a Quest instance from a YAML prototype.
    Loads initial stage objectives.
    """
    proto = prototypes.resolve_proto(proto_id)

    quest = Quest(
        id=f"quest_{proto_id}_{acquired_turn}",  # Unique runtime ID
        proto_id=proto_id,
        name=proto.get("name", "Unnamed Quest"),
        description=proto.get("description", ""),
        acquired_turn=acquired_turn,
    )

    # Load related NPCs
    quest.related_npcs = proto.get("related_npcs", [])

    # Load initial known locations (if quest giver provides them)
    quest.known_locations = [tuple(loc) for loc in proto.get("initial_locations", [])]

    # Load substeps (if any)
    substeps_data = proto.get("substeps", [])
    for substep_data in substeps_data:
        substep = QuestSubstep(
            id=substep_data["id"],
            name=substep_data["name"],
            description=substep_data.get("description", ""),
        )

        # Load substep objectives
        for obj_data in substep_data.get("objectives", []):
            obj = _create_objective_from_data(obj_data)
            substep.objectives.append(obj)

        quest.substeps.append(substep)

    # Load initial stage objectives (stage 0)
    stages = proto.get("stages", [])
    if stages and len(stages) > 0:
        load_stage_objectives(quest, stages[0])

    return quest


def _create_objective_from_data(obj_data: dict) -> QuestObjective:
    """Create a QuestObjective from YAML data."""
    return QuestObjective(
        id=obj_data["id"],
        description=obj_data["description"],
        obj_type=obj_data.get("type", "custom"),
        target=obj_data.get("target"),
        optional=obj_data.get("optional", False),
        params=obj_data.get("params", {}),
    )


def load_stage_objectives(quest: Quest, stage_data: dict) -> None:
    """Load objectives for a specific quest stage."""
    quest.objectives.clear()

    for obj_data in stage_data.get("objectives", []):
        obj = _create_objective_from_data(obj_data)
        quest.objectives.append(obj)


def update_quest_progress(
    game: Game,
    event_type: str,
    **context: Any
) -> List[str]:
    """
    Update quest progress based on game events.
    Returns a list of messages to display to the player.
    """
    messages = []

    for quest_id, quest in list(game.active_quests.items()):
        if quest.status != "active":
            continue

        # Check main quest objectives
        for obj in quest.objectives:
            if obj.completed:
                continue

            if _matches_objective(obj, event_type, context):
                obj.progress += 1

                if obj.target is None or obj.progress >= obj.target:
                    obj.completed = True
                    messages.append(f"Quest objective completed: {obj.description}")

        # Check current substep objectives
        current_substep = quest.current_substep()
        if current_substep:
            for obj in current_substep.objectives:
                if obj.completed:
                    continue

                if _matches_objective(obj, event_type, context):
                    obj.progress += 1

                    if obj.target is None or obj.progress >= obj.target:
                        obj.completed = True
                        messages.append(f"Substep objective completed: {obj.description}")

            # Check if substep is complete
            if all(obj.completed or obj.optional for obj in current_substep.objectives):
                current_substep.completed = True
                messages.append(f"Quest substep completed: {current_substep.name}")

        # Check if quest stage is complete
        if quest.all_objectives_complete():
            stage_advanced = advance_quest_stage(game, quest)
            if stage_advanced:
                messages.append(f"Quest advanced: {quest.name}")

    return messages


def _matches_objective(obj: QuestObjective, event_type: str, context: dict) -> bool:
    """Check if an event matches an objective's completion criteria."""
    if obj.obj_type != event_type:
        return False

    # Type-specific matching
    if event_type == "dialogue":
        return (
            context.get("npc_id") == obj.params.get("target_npc")
            or context.get("dialogue_id") == obj.params.get("dialogue_id")
        )

    elif event_type == "visit_location":
        target_pos = obj.params.get("location")
        if not target_pos:
            return False

        visited_pos = context.get("position")
        if not visited_pos:
            return False

        # Check if within radius
        radius = obj.params.get("radius", 0)
        dx = abs(visited_pos[0] - target_pos[0])
        dy = abs(visited_pos[1] - target_pos[1])
        return dx <= radius and dy <= radius

    elif event_type == "collect_item":
        return context.get("item_type") == obj.params.get("item_type")

    elif event_type == "kill_enemy":
        return context.get("enemy_proto") == obj.params.get("enemy_proto")

    elif event_type == "pattern_placement":
        return True  # Simple check for now

    elif event_type == "seal_rune":
        trial_id = obj.params.get("trial_id")
        poi_id = obj.params.get("poi_id")
        if trial_id and context.get("trial_id") != trial_id:
            return False
        if poi_id and context.get("poi_id") != poi_id:
            return False
        return True

    return False


def advance_quest_stage(game: Game, quest: Quest) -> bool:
    """
    Advance quest to next stage if current stage is complete.
    Returns True if stage was advanced, False if quest completed.
    """
    proto = prototypes.resolve_proto(quest.proto_id)
    stages = proto.get("stages", [])

    quest.stage += 1

    if quest.stage >= len(stages):
        # Quest complete!
        complete_quest(game, quest)
        return False
    else:
        # Load next stage objectives
        load_stage_objectives(quest, stages[quest.stage])
        return True


def complete_quest(game: Game, quest: Quest) -> None:
    """Mark quest as completed and grant rewards."""
    quest.status = "completed"

    # Move from active to completed
    if quest.id in game.active_quests:
        del game.active_quests[quest.id]
        game.completed_quests.append(quest.id)

    # Grant rewards
    proto = prototypes.resolve_proto(quest.proto_id)
    rewards = proto.get("rewards", {})

    # XP reward
    xp = rewards.get("xp", 0)
    if xp > 0:
        # Award XP to player using the game's XP system
        if hasattr(game, "_grant_xp"):
            game._grant_xp(xp)
        game.messages.add(f"Quest completed: {quest.name}! (+{xp} XP)")
    else:
        game.messages.add(f"Quest completed: {quest.name}!")

    # Item rewards (TODO: implement when inventory system is clearer)
    # Corruption reduction (if applicable)
    corruption_reduction = rewards.get("corruption_reduction", 0)
    if corruption_reduction > 0:
        game.corruption_level = max(0, game.corruption_level - corruption_reduction)


def fail_quest(game: Game, quest: Quest, reason: str = "") -> None:
    """Mark quest as failed."""
    quest.status = "failed"

    if quest.id in game.active_quests:
        del game.active_quests[quest.id]
        game.failed_quests.append(quest.id)

    msg = f"Quest failed: {quest.name}"
    if reason:
        msg += f" ({reason})"
    game.messages.add(msg)


def add_quest_location(quest: Quest, location: Tuple[int, int]) -> None:
    """Add a known location marker to a quest."""
    if location not in quest.known_locations:
        quest.known_locations.append(location)


def add_quest_note(quest: Quest, note: str) -> None:
    """Add a note to a quest."""
    if note not in quest.notes:
        quest.notes.append(note)
