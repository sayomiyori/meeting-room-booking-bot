"""Deterministic field parsers for /book clarification dialogue (no LLM)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

from backend.core.config import get_settings
from backend.services.nl_booking import ParsedIntent

MissingField = Literal["room", "date", "start_time"]
CLARIFICATION_ATTEMPTS = 2
CLARIFICATION_TIMEOUT_SECONDS = 5 * 60

_MONTHS_RU: dict[str, int] = {
    "января": 1,
    "янв": 1,
    "февраля": 2,
    "фев": 2,
    "марта": 3,
    "мар": 3,
    "апреля": 4,
    "апр": 4,
    "мая": 5,
    "июня": 6,
    "июн": 6,
    "июля": 7,
    "июл": 7,
    "августа": 8,
    "авг": 8,
    "сентября": 9,
    "сен": 9,
    "октября": 10,
    "окт": 10,
    "ноября": 11,
    "ноя": 11,
    "декабря": 12,
    "дек": 12,
}

_WEEKDAYS_RU: dict[str, int] = {
    "понедельник": 0,
    "пн": 0,
    "вторник": 1,
    "вт": 1,
    "среда": 2,
    "среду": 2,
    "ср": 2,
    "четверг": 3,
    "чт": 3,
    "пятница": 4,
    "пятницу": 4,
    "пт": 4,
    "суббота": 5,
    "субботу": 5,
    "сб": 5,
    "воскресенье": 6,
    "вс": 6,
}


def apply_duration_default(intent: ParsedIntent, default: int = 60) -> ParsedIntent:
    if intent.duration_minutes is None:
        return intent.model_copy(update={"duration_minutes": default})
    return intent


def match_room_name(text: str, room_names: Sequence[str]) -> str | None:
    """Case-insensitive / substring / short-stem match against active room names."""
    raw = (text or "").strip()
    if not raw:
        return None
    needle = raw.casefold()
    # Exact first
    for name in room_names:
        if name.casefold() == needle:
            return name
    # Substring either way
    hits = [name for name in room_names if name.casefold() in needle or needle in name.casefold()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # Prefer longest room name match
        return max(hits, key=lambda n: len(n))
    # Stem: shared prefix of length >= 3 (малую ↔ Малая)
    for name in room_names:
        n = name.casefold()
        for length in range(min(len(n), len(needle), 6), 2, -1):
            if n[:length] == needle[:length]:
                return name
    return None


def parse_clarification_date(text: str, *, today: date | None = None) -> str | None:
    """Parse relative/explicit RU dates → YYYY-MM-DD. No LLM."""
    settings = get_settings()
    tz = ZoneInfo(settings.office_timezone)
    today = today or datetime.now(tz).date()
    raw = (text or "").strip().casefold()
    if not raw:
        return None

    if raw in {"сегодня", "today"}:
        return today.isoformat()
    if raw in {"завтра", "tomorrow"}:
        return (today + timedelta(days=1)).isoformat()
    if raw in {"послезавтра"}:
        return (today + timedelta(days=2)).isoformat()

    for word, weekday in _WEEKDAYS_RU.items():
        if raw == word or raw.startswith(word + " "):
            delta = (weekday - today.weekday()) % 7
            if delta == 0:
                delta = 7
            return (today + timedelta(days=delta)).isoformat()

    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", raw)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    m = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)", raw)
    if m:
        day = int(m.group(1))
        month = _MONTHS_RU.get(m.group(2))
        if month:
            year = today.year
            try:
                parsed = date(year, month, day)
            except ValueError:
                return None
            if parsed < today:
                try:
                    parsed = date(year + 1, month, day)
                except ValueError:
                    return None
            return parsed.isoformat()

    return None


def parse_clarification_time(text: str) -> str | None:
    """Parse HH:MM / H:MM / 'в HH' → HH:MM."""
    raw = (text or "").strip().casefold()
    if not raw:
        return None
    raw = re.sub(r"^в\s+", "", raw)

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None

    m = re.fullmatch(r"(\d{1,2})", raw)
    if m:
        hour = int(m.group(1))
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"
        return None

    return None


def resolve_room_canonical(intent: ParsedIntent, room_names: Sequence[str]) -> ParsedIntent:
    """Map LLM room string onto an active room name, or clear if unmatched."""
    if intent.room is None:
        return intent
    matched = match_room_name(intent.room, room_names)
    if matched is None:
        return intent.model_copy(update={"room": None})
    return intent.model_copy(update={"room": matched})


def first_missing_field(intent: ParsedIntent) -> MissingField | None:
    if intent.room is None:
        return "room"
    if intent.date is None:
        return "date"
    if intent.start_time is None:
        return "start_time"
    return None


def clarification_question(field: MissingField, room_names: Sequence[str]) -> str:
    if field == "room":
        names = ", ".join(room_names) if room_names else "—"
        return f"Какую комнату? Доступны: {names}"
    if field == "date":
        return "На какую дату? (например: завтра, 15 августа)"
    return "Во сколько? (например: 15:00)"


def apply_clarification_answer(
    intent: ParsedIntent,
    field: MissingField,
    text: str,
    room_names: Sequence[str],
) -> ParsedIntent | None:
    """Return updated intent, or None if the answer could not be parsed."""
    if field == "room":
        matched = match_room_name(text, room_names)
        if matched is None:
            return None
        return intent.model_copy(update={"room": matched})
    if field == "date":
        parsed = parse_clarification_date(text)
        if parsed is None:
            return None
        return intent.model_copy(update={"date": parsed})
    parsed_time = parse_clarification_time(text)
    if parsed_time is None:
        return None
    return intent.model_copy(update={"start_time": parsed_time})
