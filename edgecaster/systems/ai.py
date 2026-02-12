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

    try:
        if str((getattr(actor, "tags", {}) or {}).get("rune_siege_role", "")) == "sapper":
            return _rune_sapper(game, level, actor)
    except Exception:
        pass

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
        return _deferred_attacker(game, level, actor, "chain_smash", "chain_smash_range", 4)
    if behavior_id == "gory_ascetic":
        return _gory_ascetic(game, level, actor)
    if behavior_id == "ground_slammer":
        return _ground_slammer(game, level, actor)
    if behavior_id == "bear_mauler":
        return _deferred_attacker(game, level, actor, "bear_maul", "maul_range", 3)
    if behavior_id == "thug_brawler":
        return _deferred_attacker(game, level, actor, "haymaker", "haymaker_range", 3)
    if behavior_id == "thorn_defender":
        return _deferred_attacker(game, level, actor, "thorn_burst", "thorn_range", 3)
    if behavior_id == "blood_drainer":
        return _deferred_attacker(game, level, actor, "blood_drain", "drain_range", 3)
    if behavior_id == "maw_devourer":
        return _deferred_attacker(game, level, actor, "devouring_lunge", "lunge_range", 3)
    if behavior_id == "slaver_lasher":
        return _deferred_attacker(game, level, actor, "lash", "lash_range", 3)
    if behavior_id == "mana_viper_striker":
        return _deferred_attacker(game, level, actor, "lash", "lash_range", 3)
    if behavior_id == "dandy_haymaker":
        return _deferred_attacker(game, level, actor, "haymaker", "haymaker_range", 3)
    if behavior_id == "venom_stalker":
        return _deferred_attacker(game, level, actor, "venom_snap", "venom_range", 3)
    if behavior_id == "cinder_pouncer":
        return _deferred_attacker(game, level, actor, "ember_pounce", "pounce_range", 4)
    if behavior_id == "bone_lancer":
        return _deferred_attacker(game, level, actor, "bone_lance", "lance_range", 4)
    if behavior_id == "fire_breather":
        return _deferred_attacker(game, level, actor, "fire_breath", "fire_breath_range", 5)
    if behavior_id == "circus_member":
        return _circus_member(game, level, actor)
    if behavior_id == "furious_ringmaster":
        return _furious_ringmaster(game, level, actor)
    if behavior_id == "mirror_blade_clone":
        return _mirror_blade_clone(game, level, actor)

    # Default: generic "walk toward player and bump" brain.
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


def _ground_slammer(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Ground slammer: slow, powerful AoE.

    Uses ground_slam when off cooldown and within range; otherwise walks
    toward the player using brute_move.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

    # Reputation-driven hostility check.
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

    # Read slam range from tags (default slightly > radius so slam can catch player).
    tags = getattr(actor, "tags", None) or {}
    slam_range = int(tags.get("slam_range", 4))

    # Use ground_slam if off cooldown and within range.
    if "ground_slam" in available and dist <= slam_range:
        try:
            cd = int(getattr(actor, "cooldowns", {}).get("ground_slam", 0))
        except Exception:
            cd = 0
        if cd <= 0:
            return ("ground_slam", {})

    # Otherwise walk toward player using brute_move (slow) or regular move.
    return _walk_toward(game, level, actor, available, px - ax, py - ay)


def _deferred_attacker(
    game: Any, level: Any, actor: Any,
    attack_action: str, range_tag: str, default_range: int,
) -> Tuple[str, Dict]:
    """Generic AI for enemies with a single deferred (slow) attack.

    Uses *attack_action* when off cooldown and within *range_tag* distance
    of the player, otherwise walks toward the player.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {})

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

    tags = getattr(actor, "tags", None) or {}
    attack_range = int(tags.get(range_tag, default_range))

    if attack_action in available and dist <= attack_range:
        try:
            cd = int(getattr(actor, "cooldowns", {}).get(attack_action, 0))
        except Exception:
            cd = 0
        if cd <= 0:
            return (attack_action, {})

    # Fall back to movement.
    return _walk_toward(game, level, actor, available, px - ax, py - ay)


def _circus_member(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Circus companion AI: stay near ringmaster, otherwise fight normally.

    The hard leash is enforced in movement dispatch (`Game._handle_move_or_attack`);
    this behavior adds intent-level cohesion so the troupe moves together.
    """
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    tags = getattr(actor, "tags", None) or {}
    master_id = tags.get("circus_ringmaster_id")
    leash = int(tags.get("circus_leash_range", 15) or 15)

    master = level.actors.get(str(master_id)) if master_id else None
    if master is not None and getattr(master, "alive", True):
        a_abs = _abs_pos(game, level, actor)
        m_abs = _abs_pos(game, level, master)
        if a_abs is not None and m_abs is not None:
            ax, ay = a_abs
            mx, my = m_abs
            dist = max(abs(mx - ax), abs(my - ay))
            # If too far, prioritize regrouping over attacking.
            if dist > leash:
                return _walk_toward(game, level, actor, available, mx - ax, my - ay)

    # If in cohesion, use any configured deferred attack.
    deferred_order = [
        ("ground_slam", "slam_range", 4),
        ("chain_smash", "chain_smash_range", 4),
        ("haymaker", "haymaker_range", 3),
        ("lash", "lash_range", 3),
        ("ember_pounce", "pounce_range", 4),
        ("venom_snap", "venom_range", 3),
        ("bone_lance", "lance_range", 4),
        ("bear_maul", "maul_range", 3),
        ("fire_breath", "fire_breath_range", 5),
    ]
    for action_name, range_tag, default_range in deferred_order:
        if action_name in available:
            return _deferred_attacker(
                game,
                level,
                actor,
                action_name,
                range_tag,
                default_range,
            )

    # Otherwise behave like a standard hostile.
    return _generic_walk_toward(game, level, actor)


def _furious_ringmaster(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Ringmaster AI: keep the circus pack together and crack the whip."""
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    tags = getattr(actor, "tags", None) or {}
    leash = int(tags.get("circus_leash_range", 15) or 15)
    member_ids = list(tags.get("circus_member_ids", []) or [])

    r_abs = _abs_pos(game, level, actor)
    if r_abs is not None and member_ids:
        rx, ry = r_abs
        farthest_dx = 0
        farthest_dy = 0
        farthest_dist = 0
        for mid in member_ids:
            mate = level.actors.get(str(mid))
            if mate is None or not getattr(mate, "alive", True):
                continue
            m_abs = _abs_pos(game, level, mate)
            if m_abs is None:
                continue
            mx, my = m_abs
            d = max(abs(mx - rx), abs(my - ry))
            if d > farthest_dist:
                farthest_dist = d
                farthest_dx = mx - rx
                farthest_dy = my - ry
        # If someone drifted too far, step toward them to re-center the troupe.
        if farthest_dist > leash:
            return _walk_toward(game, level, actor, available, farthest_dx, farthest_dy)

    # Prefer whip attack when in range and off cooldown.
    if "lash" in available:
        return _deferred_attacker(game, level, actor, "lash", "lash_range", 3)
    # Fall back to generic hostile movement.
    return _generic_walk_toward(game, level, actor)


def _rune_sapper(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Siege saboteur AI: prioritize repaired fractures over direct pursuit."""
    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    player = _get_player_actor(game)
    if player is None:
        return ("wait", {}) if "wait" in available else (available[0], {})

    try:
        if not reputation_system.is_hostile(game, actor, player):
            return ("wait", {}) if "wait" in available else (available[0], {})
    except Exception:
        pass

    a_abs = _abs_pos(game, level, actor)
    p_abs = _abs_pos(game, getattr(game, "_level", lambda: level)(), player)
    if a_abs is None or p_abs is None:
        return ("wait", {}) if "wait" in available else (available[0], {})

    # If adjacent to player, still take the attack.
    px, py = p_abs
    ax, ay = a_abs
    if abs(px - ax) + abs(py - ay) == 1 and "move" in available:
        return ("move", {"dx": px - ax, "dy": py - ay})

    siege = getattr(level, "rune_anchor_siege", None)
    if siege is None or getattr(siege, "phase", "") == "stabilized":
        return _generic_walk_toward(game, level, actor)

    target = None
    best_dist = None
    # Repaired fractures are the highest-value sabotage targets.
    for fracture in getattr(siege, "fractures", []):
        if not getattr(fracture, "repaired", False):
            continue
        fx, fy = int(fracture.pos[0]), int(fracture.pos[1])
        dist = abs(fx - ax) + abs(fy - ay)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            target = (fx, fy)

    # If no repaired fractures exist, pressure the anchor core.
    if target is None:
        ap = getattr(siege, "anchor_pos", None)
        if ap is not None:
            target = (int(ap[0]), int(ap[1]))

    if target is None:
        return _generic_walk_toward(game, level, actor)

    tx, ty = target
    return _walk_toward(game, level, actor, available, tx - ax, ty - ay)


def _walk_toward(
    game: Any, level: Any, actor: Any,
    available: tuple, dx: int, dy: int,
) -> Tuple[str, Dict]:
    """Move one step toward a target delta. Shared by deferred-attack AIs."""
    move_action = "move" if "move" in available else ("brute_move" if "brute_move" in available else None)
    if move_action is None:
        return ("wait", {}) if "wait" in available else (available[0], {})

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


def _mirror_blade_clone(game: Any, level: Any, actor: Any) -> Tuple[str, Dict]:
    """Mirror Blade clone AI: hunt nearest hostile, slash when adjacent, dissolve on TTL expiry."""
    # Tick down lifetime
    ttl = actor.statuses.get("mirror_blade_ttl", 0)
    if ttl <= 0:
        # Dissolve: remove from level
        try:
            game.log.add(f"The mirror of {actor.name.replace('Mirror ', '')} shatters.")
            level.actors.pop(actor.id, None)
            level.entities.pop(actor.id, None)
            # Clean up blade state
            blade_states = getattr(game, "blade_states", None)
            if isinstance(blade_states, dict):
                blade_states.pop(actor.id, None)
        except Exception:
            pass
        return ("wait", {})
    actor.statuses["mirror_blade_ttl"] = ttl - 1

    available = tuple(getattr(actor, "actions", ()))
    if not available:
        return ("wait", {})

    # Find nearest hostile actor
    a_abs = _abs_pos(game, level, actor)
    if a_abs is None:
        return ("wait", {})

    best_target = None
    best_dist = 1e9
    for aid, other in list(level.actors.items()):
        if aid == actor.id:
            continue
        if not getattr(other, "alive", True):
            continue
        # Check hostility: try reputation system first, then fall back to
        # direct faction check.  The reputation system may not have opinion
        # tables for the synthetic "player" faction the clone uses, so
        # is_hostile can return False even for genuinely hostile enemies.
        hostile = False
        try:
            hostile = reputation_system.is_hostile(game, actor, other)
        except Exception:
            pass
        if not hostile:
            # Direct faction fallback: anything in "hostile" faction (or
            # any faction hostile *to the player*) counts.
            other_faction = getattr(other, "faction", "neutral")
            if other_faction == "player":
                continue  # never attack friendlies
            if other_faction == "hostile":
                hostile = True
            elif other_faction != "neutral":
                # Check if this faction is hostile to the player
                try:
                    player = _get_player_actor(game)
                    if player is not None and reputation_system.is_hostile(game, player, other):
                        hostile = True
                except Exception:
                    pass
        if not hostile:
            continue
        t_abs = _abs_pos(game, level, other)
        if t_abs is None:
            continue
        dist = abs(t_abs[0] - a_abs[0]) + abs(t_abs[1] - a_abs[1])
        if dist < best_dist:
            best_dist = dist
            best_target = (other, t_abs)

    if best_target is None:
        return ("wait", {}) if "wait" in available else (available[0], {})

    target, t_abs = best_target
    dx = t_abs[0] - a_abs[0]
    dy = t_abs[1] - a_abs[1]

    # If adjacent, use slash for blade melee damage
    if abs(dx) + abs(dy) == 1 and "slash" in available:
        return ("slash", {"target_tile": target.pos})

    # Otherwise walk toward the target
    return _walk_toward(game, level, actor, available, dx, dy)
