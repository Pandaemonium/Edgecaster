"""
Action Runner - Unified action execution for players and AI.

This module provides a single path for executing actions, handling:
- Cooldown gating
- Charge consumption (wands)
- Confirmation prompts (player only)
- Delay calculation with slow/haste modifiers
- Post-action cooldown application

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state import Actor

from edgecaster.systems.actions import get_action, action_delay, ActionDef
from edgecaster.systems import item_grants
from edgecaster.systems import equipment as equipment_system


def _telemetry(game: "Game", event: str, **payload: Any) -> None:
    """Best-effort telemetry bridge for action execution."""
    try:
        emit = getattr(game, "_telemetry_emit", None)
        if callable(emit):
            emit(event, **payload)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """Result of an action execution attempt."""
    executed: bool  # Whether the action was actually performed
    delay: int  # Time cost (0 if not executed or instant)
    message: Optional[str] = None  # Optional feedback message
    pending_confirm: bool = False  # True if awaiting user confirmation


# ---------------------------------------------------------------------------
# Action origin resolution
# ---------------------------------------------------------------------------

def find_action_origin(
    game: "Game",
    actor: "Actor",
    action_name: str,
) -> Tuple[Any, bool]:
    """Determine the origin of an action (actor or item).

    Args:
        game: The Game instance
        actor: The actor performing the action
        action_name: Name of the action

    Returns:
        Tuple of (origin_entity, is_intrinsic) where:
        - origin_entity: The actor or item that owns this action
        - is_intrinsic: True if the action is intrinsic to the actor
    """
    # Check if action is intrinsic to the actor
    tags = getattr(actor, "tags", {}) or {}
    intrinsic = tags.get("intrinsic_actions")
    if not isinstance(intrinsic, list):
        intrinsic = list(getattr(actor, "actions", ()) or [])
        tags["intrinsic_actions"] = list(intrinsic)
        try:
            actor.tags = tags
        except Exception:
            pass

    intrinsic_set = {str(x) for x in intrinsic if x}
    is_intrinsic = str(action_name) in intrinsic_set

    if is_intrinsic:
        return actor, True

    # Look for item granting this action
    inv = game.inventories.get(actor.id, [])
    origin = item_grants.find_grant_origin(inv, action_name)

    if origin is not None:
        return origin, False

    # Fall back to actor
    return actor, True


def get_cooldown(origin: Any, action_name: str) -> int:
    """Get the remaining cooldown for an action on its origin."""
    try:
        return int(getattr(origin, "cooldowns", {}).get(action_name, 0))
    except Exception:
        return 0


def get_charges(item: Any) -> Optional[int]:
    """Get remaining charges for an item, or None if not a charged item."""
    tags = getattr(item, "tags", None) or {}
    if "charges" not in tags:
        return None
    try:
        return int(tags.get("charges", 0))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Delay modifiers
# ---------------------------------------------------------------------------

def slow_mult(actor: "Actor") -> float:
    """Get the slow multiplier for an actor (from frozen_slow status)."""
    tags = getattr(actor, "tags", {}) or {}
    try:
        mult = float(tags.get("frozen_slow", 1.0))
    except Exception:
        mult = 1.0
    return max(1.0, mult)


def apply_tick_offset(actor: "Actor", delay: int) -> int:
    """Apply additive tick offset (e.g., War Drummer haste).

    Non-instant actions are clamped to at least 1 tick to avoid 0-tick loops.
    """
    if delay <= 0:
        return delay

    tags = getattr(actor, "tags", None) or {}
    try:
        offset = int(tags.get("action_tick_offset", 0))
    except Exception:
        offset = 0

    if offset == 0:
        return delay

    return max(1, int(delay) + int(offset))


def calculate_final_delay(actor: "Actor", base_delay: int) -> int:
    """Calculate final action delay after applying all modifiers."""
    delay = base_delay

    # Apply slow multiplier
    mult = slow_mult(actor)
    if mult > 1.0:
        delay = int(math.ceil(delay * mult))

    # Apply tick offset (haste effects)
    delay = apply_tick_offset(actor, delay)

    return delay


# ---------------------------------------------------------------------------
# Charge handling
# ---------------------------------------------------------------------------

def find_charge_item(
    game: "Game",
    actor: "Actor",
    origin: Any,
) -> Optional[Any]:
    """Find the charged item if origin is an item in actor's inventory."""
    if origin is None or origin is actor:
        return None

    try:
        inv = game.inventories.get(actor.id, [])
        if origin in inv:
            return origin
    except Exception:
        pass

    return None


def consume_charge(game: "Game", actor: "Actor", charge_item: Any) -> None:
    """Consume one charge from an item and handle empty items."""
    tags = getattr(charge_item, "tags", None) or {}
    if "charges" not in tags:
        return

    try:
        charges_left = int(tags.get("charges", 0))
    except Exception:
        charges_left = 0

    before = charges_left
    charges_left = max(0, charges_left - 1)
    tags["charges"] = charges_left

    # Track max charges
    if "max_charges" not in tags:
        raw_max = tags.get("charges_max")
        try:
            tags["max_charges"] = int(raw_max) if raw_max is not None else int(before)
        except Exception:
            tags["max_charges"] = int(before)

    try:
        charge_item.tags = tags
    except Exception:
        pass

    # Auto-unequip spent items
    if charges_left == 0:
        try:
            if equipment_system.is_equipped(charge_item):
                game.unequip_item(str(actor.id), str(getattr(charge_item, "id", "")))
        except Exception:
            pass

        if actor.id == game.player_id:
            name = getattr(charge_item, "name", None) or "item"
            game.log.add(f"The {name.lower()} is spent.")


def apply_cooldown(origin: Any, action_name: str, cooldown_ticks: int) -> None:
    """Apply cooldown to the action origin."""
    if origin is None or cooldown_ticks <= 0:
        return

    try:
        origin.cooldowns[action_name] = cooldown_ticks
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------

def check_cooldown_gate(
    game: "Game",
    actor: "Actor",
    origin: Any,
    action_name: str,
    is_player: bool,
) -> Optional[str]:
    """Check if action is blocked by cooldown.

    Returns error message if blocked, None if clear.
    """
    cd = get_cooldown(origin, action_name)
    if cd > 0:
        if is_player:
            return f"That ability is recharging ({cd})."
        return "cooldown"
    return None


def check_charge_gate(
    game: "Game",
    actor: "Actor",
    charge_item: Optional[Any],
    is_player: bool,
) -> Optional[str]:
    """Check if action is blocked by lack of charges.

    Returns error message if blocked, None if clear.
    """
    if charge_item is None:
        return None

    charges_left = get_charges(charge_item)
    if charges_left is not None and charges_left <= 0:
        if is_player:
            return "That item is out of charges."
        return "no charges"

    return None


def run_action(
    game: "Game",
    actor_id: str,
    action_name: str,
    *,
    skip_confirm: bool = False,
    skip_wand_confirm: bool = False,
    is_ai: bool = False,
    **kwargs: Any,
) -> ActionResult:
    """Execute an action for an actor.

    This is the unified entry point for both player and AI actions.

    Args:
        game: The Game instance
        actor_id: ID of the acting entity
        action_name: Name of the action to perform
        skip_confirm: Skip action-defined confirmation prompts
        skip_wand_confirm: Skip wand charge confirmation
        is_ai: True if this is an AI action (disables confirmations)
        **kwargs: Action-specific parameters

    Returns:
        ActionResult with execution status and delay
    """
    try:
        level = game._level()
        actor = level.actors.get(actor_id)
    except Exception:
        actor = None

    if actor is None or not getattr(actor, "alive", True):
        _telemetry(game, "action_failed", actor_id=str(actor_id), action=action_name, reason="actor_not_found")
        return ActionResult(executed=False, delay=0, message="Actor not found")

    # Get action definition
    try:
        action_def = get_action(action_name)
    except KeyError:
        _telemetry(game, "action_failed", actor_id=str(actor_id), action=action_name, reason="unknown_action")
        return ActionResult(executed=False, delay=0, message=f"Unknown action: {action_name}")

    base_delay = action_delay(game.cfg, action_def)
    is_player = actor_id == game.player_id

    # Find action origin (actor or item)
    origin, is_intrinsic = find_action_origin(game, actor, action_name)

    # Cooldown gate
    cd_msg = check_cooldown_gate(game, actor, origin, action_name, is_player)
    if cd_msg:
        if is_player and cd_msg != "cooldown":
            game.log.add(cd_msg)
        _telemetry(
            game,
            "action_failed",
            actor_id=str(actor_id),
            action=action_name,
            reason="cooldown",
            cooldown_ticks=int(get_cooldown(origin, action_name)),
        )
        return ActionResult(executed=False, delay=0, message=cd_msg)

    # Charge handling
    charge_item = None if is_intrinsic else find_charge_item(game, actor, origin)
    charges_left = get_charges(charge_item) if charge_item else None

    # Charge gate
    charge_msg = check_charge_gate(game, actor, charge_item, is_player)
    if charge_msg:
        if is_player and charge_msg != "no charges":
            game.log.add(charge_msg)
        _telemetry(game, "action_failed", actor_id=str(actor_id), action=action_name, reason="no_charges")
        return ActionResult(executed=False, delay=0, message=charge_msg)

    # Wand confirmation (player only, not AI)
    if (
        not is_ai
        and not skip_wand_confirm
        and is_player
        and charge_item is not None
        and charges_left is not None
    ):
        is_wand_use = False
        try:
            for act, mode in item_grants.get_item_grants(charge_item):
                if act == str(action_name) and mode == item_grants.GRANT_MODE_EQUIPPED:
                    is_wand_use = True
                    break
        except Exception:
            is_wand_use = False

        if is_wand_use:
            item_name = getattr(charge_item, "name", None) or "wand"
            body = f"This will use your {item_name} ({charges_left} charges remaining.) Confirm?"
            call_kwargs = dict(kwargs)

            def on_choice(idx: int, g: "Game") -> None:
                if idx != 1:
                    return
                # Re-run the action with confirmation skipped; this will
                # execute and return the result. Then we advance time.
                result = run_action(
                    g,
                    actor_id,
                    action_name,
                    skip_wand_confirm=True,
                    skip_confirm=skip_confirm,
                    is_ai=False,
                    **call_kwargs,
                )
                # Advance time if the action executed
                if result.executed:
                    g._advance_time(g._level(), result.delay)

            game.set_urgent(
                body,
                title="Confirm",
                choices=["Cancel", "Confirm"],
                on_choice_effect=on_choice,
            )
            _telemetry(
                game,
                "action_pending_confirm",
                actor_id=str(actor_id),
                action=action_name,
                confirm_type="wand_charge",
            )
            return ActionResult(executed=False, delay=0, pending_confirm=True)

    # Action-defined confirmation (player only, not AI)
    confirm = getattr(action_def, "confirm", None)
    if (
        not is_ai
        and not skip_confirm
        and callable(confirm)
        and is_player
    ):
        try:
            prompt = confirm(game, actor_id, **kwargs)
        except Exception:
            prompt = None

        if prompt is not None:
            call_kwargs = dict(kwargs)
            choices = list(getattr(prompt, "choices", None) or ["Cancel", "Proceed"])
            proceed_idx = int(getattr(prompt, "proceed_index", 1))
            proceed_idx = max(0, min(proceed_idx, len(choices) - 1))

            def on_choice(idx: int, g: "Game") -> None:
                if idx != proceed_idx:
                    return
                # Re-run the action with confirmation skipped
                result = run_action(
                    g,
                    actor_id,
                    action_name,
                    skip_confirm=True,
                    skip_wand_confirm=True,
                    is_ai=False,
                    **call_kwargs,
                )
                # Advance time if the action executed
                if result.executed:
                    g._advance_time(g._level(), result.delay)

            game.set_urgent(
                str(getattr(prompt, "body", "")),
                title=str(getattr(prompt, "title", "")),
                choices=choices,
                on_choice_effect=on_choice,
            )
            _telemetry(
                game,
                "action_pending_confirm",
                actor_id=str(actor_id),
                action=action_name,
                confirm_type="action_confirm",
            )
            return ActionResult(executed=False, delay=0, pending_confirm=True)

    # Execute the action
    action_def.func(game, actor_id, **kwargs)

    # Consume charge if applicable
    if charge_item is not None:
        consume_charge(game, actor, charge_item)

    # Apply cooldown
    if action_def.cooldown_ticks > 0:
        apply_cooldown(origin, action_name, action_def.cooldown_ticks)

    # Calculate final delay with modifiers
    final_delay = calculate_final_delay(actor, base_delay)
    _telemetry(
        game,
        "action_executed",
        actor_id=str(actor_id),
        action=action_name,
        is_player=bool(is_player),
        delay_ticks=int(final_delay),
        cooldown_applied=int(action_def.cooldown_ticks or 0),
        used_item=bool(charge_item is not None),
        charges_before=(int(charges_left) if charges_left is not None else None),
        charges_after=(int(get_charges(charge_item)) if charge_item is not None and get_charges(charge_item) is not None else None),
    )

    return ActionResult(executed=True, delay=final_delay)


def run_ai_action(
    game: "Game",
    actor_id: str,
    action_name: str,
    **kwargs: Any,
) -> ActionResult:
    """Execute an action for an AI actor.

    This is a convenience wrapper that sets is_ai=True and skips confirmations.

    Args:
        game: The Game instance
        actor_id: ID of the AI actor
        action_name: Name of the action
        **kwargs: Action-specific parameters

    Returns:
        ActionResult with execution status and delay
    """
    return run_action(
        game,
        actor_id,
        action_name,
        skip_confirm=True,
        skip_wand_confirm=True,
        is_ai=True,
        **kwargs,
    )
