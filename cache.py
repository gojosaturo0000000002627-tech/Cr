"""Small thread-safe in-memory TTL cache.

Render free-tier services can sleep and restart at any time, so this cache is
intentionally simple and disposable.  It only reduces repeated Crunchyroll API
calls while the Python process is alive; it is not a database and it never
persists stale data across restarts.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Generic, Hashable, Tuple, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """A minimal TTL cache with no external dependencies."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = max(0, int(ttl_seconds))
        self._items: Dict[Hashable, Tuple[float, T]] = {}
        self._lock = threading.RLock()

    def get(self, key: Hashable) -> T:
        """Return a cached value or raise KeyError if missing/expired."""

        if self.ttl_seconds <= 0:
            raise KeyError(key)

        now = time.time()
        with self._lock:
            expires_at, value = self._items[key]
            if expires_at <= now:
                del self._items[key]
                raise KeyError(key)
            return value

    def set(self, key: Hashable, value: T) -> None:
        """Store a value until the cache TTL expires."""

        if self.ttl_seconds <= 0:
            return

        with self._lock:
            self._items[key] = (time.time() + self.ttl_seconds, value)

    def get_or_set(self, key: Hashable, producer: Callable[[], T]) -> T:
        """Return a cached value, otherwise compute and cache it.

        Errors from producer are deliberately not cached.  A temporary upstream
        problem should be visible to the caller and should not poison later
        requests after Crunchyroll recovers.
        """

        try:
            return self.get(key)
        except KeyError:
            value = producer()
            self.set(key, value)
            return value

    def clear(self) -> None:
        """Remove all cached items."""

        with self._lock:
            self._items.clear()
