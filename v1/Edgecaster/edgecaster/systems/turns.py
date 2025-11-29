import heapq
from dataclasses import dataclass, field
from typing import Callable, List


@dataclass(order=True)
class ScheduledAction:
    at_tick: int
    order: int
    actor_id: str = field(compare=False)
    action: Callable[[], None] = field(compare=False)


class TurnScheduler:
    def __init__(self) -> None:
        self.queue: List[ScheduledAction] = []
        self._order = 0
        self.current_tick = 0

    def schedule(self, delay: int, actor_id: str, action: Callable[[], None]) -> None:
        self._order += 1
        heapq.heappush(self.queue, ScheduledAction(self.current_tick + delay, self._order, actor_id, action))

    def next(self) -> None:
        if not self.queue:
            return
        item = heapq.heappop(self.queue)
        self.current_tick = item.at_tick
        item.action()
