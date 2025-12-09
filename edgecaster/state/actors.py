from dataclasses import dataclass, field
from typing import Dict, Tuple

from edgecaster.state.entities import Entity  # NEW

Pos = Tuple[int, int]


@dataclass
class Stats:
    hp: int = 5
    max_hp: int = 5
    mana: int = 0
    max_mana: int = 0
    xp: int = 0
    level: int = 1
    xp_to_next: int = 0
    coherence: int = 0
    max_coherence: int = 0

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def clamp(self) -> None:
        self.hp = max(0, min(self.hp, self.max_hp))
        self.mana = max(0, min(self.mana, self.max_mana))


@dataclass
class Actor(Entity):
    """An Entity with stats + a faction + turns in the energy queue."""
    faction: str = "neutral"
    stats: Stats = field(default_factory=Stats)

    # social / future bits
    disposition: float = 0.0
    affiliations: tuple = field(default_factory=tuple)  # tuple of faction ids

    # Which high-level Actions this actor can perform.
    # (Action names from edgecaster.systems.actions.)
    actions: tuple[str, ...] = field(default_factory=tuple)

    # statuses/tags are inherited from Entity

    @property
    def alive(self) -> bool:
        return self.stats.alive

    # Backwards-compat: some code may still use player.actor_id
    @property
    def actor_id(self) -> str:
        return self.id

    @actor_id.setter
    def actor_id(self, value: str) -> None:
        self.id = value


@dataclass
class Human(Actor):
    """Simple human actor subtype for NPCs and player-adjacent humans."""

    glyph: str = "@"
    color: Tuple[int, int, int] = (200, 200, 220)
    kind: str = "human"
