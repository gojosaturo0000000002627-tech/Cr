"""Application configuration helpers.

All configuration is read from environment variables so the same code runs
locally, in tests, and on Render.com.  The response cache is deliberately
capped at 45 minutes because the service promises live Crunchyroll data and
must never keep API responses longer than that.
"""

from __future__ import annotations

import os

DEFAULT_LOCALE = "en-US"
DEFAULT_CACHE_TTL_SECONDS = 45 * 60
MAX_CACHE_TTL_SECONDS = 45 * 60


def get_locale() -> str:
    """Return the Crunchyroll locale used for metadata responses."""

    return os.getenv("CR_LOCALE", DEFAULT_LOCALE).strip() or DEFAULT_LOCALE


def get_cache_ttl_seconds() -> int:
    """Return the response cache TTL, capped at 45 minutes.

    CACHE_TTL_SECONDS is configurable, but values above 2700 seconds are
    reduced to 2700 so cached Crunchyroll data never lives longer than the
    requirement allows.  Set CACHE_TTL_SECONDS=0 to disable response caching.
    """

    raw_value = os.getenv("CACHE_TTL_SECONDS", str(DEFAULT_CACHE_TTL_SECONDS))
    try:
        requested = int(raw_value)
    except (TypeError, ValueError):
        requested = DEFAULT_CACHE_TTL_SECONDS

    if requested < 0:
        requested = 0
    return min(requested, MAX_CACHE_TTL_SECONDS)


def get_port(default: int = 8000) -> int:
    """Return the port used when running the Flask development server."""

    try:
        return int(os.getenv("PORT", str(default)))
    except ValueError:
        return default
