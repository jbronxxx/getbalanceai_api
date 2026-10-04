"""Модуль модульных тестов для AIService."""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google import genai

from app.models.models import Category, Transaction, TransactionType
from app.schemas.schemas import InsightResponse
from app.services.ai_service import AIService


class TestAIService:
    """Набор тестов для AIService."""

    def test_init_creates_generative_model(self):
        """Проверка, что AIService инициализирует клиент Gemini."""
        mock_db = MagicMock()
        service = AIService(db=mock_db)

        assert isinstance(service.client, genai.Client)
        assert service.db is mock_db

    def test_init_accepts_custom_client(self):
        """Проверка возможности передать собственный клиент (Dependency Injection)."""
        mock_db = MagicMock()
        custom_client = MagicMock(spec=genai.Client)
        service = AIService(db=mock_db, client=custom_client)

        assert service.client is custom_client

    # =========================================================================
    # TASK-1.2: Тестирование надежного парсинга JSON
    # =========================================================================

    def test_extract_json_pure_json(self):
        """Проверка парсинга чистого JSON."""
        raw = '{"insights": ["Совет 1", "Совет 2"]}'
        result = AIService._extract_json(raw)
        assert result == {"insights": ["Совет 1", "Совет 2"]}

    def test_extract_json_markdown_block(self):
        """Проверка извлечения JSON из markdown-блока ```json ... ```."""
        raw = '```json\n{"insights": ["Совет 1", "Совет 2"]}\n```'
        result = AIService._extract_json(raw)
        assert result == {"insights": ["Совет 1", "Совет 2"]}

    def test_extract_json_markdown_block_without_tag(self):
        """Проверка извлечения JSON из markdown-блока без указания языка ``` ... ```."""
        raw = '```\n{"insights": ["Совет 1"]}\n```'
        result = AIService._extract_json(raw)
        assert result == {"insights": ["Совет 1"]}

    def test_extract_json_with_conversational_text(self):
        """Проверка извлечения JSON при наличии вводного и завершающего текста модели."""
        raw = 'Конечно!\n```json\n{\n  "insights": [\n    "Оптимизируйте"\n  ]\n}\n```\nНадеюсь!'
        result = AIService._extract_json(raw)
        assert result == {"insights": ["Оптимизируйте"]}

    def test_extract_json_embedded_brackets_without_code_block(self):
        """Проверка извлечения JSON, заключенного в фигурные скобки посреди текста."""
        raw = 'Вот ваш результат: {"insights": ["Экономьте на такси"]} Всего доброго!'
        result = AIService._extract_json(raw)
        assert result == {"insights": ["Экономьте на такси"]}

    def test_extract_json_invalid_raises_error(self):
        """Если валидный JSON отсутствует, выбрасывается JSONDecodeError."""
        raw = "Извините, я не могу проанализировать эти транзакции."
        with pytest.raises(json.JSONDecodeError):
            AIService._extract_json(raw)

    # =========================================================================
    # TASK-1.1 & TASK-1.3: Вызов Gemini, асинхронность и UTC время
    # =========================================================================

    @pytest.mark.asyncio
    async def test_call_gemini_uses_awaited_async_client(self):
        """Проверка, что _call_gemini асинхронно вызывает generate_content."""
        mock_db = MagicMock()
        mock_client = MagicMock(spec=genai.Client)
        mock_response = MagicMock()
        mock_response.text = '{"insights": ["Снизь расходы на кафе", "Откладывай 10%", "Инвестируй остаток"]}'

        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        service = AIService(db=mock_db, client=mock_client)
        summary = "2026-10-01 | expense | food | 500.0 руб. | Обед"

        insights = await service._call_gemini(summary, locale="ru", currency="RUB")

        assert insights == [
            "Снизь расходы на кафе",
            "Откладывай 10%",
            "Инвестируй остаток",
        ]
        mock_client.aio.models.generate_content.assert_awaited_once()
        create_kwargs = mock_client.aio.models.generate_content.call_args.kwargs
        assert "2026-10-01" in create_kwargs["contents"]

    @pytest.mark.asyncio
    async def test_get_insights_placeholder_api_key_returns_utc_datetime(self):
        """Если API ключ - плейсхолдер, возвращается заглушка с timezone-aware UTC datetime."""
        mock_db = MagicMock()
        service = AIService(db=mock_db)

        with patch("app.services.ai_service.config.gemini_api_key", "sk-your-key-here"):
            result = await service.get_insights(uuid.uuid4())

        assert isinstance(result, InsightResponse)
        assert result.insights == ["В разработке..."]
        assert result.generated_at.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_get_insights_empty_api_key(self):
        """Если API ключ пустой, возвращается заглушка."""
        mock_db = MagicMock()
        service = AIService(db=mock_db)

        with patch("app.services.ai_service.config.gemini_api_key", ""):
            result = await service.get_insights(uuid.uuid4())

        assert isinstance(result, InsightResponse)
        assert result.insights == ["В разработке..."]
        assert result.generated_at.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_get_insights_no_transactions(self):
        """Если у пользователя нет транзакций, возвращается совет добавить первую транзакцию."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute = AsyncMock(return_value=mock_result)

        service = AIService(db=mock_db)

        with patch("app.services.ai_service.config.gemini_api_key", "valid-real-key"):
            result = await service.get_insights(uuid.uuid4())

        assert isinstance(result, InsightResponse)
        assert result.insights == ["Добавь первые транзакции, чтобы получить анализ."]
        assert result.generated_at.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_get_insights_success_with_transactions(self):
        """Успешное получение инсайтов при наличии транзакций."""
        tx1 = MagicMock(spec=Transaction)
        tx1.date = datetime(2026, 10, 1, 12, 0)
        tx1.type = TransactionType.expense
        tx1.category = Category.food
        tx1.amount = 1200.0
        tx1.description = "Супермаркет"

        mock_db = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 5
        mock_tx_result = MagicMock()
        mock_tx_result.scalars.return_value.all.return_value = [tx1]

        mock_db.execute = AsyncMock(side_effect=[mock_count_result, mock_tx_result])

        mock_client = MagicMock(spec=genai.Client)
        mock_response = MagicMock()
        mock_response.text = '{"insights": ["Траты на еду в норме", "Планируйте бюджет на неделю"]}'
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        service = AIService(db=mock_db, client=mock_client)

        with patch("app.services.ai_service.config.gemini_api_key", "valid-real-key"):
            with patch("app.services.ai_service.redis_client.get", AsyncMock(return_value=None)):
                result = await service.get_insights(uuid.uuid4())

        assert isinstance(result, InsightResponse)
        assert result.insights == [
            "Траты на еду в норме",
            "Планируйте бюджет на неделю",
        ]
        assert result.generated_at.tzinfo == timezone.utc
        mock_client.aio.models.generate_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_insights_api_error_fallback(self):
        """Если вызов Gemini API завершился ошибкой, возвращается сообщение об ошибке."""
        tx1 = MagicMock(spec=Transaction)
        tx1.date = datetime(2026, 10, 1, 12, 0)
        tx1.type = TransactionType.expense
        tx1.category = Category.food
        tx1.amount = 500.0
        tx1.description = "Кофе"

        mock_db = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 1
        mock_tx_result = MagicMock()
        mock_tx_result.scalars.return_value.all.return_value = [tx1]
        mock_db.execute = AsyncMock(side_effect=[mock_count_result, mock_tx_result])

        mock_client = MagicMock(spec=genai.Client)
        mock_client.aio.models.generate_content = AsyncMock(side_effect=Exception("API Error"))

        service = AIService(db=mock_db, client=mock_client)

        with patch("app.services.ai_service.config.gemini_api_key", "valid-real-key"):
            with patch("app.services.ai_service.redis_client.get", AsyncMock(return_value=None)):
                result = await service.get_insights(uuid.uuid4())

        assert isinstance(result, InsightResponse)
        assert "Не удалось сгенерировать AI-инсайты" in result.insights[0]
        assert result.generated_at.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_block_event_loop(self):
        """Проверка, что параллельные вызовы не блокируют Event Loop."""
        mock_db = MagicMock()
        mock_client = MagicMock(spec=genai.Client)

        async def simulated_async_network_call(*args, **kwargs):
            await asyncio.sleep(0.1)
            mock_resp = MagicMock()
            mock_resp.text = '{"insights": ["Тест параллельности"]}'
            return mock_resp

        mock_client.aio.models.generate_content = AsyncMock(side_effect=simulated_async_network_call)

        service = AIService(db=mock_db, client=mock_client)

        start_time = time.monotonic()
        results = await asyncio.gather(
            service._call_gemini("summary 1", locale="ru", currency="RUB"),
            service._call_gemini("summary 2", locale="ru", currency="RUB"),
            service._call_gemini("summary 3", locale="ru", currency="RUB"),
        )
        duration = time.monotonic() - start_time

        assert len(results) == 3
        for res in results:
            assert res == ["Тест параллельности"]

        assert duration < 0.2, f"Запросы выполнялись последовательно: заняло {duration}s"

    # =========================================================================
    # TASK-5.3: Тестирование кэширования инсайтов (Threshold)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_insights_caching_returns_cached_on_small_tx_change(self):
        """Если транзакции изменились меньше порога, возвращается кэш."""
        user_id = uuid.uuid4()

        mock_db = MagicMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 10  # 10 транзакций
        mock_db.execute = AsyncMock(return_value=mock_count_result)

        service = AIService(db=mock_db)

        # Мокаем redis
        cached_json = json.dumps({"insights": ["Cached"], "generated_at": datetime.now(timezone.utc).isoformat()})
        # last_tx_count = 8. (10 - 8 = 2 < 5 Threshold)

        async def mock_redis_get(key):
            if "insights_cache" in key:
                return cached_json
            if "insights_tx_count" in key:
                return "8"
            return None

        with patch("app.services.ai_service.config.gemini_api_key", "valid-real-key"):
            with patch("app.services.ai_service.redis_client.get", side_effect=mock_redis_get):
                resp = await service.get_insights(user_id)
                assert resp.insights == ["Cached"]
                # Убедимся что к БД за транзакциями (limit 50) запроса не было
                assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_and_clear_cache(self):
        """Проверка методов ручной инвалидации кэша."""
        from app.services.ai_service import redis_client

        u1 = uuid.uuid4()
        u2 = uuid.uuid4()

        await redis_client.set(f"insights_cache:{u1}", "data1")
        await redis_client.set(f"insights_cache:{u2}", "data2")

        await AIService.invalidate_cache(u1)
        assert await redis_client.get(f"insights_cache:{u1}") is None
        assert await redis_client.get(f"insights_cache:{u2}") == "data2"

        await AIService.clear_cache()
        assert await redis_client.get(f"insights_cache:{u2}") is None
