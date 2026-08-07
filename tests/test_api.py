from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import hmac
import json
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.security import validate_init_data
from backend.main import create_app


BOT_TOKEN = "123456:TEST_TOKEN_FOR_UNIT_TESTS"


def office_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().office_timezone)


def office_instant(day: date, hour: int, minute: int = 0) -> datetime:
    """Wall-clock time in OFFICE_TIMEZONE → UTC instant."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=office_tz()).astimezone(
        UTC
    )


def future_office_day(days: int = 1) -> date:
    return (datetime.now(office_tz()) + timedelta(days=days)).date()


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
        await conn.execute(text("DELETE FROM users"))
        # Ensure the three seeded rooms exist (no Test Room)
        count = (await conn.execute(text("SELECT COUNT(*) FROM rooms"))).scalar_one()
        if count == 0:
            await conn.execute(
                text(
                    """
                    INSERT INTO rooms (name, capacity, photo_url, description) VALUES
                    ('Большая', 10, '/media/rooms/big.jpg', 'Test seed big'),
                    ('Малая', 8, '/media/rooms/small.jpg', 'Test seed small'),
                    ('Коворкинг', 4, '/media/rooms/coworking.jpg', 'Test seed coworking')
                    """
                )
            )
        # Whitelist test users (admin + members used across tests)
        await conn.execute(
            text(
                """
                INSERT INTO users (telegram_id, display_name, role) VALUES
                (42, 'Admin', 'admin'),
                (111, 'Member A', 'member'),
                (222, 'Member B', 'member'),
                (1001, 'Slot Tester', 'member')
                ON CONFLICT (telegram_id) DO NOTHING
                """
            )
        )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bookings"))
        await conn.execute(text("DELETE FROM users"))
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
    assert rooms, "expected seeded rooms"
    return rooms[0]["id"]


@pytest.mark.asyncio
async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


@pytest.mark.asyncio
async def test_slots_have_no_pii(client, room_id):
    day = future_office_day(1)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    init = make_init_data(1001)
    await client.post(
        "/api/bookings",
        headers={"X-Telegram-Init-Data": init},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    slots = (await client.get(f"/api/rooms/{room_id}/slots", params={"date": day.isoformat()})).json()
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
    day = future_office_day(1)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={"room_id": 99999, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_booking_rejects_short_duration(client, room_id):
    day = future_office_day(1)
    start = office_instant(day, 10)
    end = start + timedelta(minutes=5)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_cancel_foreign_booking_forbidden(client, room_id):
    day = future_office_day(2)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
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
async def test_cancel_via_service_sends_notification(db_engine, monkeypatch):
    from backend.core.security import TelegramUser
    from backend.services.booking import BookingService
    from backend.services.notifications import format_booking_message

    sent: list[dict] = []

    class FakeBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            sent.append({"chat_id": chat_id, "text": text})

    monkeypatch.setattr("backend.services.notifications.get_bot", lambda: FakeBot())

    user = TelegramUser(id=42, first_name="Test", last_name="User", username="tester")
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    day = future_office_day(1)
    start = office_instant(day, 10)
    end = office_instant(day, 11)

    async with session_factory() as session:
        from backend.services.booking import RoomService

        rooms = await RoomService(session).list_rooms()
        assert rooms
        created = await BookingService(session).create(rooms[0].id, start, end, user)
        await session.commit()
        booking_id = created.id

    async with session_factory() as session:
        out = await BookingService(session).cancel(booking_id, user)
        await session.commit()

    assert len(sent) == 1
    assert sent[0]["chat_id"] == 42
    assert sent[0]["text"] == format_booking_message(out, title="Бронь отменена")
    assert f"ID брони: {booking_id}" in sent[0]["text"]
    assert "Бронь отменена" in sent[0]["text"]


@pytest.mark.asyncio
async def test_missing_init_data_rejected(client, room_id, monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    day = future_office_day(3)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
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


def test_redact_secrets_masks_database_password_and_webhook():
    from backend.core.logging_safe import redact_secrets

    raw = (
        "connect failed: postgresql+asyncpg://booking:s3cretPass@postgres:5432/booking "
        f"bot={BOT_TOKEN} webhook=super-secret-webhook-token "
        "https://api.telegram.org/bot123456:AAAleak/sendMessage"
    )
    out = redact_secrets(raw, BOT_TOKEN, webhook_secret="super-secret-webhook-token")
    assert "s3cretPass" not in out
    assert "booking:[REDACTED]@postgres" in out
    assert BOT_TOKEN not in out
    assert "super-secret-webhook-token" not in out
    assert "api.telegram.org/bot[REDACTED]" in out


def test_empty_day_slots_cover_window():
    from backend.services.booking import build_day_slots

    day_start = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    day_end = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    slots = build_day_slots(day_start, day_end, [], now=day_start)
    assert len(slots) == 1
    assert slots[0].status.value == "free"
    assert slots[0].start == day_start
    assert slots[0].end == day_end
    assert slots[0].end > slots[0].start


def test_office_hours_free_interval_bounds():
    """Empty day with default office hours 09:00–18:00 MSK → free [06:00, 15:00) UTC."""
    from backend.schemas import SlotStatus
    from backend.services.booking import build_day_slots, office_day_bounds

    day = date(2026, 8, 20)
    day_start, day_end = office_day_bounds(day)
    assert day_start == datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    assert day_end == datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    slots = build_day_slots(day_start, day_end, [], now=day_start - timedelta(days=1))
    free = [s for s in slots if s.status == SlotStatus.free]
    assert len(free) == 1
    assert free[0].start == day_start
    assert free[0].end == day_end


def test_day_with_one_booking_has_two_free_gaps():
    from backend.services.booking import build_day_slots

    day_start = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    day_end = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    busy_start = day_start + timedelta(hours=4)
    busy_end = day_start + timedelta(hours=5)
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


def test_today_free_slots_start_at_ceil_30_of_now():
    from backend.schemas import SlotStatus
    from backend.services.booking import build_day_slots

    day_start = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
    day_end = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)
    now = datetime(2026, 8, 6, 9, 28, tzinfo=UTC)
    slots = build_day_slots(day_start, day_end, [], now=now)
    free = [s for s in slots if s.status == SlotStatus.free]
    assert len(free) == 1
    assert free[0].start == datetime(2026, 8, 6, 9, 30, tzinfo=UTC)
    assert free[0].end == day_end
    assert free[0].start >= now


def test_today_past_end_of_day_has_no_free_slots():
    from backend.schemas import SlotStatus
    from backend.services.booking import build_day_slots

    day_start = datetime(2026, 8, 6, 6, 0, tzinfo=UTC)
    day_end = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)
    now = day_end
    slots = build_day_slots(day_start, day_end, [], now=now)
    assert slots == []
    assert all(s.status != SlotStatus.free for s in slots)


def test_today_after_office_hours_no_free_slots():
    """Today at 19:30 MSK → office window already closed → no free intervals."""
    from backend.schemas import SlotStatus
    from backend.services.booking import build_day_slots, office_day_bounds

    day = date(2026, 8, 6)
    day_start, day_end = office_day_bounds(day)
    now = office_instant(day, 19, 30)
    slots = build_day_slots(day_start, day_end, [], now=now)
    assert all(s.status != SlotStatus.free for s in slots)
    assert slots == []


def test_ceil_to_minutes_aligned_unchanged(settings):
    from backend.services.booking import ceil_to_minutes

    step = settings.slot_step_minutes
    aligned = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)
    assert ceil_to_minutes(aligned, step) == aligned
    assert ceil_to_minutes(datetime(2026, 8, 6, 9, 28, tzinfo=UTC), step) == datetime(
        2026, 8, 6, 9, 30, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_public_booking_config(client, settings):
    res = await client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert body["office_timezone"] == settings.office_timezone
    assert body["max_duration_minutes"] == settings.max_duration_minutes
    assert body["slot_step_minutes"] == settings.slot_step_minutes
    assert body["office_hours_start"] == settings.office_hours_start
    assert body["office_hours_end"] == settings.office_hours_end
    assert body["max_recurring_weeks"] == settings.max_recurring_weeks


@pytest.mark.asyncio
async def test_slots_api_empty_day_office_hours(client, room_id):
    day = "2030-01-15"
    res = await client.get(f"/api/rooms/{room_id}/slots", params={"date": day})
    assert res.status_code == 200
    body = res.json()
    assert len(body["slots"]) == 1
    slot = body["slots"][0]
    assert slot["status"] == "free"
    # 09:00–18:00 MSK = 06:00–15:00 UTC
    assert slot["start"].startswith("2030-01-15T06:00:00")
    assert slot["end"].startswith("2030-01-15T15:00:00")
    assert slot["start"] != slot["end"]


@pytest.mark.asyncio
async def test_booking_rejects_outside_office_hours(client, room_id):
    day = future_office_day(1)
    start = office_instant(day, 20)
    end = office_instant(day, 21)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Бронирование доступно с 09:00 до 18:00"
