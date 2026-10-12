"""Сервис получения и кэширования справочника котировок валют."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from redis import asyncio as aioredis

from app.exceptions import InvalidCurrencyException
from app.models.models import SUPPORTED_CURRENCIES, CurrencyCode
from app.schemas.schemas import CurrencyRatesResponse
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Резервная таблица курсов к USD на случай сбоя внешнего шлюза или отсутствия сети
FALLBACK_RATES: dict[str, Decimal] = {
    CurrencyCode.USD.value: Decimal("1.0"),
    CurrencyCode.EUR.value: Decimal("0.92"),
    CurrencyCode.RUB.value: Decimal("96.0"),
    CurrencyCode.UZS.value: Decimal("12800.0"),
    CurrencyCode.KZT.value: Decimal("495.0"),
}

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

    @classmethod
    def calculate_cross_rate(
        cls,
        from_curr: str,
        to_curr: str,
        rates: dict[str, Decimal] | None = None,
    ) -> Decimal:
        """Рассчитать кросс-курс конвертации между двумя валютами.

        Таблица котировок привязана к USD (1 USD = rate).
        Кросс-курс from_curr -> to_curr рассчитывается как: to_usd_rate / from_usd_rate.

        Аргументы:
            from_curr (str): Исходная валюта (например, 'USD', 'UZS', 'RUB').
            to_curr (str): Целевая валюта.
            rates (dict[str, Decimal] | None): Словарь курсов валют к USD.

        Возвращает:
            Decimal: Коэффициент конвертации.

        Исключения:
            InvalidCurrencyException: Если передан неизвестный код валюты.
        """
        from_code = from_curr.strip().upper()
        to_code = to_curr.strip().upper()

        if from_code not in SUPPORTED_CURRENCIES:
            raise InvalidCurrencyException(f"Неподдерживаемая исходная валюта: '{from_curr}'")
        if to_code not in SUPPORTED_CURRENCIES:
            raise InvalidCurrencyException(f"Неподдерживаемая целевая валюта: '{to_curr}'")

        if from_code == to_code:
            return Decimal("1.0")

        rates_dict = rates if rates is not None else FALLBACK_RATES
        from_usd_rate = rates_dict.get(from_code) or FALLBACK_RATES.get(from_code)
        to_usd_rate = rates_dict.get(to_code) or FALLBACK_RATES.get(to_code)

        if not from_usd_rate or from_usd_rate <= Decimal("0"):
            raise InvalidCurrencyException(f"Не удалось определить курс для валюты '{from_code}'")
        if not to_usd_rate or to_usd_rate <= Decimal("0"):
            raise InvalidCurrencyException(f"Не удалось определить курс для валюты '{to_code}'")

        return to_usd_rate / from_usd_rate

    @classmethod
    def resolve_amounts(
        cls,
        amount: Decimal,
        currency: str | None,
        base_currency: str,
        client_rate: Decimal | None = None,
        client_base_amount: Decimal | None = None,
        rates: dict[str, Decimal] | None = None,
        max_tolerance_percent: float | Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Рассчитать согласованные курс (exchange_rate) и базовую сумму (base_amount).

        Обеспечивает единую точку правды для конвертации валют:
        - Если валюта совпадает с базовой: rate = 1.0, base_amount = amount.
          Клиентские base_amount и rate игнорируются во избежание искажения данных.
        - Если валюта отличается: рассчитывается серверный кросс-курс.
          Клиентский курс принимается только в случае, если его отклонение
          от серверного не превышает установленный порог tolerance.
          Иначе применяется серверный курс с предупреждением в лог.
        - Базовая сумма всегда вычисляется как (amount * rate), квантованная до 0.01.

        Аргументы:
            amount (Decimal): Сумма операции в валюте чека.
            currency (str | None): Валюта операции (если None, считается базовой).
            base_currency (str): Базовая валюта учета пользователя.
            client_rate (Decimal | None): Курс, переданный клиентом.
            client_base_amount (Decimal | None): Сумма в базовой валюте от клиента (для сверки).
            rates (dict[str, Decimal] | None): Актуальные курсы валют к USD.
            max_tolerance_percent (float | Decimal | None): Допустимый процент отклонения курса.

        Возвращает:
            tuple[Decimal, Decimal]: (exchange_rate, base_amount)

        Исключения:
            InvalidCurrencyException: Если валюта операции или базовая валюта не поддерживается.
        """
        curr = (currency or base_currency).strip().upper()
        base_curr = base_currency.strip().upper()

        if curr not in SUPPORTED_CURRENCIES:
            raise InvalidCurrencyException(f"Неподдерживаемая валюта транзакции: '{currency}'")
        if base_curr not in SUPPORTED_CURRENCIES:
            raise InvalidCurrencyException(f"Неподдерживаемая базовая валюта пользователя: '{base_currency}'")

        quantized_amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 1. Если валюта совпадает с базовой — строгий курс 1.0 и равенство сумм
        if curr == base_curr:
            return Decimal("1.0"), quantized_amount

        # 2. Мультивалютная операция: расчет серверного кросс-курса
        server_rate = cls.calculate_cross_rate(curr, base_curr, rates)

        # 3. Валидация клиентского курса против серверного
        tolerance_pct = (
            Decimal(str(max_tolerance_percent))
            if max_tolerance_percent is not None
            else Decimal(str(config.client_rate_tolerance_percent))
        )
        tolerance_ratio = tolerance_pct / Decimal("100")

        use_client_rate = False
        if client_rate is not None and client_rate > Decimal("0"):
            deviation = abs(client_rate - server_rate) / server_rate
            if deviation <= tolerance_ratio:
                use_client_rate = True
            else:
                logger.warning(
                    f"Клиентский курс {client_rate} для {curr}->{base_curr} отклоняется от серверного "
                    f"{server_rate:.6f} на {deviation * 100:.2f}% (порог {tolerance_pct}%). "
                    "Применен серверный курс котировок."
                )

        calc_rate = client_rate if use_client_rate else server_rate
        calculated_base_amount = (quantized_amount * calc_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        effective_rate = calc_rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

        if client_base_amount is not None:
            quantized_client_base = client_base_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            diff = abs(quantized_client_base - calculated_base_amount)
            if diff > Decimal("0.05"):
                logger.warning(
                    f"Клиентская базовая сумма {quantized_client_base} расходится с расчетной "
                    f"{calculated_base_amount} (разница {diff}). Использовано расчетное значение."
                )

        return effective_rate, calculated_base_amount

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
