from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edgecaster.systems import action_runner
from edgecaster.systems.actions import ActionDef, TargetingSpec, get_action


@dataclass(frozen=True)
class ActionTooltip:
    """Resolved tooltip content for an action."""

    action: str
    title: str
    summary: str
    lines: tuple[str, ...] = ()


# Short summaries should explain "what this does" in one line.
_ACTION_SUMMARIES: dict[str, str] = {
    "place": "Begin rune placement by selecting a terminus.",
    "subdivide": "Split current rune segments into smaller pieces.",
    "extend": "Extend the rune forward from the current segment ends.",
    "koch": "Apply a Koch-style triangular iteration to the rune.",
    "branch": "Branch rune segments into multiple angled paths.",
    "zigzag": "Apply a zigzag iteration to the current rune.",
    "cultivate": "Apply your custom branch design as a fractal generator.",
    "custom": "Apply your current custom saved generator pattern.",
    "chakra": "Rebuild the rune from active chakras in your body schema.",
    "wind_rush": "Dash to a selected rune vertex and strike along the path.",
    "activate_all": "Activate vertices in a radius around the selected vertex.",
    "activate_seed": "Activate selected vertex and neighboring graph hops.",
    "energy_kick": "Pulse from foot-lineage vertices and damage nearby targets.",
    "palm_burst": "Pulse from hand-lineage vertices and damage nearby targets.",
    "mirror_strike": "Strike from mirrored chakra-pair endpoints.",
    "mirror_blade": "Summon a mirror clone that fights for 30 heartbeats.",
    "aggressive_vines": "Grow free-form tendrils that whip out from rune edges.",
    "choking_vines": "Mutate your rune by growing constricting branches toward enemies.",
    "push_pattern": "Push and rotate the current rune over time.",
    "rainbow_edges": "Recolor rune edges with a rainbow gradient.",
    "verdant_edges": "Recolor rune edges with a green depth gradient.",
    "winter_hue": "Apply cold density-based rune coloring.",
    "ignite": "Burn along red rune edges with decaying area damage.",
    "regrow": "Heal along green rune edges with decaying intensity.",
    "freeze": "Damage and slow targets along blue rune influence.",
    "lightning": "Roll 2dN damage (N = vertex count) and split across targets.",
    "meditate": "Spend time to recover mana.",
    "reset": "Clear the current rune.",
    "destabilize": "Chaotic teleport with possible self-damage backlash.",
    "anchor_channel": "Spend Coherence Crystals to seal a nearby fracture.",
    "anchor_stabilize": "Hold the anchor core together during final stabilization.",
    "anchor_purge": "Detonate stored coherence at the core to blast nearby demons.",
}


# Optional extra details for notable actions.
_ACTION_DETAILS: dict[str, tuple[str, ...]] = {
    "energy_kick": (
        "Uses only foot-lineage chakra provenance.",
        "Damage falls off with distance from each kick vertex.",
    ),
    "palm_burst": (
        "Uses hand-lineage chakra provenance.",
        "Best used when enemies are clustered near hand vertices.",
    ),
    "mirror_strike": (
        "Only mirrored chakra pairs contribute strike points.",
        "Strongest when both mirrored branches are active.",
    ),
    "cultivate": (
        "Applies your custom branch design as a fractal replacement rule.",
        "Open the branch editor (+/=) to design your pattern first.",
    ),
    "mirror_blade": (
        "Costs mana. Spawns a phantom clone with your blade profile.",
        "The clone uses slash on nearby hostiles and dissolves after 30 heartbeats.",
    ),
    "aggressive_vines": (
        "Free-form control effect; does not become permanent rune geometry.",
        "Best in tight spaces where tendrils can repeatedly clip hostiles.",
    ),
    "choking_vines": (
        "Splits rune edges at midpoints, then grows new real branches.",
        "Each growth turn is clamped to a narrow angle toward hostiles.",
        "Hits root targets and applies low damage-over-time while rooted.",
    ),
    "wind_rush": (
        "Targeting: choose a rune vertex.",
        "You must be standing on the rune to begin the dash.",
        "Travel time is fixed to 5 heartbeats and has a long cooldown.",
        "Hits hostile actors standing on the dash line.",
    ),
    "lightning": (
        "Excludes the caster from damage.",
        "Damage is split evenly among affected targets.",
    ),
    "anchor_channel": (
        "Requires standing near an unrepaired fracture node.",
        "Consumes Coherence Crystals and raises anchor stability.",
    ),
    "anchor_stabilize": (
        "Requires standing near the anchor core.",
        "Only works after all fractures are sealed.",
    ),
    "anchor_purge": (
        "Requires standing at the anchor core.",
        "Consumes extra Coherence Crystals for a defensive burst.",
        "Clears active catastrophe telegraphs once on cast.",
    ),
}


def _targeting_line(spec: TargetingSpec | None) -> str | None:
    if spec is None:
        return None
    if spec.kind == "vertex":
        if spec.mode == "aim":
            return "Targeting: choose a rune vertex."
        return "Targeting: vertex."
    if spec.kind == "tile":
        if spec.mode == "terminus":
            return "Targeting: choose a terminus tile."
        return "Targeting: tile."
    if spec.kind == "position":
        return "Targeting: choose a world position."
    return None


def _cooldown_and_charges(
    game: Any,
    action_def: ActionDef,
    action_name: str,
    actor_id: str | None,
) -> tuple[str, ...]:
    lines: list[str] = []

    base_cd = int(getattr(action_def, "cooldown_ticks", 0) or 0)
    if base_cd > 0:
        lines.append(f"Base cooldown: {base_cd} heartbeats.")

    if actor_id is None:
        return tuple(lines)

    try:
        level = game._level()
        actor = level.actors.get(actor_id)
    except Exception:
        actor = None
    if actor is None:
        return tuple(lines)

    try:
        origin, is_intrinsic = action_runner.find_action_origin(game, actor, action_name)
        cd = int(action_runner.get_cooldown(origin, action_name))
    except Exception:
        origin = None
        is_intrinsic = True
        cd = 0

    if cd > 0:
        lines.append(f"Recharging: {cd} heartbeats remaining.")

    # If this action is item-granted and charged, surface remaining charges.
    try:
        charge_item = None if is_intrinsic else action_runner.find_charge_item(game, actor, origin)
        charges = action_runner.get_charges(charge_item) if charge_item is not None else None
        if charges is not None:
            lines.append(f"Charges: {int(charges)}")
    except Exception:
        pass

    return tuple(lines)


def resolve_action_tooltip(game: Any, action_name: str, actor_id: str | None = None) -> ActionTooltip | None:
    """Build tooltip text for an action name using registry + live game state."""
    if not action_name:
        return None
    try:
        action_def = get_action(action_name)
    except Exception:
        return None

    if actor_id is None:
        actor_id = getattr(game, "player_id", None)

    summary = _ACTION_SUMMARIES.get(action_name, "Activate this ability.")
    lines: list[str] = []

    t_line = _targeting_line(getattr(action_def, "targeting", None))
    if t_line:
        lines.append(t_line)

    lines.extend(_cooldown_and_charges(game, action_def, action_name, actor_id))

    extra = _ACTION_DETAILS.get(action_name)
    if extra:
        lines.extend(extra)

    return ActionTooltip(
        action=action_name,
        title=str(getattr(action_def, "label", action_name)),
        summary=summary,
        lines=tuple(lines),
    )
