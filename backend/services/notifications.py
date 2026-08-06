from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from backend.core.config import Settings, get_settings
from backend.core.logging_safe import redact_secrets
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


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def office_zone_label(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.office_timezone == "Europe/Moscow":
        return "МСК"
    return settings.office_timezone


def format_office_clock(
    dt: datetime,
    *,
    with_date: bool = False,
    settings: Settings | None = None,
) -> str:
    """Format an instant in OFFICE_TIMEZONE for Telegram messages."""
    settings = settings or get_settings()
    local = _ensure_aware(dt).astimezone(ZoneInfo(settings.office_timezone))
    if with_date:
        return local.strftime("%d.%m.%Y %H:%M")
    return local.strftime("%H:%M")


def format_booking_message(booking: BookingOut, *, title: str) -> str:
    settings = get_settings()
    room = booking.room_name or f"#{booking.room_id}"
    start = format_office_clock(booking.start, with_date=True, settings=settings)
    end = format_office_clock(booking.end, with_date=False, settings=settings)
    zone = office_zone_label(settings)
    return (
        f"<b>{title}</b>\n"
        f"Комната: {room}\n"
        f"Время: {start} — {end} {zone}\n"
        f"ID брони: {booking.id}"
    )


def _log_notify_failure(event: str, telegram_id: int, exc: Exception) -> None:
    token = get_settings().bot_token
    logger.error(event, telegram_id=telegram_id, error=redact_secrets(str(exc), token))


async def notify_booking_created(telegram_id: int, booking: BookingOut) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text=format_booking_message(booking, title="Бронь подтверждена"),
        )
    except Exception as exc:
        _log_notify_failure("failed_to_send_booking_confirmation", telegram_id, exc)


async def notify_booking_canceled(telegram_id: int, booking: BookingOut) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text=format_booking_message(booking, title="Бронь отменена"),
        )
    except Exception as exc:
        _log_notify_failure("failed_to_send_booking_cancellation", telegram_id, exc)


async def notify_booking_conflict(telegram_id: int) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text="Не удалось забронировать: слот только что заняли. Выберите другое время.",
        )
    except Exception as exc:
        _log_notify_failure("failed_to_send_conflict_notice", telegram_id, exc)


async def notify_reminder(telegram_id: int, booking: BookingOut) -> None:
    try:
        bot = get_bot()
        await bot.send_message(
            chat_id=telegram_id,
            text=format_booking_message(booking, title="Напоминание о брони"),
        )
    except Exception as exc:
        _log_notify_failure("failed_to_send_reminder", telegram_id, exc)
