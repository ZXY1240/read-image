from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict


class ImageCache:
    """Small thread-safe LRU cache for already-processed image results."""

    def __init__(self, max_entries: int = 256):
        self._max_entries = max(0, max_entries)
        self._items: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: str, value: str) -> None:
        if self._max_entries == 0:
            return
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
            self._items[key] = value
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def image_cache_key(
    image_bytes: bytes,
    task: str,
    mode: str,
    model: str,
) -> str:
    digest = hashlib.sha256(image_bytes).hexdigest()
    return f"{digest}:{mode}:{model}:{task}"
