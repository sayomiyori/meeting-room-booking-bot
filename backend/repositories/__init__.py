from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, func, not_, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import Booking, Room, User, UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count(self) -> int:
        total = await self.session.scalar(select(func.count()).select_from(User))
        return int(total or 0)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        telegram_id: int,
        display_name: str = "",
        role: UserRole | str = UserRole.member,
    ) -> User:
        user = User(
            telegram_id=telegram_id,
            display_name=display_name,
            role=str(role),
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user


class RoomRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self, *, active_only: bool = True) -> list[Room]:
        stmt = select(Room).order_by(Room.id)
        if active_only:
            stmt = stmt.where(Room.active.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, room_id: int, *, active_only: bool = False) -> Room | None:
        room = await self.session.get(Room, room_id)
        if room is None:
            return None
        if active_only and not room.active:
            return None
        return room

    async def create(
        self,
        *,
        name: str,
        capacity: int,
        description: str = "",
        photo_url: str = "",
    ) -> Room:
        room = Room(
            name=name,
            capacity=capacity,
            description=description,
            photo_url=photo_url,
            active=True,
        )
        self.session.add(room)
        await self.session.flush()
        await self.session.refresh(room)
        return room

    async def update_fields(
        self,
        room: Room,
        *,
        name: str | None = None,
        capacity: int | None = None,
        description: str | None = None,
    ) -> Room:
        if name is not None:
            room.name = name
        if capacity is not None:
            room.capacity = capacity
        if description is not None:
            room.description = description
        await self.session.flush()
        await self.session.refresh(room)
        return room

    async def set_active(self, room: Room, active: bool) -> Room:
        room.active = active
        await self.session.flush()
        await self.session.refresh(room)
        return room


class BookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        room_id: int,
        telegram_id: int,
        user_display_name: str,
        start: datetime,
        end: datetime,
        recurring_group_id: UUID | None = None,
    ) -> Booking:
        booking = Booking(
            room_id=room_id,
            telegram_id=telegram_id,
            user_display_name=user_display_name,
            during=Range(start, end, bounds="[)"),
            canceled=False,
            reminder_sent=False,
            checked_in=False,
            checkin_prompt_sent=False,
            auto_canceled_notified=False,
            recurring_group_id=recurring_group_id,
        )
        self.session.add(booking)
        await self.session.flush()
        await self.session.refresh(booking)
        return booking

    async def get_by_id(self, booking_id: int) -> Booking | None:
        result = await self.session.execute(
            select(Booking).options(selectinload(Booking.room)).where(Booking.id == booking_id)
        )
        return result.scalar_one_or_none()

    async def list_active_for_user(self, telegram_id: int) -> list[Booking]:
        now = datetime.now(UTC)
        stmt: Select[tuple[Booking]] = (
            select(Booking)
            .options(selectinload(Booking.room))
            .where(
                Booking.telegram_id == telegram_id,
                not_(Booking.canceled),
                text("upper(during) > :now").bindparams(now=now),
            )
            .order_by(text("lower(during)"))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_room_on_day(
        self,
        room_id: int,
        day_start: datetime,
        day_end: datetime,
    ) -> list[Booking]:
        day_range = Range(day_start, day_end, bounds="[)")
        stmt = (
            select(Booking)
            .where(
                Booking.room_id == room_id,
                not_(Booking.canceled),
                Booking.during.op("&&")(day_range),
            )
            .order_by(text("lower(during)"))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def cancel(self, booking: Booking) -> Booking:
        booking.canceled = True
        await self.session.flush()
        await self.session.refresh(booking)
        return booking

    async def cancel_future_in_group(
        self,
        *,
        group_id: UUID,
        telegram_id: int,
        now: datetime | None = None,
    ) -> list[Booking]:
        now = now or datetime.now(UTC)
        stmt = (
            select(Booking)
            .options(selectinload(Booking.room))
            .where(
                Booking.recurring_group_id == group_id,
                Booking.telegram_id == telegram_id,
                not_(Booking.canceled),
                text("lower(during) > :now").bindparams(now=now),
            )
            .order_by(text("lower(during)"))
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        for booking in rows:
            booking.canceled = True
        await self.session.flush()
        return rows

    async def list_due_reminders(
        self,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Booking]:
        stmt = (
            select(Booking)
            .options(selectinload(Booking.room))
            .where(
                not_(Booking.canceled),
                not_(Booking.reminder_sent),
                text("lower(during) >= :ws AND lower(during) <= :we").bindparams(
                    ws=window_start,
                    we=window_end,
                ),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_reminder_sent(self, booking_id: int) -> None:
        await self.session.execute(
            update(Booking).where(Booking.id == booking_id).values(reminder_sent=True)
        )
        await self.session.flush()

    async def list_due_checkin_prompts(
        self,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Booking]:
        stmt = (
            select(Booking)
            .options(selectinload(Booking.room))
            .where(
                not_(Booking.canceled),
                not_(Booking.checkin_prompt_sent),
                text("lower(during) >= :ws AND lower(during) <= :we").bindparams(
                    ws=window_start,
                    we=window_end,
                ),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_checkin_prompt_sent(self, booking_id: int) -> None:
        await self.session.execute(
            update(Booking)
            .where(Booking.id == booking_id)
            .values(checkin_prompt_sent=True)
        )
        await self.session.flush()

    async def mark_checked_in(self, booking: Booking) -> Booking:
        booking.checked_in = True
        await self.session.flush()
        await self.session.refresh(booking)
        return booking

    async def list_noshow_candidates(
        self,
        *,
        deadline: datetime,
        min_duration_minutes: int,
    ) -> list[Booking]:
        """Bookings that started at/before deadline, no check-in, long enough for no-show."""
        stmt = (
            select(Booking)
            .options(selectinload(Booking.room))
            .where(
                not_(Booking.canceled),
                not_(Booking.checked_in),
                not_(Booking.auto_canceled_notified),
                text("lower(during) <= :deadline").bindparams(deadline=deadline),
                text(
                    "(EXTRACT(EPOCH FROM (upper(during) - lower(during))) / 60.0) > :min_dur"
                ).bindparams(min_dur=float(min_duration_minutes)),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_auto_canceled(self, booking: Booking) -> Booking:
        booking.canceled = True
        booking.auto_canceled_notified = True
        await self.session.flush()
        await self.session.refresh(booking)
        return booking
