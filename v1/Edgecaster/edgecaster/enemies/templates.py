from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Set, Tuple, Iterable

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


@dataclass
class EnemyTemplate:
    id: str
    name: str
    glyph: str
    color: Tuple[int, int, int]
    actions: Tuple[str, ...]
    base_hp: int
    base_attack: int
    base_defense: int
    speed: float
    faction: str
    ai: str
    tags: Set[str]


ENEMY_TEMPLATES: Dict[str, EnemyTemplate] = {}


def _build_template(entry: dict) -> EnemyTemplate:
    return EnemyTemplate(
        id=entry["id"],
        name=entry["name"],
        glyph=entry["glyph"],
        color=tuple(entry["color"]),
        actions=tuple(entry.get("actions", ("move", "wait"))),
        base_hp=int(entry.get("base_hp", 1)),
        base_attack=int(entry.get("base_attack", 1)),
        base_defense=int(entry.get("base_defense", 0)),
        speed=float(entry.get("speed", 1.0)),
        faction=entry.get("faction", "neutral"),
        ai=entry.get("ai", "idle"),
        tags=set(entry.get("tags", [])),
    )


def load_enemy_templates(path: Path | str | None = None, logger=None) -> None:
    """Load enemy templates from YAML and populate ENEMY_TEMPLATES."""
    global ENEMY_TEMPLATES
    if path is None:
        path = Path(__file__).resolve().parent.parent / "content" / "enemies.yaml"
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Enemy template file not found: {path}")
    if yaml is None:
        raise ImportError("PyYAML is required to load enemy templates.")
    text = path.read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, Iterable):
        raise ValueError(f"Enemy template file malformed: {path}")
    ENEMY_TEMPLATES.clear()
    for entry in data:
        tmpl = _build_template(entry)
        ENEMY_TEMPLATES[tmpl.id] = tmpl
    if logger:
        logger(f"[enemies] loaded {len(ENEMY_TEMPLATES)} templates from {path}")
        logger(f"[enemies] ids: {list(ENEMY_TEMPLATES.keys())}")
    if logger:
        logger(f"[enemies] loaded {len(ENEMY_TEMPLATES)} templates from {path}")
        logger(f"[enemies] ids: {list(ENEMY_TEMPLATES.keys())}")


def get_template(tmpl_id: str) -> EnemyTemplate:
    return ENEMY_TEMPLATES[tmpl_id]
