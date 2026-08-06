from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    capacity: int
    photo_url: str
    description: str


class SlotStatus(str, Enum):
    free = "free"
    busy = "busy"
    soon_free = "soon_free"


class SlotPublic(BaseModel):
    """Public schedule slot — never include booking owner PII."""

    start: datetime
    end: datetime
    status: SlotStatus


class SlotsResponse(BaseModel):
    room_id: int
    date: str
    slots: list[SlotPublic]


class BookingCreate(BaseModel):
    room_id: int = Field(..., gt=0)
    start: datetime
    end: datetime


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    room_id: int
    room_name: str | None = None
    user_display_name: str
    start: datetime
    end: datetime
    canceled: bool
    created_at: datetime


class MessageOut(BaseModel):
    detail: str


class BookingConfigOut(BaseModel):
    """Public booking rules — safe to expose without auth."""

    office_timezone: str
    office_hours_start: int
    office_hours_end: int
    min_duration_minutes: int
    max_duration_minutes: int
    slot_step_minutes: int
    soon_free_minutes: int
