from fastapi import APIRouter
from sqlalchemy import text
from starlette.responses import JSONResponse

from backend.core.database import async_session_factory

router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
async def health():
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": "error"},
        )
