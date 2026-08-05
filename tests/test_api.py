from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hmac
import json
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.security import validate_init_data
from backend.main import create_app


BOT_TOKEN = "123456:TEST_TOKEN_FOR_UNIT_TESTS"


def make_init_data(user_id: int = 42, *, auth_age_seconds: int = 10) -> str:
    user = json.dumps(
        {"id": user_id, "first_name": "Test", "last_name": "User", "username": "tester"},
        separators=(",", ":"),
    )
    auth_date = str(int(datetime.now(UTC).timestamp()) - auth_age_seconds)
    pairs = [("auth_date", auth_date), ("query_id", "AAE"), ("user", user)]
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs, key=lambda x: x[0]))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), sha256).digest()
    dig = hmac.new(secret, check_string.encode(), sha256).hexdigest()
    return urlencode([*pairs, ("hash", dig)])


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def db_engine(settings):
    """Use the running DB schema (Alembic). Do not drop_all — preserves seed rooms."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bookings"))
        await conn.execute(
            text(
                """
                INSERT INTO rooms (name, capacity, photo_url, description)
                SELECT 'Test Room', 4, 'https://example.com/r.jpg', 'Test'
                WHERE NOT EXISTS (SELECT 1 FROM rooms WHERE name = 'Test Room')
                """
            )
        )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bookings"))
    await engine.dispose()


@pytest.fixture
async def client(db_engine, settings):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def room_id(client):
    rooms = (await client.get("/api/rooms")).json()
    test = next((r for r in rooms if r["name"] == "Test Room"), rooms[0])
    return test["id"]


@pytest.mark.asyncio
async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_slots_have_no_pii(client, room_id):
    day = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1, hours=3)
    end = start + timedelta(hours=1)
    init = make_init_data(1001)
    await client.post(
        "/api/bookings",
        headers={"X-Telegram-Init-Data": init},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    slots = (await client.get(f"/api/rooms/{room_id}/slots", params={"date": day})).json()
    for slot in slots["slots"]:
        assert set(slot.keys()) == {"start", "end", "status"}
        assert "user_display_name" not in slot
        assert "telegram_id" not in slot


@pytest.mark.asyncio
async def test_booking_rejects_past(client, room_id):
    start = datetime.now(UTC) - timedelta(hours=2)
    end = start + timedelta(hours=1)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_booking_rejects_bad_room(client):
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(hours=1)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={"room_id": 99999, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_booking_rejects_short_duration(client, room_id):
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(minutes=5)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_cancel_foreign_booking_forbidden(client, room_id):
    start = datetime.now(UTC) + timedelta(days=2, hours=4)
    end = start + timedelta(hours=1)
    create = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "111"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert create.status_code == 201, create.text
    booking_id = create.json()["id"]
    res = await client.post(
        f"/api/bookings/{booking_id}/cancel",
        headers={"X-Debug-Telegram-Id": "222"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_missing_init_data_rejected(client, room_id, monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    start = datetime.now(UTC) + timedelta(days=3)
    end = start + timedelta(hours=1)
    res = await client.post(
        "/api/bookings",
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 401
    monkeypatch.setenv("DEBUG", "true")
    get_settings.cache_clear()


def test_validate_init_data_ok():
    init = make_init_data(7)
    user = validate_init_data(init, BOT_TOKEN, 300)
    assert user.id == 7


def test_validate_init_data_replay():
    init = make_init_data(7, auth_age_seconds=10_000)
    with pytest.raises(Exception):
        validate_init_data(init, BOT_TOKEN, 300)


def test_empty_day_slots_cover_full_day():
    from backend.services.booking import build_day_slots

    day_start = datetime(2026, 8, 20, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    slots = build_day_slots(day_start, day_end, [], now=day_start)
    assert len(slots) == 1
    assert slots[0].status.value == "free"
    assert slots[0].start == day_start
    assert slots[0].end == day_end
    assert slots[0].end > slots[0].start


def test_day_with_one_booking_has_two_free_gaps():
    from backend.services.booking import build_day_slots

    day_start = datetime(2026, 8, 20, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    busy_start = day_start + timedelta(hours=10)
    busy_end = day_start + timedelta(hours=11)
    slots = build_day_slots(
        day_start,
        day_end,
        [(busy_start, busy_end)],
        now=day_start,
    )
    assert [s.status.value for s in slots] == ["free", "busy", "free"]
    assert slots[0].start == day_start and slots[0].end == busy_start
    assert slots[1].start == busy_start and slots[1].end == busy_end
    assert slots[2].start == busy_end and slots[2].end == day_end
    assert all(s.end > s.start for s in slots)


@pytest.mark.asyncio
async def test_slots_api_empty_day_full_range(client, room_id):
    day = "2030-01-15"
    res = await client.get(f"/api/rooms/{room_id}/slots", params={"date": day})
    assert res.status_code == 200
    body = res.json()
    assert len(body["slots"]) == 1
    slot = body["slots"][0]
    assert slot["status"] == "free"
    assert slot["start"].startswith("2030-01-15T00:00:00")
    assert slot["end"].startswith("2030-01-16T00:00:00")
    assert slot["start"] != slot["end"]
