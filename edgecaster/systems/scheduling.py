"""
Scheduling and time advancement system.

Handles:
- Event scheduling with heapq priority queue
- Time advancement and event processing
- Periodic regen ticks
- Cooldown decay for entities and actors
- Coherence drain based on pattern vertices
- Frozen/slow effect decay
- Attack bonus and action tick offset decay

All functions accept (game, level, ...) as parameters.
"""

from __future__ import annotations

import heapq
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState
    from edgecaster.state.actors import Actor


def schedule(game: "Game", level: "LevelState", delay: int, action: Callable[[], None]) -> None:
    """Schedule an action to run after `delay` ticks."""
    level.order += 1
    heapq.heappush(level.events, (level.current_tick + delay, level.order, action))


def advance_time(game: "Game", level: "LevelState", delta: int) -> None:
    """
    Advance time by `delta` ticks, executing any scheduled events.

    Also handles:
    - Activation TTL decay
    - FOV updates
    - Lorenz aura advancement
    - Coherence drain
    - Cooldown ticks
    - Pattern motion
    """
    # Import here to avoid circular imports
    from edgecaster.patterns import motion as pattern_motion

    target = level.current_tick + delta
    while level.events and level.events[0][0] <= target:
        tick, _, action = heapq.heappop(level.events)
        level.current_tick = tick
        action()
    level.current_tick = target

    # Decay activation TTL
    if level.activation_ttl > 0:
        level.activation_ttl = max(0, level.activation_ttl - delta)
        if level.activation_ttl == 0:
            level.activation_points = []

    # FOV update if needed
    if level.need_fov:
        game._update_fov(level)

    # Advance the Lorenz aura in game-time
    game._advance_lorenz(level, delta)

    # Coherence drain based on vertices
    coherence_tick(game, level, delta)

    # Cooldowns tick down
    cooldown_tick(game, level, delta)

    # Pattern motion tick
    pattern_motion.step_motion(game, level, delta)


def start_regen(game: "Game", level: "LevelState", actor_id: str, amount: int, interval: int) -> None:
    """
    Start periodic regen for an actor: heals `amount` HP every `interval` ticks.
    """
    def tick() -> None:
        actor = level.actors.get(actor_id)
        if actor is None or getattr(actor, "alive", True) is False:
            return
        try:
            stats = actor.stats
            if stats.hp < stats.max_hp:
                stats.hp = min(stats.max_hp, stats.hp + amount)
        except Exception:
            pass
        # Reschedule if still alive
        if actor is not None:
            schedule(game, level, interval, tick)

    schedule(game, level, interval, tick)


def coherence_tick(game: "Game", level: "LevelState", delta: int) -> None:
    """Drain coherence each tick based on vertex count beyond INT*4."""
    from edgecaster.patterns import builder

    player = game._player()
    stats = player.stats
    intel = game.character.stats.get("int", 0)
    discount = intel * 4
    verts = len(level.pattern.vertices) if level.pattern else 0
    over = max(0, verts - discount)
    if over <= 0:
        return
    # Drain per tick: over/10 per design
    drain = over * delta / 10.0
    stats.coherence = int(max(0, stats.coherence - drain))
    if stats.coherence <= 0:
        # Pattern unravels immediately
        level.pattern = builder.Pattern()
        level.pattern_anchor = None
        level.activation_points = []
        level.activation_ttl = 0
        game.log.add("Your pattern loses coherence and unravels.")
        stats.coherence = stats.max_coherence


def cooldown_tick(game: "Game", level: "LevelState", delta: int) -> None:
    """Tick down cooldowns on actors, ground entities, and inventory items."""
    seen: set[str] = set()

    def tick_entity(ent) -> None:
        if not hasattr(ent, "cooldowns"):
            return
        ent_id = getattr(ent, "id", None)
        if ent_id and ent_id in seen:
            return
        if ent_id:
            seen.add(ent_id)
        cds = getattr(ent, "cooldowns", {})
        to_delete = []
        for name, val in list(cds.items()):
            new_val = max(0, val - delta)
            if new_val <= 0:
                to_delete.append(name)
            else:
                cds[name] = new_val
        for name in to_delete:
            del cds[name]

    for act in level.actors.values():
        tick_entity(act)
    for ent in level.entities.values():
        tick_entity(ent)
    for items in getattr(game, "inventories", {}).values():
        for ent in items:
            tick_entity(ent)

    # Tick down frozen/chilled slow effects (decay 0.1 every 10 ticks).
    _tick_frozen_slow(level, delta)

    # Tick down temporary attack bonuses (used by enemies like the Gory Ascetic).
    _tick_attack_bonus(level, delta)

    # Tick down additive action-speed modifiers (used by War Drummer haste).
    _tick_action_offset(level, delta)


def _tick_frozen_slow(level: "LevelState", delta: int) -> None:
    """Decay frozen slow effects on actors."""
    for actor in level.actors.values():
        tags = getattr(actor, "tags", None) or {}
        mult = float(tags.get("frozen_slow", 1.0))
        if mult <= 1.0:
            continue
        acc = float(tags.get("frozen_slow_timer", 0.0))
        acc += delta
        if acc >= 10:
            steps = int(acc // 10)
            acc = acc % 10
            mult = max(1.0, mult - steps * 0.1)
        if mult <= 1.0 + 1e-6:
            tags.pop("frozen_slow", None)
            tags.pop("frozen_slow_timer", None)
        else:
            tags["frozen_slow"] = mult
            tags["frozen_slow_timer"] = acc
        actor.tags = tags


def _tick_attack_bonus(level: "LevelState", delta: int) -> None:
    """Decay temporary attack bonuses on actors."""
    for actor in level.actors.values():
        tags = getattr(actor, "tags", None) or {}
        try:
            ticks = int(tags.get("attack_bonus_ticks", 0))
        except Exception:
            ticks = 0
        if ticks <= 0:
            continue
        ticks = max(0, ticks - delta)
        if ticks <= 0:
            tags.pop("attack_bonus", None)
            tags.pop("attack_bonus_ticks", None)
        else:
            tags["attack_bonus_ticks"] = ticks
        actor.tags = tags


def _tick_action_offset(level: "LevelState", delta: int) -> None:
    """Decay additive action-speed modifiers on actors."""
    for actor in level.actors.values():
        tags = getattr(actor, "tags", None) or {}
        try:
            offset = int(tags.get("action_tick_offset", 0))
        except Exception:
            offset = 0
        if offset == 0:
            continue
        try:
            ticks = int(tags.get("action_tick_offset_ticks", 0))
        except Exception:
            ticks = 0
        if ticks <= 0:
            # Defensive: remove broken/incomplete entries.
            tags.pop("action_tick_offset", None)
            tags.pop("action_tick_offset_ticks", None)
            actor.tags = tags
            continue

        ticks = max(0, ticks - delta)
        if ticks <= 0:
            tags.pop("action_tick_offset", None)
            tags.pop("action_tick_offset_ticks", None)
        else:
            tags["action_tick_offset_ticks"] = ticks
        actor.tags = tags


def slow_mult(actor: "Actor") -> float:
    """Get slow multiplier for an actor. Delegates to action_runner."""
    from edgecaster.systems import action_runner
    return action_runner.slow_mult(actor)


def apply_action_tick_offset(actor: "Actor", delay: int) -> int:
    """Apply additive tick offset. Delegates to action_runner."""
    from edgecaster.systems import action_runner
    return action_runner.apply_tick_offset(actor, delay)
