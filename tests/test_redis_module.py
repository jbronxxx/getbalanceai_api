"""Модульные тесты централизованного модуля app.redis."""

import uuid

import pytest

from app.redis import (
    REDIS_KEY_CURRENCY_RATES,
    REDIS_KEY_CURRENCY_RATES_LAST_SUCCESSFUL,
    close_redis,
    get_budgets_version_key,
    get_insights_cache_key,
    get_insights_daily_quota_key,
    get_insights_user_patterns,
    get_redis,
)


def test_redis_key_builders():
    """Проверка генерации ключей Redis для бюджетов, аналитики и суточных квот."""
    test_id = uuid.uuid4()

    # 1. Ключ версии бюджетов
    budgets_key = get_budgets_version_key(test_id)
    assert budgets_key == f"budgets_version:{test_id}"

    # 2. Ключи кэша инсайтов без указания периода
    cache_key = get_insights_cache_key(test_id, locale="ru", currency="UZS")
    assert cache_key == f"insights_cache:{test_id}:ru:UZS"

    # 3. Ключи кэша инсайтов с учетом месяца и года
    cache_key_period = get_insights_cache_key(test_id, locale="kk", currency="KZT", month=10, year=2026)
    assert cache_key_period == f"insights_cache:{test_id}:kk:KZT:10:2026"

    # 4. Ключ суточного счетчика обращений к LLM
    quota_key = get_insights_daily_quota_key(test_id, "2026-10-14")
    assert quota_key == f"insights_daily_calls:{test_id}:2026-10-14"

    # 5. Шаблоны для неблокирующей очистки
    patterns = get_insights_user_patterns(test_id)
    assert len(patterns) == 3
    assert f"insights_cache:{test_id}*" in patterns
    assert f"insights_tx_count:{test_id}*" in patterns
    assert f"insights_*:{test_id}*" in patterns

    # 6. Константы котировок
    assert REDIS_KEY_CURRENCY_RATES == "currency_rates:USD"
    assert REDIS_KEY_CURRENCY_RATES_LAST_SUCCESSFUL == "currency_rates:last_successful:USD"


@pytest.mark.asyncio
async def test_get_redis_and_close():
    """Проверка получения инстанса Redis и его безопасного закрытия."""
    client = get_redis()
    assert client is not None

    # Повторный вызов возвращает тот же инстанс
    client2 = get_redis()
    assert client is client2

    # Безопасное закрытие клиента
    await close_redis()

    # После закрытия get_redis() инициализирует рабочий клиент
    reopened = get_redis()
    assert reopened is not None
