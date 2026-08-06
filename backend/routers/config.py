from fastapi import APIRouter

from backend.core.config import get_settings
from backend.schemas import BookingConfigOut

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", response_model=BookingConfigOut)
async def booking_config() -> BookingConfigOut:
    s = get_settings()
    return BookingConfigOut(
        office_timezone=s.office_timezone,
        office_hours_start=s.office_hours_start,
        office_hours_end=s.office_hours_end,
        min_duration_minutes=s.min_duration_minutes,
        max_duration_minutes=s.max_duration_minutes,
        slot_step_minutes=s.slot_step_minutes,
        soon_free_minutes=s.soon_free_minutes,
    )
