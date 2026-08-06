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
from backend.core.security import TelegramUser
from backend.services.booking import BookingService
from backend.services.notifications import format_office_clock, office_zone_label

router = Router(name="commands")


def webapp_keyboard() -> InlineKeyboardMarkup:
    settings = get_settings()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Забронировать",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            ]
        ]
    )


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
        "• /mybookings — ваши активные брони и отмена\n"
        "• /help — эта справка\n\n"
        "Напоминание придёт за несколько минут до начала встречи."
    )


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
