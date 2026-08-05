from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import json
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, status

from backend.core.config import get_settings


@dataclass(frozen=True, slots=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str | None = None

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        name = " ".join(p for p in parts if p).strip()
        if name:
            return name
        if self.username:
            return f"@{self.username}"
        return f"User {self.id}"


def _build_data_check_string(pairs: list[tuple[str, str]]) -> str:
    filtered = [(k, v) for k, v in pairs if k != "hash"]
    filtered.sort(key=lambda item: item[0])
    return "\n".join(f"{k}={v}" for k, v in filtered)


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> TelegramUser:
    if not init_data or not init_data.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Откройте приложение через кнопку бота",
        )

    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.get("hash")
    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректные данные авторизации Telegram",
        )

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), sha256).digest()
    check_string = _build_data_check_string(pairs)
    calculated = hmac.new(secret_key, check_string.encode(), sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректная подпись initData",
        )

    auth_date_raw = data.get("auth_date")
    if not auth_date_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует auth_date",
        )
    try:
        auth_date = datetime.fromtimestamp(int(auth_date_raw), tz=UTC)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный auth_date",
        ) from exc

    age = (datetime.now(UTC) - auth_date).total_seconds()
    if age > max_age_seconds or age < -60:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия Telegram истекла. Откройте Mini App заново",
        )

    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="В initData нет данных пользователя",
        )
    try:
        user_obj = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный user в initData",
        ) from exc

    user_id = user_obj.get("id")
    if not isinstance(user_id, int):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный user.id",
        )

    return TelegramUser(
        id=user_id,
        first_name=str(user_obj.get("first_name") or ""),
        last_name=str(user_obj.get("last_name") or ""),
        username=user_obj.get("username"),
    )


async def require_telegram_user(
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
    x_debug_telegram_id: int | None = Header(default=None, alias="X-Debug-Telegram-Id"),
) -> TelegramUser:
    settings = get_settings()

    # Mock only when DEBUG=true — production validation path always active otherwise
    if settings.debug and x_debug_telegram_id is not None and not x_telegram_init_data:
        return TelegramUser(
            id=x_debug_telegram_id,
            first_name="Debug",
            last_name="User",
            username="debug_user",
        )

    if not settings.bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BOT_TOKEN не настроен",
        )

    return validate_init_data(
        init_data=x_telegram_init_data or "",
        bot_token=settings.bot_token,
        max_age_seconds=settings.initdata_max_age_seconds,
    )
