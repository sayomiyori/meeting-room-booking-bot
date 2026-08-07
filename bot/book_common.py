"""Shared helpers for /book and clarification dialogue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from backend.core.config import get_settings

FALLBACK_PARSE = "Не удалось разобрать запрос, попробуйте /start для обычного бронирования"
LLM_UNAVAILABLE = "LLM-бронирование недоступно"
RATE_LIMITED = "Слишком много запросов, подождите минуту"


def webapp_keyboard(url: str | None = None) -> InlineKeyboardMarkup:
    settings = get_settings()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Забронировать",
                    web_app=WebAppInfo(url=url or settings.webapp_url),
                )
            ]
        ]
    )


def find_room_id(rooms: list, name: str) -> int | None:
    needle = name.casefold()
    for room in rooms:
        if room.name.casefold() == needle:
            return room.id
    return None


def intent_to_window(
    date_str: str,
    start_time: str,
    duration_minutes: int,
) -> tuple[datetime, datetime]:
    settings = get_settings()
    tz = ZoneInfo(settings.office_timezone)
    start_local = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M").replace(
        tzinfo=tz
    )
    end_local = start_local + timedelta(minutes=duration_minutes)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def build_book_webapp_url(
    *,
    room_id: int,
    date: str,
    start_time: str,
    duration_minutes: int,
) -> str:
    settings = get_settings()
    base = settings.webapp_url.rstrip("/")
    query = urlencode(
        {
            "room": str(room_id),
            "date": date,
            "start": start_time,
            "duration": str(duration_minutes),
        }
    )
    return f"{base}?{query}"
