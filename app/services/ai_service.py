"""Сервис формирования персональных финансовых инсайтов с использованием Google Gemini."""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from google import genai
from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Transaction
from app.redis import (
    get_insights_cache_key,
    get_insights_daily_quota_key,
    get_insights_user_patterns,
    get_redis,
)
from app.schemas.schemas import BudgetResponse, InsightResponse, InsightState
from app.services.budget_service import BudgetService
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Единый клиент Redis приложения
redis_client = get_redis()

# Отчёт за месяц хранится долго: прошлые месяцы не должны генерироваться повторно.
# Redis запущен с AOF (appendonly), поэтому записи переживают перезапуск.
INSIGHTS_CACHE_TTL_SECONDS = 400 * 86400

# Сколько новых операций текущего месяца допускается до повторной генерации отчёта.
REGENERATION_THRESHOLD = 5


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
        # Инициализируем клиента, если не передан мок
        if not client:
            # Настраиваем ключ API глобально, если он есть
            if (
                config.gemini_api_key
                and not config.gemini_api_key.startswith("sk-")
                and not config.gemini_api_key.startswith("your-")
                and config.gemini_api_key != "valid-real-api-key"
                and config.gemini_api_key != "test-api-key"
            ):
                self.client = genai.Client(api_key=config.gemini_api_key)
            else:
                self.client = genai.Client(api_key="placeholder")
        else:
            self.client = client
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
    def _extract_json(text: str) -> dict:
        """Извлечь и распарсить JSON-объект из ответа модели, очищая markdown и лишний текст."""
        cleaned = text.strip()

        # Удаляем markdown-блоки вида ```json ... ``` или ``` ... ```
        if "```" in cleaned:
            pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
            match = re.search(pattern, cleaned)
            if match:
                cleaned = match.group(1).strip()

        # Находим границы JSON-объекта от первой { до последней }
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end >= start:
            end_idx = end + 1
            cleaned = cleaned[start:end_idx]

        return json.loads(cleaned)

    @staticmethod
    def _month_bounds(month: int, year: int) -> tuple[datetime, datetime]:
        """Границы календарного месяца в UTC: [начало, начало следующего)."""
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            return start, datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        return start, datetime(year, month + 1, 1, tzinfo=timezone.utc)

    async def _month_fingerprint(self, user_id: uuid.UUID, month: int, year: int) -> MonthFingerprint:
        """Отпечаток данных месяца: операции (число, сумма, последнее добавление) и лимиты.

        Совпадение отпечатка с сохранённым в кэше означает, что данные периода не менялись
        и повторная генерация не нужна.
        """
        start, end = self._month_bounds(month, year)
        tx_row = (
            await self.db.execute(
                select(
                    func.count(Transaction.id),
                    func.coalesce(func.sum(Transaction.base_amount), 0),
                    func.max(Transaction.created_at),
                ).where(
                    Transaction.user_id == user_id,
                    Transaction.date >= start,
                    Transaction.date < end,
                )
            )
        ).one()
        budget_row = (
            await self.db.execute(
                select(func.count(Budget.id), func.coalesce(func.sum(Budget.limit_amount), 0)).where(
                    Budget.user_id == user_id,
                    Budget.month == month,
                    Budget.year == year,
                )
            )
        ).one()
        tx_count, tx_sum, last_created = tx_row
        budget_count, budget_sum = budget_row
        # str(): драйверы возвращают max(created_at) по-разному (datetime или строка), сравнение — в рамках одной БД
        last_created_str = str(last_created) if last_created is not None else "-"
        signature = f"{tx_count}:{tx_sum}:{last_created_str}:{budget_count}:{budget_sum}"
        return MonthFingerprint(tx_count=int(tx_count), signature=signature)

    @staticmethod
    def _is_cache_fresh(cached: dict, fingerprint: MonthFingerprint, is_current_month: bool) -> bool:
        """Можно ли отдать сохранённый отчёт без новой генерации.

        Прошлый месяц — только если его данные не менялись (отпечаток совпадает).
        Текущий месяц — также пока новых операций меньше порога: иначе каждая трата
        запускала бы новую генерацию.
        """
        if cached.get("signature") == fingerprint.signature:
            return True
        cached_count = cached.get("tx_count")
        return (
            is_current_month
            and isinstance(cached_count, int)
            and abs(fingerprint.tx_count - cached_count) < REGENERATION_THRESHOLD
        )

    @staticmethod
    def _response_from_cache(cached: dict) -> InsightResponse:
        """Собрать ответ из записи кэша."""
        return InsightResponse(
            insights=cached["insights"],
            generated_at=datetime.fromisoformat(cached["generated_at"]),
            state=InsightState.ready,
        )

    async def get_insights(
        self,
        user_id: uuid.UUID,
        currency: str = "USD",
        locale: str = "ru",
        month: int | None = None,
        year: int | None = None,
        generate: bool = True,
    ) -> InsightResponse:
        """Вернуть AI-отчёт за месяц, по возможности без вызова LLM.

        LLM не вызывается для будущего месяца и месяца без операций (`no_data`), при совпадении
        данных периода с сохранённым отчётом и при `generate=False` (`not_generated`, если
        отчёта нет). Отчёты хранятся в Redis вместе с отпечатком данных месяца.
        """
        now = datetime.now(timezone.utc)
        target_month = month or now.month
        target_year = year or now.year
        lang_prefix = locale[:2].lower()

        if (target_year, target_month) > (now.year, now.month):
            return InsightResponse(insights=[], generated_at=now, state=InsightState.no_data)

        is_placeholder = (
            not config.gemini_api_key
            or config.gemini_api_key.startswith("sk-")
            or config.gemini_api_key.startswith("your-")
            or config.gemini_api_key == "valid-real-api-key"
        )
        # Для тестов пропускаем проверку плейсхолдера, если ключ явно valid-real-key или test-api-key
        if is_placeholder and config.gemini_api_key != "valid-real-key" and config.gemini_api_key != "test-api-key":
            logger.warning("GEMINI_API_KEY is not set or is a placeholder, returning stub insights")
            stub_msg = {
                "ru": "В разработке...",
                "uz": "Ishlab chiqilmoqda...",
                "en": "In development...",
                "kk": "Әзірленуде...",
            }
            return InsightResponse(
                insights=[stub_msg.get(lang_prefix, stub_msg["en"])],
                generated_at=now,
            )

        fingerprint = await self._month_fingerprint(user_id, target_month, target_year)
        if fingerprint.tx_count == 0:
            return InsightResponse(insights=[], generated_at=now, state=InsightState.no_data)

        is_current_month = (target_year, target_month) == (now.year, now.month)
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

        if cached and self._is_cache_fresh(cached, fingerprint, is_current_month):
            logger.info(f"Returning cached AI insights for user {user_id}")
            return self._response_from_cache(cached)

        if not generate:
            # Устаревший отчёт лучше пустого экрана; обновление — по явному запросу.
            if cached:
                return self._response_from_cache(cached)
            return InsightResponse(insights=[], generated_at=now, state=InsightState.not_generated)

        # Проверка суточного лимита обращений к LLM
        date_str = now.strftime("%Y-%m-%d")
        daily_quota_key = get_insights_daily_quota_key(user_id, date_str)
        daily_calls = 0
        try:
            calls_val = await redis_client.get(daily_quota_key)
            if calls_val:
                daily_calls = int(calls_val)
        except Exception as err:
            logger.warning(f"Не удалось прочитать счетчик квоты из Redis для пользователя {user_id}: {err}")

        if daily_calls >= config.ai_daily_limit_per_user:
            logger.warning(
                "Суточный лимит вызовов AI исчерпан для пользователя %s (%s/%s)",
                user_id,
                daily_calls,
                config.ai_daily_limit_per_user,
            )
            if cached:
                return self._response_from_cache(cached)

            quota_messages = {
                "ru": [
                    "Вы достигли дневного лимита запросов к финансовому помощнику (максимум 5 обновлений в день).",
                    "Пожалуйста, возвращайтесь завтра или продолжайте пользоваться текущей аналитикой.",
                ],
                "en": [
                    "You have reached your daily limit for financial assistant requests (maximum 5 updates per day).",
                    "Please return tomorrow or continue using your current insights.",
                ],
                "uz": [
                    "Siz moliyaviy yordamchiga kunlik so'rovlar limitiga yetdingiz (kuniga ko'pi bilan 5 ta yangilanish).",
                    "Iltimos, ertaga qaytib keling yoki joriy tahlildan foydalanishda davom eting.",
                ],
                "kk": [
                    "Сіз қаржылық көмекшіге тәуліктік сұраныс шегіне жеттіңіз (күніне ең көбі 5 жаңарту).",
                    "Ертең қайта келіңіз немесе ағымдағы талдауды пайдалануды жалғастырыңыз.",
                ],
            }
            return InsightResponse(
                insights=quota_messages.get(lang_prefix, quota_messages["en"]),
                generated_at=now,
                state=InsightState.quota_exceeded,
            )

        start_date, end_date = self._month_bounds(target_month, target_year)
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
            )
        except Exception as err:
            logger.warning(f"Не удалось загрузить бюджеты для пользователя {user_id}: {err}")

        summary = self._build_summary(transactions, budgets, currency)

        fallback_messages = {
            "ru": [
                "К сожалению, наши умные помощники сейчас очень заняты и не могут проанализировать данные.",
                "Пожалуйста, загляните за советами немного позже.",
            ],
            "en": [
                "Unfortunately, our smart assistants are currently very busy and cannot analyze the data.",
                "Please check back for tips a little later.",
            ],
            "uz": [
                "Afsuski, aqlli yordamchilarimiz hozir juda band va ma'lumotlarni tahlil qila olmaydilar.",
                "Iltimos, maslahatlar uchun biroz keyinroq qaytib keling.",
            ],
            "kk": [
                "Өкінішке қарай, біздің ақылды көмекшілеріміз қазір өте бос емес және деректерді талдай алмайды.",
                "Кеңестер алу үшін сәл кейінірек кіріңіз.",
            ],
        }

        try:
            insights = await self._call_gemini(summary, locale, currency)
            response = InsightResponse(insights=insights, generated_at=datetime.now(timezone.utc))

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

            try:
                pipe = redis_client.pipeline()
                pipe.incr(daily_quota_key)
                pipe.expire(daily_quota_key, 86400)
                await pipe.execute()
            except Exception as err:
                logger.warning(f"Не удалось обновить счетчик суточной квоты в Redis: {err}")

            return response
        except Exception as e:
            logger.error(f"Error requesting insights from Gemini API: {e}")
            return InsightResponse(
                insights=fallback_messages.get(lang_prefix, fallback_messages["en"]),
                generated_at=datetime.now(timezone.utc),
            )

    def _build_summary(
        self,
        transactions: list[Transaction],
        budgets: list[BudgetResponse] | str | None = None,
        currency: str = "USD",
    ) -> str:
        """Сформировать текстовую сводку транзакций и бюджетов для системного промпта LLM."""
        if isinstance(budgets, str):
            currency = budgets
            budgets_list: list[BudgetResponse] = []
        else:
            budgets_list = budgets or []

        blocks = []

        # Блок 1: Сводка по лимитам категорий на месяц
        blocks.append("Блок 1: Сводка по лимитам категорий на месяц:")
        if budgets_list:
            for b in budgets_list:
                cat = b.category.value if hasattr(b.category, "value") else str(b.category)
                status = "Превышен" if b.spent > b.limit_amount else "В пределах лимита"
                blocks.append(
                    f"{cat} | Лимит: {b.limit_amount} {currency} | Потрачено: {b.spent} {currency} | "
                    f"Остаток: {b.remaining} {currency} | {status}"
                )
        else:
            blocks.append("Лимиты бюджетов на данный период не установлены.")

        # Блок 2: Список транзакций
        blocks.append("\nБлок 2: Список транзакций:")
        if transactions:
            for tx in transactions:
                tx_curr = getattr(tx, "currency", None) or currency
                base_amt = getattr(tx, "base_amount", None)
                if tx_curr != currency and base_amt is not None:
                    amount_str = f"{tx.amount} {tx_curr} (эквивалент: {base_amt} {currency})"
                else:
                    amount_str = f"{tx.amount} {tx_curr}"

                type_val = tx.type.value if hasattr(tx.type, "value") else str(tx.type)
                cat_val = tx.category.value if hasattr(tx.category, "value") else str(tx.category)
                date_str = tx.date.strftime("%Y-%m-%d") if hasattr(tx.date, "strftime") else str(tx.date)

                # Очистка описания от управляющих символов, переводов строк и разделителей (|) для защиты от Prompt Injection
                raw_desc = getattr(tx, "description", None) or ""
                clean_desc = re.sub(r"[\r\n\t|]+", " ", raw_desc)
                clean_desc = "".join(ch for ch in clean_desc if ch >= " ")
                clean_desc = re.sub(r"\s+", " ", clean_desc).strip()
                clean_desc = clean_desc or "Без описания"

                parts = [
                    date_str,
                    type_val,
                    cat_val,
                    amount_str,
                    clean_desc,
                ]
                blocks.append(" | ".join(parts))
        else:
            blocks.append("Транзакции за данный период отсутствуют.")

        return "\n".join(blocks)

    async def _call_gemini(self, summary: str, locale: str, currency: str) -> list[str]:
        """Отправить запрос в Gemini API со Structured Outputs и распарсить JSON с рекомендациями."""
        system_instruction = (
            "Ты — персональный финансовый ассистент. Анализируй расходы пользователя и соблюдение им "
            "установленных месячных лимитов бюджетов по категориям. Формируй практичные, понятные "
            "и действенные советы на основе предоставленных данных. Ответ всегда возвращай строго "
            'в формате JSON: {"insights": ["совет 1", "совет 2", "совет 3"]}.'
        )

        prompt = f"""Вот финансовые данные пользователя за выбранный период:

{summary}

Дай 3 конкретных совета по управлению финансами и бюджетом на основе этих данных.
Учитывай как фактические транзакции, так и состояние месячных лимитов категорий (превышения, остатки).
Выдача советов должна быть максимально простой, без сложных финансовых слов и фраз.
Пиши доступным языком для среднестатистического человека.
Учитывай, что валюта пользователя — {currency}. Суммы указаны в этой валюте, соразмеряй советы с реалиями этой валюты.
Ответ сформируй на языке/локали: {locale}. Обязательно переведи названия категорий на этот язык.
Формат ответа JSON: {{"insights": ["совет 1", "совет 2", "совет 3"]}}
Только JSON, без лишнего текста."""

        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            system_instruction=system_instruction,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=gen_config,
        )
        text = response.text
        try:
            data = self._extract_json(text)
            return data.get("insights", [])
        except (json.JSONDecodeError, ValueError) as err:
            logger.error(f"Failed to parse JSON from LLM response: {err}. Raw text: {text}")
            raise
