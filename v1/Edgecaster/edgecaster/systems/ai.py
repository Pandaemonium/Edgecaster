"""AI stubs."""

from typing import Any, Dict, Tuple


def choose_action(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Decide which Action this actor should take.

    Returns: (action_name, params_dict)

    For now this is a very small, imp-centric brain:
    - If no player on this level: wait.
    - If adjacent to the player: move into them (which becomes an attack).
    - Otherwise:
        * sometimes taunt, if the actor has 'imp_taunt'
        * otherwise shuffle vaguely toward the player (or wander).
    """
    # If the actor has no actions at all, just wait.
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    # Need a player to care about.
    player_id = getattr(game, "player_id", None)
    if player_id is None or player_id not in level.actors:
        return ("wait", {})

    player = level.actors[player_id]
    px, py = player.pos
    ax, ay = actor.pos
    dx = px - ax
    dy = py - ay

    # Adjacent: try to move into the player (becomes bump-attack).
    if abs(dx) + abs(dy) == 1 and "move" in available:
        return ("move", {"dx": dx, "dy": dy})

    # Prefer taunting occasionally if the actor knows how.
    rng = getattr(game, "rng", None)
    if rng is None:
        import random as rng  # type: ignore

    if "imp_taunt" in available and rng.random() < 0.06:
        return ("imp_taunt", {})

    # Otherwise, take a step roughly toward the player if possible,
    # falling back to a small random shimmy.
    candidates = []
    if dx > 0:
        candidates.append((1, 0))
    if dx < 0:
        candidates.append((-1, 0))
    if dy > 0:
        candidates.append((0, 1))
    if dy < 0:
        candidates.append((0, -1))

    if not candidates:
        # On the same tile? Just wait.
        if "wait" in available:
            return ("wait", {})
        return (available[0], {})

    step = rng.choice(candidates)  # type: ignore[attr-defined]
    if "move" in available:
        return ("move", {"dx": step[0], "dy": step[1]})

    # Last-ditch: if move isn't actually in actions, just wait.
    if "wait" in available:
        return ("wait", {})

    return (available[0], {})
