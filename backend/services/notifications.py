import structlog
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from backend.core.config import get_settings
from backend.schemas import BookingOut

logger = structlog.get_logger(__name__)

_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        settings = get_settings()
        if not settings.bot_token:
            raise RuntimeError("BOT_TOKEN is not configured")
        _bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def format_booking_message(booking: BookingOut, *, title: str) -> str:
    room = booking.room_name or f"#{booking.room_id}"
    start = booking.start.strftime("%d.%m.%Y %H:%M UTC")
    end = booking.end.strftime("%H:%M UTC")
    return (
        f"<b>{title}</b>\n"
        f"Комната: {room}\n"
        f"Время: {start} — {end}\n"
        f"ID брони: {booking.id}"
    )


async def notify_booking_created(telegram_id: int, booking: BookingOut) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text=format_booking_message(booking, title="Бронь подтверждена"),
        )
    except Exception:
        logger.exception("failed_to_send_booking_confirmation", telegram_id=telegram_id)


async def notify_booking_conflict(telegram_id: int) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text="Не удалось забронировать: слот только что заняли. Выберите другое время.",
        )
    except Exception:
        logger.exception("failed_to_send_conflict_notice", telegram_id=telegram_id)


async def notify_reminder(telegram_id: int, booking: BookingOut) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text=format_booking_message(booking, title="Напоминание о брони"),
        )
    except Exception:
        logger.exception("failed_to_send_reminder", telegram_id=telegram_id)
