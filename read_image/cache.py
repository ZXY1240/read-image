from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict


class ImageCache:
    """Small thread-safe LRU cache for already-processed image results."""

    def __init__(self, max_entries: int = 256, ttl_sec: int = 300):
        self._max_entries = max(0, max_entries)
        self._ttl_sec = max(0, ttl_sec)
        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            value, created = item
            if self._ttl_sec > 0 and time.monotonic() - created > self._ttl_sec:
                del self._items[key]
                return None
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: str, value: str) -> None:
        if self._max_entries == 0:
            return
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
            self._items[key] = (value, time.monotonic())
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
    mode: str,
    model: str,
    provider: str,
    task: str = "",
    use_task: bool = True,
) -> str:
    digest = hashlib.sha256(image_bytes).hexdigest()
    key = f"{digest}:{mode}:{model}:{provider}"
    if use_task:
        key = f"{key}:{task}"
    return key
