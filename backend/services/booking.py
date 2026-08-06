from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import Settings, get_settings
from backend.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from backend.core.security import TelegramUser
from backend.models import Booking
from backend.repositories import BookingRepository, RoomRepository
from backend.schemas import BookingOut, SlotPublic, SlotStatus, SlotsResponse


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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
        # Requested calendar day is entirely in the past
        return None
    if now.date() < day_start.date():
        return day_start

    # Today (UTC)
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
) -> list[SlotPublic]:
    """Merge busy bookings with free gaps for [day_start, day_end).

    For *today*, free gaps start at ceil_30(now), not midnight — so the UI
    never offers past times. If the whole day has passed, free gaps are omitted.
    Never emits zero-length intervals.
    """
    if day_end <= day_start:
        return []

    busy_slots: list[SlotPublic] = []
    for start, end in sorted(busy_intervals, key=lambda pair: pair[0]):
        clipped_start = max(start, day_start)
        clipped_end = min(end, day_end)
        if clipped_end <= clipped_start:
            continue
        remaining = (end - now).total_seconds()
        status = SlotStatus.busy
        if remaining > 0 and remaining <= 30 * 60:
            status = SlotStatus.soon_free
        busy_slots.append(SlotPublic(start=clipped_start, end=clipped_end, status=status))

    available = bookable_from(day_start, day_end, now)
    if available is None:
        return [s for s in busy_slots if s.end > s.start]

    slots: list[SlotPublic] = []
    cursor = available
    for busy in busy_slots:
        if busy.end <= cursor:
            # Entirely in the past relative to bookable window — still show busy
            slots.append(busy)
            continue
        if busy.start > cursor:
            slots.append(SlotPublic(start=cursor, end=busy.start, status=SlotStatus.free))
        # Clip busy display start for ordering; keep full busy interval in list
        slots.append(busy)
        cursor = max(cursor, busy.end)
    if cursor < day_end:
        slots.append(SlotPublic(start=cursor, end=day_end, status=SlotStatus.free))

    return [s for s in slots if s.end > s.start]


class RoomService:
    def __init__(self, session: AsyncSession) -> None:
        self.rooms = RoomRepository(session)
        self.bookings = BookingRepository(session)

    async def list_rooms(self):
        return await self.rooms.list_all()

    async def get_slots(self, room_id: int, day: date) -> SlotsResponse:
        room = await self.rooms.get_by_id(room_id)
        if room is None:
            raise NotFoundError("Комната не найдена")

        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        bookings = await self.bookings.list_for_room_on_day(room_id, day_start, day_end)
        now = datetime.now(UTC)

        busy_intervals = [
            (booking.during.lower, booking.during.upper) for booking in bookings
        ]
        slots = build_day_slots(day_start, day_end, busy_intervals, now=now)
        return SlotsResponse(room_id=room_id, date=day.isoformat(), slots=slots)


class BookingService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.rooms = RoomRepository(session)
        self.bookings = BookingRepository(session)

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
        return start, end

    async def create(self, payload_room_id: int, start: datetime, end: datetime, user: TelegramUser) -> BookingOut:
        start, end = self.validate_window(start, end)
        room = await self.rooms.get_by_id(payload_room_id)
        if room is None:
            raise NotFoundError("Комната не найдена")

        try:
            booking = await self.bookings.create(
                room_id=payload_room_id,
                telegram_id=user.id,
                user_display_name=user.display_name,
                start=start,
                end=end,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Слот только что заняли") from exc

        # Reload with room for notification formatting
        booking = await self.bookings.get_by_id(booking.id)
        assert booking is not None
        return booking_to_out(booking)

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
        return booking_to_out(booking)
