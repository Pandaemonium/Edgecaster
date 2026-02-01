# edgecaster/spawn_factory.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import copy
import inspect
import os

from edgecaster.state.entities import Entity
from edgecaster.state.actors import Actor, Stats
from edgecaster.prototypes import bake_instance_body_schema
from edgecaster.systems.chakras import ChakraState


# Keys that are handled explicitly by build_entity_from_spec/build_actor_from_spec
# or should never be blindly copied onto the runtime instance.
_ENTITY_RESERVED_KEYS = {
    # identity / placement
    "id", "eid", "aid", "pos",
    # presentation core (handled)
    "name", "glyph", "color", "render_layer", "kind",
    # structured blobs (handled/merged)
    "tags", "statuses",
    # prototype / inheritance metadata
    "parent",
}


def _ctor_kwargs_for(cls: type, desired: Dict[str, Any]) -> Dict[str, Any]:
    """Filter kwargs to only those accepted by cls.__init__.

    This makes the factory robust as Entity/Actor signatures evolve.
    """
    try:
        sig = inspect.signature(cls.__init__)
        params = set(sig.parameters.keys())
        # Always exclude 'self'
        params.discard("self")
        return {k: v for k, v in desired.items() if k in params}
    except Exception:
        # If signature introspection fails for any reason, fall back to the safe minimum.
        safe = {}
        for k in ("id", "name", "pos", "glyph", "color", "render_layer", "kind", "tags", "statuses"):
            if k in desired:
                safe[k] = desired[k]
        return safe


def _hydrate_entity_extras(ent: Any, spec: Dict[str, Any]) -> None:
    """Copy remaining spec keys onto the entity as attributes.

    IMPORTANT: We *do* overwrite existing attributes (except reserved keys), because
    many entities/actors define defaults on the class (e.g. blocks_vision=False) and
    we want YAML/prototype values to take precedence at spawn time.
    """
    for k, v in spec.items():
        if k in _ENTITY_RESERVED_KEYS:
            continue
        try:
            setattr(ent, k, copy.deepcopy(v))
        except Exception:
            # Swallow to keep this future-proof even if some attrs are read-only.
            pass


def _as_color(x: Any, default: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
    if x is None:
        return default
    if isinstance(x, tuple) and len(x) == 3:
        return x  # type: ignore[return-value]
    if isinstance(x, list) and len(x) == 3:
        return (int(x[0]), int(x[1]), int(x[2]))
    return default


def _merge_entity_tags(base_tags: Any, override_tags: Any) -> Dict[str, Any]:
    """Entities expect tags as a dict.

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
    """If no explicit icon is specified in tags, try to auto-attach one based on proto_id."""
    if not proto_id:
        return
    if not isinstance(tags, dict):
        return

    # Respect explicit YAML
    if tags.get("icon_path") or tags.get("icon"):
        return

    # Tune these to match your actual asset layout.
    candidate_paths = [
        os.path.join("assets", "icons", f"{proto_id}.png"),
        os.path.join("assets", f"{proto_id}.png"),
        os.path.join("assets", "sprites", f"{proto_id}.png"),
    ]

    for p in candidate_paths:
        if os.path.exists(p):
            tags["icon_path"] = p.replace("\\", "/")
            return



def _init_entity_size(ent: Any, spec: Dict[str, Any]) -> None:
    """Initialize coherent size fields for render/LoD.

    Conventions (Phase 0):
    - base_size: absolute world size in LoD0 tiles (float). Defaults to YAML 'base_size' or legacy 'size' or 1.0.
    - abs_size: cached absolute size for the entity root (currently == base_size).
      (Later: dismemberment can set abs_size on spawned subtrees directly.)
    """
    try:
        bs = getattr(ent, "base_size", None)
        if not isinstance(bs, (int, float)):
            # Back-compat: many prototypes already use 'size' at the entity root.
            bs = getattr(ent, "size", None)
        if not isinstance(bs, (int, float)):
            bs = spec.get("base_size", None)
        if not isinstance(bs, (int, float)):
            bs = spec.get("size", 1.0)
        bs_f = float(bs)
        if not (bs_f > 0.0):
            bs_f = 1.0
        ent.base_size = bs_f
        # Phase 0: root absolute size equals base size.
        ent.abs_size = float(getattr(ent, "abs_size", bs_f) or bs_f)
        if not (float(ent.abs_size) > 0.0):
            ent.abs_size = bs_f
    except Exception:
        try:
            ent.base_size = 1.0
            ent.abs_size = 1.0
        except Exception:
            pass


def build_entity_from_spec(
    *,
    spec: Dict[str, Any],
    eid: str,
    pos: Tuple[int, int],
    abs_pos: Optional[Tuple[int, int]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Entity:

    """Build a plain Entity from a resolved prototype spec.

    `overrides` merges on top; `tags` merges dict-wise.

    Design intent:
    - Only pass kwargs that Entity.__init__ actually accepts.
    - Copy every remaining YAML key onto the instance afterward so new YAML
      properties (e.g. blocks_vision) "just work" without special-casing.
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
    tags = _merge_entity_tags(s.get("tags"), None)
    _maybe_attach_default_icon_path(tags, str(s.get("id") or ""))
    statuses = dict(s.get("statuses", {}) or {})

    # Build kwargs, then filter against Entity.__init__ so we never crash on
    # new YAML fields (e.g. blocks_vision) or older Entity signatures.
    desired_kwargs: Dict[str, Any] = {
        "id": eid,
        "name": name,
        "pos": pos,
        "glyph": glyph,
        "color": color,
        "render_layer": render_layer,
        "kind": kind,
        "tags": tags,
        "statuses": statuses,
        # Common optional ctor args (only used if Entity accepts them):
        "blocks_movement": bool(s.get("blocks_movement", False)),
        "blocks_vision": bool(s.get("blocks_vision", False)),
        "pos": tuple(pos),
        "abs_pos": (tuple(abs_pos) if abs_pos is not None else None),

    }

    ent = Entity(**_ctor_kwargs_for(Entity, desired_kwargs))

    # Keep a reference to the originating prototype id (critical for body schemas,
    # save/load, introspection). IMPORTANT: this must be the prototype id.
    src_pid = s.get("id")
    if src_pid is not None:
        try:
            ent.proto_id = str(src_pid)
        except Exception:
            pass

    # Optional: attach description if your Entity supports it.
    desc = s.get("description", None)
    if desc is not None:
        try:
            ent.description = desc
        except Exception:
            pass

    # Birth-time bilateral symmetry baking.
    try:
        if getattr(ent, "proto_id", None):
            ent.body_schema = bake_instance_body_schema(str(ent.proto_id))
    except Exception:
        pass

    # Finally, hydrate any remaining YAML keys onto the instance.
    _hydrate_entity_extras(ent, s)

    # Coherent size fields (render/LoD). Keeps legacy 'size' working.
    _init_entity_size(ent, s)

    return ent


def build_actor_from_spec(
    *,
    spec: Dict[str, Any],
    aid: str,
    pos: Tuple[int, int],
    abs_pos: Optional[Tuple[int, int]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Actor:

    """Build an Actor from a resolved prototype spec (enemies.yaml style).

    YAML tags handling:
    - If YAML "tags" is a dict, merge it directly into actor.tags
    - If YAML "tags" is a list (legacy classification tags), keep under actor.tags["tags"]

    Like Entities, we hydrate all remaining YAML keys onto the instance after init,
    so adding new YAML fields doesn't require more factory code.
    """
    s = copy.deepcopy(spec)
    if overrides:
        # For actors, allow a shallow override merge.
        s.update(dict(overrides))

    name = s.get("name") or s.get("id") or "Actor"
    glyph = s.get("glyph", "@")
    color = _as_color(s.get("color"), (255, 255, 255))
    faction = s.get("faction", "neutral")

    # Preserve explicit empty action lists from YAML.
    raw_actions = s.get("actions", None)
    actions = ("move", "wait") if raw_actions is None else tuple(raw_actions)

    base_hp = int(s.get("base_hp", 1) or 1)
    base_attack = int(s.get("base_attack", 1) or 1)
    base_defense = int(s.get("base_defense", 0) or 0)
    speed = float(s.get("speed", 1.0) or 1.0)
    ai_name = s.get("ai", "idle")
    xp = int(s.get("xp", 0) or 0)

    actor_tags: Dict[str, Any] = {
        "template_id": s.get("id"),
        "ai": ai_name,
        "base_attack": base_attack,
        "base_defense": base_defense,
    }

    desired_kwargs: Dict[str, Any] = {
        "id": aid,
        "name": name,
        "pos": pos,
        "glyph": glyph,
        "color": color,
        "render_layer": 2,
        "kind": "enemy",
        "blocks_movement": True,
        "tags": actor_tags,
        "statuses": {},
        "faction": faction,
        "actions": actions,
        # Some projects put these directly on Actor.__init__ as well:
        "blocks_vision": bool(s.get("blocks_vision", False)),
        "pos": tuple(pos),
        "abs_pos": (tuple(abs_pos) if abs_pos is not None else None),

    }

    actor = Actor(**_ctor_kwargs_for(Actor, desired_kwargs))

    # Keep a reference to the originating prototype id.
    src_pid = s.get("id")
    if src_pid is not None:
        try:
            actor.proto_id = str(src_pid)
        except Exception:
            pass

    # Stats
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
    try:
        actor.tags["speed"] = speed
    except Exception:
        pass

    # Preserve YAML "tags" field
    yaml_tags = s.get("tags", None)
    if isinstance(yaml_tags, dict):
        try:
            actor.tags.update(copy.deepcopy(yaml_tags))
        except Exception:
            pass
    elif yaml_tags is not None:
        try:
            actor.tags["tags"] = copy.deepcopy(yaml_tags)
        except Exception:
            pass
    _maybe_attach_default_icon_path(actor.tags, str(s.get("id") or ""))

    # Optional: stash xp
    try:
        actor.tags["xp"] = xp
    except Exception:
        pass

    # Optional: description
    desc = s.get("description", None)
    if desc is not None:
        try:
            actor.description = desc
        except Exception:
            pass

    # Birth-time bilateral symmetry baking
    try:
        if getattr(actor, "proto_id", None):
            actor.body_schema = bake_instance_body_schema(str(actor.proto_id))
    except Exception:
        pass

    # Initialize chakra state for pattern generation.
    # All actors start with "body" (torso/core) chakra unlocked and active (if they have a body schema).
    # Note: "body" is the root node ID in the human body schema.
    # Player characters may have additional chakras unlocked via progression.
    try:
        if hasattr(actor, "body_schema") and actor.body_schema:
            actor.chakra_state = ChakraState(
                unlocked={"body"},
                active={"body"},
            )
    except Exception:
        pass

    # Hydrate remaining YAML keys onto the instance.
    _hydrate_entity_extras(actor, s)

    # Coherent size fields (render/LoD). Keeps legacy 'size' working.
    _init_entity_size(actor, s)

    return actor
