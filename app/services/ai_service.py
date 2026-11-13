"""Сервис формирования персональных финансовых инсайтов с использованием Google Gemini."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from decimal import Decimal

from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Transaction, TransactionType
from app.redis import (
    get_insights_cache_key,
    get_insights_daily_quota_key,
    get_insights_user_patterns,
    get_redis,
)
from app.schemas.schemas import BudgetResponse, InsightResponse, InsightState
from app.services.ai_usage import AIRequestKind
from app.services.budget_service import BudgetService
from app.services.gemini import (
    create_gemini_client,
    extract_json,
    gemini_configured,
    generate_text,
    release_daily_quota,
    take_daily_quota,
)
from app.services.insights_formatting import category_name, format_money
from app.services.insights_texts import insight_texts
from app.services.period import current_month as client_current_month
from app.services.period import month_bounds
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Единый клиент Redis приложения
redis_client = get_redis()

# Отчёт за месяц хранится долго: прошлые месяцы не должны генерироваться повторно.
# Redis запущен с AOF (appendonly), поэтому записи переживают перезапуск.
INSIGHTS_CACHE_TTL_SECONDS = 400 * 86400

# Меньше операций за месяц — советы ИИ получаются пустыми, а генерация тратит суточную квоту.
# Такое же значение в мобильном приложении (карточка «нужно хотя бы 7 операций»).
MIN_TRANSACTIONS_FOR_INSIGHTS = 7


@dataclass(frozen=True)
class MonthFingerprint:
    """Отпечаток данных месяца для проверки актуальности сохранённого отчёта."""

    tx_count: int
    signature: str


class AIService:
    """Сервис взаимодействия с LLM (Google Gemini) для анализа транзакций пользователя."""

    def __init__(self, db: AsyncSession, client=None, model_name=None):
        """Инициализация сервиса с сессией базы данных и клиентом Gemini."""
        self.db = db
        self.client = client or create_gemini_client()
        self.model_name = model_name or config.ai_model

    @classmethod
    async def invalidate_cache(cls, user_id: uuid.UUID) -> None:
        """Инвалидировать кэш инсайтов для конкретного пользователя без блокировки Redis."""
        try:
            client = get_redis()
            patterns = get_insights_user_patterns(user_id)
            for pattern in patterns:
                batch = []
                async for key in client.scan_iter(match=pattern, count=500):
                    batch.append(key)
                    if len(batch) >= 500:
                        await client.unlink(*batch)
                        batch.clear()
                if batch:
                    await client.unlink(*batch)
        except Exception as err:
            logger.warning(f"Ошибка инвалидации кэша Redis для пользователя {user_id}: {err}")

    @classmethod
    async def clear_cache(cls) -> None:
        """Очистить весь кэш инсайтов без блокировки Redis и без удаления других данных."""
        try:
            client = get_redis()
            batch = []
            async for key in client.scan_iter(match="insights_*", count=500):
                batch.append(key)
                if len(batch) >= 500:
                    await client.unlink(*batch)
                    batch.clear()
            if batch:
                await client.unlink(*batch)
        except Exception as err:
            logger.warning(f"Ошибка полной очистки кэша Redis: {err}")

    @staticmethod
    def _month_bounds(month: int, year: int, tz: tzinfo = timezone.utc) -> tuple[datetime, datetime]:
        """Границы календарного месяца в поясе клиента, переведённые в UTC: [начало, начало следующего)."""
        return month_bounds(year, month, tz)

    async def _month_fingerprint(
        self, user_id: uuid.UUID, month: int, year: int, tz: tzinfo = timezone.utc
    ) -> MonthFingerprint:
        """Отпечаток данных месяца: итоги операций по категории, типу и валюте и лимиты с валютой.

        Меняется при любом изменении, влияющем на суммы отчёта: добавление, удаление и правка
        операции (сумма, категория, тип, валюта), а также лимита (сумма, валюта). Совпадение
        с сохранённым в кэше означает, что цифры отчёта совпадают с текущими данными.
        """
        start, end = self._month_bounds(month, year, tz)
        tx_rows = (
            await self.db.execute(
                select(
                    Transaction.category,
                    Transaction.type,
                    Transaction.currency,
                    func.count(Transaction.id),
                    func.coalesce(func.sum(Transaction.amount), 0),
                    func.coalesce(func.sum(Transaction.base_amount), 0),
                )
                .where(
                    Transaction.user_id == user_id,
                    Transaction.date >= start,
                    Transaction.date < end,
                )
                .group_by(Transaction.category, Transaction.type, Transaction.currency)
            )
        ).all()
        budget_rows = (
            await self.db.execute(
                select(Budget.category, Budget.currency, Budget.limit_amount).where(
                    Budget.user_id == user_id,
                    Budget.month == month,
                    Budget.year == year,
                )
            )
        ).all()

        def money(value) -> str:
            # Драйверы возвращают суммы по-разному (Decimal, float, строка) — приводим к одному виду
            return str(Decimal(str(value)).quantize(Decimal("0.01")))

        def key(value) -> str:
            return str(getattr(value, "value", value))

        tx_parts = sorted(
            f"{key(cat)}:{key(tx_type)}:{cur}:{count}:{money(amount)}:{money(base)}"
            for cat, tx_type, cur, count, amount, base in tx_rows
        )
        budget_parts = sorted(f"{key(cat)}:{cur}:{money(limit)}" for cat, cur, limit in budget_rows)
        tx_count = sum(int(row[3]) for row in tx_rows)
        signature = "|".join(tx_parts) + "#" + "|".join(budget_parts)
        return MonthFingerprint(tx_count=tx_count, signature=signature)

    @staticmethod
    def _is_cache_fresh(cached: dict, fingerprint: MonthFingerprint) -> bool:
        """Сохранённый отчёт актуален, только если данные месяца не менялись после генерации."""
        return cached.get("signature") == fingerprint.signature

    @staticmethod
    def _response_from_cache(
        cached: dict,
        is_outdated: bool = False,
        state: InsightState = InsightState.ready,
    ) -> InsightResponse:
        """Собрать ответ из записи кэша; `is_outdated` — данные месяца изменились после генерации."""
        return InsightResponse(
            insights=cached["insights"],
            generated_at=datetime.fromisoformat(cached["generated_at"]),
            state=state,
            is_outdated=is_outdated,
        )

    async def get_insights(
        self,
        user_id: uuid.UUID,
        currency: str = "USD",
        locale: str = "en",
        month: int | None = None,
        year: int | None = None,
        generate: bool = True,
        tz: tzinfo = timezone.utc,
    ) -> InsightResponse:
        """Вернуть AI-отчёт за месяц, по возможности без вызова LLM.

        LLM не вызывается для будущего месяца и месяца без операций (`no_data`), при числе операций
        меньше `MIN_TRANSACTIONS_FOR_INSIGHTS` (`not_enough_data`), при совпадении
        данных периода с сохранённым отчётом и при `generate=False` (`not_generated`, если
        отчёта нет). Отчёты хранятся в Redis вместе с отпечатком данных месяца.
        """
        now = datetime.now(timezone.utc)
        # Текущий месяц — по часам клиента: в UTC+5 1-го числа до 05:00 по UTC ещё прошлый месяц
        current_year, current_month = client_current_month(tz)
        target_month = month or current_month
        target_year = year or current_year
        texts = insight_texts(locale)

        if (target_year, target_month) > (current_year, current_month):
            return InsightResponse(insights=[], generated_at=now, state=InsightState.no_data)

        if not gemini_configured():
            logger.warning("GEMINI_API_KEY is not set or is a placeholder, returning stub insights")
            return InsightResponse(insights=[texts.stub], generated_at=now)

        fingerprint = await self._month_fingerprint(user_id, target_month, target_year, tz)
        if fingerprint.tx_count == 0:
            return InsightResponse(insights=[], generated_at=now, state=InsightState.no_data)
        if fingerprint.tx_count < MIN_TRANSACTIONS_FOR_INSIGHTS:
            # Сохранённый отчёт (сделан, когда операций было больше) тоже не отдаём: он про другие данные
            return InsightResponse(insights=[], generated_at=now, state=InsightState.not_enough_data)

        cache_key = get_insights_cache_key(user_id, locale, currency, month=target_month, year=target_year)

        cached: dict | None = None
        try:
            cached_str = await redis_client.get(cache_key)
            if cached_str:
                parsed = json.loads(cached_str)
                if "insights" in parsed and "generated_at" in parsed:
                    cached = parsed
        except Exception as err:
            logger.warning(f"Не удалось прочитать кэш инсайтов из Redis для пользователя {user_id}: {err}")

        if cached and self._is_cache_fresh(cached, fingerprint):
            logger.debug(f"Инсайты для пользователя {user_id} отданы из кэша")
            return self._response_from_cache(cached)

        if not generate:
            # Устаревший отчёт лучше пустого экрана, но с пометкой: его суммы уже не совпадают
            # с данными. Обновление — по явному запросу.
            if cached:
                return self._response_from_cache(cached, is_outdated=True)
            return InsightResponse(insights=[], generated_at=now, state=InsightState.not_generated)

        # Суточная квота занимается до вызова LLM (атомарный INCR, см. take_daily_quota)
        date_str = now.strftime("%Y-%m-%d")
        daily_quota_key = get_insights_daily_quota_key(user_id, date_str)
        try:
            daily_calls = await take_daily_quota(redis_client, daily_quota_key)
        except Exception as err:
            # Без счётчика лимит не проверить, а каждый вызов LLM платный: генерацию не запускаем
            logger.warning(f"Не удалось занять квоту AI в Redis для пользователя {user_id}: {err}")
            if cached:
                return self._response_from_cache(cached, is_outdated=True, state=InsightState.busy)
            return InsightResponse(insights=list(texts.busy), generated_at=now, state=InsightState.busy)

        if daily_calls > config.ai_daily_limit_per_user:
            logger.warning(
                "Суточный лимит вызовов AI исчерпан для пользователя %s (%s/%s)",
                user_id,
                daily_calls - 1,
                config.ai_daily_limit_per_user,
            )
            if cached:
                # Старые клиенты показывают insights как есть; новые по state пишут, что обновить можно завтра
                return self._response_from_cache(cached, is_outdated=True, state=InsightState.quota_exceeded)

            return InsightResponse(
                insights=list(texts.quota_exceeded),
                generated_at=now,
                state=InsightState.quota_exceeded,
            )

        start_date, end_date = self._month_bounds(target_month, target_year, tz)
        query = (
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.date >= start_date,
                Transaction.date < end_date,
            )
            .order_by(Transaction.date.desc())
            .limit(50)
        )
        result = await self.db.execute(query)
        transactions = list(result.scalars().all())

        # Загружаем активные лимиты бюджетов за выбранный период
        budgets: list[BudgetResponse] = []
        try:
            budget_service = BudgetService(self.db)
            budgets = await budget_service.get_all(
                user_id,
                month=target_month,
                year=target_year,
                user_base_currency=currency,
                tz=tz,
            )
        except Exception as err:
            logger.warning(f"Не удалось загрузить бюджеты для пользователя {user_id}: {err}")

        summary = self.build_summary(transactions, budgets, currency, locale)
        # Данные прочитаны: завершаем читающую транзакцию, чтобы соединение вернулось в пул
        # и не простаивало весь вызов LLM (до AI_REQUEST_TIMEOUT секунд)
        await self.db.commit()

        try:
            insights = await self._call_gemini(summary, locale, currency)
            response = InsightResponse(insights=insights, generated_at=datetime.now(timezone.utc))
        except Exception as e:
            logger.error(f"Ошибка запроса инсайтов к Gemini для пользователя {user_id}: {type(e).__name__}: {e}")
            await release_daily_quota(redis_client, daily_quota_key)
            # Сообщение остаётся в insights для старых клиентов; новые по state показывают свою карточку
            return InsightResponse(
                insights=list(texts.busy),
                generated_at=datetime.now(timezone.utc),
                state=InsightState.busy,
            )

        try:
            cache_val = json.dumps(
                {
                    "insights": insights,
                    "generated_at": response.generated_at.isoformat(),
                    "signature": fingerprint.signature,
                    "tx_count": fingerprint.tx_count,
                },
                ensure_ascii=False,
            )
            await redis_client.setex(cache_key, INSIGHTS_CACHE_TTL_SECONDS, cache_val)
        except Exception as err:
            logger.warning(f"Не удалось записать кэш инсайтов в Redis для пользователя {user_id}: {err}")

        logger.info(
            f"Сгенерированы AI-инсайты для пользователя {user_id} за {target_month}/{target_year}: "
            f"{len(insights)} советов, вызовов за сутки {daily_calls}/{config.ai_daily_limit_per_user}"
        )
        return response

    @staticmethod
    def build_summary(
        transactions: list[Transaction],
        budgets: list[BudgetResponse] | str | None = None,
        currency: str = "USD",
        locale: str = "en",
    ) -> str:
        """Сформировать текстовую сводку транзакций и бюджетов для системного промпта LLM.

        Подписи, категории и суммы — на языке отчёта, как их показывает приложение («Питание»,
        «$1 234,50»): модель повторяет их в советах дословно и отвечает на том же языке.

        Операция передаётся только датой, типом, категорией и суммой. Описание не передаётся: в свободном
        тексте могут быть личные данные (имена, адреса, телефоны, номера карт), и надёжно вычистить их нельзя.
        """
        texts = insight_texts(locale)
        if isinstance(budgets, str):
            currency = budgets
            budgets_list: list[BudgetResponse] = []
        else:
            budgets_list = budgets or []

        blocks = []

        # Блок 1: Сводка по лимитам категорий на месяц
        blocks.append(texts.limits_header)
        if budgets_list:
            for b in budgets_list:
                cat = category_name(getattr(b.category, "value", b.category), locale)
                status = texts.over_limit if b.spent > b.limit_amount else texts.within_limit

                # Суммы лимита — в его собственной валюте, не обязательно базовой
                limit = format_money(b.limit_amount, b.currency, locale)
                spent = format_money(b.spent, b.currency, locale)
                remaining = format_money(b.remaining, b.currency, locale)
                blocks.append(
                    texts.limit_line.format(category=cat, limit=limit, spent=spent, remaining=remaining, status=status)
                )
        else:
            blocks.append(texts.no_limits)

        # Блок 2: Список транзакций
        blocks.append("\n" + texts.transactions_header)
        if transactions:
            for tx in transactions:
                tx_curr = getattr(tx, "currency", None) or currency
                base_amt = getattr(tx, "base_amount", None)
                amount_str = format_money(tx.amount, tx_curr, locale)
                if tx_curr != currency and base_amt is not None:
                    amount_str = texts.equivalent.format(amount=amount_str, base=format_money(base_amt, currency, locale))

                type_code = tx.type.value if hasattr(tx.type, "value") else str(tx.type)
                type_val = texts.income if type_code == TransactionType.income.value else texts.expense
                cat_val = category_name(getattr(tx.category, "value", tx.category), locale)
                date_str = tx.date.strftime("%Y-%m-%d") if hasattr(tx.date, "strftime") else str(tx.date)

                blocks.append(" | ".join((date_str, type_val, cat_val, amount_str)))
        else:
            blocks.append(texts.no_transactions)

        return "\n".join(blocks)

    @staticmethod
    def build_request(summary: str, locale: str, currency: str) -> tuple[str, str]:
        """Системная инструкция и промпт отчёта; тот же запрос шлёт скрипт замера scripts/measure_ai_cost.py."""
        texts = insight_texts(locale)
        # Пример суммы в том виде, в каком она дана в данных: модель должна повторять этот формат
        system_instruction = texts.system_instruction.format(money_example=format_money(154771, currency, locale))
        return system_instruction, texts.prompt.format(summary=summary, currency=currency)

    async def _call_gemini(self, summary: str, locale: str, currency: str) -> list[str]:
        """Отправить запрос в Gemini API со Structured Outputs и распарсить JSON с рекомендациями."""
        system_instruction, prompt = self.build_request(summary, locale, currency)

        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            system_instruction=system_instruction,
        )

        text = await generate_text(self.client, self.model_name, prompt, gen_config, AIRequestKind.insights)
        try:
            data = extract_json(text)
            return data.get("insights", [])
        except (json.JSONDecodeError, ValueError) as err:
            # Сырой ответ модели не логируется: в нём пересказ финансовых данных пользователя.
            # Ошибку логирует вызывающий get_insights, здесь только добавляется длина ответа
            raise ValueError(f"Невалидный JSON в ответе LLM: {err}. Длина ответа: {len(text or '')}") from err
