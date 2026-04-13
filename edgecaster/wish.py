from __future__ import annotations

import re
from typing import Any, Optional


_CURRENCY_RE_1 = re.compile(r"^\s*([+-]?\d+)\s*(bismuth|b)\s*$", re.IGNORECASE)
_CURRENCY_RE_2 = re.compile(r"^\s*(bismuth|b)\s*([+-]?\d+)\s*$", re.IGNORECASE)


def _normalize(s: str) -> str:
    return str(s or "").strip()


def _normalize_idish(s: str) -> str:
    s = _normalize(s).casefold()
    # common convenience: "wand of X" -> "wand_x"
    if s.startswith("wand of "):
        s = "wand_" + s[len("wand of ") :].strip()
    return s.replace(" ", "_")


def _try_parse_currency(text: str) -> Optional[int]:
    m = _CURRENCY_RE_1.match(text)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m = _CURRENCY_RE_2.match(text)
    if m:
        try:
            return int(m.group(2))
        except Exception:
            return None
    return None


def _resolve_item_proto_id(text: str) -> Optional[str]:
    """Best-effort map from user text -> item prototype id."""
    try:
        from edgecaster import prototypes
    except Exception:
        return None

    bucket = prototypes.get_master_bucket()
    if not bucket:
        return None

    # 1) Direct proto id match (with a little normalization)
    candidate = _normalize_idish(text)
    if candidate in bucket:
        try:
            spec = prototypes.resolve_proto(candidate)
        except Exception:
            spec = {}
        if str(spec.get("kind") or "") == "item":
            return candidate

    # 2) Match by display name (case-insensitive, exact)
    want = _normalize(text).casefold()
    if not want:
        return None

    for pid in list(bucket.keys()):
        try:
            spec = prototypes.resolve_proto(pid)
        except Exception:
            continue
        if str(spec.get("kind") or "") != "item":
            continue
        name = str(spec.get("name") or "").casefold()
        if name and name == want:
            return str(pid)

    return None


def _resolve_enemy_proto_id(text: str) -> Optional[str]:
    """Best-effort map from user text -> enemy prototype id."""
    try:
        from edgecaster import prototypes
    except Exception:
        return None

    bucket = prototypes.get_master_bucket()
    if not bucket:
        return None

    # 1) Direct proto id match
    candidate = _normalize_idish(text)
    if candidate in bucket:
        try:
            spec = prototypes.resolve_proto(candidate)
        except Exception:
            spec = {}
        kind = str(spec.get("kind") or "")
        if kind in ("enemy", "actor", ""):
            # Enemies often have no explicit kind, but have faction/ai tags
            if spec.get("faction") or spec.get("ai") or spec.get("base_hp"):
                return candidate

    # 2) Match by display name (case-insensitive, exact)
    want = _normalize(text).casefold()
    if not want:
        return None

    for pid in list(bucket.keys()):
        try:
            spec = prototypes.resolve_proto(pid)
        except Exception:
            continue
        name = str(spec.get("name") or "").casefold()
        if name and name == want:
            if spec.get("faction") or spec.get("ai") or spec.get("base_hp"):
                return str(pid)

    return None


def _try_spawn_enemy(game: Any, text: str) -> tuple[bool, str]:
    """Try to spawn an enemy matching *text* near the player."""
    proto_id = _resolve_enemy_proto_id(text)
    if not proto_id:
        return False, ""

    try:
        from edgecaster.systems import spawning as spawning_system
    except Exception:
        return False, ""

    try:
        level = game._level()
        player = getattr(level, "actors", {}).get(getattr(game, "player_id", ""))
        player_pos = getattr(player, "pos", (0, 0)) if player else (0, 0)
    except Exception:
        return False, "No active level."

    # Find a walkable tile near the player (but not ON the player).
    spawn_pos = None
    px, py = player_pos
    for r in range(1, 8):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if abs(dx) + abs(dy) != r:
                    continue
                tx, ty = px + dx, py + dy
                if not level.world.in_bounds(tx, ty):
                    continue
                if not level.world.is_walkable(tx, ty):
                    continue
                if game._actor_at(level, (tx, ty)):
                    continue
                spawn_pos = (tx, ty)
                break
            if spawn_pos:
                break
        if spawn_pos:
            break

    if spawn_pos is None:
        return False, "No open tile nearby to spawn on."

    try:
        actor = spawning_system.spawn_enemy_with_pack(
            game,
            level,
            proto_id,
            spawn_pos,
            zone_tier=int(getattr(level, "danger_tier", 1) or 1),
            schedule_ai=True,
        )
    except Exception as e:
        return False, f"Couldn't spawn {proto_id!r}: {e!r}"

    name = getattr(actor, "name", proto_id)
    try:
        game.log.add(f"A {name} materializes nearby!")
    except Exception:
        pass

    return True, ""


def apply_wish(game: Any, text: str) -> tuple[bool, str]:
    """
    Apply a dev 'wish' against the current game.

    Supported:
      - "<N> bismuth" / "bismuth <N>"
      - Enemy name or id (e.g. "stone golem", "imp") -> spawns nearby
      - Item prototype id or item name (e.g. "Destabilizer", "wand_koch", "wand of koch")
    """
    text = _normalize(text)
    if not text:
        return False, "Type a wish first."

    amt = _try_parse_currency(text)
    if amt is not None:
        try:
            game.adjust_currency(int(amt), log=True)
        except Exception:
            try:
                game.bismuth = max(0, int(getattr(game, "bismuth", 0)) + int(amt))
            except Exception:
                pass
        return True, ""

    # Try spawning an enemy first (so "stone golem" works).
    ok, err = _try_spawn_enemy(game, text)
    if ok:
        return True, ""

    proto_id = _resolve_item_proto_id(text)
    if not proto_id:
        if err:
            return False, err
        return False, f"Unknown wish: {text!r}"

    try:
        level = game._level()
        player = getattr(level, "actors", {}).get(getattr(game, "player_id", ""))
        pos = getattr(player, "pos", (0, 0)) if player is not None else (0, 0)
    except Exception:
        pos = (0, 0)

    try:
        ent = game._spawn_entity_from_template(proto_id, pos=pos)
    except Exception as e:
        return False, f"Couldn't conjure {proto_id!r}: {e!r}"

    try:
        # Grant directly to inventory (avoid needing to pick it up).
        player_id = getattr(game, "player_id", "")
        try:
            game.player_inventory.append(ent)
        except Exception:
            inv = game.get_inventory(player_id)
            inv.append(ent)
        try:
            from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
            from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
            from edgecaster.systems.inventory import _mark_inventory_graph_authority
            entity_lifecycle_system._track_runtime_entity(game, ent)
            entity_graph_ops_system.attach_entity_to_parent(game, ent, player_id, socket_id="inventory")
            _mark_inventory_graph_authority(game, player_id)
        except Exception:
            pass
    except Exception:
        pass

    # Item-granted actions can appear/disappear when inventory contents change.
    try:
        if hasattr(game, "refresh_actor_actions"):
            game.refresh_actor_actions(getattr(game, "player_id", ""))
    except Exception:
        pass

    try:
        game.log.add(f"You conjure {ent.name}.")
    except Exception:
        pass

    return True, ""
