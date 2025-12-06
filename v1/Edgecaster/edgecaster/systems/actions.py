from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Literal, Protocol

# ---------------------------------------------------------------------------
# Core action model
# ---------------------------------------------------------------------------

SpeedTag = Literal["instant", "fast", "slow"]


class ActionFunc(Protocol):
    """
    Signature for action implementations.

    game: the Game instance (or a mock in tests)
    actor_id: id of the acting entity
    **kwargs: action-specific parameters (e.g. dx, dy for movement)
    """
    def __call__(self, game: Any, actor_id: str, **kwargs: Any) -> None: ...


@dataclass
class ActionDef:
    name: str
    label: str
    speed: SpeedTag
    func: ActionFunc


# Global registry of all actions by name.
_action_registry: Dict[str, ActionDef] = {}


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def register_action(
    name: str,
    *,
    label: str,
    speed: SpeedTag = "fast",
) -> Callable[[ActionFunc], ActionFunc]:
    """
    Decorator to register a function as an Action.

    Usage:

        @register_action("yawp", label="Yawp", speed="instant")
        def yawp_action(game, actor_id, **kwargs):
            ...

    """
    def decorator(func: ActionFunc) -> ActionFunc:
        # Dev convenience: allow override if hot–reloading.
        _action_registry[name] = ActionDef(
            name=name,
            label=label,
            speed=speed,
            func=func,
        )
        return func

    return decorator


def get_action(name: str) -> ActionDef:
    """
    Look up an action by name.

    Raises KeyError if the action is unknown.
    """
    try:
        return _action_registry[name]
    except KeyError as exc:
        known = ", ".join(sorted(_action_registry)) or "<none>"
        raise KeyError(f"Unknown action '{name}'. Known actions: {known}") from exc


def action_delay(cfg: Any, action_def: ActionDef) -> int:
    """
    Map a SpeedTag to a tick delay, using the game config.

    - "instant": 0
    - "fast":    cfg.action_time_fast
    - "slow":    cfg.action_time_slow if present, else 2 * fast
    """
    tag = action_def.speed

    if tag == "instant":
        return 0
    if tag == "fast":
        return getattr(cfg, "action_time_fast", 1)
    if tag == "slow":
        fast = getattr(cfg, "action_time_fast", 1)
        return getattr(cfg, "action_time_slow", fast * 2)

    # Fallback if someone registered a weird speed tag
    return getattr(cfg, "action_time_fast", 1)


# ---------------------------------------------------------------------------
# Concrete actions
# ---------------------------------------------------------------------------

@register_action("move", label="Move", speed="fast")
def _action_move(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Generic movement action.

    Parameters expected in **kwargs:
        dx: int – x delta in tiles
        dy: int – y delta in tiles

    This reuses the existing Game._handle_move_or_attack logic so we
    automatically get:
        - walking
        - bumping into walls / features
        - melee attacks on hostile actors
        - edge transitions for the player
    """
    dx = int(kwargs.get("dx", 0))
    dy = int(kwargs.get("dy", 0))

    # Zero vector = effectively a wait; do nothing here.
    if dx == 0 and dy == 0:
        return

    # We deliberately don't import Game here to avoid circular imports,
    # but we assume we're running against the real Game.
    if not hasattr(game, "_level") or not hasattr(game, "_handle_move_or_attack"):
        # Fallback: naive "just move the actor".
        actor = getattr(game, "actors", {}).get(actor_id)
        if actor is None or not hasattr(actor, "pos"):
            return
        x, y = actor.pos
        actor.pos = (x + dx, y + dy)
        return

    level = game._level()
    game._handle_move_or_attack(level, actor_id, dx, dy)


@register_action("yawp", label="Yawp", speed="instant")
def _debug_yawp(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Simple sandbox action used to test the action system.

    When invoked, the acting entity emits a mighty yawp into the game log.
    """
    actor = getattr(getattr(game, "actors", {}), "get", lambda *_: None)(actor_id)
    if actor is not None:
        who = getattr(actor, "name", "Something")
    else:
        who = "Something"

    if hasattr(game, "log") and hasattr(game.log, "add"):
        game.log.add(f"{who} yawps! 'Yawp!'")
    else:
        # Fallback: print to stdout if no log is available.
        print(f"{who} yawps! 'Yawp!'")

@register_action("wait", label="Wait", speed="fast")
def _action_wait(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Do absolutely nothing for one 'fast' tick.

    Useful for AI or player 'rest' behaviour, and keeps timing unified
    with other actions via action_delay.
    """
    # Intentionally empty: all the work is done by the scheduler via delay.
    return


@register_action("imp_taunt", label="Taunt", speed="fast")
def _action_imp_taunt(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Imp-specific taunt with richer verbs + randomized insults.
    """
    import random

    actor = getattr(getattr(game, "actors", {}), "get", lambda *_: None)(actor_id)
    if actor is None:
        return

    imp_name = getattr(actor, "name", "Imp")

    # Get player name if possible
    level = game._level() if hasattr(game, "_level") else None
    if level is not None and getattr(game, "player_id", None) in level.actors:
        player = level.actors[game.player_id]
        player_name = getattr(player, "name", "you")
    else:
        player_name = "you"

    VERBS = [
        "taunts",
        "jeers",
        "sneers",
        "snidely remarks",
        "shouts",
        "yells obnoxiously",
        "yawps",
        "exclaims",
        "ejaculates",
        "erupts with raucous hideous laughter",
        "complains",
        "retorts",
        "admonishes",
        "snorts contemptuously",
    ]

    TAUNTS = [
        f"Hey {player_name}, fuck you!",
        "Fuck yooouuuuu!",
        "Go to hell bitch! Heeheheh!",
        "Wow, nice fractals, reeeaally cool. Laaaaame. Nerrrrd!!",
        "Damn, you suck, you little bitch!",
        "Thou unworthy cheesemaker, or whatever!!",
        "Curses upon thy teeth! May they grow dull and moldy!",
        "Check out this annoying sound, WAAAAAAAAA hahaha!",
        "Six seven!! Six seven!! LOOOOOOLLL!",
        "What the fuck, who the fuck is this dude, what an ugly bitch, am I right?",
        f"Nobody likes you, {player_name}, they're just afraid to say it to your face.",
        "Get punked asshole! Imps forever, imp pride!",
        "I hope you crash in an overflow error!",
        "I hate you!!! A lot!!!",
        "I hope you get ambushed by an alligator.",
        "Hey look at this guy over here, Mr. Big Deal Fractal guy, ooh la la he's a fancy fucker ain't he?",
        "Berryfucker!"
    ]

    verb = random.choice(VERBS)
    line = random.choice(TAUNTS)

    if hasattr(game, "log") and hasattr(game.log, "add"):
        game.log.add(f"The {imp_name} {verb}: \"{line}\"")
    else:
        print(f"The {imp_name} {verb}: \"{line}\"")


# ---------------------------------------------------------------------------
# Fractal / rune actions
# ---------------------------------------------------------------------------

@register_action("place", label="Place", speed="fast")
def _action_place(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Enter 'place terminus' mode for the acting entity.

    Right now this is effectively a player-only thing and does not
    consume any extra parameters; we just delegate to Game.
    """
    # Keep this robust if used in tests or before Game grows helpers.
    if hasattr(game, "begin_place_mode"):
        game.begin_place_mode()


@register_action("subdivide", label="Subdivide", speed="fast")
def _action_subdivide(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'subdivide' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "subdivide")


@register_action("extend", label="Extend", speed="fast")
def _action_extend(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'extend' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "extend")


@register_action("koch", label="Koch", speed="fast")
def _action_koch(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'koch' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "koch")


@register_action("branch", label="Branch", speed="fast")
def _action_branch(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'branch' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "branch")


@register_action("zigzag", label="Zigzag", speed="fast")
def _action_zigzag(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'zigzag' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "zigzag")


@register_action("custom", label="Custom", speed="fast")
def _action_custom(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the base 'custom' fractal pattern (index 0).

    For extra saved patterns we use action names like 'custom_1',
    'custom_2', etc. and pass the suffix through to Game.act_fractal.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "custom")


# Extra custom patterns can be registered on demand if you like, e.g.:
# @register_action("custom_1", label="Custom 2", speed="fast")
# def _action_custom_1(game: Any, actor_id: str, **kwargs: Any) -> None:
#     if hasattr(game, "act_fractal"):
#         game.act_fractal(actor_id, "custom_1")


@register_action("activate_all", label="Activate R", speed="fast")
def _action_activate_all(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Activate the rune pattern with a 'radius' illuminator around a vertex.

    Expected **kwargs:
        target_vertex: Optional[int] – index into the pattern's vertex list.
    """
    target_vertex = kwargs.get("target_vertex")
    if hasattr(game, "act_activate_all"):
        game.act_activate_all(actor_id, target_vertex)


@register_action("activate_seed", label="Activate N", speed="fast")
def _action_activate_seed(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Activate the rune pattern using a 'neighbors' illuminator.

    Expected **kwargs:
        target_vertex: Optional[int] – index into the pattern's vertex list.
    """
    target_vertex = kwargs.get("target_vertex")
    if hasattr(game, "act_activate_seed"):
        game.act_activate_seed(actor_id, target_vertex)


@register_action("reset", label="Reset Rune", speed="fast")
def _action_reset_rune(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Reset the current rune/pattern and coherence for the acting entity.
    """
    if hasattr(game, "act_reset_rune"):
        game.act_reset_rune(actor_id)


@register_action("meditate", label="Meditate", speed="slow")
def _action_meditate(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Meditate to restore mana / coherence.

    Marked as 'slow' so action_delay will charge more ticks than a
    normal 'fast' action.
    """
    if hasattr(game, "act_meditate"):
        game.act_meditate(actor_id)
