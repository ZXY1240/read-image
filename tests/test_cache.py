from __future__ import annotations

from read_image.cache import ImageCache, image_cache_key


def test_image_cache_key_changes_with_inputs() -> None:
    key_a = image_cache_key(b"a", "task", "standard", "model")
    key_b = image_cache_key(b"b", "task", "standard", "model")
    key_c = image_cache_key(b"a", "other", "standard", "model")
    key_d = image_cache_key(b"a", "task", "full", "model")
    key_e = image_cache_key(b"a", "task", "standard", "other-model")
    assert len({key_a, key_b, key_c, key_d, key_e}) == 5


def test_cache_evicts_lru() -> None:
    cache = ImageCache(max_entries=2)
    cache.put("1", "a")
    cache.put("2", "b")
    cache.get("1")
    cache.put("3", "c")
    assert cache.get("2") is None
    assert cache.get("1") == "a"
    assert cache.get("3") == "c"


def test_cache_can_be_disabled() -> None:
    cache = ImageCache(max_entries=0)
    cache.put("1", "a")
    assert cache.get("1") is None
