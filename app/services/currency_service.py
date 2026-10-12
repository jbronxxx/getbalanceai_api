"""Сервис получения и кэширования справочника котировок валют."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app.exceptions import InvalidCurrencyException
from app.models.models import SUPPORTED_CURRENCIES, CurrencyCode
from app.redis import (
    REDIS_KEY_CURRENCY_RATES,
    REDIS_KEY_CURRENCY_RATES_LAST_SUCCESSFUL,
    get_redis,
)
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

# Фиксированная временная метка актуальности базовой резервной таблицы котировок
FALLBACK_RATES_UPDATED_AT: datetime = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)

CACHE_TTL_SECONDS = config.currency_rates_cache_ttl
REDIS_CACHE_KEY = REDIS_KEY_CURRENCY_RATES
REDIS_LAST_SUCCESSFUL_KEY = REDIS_KEY_CURRENCY_RATES_LAST_SUCCESSFUL

# Единый клиент Redis приложения
redis_client = get_redis()


class CurrencyServiceState:
    """Инкапсулированное состояние валютного сервиса в оперативной памяти процесса."""

    def __init__(self) -> None:
        """Инициализация состояния синхронизации и кэша в памяти."""
        self.lock = asyncio.Lock()
        self.mem_rates: dict[str, Decimal] = dict(FALLBACK_RATES)
        self.mem_updated_at: datetime = FALLBACK_RATES_UPDATED_AT
        self.http_client: httpx.AsyncClient | None = None

    def get_http_client(self, timeout: float) -> httpx.AsyncClient:
        """Получить или инициализировать переиспользуемый клиент HTTP."""
        if self.http_client is None or self.http_client.is_closed:
            self.http_client = httpx.AsyncClient(timeout=timeout)
        return self.http_client

    async def close_http_client(self) -> None:
        """Закрыть пул соединений переиспользуемого HTTP-клиента при завершении работы."""
        if self.http_client is not None and not self.http_client.is_closed:
            await self.http_client.aclose()
            self.http_client = None

    def reset(self) -> None:
        """Сбросить кэш памяти к исходной резервной таблице (используется в тестах)."""
        self.mem_rates = dict(FALLBACK_RATES)
        self.mem_updated_at = FALLBACK_RATES_UPDATED_AT


# Синглтон состояния сервиса валют в памяти
currency_state = CurrencyServiceState()


def get_http_client() -> httpx.AsyncClient:
    """Получить или инициализировать переиспользуемый клиент HTTP."""
    return currency_state.get_http_client(config.currency_rates_timeout)


async def close_http_client() -> None:
    """Закрыть пул соединений переиспользуемого HTTP-клиента при завершении работы."""
    await currency_state.close_http_client()


class CurrencyService:
    """Сервис для предоставления актуальных валютных котировок с кэшированием в Redis."""

    def __init__(self) -> None:
        """Инициализация сервиса валют."""
        self.gateway_url = config.currency_rates_url
        self.cache_ttl = config.currency_rates_cache_ttl
        self.fallback_cache_ttl = config.currency_rates_fallback_cache_ttl
        self.timeout = config.currency_rates_timeout

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

        Использует многоуровневое кэширование:
        1. Основной кэш Redis (включая негативный кэш при сбое шлюза).
        2. Защита от состояния гонки (Thundering Herd) через asyncio.Lock.
        3. Запрос к внешнему сетевому шлюзу. При успехе результат кэшируется с основным TTL
           и дублируется в постоянный ключ последних успешных котировок.
        4. Негативный кэш при недоступности шлюза: курсы из последнего успешного источника
           или резервной таблицы кэшируются с коротким TTL (60-300 с) и флагом is_fallback=True.

        Возвращает:
            CurrencyRatesResponse: Справочник курсов валют к базовой валюте USD.
        """
        # 1. Попытка чтения из основного кэша Redis (свежий кэш либо активный негативный кэш)
        cached_response = await self._read_from_redis(REDIS_CACHE_KEY)
        if cached_response:
            return cached_response

        # 2. Защита от Thundering Herd при протухании кэша
        async with currency_state.lock:
            # Повторная проверка (double-checked locking)
            cached_response = await self._read_from_redis(REDIS_CACHE_KEY)
            if cached_response:
                return cached_response

            # 3. Запрос к внешнему сетевому шлюзу
            fetched_rates = await self._fetch_rates_from_gateway()
            now = datetime.now(timezone.utc)

            if fetched_rates:
                currency_state.mem_rates.update(fetched_rates)
                currency_state.mem_updated_at = now
                response = CurrencyRatesResponse(
                    base="USD",
                    rates=currency_state.mem_rates,
                    updated_at=currency_state.mem_updated_at,
                    is_fallback=False,
                )
                await self._write_to_redis(response, key=REDIS_CACHE_KEY, ttl=self.cache_ttl)
                await self._write_to_redis(response, key=REDIS_LAST_SUCCESSFUL_KEY, ttl=None)
                return response

            # 4. Шлюз недоступен: получение последних успешных курсов и запись в негативный кэш
            logger.warning(
                "Внешний шлюз котировок недоступен, запись fallback-котировок в негативный кэш Redis (TTL=%s с)",
                self.fallback_cache_ttl,
            )
            last_successful = await self._read_from_redis(REDIS_LAST_SUCCESSFUL_KEY)
            if last_successful:
                fallback_rates = last_successful.rates
                fallback_updated_at = last_successful.updated_at
            else:
                fallback_rates = dict(currency_state.mem_rates)
                fallback_updated_at = currency_state.mem_updated_at

            fallback_response = CurrencyRatesResponse(
                base="USD",
                rates=fallback_rates,
                updated_at=fallback_updated_at,
                is_fallback=True,
            )
            # Запись негативного кэша с коротким TTL для предотвращения деградации смежных запросов
            await self._write_to_redis(
                fallback_response,
                key=REDIS_CACHE_KEY,
                ttl=self.fallback_cache_ttl,
            )
            return fallback_response

    async def _fetch_rates_from_gateway(self) -> dict[str, Decimal] | None:
        """Выполнить асинхронный HTTP-запрос к внешнему API курсов валют с переиспользованием клиента."""
        try:
            client = get_http_client()
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
                        try:
                            val = Decimal(str(raw_rates[code]))
                            if val > Decimal("0") and val.is_finite():
                                extracted[code] = val
                        except Exception:
                            pass
                if len(extracted) == len(SUPPORTED_CURRENCIES):
                    logger.info("Котировки валют успешно загружены из внешнего шлюза")
                    return extracted
        except Exception as e:
            logger.warning("Не удалось получить курсы валют из внешнего шлюза %s: %s", self.gateway_url, e)
        return None

    async def _read_from_redis(self, key: str = REDIS_CACHE_KEY) -> CurrencyRatesResponse | None:
        """Прочитать кэшированные курсы из указанного ключа Redis."""
        try:
            client = get_redis()
            cached_json = await client.get(key)
            if cached_json:
                data = json.loads(cached_json)
                rates = {k: Decimal(str(v)) for k, v in data["rates"].items()}
                updated_at = datetime.fromisoformat(data["updated_at"])
                is_fallback = bool(data.get("is_fallback", False))
                return CurrencyRatesResponse(
                    base=data.get("base", "USD"),
                    rates=rates,
                    updated_at=updated_at,
                    is_fallback=is_fallback,
                )
        except Exception as err:
            logger.warning("Ошибка чтения кэша котировок из Redis (%s): %s", key, err)
        return None

    async def _write_to_redis(
        self,
        response: CurrencyRatesResponse,
        key: str = REDIS_CACHE_KEY,
        ttl: int | None = None,
    ) -> None:
        """Сохранить курсы валют в Redis с опциональным TTL."""
        try:
            client = get_redis()
            payload = {
                "base": response.base,
                "rates": {k: str(v) for k, v in response.rates.items()},
                "updated_at": response.updated_at.isoformat(),
                "is_fallback": response.is_fallback,
            }
            dumped = json.dumps(payload)
            if ttl is not None and ttl > 0:
                await client.setex(key, ttl, dumped)
            else:
                await client.set(key, dumped)
            logger.info("Кэш котировок валют успешно сохранен в Redis (%s, ttl=%s)", key, ttl)
        except Exception as err:
            logger.warning("Не удалось сохранить кэш котировок в Redis (%s): %s", key, err)
