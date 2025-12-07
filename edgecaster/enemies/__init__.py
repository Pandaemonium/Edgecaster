"""Enemy templates and factories."""

from .templates import EnemyTemplate, ENEMY_TEMPLATES, load_enemy_templates
from .factory import spawn_enemy

__all__ = [
    "EnemyTemplate",
    "ENEMY_TEMPLATES",
    "load_enemy_templates",
    "spawn_enemy",
]
