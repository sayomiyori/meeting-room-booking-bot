"""Shared per-telegram_id rate limits for booking actions (API + /book)."""

from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

# Same policy as historical POST /api/bookings @limiter.limit("5/minute")
BOOKING_RATE_LIMIT = 5
BOOKING_RATE_WINDOW_SECONDS = 60

_hits: dict[str, deque[float]] = defaultdict(deque)


def allow_telegram_booking_rate(
    telegram_id: int,
    *,
    limit: int = BOOKING_RATE_LIMIT,
    window_seconds: int = BOOKING_RATE_WINDOW_SECONDS,
) -> bool:
    """Return True if the action is allowed; False if the user is over the limit.

    Sliding window of *limit* hits per *window_seconds*, keyed by telegram_id.
    Shared by POST /api/bookings and bot /book so LLM calls cannot bypass the cap.
    """
    key = f"tg:{telegram_id}"
    now = monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] >= window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def reset_booking_rate_limits() -> None:
    """Clear in-memory buckets (tests only)."""
    _hits.clear()
