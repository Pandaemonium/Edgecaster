from __future__ import annotations

from typing import Tuple

from edgecaster.state.actors import Actor, Stats
from edgecaster.enemies.templates import ENEMY_TEMPLATES, load_enemy_templates, EnemyTemplate

_enemy_counter = 1


def _next_id(prefix: str = "enemy") -> str:
    global _enemy_counter
    eid = f"{prefix}_{_enemy_counter}"
    _enemy_counter += 1
    return eid


def _ensure_templates_loaded() -> None:
    if not ENEMY_TEMPLATES:
        load_enemy_templates()


def spawn_enemy(tmpl_id: str, pos: Tuple[int, int]) -> Actor:
    """Create an Actor from a template id at the given position."""
    _ensure_templates_loaded()
    tmpl: EnemyTemplate = ENEMY_TEMPLATES[tmpl_id]

    actor = Actor(
        id=_next_id(),
        name=tmpl.name,
        pos=pos,
        glyph=tmpl.glyph,
        color=tmpl.color,
        render_layer=2,
        kind="enemy",
        blocks_movement=True,
        tags={"template_id": tmpl.id, "ai": tmpl.ai, "base_attack": tmpl.base_attack, "base_defense": tmpl.base_defense},
        statuses={},
        faction=tmpl.faction,
    )
    actor.stats = Stats(
        hp=tmpl.base_hp,
        max_hp=tmpl.base_hp,
        mana=0,
        max_mana=0,
        xp=0,
        level=1,
        xp_to_next=0,
        coherence=0,
        max_coherence=0,
    )
    # stash movement speed in tags for later use by the turn/energy system
    actor.tags["speed"] = tmpl.speed
    actor.tags["tags"] = tmpl.tags.copy()
    return actor
