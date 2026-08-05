from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path


class ImageCache:
    """Small thread-safe LRU cache for already-processed image results.

    When ``cache_dir`` is provided (or the ``OMNIMODAL_CACHE_DIR``
    environment variable is set), entries are also persisted to disk as
    JSON files so the cache survives process restarts. Disk failures are
    silent: the in-memory cache keeps working either way.
    """

    def __init__(
        self,
        max_entries: int = 256,
        ttl_sec: int = 300,
        cache_dir: str | None = None,
    ):
        self._max_entries = max(0, max_entries)
        self._ttl_sec = max(0, ttl_sec)
        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        cache_dir_value = cache_dir or os.environ.get("OMNIMODAL_CACHE_DIR", "")
        self._cache_dir = Path(cache_dir_value).expanduser() if cache_dir_value else None

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._items.get(key)
            if item is not None:
                value, created = item
                if self._ttl_sec > 0 and time.monotonic() - created > self._ttl_sec:
                    del self._items[key]
                    return None
                if value is not None:
                    self._items.move_to_end(key)
                return value
            if self._cache_dir is None:
                return None
            stored = self._load_from_disk(key)
            if stored is None:
                return None
            if self._max_entries > 0:
                self._items[key] = (stored, time.monotonic())
                while len(self._items) > self._max_entries:
                    self._items.popitem(last=False)
            return stored

    def put(self, key: str, value: str) -> None:
        if self._max_entries == 0:
            return
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
            self._items[key] = (value, time.monotonic())
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)
            if self._cache_dir is not None:
                self._save_to_disk(key, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            if self._cache_dir is not None:
                for path in self._cache_dir.glob("*.json"):
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def _disk_path(self, key: str) -> Path:
        # 用整个 key 的 sha256 做文件名：key 首位虽然是图片 digest，
        # 但同一张图不同 mode/model/task 会得到不同 key，直接取首段会互相覆盖。
        assert self._cache_dir is not None
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.json"

    def _save_to_disk(self, key: str, value: str) -> None:
        path = self._disk_path(key)
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps({"result": value, "created_at": time.time()}),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        except OSError:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_from_disk(self, key: str) -> str | None:
        path = self._disk_path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        created_at = data.get("created_at")
        result = data.get("result")
        if not isinstance(created_at, (int, float)) or not isinstance(result, str):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        if self._ttl_sec > 0 and time.time() - created_at > self._ttl_sec:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return result


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
