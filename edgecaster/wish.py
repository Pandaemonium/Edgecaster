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


def apply_wish(game: Any, text: str) -> tuple[bool, str]:
    """
    Apply a dev 'wish' against the current game.

    Supported:
      - "<N> bismuth" / "bismuth <N>"
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

    proto_id = _resolve_item_proto_id(text)
    if not proto_id:
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
        game.player_inventory.append(ent)
    except Exception:
        try:
            inv = game.get_inventory(getattr(game, "player_id", ""))
            inv.append(ent)
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

