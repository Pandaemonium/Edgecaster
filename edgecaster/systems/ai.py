"""AI behaviors and dispatcher.

Current behaviors are thin stubs documenting intent. They all fall back to a simple
“walk toward player and bump-attack” brain until we flesh them out.
"""

from typing import Any, Dict, Tuple

from edgecaster.systems import reputation as reputation_system


def _get_player_actor(game: Any):
    try:
        return game._player()
    except Exception:
        return None


def _abs_pos(game: Any, level: Any, actor: Any) -> tuple[int, int] | None:
    """Return ABS position for an actor, falling back to zone/local if needed."""
    try:
        ap = getattr(actor, "abs_pos", None)
        if ap is not None:
            return (int(ap[0]), int(ap[1]))
    except Exception:
        pass
    try:
        coord = getattr(level, "coord", getattr(game, "zone_coord", (0, 0, 0)))
        return game.abs_from_zone_local(coord, actor.pos)
    except Exception:
        return None


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
    if behavior_id == "imp_loudmouth":
        return _imp_loudmouth(game, level, actor)

    if behavior_id == "war_drummer":
        return _war_drummer(game, level, actor)

    if behavior_id == "shackled_brute":
        return _shackled_brute(game, level, actor)
    if behavior_id == "gory_ascetic":
        return _gory_ascetic(game, level, actor)

    # Default: generic “walk toward player and bump” brain.
    return _generic_walk_toward(game, level, actor)


# ---------------------------------------------------------------------------
# Behavior stubs (document intent; currently use generic fallback patterns).

def _generic_walk_toward(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Simple brain: if adjacent, move into player; else step toward them."""
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

    # Reputation-driven hostility: don't chase/bump-attack the player unless hostile.
    try:
        if not reputation_system.is_hostile(game, actor, player):
            return ("wait", {}) if "wait" in available else (available[0], {})
    except Exception:
        pass

    p_abs = _abs_pos(game, getattr(game, "_level", lambda: level)(), player)
    a_abs = _abs_pos(game, level, actor)
    if p_abs is None or a_abs is None:
        return ("wait", {})
    px, py = p_abs
    ax, ay = a_abs
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


def _shackled_brute(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Shackled brute.
    Intent: hard-hitting but slow (uses brute_move, which costs more ticks).
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

    # Reputation-driven hostility: don't chase/bump-attack the player unless hostile.
    try:
        if not reputation_system.is_hostile(game, actor, player):
            return ("wait", {}) if "wait" in available else (available[0], {})
    except Exception:
        pass

    move_action = "brute_move" if "brute_move" in available else ("move" if "move" in available else None)
    if move_action is None:
        return ("wait", {}) if "wait" in available else (available[0], {})

    p_abs = _abs_pos(game, getattr(game, "_level", lambda: level)(), player)
    a_abs = _abs_pos(game, level, actor)
    if p_abs is None or a_abs is None:
        return ("wait", {})
    px, py = p_abs
    ax, ay = a_abs
    dx = px - ax
    dy = py - ay

    if abs(dx) + abs(dy) == 1:
        return (move_action, {"dx": dx, "dy": dy})

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
    return (move_action, {"dx": step[0], "dy": step[1]})


def _gory_ascetic(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Gory ascetic.
    Intent: when close to the player, self-harm to gain a short attack bonus.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

    # Reputation-driven hostility: don't chase/bump-attack the player unless hostile.
    try:
        if not reputation_system.is_hostile(game, actor, player):
            return ("wait", {}) if "wait" in available else (available[0], {})
    except Exception:
        pass

    a_abs = _abs_pos(game, level, actor)
    p_abs = _abs_pos(game, getattr(game, "_level", lambda: level)(), player)
    if a_abs is None or p_abs is None:
        return ("wait", {})
    ax, ay = a_abs
    px, py = p_abs
    dist = abs(px - ax) + abs(py - ay)

    # Only flagellate when close.
    if dist <= 2 and "flagellate_self" in available:
        tags = getattr(actor, "tags", None) or {}
        try:
            ticks = int(tags.get("attack_bonus_ticks", 0))
        except Exception:
            ticks = 0
        try:
            hp = int(getattr(actor.stats, "hp", 0))
        except Exception:
            hp = 0
        if ticks <= 0 and hp > 1:
            return ("flagellate_self", {})

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
    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})
    a_abs = _abs_pos(game, level, actor)
    p_abs = _abs_pos(game, getattr(game, "_level", lambda: level)(), player)
    if a_abs is None or p_abs is None:
        return ("wait", {})
    ax, ay = a_abs
    px, py = p_abs
    dist = abs(px - ax) + abs(py - ay)
    if dist <= 1:
        return _generic_walk_toward(game, level, actor)
    # ambient chatter chance
    rng = getattr(game, "rng", None)
    if rng is None:
        import random as rng  # type: ignore
    if rng.random() < 0.05:
        _lunatic_chatter(game, actor)
    return ("wait", {})


_LUNATIC_LINES = [
    "The lines! They wiggle—just like the coastlines...",
    "Do you hear the humming? It's the fractals singing.",
    "Press ? and the secrets unfold. Or maybe it's just a map...",
    "Don't stare into the Julia sea too long, you'll drown in detail.",
    "Patterns within patterns—draw them, cast them, flee them.",
    "Your mana leaks like sand unless you weave tight runes!",
    "Coherence... coherence... don't let it unravel.",
    "The lab? It's out there, but sometimes the doors bite back.",
    "Beware the ones that buzz—they'll drink your mana dry.",
    "The further down you go, the heavier the echoes hit.",
    "Was it Lorenz or Lorentz? I always get them confused...",
]


def _lunatic_chatter(game: Any, actor: Any) -> None:
    """Log a random voiceline from the lunatic."""
    try:
        rng = getattr(game, "rng", None)
        import random
        if rng is None:
            rng = random
        line = rng.choice(_LUNATIC_LINES)  # type: ignore[attr-defined]
        game.log.add(f"{actor.name} mutters: \"{line}\"")
    except Exception:
        pass




def _mana_bite(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Mana viper.
    Intent: fast; bite drains a small amount of mana (handled on hit in combat/effects system).
    Currently: generic walk toward + bump attack.
    """
    return _generic_walk_toward(game, level, actor)


def _imp_loudmouth(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """
    Imp loudmouth.
    Intent: mostly runs the generic melee brain, but occasionally
    uses the 'imp_taunt' action to hurl abuse at the player.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

    # Get RNG
    rng = getattr(game, "rng", None)
    if rng is None:
        import random as rng  # type: ignore

    # Chance to taunt instead of moving/attacking.
    # Keep this fairly low so it feels occasional, not spammy.
    if "imp_taunt" in available and rng.random() < 0.06:
        return ("imp_taunt", {})

    # Otherwise, just use the generic walk-toward-then-bump behavior.
    return _generic_walk_toward(game, level, actor)


def _war_drummer(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """War drummer.

    Intent: periodically buff nearby hostiles with a haste-like tick reduction.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

    # If this actor shouldn't be hostile right now, don't act aggressive.
    try:
        if not reputation_system.is_hostile(game, actor, player):
            return ("wait", {}) if "wait" in available else (available[0], {})
    except Exception:
        pass

    # Read tuning from YAML tags if present.
    tags = getattr(actor, "tags", None) or {}
    yaml_tags = tags.get("tags")
    if not isinstance(yaml_tags, dict):
        yaml_tags = {}

    try:
        radius = int(yaml_tags.get("drum_radius", 6) or 6)
    except Exception:
        radius = 6
    radius = max(1, radius)

    # Trigger range is a bit wider than the buff radius so it can "prep" the fight.
    trigger_range = max(radius + 2, radius * 2)

    # Prefer using war drum when off cooldown and the player is within range.
    if "war_drum" in available:
        try:
            cd = int(getattr(actor, "cooldowns", {}).get("war_drum", 0))
        except Exception:
            cd = 0
        if cd <= 0:
            a_abs = _abs_pos(game, level, actor)
            p_abs = _abs_pos(game, getattr(game, "_level", lambda: level)(), player)
            if a_abs is None or p_abs is None:
                return ("wait", {}) if "wait" in available else (available[0], {})
            ax, ay = a_abs
            px, py = p_abs
            dist = abs(px - ax) + abs(py - ay)
            if dist <= trigger_range:
                # If there are other hostiles nearby, this is especially valuable.
                nearby_hostiles = 0
                for other in level.actors.values():
                    if other is None or not getattr(other, "alive", True):
                        continue
                    try:
                        if not reputation_system.is_hostile(game, other, player):
                            continue
                    except Exception:
                        continue
                    ox, oy = other.pos
                    if abs(ox - ax) + abs(oy - ay) <= radius:
                        nearby_hostiles += 1
                        if nearby_hostiles >= 2:
                            break
                if nearby_hostiles >= 2 or dist <= radius:
                    return ("war_drum", {})

    return _generic_walk_toward(game, level, actor)
