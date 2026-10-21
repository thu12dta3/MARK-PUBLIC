

from dataclasses import dataclass, field
import logging
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping

from .context import CoreContext

log = logging.getLogger("mark.core.events")
_SAFE_METADATA = {
    "origin", "provider", "model", "status", "latency_ms",
    "prompt_tokens", "output_tokens", "error_type",
}


@dataclass(frozen=True, slots=True)
class CoreEvent:
    name: str
    context: CoreContext
    occurred_at: float = field(default_factory=time.time)
    metadata: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.startswith("mark.") or len(self.name) > 80:
            raise ValueError("Nome de evento inválido")
        if not isinstance(self.context, CoreContext):
            raise TypeError("context precisa ser CoreContext")
        if not isinstance(self.metadata, Mapping) or set(self.metadata) - _SAFE_METADATA:
            raise ValueError("Metadados do evento contêm campos não permitidos")
        safe = dict(self.metadata)
        if any(not isinstance(value, (str, int, float, bool, type(None))) for value in safe.values()):
            raise ValueError("Metadados precisam ser valores escalares")
        if any(isinstance(value, str) and len(value) > 160 for value in safe.values()):
            raise ValueError("Metadado excede o limite")
        object.__setattr__(self, "metadata", MappingProxyType(safe))


class EventBus:


    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_id = 0
        self._subscribers: dict[int, tuple[str | None, Callable[[CoreEvent], None]]] = {}

    def subscribe(self, handler: Callable[[CoreEvent], None], name: str | None = None) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError("handler precisa ser chamável")
        if name is not None and (not isinstance(name, str) or not name.startswith("mark.")):
            raise ValueError("Filtro de evento inválido")
        with self._lock:
            self._next_id += 1
            subscriber_id = self._next_id
            self._subscribers[subscriber_id] = (name, handler)

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(subscriber_id, None)

        return unsubscribe

    def publish(self, event: CoreEvent) -> None:
        if not isinstance(event, CoreEvent):
            raise TypeError("event precisa ser CoreEvent")
        with self._lock:
            subscribers = tuple(self._subscribers.values())
        for name, handler in subscribers:
            if name is not None and name != event.name:
                continue
            try:
                handler(event)
            except Exception as exc:
                log.debug("Ouvinte de evento falhou (%s).", type(exc).__name__)


events = EventBus()
