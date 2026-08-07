from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from backend.core.access import ACCESS_DENIED, ADMIN_ONLY
from backend.core.config import get_settings
from backend.core.database import async_session_factory
from backend.core.rate_limit import allow_telegram_booking_rate
from backend.core.security import TelegramUser
from backend.models import UserRole
from backend.repositories import UserRepository
from backend.services.book_clarification import (
    apply_duration_default,
    first_missing_field,
    resolve_room_canonical,
)
from backend.services.booking import BookingService, RoomService
from backend.services.nl_booking import ParsedIntent, parse_booking_intent
from backend.services.notifications import format_office_clock, office_zone_label
from bot.book_clarify import clear_clarification_if_any, finish_book_from_intent, start_clarification
from bot.book_common import (
    FALLBACK_PARSE,
    LLM_UNAVAILABLE,
    RATE_LIMITED,
    webapp_keyboard,
)

router = Router(name="commands")

INVITE_USAGE = (
    "Использование: /invite <telegram_id>\n"
    "Коллега может узнать свой id через @userinfobot (команда /start)."
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


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await clear_clarification_if_any(state)
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
async def cmd_help(message: Message, state: FSMContext) -> None:
    await clear_clarification_if_any(state)
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
async def cmd_invite(message: Message, state: FSMContext) -> None:
    await clear_clarification_if_any(state)
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
async def cmd_book(message: Message, state: FSMContext) -> None:
    await clear_clarification_if_any(state)
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

    # One rate-limit slot for the whole /book + clarification dialogue
    if not allow_telegram_booking_rate(message.from_user.id):
        await message.answer(RATE_LIMITED)
        return

    async with async_session_factory() as session:
        rooms = await RoomService(session).list_rooms()
        await session.commit()
    room_names = [r.name for r in rooms]

    intent = await parse_booking_intent(args, rooms)
    if intent is None:
        intent = ParsedIntent()
    intent = apply_duration_default(resolve_room_canonical(intent, room_names))

    if first_missing_field(intent) is not None:
        await start_clarification(message, state, intent, room_names)
        return

    await finish_book_from_intent(message, state, intent, room_names)


@router.message(Command("mybookings"))
async def cmd_mybookings(message: Message, state: FSMContext) -> None:
    await clear_clarification_if_any(state)
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
