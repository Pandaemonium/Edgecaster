from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Dict, Literal, Protocol

# Optional: pattern colors (only imported when used to avoid extra deps elsewhere)
try:
    from edgecaster.patterns import colors as pattern_colors
except Exception:  # pragma: no cover - keep fail-soft for minimal envs/tests
    pattern_colors = None

from edgecaster import prototypes
from edgecaster.systems import reputation as reputation_system


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
SpeedType = SpeedTag | int


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

    ax, ay = getattr(actor, "pos", (0, 0))
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

        ox, oy = getattr(other, "pos", (0, 0))
        if abs(ox - ax) + abs(oy - ay) > radius:
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


@register_action("custom", label="Custom", speed="fast", show_in_bar=True)
def _action_custom(game: Any, actor_id: str, **kwargs: Any) -> None:
    """
    Apply the base 'custom' fractal pattern (index 0).

    For extra saved patterns we use action names like 'custom_1',
    'custom_2', etc. and pass the suffix through to Game.act_fractal.
    """
    if hasattr(game, "act_fractal"):
        game.act_fractal(actor_id, "custom")


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
        level.sparkle_state = {
            "t0": float(time.monotonic()),
            # Edges fade back in more slowly than the crackle.
            "duration_s": 2.0,
            "spark_duration_s": 1.0,
            "seed": seed,
            # Snapshot the geometry at cast time so the effect doesn't jump if the
            # player immediately edits the rune after casting.
            "verts": [(float(x), float(y)) for (x, y) in verts_world],
            "edges": edges,
        }
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

    # Apply damage to *any* entity with HP (actors, NPCs, objects), except the caster.
    # We iterate over a snapshot to avoid mutation while killing actors.
    combined: dict[str, Any] = dict(getattr(level, "actors", {}) or {})
    for eid, ent in dict(getattr(level, "entities", {}) or {}).items():
        if eid not in combined:
            combined[eid] = ent

    hit_any = False
    for tid, obj in combined.items():
        if tid == actor_id:
            continue
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

        # Only actors have removal + death logic.
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

    # Pick all living actors whose tile is touched by the pattern.
    targets = []
    for target in list(getattr(level, "actors", {}).values()):
        try:
            if getattr(target, "id", None) == actor_id:
                continue
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
        level.lightning_state = {
            "t0": float(time.monotonic()),
            # TUNING: bolt VFX timing (renderer reads these; no simulation depends on them).
            # - duration_s: how long the bolt animates (seconds)
            # - flash_s: how long the initial flash lasts (seconds)
            "duration_s": 0.26,
            "flash_s": 0.08,
            "seed": seed,
            # Snapshot the footprint so the effect doesn't jump if the rune moves/edits.
            "mask_tiles": sorted(touched, key=lambda p: (p[1], p[0])),
            "start_tile": start_tile,
            "target_tiles": [
                (int(getattr(t, "pos", (0, 0))[0]), int(getattr(t, "pos", (0, 0))[1]))
                for t in targets
            ],
            # Snapshot the rune geometry for edge-constrained lightning VFX.
            "verts": [(float(x), float(y)) for (x, y) in verts_world],
            "edges": [(int(e.a), int(e.b)) for e in getattr(pattern, "edges", []) or []],
        }
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
