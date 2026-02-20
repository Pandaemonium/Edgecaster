"""God registry, favor system, and invocation logic.

Gods are entities associated with chakra signatures. When the player's active
chakras match a god's signature, they can invoke the god to gain abilities.
Favor accumulates through triggers (kills, invocations) and decays over time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, TYPE_CHECKING

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.actors import Actor


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GodDef:
    """Parsed god definition from YAML."""
    id: str
    name: str
    glyph: str
    color: Tuple[int, int, int]
    description: str
    chakra_signature: FrozenSet[str]
    domain: str
    rival_domains: List[str]
    favor_decay_rate: float
    favor_thresholds: Dict[str, int]
    abilities: List[Dict[str, Any]]
    favor_triggers: Dict[str, int]


@dataclass
class GodFavorState:
    """Per-god favor tracking for a player."""
    current_favor: float = 0.0
    peak_favor: float = 0.0
    invoked: bool = False
    last_invoked_tick: int = 0
    total_invocations: int = 0


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

_god_registry: Dict[str, GodDef] = {}


def _content_root() -> Path:
    return Path(__file__).resolve().parent.parent / "content"


def _parse_god(entry: dict) -> GodDef:
    color_raw = entry.get("color", [255, 255, 255])
    color = (int(color_raw[0]), int(color_raw[1]), int(color_raw[2]))
    sig = frozenset(str(s) for s in (entry.get("chakra_signature") or []))
    thresholds = entry.get("favor_thresholds") or {}
    abilities = entry.get("abilities") or []
    triggers = entry.get("favor_triggers") or {}
    return GodDef(
        id=str(entry["id"]),
        name=str(entry.get("name", entry["id"])),
        glyph=str(entry.get("glyph", "?")),
        color=color,
        description=str(entry.get("description", "")),
        chakra_signature=sig,
        domain=str(entry.get("domain", "")),
        rival_domains=[str(d) for d in (entry.get("rival_domains") or [])],
        favor_decay_rate=float(entry.get("favor_decay_rate", 0.5)),
        favor_thresholds={str(k): int(v) for k, v in thresholds.items()},
        abilities=[dict(a) for a in abilities],
        favor_triggers={str(k): int(v) for k, v in triggers.items()},
    )


def load_gods() -> Dict[str, GodDef]:
    """Load god definitions from gods.yaml and populate the registry."""
    global _god_registry
    if yaml is None:
        return {}
    path = _content_root() / "gods.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        return {}
    registry: Dict[str, GodDef] = {}
    for entry in data:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        god = _parse_god(entry)
        registry[god.id] = god
    _god_registry = registry
    return registry


def get_god_registry() -> Dict[str, GodDef]:
    """Return the loaded god registry (call load_gods first)."""
    return _god_registry


def get_god(god_id: str) -> Optional[GodDef]:
    return _god_registry.get(god_id)


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------

def matching_gods(
    registry: Dict[str, GodDef],
    active_chakras: Set[str],
) -> List[GodDef]:
    """Return gods whose chakra_signature is a subset of active_chakras."""
    out: List[GodDef] = []
    for god in registry.values():
        if god.chakra_signature and god.chakra_signature <= active_chakras:
            out.append(god)
    # Sort by signature size descending (most specific first)
    out.sort(key=lambda g: len(g.chakra_signature), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Favor helpers
# ---------------------------------------------------------------------------

def _ensure_favor(game: "Game", god_id: str) -> GodFavorState:
    if not hasattr(game, "god_favor"):
        game.god_favor = {}  # type: ignore[attr-defined]
    if god_id not in game.god_favor:
        game.god_favor[god_id] = GodFavorState()
    return game.god_favor[god_id]


def get_favor(game: "Game", god_id: str) -> float:
    return _ensure_favor(game, god_id).current_favor


def get_favor_tier(game: "Game", god_id: str) -> Optional[str]:
    """Return the highest favor tier reached, or None."""
    god = get_god(god_id)
    if god is None:
        return None
    favor = get_favor(game, god_id)
    best: Optional[str] = None
    best_val = 0
    for tier_name, threshold in god.favor_thresholds.items():
        if favor >= threshold and threshold >= best_val:
            best = tier_name
            best_val = threshold
    return best


def available_abilities(game: "Game", god_id: str) -> List[Dict[str, Any]]:
    """Return abilities unlocked at the current favor level."""
    god = get_god(god_id)
    if god is None:
        return []
    favor = get_favor(game, god_id)
    return [a for a in god.abilities if favor >= a.get("min_favor", 0)]


def grant_favor(game: "Game", god_id: str, amount: float) -> None:
    """Add favor to a god, applying jealousy to rivals."""
    if amount <= 0:
        return
    god = get_god(god_id)
    if god is None:
        return
    state = _ensure_favor(game, god_id)
    old_favor = state.current_favor
    state.current_favor += amount
    state.peak_favor = max(state.peak_favor, state.current_favor)

    # Log tier transitions
    _check_tier_transition(game, god, old_favor, state.current_favor)

    # Jealousy: reduce favor for rival-domain gods
    if god.rival_domains:
        for other_id, other_god in _god_registry.items():
            if other_id == god_id:
                continue
            if other_god.domain in god.rival_domains:
                other_state = _ensure_favor(game, other_id)
                reduction = amount * 0.3
                old_other = other_state.current_favor
                other_state.current_favor = max(0.0, other_state.current_favor - reduction)
                if old_other > 0 and other_state.current_favor <= 0:
                    try:
                        game.log.add(f"{other_god.name} grows distant.")
                    except Exception:
                        pass


def _check_tier_transition(
    game: "Game", god: GodDef, old_favor: float, new_favor: float
) -> None:
    """Log when the player crosses a favor threshold."""
    for tier_name, threshold in sorted(god.favor_thresholds.items(), key=lambda t: t[1]):
        if old_favor < threshold <= new_favor:
            try:
                game.log.add(f"{god.name} has {tier_name} you.")
            except Exception:
                pass
            # Update player actions when abilities unlock
            _sync_god_abilities(game, god.id)


def decay_favor(game: "Game", dt_ticks: int) -> None:
    """Decay favor for all gods each tick."""
    if not hasattr(game, "god_favor"):
        return
    for god_id, state in game.god_favor.items():
        if state.current_favor <= 0:
            continue
        god = get_god(god_id)
        if god is None:
            continue
        # Decay rate is per 100 ticks
        decay = god.favor_decay_rate * (dt_ticks / 100.0)
        old = state.current_favor
        state.current_favor = max(0.0, state.current_favor - decay)
        # Check if we dropped below a tier
        if old != state.current_favor:
            _check_tier_loss(game, god, old, state.current_favor)


def _check_tier_loss(
    game: "Game", god: GodDef, old_favor: float, new_favor: float
) -> None:
    """Remove abilities when favor drops below a threshold."""
    for tier_name, threshold in sorted(god.favor_thresholds.items(), key=lambda t: t[1]):
        if old_favor >= threshold > new_favor:
            _sync_god_abilities(game, god.id)
            return


# ---------------------------------------------------------------------------
# Invoke / Dismiss
# ---------------------------------------------------------------------------

def invoke_god(game: "Game", god_id: str) -> bool:
    """Invoke a god. Returns True if successful."""
    god = get_god(god_id)
    if god is None:
        return False

    # Dismiss any currently invoked god
    if hasattr(game, "god_favor"):
        for gid, state in game.god_favor.items():
            if state.invoked and gid != god_id:
                dismiss_god(game, gid)

    state = _ensure_favor(game, god_id)
    state.invoked = True
    try:
        state.last_invoked_tick = game._level().current_tick
    except Exception:
        pass
    state.total_invocations += 1

    # Invoke favor trigger
    invoke_favor = god.favor_triggers.get("invoke", 0)
    if invoke_favor > 0:
        grant_favor(game, god_id, invoke_favor)

    try:
        game.log.add(f"You invoke {god.name}.")
    except Exception:
        pass

    # Add god abilities to player actions
    _sync_god_abilities(game, god_id)
    return True


def dismiss_god(game: "Game", god_id: str) -> None:
    """Dismiss a currently invoked god."""
    state = _ensure_favor(game, god_id)
    if not state.invoked:
        return
    state.invoked = False
    god = get_god(god_id)

    # Remove god abilities from player actions
    _remove_god_abilities(game, god_id)

    if god is not None:
        try:
            game.log.add(f"{god.name} recedes.")
        except Exception:
            pass


def get_invoked_god(game: "Game") -> Optional[str]:
    """Return the ID of the currently invoked god, or None."""
    if not hasattr(game, "god_favor"):
        return None
    for god_id, state in game.god_favor.items():
        if state.invoked:
            return god_id
    return None


# ---------------------------------------------------------------------------
# Action loadout management
# ---------------------------------------------------------------------------

def _sync_god_abilities(game: "Game", god_id: str) -> None:
    """Add/remove god abilities based on current favor and invocation status."""
    god = get_god(god_id)
    if god is None:
        return
    state = _ensure_favor(game, god_id)

    try:
        level = game._level()
        player = level.actors.get(game.player_id)
        if player is None:
            return
    except Exception:
        return

    actions = list(player.actions)
    favor = state.current_favor

    for ability in god.abilities:
        aid = ability.get("id", "")
        if not aid:
            continue
        if state.invoked and favor >= ability.get("min_favor", 0):
            if aid not in actions:
                actions.append(aid)
        else:
            while aid in actions:
                actions.remove(aid)

    player.actions = tuple(actions)


def _remove_god_abilities(game: "Game", god_id: str) -> None:
    """Remove all abilities from a god."""
    god = get_god(god_id)
    if god is None:
        return
    try:
        level = game._level()
        player = level.actors.get(game.player_id)
        if player is None:
            return
    except Exception:
        return

    actions = list(player.actions)
    for ability in god.abilities:
        aid = ability.get("id", "")
        while aid in actions:
            actions.remove(aid)
    player.actions = tuple(actions)


# ---------------------------------------------------------------------------
# Kill trigger
# ---------------------------------------------------------------------------

def on_kill_trigger(game: "Game", killed_actor: "Actor") -> None:
    """Grant favor to the invoked god when an enemy is killed."""
    invoked = get_invoked_god(game)
    if invoked is None:
        return
    god = get_god(invoked)
    if god is None:
        return

    faction = getattr(killed_actor, "faction", "")
    if faction == "hostile":
        amount = god.favor_triggers.get("kill_hostile", 0)
    else:
        amount = god.favor_triggers.get("kill_any", 0)

    # kill_any also applies for hostile kills (additive)
    if faction == "hostile":
        amount += god.favor_triggers.get("kill_any", 0)

    if amount > 0:
        grant_favor(game, invoked, amount)


def on_damage_taken_trigger(game: "Game") -> None:
    """Grant favor to the invoked god when the player takes damage."""
    invoked = get_invoked_god(game)
    if invoked is None:
        return
    god = get_god(invoked)
    if god is None:
        return
    amount = god.favor_triggers.get("take_damage", 0)
    if amount > 0:
        grant_favor(game, invoked, amount)


# ---------------------------------------------------------------------------
# Per-tick
# ---------------------------------------------------------------------------

def tick_gods(game: "Game", dt_ticks: int) -> None:
    """Per-tick god system update: decay favor."""
    decay_favor(game, dt_ticks)


# ---------------------------------------------------------------------------
# Invoke action implementation
# ---------------------------------------------------------------------------

def act_invoke_god(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Action handler for invoke_god. Checks chakras and presents matching gods."""
    try:
        from edgecaster.systems import chakra_items as chakra_items_system

        level = game._level()
        player = level.actors.get(actor_id)
        if player is None or not getattr(player, "alive", False):
            return

        chakra_state = chakra_items_system.ensure_actor_chakra_state(player)
        if chakra_state is None:
            game.log.add("You have no chakra state to invoke a god.")
            return

        active = set(chakra_state.active)
        registry = get_god_registry()
        matches = matching_gods(registry, active)

        if not matches:
            game.log.add("Your active chakras do not match any known god.")
            return

        # If only one match, invoke directly
        if len(matches) == 1:
            invoke_god(game, matches[0].id)
            return

        # Multiple matches — present a choice
        choices = [g.name for g in matches]

        def on_choice(idx: int, g: "Game") -> None:
            if 0 <= idx < len(matches):
                invoke_god(g, matches[idx].id)

        game.set_urgent(
            "Which god do you invoke?",
            title="Invoke God",
            choices=choices,
            on_choice_effect=on_choice,
        )
    except Exception:
        game.log.add("You fail to invoke a god.")
