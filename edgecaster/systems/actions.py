from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Callable, Dict, Literal, Protocol, Union

# Optional: pattern colors (only imported when used to avoid extra deps elsewhere)
try:
    from edgecaster.patterns import colors as pattern_colors
except Exception:  # pragma: no cover - keep fail-soft for minimal envs/tests
    pattern_colors = None

from edgecaster import prototypes
from edgecaster.systems import reputation as reputation_system
from edgecaster.systems import damage_policy as damage_policy_system
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import footprints as footprints_system


# ---------------------------------------------------------------------------
# Prototype helpers (YAML content layer: entities + enemies merged into one pool)
# ---------------------------------------------------------------------------

def get_prototype(proto_id: str) -> dict:
    """
    Return the RESOLVED prototype dict (entities + enemies) for a given id,
    with inheritance applied.
    """
    if not proto_id:
        return {}
    return prototypes.resolve_proto(str(proto_id))


def _lookup_proto_id_for_entity(ent: Any) -> str | None:
    """
    Best-effort guess of which YAML prototype this runtime entity came from.

    Priority:
    1) Explicit template id in tags (template_id).
    2) Item-type tag for item entities (item_type).
    3) ent.kind (for actors/enemies, this is usually the enemy template id).
    4) ent.id as a last resort.
    """
    tags = getattr(ent, "tags", None) or {}

    candidates = [
        tags.get("template_id"),     # e.g. enemy factory could stash this
        tags.get("item_type"),       # e.g. "strawberry", "destabilizer", etc.
        getattr(ent, "kind", None),  # e.g. "imp", "mana_viper", "human_base"
        getattr(ent, "id", None),    # absolute last resort
    ]

    for cid in candidates:
        if cid:
            return str(cid)

    return None


def resolve_entity_description(ent: Any) -> str | None:
    """
    1. If ent.description exists, use that.
    2. Else infer proto id and use resolved prototype chain.
    """
    direct = getattr(ent, "description", None)
    if direct:
        return str(direct)

    proto_id = _lookup_proto_id_for_entity(ent)
    if not proto_id:
        return None

    proto = prototypes.resolve_proto(proto_id)
    desc = proto.get("description")
    return str(desc) if desc else None


def describe_entity_for_look(ent: Any) -> Dict[str, Any]:
    """Return name, glyph, color, description, and faction standings for an entity."""

    proto_id = _lookup_proto_id_for_entity(ent)
    proto = prototypes.resolve_proto(proto_id) if proto_id else {}

    name = (
        getattr(ent, "name", None)
        or getattr(ent, "label", None)
        or proto.get("name")
        or "something"
    )

    glyph = getattr(ent, "glyph", None) or proto.get("glyph") or "?"
    color = getattr(ent, "color", None) or proto.get("color") or (255, 255, 255)

    desc = resolve_entity_description(ent) or "You see nothing remarkable about it."
    hp_text = None
    try:
        tags = getattr(ent, "tags", {}) or {}
        if getattr(ent, "show_exact_hp", False) or tags.get("show_exact_hp"):
            stats = getattr(ent, "stats", None)
            if stats and hasattr(stats, "hp") and hasattr(stats, "max_hp"):
                hp_text = f"HP: {int(stats.hp)}/{int(stats.max_hp)}"
    except Exception:
        hp_text = None

    # Get faction standings for display (e.g., for legendaries)
    faction_lines = []
    try:
        faction_lines = reputation_system.describe_faction_standings(ent)
    except Exception:
        faction_lines = []

    # Append faction standings to the description if present
    full_desc = str(desc)
    if faction_lines:
        full_desc += "\n\nFaction Standings:\n" + "\n".join(faction_lines)

    info = {
        "name": str(name),
        "glyph": str(glyph),
        "color": tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
        "description": full_desc,
    }
    if hp_text:
        info["hp_text"] = hp_text
    if faction_lines:
        info["faction_standings"] = faction_lines
    return info


# ---------------------------------------------------------------------------
# Core action model
# ---------------------------------------------------------------------------

SpeedTag = Literal["instant", "fast", "slow"]
SpeedType = Union[SpeedTag, int]


class ActionFunc(Protocol):
    """
    Signature for action implementations.

    game: the Game instance (or a mock in tests)
    actor_id: id of the acting entity
    **kwargs: action-specific parameters (e.g. dx, dy for movement)
    """
    def __call__(self, game: Any, actor_id: str, **kwargs: Any) -> None: ...


@dataclass(frozen=True)
class ConfirmPrompt:
    """A prompt shown before an action executes."""

    title: str
    body: str
    choices: list[str]
    proceed_index: int = 1


class ConfirmFunc(Protocol):
    """Return a ConfirmPrompt if the action should ask the player first."""

    def __call__(self, game: Any, actor_id: str, **kwargs: Any) -> ConfirmPrompt | None: ...


@dataclass
class TargetingSpec:
    kind: str | None = None              # "tile" or "vertex"
    mode: str | None = None              # "terminus" or "aim"
    max_range: int | None = None
    radius_param: str | None = None      # e.g. "radius" for activate_all
    neighbor_depth_param: str | None = None  # e.g. "neighbor_depth" for activate_seed
    requires_confirm: bool = True        # reserved for later (auto-fire on click, etc.)


@dataclass
class ActionDef:
    name: str
    label: str
    speed: SpeedType
    func: ActionFunc
    # Whether this action is eligible to appear in the player-facing
    # ability bar when owned by the current host actor.
    show_in_bar: bool = False
    cooldown_ticks: int = 0
    # Targeting metadata (None = immediate, non-targeted action).
    targeting: TargetingSpec | None = None
    # Optional prompt hook (e.g. confirmations for dangerous actions).
    confirm: ConfirmFunc | None = None









# Global registry of all actions by name.
_action_registry: Dict[str, ActionDef] = {}



# ---------------------------------------------------------------------------
# UI metadata (ability bar icons, sub-buttons, etc.)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubButtonMeta:
    """Metadata for a small sub-button attached to an action in the ability bar.

    This keeps all "what this button *means*" information close to the
    action definition layer, while the UI decides *how* to draw it.
    """
    id: str               # stable identifier, e.g. "radius_plus"
    icon: str             # short text/icon rendered in the tiny button ("+", "-", "⚙"...)
    kind: str             # semantic kind, e.g. "param_delta", "open_config"
    param_key: str | None = None   # which parameter this manipulates (if any)
    delta: int | None = None       # integer delta for param_delta buttons (if any)


# Mapping from action name -> list of sub-button metadata.
# The UI is free to ignore this or to lay these out however it likes.
ACTION_SUB_BUTTONS: Dict[str, list[SubButtonMeta]] = {
    # Radius-based activator gets +/- for the radius, plus a gear for config.
    "activate_all": [
        SubButtonMeta(
            id="radius_minus",
            icon="-",
            kind="param_delta",
            param_key="radius",
            delta=-1,
        ),
        SubButtonMeta(
            id="radius_plus",
            icon="+",
            kind="param_delta",
            param_key="radius",
            delta=1,
        ),
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    # Seed activator just exposes its config for now.
    "activate_seed": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    # Generators (and custom patterns) expose their config.
    "subdivide": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "extend": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "koch": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "branch": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "zigzag": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "custom": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "chakra": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "place_rune_anchor": [
        SubButtonMeta(
            id="config",
            # Same "gear" glyph used elsewhere in the ability bar.
            icon="ƒsT",
            kind="open_config",
        ),
    ],
    "polygon": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
    "star": [
        SubButtonMeta(
            id="config",
            icon="⚙",
            kind="open_config",
        ),
    ],
}


def action_sub_buttons(action_name: str) -> list[SubButtonMeta]:
    """Return UI sub-button metadata for a given action name (may be empty)."""
    return ACTION_SUB_BUTTONS.get(action_name, [])




# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def register_action(
    name: str,
    *,
    label: str,
    speed: SpeedTag = "fast",
    show_in_bar: bool = False,
    cooldown_ticks: int = 0,
    targeting: TargetingSpec | None = None,
    confirm: ConfirmFunc | None = None,
) -> Callable[[ActionFunc], ActionFunc]:
    """
    Decorator to register a function as an Action.
    """
    def decorator(func: ActionFunc) -> ActionFunc:
        # Dev convenience: allow override if hot–reloading.
        _action_registry[name] = ActionDef(
            name=name,
            label=label,
            speed=speed,
            func=func,
            show_in_bar=show_in_bar,
            cooldown_ticks=cooldown_ticks,
            targeting=targeting,
            confirm=confirm,
        )
        return func

    return decorator



def get_action(name: str) -> ActionDef:
    """
    Look up an action by name.

    Raises KeyError if the action is unknown.
    """
    # On-demand aliases for custom_N -> same base but passing through the suffix.
    if name.startswith("custom_") and "custom" in _action_registry:
        if name not in _action_registry:
            base = _action_registry["custom"]

            def _custom_n_action(game: Any, actor_id: str, **kwargs: Any) -> None:
                if hasattr(game, "act_fractal"):
                    game.act_fractal(actor_id, name)

            _action_registry[name] = ActionDef(
                name=name,
                label=base.label,
                speed=base.speed,
                func=_custom_n_action,
                show_in_bar=base.show_in_bar,
                cooldown_ticks=base.cooldown_ticks,
                targeting=base.targeting,
                confirm=base.confirm,
            )
        return _action_registry[name]

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

    if isinstance(tag, int):
        return max(0, int(tag))
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
    # Status: rooted prevents movement.
    try:
        actor = level.actors.get(actor_id)
        if actor and game._has_status(actor, "rooted"):
            if actor_id == getattr(game, "player_id", ""):
                game.log.add("You are rooted and cannot move!")
            return
    except Exception:
        pass
    game._handle_move_or_attack(level, actor_id, dx, dy)


@register_action("brute_move", label="Move", speed="slow")
def _action_brute_move(game: Any, actor_id: str, **kwargs: Any) -> None:
    """A slower movement/attack action used by heavy enemies (e.g. Shackled Brutes)."""
    dx = int(kwargs.get("dx", 0))
    dy = int(kwargs.get("dy", 0))
    if dx == 0 and dy == 0:
        return

    if not hasattr(game, "_level") or not hasattr(game, "_handle_move_or_attack"):
        actor = getattr(game, "actors", {}).get(actor_id)
        if actor is None or not hasattr(actor, "pos"):
            return
        x, y = actor.pos
        actor.pos = (x + dx, y + dy)
        return

    level = game._level()
    # Status: rooted prevents movement.
    try:
        actor = level.actors.get(actor_id)
        if actor and game._has_status(actor, "rooted"):
            return
    except Exception:
        pass
    game._handle_move_or_attack(level, actor_id, dx, dy)


@register_action("yawp", label="Yawp", speed="instant")
def _debug_yawp(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Simple sandbox action used to test the action system.

    When invoked, the acting entity emits a mighty yawp into the game log.
    It also rotates the current scene's visual profile by 90 degrees as
    a visual test (typically the dungeon scene).
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

    # Screen shake test: impulse
    mgr = getattr(game, "scene_manager", None)
    if mgr and hasattr(mgr, "renderer"):
        rnd = mgr.renderer
        if hasattr(rnd, "apply_shake"):
            rnd.apply_shake(amplitude=18.0, duration_ms=350)




@register_action("wait", label="Wait", speed="fast")
def _action_wait(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Do absolutely nothing for one 'fast' tick.

    Useful for AI or player 'rest' behaviour, and keeps timing unified
    with other actions via action_delay.
    """
    # Intentionally empty: all the work is done by the scheduler via delay.
    return


@register_action("imp_taunt", label="Taunt", speed="fast", show_in_bar=True)
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
        "parades about",
        "snickers",
        "catcalls",
        "waves his rump alluringly",
        "screeches",
        "crows",
        "barks",
        
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
        "I hate you!!! A lot!!!",
        "I hope you get ambushed by an alligator.",
        "Hey look at this guy over here, Mr. Big Deal Fractal guy, ooh la la he's a fancy fucker ain't he?",
        "Berryfucker!",
        "Your MOM is self-similar!",
        "You know what would fit perfectly around that pinky slot... my asshole!",
        "You know what would fit wonderfully around that first knuckle slot slot... my asshole!",
        "You know what would fit snugly around that second knuckle slot slot... my asshole!",
        "You know what would fit beautifully around that third knuckle slot slot... my asshole!",
        
        
    ]

    verb = random.choice(VERBS)
    line = random.choice(TAUNTS)

    if hasattr(game, "log") and hasattr(game.log, "add"):
        game.log.add(f"The {imp_name} {verb}: \"{line}\"")
    else:
        print(f"The {imp_name} {verb}: \"{line}\"")


@register_action("war_drum", label="War Drum", speed="slow", cooldown_ticks=80)
def _action_war_drum(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Buff nearby creatures that are hostile to the player.

    This applies an additive action-speed modifier (tick offset) for a fixed
    duration. The scheduler consumes that tag when computing action delays.

    Tuning is data-driven via the actor's YAML tags (enemies.yaml):
    - drum_radius: Manhattan radius around the drummer
    - drum_duration: duration in ticks
    - drum_tick_reduction: how many ticks to subtract from action costs (min 1)
    """
    if not hasattr(game, "_level"):
        return
    level = game._level()
    actor = getattr(getattr(level, "actors", {}), "get", lambda *_: None)(actor_id)
    if actor is None:
        return

    # Read optional tuning params from the actor's YAML tags.
    raw_tags = getattr(actor, "tags", None) or {}
    yaml_tags = raw_tags.get("tags", {})
    if not isinstance(yaml_tags, dict):
        yaml_tags = {}

    try:
        radius = int(yaml_tags.get("drum_radius", 6) or 6)
    except Exception:
        radius = 6
    try:
        duration = int(yaml_tags.get("drum_duration", 40) or 40)
    except Exception:
        duration = 40
    try:
        reduction = int(yaml_tags.get("drum_tick_reduction", 1) or 1)
    except Exception:
        reduction = 1

    radius = max(0, radius)
    duration = max(0, duration)
    reduction = max(0, reduction)
    if radius <= 0 or duration <= 0 or reduction <= 0:
        return

    # Who is "hostile"? Use the same reputation-driven hostility logic as AI.
    player_id = getattr(game, "player_id", None)
    player = getattr(getattr(level, "actors", {}), "get", lambda *_: None)(player_id)
    if player is None:
        return

    tick_offset = -abs(reduction)

    affected = 0
    for other in list(getattr(level, "actors", {}).values()):
        if other is None or not getattr(other, "alive", True):
            continue

        try:
            if not reputation_system.is_hostile(game, other, player):
                continue
        except Exception:
            # Fallback: treat differing factions as hostile.
            if getattr(other, "faction", None) == getattr(player, "faction", None):
                continue

        if _entity_manhattan_distance_local(actor, other) > radius:
            continue

        otags = getattr(other, "tags", None) or {}
        try:
            existing_offset = int(otags.get("action_tick_offset", 0))
        except Exception:
            existing_offset = 0
        # Keep the most beneficial (most negative) offset if multiple sources exist.
        if existing_offset == 0 or tick_offset < existing_offset:
            otags["action_tick_offset"] = tick_offset

        try:
            existing_ticks = int(otags.get("action_tick_offset_ticks", 0))
        except Exception:
            existing_ticks = 0
        otags["action_tick_offset_ticks"] = max(existing_ticks, duration)

        try:
            other.tags = otags
        except Exception:
            pass

        affected += 1

    # Single log line (avoid spam).
    try:
        if hasattr(game, "log") and hasattr(game.log, "add"):
            if affected <= 1:
                game.log.add(f"{actor.name} beats a war drum.")
            else:
                game.log.add(f"{actor.name} beats a war drum, spurring {affected} foes onward.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fractal / rune actions
# ---------------------------------------------------------------------------

@register_action(
    "place",
    label="Place",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(
        kind="tile",
        mode="terminus",
        # max_range could later be wired to a param if desired
    ),
)
def _action_place(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Enter 'place terminus' mode for the acting entity.

    Right now this is effectively a player-only thing and does not
    consume any extra parameters; we just delegate to Game.
    """
    # This is still a setup hook; the actual placement occurs when
    # TargetMode confirms and calls Game.try_place_terminus.
    if hasattr(game, "begin_place_mode"):
        game.begin_place_mode()



@register_action("subdivide", label="Subdivide", speed="fast", show_in_bar=True)
def _action_subdivide(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'subdivide' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "subdivide")


@register_action("extend", label="Extend", speed="fast", show_in_bar=True)
def _action_extend(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'extend' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "extend")


@register_action("koch", label="Koch", speed="fast", show_in_bar=True)
def _action_koch(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'koch' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "koch")


@register_action("branch", label="Branch", speed="fast", show_in_bar=True)
def _action_branch(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'branch' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "branch")


@register_action("zigzag", label="Zigzag", speed="fast", show_in_bar=True)
def _action_zigzag(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the 'zigzag' fractal generator to the current rune pattern.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "zigzag")


@register_action("cultivate", label="Cultivate", speed="fast", show_in_bar=True)
def _action_cultivate(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Apply the Gardener's custom branch design as a fractal generator."""
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "cultivate")


@register_action("custom", label="Custom", speed="fast", show_in_bar=True)
def _action_custom(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the base 'custom' fractal pattern (index 0).

    For extra saved patterns we use action names like 'custom_1',
    'custom_2', etc. and pass the suffix through to Game.act_fractal.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "custom")


@register_action("chakra", label="Chakra", speed="fast", show_in_bar=True)
def _action_chakra(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Generate a pattern from the actor's active chakras.

    Each active chakra becomes a vertex, and body tree edges between
    active chakras become pattern edges. This creates a seed pattern
    based on body geometry that can then be fractally iterated.
    """
    if hasattr(game, "act_chakra"):
        game.act_chakra(actor_id)


@register_action(
    "slash",
    label="Slash",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(kind="tile", mode="aim"),
)
def _action_slash(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Blade melee: short-range single-target strike."""
    target_pos = kwargs.get("target_tile")
    if hasattr(game, "act_blade_attack"):
        game.act_blade_attack(actor_id, "slash", target_pos=target_pos)


@register_action(
    "thrust",
    label="Thrust",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(kind="tile", mode="aim"),
)
def _action_thrust(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Blade melee: longer line attack that favors the first target hit."""
    target_pos = kwargs.get("target_tile")
    if hasattr(game, "act_blade_attack"):
        game.act_blade_attack(actor_id, "thrust", target_pos=target_pos)


@register_action(
    "cleave",
    label="Cleave",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(kind="tile", mode="aim"),
)
def _action_cleave(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Blade melee: front-arc sweep that can hit multiple hostiles."""
    target_pos = kwargs.get("target_tile")
    if hasattr(game, "act_blade_attack"):
        game.act_blade_attack(actor_id, "cleave", target_pos=target_pos)


@register_action(
    "throwing_knife",
    label="Throwing Knife",
    speed="fast",
    show_in_bar=True,
    # Cooldown is set dynamically in blade_runtime (RES scaling), so leave static as 0.
    cooldown_ticks=0,
    targeting=TargetingSpec(kind="tile", mode="aim"),
)
def _action_throwing_knife(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Blade ranged strike: throw one visible spinning runeblade copy."""
    target_pos = kwargs.get("target_tile")
    if hasattr(game, "act_throwing_knife"):
        game.act_throwing_knife(actor_id, target_pos=target_pos)


@register_action(
    "mirror_blade",
    label="Mirror Blade",
    speed="fast",
    show_in_bar=True,
    cooldown_ticks=300,
    targeting=TargetingSpec(kind="tile", mode="aim"),
)
def _action_mirror_blade(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Summon a mirror clone of yourself that fights for 100 heartbeats."""
    target_pos = kwargs.get("target_tile")
    if hasattr(game, "act_mirror_blade"):
        game.act_mirror_blade(actor_id, target_pos=target_pos)


@register_action(
    "wind_rush",
    label="Wind Rush",
    speed=5,  # fixed travel/action time in ticks
    show_in_bar=True,
    cooldown_ticks=42,
    targeting=TargetingSpec(
        kind="vertex",
        mode="aim",
    ),
)
def _action_wind_rush(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Dash to a selected rune vertex and strike enemies along the path."""
    target_vertex = kwargs.get("hover_vertex")
    if target_vertex is None:
        target_vertex = kwargs.get("target_vertex")
    if hasattr(game, "act_wind_rush"):
        game.act_wind_rush(actor_id, target_vertex=target_vertex)


@register_action("energy_kick", label="Energy Kick", speed="fast", show_in_bar=True, cooldown_ticks=18)
def _action_energy_kick(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Pulse from foot-lineage chakra vertices and damage nearby entities.
    """
    if hasattr(game, "act_energy_kick"):
        game.act_energy_kick(actor_id)


@register_action("palm_burst", label="Palm Burst", speed="fast", show_in_bar=True, cooldown_ticks=14)
def _action_palm_burst(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Pulse damage from hand/palm/finger-lineage chakra vertices."""
    if hasattr(game, "act_palm_burst"):
        game.act_palm_burst(actor_id)


@register_action("mirror_strike", label="Mirror Strike", speed="fast", show_in_bar=True, cooldown_ticks=22)
def _action_mirror_strike(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Strike from mirrored chakra pairs, damaging enemies near paired endpoints."""
    if hasattr(game, "act_mirror_strike"):
        game.act_mirror_strike(actor_id)


@register_action("aggressive_vines", label="Aggressive Vines", speed="fast", show_in_bar=True, cooldown_ticks=36)
def _action_aggressive_vines(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Grow free-form tendrils from rune edges near enemies."""
    if hasattr(game, "act_aggressive_vines"):
        game.act_aggressive_vines(actor_id)


@register_action("choking_vines", label="Choking Vines", speed="fast", show_in_bar=True, cooldown_ticks=34)
def _action_choking_vines(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Grow constricting rune branches from edges toward nearby enemies."""
    if hasattr(game, "act_choking_vines"):
        game.act_choking_vines(actor_id)


@register_action("polygon", label="Polygon", speed="fast", show_in_bar=True)
def _action_polygon(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Place a regular polygon pattern centered on the player.

    Clears any existing pattern and creates a new polygon with configurable
    number of sides and radius. The root/terminus vertex is directly north.
    """
    if hasattr(game, "act_polygon"):
        game.act_polygon(actor_id)


@register_action("star", label="Star", speed="fast", show_in_bar=True)
def _action_star(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Place a star pattern centered on the player.

    Clears any existing pattern and creates a new star with configurable
    number of points, outer radius, and inner radius. The first point
    (root/terminus) is directly north.
    """
    if hasattr(game, "act_star"):
        game.act_star(actor_id)


@register_action("destabilize", label="Destabilize", speed="fast", show_in_bar=True, cooldown_ticks=15)
def _action_destabilize(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Teleport randomly within 10 tiles; risky HP backlash."""
    if hasattr(game, "act_destabilize"):
        game.act_destabilize(actor_id)


@register_action("rainbow_edges", label="Rainbow", speed="fast", show_in_bar=True)
def _action_rainbow_edges(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Color current pattern edges in ROYGBIV order starting from the root.
    """
    if pattern_colors and hasattr(pattern_colors, "apply_rainbow_edges"):
        pattern_colors.apply_rainbow_edges(game)


@register_action("verdant_edges", label="Verdant", speed="fast", show_in_bar=True)
def _action_verdant_edges(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Color edges greener with depth: nearest are white, furthest are fully green.
    """
    if pattern_colors and hasattr(pattern_colors, "apply_depth_green_edges"):
        pattern_colors.apply_depth_green_edges(game)


@register_action("corrosive_melt", label="Corrosive Melt", speed="fast", show_in_bar=True)
def _action_corrosive_melt(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Activate acidic mode on the pattern. Edges that touch enemies dissolve
    and deal damage based on their green intensity.
    """
    if hasattr(game, "act_corrosive_melt"):
        game.act_corrosive_melt(actor_id)


@register_action("start_fern", label="Fern Growth", speed="fast", show_in_bar=True)
def _action_start_fern(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Toggle Barnsley fern auto-growth on the current pattern.

    When active, the pattern grows outward from existing vertices using
    the classic Barnsley fern IFS transforms. Growth consumes coherence
    and oldest vertices are pruned when over capacity.
    """
    if hasattr(game, "act_start_fern"):
        game.act_start_fern(actor_id)


@register_action("winter_hue", label="Winter Hue", speed=5, show_in_bar=True)
def _action_winter_hue(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Color vertices based on local density (white → deep blue) and gradient edges between them.
    """
    if pattern_colors and hasattr(pattern_colors, "apply_winter_hue"):
        pattern_colors.apply_winter_hue(game)


def _confirm_self_damage_ignite(game: Any, actor_id: str, **kwargs: Any) -> ConfirmPrompt | None:
    """
    Prompt if Ignite would hit the acting player (directly or indirectly).

    This uses the same coarse tile model as Ignite itself: edges are rasterized
    into tiles and indirect damage affects 8-neighbors.
    """
    if actor_id != getattr(game, "player_id", None):
        return None
    if not hasattr(game, "_level"):
        return None

    try:
        level = game._level()
        actor = level.actors.get(actor_id)
        pattern = getattr(level, "pattern", None)
        anchor = getattr(level, "pattern_anchor", None)
    except Exception:
        return None

    if actor is None or pattern is None or anchor is None:
        return None

    px, py = getattr(actor, "pos", (None, None))
    if px is None or py is None:
        return None

    edges = getattr(pattern, "edges", None) or []
    verts = getattr(pattern, "vertices", None) or []
    if not edges or not verts:
        return None

    edge_colors = getattr(pattern, "edge_colors", {}) or {}

    def normalize_edge_key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a <= b else (b, a)

    def line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        points: list[tuple[int, int]] = []
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            points.append((x, y))
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
        return points

    # Quick bounds reject: if the actor isn't even near the rune bounds,
    # they can't be affected.
    try:
        min_x = min(v.pos[0] + anchor[0] for v in verts)
        max_x = max(v.pos[0] + anchor[0] for v in verts)
        min_y = min(v.pos[1] + anchor[1] for v in verts)
        max_y = max(v.pos[1] + anchor[1] for v in verts)
        if px < min_x - 2 or px > max_x + 2 or py < min_y - 2 or py > max_y + 2:
            return None
    except Exception:
        # Be conservative if bounds calc fails.
        pass

    for edge in edges:
        a = getattr(edge, "a", None)
        b = getattr(edge, "b", None)
        if a is None or b is None:
            continue

        col = edge_colors.get(normalize_edge_key(int(a), int(b)))
        if col is None and isinstance(getattr(edge, "color", None), (list, tuple)):
            col = edge.color
        if not isinstance(col, (list, tuple)) or len(col) < 3:
            continue

        try:
            r, g, bl = int(col[0]), int(col[1]), int(col[2])
        except Exception:
            continue

        if max(0, r - max(g, bl)) <= 0:
            continue

        try:
            va = verts[int(a)].pos
            vb = verts[int(b)].pos
        except Exception:
            continue

        x0 = int(round(va[0] + anchor[0]))
        y0 = int(round(va[1] + anchor[1]))
        x1 = int(round(vb[0] + anchor[0]))
        y1 = int(round(vb[1] + anchor[1]))

        # Direct tiles and their neighbors (indirect) both cause self-damage.
        for tx, ty in line_points(x0, y0, x1, y1):
            if max(abs(tx - px), abs(ty - py)) <= 1:
                return ConfirmPrompt(
                    title="Confirm Ignite",
                    body="Ignite will damage you. Proceed?",
                    choices=["Cancel", "Cast anyway"],
                    proceed_index=1,
                )

    return None


def _confirm_self_damage_freeze(game: Any, actor_id: str, **kwargs: Any) -> ConfirmPrompt | None:
    """Prompt if Freeze would deal damage to the acting player."""
    if actor_id != getattr(game, "player_id", None):
        return None
    if not hasattr(game, "_level"):
        return None

    try:
        level = game._level()
        actor = level.actors.get(actor_id)
        pattern = getattr(level, "pattern", None)
        anchor = getattr(level, "pattern_anchor", None)
    except Exception:
        return None

    if actor is None or pattern is None or anchor is None:
        return None

    px, py = getattr(actor, "pos", (None, None))
    if px is None or py is None:
        return None

    verts = getattr(pattern, "vertices", None) or []
    vcolors = getattr(pattern, "vertex_colors", None) or []
    if not verts or not vcolors:
        return None

    bsum = 0.0
    for i, v in enumerate(verts):
        try:
            vx = v.pos[0] + anchor[0]
            vy = v.pos[1] + anchor[1]
        except Exception:
            continue
        if int(round(vx)) != px or int(round(vy)) != py:
            continue
        try:
            col = vcolors[i]
            r, g, b = float(col[0]), float(col[1]), float(col[2])
        except Exception:
            continue
        bsum += max(0.0, b - max(r, g))

    if bsum <= 0.0:
        return None

    dmg_scale = getattr(game, "get_param_value", lambda a, k: 0.1)("freeze", "damage_scale") or 0.1
    try:
        dmg_int = int(max(0.0, bsum * float(dmg_scale)))
    except Exception:
        dmg_int = 0

    if dmg_int <= 0:
        return None

    return ConfirmPrompt(
        title="Confirm Freeze",
        body=f"Freeze will deal {dmg_int} damage to you. Proceed?",
        choices=["Cancel", "Cast anyway"],
        proceed_index=1,
    )


@register_action(
    "ignite",
    label="Ignite",
    speed="fast",
    show_in_bar=True,
    cooldown_ticks=50,
    confirm=_confirm_self_damage_ignite,
)
def _action_ignite(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Ignite red edges for 30 ticks with decaying damage.
    """
    if hasattr(game, "act_ignite"):
        game.act_ignite(actor_id)


@register_action("regrow", label="Regrow", speed="fast", show_in_bar=True, cooldown_ticks=50)
def _action_regrow(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Heal along green edges for 30 ticks with decaying strength.
    """
    if hasattr(game, "act_regrow"):
        game.act_regrow(actor_id)


@register_action("sparkle", label="Sparkle", speed="fast", show_in_bar=True)
def _action_sparkle(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Sparkle a pseudo-random subset of pattern vertices and deal damage on-hit.

    - No targeting: affects the whole pattern.
    - Does not damage the caster.
    - Damage stacks per vertex that hits the same tile.
    - Charges (from the wand) are the only limiter.
    """
    if not hasattr(game, "_level"):
        return
    try:
        level = game._level()
    except Exception:
        return

    # Project vertices into world space.
    try:
        if hasattr(game, "projected_vertices"):
            verts_world = list(game.projected_vertices())
        else:
            verts_world = []
    except Exception:
        verts_world = []

    if not verts_world:
        if actor_id == getattr(game, "player_id", None):
            try:
                game.log.add("No pattern to sparkle.")
            except Exception:
                pass
        return

    rng = getattr(game, "rng", None)
    if rng is None:
        import random

        rng = random.Random()

    # Choose a pseudo-random subset of vertices to "sparkle".
    # Tunable: keep probability low so large patterns don't always fully light.
    select_prob = 0.35
    selected = [v for v in verts_world if float(rng.random()) < select_prob]
    if not selected:
        try:
            idx = int(rng.randint(0, len(verts_world) - 1))
        except Exception:
            idx = 0
        selected = [verts_world[idx]]

    # Store a time-based VFX state for the renderer (pure visuals).
    #
    # This is intentionally keyed off real-time (monotonic clock) so the
    # "firecracker / sequin" effect animates even when the game is waiting
    # for player input (i.e., no game-tick advancement).
    try:
        seed = int(rng.randint(0, 2**31 - 1))
    except Exception:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    try:
        edges = [(int(e.a), int(e.b)) for e in getattr(getattr(level, "pattern", None), "edges", []) or []]
    except Exception:
        edges = []

    try:
        eid = game._new_id()
        from edgecaster.state.entities import Entity
        
        cx = sum(x for x, y in verts_world) / len(verts_world)
        cy = sum(y for x, y in verts_world) / len(verts_world)
        pos = (int(cx), int(cy))
        
        ent = Entity(
            id=eid,
            name="Sparkle",
            pos=pos,
            abs_pos=game.abs_from_zone_local(level.coord, pos) if hasattr(game, "abs_from_zone_local") else pos,
            glyph="*",
            color=(255, 245, 180),
            kind="sparkle_effect",
            render_layer=0,
            tags={
                "t0": float(time.monotonic()),
                "duration_s": 2.0,
                "spark_duration_s": 1.0,
                "seed": seed,
                "verts": [(float(x), float(y)) for (x, y) in verts_world],
                "edges": edges,
            }
        )
        level.entities[eid] = ent
        try:
            from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
            entity_graph_ops_system.register_entity(game, ent, lod_state="expanded")
        except Exception:
            pass
            
        def remove_sparkle():
            try:
                entity_ops_system.remove_entity(level, eid)
            except Exception:
                pass
        import edgecaster.systems.scheduling as scheduling
        scheduling.schedule(game, level, int(2.0 / getattr(game.cfg, "action_time_fast", 10) * 10), remove_sparkle)
    except Exception:
        pass

    # Count how many sparkles hit each tile.
    tile_hits: dict[tuple[int, int], int] = {}
    for vx, vy in selected:
        tx = int(round(float(vx)))
        ty = int(round(float(vy)))
        tile_hits[(tx, ty)] = tile_hits.get((tx, ty), 0) + 1

    damage_per_hit = 1
    caster_is_player = actor_id == getattr(game, "player_id", None)

    # Centralized targeting policy:
    # Sparkle currently affects everything with HP except the caster.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=True,
        include_friendly=True,
        include_environment=True,
    )

    hit_any = False
    for tid, obj in damage_policy_system.iter_damage_targets(
        game,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=True,
    ):
        pos = getattr(obj, "pos", None)
        if not pos:
            continue
        tx = int(round(float(pos[0])))
        ty = int(round(float(pos[1])))
        hits = int(tile_hits.get((tx, ty), 0))
        if hits <= 0:
            continue

        dmg = hits * damage_per_hit
        stats = getattr(obj, "stats", None)
        if stats is None or not hasattr(stats, "hp"):
            continue

        try:
            stats.hp -= dmg
            if hasattr(stats, "clamp"):
                stats.clamp()
        except Exception:
            continue

        hit_any = True
        if caster_is_player:
            try:
                name = getattr(obj, "name", None) or "something"
                game.log.add(f"Sparkles strike {name} for {dmg}.")
            except Exception:
                pass

        # Actors run canonical death handling; HP-bearing non-actors are removed.
        if tid in getattr(level, "actors", {}):
            try:
                if int(getattr(stats, "hp", 0)) <= 0:
                    if hasattr(game, "_kill_actor"):
                        game._kill_actor(
                            level,
                            obj,
                            killer_id=actor_id,
                            killer_is_player=caster_is_player,
                        )
            except Exception:
                pass
        elif int(getattr(stats, "hp", 0)) <= 0 and tid in getattr(level, "entities", {}):
            try:
                if hasattr(game, "_remove_entity"):
                    game._remove_entity(level, obj, reason="destroyed_sparkle")
                else:
                    del level.entities[tid]
            except Exception:
                pass

    if caster_is_player and not hit_any:
        try:
            game.log.add("The wand crackles, but finds no targets.")
        except Exception:
            pass


@register_action("freeze", label="Freeze", speed="fast", show_in_bar=True, confirm=_confirm_self_damage_freeze)
def _action_freeze(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Deal damage and apply slowing based on pattern blueness across all pattern tiles.
    """
    if hasattr(game, "act_freeze"):
        game.act_freeze(actor_id)


@register_action("lightning", label="Lightning", speed="fast", show_in_bar=True, cooldown_ticks=80)
def _action_lightning(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Strike all creatures touching the squares occupied by the current pattern.

    - High mana cost, long cooldown.
    - Rolls 2dN total damage, where N is the number of vertices in the pattern.
    - Distributes damage evenly (rounding up) across all hit creatures.
    """
    if not hasattr(game, "_level"):
        return

    try:
        level = game._level()
    except Exception:
        return

    actor = None
    try:
        actor = getattr(level, "actors", {}).get(actor_id)
    except Exception:
        actor = None

    if actor is None:
        return

    # Mana gate (intrinsic ability)
    mana_cost = 50
    try:
        if getattr(actor, "stats", None) is None:
            return
        if actor.stats.mana < mana_cost:
            if actor_id == getattr(game, "player_id", None):
                game.log.add("Not enough mana to call lightning.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        # If stats/mana aren't available, fail silently for now.
        return

    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
        if actor_id == getattr(game, "player_id", None):
            try:
                game.log.add("No pattern to conduct lightning.")
            except Exception:
                pass
        return

    try:
        from edgecaster.patterns.activation import project_vertices
    except Exception:
        return

    try:
        verts_world = project_vertices(pattern, anchor)
    except Exception:
        verts_world = []

    if not verts_world:
        return

    # Basic activation glow (reuses existing post-activation overlay).
    try:
        level.activation_points = list(verts_world)
        ttl = int(getattr(getattr(game, "cfg", None), "pattern_overlay_ttl", 30))
        level.activation_ttl = max(ttl, 1)
    except Exception:
        pass

    def line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        """Bresenham line (integer grid points)."""
        points: list[tuple[int, int]] = []
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            points.append((x, y))
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy
        return points

    # Tiles touched by the pattern (edges + vertices).
    touched: set[tuple[int, int]] = set()
    for vx, vy in verts_world:
        touched.add((int(round(float(vx))), int(round(float(vy)))))

    for e in getattr(pattern, "edges", []) or []:
        try:
            a = verts_world[int(e.a)]
            b = verts_world[int(e.b)]
        except Exception:
            continue
        touched.update(
            line_points(
                int(round(float(a[0]))),
                int(round(float(a[1]))),
                int(round(float(b[0]))),
                int(round(float(b[1]))),
            )
        )

    if not touched:
        return

    # Centralized policy:
    # Lightning can hit all actors except the caster (hostile/neutral/friendly).
    # It currently does not hit environment entities.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=True,
        include_friendly=True,
        include_environment=False,
    )
    targets = []
    for _tid, target in damage_policy_system.iter_damage_targets(
        game,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        try:
            if not getattr(target, "alive", True):
                continue
            tx, ty = getattr(target, "pos", (None, None))
            if tx is None or ty is None:
                continue
            if (int(tx), int(ty)) in touched:
                targets.append(target)
        except Exception:
            continue

    # Roll 2d20 total damage.
    rng = getattr(game, "rng", None)
    try:
        import random

        if rng is None:
            rng = random.Random()
    except Exception:
        pass

    # Store a time-based VFX state for the renderer (pure visuals).
    #
    # This is intentionally keyed off real-time (monotonic clock) so the bolt
    # animation can play even while the game is waiting for player input.
    try:
        seed = int(rng.randint(0, 2**31 - 1))
    except Exception:
        seed = int(time.time() * 1000) & 0x7FFFFFFF

    try:
        caster_pos = getattr(actor, "pos", (None, None))
        caster_tile = (int(caster_pos[0]), int(caster_pos[1]))
    except Exception:
        caster_tile = None

    try:
        anchor_tile = (int(anchor[0]), int(anchor[1])) if anchor is not None else None
    except Exception:
        anchor_tile = None

    start_tile = None
    try:
        if caster_tile is not None and caster_tile in touched:
            start_tile = caster_tile
        elif anchor_tile is not None and anchor_tile in touched:
            start_tile = anchor_tile
        else:
            start_tile = next(iter(touched))
    except Exception:
        start_tile = None

    try:
        eid = game._new_id()
        from edgecaster.state.entities import Entity
        
        pos = start_tile if start_tile else (0, 0)
        
        ent = Entity(
            id=eid,
            name="Lightning",
            pos=pos,
            abs_pos=game.abs_from_zone_local(level.coord, pos) if hasattr(game, "abs_from_zone_local") else pos,
            glyph="*",
            color=(185, 250, 255),
            kind="lightning_effect",
            render_layer=0,
            tags={
                "t0": float(time.monotonic()),
                "duration_s": 0.26,
                "flash_s": 0.08,
                "seed": seed,
                "mask_tiles": sorted(touched, key=lambda p: (p[1], p[0])),
                "start_tile": start_tile,
                "target_tiles": [
                    (int(getattr(t, "pos", (0, 0))[0]), int(getattr(t, "pos", (0, 0))[1]))
                    for t in targets
                ],
                "verts": [(float(x), float(y)) for (x, y) in verts_world],
                "edges": [(int(e.a), int(e.b)) for e in getattr(pattern, "edges", []) or []],
            }
        )
        level.entities[eid] = ent
        try:
            from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
            entity_graph_ops_system.register_entity(game, ent, lod_state="expanded")
        except Exception:
            pass
            
        def remove_lightning():
            try:
                entity_ops_system.remove_entity(level, eid)
            except Exception:
                pass
        import edgecaster.systems.scheduling as scheduling
        scheduling.schedule(game, level, 10, remove_lightning)
    except Exception:
        pass

    # Total damage: 2dN where N = number of vertices in the pattern.
    try:
        n = int(len(getattr(pattern, "vertices", []) or []))
        n = max(1, n)
        total = int(rng.randint(1, n)) + int(rng.randint(1, n))
    except Exception:
        # Fallback: expected value of 2dN is N+1.
        try:
            total = max(2, int(len(getattr(pattern, "vertices", []) or [])) + 1)
        except Exception:
            total = 2

    if not targets:
        if actor_id == getattr(game, "player_id", None):
            try:
                game.log.add("Lightning crackles, but finds no creatures.")
            except Exception:
                pass
        return

    import math as _math

    per = int(_math.ceil(total / max(1, len(targets))))
    caster_is_player = actor_id == getattr(game, "player_id", None)

    if caster_is_player:
        try:
            game.log.add(f"Lightning forks into {len(targets)} bolts ({per} each).")
        except Exception:
            pass

    for target in targets:
        try:
            stats = getattr(target, "stats", None)
            if stats is None or not hasattr(stats, "hp"):
                continue
            stats.hp -= per
            if hasattr(stats, "clamp"):
                stats.clamp()
        except Exception:
            continue

        if caster_is_player:
            try:
                game.log.add(f"Lightning strikes {getattr(target, 'name', 'something')} for {per}.")
            except Exception:
                pass

        try:
            if int(getattr(target.stats, "hp", 0)) <= 0 and hasattr(game, "_kill_actor"):
                game._kill_actor(
                    level,
                    target,
                    killer_id=actor_id,
                    killer_is_player=caster_is_player,
                )
        except Exception:
            pass


@register_action("corruption_cone", label="Corruption Cone", speed="fast", show_in_bar=True)
def _action_corruption_cone(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Create a localized cone of corruption centered on the actor."""
    if hasattr(game, "act_corruption_cone"):
        game.act_corruption_cone(actor_id)


@register_action("place_rune_anchor", label="Rune Anchor", speed="fast", show_in_bar=True)
def _action_place_rune_anchor(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Place a rune anchor at the actor's current overworld position."""
    if hasattr(game, "act_place_rune_anchor"):
        game.act_place_rune_anchor(actor_id)


@register_action("seal_rune", label="Seal Rune", speed="fast", show_in_bar=True)
def _action_seal_rune(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Bind a sealing rune (trial zones only)."""
    if hasattr(game, "act_seal_rune"):
        game.act_seal_rune(actor_id)


@register_action("anchor_channel", label="Seal Fracture", speed="slow", show_in_bar=True)
def _action_anchor_channel(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Stabilize a nearby rune-anchor fracture using coherence crystals."""
    if hasattr(game, "act_anchor_channel"):
        game.act_anchor_channel(actor_id)


@register_action("anchor_stabilize", label="Stabilize Anchor", speed="slow", show_in_bar=True)
def _action_anchor_stabilize(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Reinforce the rune anchor core during the final hold phase."""
    if hasattr(game, "act_anchor_stabilize"):
        game.act_anchor_stabilize(actor_id)


@register_action("anchor_purge", label="Anchor Purge", speed="slow", show_in_bar=True, cooldown_ticks=6)
def _action_anchor_purge(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Burn coherence at the anchor core to blast back demonic pressure."""
    if hasattr(game, "act_anchor_purge"):
        game.act_anchor_purge(actor_id)


@register_action(
    "push_pattern",
    label="Push",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(kind="position", mode="aim"),
)
def _action_push_pattern(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Begin moving/spinning the current pattern: applies a repeated translation + rotation every 10 ticks.
    """
    if hasattr(game, "act_push_pattern"):
        target = kwargs.get("target_pos")
        rot = kwargs.get("rotation_deg", 0)
        game.act_push_pattern(actor_id, target_pos=target, rotation_deg=rot)



@register_action(
    "activate_all",
    label="Activate R",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(
        kind="vertex",
        mode="aim",
        radius_param="radius",
    ),
)
def _action_activate_all(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
	...
    """
    target_vertex = kwargs.get("target_vertex")
    if hasattr(game, "act_activate_all"):
        game.act_activate_all(actor_id, target_vertex)


@register_action(
    "throw_flask",
    label="Throw Flask",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(
        kind="vertex",
        mode="aim",
    ),
)
def _action_throw_flask(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Throw an energy flask to activate nearby vertices."""
    hover_vertex = kwargs.get("hover_vertex")
    if hover_vertex is None:
        return

    level = game._level()
    origin = game._activation_origin(level)
    if origin is None or not level.pattern.vertices:
        return

    from edgecaster.patterns.activation import project_vertices
    world_vertices = project_vertices(level.pattern, origin)

    if hover_vertex >= len(world_vertices):
        return

    vx, vy = world_vertices[hover_vertex]
    target_pos = (int(round(vx)), int(round(vy)))

    if hasattr(game, "act_throw_flask"):
        game.act_throw_flask(actor_id, target_pos)



@register_action(
    "activate_seed",
    label="Activate N",
    speed="fast",
    show_in_bar=True,
    targeting=TargetingSpec(
        kind="vertex",
        mode="aim",
        neighbor_depth_param="neighbor_depth",
    ),
)
def _action_activate_seed(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
	...
    """
    target_vertex = kwargs.get("target_vertex")
    if hasattr(game, "act_activate_seed"):
        game.act_activate_seed(actor_id, target_vertex)



@register_action("reset", label="Reset Rune", speed="fast", show_in_bar=True)
def _action_reset_rune(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Reset the current rune/pattern and coherence for the acting entity.
    """
    if hasattr(game, "act_reset_rune"):
        game.act_reset_rune(actor_id)


@register_action("meditate", label="Meditate", speed="slow", show_in_bar=True)
def _action_meditate(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Meditate to restore mana / coherence.

    Marked as 'slow' so action_delay will charge more ticks than a
    normal 'fast' action.
    """
    if hasattr(game, "act_meditate"):
        game.act_meditate(actor_id)


@register_action("flagellate_self", label="Flagellate", speed="fast")
def _action_flagellate_self(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Self-harm to gain a temporary attack bonus (used by the Gory Ascetic)."""
    if not hasattr(game, "_level"):
        return
    level = game._level()
    actor = getattr(level, "actors", {}).get(actor_id)
    if actor is None:
        return

    # Never allow this to kill the actor.
    try:
        if int(getattr(actor.stats, "hp", 0)) <= 1:
            return
    except Exception:
        return

    # Damage self.
    try:
        actor.stats.hp = max(1, int(actor.stats.hp) - 1)
        actor.stats.clamp()
    except Exception:
        return

    # Apply/refresh a short-lived attack bonus.
    tags = getattr(actor, "tags", None) or {}
    try:
        cur_bonus = int(tags.get("attack_bonus", 0))
    except Exception:
        cur_bonus = 0
    try:
        cur_ticks = int(tags.get("attack_bonus_ticks", 0))
    except Exception:
        cur_ticks = 0

    bonus_amt = 2
    bonus_ticks = 60  # 60 ticks = ~6 "fast" turns at default config
    tags["attack_bonus"] = max(cur_bonus, bonus_amt)
    tags["attack_bonus_ticks"] = max(cur_ticks, bonus_ticks)
    actor.tags = tags

    # Only narrate if the player could plausibly observe it.
    try:
        tile = level.world.get_tile(*actor.pos)
        if tile is not None and getattr(tile, "visible", False):
            game.log.add(f"{actor.name} lashes themself into a frenzy!")
    except Exception:
        pass


@register_action(
    "look",
    label="Look",
    speed="instant",
    show_in_bar=False,
    targeting=TargetingSpec(
        kind="look",
        mode="look",
    ),
)
@register_action(
    "look",
    label="Look",
    speed="instant",
    show_in_bar=False,
    targeting=TargetingSpec(
        kind="look",
        mode="look",
    ),
)
def _action_look(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Inspect a distant tile / entity.

    The actual popup is currently triggered by the DungeonScene
    confirm stub (_confirm_look).
    """
    return


# ---------------------------------------------------------------------------
# Deferred action: Ground Slam
# ---------------------------------------------------------------------------

@register_action("ground_slam", label="Ground Slam", speed="slow", cooldown_ticks=40)
def _action_ground_slam(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred AoE slam targeting the player's current position.

    Phase 1 (prep): telegraph tiles around where the player is standing NOW.
    Phase 2 (resolve): everything still in the zone takes high damage.
    The player has ``prep_ticks`` to move out of the danger zone.
    """
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return

    # Determine target: snapshot the player's LOCAL position at prep time.
    try:
        player = game._player()
    except Exception:
        return
    target_x, target_y = player.pos

    # Parameters (data-driven via actor tags for easy tuning).
    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("slam_radius", 2))
    prep_ticks = int(tags.get("slam_prep_ticks", 15))
    damage = int(tags.get("slam_damage", 8))

    # Compute diamond-shaped AoE tiles (Manhattan distance <= radius).
    tiles: list[tuple[int, int]] = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if abs(dx) + abs(dy) <= radius:
                tx, ty = target_x + dx, target_y + dy
                if level.world.in_bounds(tx, ty):
                    tiles.append((tx, ty))

    from edgecaster.systems.deferred import DeferredAction
    from edgecaster.systems import scheduling

    deferred_id = f"{actor_id}_ground_slam_{level.current_tick}"

    def resolve() -> None:
        # Guard: caster still alive?
        caster = level.actors.get(actor_id)
        if caster is None or not getattr(caster, "alive", True):
            level.deferred_actions = [
                da for da in getattr(level, "deferred_actions", [])
                if da.id != deferred_id
            ]
            return

        # Remove telegraph record.
        level.deferred_actions = [
            da for da in getattr(level, "deferred_actions", [])
            if da.id != deferred_id
        ]

        # Damage everything hostile to the caster in the tile set.
        tile_set = set(tiles)
        policy = damage_policy_system.DamagePolicy(
            include_self=False,
            include_hostile=True,
            include_neutral=False,
            include_friendly=False,
            include_environment=False,
        )
        caster_is_player = actor_id == getattr(game, "player_id", "")
        for tid, target in damage_policy_system.iter_damage_targets(
            game, level, actor_id, policy,
            include_actors=True, include_entities=False,
        ):
            if not _target_overlaps_tile_set(target, tile_set):
                continue
            if not getattr(target, "alive", True):
                continue

            try:
                target.stats.hp -= damage
                if hasattr(target.stats, "clamp"):
                    target.stats.clamp()
            except Exception:
                continue

            if tid == getattr(game, "player_id", None):
                game.log.add(f"The ground slam hits you for {damage}!")
            else:
                game.log.add(
                    f"The ground slam hits {getattr(target, 'name', 'something')} for {damage}!"
                )

            if int(getattr(target.stats, "hp", 0)) <= 0:
                try:
                    game._kill_actor(
                        level,
                        target,
                        killer_id=actor_id,
                        killer_is_player=caster_is_player,
                    )
                except Exception:
                    pass

        game.log.add("The ground shakes violently!")

    # Create deferred action record and register on the level.
    da = DeferredAction(
        id=deferred_id,
        caster_id=actor_id,
        action_name="ground_slam",
        label="Ground Slam",
        tiles=tiles,
        resolve_tick=level.current_tick + prep_ticks,
        created_tick=level.current_tick,
        resolve_fn=resolve,
        color=(255, 100, 40),
    )
    if not hasattr(level, "deferred_actions"):
        level.deferred_actions = []
    level.deferred_actions.append(da)

    # Schedule resolution on the level's event heap.
    scheduling.schedule(game, level, prep_ticks, resolve)

    # Log the telegraph.
    caster_name = getattr(actor, "name", "Something")
    game.log.add(f"{caster_name} raises a massive fist!")


# ---------------------------------------------------------------------------
# Deferred action helper
# ---------------------------------------------------------------------------

def _target_overlaps_tile_set(target: Any, tile_set: set[tuple[int, int]]) -> bool:
    """Return True when target footprint overlaps any tile in tile_set."""
    if not tile_set:
        return False
    try:
        rect = footprints_system.entity_footprint_local(target)
        for tx, ty in footprints_system.iter_tiles_overlapped_by_rect(rect):
            if (int(tx), int(ty)) in tile_set:
                return True
        return False
    except Exception:
        pass
    t_pos = getattr(target, "pos", None)
    if t_pos is None:
        return False
    return (int(t_pos[0]), int(t_pos[1])) in tile_set


def _entity_tiles_local(ent: Any, *, max_tiles: int = 128) -> list[tuple[int, int]]:
    try:
        rect = footprints_system.entity_footprint_local(ent)
        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for tx, ty in footprints_system.iter_tiles_overlapped_by_rect(rect):
            key = (int(tx), int(ty))
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= int(max_tiles):
                break
        if out:
            return out
    except Exception:
        pass
    pos = getattr(ent, "pos", None)
    if pos is None:
        return []
    return [(int(pos[0]), int(pos[1]))]


def _entity_center_tile_local(ent: Any) -> tuple[int, int] | None:
    try:
        x0, y0, x1, y1 = footprints_system.entity_footprint_local(ent)
        cx = int((float(x0) + float(x1)) * 0.5)
        cy = int((float(y0) + float(y1)) * 0.5)
        return (cx, cy)
    except Exception:
        pos = getattr(ent, "pos", None)
        if pos is None:
            return None
        return (int(pos[0]), int(pos[1]))


def _entity_manhattan_distance_local(a: Any, b: Any) -> int:
    at = _entity_tiles_local(a)
    bt = _entity_tiles_local(b)
    if not at or not bt:
        return 10**9
    best = 10**9
    for ax, ay in at:
        for bx, by in bt:
            d = abs(int(bx) - int(ax)) + abs(int(by) - int(ay))
            if d < best:
                best = d
                if best <= 0:
                    return 0
    return best


def _setup_deferred_aoe(
    game: Any,
    actor_id: str,
    *,
    action_name: str,
    label: str,
    tiles: list[tuple[int, int]],
    damage: int,
    prep_ticks: int,
    color: tuple[int, int, int],
    log_prep: str,
    log_resolve: str,
    damage_policy: damage_policy_system.DamagePolicy | None = None,
    include_entities: bool = False,
) -> None:
    """Shared boilerplate for any deferred AoE action.

    Creates a DeferredAction, schedules resolution, and logs the telegraph.
    The caller is responsible for computing *tiles* (LOCAL coordinates).
    """
    from edgecaster.systems.deferred import DeferredAction
    from edgecaster.systems import scheduling

    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    deferred_id = f"{actor_id}_{action_name}_{level.current_tick}"

    def resolve() -> None:
        caster = level.actors.get(actor_id)
        if caster is None or not getattr(caster, "alive", True):
            level.deferred_actions = [
                da for da in getattr(level, "deferred_actions", [])
                if da.id != deferred_id
            ]
            return

        level.deferred_actions = [
            da for da in getattr(level, "deferred_actions", [])
            if da.id != deferred_id
        ]

        tile_set = set(tiles)
        policy = damage_policy or damage_policy_system.DamagePolicy(
            include_self=False,
            include_hostile=True,
            include_neutral=False,
            include_friendly=False,
            include_environment=False,
        )
        caster_is_player = actor_id == getattr(game, "player_id", "")
        for tid, target in damage_policy_system.iter_damage_targets(
            game, level, actor_id, policy,
            include_actors=True, include_entities=include_entities,
        ):
            if not _target_overlaps_tile_set(target, tile_set):
                continue
            if not getattr(target, "alive", True):
                continue
            try:
                target.stats.hp -= damage
                if hasattr(target.stats, "clamp"):
                    target.stats.clamp()
            except Exception:
                continue
            if tid == getattr(game, "player_id", None):
                game.log.add(f"The {label.lower()} hits you for {damage}!")
            else:
                game.log.add(
                    f"The {label.lower()} hits {getattr(target, 'name', 'something')} for {damage}!"
                )
            if int(getattr(target.stats, "hp", 0)) <= 0:
                try:
                    if tid in getattr(level, "actors", {}):
                        game._kill_actor(
                            level, target,
                            killer_id=actor_id,
                            killer_is_player=caster_is_player,
                        )
                    elif tid in getattr(level, "entities", {}):
                        if hasattr(game, "_remove_entity"):
                            game._remove_entity(level, target, reason=f"destroyed_{action_name}")
                        else:
                            del level.entities[tid]
                except Exception:
                    pass

        game.log.add(log_resolve)

    da = DeferredAction(
        id=deferred_id,
        caster_id=actor_id,
        action_name=action_name,
        label=label,
        tiles=tiles,
        resolve_tick=level.current_tick + prep_ticks,
        created_tick=level.current_tick,
        resolve_fn=resolve,
        color=color,
    )
    if not hasattr(level, "deferred_actions"):
        level.deferred_actions = []
    level.deferred_actions.append(da)

    scheduling.schedule(game, level, prep_ticks, resolve)
    game.log.add(log_prep.format(name=getattr(actor, "name", "Something")))


def _cone_tiles_toward_target(
    level: Any,
    origin: tuple[int, int],
    target: tuple[int, int],
    *,
    max_range: int,
    half_angle_deg: float,
) -> list[tuple[int, int]]:
    """Return local tiles in a forward cone from origin toward target."""
    ox, oy = int(origin[0]), int(origin[1])
    tx, ty = int(target[0]), int(target[1])
    dx = float(tx - ox)
    dy = float(ty - oy)
    mag = math.hypot(dx, dy)
    if mag <= 1e-6:
        # If target overlaps origin, bias "up" for deterministic telegraph.
        dx, dy, mag = 0.0, -1.0, 1.0
    dir_x = dx / mag
    dir_y = dy / mag
    cos_half = math.cos(math.radians(float(half_angle_deg)))

    out: list[tuple[int, int]] = []
    r = int(max(1, max_range))
    for y in range(oy - r, oy + r + 1):
        for x in range(ox - r, ox + r + 1):
            if x == ox and y == oy:
                continue
            if not level.world.in_bounds(x, y):
                continue
            vx = float(x - ox)
            vy = float(y - oy)
            dist = math.hypot(vx, vy)
            if dist > float(r) or dist <= 1e-6:
                continue
            dot = (vx * dir_x + vy * dir_y) / dist
            if dot >= cos_half:
                out.append((x, y))
    return out


# ---------------------------------------------------------------------------
# Deferred action: Bear Maul
# ---------------------------------------------------------------------------

@register_action("bear_maul", label="Bear Maul", speed="slow", cooldown_ticks=30)
def _action_bear_maul(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred swipe targeting the player's position. Radius 1 diamond."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("maul_radius", 1))
    prep = int(tags.get("maul_prep_ticks", 10))
    dmg = int(tags.get("maul_damage", 6))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="bear_maul", label="Bear Maul", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(200, 150, 60),
        log_prep="{name} rears up on its hind legs!",
        log_resolve="Claws rake the ground!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Haymaker
# ---------------------------------------------------------------------------

@register_action("haymaker", label="Haymaker", speed="slow", cooldown_ticks=25)
def _action_haymaker(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred heavy punch. Small AoE (radius 1) on the player."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("haymaker_radius", 1))
    prep = int(tags.get("haymaker_prep_ticks", 8))
    dmg = int(tags.get("haymaker_damage", 5))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="haymaker", label="Haymaker", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(255, 160, 60),
        log_prep="{name} winds up a massive punch!",
        log_resolve="The fist crashes down!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Thorn Burst
# ---------------------------------------------------------------------------

@register_action("thorn_burst", label="Thorn Burst", speed="slow", cooldown_ticks=35)
def _action_thorn_burst(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred AoE centered on the CASTER. Thorns erupt around the guardian."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("thorn_radius", 2))
    prep = int(tags.get("thorn_prep_ticks", 12))
    dmg = int(tags.get("thorn_damage", 6))
    cx, cy = actor.pos  # centered on self, not the player

    tiles = [
        (cx + dx, cy + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius
        and (dx != 0 or dy != 0)  # exclude own tile
        and level.world.in_bounds(cx + dx, cy + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="thorn_burst", label="Thorn Burst", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(80, 180, 60),
        log_prep="{name} slams the earth and thorns begin to rise!",
        log_resolve="Thorns erupt from the ground!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Chain Smash (Shackled Brute)
# ---------------------------------------------------------------------------

@register_action("chain_smash", label="Chain Smash", speed="slow", cooldown_ticks=35)
def _action_chain_smash(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred wide chain swing. Radius 2 diamond on the player."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("chain_smash_radius", 2))
    prep = int(tags.get("chain_smash_prep_ticks", 12))
    dmg = int(tags.get("chain_smash_damage", 12))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="chain_smash", label="Chain Smash", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(160, 140, 120),
        log_prep="{name} swings its chains overhead!",
        log_resolve="Chains crash into the ground!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Blood Drain (Blood Sipper)
# ---------------------------------------------------------------------------

@register_action("blood_drain", label="Blood Drain", speed="slow", cooldown_ticks=30)
def _action_blood_drain(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred vampiric burst on the player's position. Radius 1."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("drain_radius", 1))
    prep = int(tags.get("drain_prep_ticks", 10))
    dmg = int(tags.get("drain_damage", 7))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="blood_drain", label="Blood Drain", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(180, 30, 50),
        log_prep="{name} opens its maw and begins to inhale!",
        log_resolve="A wave of draining energy pulses outward!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Devouring Lunge (Maw Beast)
# ---------------------------------------------------------------------------

@register_action("devouring_lunge", label="Devouring Lunge", speed="slow", cooldown_ticks=25)
def _action_devouring_lunge(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred lunging bite on the player's position. Radius 1."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("lunge_radius", 1))
    prep = int(tags.get("lunge_prep_ticks", 8))
    dmg = int(tags.get("lunge_damage", 8))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="devouring_lunge", label="Devouring Lunge", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(160, 50, 70),
        log_prep="{name} coils back, jaws gaping!",
        log_resolve="Teeth snap shut on the ground!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Lash (Slaver)
# ---------------------------------------------------------------------------

@register_action("lash", label="Lash", speed="slow", cooldown_ticks=20)
def _action_lash(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred whip strike on the player's position. Radius 1."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("lash_radius", 1))
    prep = int(tags.get("lash_prep_ticks", 8))
    dmg = int(tags.get("lash_damage", 5))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="lash", label="Lash", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(200, 160, 100),
        log_prep="{name} draws back its whip!",
        log_resolve="The whip cracks!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Venom Snap (Venomous Snake)
# ---------------------------------------------------------------------------

@register_action("venom_snap", label="Venom Snap", speed="slow", cooldown_ticks=18)
def _action_venom_snap(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred bite strike on the player's position. Radius 1."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("venom_radius", 1))
    prep = int(tags.get("venom_prep_ticks", 6))
    dmg = int(tags.get("venom_damage", 4))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="venom_snap", label="Venom Snap", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(90, 210, 90),
        log_prep="{name} coils and hisses!",
        log_resolve="Fangs strike with venomous speed!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Ember Pounce (Cinder Hound)
# ---------------------------------------------------------------------------

@register_action("ember_pounce", label="Ember Pounce", speed="slow", cooldown_ticks=22)
def _action_ember_pounce(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred pounce strike on the player's position. Radius 1."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("pounce_radius", 1))
    prep = int(tags.get("pounce_prep_ticks", 7))
    dmg = int(tags.get("pounce_damage", 6))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="ember_pounce", label="Ember Pounce", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(255, 120, 40),
        log_prep="{name} crouches low, embers sparking!",
        log_resolve="A blazing pounce tears through the air!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Bone Lance (Bone Weaver)
# ---------------------------------------------------------------------------

@register_action("bone_lance", label="Bone Lance", speed="slow", cooldown_ticks=24)
def _action_bone_lance(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Deferred piercing strike on the player's position. Radius 1."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    radius = int(tags.get("lance_radius", 1))
    prep = int(tags.get("lance_prep_ticks", 9))
    dmg = int(tags.get("lance_damage", 7))
    tx, ty = player.pos

    tiles = [
        (tx + dx, ty + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius and level.world.in_bounds(tx + dx, ty + dy)
    ]
    _setup_deferred_aoe(
        game, actor_id,
        action_name="bone_lance", label="Bone Lance", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(220, 220, 220),
        log_prep="{name} raises a shard of white bone!",
        log_resolve="A lance of bone erupts forward!",
    )


# ---------------------------------------------------------------------------
# Deferred action: Fire Breath (Fire Breather)
# ---------------------------------------------------------------------------

@register_action("fire_breath", label="Fire Breath", speed="slow", cooldown_ticks=30)
def _action_fire_breath(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Telegraphed cone attack that scorches everything in front of the caster."""
    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", True):
        return
    try:
        player = game._player()
    except Exception:
        return

    tags = getattr(actor, "tags", None) or {}
    prep = int(tags.get("fire_breath_prep_ticks", 10))
    dmg = int(tags.get("fire_breath_damage", 7))
    cone_range = int(tags.get("fire_breath_range", 5))
    half_angle = float(tags.get("fire_breath_half_angle_deg", 30.0))

    # Snapshot the player's current position and build a forward cone telegraph.
    tiles = _cone_tiles_toward_target(
        level,
        origin=(int(actor.pos[0]), int(actor.pos[1])),
        target=(int(player.pos[0]), int(player.pos[1])),
        max_range=cone_range,
        half_angle_deg=half_angle,
    )
    if not tiles:
        return

    # Fire breath is indiscriminate: it damages all damageable actors/entities
    # in the cone, excluding the caster.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=True,
        include_friendly=True,
        include_environment=True,
    )
    _setup_deferred_aoe(
        game, actor_id,
        action_name="fire_breath", label="Fire Breath", tiles=tiles,
        damage=dmg, prep_ticks=prep, color=(255, 110, 40),
        log_prep="{name} inhales and its throat glows orange!",
        log_resolve="A cone of flame roars outward!",
        damage_policy=policy,
        include_entities=True,
    )


# ===========================================================================
# CHAKRA ACTIVATED ABILITIES
# ===========================================================================
# These require specific chakras to be active.  They are typically granted
# automatically when the prerequisite chakra is first activated, or by
# equipping the matching chakra item.

def _chakra_active_tokens(game: Any, actor_id: str) -> set[str]:
    """Return the set of normalized tokens from the actor's effective active chakras."""
    try:
        from edgecaster.systems import chakra_items as chakra_items_system

        level = game._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return set()
        active = chakra_items_system.effective_active_nodes(game, actor)
        out: set[str] = set()
        for nid in active:
            for tok in str(nid).lower().split("."):
                t = tok.strip()
                if t:
                    out.add(t)
                    if t.endswith("_m"):
                        out.add(t[:-2])
        return out
    except Exception:
        return set()


def _consume_charge(game: Any, actor_id: str, amount: float) -> None:
    """Consume chakra charge from the actor's active chakras."""
    try:
        game._consume_chakra_charge(actor_id, amount)
    except Exception:
        pass


# ---- Chakra Pulse ----
# Requires: any active chakra (always available once body is active)
# Effect: AoE knockback centered on self, pushes enemies 2 tiles away.

@register_action("chakra_pulse", label="Chakra Pulse", speed="fast", cooldown_ticks=30)
def _action_chakra_pulse(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Release a pulse of chakra energy that pushes nearby enemies away."""
    tokens = _chakra_active_tokens(game, actor_id)
    if not tokens:
        game.log.add("No active chakras to channel.")
        return

    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    ax, ay = actor.pos
    push_radius = 2
    push_dist = 2

    pushed_any = False
    for tid, target in list(level.actors.items()):
        if tid == actor_id:
            continue
        if not getattr(target, "alive", True):
            continue
        if not game.is_hostile(actor, target):
            continue
        tx, ty = target.pos
        dx = tx - ax
        dy = ty - ay
        dist = abs(dx) + abs(dy)
        if dist < 1 or dist > push_radius:
            continue

        # Determine push direction (away from caster).
        mag = math.hypot(float(dx), float(dy))
        if mag < 0.01:
            continue
        ndx = dx / mag
        ndy = dy / mag
        step_x = int(round(ndx))
        step_y = int(round(ndy))
        if step_x == 0 and step_y == 0:
            continue

        # Apply knockback_resist from target.
        effective_push = push_dist
        try:
            resist = int(game.chakra_effect_value("knockback_resist", actor_id=tid))
            effective_push = max(0, effective_push - resist)
        except Exception:
            pass
        if effective_push <= 0:
            game.log.add(f"{target.name} resists the pulse!")
            pushed_any = True
            continue

        # Push tile by tile, stopping at blocked footprint overlap.
        base_rect = footprints_system.entity_footprint_local(target)
        cx, cy = tx, ty
        for _ in range(effective_push):
            nx = cx + step_x
            ny = cy + step_y
            candidate_rect = footprints_system.rect_translate(
                base_rect,
                float(nx - tx),
                float(ny - ty),
            )
            try:
                in_bounds = footprints_system.rect_within_bounds(
                    candidate_rect,
                    width=int(level.world.width),
                    height=int(level.world.height),
                )
            except Exception:
                in_bounds = bool(level.world.in_bounds(nx, ny))
            if not in_bounds:
                break
            if not footprints_system.world_walkable_for_rect(level.world, candidate_rect):
                break
            if entity_ops_system.first_actor_overlapping_rect(
                level,
                candidate_rect,
                exclude_id=tid,
            ):
                break
            if entity_ops_system.blocking_entity_overlapping_rect(
                level,
                candidate_rect,
                exclude_ids={tid},
                ignore_actor_entities=True,
            ):
                break
            cx, cy = nx, ny

        if (cx, cy) != (tx, ty):
            moved = False
            if hasattr(game, "_move_actor_to_abs") and hasattr(game, "abs_from_zone_local"):
                try:
                    dest_abs = game.abs_from_zone_local(level.coord, (cx, cy))
                    game._move_actor_to_abs(target, dest_abs, from_level=level)
                    moved = True
                except Exception:
                    moved = False
            if not moved:
                fn = getattr(target, "set_pos", None)
                if callable(fn):
                    try:
                        fn((cx, cy))
                    except Exception:
                        target.pos = (cx, cy)
                else:
                    target.pos = (cx, cy)
                try:
                    if hasattr(game, "abs_from_zone_local"):
                        abs_pos = game.abs_from_zone_local(level.coord, (cx, cy))
                        afn = getattr(target, "set_abs_pos", None)
                        if callable(afn):
                            afn(abs_pos)
                        else:
                            setattr(target, "abs_pos", abs_pos)
                except Exception:
                    pass
                try:
                    level.spatial_dirty = True
                except Exception:
                    pass
            pushed_any = True

    _consume_charge(game, actor_id, 0.3)

    if pushed_any:
        game.log.add("You release a pulse of chakra energy!")
    else:
        game.log.add("The pulse ripples outward, but nothing is pushed.")


# ---- Iron Skin ----
# Requires: chest + back active
# Effect: Temporary buff — 50% incoming damage reduction for 20 ticks.

@register_action("iron_skin", label="Iron Skin", speed="instant", cooldown_ticks=60)
def _action_iron_skin(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Harden your skin with chakra energy, halving incoming damage."""
    tokens = _chakra_active_tokens(game, actor_id)
    if "chest" not in tokens or "back" not in tokens:
        game.log.add("Iron Skin requires both chest and back chakras active.")
        return

    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    game._add_status(actor, "iron_skin", 20, on_apply="Your skin hardens to iron!")
    _consume_charge(game, actor_id, 0.5)


# ---- Third Eye ----
# Requires: any eye chakra active
# Effect: Reveals all enemies/items in radius 15 for 30 ticks (sees through walls).

@register_action("third_eye", label="Third Eye", speed="instant", cooldown_ticks=50)
def _action_third_eye(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Open your inner eye to perceive all nearby beings."""
    tokens = _chakra_active_tokens(game, actor_id)
    if "eye" not in tokens:
        game.log.add("Third Eye requires an eye chakra to be active.")
        return

    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    game._add_status(actor, "third_eye", 30, on_apply="Your inner eye opens wide...")
    _consume_charge(game, actor_id, 0.4)


# ---- Root Grasp ----
# Requires: any foot chakra active
# Effect: Deferred AoE — roots erupt from the ground, immobilizing enemies.

@register_action("root_grasp", label="Root Grasp", speed="slow", cooldown_ticks=40)
def _action_root_grasp(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Summon roots that immobilize enemies in a target area."""
    tokens = _chakra_active_tokens(game, actor_id)
    if "foot" not in tokens and "ankle" not in tokens and "sole" not in tokens:
        game.log.add("Root Grasp requires a foot chakra to be active.")
        return

    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    # Target the player's position (for enemies) or cursor (for player).
    # For simplicity, target a radius-2 diamond centered on the nearest enemy.
    player = level.actors.get(getattr(game, "player_id", ""))
    if player is None:
        return

    if actor_id == getattr(game, "player_id", ""):
        # Player usage: target nearest hostile.
        best_target = None
        best_dist = 999
        for tid, t in level.actors.items():
            if tid == actor_id or not getattr(t, "alive", True):
                continue
            if not game.is_hostile(actor, t):
                continue
            d = _entity_manhattan_distance_local(actor, t)
            if d < best_dist:
                best_dist = d
                best_target = t
        if best_target is None or best_dist > 8:
            game.log.add("No enemy close enough for Root Grasp.")
            return
        center = _entity_center_tile_local(best_target)
        if center is None:
            game.log.add("No enemy close enough for Root Grasp.")
            return
        cx, cy = center
    else:
        center = _entity_center_tile_local(player)
        if center is None:
            return
        cx, cy = center

    radius = 2
    tiles = [
        (cx + dx, cy + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if abs(dx) + abs(dy) <= radius
        and level.world.in_bounds(cx + dx, cy + dy)
    ]

    from edgecaster.systems.deferred import DeferredAction
    from edgecaster.systems import scheduling

    prep_ticks = 8
    deferred_id = f"{actor_id}_root_grasp_{level.current_tick}"

    def resolve() -> None:
        caster = level.actors.get(actor_id)
        if caster is None or not getattr(caster, "alive", True):
            level.deferred_actions = [
                da for da in getattr(level, "deferred_actions", [])
                if da.id != deferred_id
            ]
            return

        level.deferred_actions = [
            da for da in getattr(level, "deferred_actions", [])
            if da.id != deferred_id
        ]

        tile_set = set(tiles)
        rooted_any = False
        for tid, target in list(level.actors.items()):
            if tid == actor_id:
                continue
            if not getattr(target, "alive", True):
                continue
            if not _target_overlaps_tile_set(target, tile_set):
                continue
            game._add_status(target, "rooted", 10)
            game.log.add(f"Roots ensnare {target.name}!")
            rooted_any = True

        if rooted_any:
            game.log.add("Roots claw up from the earth!")
        else:
            game.log.add("The roots find nothing to grasp.")

    da = DeferredAction(
        id=deferred_id,
        caster_id=actor_id,
        action_name="root_grasp",
        label="Root Grasp",
        tiles=tiles,
        resolve_tick=level.current_tick + prep_ticks,
        created_tick=level.current_tick,
        resolve_fn=resolve,
        color=(60, 180, 60),
    )
    if not hasattr(level, "deferred_actions"):
        level.deferred_actions = []
    level.deferred_actions.append(da)

    scheduling.schedule(game, level, prep_ticks, resolve)
    game.log.add("Roots begin to claw up from the earth!")
    _consume_charge(game, actor_id, 0.3)


# ---- Phantom Limb ----
# Requires: any arm chakra active
# Effect: Temporary buff — extends melee range by 2 for 15 ticks or 1 attack.

@register_action("phantom_limb", label="Phantom Limb", speed="fast", cooldown_ticks=35)
def _action_phantom_limb(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Extend a spectral arm, increasing melee range temporarily."""
    tokens = _chakra_active_tokens(game, actor_id)
    has_arm = any(t in tokens for t in ("arm", "shoulder", "elbow", "forearm", "hand"))
    if not has_arm:
        game.log.add("Phantom Limb requires an arm chakra to be active.")
        return

    try:
        level = game._level()
    except Exception:
        return
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    game._add_status(actor, "phantom_limb", 15, on_apply="A spectral arm extends from your shoulder!")
    _consume_charge(game, actor_id, 0.3)


# ---- Spinal Surge ----
# Requires: back active, 3+ chakras active total
# Effect: Instantly recharges all active chakras to full.

@register_action("spinal_surge", label="Spinal Surge", speed="instant", cooldown_ticks=80)
def _action_spinal_surge(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Surge energy up your spine, recharging all active chakras."""
    tokens = _chakra_active_tokens(game, actor_id)
    if "back" not in tokens:
        game.log.add("Spinal Surge requires the back chakra to be active.")
        return

    try:
        from edgecaster.systems import chakra_items as chakra_items_system

        level = game._level()
        actor = level.actors.get(actor_id)
        if actor is None:
            return
        active = chakra_items_system.effective_active_nodes(game, actor)
    except Exception:
        game.log.add("Cannot read chakra state.")
        return

    if len(active) < 3:
        game.log.add("Spinal Surge requires at least 3 active chakras.")
        return

    # Recharge all active chakras to full (1.0).
    # set_actor_chakra_charge is defensive; no null-guard needed here since
    # len(active) >= 3 already confirms the actor has an active chakra state.
    for node_id in active:
        chakra_items_system.set_actor_chakra_charge(actor, node_id, 1.0, game=game)

    game.log.add("Energy surges up your spine, flooding every channel!")


# ---------------------------------------------------------------------------
# God actions
# ---------------------------------------------------------------------------

@register_action("knife_rune", label="Death Rune", speed="fast", show_in_bar=True, cooldown_ticks=20)
def _action_knife_rune(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Activate the Dark Knife's rune to damage nearby enemies."""
    from edgecaster.systems import god_abilities
    god_abilities.act_knife_rune(game, actor_id, **kwargs)


@register_action(
    "reaper_mark",
    label="Reaper Mark",
    speed="fast",
    show_in_bar=True,
    cooldown_ticks=30,
    targeting=TargetingSpec(kind="tile", mode="aim", max_range=6),
)
def _action_reaper_mark(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Mark a hostile target for death. Heal if it dies within 10 turns."""
    from edgecaster.systems import god_abilities
    god_abilities.act_reaper_mark(game, actor_id, **kwargs)


@register_action("verdant_mend", label="Verdant Mend", speed="fast", show_in_bar=True, cooldown_ticks=20)
def _action_verdant_mend(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Heal with the power of The Verdant Mother."""
    from edgecaster.systems import god_abilities
    god_abilities.act_verdant_mend(game, actor_id, **kwargs)


@register_action("root_ward", label="Root Ward", speed="slow", show_in_bar=True, cooldown_ticks=40)
def _action_root_ward(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Grow blocking roots on adjacent tiles."""
    from edgecaster.systems import god_abilities
    god_abilities.act_root_ward(game, actor_id, **kwargs)


@register_action("all_seeing", label="All-Seeing", speed="fast", show_in_bar=True, cooldown_ticks=25)
def _action_all_seeing(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Expand your vision with the power of The Hollow Eye."""
    from edgecaster.systems import god_abilities
    god_abilities.act_all_seeing(game, actor_id, **kwargs)


@register_action(
    "piercing_gaze",
    label="Piercing Gaze",
    speed="fast",
    show_in_bar=True,
    cooldown_ticks=30,
    targeting=TargetingSpec(kind="tile", mode="aim", max_range=8),
)
def _action_piercing_gaze(game: Any, actor_id: str, **kwargs: Any) -> None:
    """See through walls in a line toward the target."""
    from edgecaster.systems import god_abilities
    god_abilities.act_piercing_gaze(game, actor_id, **kwargs)


@register_action("god_iron_skin", label="Iron Skin", speed="fast", show_in_bar=True, cooldown_ticks=25)
def _action_god_iron_skin(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Harden your body with The Iron Spine's blessing."""
    from edgecaster.systems import god_abilities
    god_abilities.act_god_iron_skin(game, actor_id, **kwargs)


@register_action("unbreakable", label="Unbreakable", speed="instant", show_in_bar=True, cooldown_ticks=60)
def _action_unbreakable(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Survive one lethal hit with 1 HP. Costs 30 favor."""
    from edgecaster.systems import god_abilities
    god_abilities.act_unbreakable(game, actor_id, **kwargs)
