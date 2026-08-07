from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from backend.core.config import get_settings
from backend.core.database import async_session_factory
from backend.core.exceptions import AppError
from backend.core.rate_limit import allow_telegram_booking_rate
from backend.core.security import TelegramUser
from backend.services.booking import BookingService, RoomService
from backend.services.nl_booking import parse_booking_intent
from backend.services.notifications import format_office_clock, office_zone_label

router = Router(name="commands")

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


def _command_args(message: Message, command: str) -> str:
    text = (message.text or "").strip()
    prefix = f"/{command}"
    if text.lower().startswith(prefix.lower()):
        rest = text[len(prefix) :]
        if rest.startswith("@"):  # /book@BotName args
            space = rest.find(" ")
            rest = rest[space:] if space >= 0 else ""
        return rest.lstrip()
    return ""


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
        "• /help — эта справка\n\n"
        "Напоминание придёт за несколько минут до начала встречи."
    )


@router.message(Command("book"))
async def cmd_book(message: Message) -> None:
    if message.from_user is None:
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

    intent = parse_booking_intent(args, rooms)
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
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отменить",
                        callback_data=f"cancel:{booking.id}",
                    )
                ]
            ]
        )
        await message.answer(text, reply_markup=kb)


@router.callback_query(lambda c: c.data is not None and c.data.startswith("cancel:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None:
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
