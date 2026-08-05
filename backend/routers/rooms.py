from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.exceptions import AppError
from backend.schemas import RoomOut, SlotsResponse
from backend.services.booking import RoomService

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.get("", response_model=list[RoomOut])
async def list_rooms(db: AsyncSession = Depends(get_db)) -> list[RoomOut]:
    rooms = await RoomService(db).list_rooms()
    return [RoomOut.model_validate(r) for r in rooms]


@router.get("/{room_id}/slots", response_model=SlotsResponse)
async def room_slots(
    room_id: int,
    day: date = Query(..., alias="date", description="YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
) -> SlotsResponse:
    try:
        return await RoomService(db).get_slots(room_id, day)
    except AppError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
