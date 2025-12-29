from __future__ import annotations

from typing import Tuple
from pathlib import Path

from edgecaster.state.actors import Actor
from edgecaster import prototypes
from edgecaster import spawn_factory

# debug logger
_DEBUG_PATH = Path(__file__).resolve().parent.parent / "debug.log"


def _dbg(msg: str) -> None:
    try:
        with open(_DEBUG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[factory] {msg}\n")
    except Exception:
        pass


_enemy_counter = 1


def _next_id(prefix: str = "enemy") -> str:
    global _enemy_counter
    eid = f"{prefix}_{_enemy_counter}"
    _enemy_counter += 1
    return eid


def spawn_enemy(tmpl_id: str, pos: Tuple[int, int]) -> Actor:
    """Create an Actor from a prototype id at the given position."""
    try:
        spec = prototypes.resolve_proto(tmpl_id)
    except Exception as e:
        _dbg(f"resolve_proto({tmpl_id!r}) failed: {e!r}")
        spec = {}

    if not spec:
        _dbg(f"Unknown enemy proto id '{tmpl_id}'. Falling back to placeholder Actor.")
        # Placeholder enemy (same spirit as before)
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
            aid=_next_id(),
            pos=pos,
        )

    # Normal case: build actor from resolved proto
    return spawn_factory.build_actor_from_spec(
        spec=spec,
        aid=_next_id(),
        pos=pos,
    )
