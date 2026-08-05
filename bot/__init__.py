from aiogram import Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status

from backend.core.config import get_settings
from backend.services.notifications import get_bot
from bot.handlers import router as commands_router

dp = Dispatcher()
dp.include_router(commands_router)

webhook_router = APIRouter(tags=["telegram"])


@webhook_router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> dict[str, bool]:
    settings = get_settings()
    if not settings.webhook_secret or x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret")

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": get_bot()})
    await dp.feed_update(bot=get_bot(), update=update)
    return {"ok": True}
