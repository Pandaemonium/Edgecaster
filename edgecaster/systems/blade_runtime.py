"""Fractal blade runtime (V1).

This module centralizes blade-melee state and verb resolution so combat logic does
not sprawl across ``game.py`` and action handlers.

Design goals for V1:
- Keep one execution path for both action-triggered melee and bump melee.
- Keep policy explicit: melee hits hostile actors only by default.
- Keep blade state lightweight and actor-scoped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Sequence, Tuple

from edgecaster.systems import damage_policy as damage_policy_system

if TYPE_CHECKING:
    from edgecaster.game import Game


@dataclass
class BladeState:
    """Runtime blade configuration for an actor.

    ``generators`` is intentionally string-based in V1 so we can attach real
    generator metadata/evaluators later without changing the storage shape.
    """

    generators: List[str] = field(default_factory=list)


@dataclass
class BladeStatSnapshot:
    """Read-only blade stat payload for UI surfaces (editor/tooltips)."""

    slots: int
    slash_damage: int
    slash_reach: float
    thrust_damage: int
    thrust_reach: float
    cleave_damage_min: int
    cleave_damage_max: int
    cleave_reach: float


def _player_class(game: "Game") -> str:
    try:
        cls = getattr(getattr(game, "character", None), "player_class", None)
        if cls:
            return str(cls)
        cls = getattr(getattr(game, "character", None), "char_class", None)
        if cls:
            return str(cls)
    except Exception:
        pass
    return ""


def actor_uses_blade_melee(game: "Game", actor_id: str) -> bool:
    """Whether this actor should use blade-melee routing.

    V1 keeps this conservative: Blade-class player host only.
    """
    try:
        return bool(actor_id == getattr(game, "player_id", None) and _player_class(game) == "Blade")
    except Exception:
        return False


def blade_slots(game: "Game", actor_id: str) -> int:
    """Generator slot count for an actor's blade.

    V1 uses the confirmed rule: ``max(1, INT)``.
    """
    int_stat = 0
    try:
        if actor_id == getattr(game, "player_id", None) and hasattr(game, "effective_character_stats"):
            estats = game.effective_character_stats() or {}
            int_stat = int(estats.get("int", 0) or 0)
        else:
            actor = getattr(game._level(), "actors", {}).get(actor_id)
            if actor is not None:
                tags = getattr(actor, "tags", {}) or {}
                int_stat = int(tags.get("int", 0) or 0)
    except Exception:
        int_stat = 0
    return max(1, int(int_stat))


def ensure_actor_blade_state(game: "Game", actor_id: str) -> BladeState:
    """Ensure ``game.blade_states[actor_id]`` exists and is slot-valid."""
    store = getattr(game, "blade_states", None)
    if not isinstance(store, dict):
        store = {}
        setattr(game, "blade_states", store)

    state = store.get(actor_id)
    if not isinstance(state, BladeState):
        state = BladeState()
        store[actor_id] = state

    # Enforce slot cap (future editor/item systems depend on this invariant).
    slots = blade_slots(game, actor_id)
    if len(state.generators) > slots:
        state.generators = list(state.generators[:slots])

    return state


def set_actor_blade_generators(game: "Game", actor_id: str, generators: Sequence[str]) -> BladeState:
    """Set blade generators while respecting the actor's slot limit."""
    state = ensure_actor_blade_state(game, actor_id)
    slots = blade_slots(game, actor_id)
    cleaned = [str(g).strip() for g in generators if str(g).strip()]
    state.generators = cleaned[:slots]
    return state


def blade_stat_snapshot(game: "Game", actor_id: str) -> BladeStatSnapshot:
    """Compute a stable blade stat view for rendering/UI.

    This keeps the editor and any future tooltips on the same source of truth as
    combat resolution (reach and damage helpers in this module).
    """
    state = ensure_actor_blade_state(game, actor_id)
    slash_reach = _verb_reach(state, "slash")
    thrust_reach = _verb_reach(state, "thrust")
    cleave_reach = _verb_reach(state, "cleave")
    return BladeStatSnapshot(
        slots=blade_slots(game, actor_id),
        slash_damage=_verb_damage(state, "slash"),
        slash_reach=float(slash_reach),
        thrust_damage=_verb_damage(state, "thrust"),
        thrust_reach=float(thrust_reach),
        cleave_damage_min=_verb_damage(state, "cleave", distance=float(cleave_reach), reach=float(cleave_reach)),
        cleave_damage_max=_verb_damage(state, "cleave", distance=0.0, reach=float(cleave_reach)),
        cleave_reach=float(cleave_reach),
    )


def _actor_abs(game: "Game", level: Any, actor: Any) -> Tuple[int, int]:
    abs_pos = getattr(actor, "abs_pos", None)
    if abs_pos is not None:
        return int(abs_pos[0]), int(abs_pos[1])
    return tuple(int(v) for v in game.abs_from_zone_local(level.coord, actor.pos))


def _target_abs_from_local(game: "Game", target_pos: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    if target_pos is None:
        return None
    try:
        return tuple(int(v) for v in game.abs_from_zone_local(game.zone_coord, target_pos))
    except Exception:
        return None


def _iter_hostile_actor_targets(game: "Game", actor_id: str) -> Iterable[Tuple[Any, Any, int, int]]:
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    for lvl in game._loaded_active_levels():
        for tid, obj in damage_policy_system.iter_damage_targets(
            game,
            lvl,
            actor_id,
            policy,
            include_actors=True,
            include_entities=False,
        ):
            if tid not in getattr(lvl, "actors", {}):
                continue
            if not getattr(obj, "alive", True):
                continue
            tx, ty = _actor_abs(game, lvl, obj)
            yield lvl, obj, tx, ty


def _distance(a: Tuple[int, int], b: Tuple[int, int]) -> float:
    return math.hypot(float(a[0] - b[0]), float(a[1] - b[1]))


def _point_segment_dist(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    vv = vx * vx + vy * vy
    if vv <= 1e-9:
        return math.hypot(px - ax, py - ay)
    t = (wx * vx + wy * vy) / vv
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx = ax + t * vx
    qy = ay + t * vy
    return math.hypot(px - qx, py - qy)


def _verb_reach(state: BladeState, verb: str) -> float:
    g = len(state.generators)
    if verb == "thrust":
        return 2.2 + min(1.6, 0.25 * g)
    if verb == "cleave":
        return 1.7 + min(0.9, 0.12 * g)
    return 1.45 + min(0.65, 0.08 * g)


def _verb_damage(state: BladeState, verb: str, *, distance: float = 0.0, reach: float = 1.0) -> int:
    g = len(state.generators)
    if verb == "thrust":
        base = 7 + (2 * g)
        return max(1, int(base))
    if verb == "cleave":
        base = 5 + g
        if reach > 1e-6:
            falloff = max(0.35, 1.0 - (distance / reach))
        else:
            falloff = 1.0
        return max(1, int(math.ceil(base * falloff)))
    # slash
    base = 6 + (2 * g)
    return max(1, int(base))


def _apply_actor_damage(
    game: "Game",
    level: Any,
    actor_id: str,
    target: Any,
    dmg: int,
    *,
    verb: str,
) -> bool:
    if dmg <= 0:
        return False

    try:
        target.stats.hp -= int(dmg)
        if hasattr(target.stats, "clamp"):
            target.stats.clamp()
    except Exception:
        return False

    caster_is_player = actor_id == getattr(game, "player_id", None)
    if caster_is_player:
        tname = getattr(target, "name", None) or "something"
        vlabel = verb.capitalize()
        game.log.add(f"{vlabel} cuts {tname} for {int(dmg)}.")

    if int(getattr(target.stats, "hp", 0)) <= 0:
        game._kill_actor(
            level,
            target,
            killer_id=actor_id,
            killer_is_player=caster_is_player,
        )
    return True


def act_blade_attack(
    game: "Game",
    actor_id: str,
    verb: str,
    *,
    target_pos: Optional[Tuple[int, int]] = None,
    from_bump: bool = False,
) -> bool:
    """Execute a blade attack verb.

    Returns ``True`` when the action route was handled (hit or miss), ``False``
    only when no valid actor context exists.
    """
    level = game._level()
    actor = level.actors.get(actor_id)
    if actor is None or not getattr(actor, "alive", False):
        return False

    state = ensure_actor_blade_state(game, actor_id)

    actor_abs = _actor_abs(game, level, actor)
    target_abs = _target_abs_from_local(game, target_pos)
    reach = _verb_reach(state, verb)

    candidates = list(_iter_hostile_actor_targets(game, actor_id))
    if not candidates:
        if actor_id == getattr(game, "player_id", None) and not from_bump:
            game.log.add("No hostile targets nearby.")
        return True

    # Selection helpers shared across verbs.
    def nearest_to_actor_within(max_dist: float) -> Optional[Tuple[Any, Any, int, int]]:
        near: Optional[Tuple[Any, Any, int, int]] = None
        best = 1e9
        for cand in candidates:
            _, _, tx, ty = cand
            d = _distance(actor_abs, (tx, ty))
            if d > max_dist:
                continue
            if d < best:
                best = d
                near = cand
        return near

    def nearest_to_point_within(point_abs: Tuple[int, int], max_dist: float) -> Optional[Tuple[Any, Any, int, int]]:
        near: Optional[Tuple[Any, Any, int, int]] = None
        best = 1e9
        for cand in candidates:
            _, _, tx, ty = cand
            da = _distance(actor_abs, (tx, ty))
            if da > max_dist:
                continue
            dpt = _distance(point_abs, (tx, ty))
            if dpt < best:
                best = dpt
                near = cand
        return near

    if verb == "slash":
        chosen = None
        if target_abs is not None:
            chosen = nearest_to_point_within(target_abs, reach)
        if chosen is None:
            chosen = nearest_to_actor_within(reach)

        if chosen is None:
            if actor_id == getattr(game, "player_id", None):
                game.log.add("Your slash finds no target.")
            return True

        tlvl, target, tx, ty = chosen
        dmg = _verb_damage(state, "slash")
        _apply_actor_damage(game, tlvl, actor_id, target, dmg, verb="slash")
        try:
            level.activation_points = [(float(actor.pos[0]), float(actor.pos[1])), (float(tx), float(ty))]
            level.activation_ttl = max(5, int(getattr(game.cfg, "pattern_overlay_ttl", 10) // 2))
        except Exception:
            pass
        return True

    if verb == "thrust":
        # Choose direction from explicit target if available, otherwise from nearest hostile.
        if target_abs is None:
            nearest = nearest_to_actor_within(reach)
            if nearest is None:
                if actor_id == getattr(game, "player_id", None):
                    game.log.add("No target in thrust range.")
                return True
            _, _, tx, ty = nearest
            target_abs = (tx, ty)

        dx = float(target_abs[0] - actor_abs[0])
        dy = float(target_abs[1] - actor_abs[1])
        mag = math.hypot(dx, dy)
        if mag <= 1e-6:
            dx, dy = 1.0, 0.0
            mag = 1.0
        ux = dx / mag
        uy = dy / mag
        end_x = float(actor_abs[0]) + (ux * reach)
        end_y = float(actor_abs[1]) + (uy * reach)

        best = None
        best_t = 1e9
        for cand in candidates:
            _, _, tx, ty = cand
            # Slightly forgiving line width so thrust feels responsive.
            dist_line = _point_segment_dist(float(tx), float(ty), float(actor_abs[0]), float(actor_abs[1]), end_x, end_y)
            if dist_line > 0.55:
                continue
            # Parameter t for ordering from actor -> endpoint.
            vx = end_x - float(actor_abs[0])
            vy = end_y - float(actor_abs[1])
            vv = (vx * vx + vy * vy) or 1.0
            wx = float(tx - actor_abs[0])
            wy = float(ty - actor_abs[1])
            t = (wx * vx + wy * vy) / vv
            if t < 0.0 or t > 1.0:
                continue
            if t < best_t:
                best_t = t
                best = cand

        if best is None:
            if actor_id == getattr(game, "player_id", None):
                game.log.add("Your thrust pierces empty air.")
            return True

        tlvl, target, tx, ty = best
        dmg = _verb_damage(state, "thrust")
        _apply_actor_damage(game, tlvl, actor_id, target, dmg, verb="thrust")
        try:
            level.activation_points = [
                (float(actor.pos[0]), float(actor.pos[1])),
                (float(tx), float(ty)),
            ]
            level.activation_ttl = max(5, int(getattr(game.cfg, "pattern_overlay_ttl", 10) // 2))
        except Exception:
            pass
        return True

    # Cleave (default for unknown verb values too).
    if target_abs is None:
        nearest = nearest_to_actor_within(reach)
        if nearest is not None:
            _, _, tx, ty = nearest
            target_abs = (tx, ty)

    if target_abs is None:
        # No directional hint and no nearby targets.
        if actor_id == getattr(game, "player_id", None):
            game.log.add("You begin a cleave, but nothing is close enough.")
        return True

    dir_x = float(target_abs[0] - actor_abs[0])
    dir_y = float(target_abs[1] - actor_abs[1])
    dir_mag = math.hypot(dir_x, dir_y)
    if dir_mag <= 1e-6:
        dir_x, dir_y = 1.0, 0.0
        dir_mag = 1.0
    dir_x /= dir_mag
    dir_y /= dir_mag

    hits = 0
    for tlvl, target, tx, ty in candidates:
        vec_x = float(tx - actor_abs[0])
        vec_y = float(ty - actor_abs[1])
        dist = math.hypot(vec_x, vec_y)
        if dist > reach:
            continue
        # Front hemisphere relative to target direction.
        dot = (vec_x * dir_x) + (vec_y * dir_y)
        if dot < 0.0:
            continue
        dmg = _verb_damage(state, "cleave", distance=dist, reach=reach)
        if _apply_actor_damage(game, tlvl, actor_id, target, dmg, verb="cleave"):
            hits += 1

    if hits <= 0 and actor_id == getattr(game, "player_id", None):
        game.log.add("Your cleave finds no target.")
    return True
