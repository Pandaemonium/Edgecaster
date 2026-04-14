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
    game: "Game",
) -> Actor:
    """Create an Actor from a prototype id at the given position.

    Uses ``game._new_id()`` to allocate a session-wide sequential ID so every
    spawned actor participates in the same deterministic ID space.
    """
    aid = game._new_id()

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
