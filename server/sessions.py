"""
Per-visitor conversation and cart state, held in memory with a TTL.

In-process storage is right for a single instance. Behind more than one worker this
needs Redis or a shared store — the interface is small enough that swapping it is
contained to this file.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from . import config
from .cart import Cart
from .menu import Menu


@dataclass
class Session:
    session_id: str
    cart: Cart
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    _hits: list[float] = field(default_factory=list)

    def touch(self) -> None:
        self.last_seen = time.time()

    def rate_limited(self) -> bool:
        """Sliding one-minute window per session."""
        now = time.time()
        self._hits = [t for t in self._hits if now - t < 60]
        if len(self._hits) >= config.RATE_LIMIT_PER_MINUTE:
            return True
        self._hits.append(now)
        return False


class SessionStore:
    def __init__(self, menu: Menu) -> None:
        self._menu = menu
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str | None) -> Session:
        self._evict()
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.touch()
            return session

        new_id = secrets.token_urlsafe(16)
        session = Session(session_id=new_id, cart=Cart(self._menu))
        self._sessions[new_id] = session
        return session

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _evict(self) -> None:
        cutoff = time.time() - config.SESSION_TTL_SECONDS
        for sid in [s for s, sess in self._sessions.items() if sess.last_seen < cutoff]:
            del self._sessions[sid]

    def __len__(self) -> int:
        return len(self._sessions)
