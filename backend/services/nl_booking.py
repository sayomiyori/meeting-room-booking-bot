"""Tier 3: optional NL booking intent parsing via Groq (no side effects)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, ValidationError

from backend.core.config import get_settings
from backend.core.logging_safe import redact_secrets
from backend.models import Room

logger = structlog.get_logger(__name__)

GROQ_TIMEOUT_SECONDS = 8.0
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


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


async def _call_groq_async(text: str, rooms: Sequence[Room], api_key: str) -> ParsedIntent | None:
    from openai import AsyncOpenAI

    settings = get_settings()
    today_iso = _office_today_iso(settings.office_timezone)
    room_names = [r.name for r in rooms]
    system = _system_prompt(today_iso, settings.office_timezone, room_names)

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=GROQ_BASE_URL,
        timeout=GROQ_TIMEOUT_SECONDS,
    )
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_completion_tokens=512,
    )
    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        return None
    data = json.loads(raw)
    intent = ParsedIntent.model_validate(data)
    if not _intent_complete(intent):
        return None
    return intent


def _call_groq(text: str, rooms: Sequence[Room], api_key: str) -> ParsedIntent | None:
    """Sync wrapper so handlers keep calling parse_booking_intent without await."""
    return asyncio.run(_call_groq_async(text, rooms, api_key))


def parse_booking_intent(text: str, rooms: list[Room]) -> ParsedIntent | None:
    """Parse natural-language booking text into a fully populated intent, or None.

    User text is treated as extraction data only (enforced in the system prompt).
    Any Groq/network/JSON/validation failure → None (never raises).
    """
    settings = get_settings()
    if not settings.groq_api_key:
        return None
    if not (text or "").strip():
        return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_call_groq, text.strip(), rooms, settings.groq_api_key)
            return future.result(timeout=GROQ_TIMEOUT_SECONDS)
    except (concurrent.futures.TimeoutError, TimeoutError, ValidationError, json.JSONDecodeError):
        return None
    except Exception as exc:
        logger.warning(
            "nl_booking_parse_failed",
            error=redact_secrets(
                str(exc),
                settings.bot_token,
                webhook_secret=settings.webhook_secret,
                groq_api_key=settings.groq_api_key,
            ),
        )
        return None
