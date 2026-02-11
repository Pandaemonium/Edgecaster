"""POI content realization helpers.

This module owns the runtime realization of POI content into a loaded level.
It is invoked by ``Game._spawn_poi_contents`` as a thin delegate so the Game
orchestrator stays focused on coordination instead of spawn details.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from edgecaster.content import npcs
from edgecaster.enemies import factory as enemy_factory
from edgecaster.state.actors import Human, Stats
from edgecaster.systems import spawning as spawning_system

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState


def spawn_poi_contents(*args, **kwargs):
    # Legacy POI system disabled.
    return
