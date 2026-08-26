from dataclasses import dataclass
from typing import Callable, Any

@dataclass
class AgentEvent:
    kind: str
    message: str
    data: dict

class EventBus:
    def __init__(self, callback: Callable[[AgentEvent], Any] | None = None):
        self.callback = callback
    def emit(self, kind: str, message: str, **data):
        if self.callback:
            self.callback(AgentEvent(kind, message, data))
