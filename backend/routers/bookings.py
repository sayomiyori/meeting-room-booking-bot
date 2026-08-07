from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import RegisteredUser, require_registered_user
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import AppError, ConflictError
from backend.core.rate_limit import allow_telegram_booking_rate
from backend.schemas import (
    BookingCreate,
    BookingOut,
    RecurringBookingCreate,
    RecurringBookingOut,
)
from backend.services.booking import BookingService
from backend.services.notifications import (
    notify_booking_conflict,
    notify_booking_created,
    notify_recurring_created,
)

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def _telegram_rate_key(request: Request) -> str:
    settings = get_settings()
    if settings.debug:
        debug_id = request.headers.get("X-Debug-Telegram-Id")
        if debug_id:
            return f"tg:{debug_id}"
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if "user=" in init_data:
        return f"init:{hash(init_data) % 10_000_000}"
    return get_remote_address(request)


@router.post("", response_model=BookingOut, status_code=201)
async def create_booking(
    body: BookingCreate,
    db: AsyncSession = Depends(get_db),
    user: RegisteredUser = Depends(require_registered_user),
) -> BookingOut:
    if not allow_telegram_booking_rate(user.id):
        raise HTTPException(status_code=429, detail="Слишком много запросов, подождите минуту")
    service = BookingService(db)
    try:
        booking = await service.create(body.room_id, body.start, body.end, user.telegram)
    except ConflictError as exc:
        await notify_booking_conflict(user.id)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    await notify_booking_created(user.id, booking)
    return booking


@router.post("/recurring", response_model=RecurringBookingOut, status_code=201)
@limiter.limit("10/minute", key_func=_telegram_rate_key)
async def create_recurring_booking(
    request: Request,
    body: RecurringBookingCreate,
    db: AsyncSession = Depends(get_db),
    user: RegisteredUser = Depends(require_registered_user),
) -> RecurringBookingOut:
    if not allow_telegram_booking_rate(user.id):
        raise HTTPException(status_code=429, detail="Слишком много запросов, подождите минуту")
    service = BookingService(db)
    try:
        result = await service.create_recurring(
            body.room_id,
            body.start,
            body.end,
            body.weeks,
            user.telegram,
        )
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    group_id = result.created[0].recurring_group_id if result.created else None
    await notify_recurring_created(
        user.id,
        created_count=len(result.created),
        skipped_count=len(result.skipped),
        group_id=group_id,
    )
    for booking in result.created:
        await notify_booking_created(user.id, booking)
    return result


@router.get("/my", response_model=list[BookingOut])
@limiter.limit("30/minute", key_func=_telegram_rate_key)
async def my_bookings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: RegisteredUser = Depends(require_registered_user),
) -> list[BookingOut]:
    try:
        return await BookingService(db).my_bookings(user.telegram)
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/recurring/{group_id}/cancel", response_model=list[BookingOut])
@limiter.limit("10/minute", key_func=_telegram_rate_key)
async def cancel_recurring_series(
    request: Request,
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: RegisteredUser = Depends(require_registered_user),
) -> list[BookingOut]:
    try:
        return await BookingService(db).cancel_series(group_id, user.telegram)
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{booking_id}/cancel", response_model=BookingOut)
@limiter.limit("10/minute", key_func=_telegram_rate_key)
async def cancel_booking(
    request: Request,
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    user: RegisteredUser = Depends(require_registered_user),
) -> BookingOut:
    try:
        return await BookingService(db).cancel(booking_id, user.telegram)
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
