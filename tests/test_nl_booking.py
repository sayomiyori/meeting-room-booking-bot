import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.config import get_settings
from backend.core.rate_limit import reset_booking_rate_limits
from backend.services.nl_booking import ParsedIntent, parse_booking_intent


@pytest.fixture(autouse=True)
def _clear_rate_and_settings():
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


def test_parse_booking_intent_valid_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()
    expected = ParsedIntent(
        room="Большая",
        date="2026-08-08",
        start_time="15:00",
        duration_minutes=60,
    )
    monkeypatch.setattr(
        "backend.services.nl_booking._call_groq",
        lambda *_a, **_k: expected,
    )
    result = parse_booking_intent("большую завтра в 15 на час", _rooms())
    assert result == expected


def test_parse_booking_intent_invalid_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()

    def boom(*_a, **_k):
        raise json.JSONDecodeError("Expecting value", "doc", 0)

    monkeypatch.setattr("backend.services.nl_booking._call_groq", boom)
    assert parse_booking_intent("большую завтра", _rooms()) is None


def test_parse_booking_intent_partial_fields(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()
    # _call_groq returns None when any field is null after parse
    monkeypatch.setattr(
        "backend.services.nl_booking._call_groq",
        lambda *_a, **_k: None,
    )
    assert parse_booking_intent("давай на следующей неделе как-нибудь", _rooms()) is None


def test_parse_booking_intent_without_api_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    assert parse_booking_intent("большую завтра в 15 на час", _rooms()) is None


@pytest.mark.asyncio
async def test_book_ambiguous_text_fallback(monkeypatch):
    from bot.handlers import FALLBACK_PARSE, cmd_book

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "bot.handlers.parse_booking_intent",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "bot.handlers._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    rooms = [
        SimpleNamespace(id=1, name="Большая"),
        SimpleNamespace(id=2, name="Малая"),
    ]

    class FakeRoomService:
        def __init__(self, _session):
            pass

        async def list_rooms(self):
            return rooms

    monkeypatch.setattr("bot.handlers.RoomService", FakeRoomService)
    monkeypatch.setattr(
        "bot.handlers.async_session_factory",
        lambda: _FakeSession(),
    )

    answers: list[str] = []
    message = MagicMock()
    message.from_user = MagicMock(id=4242)
    message.text = "/book давай на следующей неделе как-нибудь"
    message.answer = AsyncMock(side_effect=lambda text, **_k: answers.append(text))

    await cmd_book(message)
    assert answers == [FALLBACK_PARSE]


@pytest.mark.asyncio
async def test_book_valid_builds_webapp_query(monkeypatch):
    from bot.handlers import cmd_book

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("WEBAPP_URL", "https://example.com/app")
    get_settings.cache_clear()

    intent = ParsedIntent(
        room="Большая",
        date="2026-08-10",
        start_time="15:00",
        duration_minutes=60,
    )
    monkeypatch.setattr("bot.handlers.parse_booking_intent", lambda *_a, **_k: intent)
    monkeypatch.setattr(
        "bot.handlers._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    rooms = [SimpleNamespace(id=7, name="Большая")]

    class FakeRoomService:
        def __init__(self, _session):
            pass

        async def list_rooms(self):
            return rooms

    class FakeBookingService:
        def __init__(self, _session):
            pass

        def validate_window(self, start, end):
            return start, end

    monkeypatch.setattr("bot.handlers.RoomService", FakeRoomService)
    monkeypatch.setattr("bot.handlers.BookingService", FakeBookingService)
    monkeypatch.setattr("bot.handlers.async_session_factory", lambda: _FakeSession())

    captured: dict = {}

    async def capture_answer(text, reply_markup=None, **_k):
        captured["text"] = text
        captured["markup"] = reply_markup

    message = MagicMock()
    message.from_user = MagicMock(id=4242)
    message.text = "/book большую 2026-08-10 в 15 на час"
    message.answer = AsyncMock(side_effect=capture_answer)

    await cmd_book(message)

    assert "Понял: Большая, 2026-08-10 15:00" in captured["text"]
    button = captured["markup"].inline_keyboard[0][0]
    url = button.web_app.url
    assert url.startswith("https://example.com/app?")
    assert "room=7" in url
    assert "date=2026-08-10" in url
    assert "start=15%3A00" in url or "start=15:00" in url
    assert "duration=60" in url


@pytest.mark.asyncio
async def test_book_without_groq_key(monkeypatch):
    from bot.handlers import LLM_UNAVAILABLE, cmd_book

    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "bot.handlers._get_registered",
        AsyncMock(return_value=SimpleNamespace(role="member")),
    )

    answers: list[str] = []
    message = MagicMock()
    message.from_user = MagicMock(id=1)
    message.text = "/book большую завтра в 15 на час"
    message.answer = AsyncMock(side_effect=lambda text, **_k: answers.append(text))

    await cmd_book(message)
    assert answers == [LLM_UNAVAILABLE]


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None
