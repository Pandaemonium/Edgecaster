from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional

from edgecaster.game import Game
from edgecaster.systems.actions import get_action


@dataclass
class Ability:
    """
    Pure model for an ability slot in the bar.

    NOTE: This has no pygame.Rects or UI fields; the renderer is free
    to hang extra attributes like .rect or .gear_rect onto instances
    for hit-testing.
    """
    name: str
    hotkey: int  # 1-based numeric hotkey
    action: str  # e.g. "place", "subdivide", "koch", "activate_all", ...


# ---------------------------------------------------------------------
# Ability list construction / signature
# ---------------------------------------------------------------------


def compute_abilities_signature(game: Game) -> Tuple[
    Tuple[str, ...],                      # generators
    str,                                  # illuminator choice
    Tuple[Tuple[int, int], ...],          # custom pattern "shape"
    Tuple[str, ...],                      # host-visible actions
]:
    """
    A small hashable signature for “what abilities should exist?”
    Used so the caller can detect when it needs to rebuild.

    Now includes the set of host-visible actions (names) so that
    body-swaps and class changes can alter the bar.
    """
    char = getattr(game, "character", None)
    generator_choice = "koch"
    illuminator_choice = "radius"
    if char:
        generator_choice = getattr(char, "generator", "koch")
        illuminator_choice = getattr(char, "illuminator", "radius")

    unlocked = getattr(game, "unlocked_generators", [generator_choice])
    gen_list = tuple(unlocked)

    customs = getattr(game, "custom_patterns", [])
    custom_sig: Tuple[Tuple[int, int], ...] = tuple(
        (len(p.get("vertices", p)), len(p.get("edges", []))) if isinstance(p, dict) else (len(p), 0)
        for p in customs
    )

    # Host-visible actions: any actions on the current host actor that
    # are marked show_in_bar=True in the action registry.
    host_actions: List[str] = []
    try:
        level = game._level()
        host = level.actors.get(game.player_id)
    except Exception:
        host = None

    if host is not None:
        for name in getattr(host, "actions", ()) or ():
            try:
                adef = get_action(name)
            except Exception:
                continue
            if getattr(adef, "show_in_bar", False):
                host_actions.append(name)

    host_sig = tuple(sorted(set(host_actions)))

    return gen_list, illuminator_choice, custom_sig, host_sig


def build_abilities(game: Game) -> List[Ability]:
    """
    Build the ability bar directly from the current host actor's actions.

    Any ActionDef with show_in_bar=True that appears in host.actions
    becomes an Ability in the bar, in that order.
    """
    abilities: List[Ability] = []
    hotkey = 1

    # Find the current "host" actor (whoever the player is currently driving).
    try:
        level = game._level()
        host = level.actors.get(game.player_id)
    except Exception:
        host = None

    if host is None:
        return abilities

    def add_from_action_name(name: str) -> None:
        nonlocal hotkey
        try:
            adef = get_action(name)
        except Exception:
            # Unknown / unregistered action; ignore for the bar.
            return
        if not getattr(adef, "show_in_bar", False):
            return
        abilities.append(Ability(name=adef.label, hotkey=hotkey, action=adef.name))
        hotkey += 1

    # Preserve order: whatever is in host.actions is the bar order.
    for name in getattr(host, "actions", ()) or ():
        add_from_action_name(name)

    return abilities


# ---------------------------------------------------------------------
# Action routing (what an ability *does* to the Game)
# ---------------------------------------------------------------------


def trigger_ability_effect(
    game: Game,
    action: str,
    *,
    hover_vertex: Optional[int] = None,
) -> None:
    """
    Execute the *effect* of an ability.

    UI / aim-mode decisions (like setting aim_action or target_cursor)
    should live in the scene/renderer; this just tells the Game what to do.

    Special cases (place / fractal / activation / meta) are handled
    explicitly; everything else falls back to queue_player_action if
    available.
    """

    def do_action(action_name: str, **kwargs) -> None:
        # Prefer the new generic action system if it exists.
        if hasattr(game, "queue_player_action"):
            game.queue_player_action(action_name, **kwargs)
        else:
            # Fallbacks for older builds.
            if (
                action_name in ("subdivide", "koch", "branch", "extend", "zigzag")
                or action_name.startswith("custom")
            ):
                game.queue_player_fractal(action_name)
            elif action_name == "reset":
                game.reset_pattern()
            elif action_name == "meditate":
                game.queue_meditate()
            elif action_name == "activate_all":
                if hover_vertex is None:
                    return
                game.queue_player_activate(hover_vertex)
            elif action_name == "activate_seed":
                if hover_vertex is None:
                    return
                game.queue_player_activate_seed(hover_vertex)

    # "Place" is more of a mode-toggle; we still send a generic action if available.
    if action == "place":
        if hasattr(game, "queue_player_action"):
            game.queue_player_action("place")
        else:
            game.begin_place_mode()
        return

    # Fractal operators
    if action in ("subdivide", "koch", "branch", "extend", "zigzag") or action.startswith("custom"):
        do_action(action)
        return

    # Activation with a chosen target
    if action == "activate_all":
        if hover_vertex is None:
            return
        if hasattr(game, "queue_player_action"):
            game.queue_player_action("activate_all", target_vertex=hover_vertex)
        else:
            game.queue_player_activate(hover_vertex)
        return

    if action == "activate_seed":
        if hover_vertex is None:
            return
        if hasattr(game, "queue_player_action"):
            game.queue_player_action("activate_seed", target_vertex=hover_vertex)
        else:
            game.queue_player_activate_seed(hover_vertex)
        return

    # Meta
    if action == "reset":
        do_action("reset")
        return

    if action == "meditate":
        do_action("meditate")
        return

    # Generic fallback: any other ability string is assumed to be the
    # name of a registered action. If the action system is present,
    # we let it handle the details (e.g. imp_taunt, class abilities).
    if hasattr(game, "queue_player_action"):
        game.queue_player_action(action)
    # If not, we just no-op rather than crash.
