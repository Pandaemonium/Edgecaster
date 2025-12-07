from dataclasses import dataclass
from typing import Callable, Dict, Any


@dataclass
class Effect:
    name: str
    apply: Callable[[Dict[str, Any]], None]


effects: Dict[str, Effect] = {}


def register_effect(effect: Effect) -> None:
    effects[effect.name] = effect
