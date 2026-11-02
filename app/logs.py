from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

Level = Literal["OK", "INF", "WRN", "ERR"]


class ActionLog:
    def __init__(self, maxlen: int = 400) -> None:
        self._items: deque[dict] = deque(maxlen=maxlen)
        self._lock = Lock()

    def add(self, level: Level, message: str) -> dict:
        entry = {
            "time": datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        with self._lock:
            self._items.append(entry)
        return entry

    def list(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


log = ActionLog()
