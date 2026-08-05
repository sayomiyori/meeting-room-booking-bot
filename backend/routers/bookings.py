from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.exceptions import AppError, ConflictError
from backend.core.security import TelegramUser, require_telegram_user
from backend.schemas import BookingCreate, BookingOut, MessageOut
from backend.services.booking import BookingService
from backend.services.notifications import notify_booking_conflict, notify_booking_created

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def _telegram_rate_key(request: Request) -> str:
    # Prefer authenticated id when present in debug header; fallback IP
    debug_id = request.headers.get("X-Debug-Telegram-Id")
    if debug_id:
        return f"tg:{debug_id}"
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if "user=" in init_data:
        return f"init:{hash(init_data) % 10_000_000}"
    return get_remote_address(request)


@router.post("", response_model=BookingOut, status_code=201)
@limiter.limit("5/minute", key_func=_telegram_rate_key)
async def create_booking(
    request: Request,
    body: BookingCreate,
    db: AsyncSession = Depends(get_db),
    user: TelegramUser = Depends(require_telegram_user),
) -> BookingOut:
    service = BookingService(db)
    try:
        booking = await service.create(body.room_id, body.start, body.end, user)
    except ConflictError as exc:
        await notify_booking_conflict(user.id)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    await notify_booking_created(user.id, booking)
    return booking


@router.get("/my", response_model=list[BookingOut])
@limiter.limit("30/minute", key_func=_telegram_rate_key)
async def my_bookings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: TelegramUser = Depends(require_telegram_user),
) -> list[BookingOut]:
    try:
        return await BookingService(db).my_bookings(user)
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{booking_id}/cancel", response_model=BookingOut)
@limiter.limit("10/minute", key_func=_telegram_rate_key)
async def cancel_booking(
    request: Request,
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    user: TelegramUser = Depends(require_telegram_user),
) -> BookingOut:
    try:
        return await BookingService(db).cancel(booking_id, user)
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
