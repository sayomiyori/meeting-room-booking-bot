"""FSM clarification dialogue for incomplete /book LLM parses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from backend.core.access import ACCESS_DENIED
from backend.core.database import async_session_factory
from backend.core.exceptions import AppError
from backend.services.book_clarification import (
    CLARIFICATION_ATTEMPTS,
    CLARIFICATION_TIMEOUT_SECONDS,
    apply_clarification_answer,
    apply_duration_default,
    clarification_not_understood,
    clarification_question,
    first_missing_field,
    resolve_room_canonical,
)
from backend.services.booking import BookingService, RoomService
from backend.services.nl_booking import ParsedIntent
from backend.services.notifications import format_office_clock
from bot.book_common import (
    FALLBACK_PARSE,
    build_book_webapp_url,
    find_room_id,
    intent_to_window,
    webapp_keyboard,
)

router = Router(name="book_clarify")


class BookingClarification(StatesGroup):
    awaiting_clarification = State()


async def clear_clarification_if_any(state: FSMContext) -> None:
    current = await state.get_state()
    if current == BookingClarification.awaiting_clarification.state:
        await state.clear()


def _intent_from_data(data: dict[str, Any]) -> ParsedIntent:
    partial = data.get("partial_intent") or {}
    return ParsedIntent.model_validate(partial)


async def _get_registered(telegram_id: int):
    from backend.repositories import UserRepository

    async with async_session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        await session.commit()
    return user


async def _load_room_names() -> list[str]:
    async with async_session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        await session.commit()
    return [r.name for r in rooms]


async def start_clarification(
    message: Message,
    state: FSMContext,
    intent: ParsedIntent,
    room_names: list[str],
    *,
    attempts_left: int = CLARIFICATION_ATTEMPTS,
) -> None:
    intent = apply_duration_default(resolve_room_canonical(intent, room_names))
    missing = first_missing_field(intent)
    if missing is None:
        await finish_book_from_intent(message, state, intent, room_names)
        return
    if attempts_left <= 0:
        await state.clear()
        await message.answer(FALLBACK_PARSE)
        return

    await state.set_state(BookingClarification.awaiting_clarification)
    await state.update_data(
        partial_intent=intent.model_dump(),
        missing_field=missing,
        attempts_left=attempts_left - 1,
        asked_at=datetime.now(UTC).isoformat(),
    )
    await message.answer(clarification_question(missing, room_names))


async def finish_book_from_intent(
    message: Message,
    state: FSMContext,
    intent: ParsedIntent,
    room_names: list[str] | None = None,
) -> None:
    await state.clear()
    intent = apply_duration_default(intent)
    if room_names is None:
        room_names = await _load_room_names()
    intent = resolve_room_canonical(intent, room_names)

    if first_missing_field(intent) is not None or intent.duration_minutes is None:
        await message.answer(FALLBACK_PARSE)
        return

    async with async_session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        await session.commit()

    assert intent.room and intent.date and intent.start_time and intent.duration_minutes
    room_id = find_room_id(rooms, intent.room)
    if room_id is None:
        await message.answer(FALLBACK_PARSE)
        return

    room_name = next(r.name for r in rooms if r.id == room_id)
    try:
        start_utc, end_utc = intent_to_window(
            intent.date,
            intent.start_time,
            intent.duration_minutes,
        )
    except (TypeError, ValueError):
        await message.answer(FALLBACK_PARSE)
        return

    async with async_session_factory() as session:
        try:
            BookingService(session).validate_window(start_utc, end_utc)
        except AppError as exc:
            await message.answer(exc.message)
            return

    url = build_book_webapp_url(
        room_id=room_id,
        date=intent.date,
        start_time=intent.start_time,
        duration_minutes=intent.duration_minutes,
    )
    end_label = format_office_clock(end_utc, with_date=False)
    text = (
        f"Понял: {room_name}, {intent.date} {intent.start_time}–{end_label}. "
        "Проверьте и подтвердите:"
    )
    await message.answer(text, reply_markup=webapp_keyboard(url))


@router.message(
    StateFilter(BookingClarification.awaiting_clarification),
    F.text,
)
async def on_clarification_answer(message: Message, state: FSMContext) -> None:
    """Handle clarification replies. Always answer — never swallow a failed parse."""
    if message.from_user is None:
        return
    # Let command handlers own slash-commands (they clear clarification state).
    if (message.text or "").startswith("/"):
        return

    try:
        await _handle_clarification_answer(message, state)
    except Exception:
        import structlog

        structlog.get_logger(__name__).exception("clarification_handler_failed")
        await state.clear()
        await message.answer(FALLBACK_PARSE)


async def _handle_clarification_answer(message: Message, state: FSMContext) -> None:
    if await _get_registered(message.from_user.id) is None:  # type: ignore[union-attr]
        await state.clear()
        await message.answer(ACCESS_DENIED)
        return

    data = await state.get_data()
    asked_at_raw = data.get("asked_at")
    if asked_at_raw:
        try:
            asked_at = datetime.fromisoformat(asked_at_raw)
            if asked_at.tzinfo is None:
                asked_at = asked_at.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - asked_at).total_seconds()
            if age > CLARIFICATION_TIMEOUT_SECONDS:
                await state.clear()
                await message.answer(
                    "Диалог бронирования истёк. Начните заново: /book …"
                )
                return
        except ValueError:
            pass

    missing = data.get("missing_field")
    attempts_left = int(data.get("attempts_left", 0))
    intent = _intent_from_data(data)
    room_names = await _load_room_names()

    if missing not in {"room", "date", "start_time"}:
        await state.clear()
        await message.answer(FALLBACK_PARSE)
        return

    updated = apply_clarification_answer(
        intent,
        missing,  # type: ignore[arg-type]
        message.text or "",
        room_names,
    )
    if updated is None:
        if attempts_left <= 0:
            await state.clear()
            await message.answer(FALLBACK_PARSE)
            return
        await state.update_data(
            attempts_left=attempts_left - 1,
            asked_at=datetime.now(UTC).isoformat(),
        )
        await message.answer(clarification_not_understood(missing, room_names))
        return

    next_missing = first_missing_field(apply_duration_default(updated))
    if next_missing is None:
        await finish_book_from_intent(message, state, updated, room_names)
        return

    if attempts_left <= 0:
        await state.clear()
        await message.answer(FALLBACK_PARSE)
        return

    await state.update_data(
        partial_intent=updated.model_dump(),
        missing_field=next_missing,
        attempts_left=attempts_left - 1,
        asked_at=datetime.now(UTC).isoformat(),
    )
    await message.answer(clarification_question(next_missing, room_names))
