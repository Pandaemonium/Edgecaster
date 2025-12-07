# edgecaster/systems/abilities.py

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional

from edgecaster.game import Game


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


def compute_abilities_signature(game: Game) -> Tuple[Tuple[str, ...], str, Tuple[Tuple[int, int], ...]]:
    """
    A small hashable signature for “what abilities should exist?”
    Used so the caller can detect when it needs to rebuild.
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
    return gen_list, illuminator_choice, custom_sig


def build_abilities(game: Game) -> List[Ability]:
    """
    Build the ability list based on the character + game state.

    This is basically the old AsciiRenderer._build_abilities logic,
    but renderer-agnostic and without Rects.
    """
    char = getattr(game, "character", None)
    generator_choice = "koch"
    illuminator_choice = "radius"
    if char:
        generator_choice = getattr(char, "generator", "koch")
        illuminator_choice = getattr(char, "illuminator", "radius")

    unlocked = getattr(game, "unlocked_generators", [generator_choice])

    # keep order but de-dupe, while ensuring the chosen generator is first
    seen = set()
    gens_ordered: list[str] = []
    for g in unlocked:
        if g in seen:
            continue
        seen.add(g)
        gens_ordered.append(g)
    if generator_choice not in seen:
        gens_ordered.insert(0, generator_choice)

    abilities: List[Ability] = []
    hotkey = 1

    def add(name: str, action: str) -> None:
        nonlocal hotkey
        abilities.append(Ability(name=name, hotkey=hotkey, action=action))
        hotkey += 1

    # Core
    add("Place", "place")
    add("Subdivide", "subdivide")
    add("Extend", "extend")

    # generator-specific (all unlocked)
    for g in gens_ordered:
        gen_label = {"koch": "Koch", "branch": "Branch", "zigzag": "Zigzag", "custom": "Custom"}.get(g, g)
        if g in ("koch", "branch", "zigzag", "custom"):
            add(gen_label, g)

    # additional custom patterns (beyond the first)
    customs = getattr(game, "custom_patterns", [])
    for idx, _pts in enumerate(customs):
        action = "custom" if idx == 0 else f"custom_{idx}"
        label = "Custom" if idx == 0 else f"Custom {idx+1}"
        # first custom is already covered if "custom" is in gens_ordered
        if idx == 0 and "custom" in gens_ordered:
            continue
        add(label, action)

    # illuminator choice
    if illuminator_choice == "radius":
        add("Activate R", "activate_all")
    elif illuminator_choice == "neighbors":
        add("Activate N", "activate_seed")
    else:
        add("Activate R", "activate_all")
        add("Activate N", "activate_seed")

    # meta
    add("Reset", "reset")
    add("Meditate", "meditate")

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

    # If someone invents a new action string and forgets to wire it:
    # fail silently rather than crash the renderer.
