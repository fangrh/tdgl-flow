import asyncio
from dataclasses import dataclass, field


@dataclass
class FrameAvailableEvent:
    run_id: str
    frame_index: int
    time_value: float
    je: float
    voltage: float
    frame_count: int


@dataclass
class RunCompletedEvent:
    run_id: str
    status: str


MAX_QUEUE_SIZE = 100


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        try:
            subscribers.remove(queue)
        except ValueError:
            pass
        if not subscribers:
            del self._subscribers[run_id]

    def publish(self, run_id: str, event: object) -> None:
        subscribers = self._subscribers.get(run_id, [])
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)


bus = EventBus()