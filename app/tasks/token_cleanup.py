"""Фоновые задачи и периодическая регламентная очистка устаревших токенов."""

import asyncio
import os
from typing import Any

from redis.exceptions import RedisError

from app.database import UnitOfWork, async_session_maker
from app.redis import REDIS_KEY_TOKEN_CLEANUP_LOCK, get_redis
from app.services.auth_service import AuthService
from logger.logger import get_logger

logger = get_logger(__name__)


async def run_token_cleanup(retention_days: int = 30, session_factory: Any = async_session_maker) -> int:
    """Выполнить регламентную очистку истекших и отозванных токенов в рамках UnitOfWork."""
    logger.info(f"Запуск регламентной очистки токенов старше {retention_days} дней...")
    async with UnitOfWork(session_factory=session_factory) as session:
        service = AuthService(session)
        return await service.cleanup_expired_tokens(retention_days=retention_days)
    return 0


async def _acquire_cleanup_lock(ttl_seconds: int) -> bool:
    """Занять блокировку очистки на ttl_seconds; False — её держит другой воркер или Redis недоступен."""
    try:
        return bool(await get_redis().set(REDIS_KEY_TOKEN_CLEANUP_LOCK, str(os.getpid()), nx=True, ex=ttl_seconds))
    except RedisError as err:
        logger.warning(f"Очистка токенов пропущена: Redis недоступен для блокировки ({type(err).__name__})")
        return False


async def _release_cleanup_lock() -> None:
    """Снять блокировку после сбоя очистки, чтобы следующая проверка любого воркера повторила её."""
    try:
        await get_redis().delete(REDIS_KEY_TOKEN_CLEANUP_LOCK)
    except RedisError as err:
        logger.warning(f"Не удалось снять блокировку очистки токенов ({type(err).__name__})")


async def periodic_token_cleanup(
    interval_seconds: int = 86400,
    retention_days: int = 30,
    session_factory: Any = async_session_maker,
    check_interval_seconds: float = 3600,
) -> None:
    """Периодическая очистка токенов, выполняемая одним воркером на весь кластер.

    Задача стартует в lifespan каждого воркера gunicorn и раз в check_interval_seconds пытается
    занять блокировку в Redis (SET NX EX) на interval_seconds. Очистку выполняет только воркер,
    занявший блокировку, остальные пропускают проверку. Период отсчитывает TTL блокировки, а не
    таймер процесса, поэтому рестарты при деплое не откладывают очистку.
    """
    logger.info(
        f"Инициализирован планировщик очистки токенов (интервал: {interval_seconds}c, "
        f"проверка: {check_interval_seconds}c, срок хранения: {retention_days} дн.)"
    )
    while True:
        try:
            await asyncio.sleep(check_interval_seconds)
            if not await _acquire_cleanup_lock(interval_seconds):
                logger.debug("Очистка токенов пропущена: блокировка занята")
                continue
            try:
                await run_token_cleanup(retention_days=retention_days, session_factory=session_factory)
            except Exception:
                await _release_cleanup_lock()
                raise
        except asyncio.CancelledError:
            logger.info("Фоновая задача очистки токенов успешно остановлена (Cancelled)")
            break
        except Exception as e:
            logger.error(
                f"Непредвиденная ошибка в периодической задаче очистки токенов: {e}",
                exc_info=True,
            )
