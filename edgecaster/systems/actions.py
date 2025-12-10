from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Literal, Protocol
from pathlib import Path
import yaml






# Lightweight prototype cache built from the YAML content layer.
_ENTITY_PROTO_INDEX: Dict[str, dict] = {}
_ENEMY_PROTO_INDEX: Dict[str, dict] = {}
_PROTO_INDEX: Dict[str, dict] = {}

def get_prototype(proto_id: str) -> dict:
    """Return the raw prototype dict (entities + enemies) for a given id.

    This is a thin wrapper over the cached YAML content.
    """
    if not proto_id:
        return {}
    if not _PROTO_INDEX:
        _load_prototype_index()
    return _PROTO_INDEX.get(str(proto_id), {})

def _load_prototype_index() -> None:
    global _ENTITY_PROTO_INDEX, _ENEMY_PROTO_INDEX, _PROTO_INDEX

    try:
        content_root = Path(__file__).resolve().parents[1] / "content"
        entities_path = content_root / "entities.yaml"
        enemies_path = content_root / "enemies.yaml"
    except Exception:
        return

    try:
        if entities_path.is_file():
            with entities_path.open("r", encoding="utf-8") as f:
                entities = yaml.safe_load(f) or []
                _ENTITY_PROTO_INDEX = {
                    str(p.get("id")): p for p in entities if isinstance(p, dict) and "id" in p
                }
        if enemies_path.is_file():
            with enemies_path.open("r", encoding="utf-8") as f:
                enemies = yaml.safe_load(f) or []
                _ENEMY_PROTO_INDEX = {
                    str(p.get("id")): p for p in enemies if isinstance(p, dict) and "id" in p
                }
    except Exception:
        _ENTITY_PROTO_INDEX = {}
        _ENEMY_PROTO_INDEX = {}

    _PROTO_INDEX = {}
    _PROTO_INDEX.update(_ENTITY_PROTO_INDEX)
    _PROTO_INDEX.update(_ENEMY_PROTO_INDEX)

# Build the cache at import time (best-effort).
_load_prototype_index()

# Optional: pattern colors (only imported when used to avoid extra deps elsewhere)
try:
    from edgecaster.patterns import colors as pattern_colors
except Exception:  # pragma: no cover - keep fail-soft for minimal envs/tests
    pattern_colors = None


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



def _resolve_description_via_parents(proto_id: str) -> str | None:
    if not proto_id or not _PROTO_INDEX:
        return None

    visited: set[str] = set()
    cur = proto_id

    while cur and cur not in visited:
        visited.add(cur)
        proto = _PROTO_INDEX.get(cur)
        if not proto:
            break
        desc = proto.get("description")
        if desc:
            return str(desc)
        parent = proto.get("parent")
        if not parent:
            break
        cur = str(parent)

    return None


def resolve_entity_description(ent: Any) -> str | None:
    """
    1. If ent.description exists, use that.
    2. Else infer proto id (kind/id) and climb YAML parent chain.
    """
    direct = getattr(ent, "description", None)
    if direct:
        return str(direct)

    proto_id = _lookup_proto_id_for_entity(ent)
    if not proto_id:
        return None

    return _resolve_description_via_parents(proto_id)


def describe_entity_for_look(ent: Any) -> Dict[str, Any]:
    """Return name, glyph, color, and description for an entity."""

    proto_id = _lookup_proto_id_for_entity(ent)
    proto = _PROTO_INDEX.get(proto_id, {}) if proto_id and _PROTO_INDEX else {}

    name = (
        getattr(ent, "name", None)
        or getattr(ent, "label", None)
        or proto.get("name")
        or "something"
    )

    glyph = getattr(ent, "glyph", None) or proto.get("glyph") or "?"
    color = getattr(ent, "color", None) or proto.get("color") or (255, 255, 255)

    desc = resolve_entity_description(ent) or "You see nothing remarkable about it."

    return {
        "name": str(name),
        "glyph": str(glyph),
        "color": tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
        "description": str(desc),
    }


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
    speed: SpeedTag
    func: ActionFunc
    # Whether this action is eligible to appear in the player-facing
    # ability bar when owned by the current host actor.
    show_in_bar: bool = False
    cooldown_ticks: int = 0
    # Targeting metadata (None = immediate, non-targeted action).
    targeting: TargetingSpec | None = None









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
        "I hope you crash in an overflow error!",
        "I hate you!!! A lot!!!",
        "I hope you get ambushed by an alligator.",
        "Hey look at this guy over here, Mr. Big Deal Fractal guy, ooh la la he's a fancy fucker ain't he?",
        "Berryfucker!",
        "Your MOM is self-similar!",
        
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
