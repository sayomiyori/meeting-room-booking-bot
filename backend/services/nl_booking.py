"""Tier 3: optional NL booking intent parsing via Groq (no side effects)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from backend.core.config import get_settings
from backend.core.logging_safe import redact_secrets
from backend.models import Room

logger = structlog.get_logger(__name__)
_stdlib_logger = logging.getLogger(__name__)

GROQ_TIMEOUT_SECONDS = 8.0
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MAX_COMPLETION_TOKENS = 300


class ParsedIntent(BaseModel):
    room: str | None = None
    date: str | None = None
    start_time: str | None = None
    duration_minutes: int | None = None


def _system_prompt(today_iso: str, office_timezone: str, room_names: Sequence[str]) -> str:
    names = ", ".join(room_names) if room_names else "(нет)"
    return (
        "Ты парсишь запрос на бронирование переговорной в JSON. "
        f"Сегодня {today_iso} ({office_timezone}). "
        f"Доступные комнаты: {names}. "
        "Верни ТОЛЬКО JSON без пояснений в формате: "
        '{"room": string|null, "date": "YYYY-MM-DD"|null, '
        '"start_time": "HH:MM"|null, "duration_minutes": int|null}. '
        "Если что-то не удалось однозначно определить — null для этого поля, не гадай."
    )


def _intent_complete(intent: ParsedIntent) -> bool:
    return (
        intent.room is not None
        and intent.date is not None
        and intent.start_time is not None
        and intent.duration_minutes is not None
    )


def _office_today_iso(office_timezone: str) -> str:
    return datetime.now(ZoneInfo(office_timezone)).date().isoformat()


def _log_parse_failure(settings, exc: BaseException | str) -> None:
    """Always surface Groq/parse failures — silent None made 400 diagnosis hard."""
    raw = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    error = redact_secrets(
        raw,
        settings.bot_token,
        webhook_secret=settings.webhook_secret,
        groq_api_key=settings.groq_api_key,
    )
    logger.warning(
        "nl_booking_parse_failed",
        error=error,
        error_type=type(exc).__name__ if isinstance(exc, BaseException) else "str",
    )
    # stdlib bridge so pytest caplog / external aggregators see the same text
    _stdlib_logger.warning("nl_booking_parse_failed error=%s", error)


async def parse_booking_intent(text: str, rooms: list[Room]) -> ParsedIntent | None:
    """Parse natural-language booking text into a fully populated intent, or None.

    Creates and closes AsyncOpenAI inside this await so the httpx client never
    outlives the request event loop (avoids "Event loop is closed" on aclose).

    User text is treated as extraction data only (enforced in the system prompt).
    Any Groq/network/JSON/validation failure → None (never raises).
    """
    settings = get_settings()
    if not settings.groq_api_key:
        return None
    if not (text or "").strip():
        return None

    from openai import AsyncOpenAI

    today_iso = _office_today_iso(settings.office_timezone)
    room_names = [r.name for r in rooms]
    system = _system_prompt(today_iso, settings.office_timezone, room_names)

    try:
        async with AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url=GROQ_BASE_URL,
            timeout=GROQ_TIMEOUT_SECONDS,
        ) as client:
            response = await client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text.strip()},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_completion_tokens=GROQ_MAX_COMPLETION_TOKENS,
            )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            _log_parse_failure(settings, "empty model content (no JSON document)")
            return None
        data = json.loads(raw)
        intent = ParsedIntent.model_validate(data)
        # Return partial intents so /book can run a clarification dialogue
        if not _intent_complete(intent):
            logger.info(
                "nl_booking_partial_intent",
                fields=intent.model_dump(),
            )
        return intent
    except Exception as exc:
        _log_parse_failure(settings, exc)
        return None
