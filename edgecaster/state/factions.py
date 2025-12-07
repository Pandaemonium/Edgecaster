from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Faction:
    name: str
    description: str = ''
    allies: Dict[str, int] = field(default_factory=dict)
    enemies: Dict[str, int] = field(default_factory=dict)


@dataclass
class Reputation:
    standings: Dict[str, int] = field(default_factory=dict)

    def adjust(self, faction: str, delta: int) -> None:
        self.standings[faction] = self.standings.get(faction, 0) + delta
