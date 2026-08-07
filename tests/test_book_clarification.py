"""Tests for /book clarification dialogue (parsers + FSM flow)."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from backend.core.config import get_settings
from backend.core.rate_limit import allow_telegram_booking_rate, reset_booking_rate_limits
from backend.services.book_clarification import (
    apply_duration_default,
    clarification_question,
    first_missing_field,
    match_room_name,
    parse_clarification_date,
    parse_clarification_time,
    resolve_room_canonical,
)
from backend.services.nl_booking import ParsedIntent
from bot.book_clarify import BookingClarification, on_clarification_answer, start_clarification
from bot.book_common import FALLBACK_PARSE
from bot.handlers import cmd_book


@pytest.fixture(autouse=True)
def _reset():
    reset_booking_rate_limits()
    get_settings.cache_clear()
    yield
    reset_booking_rate_limits()
    get_settings.cache_clear()


def _rooms():
    return [
        SimpleNamespace(id=1, name="Большая"),
        SimpleNamespace(id=2, name="Малая"),
        SimpleNamespace(id=3, name="Коворкинг"),
    ]


def test_match_room_partial_cases():
    names = ["Большая", "Малая", "Коворкинг"]
    assert match_room_name("малую", names) == "Малая"
    assert match_room_name("Малая", names) == "Малая"
    assert match_room_name("больш", names) == "Большая"
    assert match_room_name("xyz", names) is None


def test_parse_date_relative():
    today = date(2026, 8, 7)
    assert parse_clarification_date("сегодня", today=today) == "2026-08-07"
    assert parse_clarification_date("завтра", today=today) == "2026-08-08"
    assert parse_clarification_date("послезавтра", today=today) == "2026-08-09"
    assert parse_clarification_date("15 августа", today=today) == "2026-08-15"
    assert parse_clarification_date("15.08", today=today) == "2026-08-15"


def test_parse_time_formats():
    assert parse_clarification_time("15:00") == "15:00"
    assert parse_clarification_time("9:30") == "09:30"
    assert parse_clarification_time("в 15") == "15:00"
    assert parse_clarification_time("15") == "15:00"
    assert parse_clarification_time("нет") is None


def test_first_missing_priority():
    intent = ParsedIntent(room="Малая", date=None, start_time=None, duration_minutes=60)
    assert first_missing_field(intent) == "date"
    intent2 = ParsedIntent(room=None, date="2026-08-08", start_time="15:00", duration_minutes=60)
    assert first_missing_field(intent2) == "room"


def test_clarification_question_room_lists_names():
    q = clarification_question("room", ["Большая", "Малая"])
    assert "Большая" in q and "Малая" in q


@pytest.mark.asyncio
async def test_book_partial_room_asks_date(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()

    partial = ParsedIntent(room="Малая", date=None, start_time=None, duration_minutes=None)
    monkeypatch.setattr("bot.handlers.parse_booking_intent", AsyncMock(return_value=partial))
    monkeypatch.setattr(
        "bot.handlers._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    rooms = _rooms()

    class FakeRoomService:
        def __init__(self, _s):
            pass

        async def list_rooms(self):
            return rooms

    monkeypatch.setattr("bot.handlers.RoomService", FakeRoomService)
    monkeypatch.setattr(
        "bot.handlers.async_session_factory",
        lambda: _FakeSession(),
    )

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)
    state = FSMContext(storage=storage, key=key)

    answers: list[str] = []
    message = MagicMock()
    message.from_user = MagicMock(id=42)
    message.text = "/book малую"
    message.answer = AsyncMock(side_effect=lambda text, **_k: answers.append(text))

    await cmd_book(message, state)

    assert await state.get_state() == BookingClarification.awaiting_clarification.state
    assert len(answers) == 1
    assert "дату" in answers[0].casefold()
    data = await state.get_data()
    assert data["missing_field"] == "date"
    assert data["partial_intent"]["room"] == "Малая"
    assert data["partial_intent"]["duration_minutes"] == 60


@pytest.mark.asyncio
async def test_clarification_tomorrow_then_time_builds_webapp(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("WEBAPP_URL", "https://example.com/app")
    monkeypatch.setenv("OFFICE_TIMEZONE", "Europe/Moscow")
    get_settings.cache_clear()

    monkeypatch.setattr(
        "bot.book_clarify._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    rooms = _rooms()

    class FakeRoomService:
        def __init__(self, _s):
            pass

        async def list_rooms(self):
            return rooms

    class FakeBookingService:
        def __init__(self, _s):
            pass

        def validate_window(self, start, end):
            return start, end

    monkeypatch.setattr("bot.book_clarify.RoomService", FakeRoomService)
    monkeypatch.setattr("bot.book_clarify.BookingService", FakeBookingService)
    monkeypatch.setattr("bot.book_clarify.async_session_factory", lambda: _FakeSession())

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=42, user_id=42)
    state = FSMContext(storage=storage, key=key)

    message = MagicMock()
    message.from_user = MagicMock(id=42)
    answers: list[dict] = []

    async def capture(text, reply_markup=None, **_k):
        answers.append({"text": text, "markup": reply_markup})

    message.answer = AsyncMock(side_effect=capture)

    intent = apply_duration_default(
        resolve_room_canonical(
            ParsedIntent(room="Малая", date=None, start_time=None, duration_minutes=None),
            [r.name for r in rooms],
        )
    )
    await start_clarification(message, state, intent, [r.name for r in rooms])
    assert "дату" in answers[-1]["text"].casefold()

    message.text = "завтра"
    await on_clarification_answer(message, state)
    assert "во сколько" in answers[-1]["text"].casefold()

    message.text = "15:00"
    await on_clarification_answer(message, state)

    assert await state.get_state() is None
    final = answers[-1]
    assert "Понял: Малая" in final["text"]
    assert final["markup"] is not None
    url = final["markup"].inline_keyboard[0][0].web_app.url
    assert "room=" in url
    assert "start=15" in url or "start=15%3A00" in url or "start=15:00" in url
    assert "duration=60" in url


@pytest.mark.asyncio
async def test_clarification_two_bad_answers_fallback(monkeypatch):
    monkeypatch.setattr(
        "bot.book_clarify._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    class FakeRoomService:
        def __init__(self, _s):
            pass

        async def list_rooms(self):
            return _rooms()

    monkeypatch.setattr("bot.book_clarify.RoomService", FakeRoomService)
    monkeypatch.setattr("bot.book_clarify.async_session_factory", lambda: _FakeSession())

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=7, user_id=7)
    state = FSMContext(storage=storage, key=key)

    answers: list[str] = []
    message = MagicMock()
    message.from_user = MagicMock(id=7)
    message.answer = AsyncMock(side_effect=lambda text, **_k: answers.append(text))

    await start_clarification(
        message,
        state,
        ParsedIntent(),
        [r.name for r in _rooms()],
    )
    assert "комнат" in answers[-1].casefold()

    message.text = "абракадабра1"
    await on_clarification_answer(message, state)
    assert "комнат" in answers[-1].casefold()

    message.text = "абракадабра2"
    await on_clarification_answer(message, state)
    assert answers[-1] == FALLBACK_PARSE
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_book_dialogue_uses_one_rate_slot(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()
    reset_booking_rate_limits()

    tg_id = 4242
    partial = ParsedIntent(room="Малая", date=None, start_time=None, duration_minutes=60)
    monkeypatch.setattr("bot.handlers.parse_booking_intent", AsyncMock(return_value=partial))
    monkeypatch.setattr(
        "bot.handlers._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )
    monkeypatch.setattr(
        "bot.book_clarify._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    class FakeRoomService:
        def __init__(self, _s):
            pass

        async def list_rooms(self):
            return _rooms()

    class FakeBookingService:
        def __init__(self, _s):
            pass

        def validate_window(self, start, end):
            return start, end

    monkeypatch.setattr("bot.handlers.RoomService", FakeRoomService)
    monkeypatch.setattr("bot.handlers.async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("bot.book_clarify.RoomService", FakeRoomService)
    monkeypatch.setattr("bot.book_clarify.BookingService", FakeBookingService)
    monkeypatch.setattr("bot.book_clarify.async_session_factory", lambda: _FakeSession())

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=tg_id, user_id=tg_id)
    state = FSMContext(storage=storage, key=key)

    message = MagicMock()
    message.from_user = MagicMock(id=tg_id)
    message.text = "/book малую"
    message.answer = AsyncMock()

    await cmd_book(message, state)
    # Clarification replies must not consume extra rate slots
    message.text = "завтра"
    await on_clarification_answer(message, state)
    message.text = "15:00"
    await on_clarification_answer(message, state)

    # 4 more /book starts should still be allowed (5/minute total, 1 used)
    for i in range(4):
        assert allow_telegram_booking_rate(tg_id), f"slot {i + 2} should be free"
    assert allow_telegram_booking_rate(tg_id) is False


@pytest.mark.asyncio
async def test_partial_intent_returned_from_llm(monkeypatch):
    """Regression: incomplete LLM JSON is returned for clarification, not None."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    get_settings.cache_clear()

    from backend.services.nl_booking import parse_booking_intent

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"room":"Малая","date":null,"start_time":null,"duration_minutes":null}'
                        )
                    )
                ]
            )

    class FakeClient:
        chat = SimpleNamespace(completions=FakeCompletions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_k: FakeClient())
    result = await parse_booking_intent("малую", _rooms())
    assert result is not None
    assert result.room == "Малая"
    assert result.date is None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None
