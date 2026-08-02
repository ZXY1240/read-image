from __future__ import annotations

import threading
import time

from read_image.api import ConcurrencyGate


def test_gate_lowers_after_rate_limits_and_recovers() -> None:
    gate = ConcurrencyGate(4, recovery_threshold=2)
    gate.note_rate_limit()
    gate.note_rate_limit()
    assert gate.current_limit == 3
    gate.note_success()
    gate.note_success()
    assert gate.current_limit == 4


def test_gate_never_goes_below_one() -> None:
    gate = ConcurrencyGate(2, recovery_threshold=2)
    for _ in range(10):
        gate.note_rate_limit()
    assert gate.current_limit == 1


def test_gate_never_exceeds_initial_limit() -> None:
    gate = ConcurrencyGate(3, recovery_threshold=1)
    for _ in range(10):
        gate.note_success()
    assert gate.current_limit == 3


def test_gate_wakes_waiter_on_release_without_long_polling() -> None:
    gate = ConcurrencyGate(1)
    gate.acquire()
    acquired = threading.Event()

    def waiter() -> None:
        gate.acquire()
        acquired.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)
    gate.release()
    assert acquired.wait(0.2)
