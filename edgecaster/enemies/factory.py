from __future__ import annotations

from typing import Tuple, TYPE_CHECKING

from edgecaster.state.actors import Actor
from edgecaster import prototypes
from edgecaster import spawn_factory

if TYPE_CHECKING:
    from edgecaster.game import Game


def spawn_enemy(
    tmpl_id: str,
    pos: Tuple[int, int],
    abs_pos: Tuple[int, int] | None = None,
    *,
    game: "Game | None" = None,
) -> Actor:
    """Create an Actor from a prototype id at the given position.

    When *game* is provided, uses ``game._new_id()`` so the actor participates
    in the session-wide sequential ID space (avoids determinism violations from
    a module-level global counter).  Falls back to a simple sequential counter
    only for legacy callers that do not pass *game*.
    """
    aid = game._new_id() if game is not None else _fallback_next_id()

    try:
        spec = prototypes.resolve_proto(tmpl_id)
    except Exception:
        spec = {}

    if not spec:
        return spawn_factory.build_actor_from_spec(
            spec={
                "id": "unknown_enemy",
                "name": "Unknown",
                "glyph": "i",
                "color": (255, 120, 120),
                "faction": "hostile",
                "ai": "skirmisher",
                "base_hp": 5,
                "base_attack": 1,
                "base_defense": 0,
                "speed": 1.0,
                "actions": ("move", "wait"),
                "tags": ["placeholder"],
            },
            aid=aid,
            pos=pos,
            abs_pos=abs_pos,
        )

    return spawn_factory.build_actor_from_spec(
        spec=spec,
        aid=aid,
        pos=pos,
        abs_pos=abs_pos,
    )


# ---------------------------------------------------------------------------
# Fallback counter — only for callers that cannot pass *game* yet.
# Tagged for removal once all callers are updated.
# [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
# ---------------------------------------------------------------------------
_fallback_counter = 1


def _fallback_next_id(prefix: str = "enemy") -> str:
    global _fallback_counter
    eid = f"{prefix}_{_fallback_counter}"
    _fallback_counter += 1
    return eid
