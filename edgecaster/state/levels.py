"""LevelState and related level metadata definitions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Set

from edgecaster.state.world import World
from edgecaster.state.actors import Actor
from edgecaster.state.entities import Entity
from edgecaster.patterns import builder

class ActorDictProxy(dict):
    """A proxy dictionary that presents actors from the unified entities dict.
    Provides full dict interface so legacy code treating actors as a separate dict
    will seamlessly read and write to the unified entities collection.
    """
    def __init__(self, entities: Dict[str, Any]):
        self.entities = entities
        super().__init__()

    def _is_actor(self, v: Any) -> bool:
        return hasattr(v, "faction") or getattr(v, "kind", "") == "actor"

    def __getitem__(self, k: str) -> Any:
        v = self.entities[k]
        if self._is_actor(v):
            return v
        raise KeyError(k)

    def __setitem__(self, k: str, v: Any) -> None:
        self.entities[k] = v

    def __delitem__(self, k: str) -> None:
        v = self.entities.get(k)
        if v is not None and self._is_actor(v):
            del self.entities[k]
        else:
            raise KeyError(k)

    def __contains__(self, k: object) -> bool:
        if not isinstance(k, str):
            return False
        v = self.entities.get(k)
        return v is not None and self._is_actor(v)

    def __iter__(self):
        for k, v in self.entities.items():
            if self._is_actor(v):
                yield k

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def keys(self):
        return {k: v for k, v in self.entities.items() if self._is_actor(v)}.keys()

    def values(self):
        return {k: v for k, v in self.entities.items() if self._is_actor(v)}.values()

    def items(self):
        return {k: v for k, v in self.entities.items() if self._is_actor(v)}.items()

    def get(self, k: str, default: Any = None) -> Any:
        v = self.entities.get(k)
        if v is not None and self._is_actor(v):
            return v
        return default

    def pop(self, k: str, default: Any = object()) -> Any:
        v = self.entities.get(k)
        if v is not None and self._is_actor(v):
            return self.entities.pop(k)
        if default is not object():
            return default
        raise KeyError(k)

    def popitem(self) -> Tuple[str, Any]:
        for k in list(self):
            v = self.entities.pop(k)
            return k, v
        raise KeyError('popitem(): dictionary is empty')

    def clear(self) -> None:
        for k in list(self):
            del self.entities[k]

    def update(self, other=(), **kwargs):
        if hasattr(other, "keys"):
            for k in other.keys():
                self[k] = other[k]
        else:
            for k, v in other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, k: str, default: Any = None) -> Any:
        if k not in self:
            self[k] = default
        return self[k]

    def copy(self):
        return {k: v for k, v in self.items()}


@dataclass
class LevelState:
    world: World
    entities: Dict[str, Any]
    events: List[Tuple[int, int, Callable[[], None]]]
    order: int
    current_tick: int
    pattern: builder.Pattern
    pattern_anchor: Optional[Tuple[int, int]]
    activation_points: List[Tuple[float, float]]
    activation_ttl: int
    awaiting_terminus: bool
    need_fov: bool
    up_stairs: Optional[Tuple[int, int]] = None
    down_stairs: Optional[Tuple[int, int]] = None
    hover_vertex: Optional[int] = None  # for renderer hinting
    spotted: Set[str] = field(default_factory=set)  # seen actors
    coord: Tuple[int, int, int] = (0, 0, 0)  # (x, y, depth)
    acidic_pattern: bool = False  # True when Corrosive Melt is active
    # Fern growth state (Barnsley fern auto-growth system)
    fern_active: bool = False  # Is fern growth enabled?
    fern_growth_tips: List[int] = field(default_factory=list)  # Vertex indices that can spawn growth
    fern_accum: float = 0.0  # Fractional tick accumulator for growth timing
    seal_trial: Optional[Any] = None  # Sealing rune trial state (if any)
    # Zone difficulty metadata (computed on zone creation).
    danger_value: float = 0.0
    danger_tier: int = 1
    danger_sources: Dict[str, float] = field(default_factory=dict)
    # Active deferred (telegraphed) actions pending resolution.
    deferred_actions: List[Any] = field(default_factory=list)
    # Accumulator for ambient hostile top-up timing (Option 2 roaming spawns).
    ambient_spawn_accum: float = 0.0

    @property
    def actors(self) -> Dict[str, Any]:
        return ActorDictProxy(self.entities)

    @actors.setter
    def actors(self, value: Dict[str, Any]) -> None:
        if value:
            self.entities.update(value)
