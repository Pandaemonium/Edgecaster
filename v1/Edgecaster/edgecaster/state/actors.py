from dataclasses import dataclass, field
from typing import Dict, Tuple

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
class Actor:
    actor_id: str
    name: str
    pos: Pos
    faction: str = "neutral"
    stats: Stats = field(default_factory=Stats)
    tags: Dict[str, int] = field(default_factory=dict)
    disposition: float = 0.0  # placeholder for future reputation system
    affiliations: tuple = field(default_factory=tuple)  # tuple of faction ids
    statuses: Dict[str, int] = field(default_factory=dict)  # status_name -> remaining turns

    @property
    def alive(self) -> bool:
        return self.stats.alive
