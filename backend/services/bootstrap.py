import structlog

from backend.core.config import get_settings
from backend.core.database import async_session_factory
from backend.models import UserRole
from backend.repositories import UserRepository

logger = structlog.get_logger(__name__)


async def bootstrap_admin_user() -> None:
    """If users table is empty and BOOTSTRAP_ADMIN_TELEGRAM_ID is set, create admin."""
    settings = get_settings()
    admin_id = settings.bootstrap_admin_telegram_id
    if admin_id is None:
        logger.info("bootstrap_admin_skipped", reason="BOOTSTRAP_ADMIN_TELEGRAM_ID not set")
        return

    async with async_session_factory() as session:
        repo = UserRepository(session)
        if await repo.count() > 0:
            return
        await repo.create(
            telegram_id=admin_id,
            display_name="Admin",
            role=UserRole.admin,
        )
        await session.commit()
        logger.info("bootstrap_admin_created", telegram_id=admin_id)

