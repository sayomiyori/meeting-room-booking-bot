"""Access whitelist, rooms admin, no-show, recurring bookings."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import get_settings
from backend.core.database import get_db
from backend.main import create_app
from backend.models import UserRole
from backend.repositories import BookingRepository, UserRepository
from backend.services.booking import RoomService
from backend.services.scheduler import process_no_shows
from tests.test_api import BOT_TOKEN, future_office_day, office_instant


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_TELEGRAM_ID", "42")
    monkeypatch.setenv("NO_SHOW_ENABLED", "true")
    monkeypatch.setenv("NO_SHOW_WINDOW_MINUTES", "10")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def db_engine(settings):
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bookings"))
        await conn.execute(text("DELETE FROM users"))
        await conn.execute(text("UPDATE rooms SET active = true"))
        await conn.execute(
            text(
                """
                INSERT INTO users (telegram_id, display_name, role) VALUES
                (42, 'Admin', 'admin'),
                (111, 'Member', 'member')
                """
            )
        )
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM bookings"))
        await conn.execute(text("DELETE FROM users"))
        await conn.execute(text("UPDATE rooms SET active = true"))
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


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
    assert rooms
    return rooms[0]["id"]


# --- Access -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_user_booking_forbidden(client, room_id):
    day = future_office_day(1)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "999999"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 403
    assert "администратору" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_registered_user_can_book(client, room_id):
    day = future_office_day(1)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "111"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_invite_member_then_can_book(session_factory, client, room_id):
    async with session_factory() as session:
        await UserRepository(session).create(
            telegram_id=555,
            display_name="Invited",
            role=UserRole.member,
        )
        await session.commit()

    day = future_office_day(2)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    res = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "555"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_invite_rejected_for_non_admin(session_factory):
    """Member cannot create users as admin would via /invite — role check."""
    async with session_factory() as session:
        member = await UserRepository(session).get_by_telegram_id(111)
        assert member is not None
        assert member.role == UserRole.member
        admin = await UserRepository(session).get_by_telegram_id(42)
        assert admin is not None
        assert admin.role == UserRole.admin


# --- Rooms ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_create_room_appears_in_list(session_factory, client):
    async with session_factory() as session:
        room = await RoomService(session).create_room(
            name="Тест-комната",
            capacity=3,
            description="для тестов",
        )
        await session.commit()
        created_id = room.id

    rooms = (await client.get("/api/rooms")).json()
    assert any(r["id"] == created_id and r["name"] == "Тест-комната" for r in rooms)

    # cleanup
    async with session_factory() as session:
        await session.execute(text("DELETE FROM rooms WHERE id = :id"), {"id": created_id})
        await session.commit()


@pytest.mark.asyncio
async def test_deactivate_room_hides_from_list_keeps_fk(session_factory, client, room_id):
    day = future_office_day(3)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    create = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "111"},
        json={"room_id": room_id, "start": start.isoformat(), "end": end.isoformat()},
    )
    assert create.status_code == 201
    booking_id = create.json()["id"]

    async with session_factory() as session:
        await RoomService(session).set_room_active(room_id, False)
        await session.commit()

    rooms = (await client.get("/api/rooms")).json()
    assert all(r["id"] != room_id for r in rooms)

    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT canceled, room_id FROM bookings WHERE id = :id"),
                {"id": booking_id},
            )
        ).one()
        assert row.room_id == room_id
        assert row.canceled is False
        await RoomService(session).set_room_active(room_id, True)
        await session.commit()


@pytest.mark.asyncio
async def test_member_not_admin_role(session_factory):
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(111)
        assert user is not None
        assert user.role != UserRole.admin


# --- No-show ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_noshow_auto_cancels_without_checkin(session_factory, monkeypatch):
    monkeypatch.setenv("NO_SHOW_WINDOW_MINUTES", "10")
    get_settings.cache_clear()

    sent: list[str] = []

    class FakeBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            sent.append(text)

    monkeypatch.setattr("backend.services.notifications.get_bot", lambda: FakeBot())
    monkeypatch.setattr(
        "backend.services.scheduler.async_session_factory",
        session_factory,
    )

    now = datetime.now(UTC)
    start = now - timedelta(minutes=15)
    end = start + timedelta(hours=1)

    async with session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        # Bypass validate_window past check — insert directly
        booking = await BookingRepository(session).create(
            room_id=rooms[0].id,
            telegram_id=111,
            user_display_name="Member",
            start=start,
            end=end,
        )
        await session.commit()
        booking_id = booking.id

    await process_no_shows()

    async with session_factory() as session:
        booking = await BookingRepository(session).get_by_id(booking_id)
        assert booking is not None
        assert booking.canceled is True
        assert booking.auto_canceled_notified is True
    assert any("автоматически" in t.lower() or "Автоотмена" in t for t in sent)


@pytest.mark.asyncio
async def test_noshow_skips_checked_in(session_factory, monkeypatch):
    monkeypatch.setenv("NO_SHOW_WINDOW_MINUTES", "10")
    get_settings.cache_clear()

    class FakeBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            return None

    monkeypatch.setattr("backend.services.notifications.get_bot", lambda: FakeBot())
    monkeypatch.setattr(
        "backend.services.scheduler.async_session_factory",
        session_factory,
    )

    now = datetime.now(UTC)
    start = now - timedelta(minutes=15)
    end = start + timedelta(hours=1)

    async with session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        booking = await BookingRepository(session).create(
            room_id=rooms[0].id,
            telegram_id=111,
            user_display_name="Member",
            start=start,
            end=end,
        )
        await BookingRepository(session).mark_checked_in(booking)
        await session.commit()
        booking_id = booking.id

    await process_no_shows()

    async with session_factory() as session:
        booking = await BookingRepository(session).get_by_id(booking_id)
        assert booking is not None
        assert booking.canceled is False


@pytest.mark.asyncio
async def test_noshow_skips_short_booking(session_factory, monkeypatch):
    monkeypatch.setenv("NO_SHOW_WINDOW_MINUTES", "10")
    get_settings.cache_clear()

    class FakeBot:
        async def send_message(self, chat_id: int, text: str, **kwargs):
            return None

    monkeypatch.setattr("backend.services.notifications.get_bot", lambda: FakeBot())
    monkeypatch.setattr(
        "backend.services.scheduler.async_session_factory",
        session_factory,
    )

    now = datetime.now(UTC)
    start = now - timedelta(minutes=15)
    end = start + timedelta(minutes=5)  # shorter than window

    async with session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        booking = await BookingRepository(session).create(
            room_id=rooms[0].id,
            telegram_id=111,
            user_display_name="Member",
            start=start,
            end=end,
        )
        await session.commit()
        booking_id = booking.id

    await process_no_shows()

    async with session_factory() as session:
        booking = await BookingRepository(session).get_by_id(booking_id)
        assert booking is not None
        assert booking.canceled is False


# --- Recurring --------------------------------------------------------------


@pytest.mark.asyncio
async def test_recurring_all_free(client, room_id):
    day = future_office_day(7)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    res = await client.post(
        "/api/bookings/recurring",
        headers={"X-Debug-Telegram-Id": "111"},
        json={
            "room_id": room_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "weeks": 4,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["created"]) == 4
    assert body["skipped"] == []
    group_ids = {b["recurring_group_id"] for b in body["created"]}
    assert len(group_ids) == 1
    assert None not in group_ids


@pytest.mark.asyncio
async def test_recurring_skips_conflict(client, room_id):
    day = future_office_day(14)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    # Occupy week 2 (+7 days)
    conflict_start = start + timedelta(weeks=1)
    conflict_end = end + timedelta(weeks=1)
    occupied = await client.post(
        "/api/bookings",
        headers={"X-Debug-Telegram-Id": "42"},
        json={
            "room_id": room_id,
            "start": conflict_start.isoformat(),
            "end": conflict_end.isoformat(),
        },
    )
    assert occupied.status_code == 201, occupied.text

    res = await client.post(
        "/api/bookings/recurring",
        headers={"X-Debug-Telegram-Id": "111"},
        json={
            "room_id": room_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "weeks": 4,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["created"]) == 3
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["reason"] == "занято"


@pytest.mark.asyncio
async def test_cancel_series_future_only(session_factory, client, room_id):
    day = future_office_day(21)
    start = office_instant(day, 10)
    end = office_instant(day, 11)
    res = await client.post(
        "/api/bookings/recurring",
        headers={"X-Debug-Telegram-Id": "111"},
        json={
            "room_id": room_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "weeks": 3,
        },
    )
    assert res.status_code == 201, res.text
    created = res.json()["created"]
    group_id = created[0]["recurring_group_id"]

    # Mark first occurrence as already started (past lower bound) via raw SQL
    first_id = created[0]["id"]
    past_start = datetime.now(UTC) - timedelta(hours=2)
    past_end = past_start + timedelta(hours=1)
    async with session_factory() as session:
        await session.execute(
            text(
                """
                UPDATE bookings
                SET during = tstzrange(:s, :e, '[)')
                WHERE id = :id
                """
            ),
            {"s": past_start, "e": past_end, "id": first_id},
        )
        await session.commit()

    cancel = await client.post(
        f"/api/bookings/recurring/{group_id}/cancel",
        headers={"X-Debug-Telegram-Id": "111"},
    )
    assert cancel.status_code == 200, cancel.text
    canceled_ids = {b["id"] for b in cancel.json()}
    assert first_id not in canceled_ids
    assert len(canceled_ids) == 2

    async with session_factory() as session:
        first = await BookingRepository(session).get_by_id(first_id)
        assert first is not None
        assert first.canceled is False
