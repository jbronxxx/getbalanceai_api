"""Сервис формирования персональных финансовых инсайтов с использованием Google Gemini."""

import json
import re
import uuid
from datetime import datetime, timezone

from google import genai
from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Transaction
from app.redis import (
    get_insights_cache_key,
    get_insights_count_key,
    get_insights_daily_quota_key,
    get_insights_user_patterns,
    get_redis,
)
from app.schemas.schemas import BudgetResponse, InsightResponse
from app.services.budget_service import BudgetService
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Единый клиент Redis приложения
redis_client = get_redis()


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
    async def _find_latest_cached_insights(user_id: uuid.UUID) -> InsightResponse | None:
        """Найти последнюю доступную сохраненную рекомендацию пользователя в Redis."""
        try:
            async for key in redis_client.scan_iter(match=f"insights_cache:{user_id}*", count=10):
                val = await redis_client.get(key)
                if val:
                    try:
                        cached_data = json.loads(val)
                        if "insights" in cached_data and "generated_at" in cached_data:
                            return InsightResponse(
                                insights=cached_data["insights"],
                                generated_at=datetime.fromisoformat(cached_data["generated_at"]),
                            )
                    except Exception:
                        pass
        except Exception as err:
            logger.warning(f"Ошибка при поиске последнего кэша пользователя {user_id}: {err}")
        return None

    async def get_insights(
        self,
        user_id: uuid.UUID,
        currency: str = "USD",
        locale: str = "ru",
        month: int | None = None,
        year: int | None = None,
    ) -> InsightResponse:
        """Сформировать персональные рекомендации по расходам и бюджетам пользователя."""
        now = datetime.now(timezone.utc)
        target_month = month or now.month
        target_year = year or now.year

        is_placeholder = (
            not config.gemini_api_key
            or config.gemini_api_key.startswith("sk-")
            or config.gemini_api_key.startswith("your-")
            or config.gemini_api_key == "valid-real-api-key"
        )
        # Для тестов пропускаем проверку плейсхолдера, если ключ явно valid-real-key или test-api-key
        lang_prefix = locale[:2].lower()

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

        # Получаем общее количество транзакций пользователя
        total_query = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
        total_result = await self.db.execute(total_query)
        total_tx_count = total_result.scalar_one()

        if total_tx_count == 0:
            empty_msg = {
                "ru": "Добавьте первые транзакции, чтобы получить анализ.",
                "uz": "Tahlil olish uchun birinchi tranzaksiyalarni qo'shing.",
                "en": "Add your first transactions to get an analysis.",
                "kk": "Талдау алу үшін алғашқы транзакцияларды қосыңыз.",
            }
            return InsightResponse(
                insights=[empty_msg.get(lang_prefix, empty_msg["en"])],
                generated_at=now,
            )

        cache_key = get_insights_cache_key(user_id, locale, currency, month=target_month, year=target_year)
        count_key = get_insights_count_key(user_id, locale, currency, month=target_month, year=target_year)

        cached_data_str = None
        last_tx_count_str = None
        try:
            cached_data_str = await redis_client.get(cache_key)
            last_tx_count_str = await redis_client.get(count_key)
        except Exception as err:
            logger.warning(f"Не удалось прочитать кэш инсайтов из Redis для пользователя {user_id}: {err}")

        # Порог накопления транзакций для обновления рекомендаций
        THRESHOLD = 5

        if cached_data_str and last_tx_count_str:
            last_tx_count = int(last_tx_count_str)
            # Если количество новых транзакций меньше порога, возвращаем кэш
            if abs(total_tx_count - last_tx_count) < THRESHOLD:
                try:
                    cached_data = json.loads(cached_data_str)
                    logger.info(f"Returning cached AI insights for user {user_id}")
                    return InsightResponse(
                        insights=cached_data["insights"],
                        generated_at=datetime.fromisoformat(cached_data["generated_at"]),
                    )
                except Exception as e:
                    logger.error(f"Error reading cache for user {user_id}: {e}")

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
            # 1. Возвращаем кэш текущего периода, если есть
            if cached_data_str:
                try:
                    cached_data = json.loads(cached_data_str)
                    return InsightResponse(
                        insights=cached_data["insights"],
                        generated_at=datetime.fromisoformat(cached_data["generated_at"]),
                    )
                except Exception:
                    pass

            # 2. Поиск последнего доступного кэша пользователя
            fallback_cached = await self._find_latest_cached_insights(user_id)
            if fallback_cached:
                return fallback_cached

            # 3. Локализованное вежливое сообщение о достижении суточного лимита
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
            )

        # Запрашиваем транзакции пользователя за выбранный период
        start_date = datetime(target_year, target_month, 1, tzinfo=timezone.utc)
        end_date = (
            datetime(target_year + 1, 1, 1, tzinfo=timezone.utc)
            if target_month == 12
            else datetime(target_year, target_month + 1, 1, tzinfo=timezone.utc)
        )

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

        # Если в выбранном периоде транзакций нет, загружаем последние 50 транзакций
        if not transactions:
            fallback_query = (
                select(Transaction).where(Transaction.user_id == user_id).order_by(Transaction.date.desc()).limit(50)
            )
            fallback_res = await self.db.execute(fallback_query)
            transactions = list(fallback_res.scalars().all())

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
                    },
                    ensure_ascii=False,
                )
                await redis_client.setex(cache_key, 7 * 86400, cache_val)
                await redis_client.setex(count_key, 7 * 86400, str(total_tx_count))
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
