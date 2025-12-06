"""AI behaviors and dispatcher.

Current behaviors are thin stubs documenting intent. They all fall back to a simple
“walk toward player and bump-attack” brain until we flesh them out.
"""

from typing import Any, Dict, Tuple


def choose_action(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Decide which Action this actor should take.

    Returns: (action_name, params_dict)

    Dispatches on actor.tags.get("ai") to behavior functions. If unknown, uses
    the generic_walk_toward brain.
    """
    behavior_id = None
    try:
        behavior_id = actor.tags.get("ai")
    except Exception:
        behavior_id = None

    if behavior_id == "melee_brute":
        return _melee_brute(game, level, actor)
    if behavior_id == "skirmisher":
        return _skirmisher(game, level, actor)
    if behavior_id == "dive_bite":
        return _dive_bite(game, level, actor)
    if behavior_id == "lunatic":
        return _lunatic(game, level, actor)
    if behavior_id == "mana_bite":
        return _mana_bite(game, level, actor)

    # Default: generic “walk toward player and bump” brain.
    return _generic_walk_toward(game, level, actor)


# ---------------------------------------------------------------------------
# Behavior stubs (document intent; currently use generic fallback patterns).

def _generic_walk_toward(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Simple brain: if adjacent, move into player; else step toward them."""
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player_id = getattr(game, "player_id", None)
    if player_id is None or player_id not in level.actors:
        return ("wait", {})

    player = level.actors[player_id]
    px, py = player.pos
    ax, ay = actor.pos
    dx = px - ax
    dy = py - ay

    if abs(dx) + abs(dy) == 1 and "move" in available:
        return ("move", {"dx": dx, "dy": dy})

    rng = getattr(game, "rng", None)
    if rng is None:
        import random as rng  # type: ignore

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
        return ("wait", {}) if "wait" in available else (available[0], {})

    step = rng.choice(candidates)  # type: ignore[attr-defined]
    if "move" in available:
        return ("move", {"dx": step[0], "dy": step[1]})

    return ("wait", {}) if "wait" in available else (available[0], {})


def _melee_brute(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Corrupted thug / melee brute.
    Intent: slow, high damage. Could prefer waiting an extra beat before striking.
    Currently: generic walk toward + bump attack.
    """
    return _generic_walk_toward(game, level, actor)


def _skirmisher(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Goblin skirmisher.
    Intent: low HP, medium damage, might kite; may drop items on death (handled elsewhere).
    Currently: generic walk toward + bump attack.
    """
    return _generic_walk_toward(game, level, actor)


def _dive_bite(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Vampire bat.
    Intent: fast movement, low HP, medium-low damage, often in packs; could “dive” if not adjacent.
    Currently: generic walk toward + bump attack.
    """
    return _generic_walk_toward(game, level, actor)


def _lunatic(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Raving lunatic.
    Intent: non-hostile until close; barks semi-coherent lines; hostile when nearby.
    Currently: becomes generic once player is adjacent; otherwise waits.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})
    player_id = getattr(game, "player_id", None)
    if player_id is None or player_id not in level.actors:
        return ("wait", {})
    player = level.actors[player_id]
    ax, ay = actor.pos
    px, py = player.pos
    dist = abs(px - ax) + abs(py - ay)
    if dist <= 1:
        return _generic_walk_toward(game, level, actor)
    # TODO: emit ambient chatter lines here.
    return ("wait", {})


def _mana_bite(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Mana viper.
    Intent: fast; bite drains a small amount of mana (handled on hit in combat/effects system).
    Currently: generic walk toward + bump attack.
    """
    return _generic_walk_toward(game, level, actor)
