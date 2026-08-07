from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.core.config import get_settings
from backend.core.database import async_session_factory
from backend.repositories import BookingRepository
from backend.services.booking import booking_to_out
from backend.services.notifications import (
    notify_auto_canceled,
    notify_checkin_prompt,
    notify_reminder,
)

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


async def send_due_checkin_prompts() -> None:
    """At booking start: ask user to confirm presence."""
    settings = get_settings()
    if not settings.no_show_enabled:
        return

    now = datetime.now(UTC)
    # Catch starts in the last ~2 minutes so a missed tick still delivers
    window_start = now - timedelta(minutes=2)
    window_end = now + timedelta(seconds=30)

    async with async_session_factory() as session:
        repo = BookingRepository(session)
        due = await repo.list_due_checkin_prompts(window_start, window_end)
        for booking in due:
            out = booking_to_out(booking)
            await notify_checkin_prompt(booking.telegram_id, out)
            await repo.mark_checkin_prompt_sent(booking.id)
        await session.commit()
        if due:
            logger.info("checkin_prompts_sent", count=len(due))


async def process_no_shows() -> None:
    settings = get_settings()
    if not settings.no_show_enabled:
        return

    now = datetime.now(UTC)
    deadline = now - timedelta(minutes=settings.no_show_window_minutes)

    async with async_session_factory() as session:
        repo = BookingRepository(session)
        candidates = await repo.list_noshow_candidates(
            deadline=deadline,
            min_duration_minutes=settings.no_show_window_minutes,
        )
        for booking in candidates:
            out = booking_to_out(booking)
            await repo.mark_auto_canceled(booking)
            await notify_auto_canceled(booking.telegram_id, out)
        await session.commit()
        if candidates:
            logger.info("noshow_auto_canceled", count=len(candidates))


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.add_job(
            send_due_reminders, "interval", minutes=1, id="reminders", replace_existing=True
        )
        scheduler.add_job(
            send_due_checkin_prompts,
            "interval",
            minutes=1,
            id="checkin_prompts",
            replace_existing=True,
        )
        scheduler.add_job(
            process_no_shows, "interval", minutes=1, id="noshow", replace_existing=True
        )
        scheduler.start()
        logger.info("scheduler_started")


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
