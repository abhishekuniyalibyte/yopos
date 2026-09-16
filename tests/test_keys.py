"""
Key rotation tests.

The distinction that matters: a spent DAILY budget retires a key and moves to the next,
while the short per-minute throttle must NOT, or a few seconds of normal traffic would
burn the whole pool.
"""

from __future__ import annotations

import time

from server.keys import KeyPool, _Key


def pool(n: int = 3) -> KeyPool:
    return KeyPool.from_values([f"gsk_key{i}" for i in range(n)])


def test_empty_pool_has_no_current_key() -> None:
    assert KeyPool.from_values([]).current() is None
    assert len(KeyPool.from_values([])) == 0


def test_blank_values_are_dropped() -> None:
    assert len(KeyPool.from_values(["gsk_a", "", "gsk_b"])) == 2


def test_current_is_stable_until_something_retires_it() -> None:
    p = pool()
    assert p.current().value == p.current().value == "gsk_key0"


def test_retiring_advances_to_the_next_key() -> None:
    p = pool()
    first = p.current()
    assert p.retire(first) is True
    assert p.current().value == "gsk_key1"


def test_retiring_every_key_leaves_nothing_available() -> None:
    p = pool(2)
    assert p.retire(p.current()) is True
    assert p.retire(p.current()) is False
    assert p.current() is None
    assert p.available_count == 0


def test_a_retired_key_is_skipped_but_not_forgotten() -> None:
    p = pool()
    p.retire(p.current())
    assert len(p) == 3
    assert p.available_count == 2


def test_a_retired_key_comes_back_after_its_window() -> None:
    """Groq's daily window rolls, so a spent key is usable again before long."""
    p = pool(1)
    key = p.current()
    p.retire(key)
    assert p.current() is None

    key.spent_until = time.time() - 1  # simulate the window passing
    assert p.current() is key


def test_status_never_exposes_a_whole_key() -> None:
    p = pool(1)
    status = p.status()
    assert status["total"] == 1
    assert status["keys"][0]["key"].endswith("…")
    assert "gsk_key0" not in str(status)


def test_status_reports_when_a_retired_key_returns() -> None:
    p = pool(1)
    p.retire(p.current())
    entry = p.status()["keys"][0]
    assert entry["available"] is False
    assert entry["resumes_in_seconds"] > 0


def test_short_keys_are_masked_safely() -> None:
    assert _Key("abc").masked == "…"
