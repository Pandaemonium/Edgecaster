"""Deferred (telegraphed) action system.

A DeferredAction represents a two-phase ability: the actor declares intent
(Phase 1 / prep), a hitbox becomes visible, and after a delay the attack
resolves (Phase 2).  Resolution is scheduled via scheduling.schedule();
this module handles the data shape and per-tick cleanup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    pass

from edgecaster.systems import entity_ops as entity_ops_system


@dataclass
class DeferredAction:
    """A pending telegraphed action on a level."""

    id: str  # unique (e.g. f"{caster_id}_ground_slam_{tick}")
    caster_id: str  # actor who is prepping
    action_name: str  # e.g. "ground_slam"
    label: str  # human-readable (e.g. "Ground Slam")
    tiles: List[Tuple[int, int]]  # LOCAL-coordinate tiles in the danger zone
    resolve_tick: int  # level.current_tick at which this resolves
    created_tick: int  # level.current_tick when prep started
    resolve_fn: Callable[[], None]  # closure called on resolution
    color: Tuple[int, int, int] = (255, 60, 60)  # telegraph tint (RGB)


def tick_deferred_actions(level: Any) -> None:
    """Remove deferred actions whose caster has died (cancellation).

    Called each tick from scheduling.advance_time().  Does NOT trigger
    resolution -- that is handled by the scheduled event on level.events.
    """
    da_list = getattr(level, "deferred_actions", None)
    if not da_list:
        return
    level.deferred_actions = [
        da
        for da in da_list
        if entity_ops_system.get_actor(level, da.caster_id) is not None
        and getattr(entity_ops_system.get_actor(level, da.caster_id), "alive", True)
    ]


def cancel_for_actor(level: Any, actor_id: str) -> None:
    """Cancel all deferred actions owned by a specific actor."""
    da_list = getattr(level, "deferred_actions", None)
    if not da_list:
        return
    level.deferred_actions = [
        da for da in da_list if da.caster_id != actor_id
    ]
