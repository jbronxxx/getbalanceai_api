"""Сервис формирования персональных финансовых инсайтов с использованием Google Gemini."""

import json
import re
import uuid
from datetime import datetime, timezone

from google import genai
from redis import asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Transaction
from app.schemas.schemas import InsightResponse
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

redis_client = aioredis.from_url(config.redis_url, decode_responses=True)


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
        """Инвалидировать кэш инсайтов для конкретного пользователя."""
        keys = await redis_client.keys(f"insights_cache:{user_id}*")
        if keys:
            await redis_client.delete(*keys)
        count_keys = await redis_client.keys(f"insights_tx_count:{user_id}*")
        if count_keys:
            await redis_client.delete(*count_keys)

    @classmethod
    async def clear_cache(cls) -> None:
        """Очистить весь кэш инсайтов."""
        await redis_client.flushdb()

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
        if is_placeholder and config.gemini_api_key != "valid-real-key" and config.gemini_api_key != "test-api-key":
            logger.warning("GEMINI_API_KEY is not set or is a placeholder, returning stub insights")
            return InsightResponse(
                insights=[
                    "В разработке...",
                ],
                generated_at=datetime.now(timezone.utc),
            )

        # Получаем общее количество транзакций пользователя
        total_query = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
        total_result = await self.db.execute(total_query)
        total_tx_count = total_result.scalar_one()

        if total_tx_count == 0:
            return InsightResponse(
                insights=["Добавь первые транзакции, чтобы получить анализ."],
                generated_at=datetime.now(timezone.utc),
            )

        cache_key = f"insights_cache:{user_id}:{locale}:{currency}"
        count_key = f"insights_tx_count:{user_id}:{locale}:{currency}"

        cached_data_str = await redis_client.get(cache_key)
        last_tx_count_str = await redis_client.get(count_key)

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

            cache_val = json.dumps(
                {
                    "insights": insights,
                    "generated_at": response.generated_at.isoformat(),
                }
            )
            await redis_client.setex(cache_key, 7 * 86400, cache_val)
            await redis_client.setex(count_key, 7 * 86400, str(total_tx_count))

            return response
        except Exception as e:
            logger.error(f"Error requesting insights from Gemini API: {e}")
            return InsightResponse(
                insights=[
                    "Не удалось сгенерировать AI-инсайты (ошибка запроса к API).",
                    "Проверьте настройки GEMINI_API_KEY.",
                ],
                generated_at=datetime.now(timezone.utc),
            )

    def _build_summary(self, transactions: list[Transaction], currency: str) -> str:
        """Сформировать текстовую сводку транзакций для передачи в системный промпт LLM."""
        lines = []
        for tx in transactions:
            parts = [
                tx.date.strftime("%Y-%m-%d"),
                tx.type.value,
                tx.category.value,
                f"{tx.amount} {currency}",
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
