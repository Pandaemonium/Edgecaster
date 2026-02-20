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
    shrine: Optional[Dict[str, Any]] = None


@dataclass
class GodFavorState:
    """Per-god favor tracking for a player."""
    current_favor: float = 0.0
    peak_favor: float = 0.0
    pattern_active: bool = False
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
        shrine=dict(entry["shrine"]) if entry.get("shrine") else None,
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
    """Log when favor drops below a threshold. Ability sync happens in tick_gods."""
    for tier_name, threshold in sorted(god.favor_thresholds.items(), key=lambda t: t[1]):
        if old_favor >= threshold > new_favor:
            try:
                game.log.add(f"{god.name}'s {tier_name} fades.")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Pattern-based access (replaces invoke)
# ---------------------------------------------------------------------------

def get_active_gods(game: "Game") -> List[str]:
    """Return IDs of gods whose holy symbol pattern is currently active."""
    if not hasattr(game, "god_favor"):
        return []
    return [gid for gid, state in game.god_favor.items() if state.pattern_active]


def sync_all_god_abilities(game: "Game") -> None:
    """Check player's active chakras against all gods.

    For each god whose signature matches, add abilities unlocked by favor.
    For each god whose signature does NOT match, remove all abilities.
    """
    registry = get_god_registry()
    if not registry:
        return
    try:
        from edgecaster.systems import chakra_items as chakra_items_system
        level = game._level()
        player = level.actors.get(game.player_id)
        if player is None or not getattr(player, "alive", False):
            return
        chakra_state = chakra_items_system.ensure_actor_chakra_state(player)
        if chakra_state is None:
            return
        active = set(chakra_state.active)
    except Exception:
        return

    matched = matching_gods(registry, active)
    matched_ids = {g.id for g in matched}

    actions = list(player.actions)

    for god_id, god in registry.items():
        state = _ensure_favor(game, god_id)
        favor = state.current_favor

        if god_id in matched_ids:
            was_active = state.pattern_active
            state.pattern_active = True
            if not was_active:
                try:
                    game.log.add(f"You feel the presence of {god.name}.")
                except Exception:
                    pass
            for ability in god.abilities:
                aid = ability.get("id", "")
                if not aid:
                    continue
                if favor >= ability.get("min_favor", 0):
                    if aid not in actions:
                        actions.append(aid)
                else:
                    while aid in actions:
                        actions.remove(aid)
        else:
            was_active = state.pattern_active
            state.pattern_active = False
            if was_active:
                try:
                    game.log.add(f"{god.name} recedes.")
                except Exception:
                    pass
            for ability in god.abilities:
                aid = ability.get("id", "")
                while aid in actions:
                    actions.remove(aid)

    player.actions = tuple(actions)


# ---------------------------------------------------------------------------
# Kill trigger
# ---------------------------------------------------------------------------

def on_kill_trigger(game: "Game", killed_actor: "Actor") -> None:
    """Grant favor to ALL gods based on their kill triggers."""
    registry = get_god_registry()
    if not registry:
        return
    faction = getattr(killed_actor, "faction", "")
    for god_id, god in registry.items():
        amount = 0
        if faction == "hostile":
            amount = god.favor_triggers.get("kill_hostile", 0)
            amount += god.favor_triggers.get("kill_any", 0)
        else:
            amount = god.favor_triggers.get("kill_any", 0)
        if amount > 0:
            grant_favor(game, god_id, amount)


def on_damage_taken_trigger(game: "Game") -> None:
    """Grant favor to ALL gods that have take_damage triggers."""
    registry = get_god_registry()
    if not registry:
        return
    for god_id, god in registry.items():
        amount = god.favor_triggers.get("take_damage", 0)
        if amount > 0:
            grant_favor(game, god_id, amount)


def on_explore_trigger(game: "Game") -> None:
    """Grant favor to ALL gods with explore_new_tile triggers."""
    registry = get_god_registry()
    if not registry:
        return
    for god_id, god in registry.items():
        amount = god.favor_triggers.get("explore_new_tile", 0)
        if amount > 0:
            grant_favor(game, god_id, amount)


# ---------------------------------------------------------------------------
# Per-tick
# ---------------------------------------------------------------------------

def tick_gods(game: "Game", dt_ticks: int) -> None:
    """Per-tick god system update: decay favor, sync pattern-based abilities."""
    decay_favor(game, dt_ticks)
    sync_all_god_abilities(game)


# ---------------------------------------------------------------------------
# Shrine spawning
# ---------------------------------------------------------------------------

def spawn_god_actor(
    game: "Game",
    level: Any,
    god_id: str,
    pos: Tuple[int, int],
) -> Optional["Actor"]:
    """Create and register a god Actor at a shrine position.

    The god entity has high HP, divine faction, and the god's glyph/color.
    """
    god = get_god(god_id)
    if god is None:
        return None

    try:
        from edgecaster.state.actors import Actor, Stats
        from edgecaster.systems import spawning as spawning_system

        shrine_cfg = god.shrine or {}
        hp = int(shrine_cfg.get("hp", 200))
        desc = str(shrine_cfg.get("description", f"A manifestation of {god.name}."))

        actor = Actor(
            id=game._new_id(),
            name=god.name,
            pos=pos,
            glyph=god.glyph,
            color=god.color,
            kind="god_shrine",
            faction="divine",
            stats=Stats(hp=hp, max_hp=hp),
            actions=(),
            tags={
                "god_id": god_id,
                "shrine": True,
                "description": desc,
            },
        )

        # Set absolute position
        try:
            actor.abs_pos = game.abs_from_zone_local(level.coord, pos)
        except Exception:
            pass

        spawning_system.register_actor(game, level, actor, schedule_ai=False)
        return actor
    except Exception:
        return None
