from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Tuple


@dataclass(frozen=True)
class DamagePolicy:
    """Declarative target filtering for damaging effects.

    This keeps "who can be hit" centralized instead of re-encoding faction and
    self-damage rules inside each ability implementation.
    """

    include_self: bool = False
    include_hostile: bool = True
    include_neutral: bool = False
    include_friendly: bool = False
    include_environment: bool = False
    require_damageable_hp: bool = True


def _is_damageable(obj: Any) -> bool:
    stats = getattr(obj, "stats", None)
    return stats is not None and hasattr(stats, "hp")


def _relation(
    game: Any,
    source_actor: Any,
    source_actor_id: str,
    target_id: str,
    target_obj: Any,
) -> str:
    """Return one of: self|hostile|friendly|neutral|environment."""
    if str(target_id) == str(source_actor_id):
        return "self"

    # Non-actors / no faction are treated as environment.
    if not hasattr(target_obj, "faction"):
        return "environment"

    if source_actor is None:
        return "neutral"

    # Reputation-aware hostility if available.
    #
    # IMPORTANT:
    # Combat movement/attacks treat hostility as "either direction"
    # (A hostile to B OR B hostile to A). Ability damage should use the same
    # semantics, otherwise offensive abilities can incorrectly see no hostiles
    # when the player is not explicitly hostile *toward* monsters.
    try:
        game_is_hostile = getattr(game, "is_hostile")
        is_hostile = bool(game_is_hostile(source_actor, target_obj)) or bool(
            game_is_hostile(target_obj, source_actor)
        )
    except Exception:
        is_hostile = False
    if is_hostile:
        return "hostile"

    src_f = str(getattr(source_actor, "faction", "") or "")
    dst_f = str(getattr(target_obj, "faction", "") or "")
    if src_f and dst_f and src_f == dst_f:
        return "friendly"

    return "neutral"


def _allows(policy: DamagePolicy, relation: str) -> bool:
    if relation == "self":
        return bool(policy.include_self)
    if relation == "hostile":
        return bool(policy.include_hostile)
    if relation == "friendly":
        return bool(policy.include_friendly)
    if relation == "neutral":
        return bool(policy.include_neutral)
    if relation == "environment":
        return bool(policy.include_environment)
    return False


def can_damage_target(
    game: Any,
    level: Any,
    source_actor_id: str,
    target_id: str,
    target_obj: Any,
    policy: DamagePolicy,
) -> bool:
    """Fast single-target predicate for ad-hoc ability loops."""
    if target_obj is None:
        return False
    if policy.require_damageable_hp and not _is_damageable(target_obj):
        return False

    source_actor = None
    try:
        source_actor = getattr(level, "actors", {}).get(source_actor_id)
    except Exception:
        source_actor = None

    rel = _relation(game, source_actor, source_actor_id, target_id, target_obj)
    return _allows(policy, rel)


def iter_damage_targets(
    game: Any,
    level: Any,
    source_actor_id: str,
    policy: DamagePolicy,
    *,
    include_actors: bool = True,
    include_entities: bool = True,
) -> Iterator[Tuple[str, Any]]:
    """Iter targets passing policy filters.

    Yields `(target_id, target_obj)` with actor/entity dedupe by id.
    """
    seen: set[str] = set()

    source_actor = None
    try:
        source_actor = getattr(level, "actors", {}).get(source_actor_id)
    except Exception:
        source_actor = None

    if include_actors:
        for tid, obj in list(getattr(level, "actors", {}).items()):
            sid = str(tid)
            if sid in seen:
                continue
            seen.add(sid)
            if policy.require_damageable_hp and not _is_damageable(obj):
                continue
            rel = _relation(game, source_actor, source_actor_id, sid, obj)
            if _allows(policy, rel):
                yield sid, obj

    if include_entities:
        for tid, obj in list(getattr(level, "entities", {}).items()):
            sid = str(tid)
            if sid in seen:
                continue
            seen.add(sid)
            if policy.require_damageable_hp and not _is_damageable(obj):
                continue
            rel = _relation(game, source_actor, source_actor_id, sid, obj)
            if _allows(policy, rel):
                yield sid, obj
