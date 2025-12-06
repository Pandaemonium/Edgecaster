from __future__ import annotations

from typing import Tuple
from pathlib import Path

from edgecaster.state.actors import Actor, Stats
import edgecaster.enemies.templates as templates

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


def _ensure_templates_loaded() -> None:
    if not templates.ENEMY_TEMPLATES:
        try:
            templates.load_enemy_templates(logger=_dbg)
        except Exception as e:
            _dbg(f"load_enemy_templates failed: {e!r}")


def spawn_enemy(tmpl_id: str, pos: Tuple[int, int]) -> Actor:
    """Create an Actor from a template id at the given position."""
    _ensure_templates_loaded()
    if tmpl_id not in templates.ENEMY_TEMPLATES:
        _dbg(f"Unknown enemy template id '{tmpl_id}', registry keys={list(templates.ENEMY_TEMPLATES.keys())}")
        # try reloading in case new templates were added mid-run
        _ensure_templates_loaded()
    if tmpl_id not in templates.ENEMY_TEMPLATES:
        # fallback: if any template exists, use the first; else make a dummy
        if templates.ENEMY_TEMPLATES:
            fallback = next(iter(templates.ENEMY_TEMPLATES.keys()))
            _dbg(f"Falling back to first template id '{fallback}'")
            tmpl_id = fallback
        else:
            _dbg("No templates loaded; creating placeholder enemy")
            actor = Actor(
                id=_next_id(),
                name="Unknown",
                pos=pos,
                glyph="i",
                color=(255, 120, 120),
                render_layer=2,
                kind="enemy",
                blocks_movement=True,
                tags={"ai": "skirmisher"},
                statuses={},
                faction="hostile",
                actions=("move", "wait"),
            )
            actor.stats = Stats(hp=5, max_hp=5)
            return actor

    tmpl: templates.EnemyTemplate = templates.ENEMY_TEMPLATES[tmpl_id]

    actor = Actor(
        id=_next_id(),
        name=tmpl.name,
        pos=pos,
        glyph=tmpl.glyph,
        color=tmpl.color,
        render_layer=2,
        kind="enemy",
        blocks_movement=True,
        tags={
            "template_id": tmpl.id,
            "ai": tmpl.ai,
            "base_attack": tmpl.base_attack,
            "base_defense": tmpl.base_defense,
        },
        statuses={},
        faction=tmpl.faction,
        actions=tuple(tmpl.actions) if tmpl.actions else ("move", "wait"),
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
