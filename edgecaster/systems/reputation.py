from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Tuple

import random as _random

from edgecaster.content.factions import FACTIONS

# Debug logging for reputation changes
_log = logging.getLogger(__name__)


HATED: int = -250
DISLIKED: int = -125
LIKED: int = 125
LOVED: int = 250

# Reputation deltas for killing legendaries (big swings!)
LEGENDARY_KILL_DELTAS = {
    "hated": 100,    # Killing a hated legendary = +100 with factions that hate them
    "disliked": 50,  # Killing a disliked legendary = +50
    "liked": 50,     # Killing a liked legendary = -50 (negative applied in code)
    "loved": 100,    # Killing a loved legendary = -100
}


def rep_tier(score: int) -> str:
    """Convert a numeric reputation/opinion score into a tier label."""
    try:
        s = int(score)
    except Exception:
        s = 0
    if s <= HATED:
        return "hated"
    if s <= DISLIKED:
        return "disliked"
    if s >= LOVED:
        return "loved"
    if s >= LIKED:
        return "liked"
    return "neutral"


def is_legendary(actor: Any) -> bool:
    """Return True if the actor is a legendary creature."""
    tags = getattr(actor, "tags", None) or {}
    return bool(tags.get("legendary_id"))


def actor_factions(actor: Any) -> Tuple[str, ...]:
    """Return this actor's faction memberships (affiliations) as ids."""
    aff = getattr(actor, "affiliations", None)
    if isinstance(aff, (list, tuple)) and len(aff) > 0:
        return tuple(str(x) for x in aff if x)

    # Legacy: fall back to Actor.faction.
    f = str(getattr(actor, "faction", "") or "")
    if not f:
        return tuple()

    # Some older code uses faction="npc" as a role rather than a membership.
    if f == "npc":
        return ("neutral",)
    return (f,)


def _opinion_reduce(values: Iterable[int]) -> int:
    vals = [int(v) for v in values]
    if not vals:
        return 0
    # If any faction relation is negative, treat the *most negative* as dominant.
    if any(v < 0 for v in vals):
        return min(vals)
    # Otherwise, treat the most positive as dominant.
    if any(v > 0 for v in vals):
        return max(vals)
    return 0


def faction_opinion_toward_factions(observer_faction_id: str, target_factions: Iterable[str]) -> int:
    """How a faction feels about a set of target faction ids."""
    observer_faction_id = str(observer_faction_id)
    fdef = FACTIONS.get(observer_faction_id)
    if fdef is None:
        return 0

    opinions = getattr(fdef, "opinions", None) or {}
    vals = []
    for tf in target_factions:
        tf = str(tf)
        try:
            vals.append(int(opinions.get(tf, 0)))
        except Exception:
            vals.append(0)
    return _opinion_reduce(vals)


def faction_opinion_toward_actor(observer_faction_id: str, actor: Any) -> int:
    """How a faction feels about a specific actor.

    Checks for explicit per-actor faction standings first (stored in
    actor.tags["faction_standings"]), then falls back to the actor's
    affiliations-based opinion.
    """
    observer_faction_id = str(observer_faction_id)

    # Check for explicit per-actor faction standings (e.g., for legendaries)
    tags = getattr(actor, "tags", None) or {}
    standings = tags.get("faction_standings")
    if isinstance(standings, dict) and observer_faction_id in standings:
        try:
            return int(standings[observer_faction_id])
        except (TypeError, ValueError):
            pass

    return faction_opinion_toward_factions(observer_faction_id, actor_factions(actor))


def player_reputation(game: Any) -> Dict[str, int]:
    char = getattr(game, "character", None)
    rep = getattr(char, "reputation", None) if char is not None else None
    if not isinstance(rep, dict):
        rep = {}
        if char is not None:
            try:
                char.reputation = rep
            except Exception:
                pass
    return rep


def standing_with_faction(game: Any, entity: Any, faction_id: str) -> int:
    """Return the perceived standing of `entity` with `faction_id`."""
    faction_id = str(faction_id)

    # Player uses dynamic reputation.
    try:
        if getattr(entity, "id", None) == getattr(game, "player_id", None):
            return int(player_reputation(game).get(faction_id, 0))
    except Exception:
        pass

    # Everyone else uses static faction opinions as their baseline standing.
    return int(faction_opinion_toward_actor(faction_id, entity))


def hostility_threshold(attacker: Any, faction_id: str) -> int:
    """Return the hostility threshold used by attacker for this faction."""
    faction_id = str(faction_id)
    tags = getattr(attacker, "tags", None) or {}
    if isinstance(tags, dict):
        by_f = tags.get("hostile_below_by_faction")
        if isinstance(by_f, dict) and faction_id in by_f:
            try:
                return int(by_f[faction_id])
            except Exception:
                pass
        if "hostile_below" in tags:
            try:
                return int(tags["hostile_below"])
            except Exception:
                pass

    fdef = FACTIONS.get(faction_id)
    if fdef is not None:
        try:
            return int(getattr(fdef, "hostile_below", 125))
        except Exception:
            return 125
    return 125


def is_hostile(game: Any, attacker: Any, target: Any) -> bool:
    """True if `attacker` should treat `target` as hostile right now."""
    if attacker is None or target is None:
        return False
    if getattr(attacker, "id", None) == getattr(target, "id", None):
        return False

    factions = actor_factions(attacker)
    if not factions:
        return False

    for fid in factions:
        thr = hostility_threshold(attacker, fid)
        standing = standing_with_faction(game, target, fid)
        if standing < thr:
            return True

    return False


def adjust_player_reputation(game: Any, faction_id: str, delta: int) -> None:
    faction_id = str(faction_id)
    try:
        dv = int(delta)
    except Exception:
        return
    if dv == 0:
        return

    rep = player_reputation(game)
    rep[faction_id] = int(rep.get(faction_id, 0)) + dv


def apply_rep_event(game: Any, target_actor: Any, *, event: str) -> List[Tuple[str, int]]:
    """Apply player reputation changes from an event involving `target_actor`.

    `event` is currently one of:
    - "kill": target_actor was killed by the player (directly or indirectly)
    - "help": player helped target_actor (quests/dialogue can call this later)

    Legendary kills use big fixed deltas from LEGENDARY_KILL_DELTAS.
    Regular kills use faction-defined kill_rep_delta (0 = no change).

    Returns:
        List of (faction_name, delta) tuples for changes that were applied.
    """
    changes: List[Tuple[str, int]] = []

    if target_actor is None:
        return changes
    event = str(event)
    if event not in ("kill", "help"):
        return changes

    # Check if this is a legendary kill (big reputation swings!)
    target_is_legendary = is_legendary(target_actor)
    is_legendary_kill = event == "kill" and target_is_legendary
    target_name = getattr(target_actor, "name", "unknown")

    _log.debug(
        f"apply_rep_event: event={event}, target={target_name}, "
        f"is_legendary={target_is_legendary}"
    )

    # Adjust reputation with every known faction based on how that faction
    # feels about the target actor.
    for fid, fdef in FACTIONS.items():
        opinion = faction_opinion_toward_actor(fid, target_actor)
        tier = rep_tier(opinion)

        _log.debug(f"  faction={fid}, opinion={opinion}, tier={tier}")

        if tier == "neutral":
            continue

        if event == "kill":
            if is_legendary_kill:
                # Legendary kills use fixed big deltas
                mag = LEGENDARY_KILL_DELTAS.get(tier, 0)
                _log.debug(f"    legendary kill: mag={mag}")
                if mag == 0:
                    continue
            else:
                # Regular kills use faction-defined delta (0 = no change)
                raw_delta = getattr(fdef, "kill_rep_delta", None)
                mag = int(raw_delta) if raw_delta is not None else 5
                _log.debug(f"    regular kill: raw_delta={raw_delta}, mag={mag}")
                if mag == 0:
                    continue

            if tier in ("liked", "loved"):
                delta = -mag
            else:
                delta = +mag

            _log.debug(f"    applying delta={delta} to {fid}")
            adjust_player_reputation(game, fid, delta)
            fname = getattr(fdef, "name", fid)
            changes.append((fname, delta))
        else:
            raw_delta = getattr(fdef, "help_rep_delta", None)
            mag = int(raw_delta) if raw_delta is not None else 5
            if mag == 0:
                continue  # Skip if faction doesn't care about help
            if tier in ("liked", "loved"):
                delta = +mag
            else:
                delta = -mag
            adjust_player_reputation(game, fid, delta)
            fname = getattr(fdef, "name", fid)
            changes.append((fname, delta))

    _log.debug(f"  total changes: {changes}")
    return changes


# ---------------------------------------------------------------------------
# Procedural Faction Standings (Caves of Qud-style)
# ---------------------------------------------------------------------------

# Which factions can have procedural standings generated for NPCs.
# Excludes player, neutral, and hostile (the base umbrella faction).
PROCEDURAL_FACTIONS = (
    "natures_chosen",
    "high_society",
    "demon_cult_flame",
    "demon_cult_void",
    "demon_cult_flesh",
    "patternkeepers",
    "edgecasters",
)

# Tier thresholds for generating standings
STANDING_TIERS = {
    "loved": 250,
    "liked": 150,
    "neutral": 0,
    "disliked": -150,
    "hated": -250,
}


def generate_procedural_standings(
    rng: _random.Random | None = None,
    *,
    num_factions: int | None = None,
    bias_positive: float = 0.5,
) -> Dict[str, int]:
    """Generate random faction standings for an NPC (e.g., legendary).

    Args:
        rng: Random number generator (uses default if None)
        num_factions: How many factions to have opinions about (1-4, random if None)
        bias_positive: Probability of a standing being positive (0.0-1.0)

    Returns:
        Dict mapping faction_id -> standing value (e.g., {"natures_chosen": 250})
    """
    if rng is None:
        rng = _random.Random()

    available = [f for f in PROCEDURAL_FACTIONS if f in FACTIONS]
    if not available:
        return {}

    if num_factions is None:
        num_factions = rng.randint(1, min(4, len(available)))
    num_factions = max(1, min(num_factions, len(available)))

    chosen = rng.sample(available, num_factions)
    standings: Dict[str, int] = {}

    for fid in chosen:
        # Decide if positive or negative
        if rng.random() < bias_positive:
            # Positive: liked or loved
            tier = rng.choice(["liked", "loved"])
        else:
            # Negative: disliked or hated
            tier = rng.choice(["disliked", "hated"])

        base = STANDING_TIERS[tier]
        # Add some variance within the tier
        variance = rng.randint(-25, 25)
        standings[fid] = base + variance

    return standings


def get_actor_faction_standings(actor: Any) -> Dict[str, int]:
    """Get the faction standings dict for an actor, if any.

    Returns explicit standings from tags["faction_standings"], or
    derives standings from affiliations if no explicit dict exists.
    """
    tags = getattr(actor, "tags", None) or {}
    standings = tags.get("faction_standings")
    if isinstance(standings, dict):
        return dict(standings)

    # Derive from affiliations if present
    affiliations = actor_factions(actor)
    if affiliations:
        derived: Dict[str, int] = {}
        for aff in affiliations:
            if aff in FACTIONS:
                # Affiliated faction loves this actor
                derived[aff] = LOVED
        return derived

    return {}


def describe_faction_standings(actor: Any) -> list[str]:
    """Return human-readable lines describing an actor's faction standings.

    Used by the look/examine display to show NPC faction relationships.
    """
    standings = get_actor_faction_standings(actor)
    if not standings:
        return []

    lines = []
    # Sort by standing value (loved first, hated last)
    for fid, val in sorted(standings.items(), key=lambda x: -x[1]):
        fdef = FACTIONS.get(fid)
        fname = fdef.name if fdef else fid
        tier = rep_tier(val)
        tier_label = tier.capitalize()
        lines.append(f"  {fname}: {tier_label}")

    return lines

