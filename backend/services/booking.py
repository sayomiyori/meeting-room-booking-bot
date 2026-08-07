from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, get_settings
from backend.core.exceptions import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationAppError,
)
from backend.core.security import TelegramUser
from backend.models import Booking
from backend.repositories import BookingRepository, RoomRepository
from backend.schemas import BookingOut, RecurringBookingOut, RecurringSkipped, SlotPublic, SlotStatus, SlotsResponse
from backend.services.notifications import notify_booking_canceled


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def office_day_bounds(
    day: date,
    settings: Settings | None = None,
) -> tuple[datetime, datetime]:
    """UTC instants for [office_start, office_end) on *day* in OFFICE_TIMEZONE."""
    settings = settings or get_settings()
    tz = ZoneInfo(settings.office_timezone)
    start_local = datetime(
        day.year,
        day.month,
        day.day,
        settings.office_hours_start,
        0,
        0,
        tzinfo=tz,
    )
    end_local = datetime(
        day.year,
        day.month,
        day.day,
        settings.office_hours_end,
        0,
        0,
        tzinfo=tz,
    )
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def booking_to_out(booking: Booking) -> BookingOut:
    start = booking.during.lower
    end = booking.during.upper
    return BookingOut(
        id=booking.id,
        room_id=booking.room_id,
        room_name=booking.room.name if booking.room else None,
        user_display_name=booking.user_display_name,
        start=start,
        end=end,
        canceled=booking.canceled,
        created_at=booking.created_at,
        recurring_group_id=booking.recurring_group_id,
    )


def ceil_to_minutes(dt: datetime, step_minutes: int = 30) -> datetime:
    """Round *up* to the next step boundary (already aligned → unchanged)."""
    dt = _ensure_aware(dt).replace(second=0, microsecond=0)
    remainder = dt.minute % step_minutes
    if remainder == 0:
        return dt
    return dt + timedelta(minutes=step_minutes - remainder)


def bookable_from(
    day_start: datetime,
    day_end: datetime,
    now: datetime,
    *,
    step_minutes: int = 30,
) -> datetime | None:
    """Earliest bookable instant for the day, or None if no free time remains."""
    now = _ensure_aware(now)
    day_start = _ensure_aware(day_start)
    day_end = _ensure_aware(day_end)

    if now.date() > day_start.date():
        return None
    if now.date() < day_start.date():
        return day_start

    if now >= day_end:
        return None
    start = max(day_start, ceil_to_minutes(now, step_minutes))
    if start >= day_end:
        return None
    return start


def build_day_slots(
    day_start: datetime,
    day_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    *,
    now: datetime,
    step_minutes: int | None = None,
    soon_free_minutes: int | None = None,
) -> list[SlotPublic]:
    """Merge busy bookings with free gaps for [day_start, day_end)."""
    settings = get_settings()
    if step_minutes is None:
        step_minutes = settings.slot_step_minutes
    if soon_free_minutes is None:
        soon_free_minutes = settings.soon_free_minutes

    if day_end <= day_start:
        return []

    busy_slots: list[SlotPublic] = []
    soon_free_seconds = soon_free_minutes * 60
    for start, end in sorted(busy_intervals, key=lambda pair: pair[0]):
        clipped_start = max(start, day_start)
        clipped_end = min(end, day_end)
        if clipped_end <= clipped_start:
            continue
        remaining = (end - now).total_seconds()
        status = SlotStatus.busy
        if remaining > 0 and remaining <= soon_free_seconds:
            status = SlotStatus.soon_free
        busy_slots.append(SlotPublic(start=clipped_start, end=clipped_end, status=status))

    available = bookable_from(day_start, day_end, now, step_minutes=step_minutes)
    if available is None:
        return [s for s in busy_slots if s.end > s.start]

    slots: list[SlotPublic] = []
    cursor = available
    for busy in busy_slots:
        if busy.end <= cursor:
            slots.append(busy)
            continue
        if busy.start > cursor:
            slots.append(SlotPublic(start=cursor, end=busy.start, status=SlotStatus.free))
        slots.append(busy)
        cursor = max(cursor, busy.end)
    if cursor < day_end:
        slots.append(SlotPublic(start=cursor, end=day_end, status=SlotStatus.free))

    return [s for s in slots if s.end > s.start]


class RoomService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.rooms = RoomRepository(session)
        self.bookings = BookingRepository(session)
        self.settings = settings or get_settings()

    async def list_rooms(self, *, active_only: bool = True):
        return await self.rooms.list_all(active_only=active_only)

    async def get_slots(self, room_id: int, day: date) -> SlotsResponse:
        room = await self.rooms.get_by_id(room_id, active_only=True)
        if room is None:
            raise NotFoundError("Комната не найдена")

        day_start, day_end = office_day_bounds(day, self.settings)
        bookings = await self.bookings.list_for_room_on_day(room_id, day_start, day_end)
        now = datetime.now(UTC)

        busy_intervals = [
            (booking.during.lower, booking.during.upper) for booking in bookings
        ]
        slots = build_day_slots(
            day_start,
            day_end,
            busy_intervals,
            now=now,
            step_minutes=self.settings.slot_step_minutes,
            soon_free_minutes=self.settings.soon_free_minutes,
        )
        return SlotsResponse(room_id=room_id, date=day.isoformat(), slots=slots)

    async def create_room(
        self,
        *,
        name: str,
        capacity: int,
        description: str = "",
    ):
        name = name.strip()
        if not name:
            raise ValidationAppError("Название комнаты не может быть пустым")
        if capacity < 1:
            raise ValidationAppError("Вместимость должна быть >= 1")
        return await self.rooms.create(
            name=name,
            capacity=capacity,
            description=description.strip(),
            photo_url="",
        )

    async def update_room(
        self,
        room_id: int,
        *,
        name: str | None = None,
        capacity: int | None = None,
        description: str | None = None,
    ):
        room = await self.rooms.get_by_id(room_id)
        if room is None:
            raise NotFoundError("Комната не найдена")
        if name is not None:
            name = name.strip()
            if not name:
                raise ValidationAppError("Название комнаты не может быть пустым")
        if capacity is not None and capacity < 1:
            raise ValidationAppError("Вместимость должна быть >= 1")
        return await self.rooms.update_fields(
            room,
            name=name,
            capacity=capacity,
            description=description.strip() if description is not None else None,
        )

    async def set_room_active(self, room_id: int, active: bool):
        room = await self.rooms.get_by_id(room_id)
        if room is None:
            raise NotFoundError("Комната не найдена")
        return await self.rooms.set_active(room, active)


class BookingService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.rooms = RoomRepository(session)
        self.bookings = BookingRepository(session)

    def _office_hours_message(self) -> str:
        start = self.settings.office_hours_start
        end = self.settings.office_hours_end
        return f"Бронирование доступно с {start:02d}:00 до {end:02d}:00"

    def validate_window(self, start: datetime, end: datetime) -> tuple[datetime, datetime]:
        start = _ensure_aware(start)
        end = _ensure_aware(end)
        now = datetime.now(UTC)

        if start >= end:
            raise ValidationAppError("Время начала должно быть раньше окончания")
        if start < now - timedelta(seconds=30):
            raise ValidationAppError("Нельзя бронировать время в прошлом")

        duration = end - start
        min_d = timedelta(minutes=self.settings.min_duration_minutes)
        max_d = timedelta(minutes=self.settings.max_duration_minutes)
        if duration < min_d:
            raise ValidationAppError(
                f"Минимальная длительность — {self.settings.min_duration_minutes} минут"
            )
        if duration > max_d:
            raise ValidationAppError(
                f"Максимальная длительность — {self.settings.max_duration_minutes // 60} часа"
            )

        tz = ZoneInfo(self.settings.office_timezone)
        office_day = start.astimezone(tz).date()
        office_start, office_end = office_day_bounds(office_day, self.settings)
        if start < office_start or end > office_end:
            raise AppError(self._office_hours_message(), status_code=400)

        return start, end

    async def create(
        self,
        payload_room_id: int,
        start: datetime,
        end: datetime,
        user: TelegramUser,
        *,
        recurring_group_id: UUID | None = None,
    ) -> BookingOut:
        start, end = self.validate_window(start, end)
        room = await self.rooms.get_by_id(payload_room_id, active_only=True)
        if room is None:
            raise NotFoundError("Комната не найдена")

        try:
            booking = await self.bookings.create(
                room_id=payload_room_id,
                telegram_id=user.id,
                user_display_name=user.display_name,
                start=start,
                end=end,
                recurring_group_id=recurring_group_id,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Слот только что заняли") from exc

        booking = await self.bookings.get_by_id(booking.id)
        assert booking is not None
        return booking_to_out(booking)

    async def create_recurring(
        self,
        payload_room_id: int,
        start: datetime,
        end: datetime,
        weeks: int,
        user: TelegramUser,
    ) -> RecurringBookingOut:
        max_weeks = self.settings.max_recurring_weeks
        if weeks < 2 or weeks > max_weeks:
            raise ValidationAppError(f"Число недель должно быть от 2 до {max_weeks}")

        start, end = self.validate_window(start, end)
        room = await self.rooms.get_by_id(payload_room_id, active_only=True)
        if room is None:
            raise NotFoundError("Комната не найдена")

        group_id = uuid4()
        created: list[BookingOut] = []
        skipped: list[RecurringSkipped] = []
        tz = ZoneInfo(self.settings.office_timezone)

        for week in range(weeks):
            week_start = start + timedelta(weeks=week)
            week_end = end + timedelta(weeks=week)
            date_label = week_start.astimezone(tz).date().isoformat()
            try:
                # Re-validate office hours / past for each occurrence
                self.validate_window(week_start, week_end)
            except AppError as exc:
                skipped.append(RecurringSkipped(date=date_label, reason=exc.message))
                continue

            try:
                async with self.session.begin_nested():
                    booking = await self.bookings.create(
                        room_id=payload_room_id,
                        telegram_id=user.id,
                        user_display_name=user.display_name,
                        start=week_start,
                        end=week_end,
                        recurring_group_id=group_id,
                    )
                # Room already loaded above — avoid N+1 get_by_id per insert
                booking.room = room
                created.append(booking_to_out(booking))
            except IntegrityError:
                skipped.append(RecurringSkipped(date=date_label, reason="занято"))

        return RecurringBookingOut(created=created, skipped=skipped)

    async def my_bookings(self, user: TelegramUser) -> list[BookingOut]:
        rows = await self.bookings.list_active_for_user(user.id)
        return [booking_to_out(b) for b in rows]

    async def cancel(self, booking_id: int, user: TelegramUser) -> BookingOut:
        booking = await self.bookings.get_by_id(booking_id)
        if booking is None or booking.canceled:
            raise NotFoundError("Бронь не найдена")
        if booking.telegram_id != user.id:
            raise ForbiddenError("Нельзя отменить чужую бронь")
        booking = await self.bookings.cancel(booking)
        out = booking_to_out(booking)
        await notify_booking_canceled(user.id, out)
        return out

    async def cancel_series(self, group_id: UUID, user: TelegramUser) -> list[BookingOut]:
        rows = await self.bookings.cancel_future_in_group(
            group_id=group_id,
            telegram_id=user.id,
        )
        if not rows:
            raise NotFoundError("Серия не найдена или уже завершена")
        outs = [booking_to_out(b) for b in rows]
        for out in outs:
            await notify_booking_canceled(user.id, out)
        return outs

    async def check_in(self, booking_id: int, user: TelegramUser) -> BookingOut:
        booking = await self.bookings.get_by_id(booking_id)
        # Same message for missing vs foreign — avoid existence oracle via callback
        if booking is None or booking.canceled or booking.telegram_id != user.id:
            raise NotFoundError("Бронь не найдена или недоступна")
        booking = await self.bookings.mark_checked_in(booking)
        return booking_to_out(booking)
