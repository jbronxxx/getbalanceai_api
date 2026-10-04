"""Фоновые задачи и периодическая регламентная очистка устаревших токенов."""

import asyncio
from typing import Any

from app.database import UnitOfWork, async_session_maker
from app.services.auth_service import AuthService
from logger.logger import get_logger

logger = get_logger(__name__)


async def run_token_cleanup(retention_days: int = 30, session_factory: Any = async_session_maker) -> int:
    """Выполнить регламентную очистку истекших и отозванных токенов в рамках UnitOfWork."""
    logger.info(f"Запуск регламентной очистки токенов старше {retention_days} дней...")
    async with UnitOfWork(session_factory=session_factory) as session:
        service = AuthService(session)
        deleted_count = await service.cleanup_expired_tokens(retention_days=retention_days)
        logger.info(f"Регламентная очистка токенов завершена: удалено {deleted_count} записей")
        return deleted_count
    return 0


async def periodic_token_cleanup(
    interval_seconds: int = 86400,
    retention_days: int = 30,
    session_factory: Any = async_session_maker,
) -> None:
    """Асинхронная периодическая фоновая задача для автоматической очистки токенов."""
    logger.info(
        f"Инициализирован планировщик очистки токенов (интервал: {interval_seconds}c, "
        f"срок хранения: {retention_days} дн.)"
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await run_token_cleanup(retention_days=retention_days, session_factory=session_factory)
        except asyncio.CancelledError:
            logger.info("Фоновая задача очистки токенов успешно остановлена (Cancelled)")
            break
        except Exception as e:
            logger.error(
                f"Непредвиденная ошибка в периодической задаче очистки токенов: {e}",
                exc_info=True,
            )
