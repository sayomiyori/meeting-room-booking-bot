from datetime import UTC, datetime

from sqlalchemy import Select, not_, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import Booking, Room


class RoomRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Room]:
        result = await self.session.execute(select(Room).order_by(Room.id))
        return list(result.scalars().all())

    async def get_by_id(self, room_id: int) -> Room | None:
        return await self.session.get(Room, room_id)


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
    ) -> Booking:
        booking = Booking(
            room_id=room_id,
            telegram_id=telegram_id,
            user_display_name=user_display_name,
            during=Range(start, end, bounds="[)"),
            canceled=False,
            reminder_sent=False,
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
