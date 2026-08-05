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

        # Public DTO only — never expose owner PII
        busy_slots: list[SlotPublic] = []
        for booking in bookings:
            start = max(booking.during.lower, day_start)
            end = min(booking.during.upper, day_end)
            if end <= start:
                continue
            remaining = (booking.during.upper - now).total_seconds()
            status = SlotStatus.busy
            if remaining > 0 and remaining <= 30 * 60:
                status = SlotStatus.soon_free
            busy_slots.append(SlotPublic(start=start, end=end, status=status))

        # Fill free gaps for the calendar day (00:00–24:00 UTC of selected date)
        slots: list[SlotPublic] = []
        cursor = day_start
        for busy in busy_slots:
            if busy.start > cursor:
                slots.append(SlotPublic(start=cursor, end=busy.start, status=SlotStatus.free))
            slots.append(busy)
            cursor = max(cursor, busy.end)
        if cursor < day_end:
            slots.append(SlotPublic(start=cursor, end=day_end, status=SlotStatus.free))

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
