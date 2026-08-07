from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.security import TelegramUser, require_telegram_user
from backend.models import User, UserRole
from backend.repositories import UserRepository

ACCESS_DENIED = "Обратитесь к администратору офиса для доступа"
ADMIN_ONLY = "Команда доступна только администратору"


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    telegram: TelegramUser
    db_user: User

    @property
    def id(self) -> int:
        return self.telegram.id

    @property
    def display_name(self) -> str:
        return self.telegram.display_name

    @property
    def is_admin(self) -> bool:
        return self.db_user.role == UserRole.admin


async def require_registered_user(
    user: TelegramUser = Depends(require_telegram_user),
    db: AsyncSession = Depends(get_db),
) -> RegisteredUser:
    db_user = await UserRepository(db).get_by_telegram_id(user.id)
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ACCESS_DENIED)
    return RegisteredUser(telegram=user, db_user=db_user)


async def require_admin_user(
    registered: RegisteredUser = Depends(require_registered_user),
) -> RegisteredUser:
    if not registered.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ADMIN_ONLY)
    return registered
