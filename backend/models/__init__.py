from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint, TSTZRANGE, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base


class UserRole(StrEnum):
    member = "member"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'member'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    photo_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    bookings: Mapped[list["Booking"]] = relationship(back_populates="room")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        ExcludeConstraint(
            ("room_id", "="),
            ("during", "&&"),
            where=text("NOT canceled"),
            using="gist",
            name="bookings_room_during_excl",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_display_name: Mapped[str] = mapped_column(Text, nullable=False)
    during: Mapped[Any] = mapped_column(TSTZRANGE, nullable=False)
    canceled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reminder_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    checked_in: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    checkin_prompt_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    auto_canceled_notified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    recurring_group_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    room: Mapped[Room] = relationship(back_populates="bookings")
