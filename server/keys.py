"""
API key rotation.

Groq's free tier caps tokens per day per organization. When one key's daily budget is
spent, a key from a different account is a fresh budget — so rather than failing the
customer, the assistant moves to the next key and retries immediately.

Only the DAILY cap retires a key. The short per-minute throttle is a different condition
entirely: it clears in under a second and is handled by retrying on the same key, since
rotating for it would burn through the pool in a few seconds of normal traffic.

A retired key is reinstated after RETIRE_SECONDS, because Groq's daily window rolls
rather than resetting at midnight — a key exhausted now is usable again before long.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Groq reports its daily window as a rolling reset, typically well under an hour. Retrying
# a retired key after this long costs one wasted request at worst.
RETIRE_SECONDS = 3600.0


@dataclass
class _Key:
    value: str
    spent_until: float = 0.0

    @property
    def available(self) -> bool:
        return time.time() >= self.spent_until

    @property
    def masked(self) -> str:
        """Never log a key in full."""
        return f"{self.value[:12]}…" if len(self.value) > 12 else "…"


@dataclass
class KeyPool:
    """Round-robin over usable keys, skipping any whose daily budget is spent."""

    keys: list[_Key] = field(default_factory=list)
    _cursor: int = 0

    @classmethod
    def from_values(cls, values: list[str]) -> "KeyPool":
        return cls(keys=[_Key(v) for v in values if v])

    def __len__(self) -> int:
        return len(self.keys)

    @property
    def available_count(self) -> int:
        return sum(1 for k in self.keys if k.available)

    def current(self) -> _Key | None:
        """The key to use now, advancing past any that are spent."""
        if not self.keys:
            return None
        for offset in range(len(self.keys)):
            key = self.keys[(self._cursor + offset) % len(self.keys)]
            if key.available:
                self._cursor = (self._cursor + offset) % len(self.keys)
                return key
        return None

    def retire(self, key: _Key) -> bool:
        """
        Mark a key's daily budget spent and move on.

        Returns True if another key is available to retry with.
        """
        key.spent_until = time.time() + RETIRE_SECONDS
        self._cursor = (self._cursor + 1) % len(self.keys)
        remaining = self.available_count
        log.warning(
            "key %s hit its daily cap; %d of %d keys still available",
            key.masked, remaining, len(self.keys),
        )
        return remaining > 0

    def status(self) -> dict[str, object]:
        """Shape for /api/health — masked, never the raw values."""
        now = time.time()
        return {
            "total": len(self.keys),
            "available": self.available_count,
            "keys": [
                {
                    "key": k.masked,
                    "available": k.available,
                    "resumes_in_seconds": max(0, round(k.spent_until - now)) if not k.available else 0,
                }
                for k in self.keys
            ],
        }
