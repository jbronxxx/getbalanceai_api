"""Сервис получения и кэширования справочника котировок валют."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from redis import asyncio as aioredis

from app.schemas.schemas import CurrencyRatesResponse
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Резервная таблица курсов к USD на случай сбоя внешнего шлюза или отсутствия сети
FALLBACK_RATES: dict[str, Decimal] = {
    "USD": Decimal("1.0"),
    "EUR": Decimal("0.92"),
    "RUB": Decimal("96.0"),
    "UZS": Decimal("12800.0"),
    "KZT": Decimal("495.0"),
}

SUPPORTED_CURRENCIES = ("USD", "EUR", "RUB", "UZS", "KZT")
CACHE_TTL_SECONDS = config.currency_rates_cache_ttl
REDIS_CACHE_KEY = "currency_rates:USD"

redis_client = aioredis.from_url(config.redis_url, decode_responses=True)
_fetch_lock = asyncio.Lock()
_mem_rates: dict[str, Decimal] = dict(FALLBACK_RATES)
_mem_updated_at: datetime = datetime.now(timezone.utc)


class CurrencyService:
    """Сервис для предоставления актуальных валютных котировок с кэшированием в Redis."""

    def __init__(self) -> None:
        """Инициализация сервиса валют."""
        self.gateway_url = config.currency_rates_url
        self.cache_ttl = config.currency_rates_cache_ttl

    async def get_rates(self) -> CurrencyRatesResponse:
        """Получить актуальные курсы валют из Redis, внешнего шлюза или резервной таблицы.

        Возвращает:
            CurrencyRatesResponse: Справочник курсов валют к базовой валюте USD.
        """
        # 1. Попытка чтения из Redis
        cached_response = await self._read_from_redis()
        if cached_response:
            return cached_response

        # 2. Защита от Thundering Herd (состояния гонки) при протухании кэша
        async with _fetch_lock:
            # Повторная проверка (double-checked locking)
            cached_response = await self._read_from_redis()
            if cached_response:
                return cached_response

            # 3. Запрос к внешнему сетевому шлюзу
            fetched_rates = await self._fetch_rates_from_gateway()
            now = datetime.now(timezone.utc)

            if fetched_rates:
                global _mem_updated_at
                _mem_rates.update(fetched_rates)
                _mem_updated_at = now
                response = CurrencyRatesResponse(base="USD", rates=_mem_rates, updated_at=_mem_updated_at)
                await self._write_to_redis(response)
                return response

        # 4. Fallback на память процесса или резервную таблицу
        logger.warning("Используется резервная таблица котировок валют")
        return CurrencyRatesResponse(base="USD", rates=_mem_rates, updated_at=_mem_updated_at)

    async def _fetch_rates_from_gateway(self) -> dict[str, Decimal] | None:
        """Выполнить асинхронный HTTP-запрос к внешнему API курсов валют."""
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(
                    self.gateway_url,
                    headers={"User-Agent": "GetBalanceAI-Backend/1.0"},
                )
                if res.status_code == 200:
                    data = res.json()
                    raw_rates = data.get("rates", {})
                    extracted: dict[str, Decimal] = {}
                    for code in SUPPORTED_CURRENCIES:
                        if code in raw_rates:
                            extracted[code] = Decimal(str(raw_rates[code]))
                    if len(extracted) == len(SUPPORTED_CURRENCIES):
                        logger.info("Котировки валют успешно загружены из внешнего шлюза")
                        return extracted
        except Exception as e:
            logger.warning(f"Не удалось получить курсы валют из внешнего шлюза {self.gateway_url}: {e}")
        return None

    async def _read_from_redis(self) -> CurrencyRatesResponse | None:
        """Прочитать кэшированные курсы из Redis."""
        try:
            cached_json = await redis_client.get(REDIS_CACHE_KEY)
            if cached_json:
                data = json.loads(cached_json)
                rates = {k: Decimal(str(v)) for k, v in data["rates"].items()}
                updated_at = datetime.fromisoformat(data["updated_at"])
                return CurrencyRatesResponse(base=data.get("base", "USD"), rates=rates, updated_at=updated_at)
        except Exception as err:
            logger.warning(f"Ошибка чтения кэша котировок из Redis: {err}")
        return None

    async def _write_to_redis(self, response: CurrencyRatesResponse) -> None:
        """Сохранить курсы валют в Redis с установленным TTL."""
        try:
            payload = {
                "base": response.base,
                "rates": {k: str(v) for k, v in response.rates.items()},
                "updated_at": response.updated_at.isoformat(),
            }
            await redis_client.setex(REDIS_CACHE_KEY, self.cache_ttl, json.dumps(payload))
            logger.info("Кэш котировок валют успешно сохранен в Redis")
        except Exception as err:
            logger.warning(f"Не удалось сохранить кэш котировок в Redis: {err}")
