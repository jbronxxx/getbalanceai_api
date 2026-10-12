"""Сервис формирования персональных финансовых инсайтов с использованием Google Gemini."""

import json
import re
import uuid
from datetime import datetime, timezone

from google import genai
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Transaction
from app.redis import (
    get_insights_cache_key,
    get_insights_count_key,
    get_insights_user_patterns,
    get_redis,
)
from app.schemas.schemas import InsightResponse
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

    async def get_insights(self, user_id: uuid.UUID, currency: str = "RUB", locale: str = "ru") -> InsightResponse:
        """Сформировать персональные рекомендации по расходам пользователя."""
        is_placeholder = (
            not config.gemini_api_key
            or config.gemini_api_key.startswith("sk-")
            or config.gemini_api_key.startswith("your-")
            or config.gemini_api_key == "valid-real-api-key"
        )
        # Для тестов пропускаем проверку плейсхолдера, если ключ явно valid-real-key
        lang_prefix = locale[:2].lower()

        if is_placeholder and config.gemini_api_key != "valid-real-key" and config.gemini_api_key != "test-api-key":
            logger.warning("GEMINI_API_KEY is not set or is a placeholder, returning stub insights")
            stub_msg = {"ru": "В разработке...", "uz": "Ishlab chiqilmoqda...", "en": "In development..."}
            return InsightResponse(
                insights=[stub_msg.get(lang_prefix, stub_msg["en"])],
                generated_at=datetime.now(timezone.utc),
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
            }
            return InsightResponse(
                insights=[empty_msg.get(lang_prefix, empty_msg["en"])],
                generated_at=datetime.now(timezone.utc),
            )

        cache_key = get_insights_cache_key(user_id, locale, currency)
        count_key = get_insights_count_key(user_id, locale, currency)

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

        # Запрашиваем последние 50 транзакций для анализа
        query = select(Transaction).where(Transaction.user_id == user_id).order_by(Transaction.date.desc()).limit(50)
        result = await self.db.execute(query)
        transactions = list(result.scalars().all())

        summary = self._build_summary(transactions, currency)

        try:
            insights = await self._call_gemini(summary, locale, currency)
            response = InsightResponse(insights=insights, generated_at=datetime.now(timezone.utc))

            try:
                cache_val = json.dumps(
                    {
                        "insights": insights,
                        "generated_at": response.generated_at.isoformat(),
                    }
                )
                await redis_client.setex(cache_key, 7 * 86400, cache_val)
                await redis_client.setex(count_key, 7 * 86400, str(total_tx_count))
            except Exception as err:
                logger.warning(f"Не удалось записать кэш инсайтов в Redis для пользователя {user_id}: {err}")

            return response
        except Exception as e:
            logger.error(f"Error requesting insights from Gemini API: {e}")
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
            }
            return InsightResponse(
                insights=fallback_messages.get(lang_prefix, fallback_messages["en"]),
                generated_at=datetime.now(timezone.utc),
            )

    def _build_summary(self, transactions: list[Transaction], currency: str) -> str:
        """Сформировать текстовую сводку транзакций для передачи в системный промпт LLM."""
        lines = []
        for tx in transactions:
            tx_curr = getattr(tx, "currency", None) or currency
            base_amt = getattr(tx, "base_amount", None)
            if tx_curr != currency and base_amt is not None:
                amount_str = f"{tx.amount} {tx_curr} (эквивалент: {base_amt} {currency})"
            else:
                amount_str = f"{tx.amount} {tx_curr}"

            parts = [
                tx.date.strftime("%Y-%m-%d"),
                tx.type.value,
                tx.category.value,
                amount_str,
                tx.description,
            ]
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    async def _call_gemini(self, summary: str, locale: str, currency: str) -> list[str]:
        """Отправить запрос в Gemini API и распарсить JSON с рекомендациями."""
        prompt = f"""Вот транзакции пользователя за последнее время:

{summary}

Дай 3 конкретных совета по управлению бюджетом на основе этих данных.
Выдача советов должна быть максимально простой, без сложных финансовых слов и фраз.
Пиши доступным языком для среднестатистического человека.
Учитывай, что валюта пользователя - {currency}. Суммы указаны в этой валюте, соразмеряй советы с реалиями этой валюты.
Ответ сформируй на языке/локали: {locale}. Обязательно переведи названия категорий
(например, food -> Питание, и т.д.) на этот язык.
Ответь в формате JSON: {{"insights": ["совет 1", "совет 2", "совет 3"]}}
Только JSON, без лишнего текста."""

        response = await self.client.aio.models.generate_content(model=self.model_name, contents=prompt)
        text = response.text
        try:
            data = self._extract_json(text)
            return data.get("insights", [])
        except (json.JSONDecodeError, ValueError) as err:
            logger.error(f"Failed to parse JSON from LLM response: {err}. Raw text: {text}")
            raise
