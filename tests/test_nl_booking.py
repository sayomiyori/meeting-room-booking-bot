import json
import warnings
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


@pytest.mark.asyncio
async def test_parse_booking_intent_valid_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()
    expected = ParsedIntent(
        room="Большая",
        date="2026-08-08",
        start_time="15:00",
        duration_minutes=60,
    )

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=expected.model_dump_json(),
                        )
                    )
                ]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **_kwargs: FakeClient(),
    )
    result = await parse_booking_intent("большую завтра в 15 на час", _rooms())
    assert result == expected


@pytest.mark.asyncio
async def test_parse_booking_intent_invalid_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="not-json{"))]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kwargs: FakeClient())
    assert await parse_booking_intent("большую завтра", _rooms()) is None


@pytest.mark.asyncio
async def test_parse_booking_intent_partial_fields(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "room": None,
                                    "date": None,
                                    "start_time": None,
                                    "duration_minutes": None,
                                }
                            )
                        )
                    )
                ]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kwargs: FakeClient())
    result = await parse_booking_intent("давай на следующей неделе как-нибудь", _rooms())
    assert result is not None
    assert result.room is None
    assert result.date is None


@pytest.mark.asyncio
async def test_parse_booking_intent_without_api_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    assert await parse_booking_intent("большую завтра в 15 на час", _rooms()) is None


@pytest.mark.asyncio
async def test_reasoning_token_limit_error_is_logged(monkeypatch, caplog):
    """Groq 400 (json_validate_failed / max tokens) → None + warning with full error."""
    import logging

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()

    error_text = (
        "Error code: 400 - Failed to generate JSON. "
        "max completion tokens reached before generating a valid document "
        "(json_validate_failed)"
    )

    class FakeCompletions:
        async def create(self, **_kwargs):
            raise RuntimeError(error_text)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kwargs: FakeClient())

    with caplog.at_level(logging.WARNING, logger="backend.services.nl_booking"):
        result = await parse_booking_intent("большую завтра в 15 на час", _rooms())

    assert result is None
    joined = " ".join(r.message for r in caplog.records)
    assert "nl_booking_parse_failed" in joined
    assert "max completion tokens" in joined.lower()
    assert "json_validate_failed" in joined.lower()


@pytest.mark.asyncio
async def test_parse_booking_intent_repeated_calls_no_loop_closed(monkeypatch, capsys):
    """Sequential /book-like calls must close AsyncOpenAI in-loop (no orphan aclose)."""
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()

    closed: list[bool] = []

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "room": "Большая",
                                    "date": "2026-08-10",
                                    "start_time": "15:00",
                                    "duration_minutes": 60,
                                }
                            )
                        )
                    )
                ]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            closed.append(True)
            return False

        async def close(self):
            closed.append(True)

    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kwargs: FakeClient())

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _ in range(3):
            result = await parse_booking_intent("большую завтра в 15 на час", _rooms())
            assert result is not None
            assert result.room == "Большая"

    err = capsys.readouterr().err
    joined = "\n".join(str(w.message) for w in caught) + "\n" + err
    assert "Event loop is closed" not in joined
    assert "Task exception was never retrieved" not in joined
    assert len(closed) == 3


@pytest.mark.asyncio
async def test_book_ambiguous_text_starts_clarification(monkeypatch):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.book_clarify import BookingClarification
    from bot.handlers import cmd_book

    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "bot.handlers.parse_booking_intent",
        AsyncMock(return_value=None),
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

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=4242))

    answers: list[str] = []
    message = MagicMock()
    message.from_user = MagicMock(id=4242)
    message.text = "/book давай на следующей неделе как-нибудь"
    message.answer = AsyncMock(side_effect=lambda text, **_k: answers.append(text))

    await cmd_book(message, state)
    assert await state.get_state() == BookingClarification.awaiting_clarification.state
    assert "комнат" in answers[-1].casefold()


@pytest.mark.asyncio
async def test_book_valid_builds_webapp_query(monkeypatch):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

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
    monkeypatch.setattr("bot.handlers.parse_booking_intent", AsyncMock(return_value=intent))
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
    monkeypatch.setattr("bot.book_clarify.RoomService", FakeRoomService)
    monkeypatch.setattr("bot.book_clarify.BookingService", FakeBookingService)
    monkeypatch.setattr("bot.handlers.async_session_factory", lambda: _FakeSession())
    monkeypatch.setattr("bot.book_clarify.async_session_factory", lambda: _FakeSession())

    captured: dict = {}

    async def capture_answer(text, reply_markup=None, **_k):
        captured["text"] = text
        captured["markup"] = reply_markup

    message = MagicMock()
    message.from_user = MagicMock(id=4242)
    message.text = "/book большую 2026-08-10 в 15 на час"
    message.answer = AsyncMock(side_effect=capture_answer)

    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=4242),
    )
    await cmd_book(message, state)

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
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from bot.book_common import LLM_UNAVAILABLE
    from bot.handlers import cmd_book

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

    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=1),
    )
    await cmd_book(message, state)
    assert answers == [LLM_UNAVAILABLE]


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        return None
