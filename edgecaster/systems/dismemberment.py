"""Dismemberment system.

Handles severing body parts from actors during combat. A dismembering attack
promotes an internal body-node entity into a world item at the defender's
position so it can be picked up.

Entry point: attempt_dismember(game, level, attacker, defender)
  Called from combat.attack() when the attacker carries dismember_chance > 0.

Key invariants:
- Only nodes whose top-level anatomy proto has dismemberable=True are eligible.
- A node that is already severed (tags["severed"]=True) is skipped.
- The body-node entity already exists in the entity graph; we promote it rather
  than creating a new object.
- The defender's chakra component is updated so the reducer automatically drops
  any bonuses from the severed node.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def attempt_dismember(
    game: "Game",
    level: "LevelState",
    attacker: Any,
    defender: Any,
) -> bool:
    """Roll for dismemberment and execute if triggered. Returns True if a part was severed."""
    chance = _dismember_chance(game, attacker)
    _dbg(game, f"[dismember] chance={chance:.2f} attacker={_entity_id(attacker)!r}")
    if chance <= 0.0:
        return False
    roll = game.rng.random()
    _dbg(game, f"[dismember] roll={roll:.2f} vs chance={chance:.2f} → {'FIRE' if roll < chance else 'miss'}")
    if roll >= chance:
        return False
    return sever_random_body_part(game, level, defender)


def sever_random_body_part(
    game: "Game",
    level: "LevelState",
    defender: Any,
) -> bool:
    """Sever a random eligible body part from defender. Returns True on success."""
    candidates = _dismemberable_body_node_entities(game, defender)
    if not candidates:
        return False
    try:
        target_ent = game.rng.choice(candidates)
    except Exception:
        if not candidates:
            return False
        target_ent = candidates[0]
    return _sever(game, level, defender, target_ent)


def sever_body_part_by_node_id(
    game: "Game",
    level: "LevelState",
    defender: Any,
    node_full_id: str,
) -> bool:
    """Sever a specific body part (by body_full_id tag). Returns True on success."""
    defender_eid = _entity_id(defender)
    if not defender_eid:
        return False
    graph = getattr(game, "entity_graph", None)
    if graph is None:
        return False
    for child_eid in graph.get_children(defender_eid, socket_id="body"):
        node_ent = _find_runtime(game, child_eid)
        if node_ent is None:
            continue
        tags = _tags(node_ent)
        if str(tags.get("body_full_id", "")) == str(node_full_id):
            return _sever(game, level, defender, node_ent)
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dismember_chance(game: "Game", attacker: Any) -> float:
    """Return the dismember_chance from the attacker's equipped weapon or own tags."""
    # Check the attacker entity's own tags first (natural weapons / NPC abilities).
    own_tags = _tags(attacker)
    raw = own_tags.get("dismember_chance")
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass

    # Scan all equipped items (any graph socket that isn't inventory/body/resolve).
    # This is robust to zoom-path slot IDs like "arm/hand" without guessing names.
    _NON_EQUIP = frozenset({"inventory", "body", "resolve"})
    attacker_id = _entity_id(attacker)
    graph = getattr(game, "entity_graph", None)
    _dbg(game, f"[dismember] _dismember_chance: attacker_id={attacker_id!r} graph={'yes' if graph else 'NO'}")
    if attacker_id and graph is not None:
        children = graph.get_children(attacker_id)
        _dbg(game, f"[dismember] attacker graph children ({len(children)}): {children}")
        for child_eid in children:
            node = graph.get_node(child_eid)
            socket = node.socket_id if node else None
            _dbg(game, f"[dismember]   child {child_eid!r} socket={socket!r}")
            if node is None or (node.socket_id in _NON_EQUIP) or node.socket_id is None:
                continue
            item = _find_runtime(game, child_eid)
            if item is not None:
                raw = _tags(item).get("dismember_chance")
                _dbg(game, f"[dismember]   equipped item {getattr(item,'name','?')!r} dismember_chance={raw!r}")
                if raw is not None:
                    try:
                        return float(raw)
                    except Exception:
                        pass
    return 0.0


def _dismemberable_body_node_entities(game: "Game", defender: Any) -> List[Any]:
    """Return body-node entities on defender that are eligible for severing.

    Walks the full body tree (expanding levels on demand) rather than relying
    on pre-expansion having been called correctly at spawn time.
    """
    defender_eid = _entity_id(defender)
    if not defender_eid:
        _dbg(game, "[dismember] _dismemberable: defender has no entity_id")
        return []
    graph = getattr(game, "entity_graph", None)
    if graph is None:
        _dbg(game, "[dismember] _dismemberable: no entity_graph on game")
        return []

    # Recursively expand and walk all body-socket descendants.
    try:
        from edgecaster.systems import entity_lifecycle as _elc
        from edgecaster.systems import entity_body as _eb
    except Exception:
        return []

    all_body_entities: List[Any] = []
    visit_queue = [defender_eid]
    visited: set = set()

    while visit_queue:
        parent_eid = visit_queue.pop(0)
        if parent_eid in visited:
            continue
        visited.add(parent_eid)

        children = graph.get_children(parent_eid, socket_id="body")
        if not children:
            # Expand this node and retry.
            try:
                _elc.expand_entity(game, parent_eid, reason="dismember")
                children = graph.get_children(parent_eid, socket_id="body")
            except Exception:
                pass

        for child_eid in children:
            if child_eid in visited:
                continue
            ent = _find_runtime(game, child_eid)
            if ent is None:
                # obj not on graph node yet — try entity_lifecycle directly
                try:
                    ent = _elc.find_runtime_entity(game, child_eid)
                except Exception:
                    pass
            if ent is not None:
                all_body_entities.append(ent)
            visit_queue.append(child_eid)

    _dbg(game, f"[dismember] defender {defender_eid!r} total body entities: {len(all_body_entities)}")

    candidates: List[Any] = []
    for ent in all_body_entities:
        tags = _tags(ent)
        if tags.get("severed"):
            continue
        proto_id = str(tags.get("body_node_proto_id", "") or "")
        is_dis = _proto_is_dismemberable(proto_id)
        _dbg(game, f"[dismember]   entity proto={proto_id!r} dismemberable={is_dis}")
        if proto_id and is_dis:
            candidates.append(ent)

    _dbg(game, f"[dismember] candidates: {len(candidates)}")
    return candidates


def _proto_is_dismemberable(proto_id: str) -> bool:
    """Return True if the anatomy prototype is marked dismemberable=True."""
    try:
        from edgecaster import prototypes as _prototypes
        raw = _prototypes.get_raw_proto(str(proto_id))
        return bool(raw.get("dismemberable"))
    except Exception:
        return False


def _sever(
    game: "Game",
    level: "LevelState",
    defender: Any,
    body_node_ent: Any,
) -> bool:
    """Execute the sever: deactivate chakra node, detach from graph, drop in world."""
    tags = _tags(body_node_ent)
    node_full_id = str(tags.get("body_full_id", "") or "")
    body_node_proto_id = str(tags.get("body_node_proto_id", "") or "")
    node_name = str(getattr(body_node_ent, "name", None) or body_node_proto_id or "body part")

    # 1. Deactivate the corresponding chakra node on the defender so the reducer
    #    drops any bonuses from it on the next tick.
    if node_full_id:
        try:
            from edgecaster.systems import chakra_items as chakra_items_system
            chakra_items_system.toggle_actor_chakra(
                defender, node_full_id, active=False, game=game
            )
        except Exception:
            pass

    # 2. Detach from the body socket in the entity graph.
    try:
        from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
        entity_graph_ops_system.detach_entity_from_parent(game, body_node_ent)
    except Exception:
        pass

    # 3. Remove from the expanded-children registry so future
    #    collapse/expand cycles on the defender don't try to snapshot it.
    try:
        from edgecaster.systems.entity_lifecycle import _expanded_children_map
        defender_eid = _entity_id(defender)
        expanded = _expanded_children_map(game)
        body_node_eid = _entity_id(body_node_ent)
        for parent_children in expanded.values():
            parent_children.discard(body_node_eid)
        expanded.pop(body_node_eid, None)
    except Exception:
        pass

    # 4. Promote the entity to a world item.
    try:
        tags["severed"] = True
        tags["severed_from"] = _entity_id(defender)
        tags["pickable"] = True
        tags.pop("internal_entity", None)
        # Clear the body-node flag so it won't be treated as an anatomy node.
        tags["body_node"] = False
        body_node_ent.tags = tags
    except Exception:
        pass

    try:
        body_node_ent.name = f"Severed {node_name.capitalize()}"
    except Exception:
        pass

    try:
        body_node_ent.glyph = "%"
    except Exception:
        pass

    try:
        body_node_ent.kind = "item"
    except Exception:
        pass

    try:
        body_node_ent.render_layer = 1
    except Exception:
        pass

    try:
        body_node_ent.blocks_movement = False
    except Exception:
        pass

    # 5. Place at the defender's current position in the loaded zone.
    defender_pos = getattr(defender, "pos", None)
    defender_abs = getattr(defender, "abs_pos", None)
    if isinstance(defender_pos, (tuple, list)) and len(defender_pos) >= 2:
        try:
            body_node_ent.pos = (int(defender_pos[0]), int(defender_pos[1]))
        except Exception:
            pass
    if isinstance(defender_abs, (tuple, list)) and len(defender_abs) >= 2:
        try:
            body_node_ent.abs_pos = (int(defender_abs[0]), int(defender_abs[1]))
        except Exception:
            pass

    body_node_eid = _entity_id(body_node_ent)
    if body_node_eid:
        try:
            level.entities[body_node_eid] = body_node_ent
        except Exception:
            pass

    # 6. Log the event.
    try:
        defender_name = getattr(defender, "name", "creature")
        is_player_defender = str(getattr(defender, "id", "")) == str(
            getattr(game, "player_id", "")
        )
        if is_player_defender:
            game.log.add(f"Your {node_name.lower()} is severed!")
        else:
            game.log.add(
                f"{defender_name}'s {node_name.lower()} is severed and falls to the ground!"
            )
    except Exception:
        pass

    return True


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _entity_id(obj: Any) -> str:
    try:
        eid = str(getattr(obj, "entity_id", "") or "").strip()
        if eid:
            return eid
        return str(getattr(obj, "id", "") or "").strip()
    except Exception:
        return ""


def _tags(obj: Any) -> dict:
    try:
        raw = getattr(obj, "tags", None)
        return dict(raw) if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _find_runtime(game: Any, entity_id: str) -> Optional[Any]:
    """Lightweight entity lookup: graph node obj → SpatialIndex → zone dicts."""
    eid = str(entity_id or "").strip()
    if not eid:
        return None
    try:
        graph = getattr(game, "entity_graph", None)
        if graph is not None:
            node = graph.get_node(eid)
            if node is not None and node.obj is not None:
                return node.obj
    except Exception:
        pass
    try:
        from edgecaster.systems import entity_lifecycle as _elc
        return _elc.find_runtime_entity(game, eid)
    except Exception:
        return None


def _dbg(game: Any, msg: str) -> None:
    """Write a debug message to debug.log (via game._debug). Silent when unavailable."""
    try:
        dbg = getattr(game, "_debug", None)
        if callable(dbg):
            dbg(msg)
    except Exception:
        pass
