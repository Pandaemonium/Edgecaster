# edgecaster/spawn_factory.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union
import copy

from edgecaster.state.entities import Entity
from edgecaster.state.actors import Actor, Stats


def _as_color(x: Any, default: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
    if x is None:
        return default
    if isinstance(x, tuple) and len(x) == 3:
        return x  # type: ignore[return-value]
    if isinstance(x, list) and len(x) == 3:
        return (int(x[0]), int(x[1]), int(x[2]))
    return default


def _merge_entity_tags(base_tags: Any, override_tags: Any) -> Dict[str, Any]:
    """
    Entities expect tags as a dict.
    - If base is missing or non-dict, treat as empty.
    - Overrides must be dict-like; otherwise ignored.
    """
    out: Dict[str, Any] = {}
    if isinstance(base_tags, dict):
        out.update(base_tags)
    if isinstance(override_tags, dict):
        out.update(override_tags)
    return out


def build_entity_from_spec(
    *,
    spec: Dict[str, Any],
    eid: str,
    pos: Tuple[int, int],
    overrides: Optional[Dict[str, Any]] = None,
) -> Entity:
    """
    Build a plain Entity from a resolved prototype spec.
    `overrides` merges on top; `tags` merges dict-wise.
    """
    s = copy.deepcopy(spec)

    # Apply overrides (tags merge)
    if overrides:
        o = dict(overrides)
        o_tags = o.pop("tags", None)
        s_tags = _merge_entity_tags(s.get("tags"), o_tags)
        s.update(o)
        s["tags"] = s_tags

    name = s.get("name") or s.get("id") or "Entity"
    glyph = s.get("glyph", "?")
    color = _as_color(s.get("color"), (255, 255, 255))
    kind = s.get("kind", "generic")
    render_layer = int(s.get("render_layer", 1) or 1)
    blocks_movement = bool(s.get("blocks_movement", False))
    tags = _merge_entity_tags(s.get("tags"), None)
    statuses = dict(s.get("statuses", {}) or {})

    ent = Entity(
        id=eid,
        name=name,
        pos=pos,
        glyph=glyph,
        color=color,  # type: ignore[arg-type]
        render_layer=render_layer,
        kind=kind,
        blocks_movement=blocks_movement,
        tags=tags,
        statuses=statuses,
    )

    # Optional: attach description if your Entity supports it (it seems to in your project).
    # This is safe even if not declared in the dataclass, because Python allows dynamic attrs.
    desc = s.get("description", None)
    if desc is not None:
        try:
            ent.description = desc
        except Exception:
            pass

    return ent


def build_actor_from_spec(
    *,
    spec: Dict[str, Any],
    aid: str,
    pos: Tuple[int, int],
    overrides: Optional[Dict[str, Any]] = None,
) -> Actor:
    """
    Build an Actor from a resolved prototype spec (enemies.yaml style).
    We keep “classification tags” from YAML under actor.tags["tags"] (list or dict).
    """
    s = copy.deepcopy(spec)
    if overrides:
        # For actors, we’ll allow a shallow override merge; if you later want patch ops,
        # do it at the prototype layer.
        s.update(dict(overrides))

    name = s.get("name") or s.get("id") or "Actor"
    glyph = s.get("glyph", "@")
    color = _as_color(s.get("color"), (255, 255, 255))
    faction = s.get("faction", "neutral")
    actions = tuple(s.get("actions", ("move", "wait")) or ("move", "wait"))

    base_hp = int(s.get("base_hp", 1) or 1)
    base_attack = int(s.get("base_attack", 1) or 1)
    base_defense = int(s.get("base_defense", 0) or 0)
    speed = float(s.get("speed", 1.0) or 1.0)
    ai_name = s.get("ai", "idle")
    xp = int(s.get("xp", 0) or 0)

    actor = Actor(
        id=aid,
        name=name,
        pos=pos,
        glyph=glyph,
        color=color,
        render_layer=2,
        kind="enemy",
        blocks_movement=True,
        tags={
            "template_id": s.get("id"),
            "ai": ai_name,
            "base_attack": base_attack,
            "base_defense": base_defense,
        },
        statuses={},
        faction=faction,
        actions=actions,
    )

    actor.stats = Stats(
        hp=base_hp,
        max_hp=base_hp,
        mana=int(s.get("base_mana", 0) or 0),
        max_mana=int(s.get("base_mana", 0) or 0),
        xp=0,
        level=1,
        xp_to_next=0,
        coherence=0,
        max_coherence=0,
    )

    # Movement speed hint for energy system
    actor.tags["speed"] = speed

    # Preserve YAML “tags” field without forcing schema:
    # - enemies.yaml currently uses list tags
    # - entities.yaml uses dict tags
    if "tags" in s:
        actor.tags["tags"] = copy.deepcopy(s.get("tags"))

    # Optional: stash xp
    actor.tags["xp"] = xp

    # Optional: description, same rationale as build_entity_from_spec
    desc = s.get("description", None)
    if desc is not None:
        try:
            actor.description = desc
        except Exception:
            pass

    return actor
