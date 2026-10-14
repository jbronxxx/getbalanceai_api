"""Централизованный модуль распределенного кэширования Redis.

Предоставляет единый пул соединений, управление жизненным циклом (инициализация и закрытие в lifespan),
а также типобезопасные билдеры ключей кэша для всех сервисов приложения.
"""

import uuid
from typing import Optional

from redis import asyncio as aioredis

from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# Константы и билдеры ключей Redis
# ============================================================================

REDIS_KEY_CURRENCY_RATES = "currency_rates:USD"
REDIS_KEY_CURRENCY_RATES_LAST_SUCCESSFUL = "currency_rates:last_successful:USD"


def get_budgets_version_key(user_id: uuid.UUID | str) -> str:
    """Сформировать ключ версии ETag кэша бюджетов пользователя."""
    return f"budgets_version:{user_id}"


def get_insights_cache_key(
    user_id: uuid.UUID | str,
    locale: str = "ru",
    currency: str = "USD",
    month: int | None = None,
    year: int | None = None,
) -> str:
    """Сформировать ключ кэшированных рекомендаций AI-инсайтов пользователя."""
    if month is not None and year is not None:
        return f"insights_cache:{user_id}:{locale}:{currency}:{month}:{year}"
    return f"insights_cache:{user_id}:{locale}:{currency}"


def get_insights_count_key(
    user_id: uuid.UUID | str,
    locale: str = "ru",
    currency: str = "USD",
    month: int | None = None,
    year: int | None = None,
) -> str:
    """Сформировать ключ счетчика транзакций пользователя для валидации кэша инсайтов."""
    if month is not None and year is not None:
        return f"insights_tx_count:{user_id}:{locale}:{currency}:{month}:{year}"
    return f"insights_tx_count:{user_id}:{locale}:{currency}"


def get_insights_daily_quota_key(user_id: uuid.UUID | str, date_str: str) -> str:
    """Сформировать ключ суточного счетчика вызовов LLM для пользователя."""
    return f"insights_daily_calls:{user_id}:{date_str}"


def get_insights_user_patterns(user_id: uuid.UUID | str) -> tuple[str, ...]:
    """Сформировать кортеж масок для неблокирующей инвалидации всех ключей инсайтов пользователя."""
    return (
        f"insights_cache:{user_id}*",
        f"insights_tx_count:{user_id}*",
        f"insights_*:{user_id}*",
    )


def get_revoked_jti_key(jti: str) -> str:
    """Сформировать ключ для отозванного JWT ID (jti)."""
    return f"revoked_jti:{jti}"


# ============================================================================
# Управление клиентом и соединением Redis
# ============================================================================

_redis_client: Optional[aioredis.Redis] = None


def get_redis() -> aioredis.Redis:
    """Получить или лениво инициализировать единый клиент распределенного кэша Redis."""
    global _redis_client, redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(config.redis_url, decode_responses=True)
        redis_client = _redis_client
    return _redis_client


async def close_redis() -> None:
    """Корректно закрыть пул соединений клиента Redis при завершении работы приложения."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Пул соединений Redis успешно закрыт")
        except Exception as err:
            logger.warning("Ошибка при закрытии соединений Redis: %s", err)
        finally:
            _redis_client = None


# Экземпляр клиента для обратной совместимости с модулями и тестами
redis_client: aioredis.Redis = get_redis()
