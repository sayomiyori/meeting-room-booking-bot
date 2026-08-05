"""
Concurrent booking race: N parallel POSTs for the same slot.
Exactly one must succeed (201); the rest must be 409 (EXCLUDE constraint).

Usage (from repo root, with app+postgres up or DATABASE_URL reachable):

  DEBUG=true python scripts/race_condition_test.py

Requires DEBUG=true so X-Debug-Telegram-Id auth works.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
N = int(os.getenv("RACE_N", "20"))


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        health = await client.get("/health")
        health.raise_for_status()

        rooms = (await client.get("/api/rooms")).json()
        if not rooms:
            raise SystemExit("No rooms seeded")
        room_id = rooms[0]["id"]

        start = (datetime.now(UTC) + timedelta(days=5)).replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
        payload = {
            "room_id": room_id,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        }

        async def attempt(i: int) -> int:
            res = await client.post(
                "/api/bookings",
                headers={"X-Debug-Telegram-Id": str(10_000 + i)},
                json=payload,
            )
            return res.status_code

        codes = await asyncio.gather(*[attempt(i) for i in range(N)])
        ok = sum(1 for c in codes if c == 201)
        conflict = sum(1 for c in codes if c == 409)
        other = [c for c in codes if c not in (201, 409)]

        print(f"N={N} success={ok} conflict={conflict} other={other}")
        if ok != 1:
            raise SystemExit(f"FAIL: expected exactly 1 success, got {ok}")
        if conflict != N - 1:
            raise SystemExit(f"FAIL: expected {N - 1} conflicts, got {conflict}")
        print("PASS: EXCLUDE constraint enforced under concurrency")


if __name__ == "__main__":
    asyncio.run(main())
