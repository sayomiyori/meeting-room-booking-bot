from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.core.config import get_settings
from backend.core.database import async_session_factory
from backend.repositories import BookingRepository
from backend.services.booking import booking_to_out
from backend.services.notifications import notify_reminder

logger = structlog.get_logger(__name__)
scheduler = AsyncIOScheduler(timezone="UTC")


async def send_due_reminders() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    minutes = settings.reminder_minutes_before
    window_start = now + timedelta(minutes=minutes - 1)
    window_end = now + timedelta(minutes=minutes + 1)

    async with async_session_factory() as session:
        repo = BookingRepository(session)
        due = await repo.list_due_reminders(window_start, window_end)
        for booking in due:
            out = booking_to_out(booking)
            await notify_reminder(booking.telegram_id, out)
            await repo.mark_reminder_sent(booking.id)
        await session.commit()
        if due:
            logger.info("reminders_sent", count=len(due))


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(send_due_reminders, "interval", minutes=1, id="reminders", replace_existing=True)
        scheduler.start()
        logger.info("scheduler_started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
