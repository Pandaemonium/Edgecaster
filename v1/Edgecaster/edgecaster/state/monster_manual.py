from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional, Mapping, Any

from edgecaster.state.actors import Actor, Stats


Pos = Tuple[int, int]


@dataclass(frozen=True)
class MonsterTemplate:
    key: str
    name: str
    faction: str
    base_hp: int
    # Names of actions from edgecaster.systems.actions
    actions: tuple[str, ...]
    # XP settings: either a fixed value or read from cfg.<xp_cfg_key>
    xp: Optional[int] = None
    xp_cfg_key: Optional[str] = None
    # Placeholder for future stuff (glyph, color, AI profile, etc.)
    tags: Mapping[str, Any] = frozenset()  # type: ignore[assignment]


MONSTERS: Dict[str, MonsterTemplate] = {
    "imp": MonsterTemplate(
        key="imp",
        name="Imp",
        faction="hostile",
        base_hp=5,
        actions=("move", "wait", "imp_taunt"),
        xp_cfg_key="xp_per_imp",
    ),
    "fractal_echo": MonsterTemplate(
        key="fractal_echo",
        name="Fractal Echo",
        faction="hostile",
        base_hp=6,
        actions=("move", "wait"),
        xp_cfg_key="xp_per_imp",  # reuse for now
    ),
}


def make_monster(
    kind: str,
    *,
    id: str,
    pos: Pos,
    cfg: Any,
) -> Actor:
    """
    Instantiate an Actor from a MonsterTemplate.

    - Uses template.base_hp for hp/max_hp
    - Uses template.actions for actor.actions
    - Fills tags["xp"] from template.xp or cfg.<xp_cfg_key> if present
    """
    if kind not in MONSTERS:
        raise KeyError(f"Unknown monster kind '{kind}'")

    tmpl = MONSTERS[kind]

    hp = tmpl.base_hp
    xp = tmpl.xp
    if xp is None and tmpl.xp_cfg_key:
        xp = getattr(cfg, tmpl.xp_cfg_key)

    stats = Stats(hp=hp, max_hp=hp)

    tags = dict(tmpl.tags)
    if xp is not None:
        tags.setdefault("xp", xp)

    actor = Actor(
        id=id,
        name=tmpl.name,
        pos=pos,
        faction=tmpl.faction,
        stats=stats,
        tags=tags,
        actions=tmpl.actions,
    )
    return actor
