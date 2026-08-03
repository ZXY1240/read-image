from __future__ import annotations

from read_image.cache import ImageCache, image_cache_key


def test_image_cache_key_changes_with_inputs() -> None:
    key_a = image_cache_key(b"a", "standard", "model", "doubao")
    key_b = image_cache_key(b"b", "standard", "model", "doubao")
    key_c = image_cache_key(b"a", "full", "model", "doubao")
    key_d = image_cache_key(b"a", "standard", "other-model", "doubao")
    key_e = image_cache_key(b"a", "standard", "model", "openai_compatible")
    assert len({key_a, key_b, key_c, key_d, key_e}) == 5


def test_image_cache_key_ignores_task_by_default() -> None:
    key_a = image_cache_key(b"a", "standard", "model", "doubao", task="task1")
    key_b = image_cache_key(b"a", "standard", "model", "doubao", task="task2")
    assert key_a == key_b


def test_image_cache_key_can_opt_into_task() -> None:
    key_a = image_cache_key(
        b"a",
        "standard",
        "model",
        "doubao",
        task="task1",
        use_task=True,
    )
    key_b = image_cache_key(
        b"a",
        "standard",
        "model",
        "doubao",
        task="task2",
        use_task=True,
    )
    assert key_a != key_b


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
