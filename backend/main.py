from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.core.config import get_settings
from backend.routers import bookings, health, rooms
from backend.routers.bookings import limiter
from backend.services.scheduler import start_scheduler, stop_scheduler
from bot import webhook_router

logger = structlog.get_logger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    start_scheduler()

    polling_task = None
    if settings.debug and settings.bot_token and not settings.public_base_url:
        import asyncio

        from backend.services.notifications import get_bot
        from bot import dp

        bot = get_bot()
        polling_task = asyncio.create_task(dp.start_polling(bot))
        logger.info("bot_polling_started")
    elif settings.bot_token and settings.public_base_url and settings.webhook_secret:
        from backend.services.notifications import get_bot

        bot = get_bot()
        webhook_url = f"{settings.public_base_url.rstrip('/')}{settings.webhook_path}"
        await bot.set_webhook(url=webhook_url, secret_token=settings.webhook_secret)
        logger.info("bot_webhook_set", url=webhook_url)
    else:
        logger.warning("bot_not_started", reason="missing token or webhook config")

    yield

    stop_scheduler()
    if polling_task is not None:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if settings.bot_token:
        try:
            from backend.services.notifications import get_bot

            bot = get_bot()
            if settings.public_base_url:
                await bot.delete_webhook(drop_pending_updates=False)
            await bot.session.close()
        except Exception:
            logger.exception("bot_shutdown_error")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Meeting Room Booking",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(_request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "Ошибка валидации", "errors": exc.errors()})

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        logger.exception("unhandled_error", error=str(exc))
        detail = str(exc) if settings.debug else "Внутренняя ошибка сервера"
        return JSONResponse(status_code=500, content={"detail": detail})

    app.include_router(health.router)
    app.include_router(rooms.router)
    app.include_router(bookings.router)
    app.include_router(webhook_router)

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
