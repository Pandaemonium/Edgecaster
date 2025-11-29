from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class ActionDef:
    name: str
    speed_tag: str  # instant | fast | slow
    cost: int
    perform: Callable[..., None]


action_registry: Dict[str, ActionDef] = {}


def register_action(action: ActionDef) -> None:
    action_registry[action.name] = action
