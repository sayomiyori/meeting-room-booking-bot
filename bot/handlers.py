from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from backend.core.access import ACCESS_DENIED, ADMIN_ONLY
from backend.core.config import get_settings
from backend.core.database import async_session_factory
from backend.core.exceptions import AppError
from backend.core.rate_limit import allow_telegram_booking_rate
from backend.core.security import TelegramUser
from backend.models import UserRole
from backend.repositories import UserRepository
from backend.services.booking import BookingService, RoomService
from backend.services.nl_booking import parse_booking_intent
from backend.services.notifications import format_office_clock, office_zone_label

router = Router(name="commands")

FALLBACK_PARSE = "Не удалось разобрать запрос, попробуйте /start для обычного бронирования"
LLM_UNAVAILABLE = "LLM-бронирование недоступно"
RATE_LIMITED = "Слишком много запросов, подождите минуту"
INVITE_USAGE = (
    "Использование: /invite <telegram_id>\n"
    "Коллега может узнать свой id через @userinfobot (команда /start)."
)


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


def _command_args(message: Message, command: str) -> str:
    text = (message.text or "").strip()
    prefix = f"/{command}"
    if text.lower().startswith(prefix.lower()):
        rest = text[len(prefix) :]
        if rest.startswith("@"):
            space = rest.find(" ")
            rest = rest[space:] if space >= 0 else ""
        return rest.lstrip()
    return ""


async def _get_registered(telegram_id: int):
    async with async_session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_id)
        await session.commit()
    return user


def _find_room_id(rooms: list, name: str) -> int | None:
    needle = name.casefold()
    for room in rooms:
        if room.name.casefold() == needle:
            return room.id
    return None


def _intent_to_window(
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


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        return
    registered = await _get_registered(message.from_user.id)
    if registered is None:
        await message.answer(ACCESS_DENIED)
        return
    await message.answer(
        "Привет! Я бот бронирования переговорных.\n"
        "Нажмите кнопку ниже, чтобы открыть Mini App.",
        reply_markup=webapp_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Что умеет бот</b>\n"
        "• /start — открыть Mini App для бронирования\n"
        "• /book … — забронировать фразой (если доступно LLM)\n"
        "• /mybookings — ваши активные брони и отмена\n"
        "• /invite &lt;id&gt; — добавить коллегу (только админ)\n"
        "• /admin — управление комнатами (только админ)\n"
        "• /help — эта справка\n\n"
        "Доступ по whitelist: новый пользователь пишет /start "
        "боту @userinfobot, присылает свой telegram_id админу, "
        "админ делает /invite.\n"
        "Напоминание придёт за несколько минут до начала встречи."
    )


@router.message(Command("invite"))
async def cmd_invite(message: Message) -> None:
    if message.from_user is None:
        return
    admin = await _get_registered(message.from_user.id)
    if admin is None or admin.role != UserRole.admin:
        await message.answer(ADMIN_ONLY)
        return

    args = _command_args(message, "invite")
    if not args:
        await message.answer(INVITE_USAGE)
        return
    try:
        new_id = int(args.split()[0])
    except ValueError:
        await message.answer(INVITE_USAGE)
        return
    if new_id <= 0:
        await message.answer("telegram_id должен быть положительным числом.")
        return

    async with async_session_factory() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_telegram_id(new_id)
        if existing is not None:
            await session.commit()
            await message.answer(f"Пользователь {new_id} уже в whitelist (role={existing.role}).")
            return
        await repo.create(telegram_id=new_id, display_name="", role=UserRole.member)
        await session.commit()
    await message.answer(f"Пользователь {new_id} добавлен как member.")


@router.message(Command("book"))
async def cmd_book(message: Message) -> None:
    if message.from_user is None:
        return

    if await _get_registered(message.from_user.id) is None:
        await message.answer(ACCESS_DENIED)
        return

    settings = get_settings()
    if not settings.groq_api_key:
        await message.answer(LLM_UNAVAILABLE)
        return

    args = _command_args(message, "book")
    if not args:
        await message.answer(
            "Напишите после команды, что забронировать, например: "
            "/book большую завтра в 15 на час"
        )
        return

    if not allow_telegram_booking_rate(message.from_user.id):
        await message.answer(RATE_LIMITED)
        return

    async with async_session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        await session.commit()

    intent = await parse_booking_intent(args, rooms)
    if (
        intent is None
        or intent.room is None
        or intent.date is None
        or intent.start_time is None
        or intent.duration_minutes is None
    ):
        await message.answer(FALLBACK_PARSE)
        return

    room_id = _find_room_id(rooms, intent.room)
    if room_id is None:
        await message.answer(FALLBACK_PARSE)
        return

    room_name = next(r.name for r in rooms if r.id == room_id)

    try:
        start_utc, end_utc = _intent_to_window(
            intent.date, intent.start_time, intent.duration_minutes
        )
    except ValueError:
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


@router.message(Command("mybookings"))
async def cmd_mybookings(message: Message) -> None:
    if message.from_user is None:
        return
    if await _get_registered(message.from_user.id) is None:
        await message.answer(ACCESS_DENIED)
        return

    user = TelegramUser(
        id=message.from_user.id,
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        username=message.from_user.username,
    )
    async with async_session_factory() as session:
        bookings = await BookingService(session).my_bookings(user)
        await session.commit()

    if not bookings:
        await message.answer("У вас нет активных броней.")
        return

    for booking in bookings:
        room = booking.room_name or f"#{booking.room_id}"
        start = format_office_clock(booking.start, with_date=True)
        end = format_office_clock(booking.end, with_date=False)
        zone = office_zone_label()
        text = (
            f"<b>{room}</b>\n"
            f"{start} — {end} {zone}\n"
            f"ID: {booking.id}"
        )
        rows = [
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=f"cancel:{booking.id}",
                )
            ]
        ]
        if booking.recurring_group_id is not None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Отменить всю серию",
                        callback_data=f"cancel_series:{booking.recurring_group_id}",
                    )
                ]
            )
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    # skip cancel_series:
    if callback.data.startswith("cancel_series:"):
        return
    if await _get_registered(callback.from_user.id) is None:
        await callback.answer(ACCESS_DENIED, show_alert=True)
        return

    booking_id = int(callback.data.split(":", 1)[1])
    user = TelegramUser(
        id=callback.from_user.id,
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        username=callback.from_user.username,
    )
    async with async_session_factory() as session:
        try:
            await BookingService(session).cancel(booking_id, user)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await callback.answer(str(getattr(exc, "message", exc)), show_alert=True)
            return

    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_text(f"{callback.message.html_text}\n\n<i>Отменено</i>")


@router.callback_query(F.data.startswith("cancel_series:"))
async def cb_cancel_series(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if await _get_registered(callback.from_user.id) is None:
        await callback.answer(ACCESS_DENIED, show_alert=True)
        return

    group_id = UUID(callback.data.split(":", 1)[1])
    user = TelegramUser(
        id=callback.from_user.id,
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        username=callback.from_user.username,
    )
    async with async_session_factory() as session:
        try:
            canceled = await BookingService(session).cancel_series(group_id, user)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await callback.answer(str(getattr(exc, "message", exc)), show_alert=True)
            return

    await callback.answer(f"Отменено: {len(canceled)}")
    if callback.message:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n<i>Серия отменена ({len(canceled)})</i>"
        )


@router.callback_query(F.data.startswith("checkin:"))
async def cb_checkin(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
        return
    if await _get_registered(callback.from_user.id) is None:
        await callback.answer(ACCESS_DENIED, show_alert=True)
        return

    booking_id = int(callback.data.split(":", 1)[1])
    user = TelegramUser(
        id=callback.from_user.id,
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        username=callback.from_user.username,
    )
    async with async_session_factory() as session:
        try:
            await BookingService(session).check_in(booking_id, user)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await callback.answer(str(getattr(exc, "message", exc)), show_alert=True)
            return

    await callback.answer("Присутствие подтверждено")
    if callback.message:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n<i>Присутствие подтверждено</i>"
        )
