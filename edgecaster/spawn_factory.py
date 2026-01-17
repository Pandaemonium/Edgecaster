# edgecaster/spawn_factory.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Union
import copy

from edgecaster.state.entities import Entity
from edgecaster.state.actors import Actor, Stats
from edgecaster.prototypes import bake_instance_body_schema
import os


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


def _maybe_attach_default_icon_path(tags: Dict[str, Any], proto_id: Optional[str]) -> None:
    """
    If no explicit icon is specified in tags, try to auto-attach one based on proto_id.
    This is purely a convenience fallback to reduce YAML boilerplate.
    """
    if not proto_id:
        return
    if not isinstance(tags, dict):
        return

    # Respect explicit YAML
    if tags.get("icon_path") or tags.get("icon"):
        return

    # Tune these to match your actual asset layout.
    # Order matters: first match wins.
    candidate_paths = [
        os.path.join("assets", "icons", f"{proto_id}.png"),
        os.path.join("assets", f"{proto_id}.png"),
        os.path.join("assets", "sprites", f"{proto_id}.png"),
    ]

    for p in candidate_paths:
        if os.path.exists(p):
            # Normalize slashes for consistency across platforms
            tags["icon_path"] = p.replace("\\", "/")
            return


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
    _maybe_attach_default_icon_path(tags, str(s.get("id") or ""))
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

    # Keep a reference to the originating prototype id (critical for body schemas, save/load, introspection).
    # IMPORTANT: this must be the prototype id, not the runtime instance id.
    src_pid = s.get("id")  # resolved prototype id
    if src_pid is not None:
        try:
            ent.proto_id = str(src_pid)
        except Exception:
            pass


    # Optional: attach description if your Entity supports it (it seems to in your project).
    # This is safe even if not declared in the dataclass, because Python allows dynamic attrs.
    desc = s.get("description", None)
    if desc is not None:
        try:
            ent.description = desc
        except Exception:
            pass


    # Birth-time bilateral symmetry baking:
    # If this entity has mirrored nodes (e.g. arm_m), rewrite their layouts/props and proto
    # references so zooming into them resolves mirrored sub-schemas without inventory_scene
    # needing to do any mirror math.
    try:
        if getattr(ent, "proto_id", None):
            ent.body_schema = bake_instance_body_schema(str(ent.proto_id))
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
    YAML tags handling:
    - If the YAML "tags" field is a dict, we merge it directly into actor.tags (so icon_path/icon work like Entities).
    - If the YAML "tags" field is a list (legacy classification tags), we keep it under actor.tags["tags"].
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
    # Preserve explicit empty action lists from YAML (e.g. training dummies).
    # Only fall back to default if the key is missing or None.
    raw_actions = s.get("actions", None)
    if raw_actions is None:
        actions = ("move", "wait")
    else:
        actions = tuple(raw_actions)

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

    # Keep a reference to the originating prototype id (critical for body schemas, save/load, introspection).
    # IMPORTANT: this must be the prototype id, not the runtime instance id.
    src_pid = s.get("id")  # resolved prototype id
    if src_pid is not None:
        try:
            actor.proto_id = str(src_pid)
        except Exception:
            pass


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

    # Preserve YAML "tags" field without forcing schema:
    # - If YAML tags is a dict, merge into actor.tags (so icon_path/icon work like Entities).
    # - If YAML tags is a list (legacy enemies.yaml classification tags), keep under actor.tags["tags"].
    yaml_tags = s.get("tags", None)
    if isinstance(yaml_tags, dict):
        actor.tags.update(copy.deepcopy(yaml_tags))
    elif yaml_tags is not None:
        actor.tags["tags"] = copy.deepcopy(yaml_tags)
    _maybe_attach_default_icon_path(actor.tags, str(s.get("id") or ""))


    # Optional: stash xp
    actor.tags["xp"] = xp

    # Optional: description, same rationale as build_entity_from_spec
    desc = s.get("description", None)
    if desc is not None:
        try:
            actor.description = desc
        except Exception:
            pass


    # Birth-time bilateral symmetry baking (see build_entity_from_spec for details).
    try:
        if getattr(actor, "proto_id", None):
            actor.body_schema = bake_instance_body_schema(str(actor.proto_id))
    except Exception:
        pass

    return actor