"""Модуль автоматических тестов для проверки эндпоинтов API и авторизации.

Запуск тестов: pytest tests/ -v
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.models.models import User
from app.services.ai_service import AIService
from app.services.auth_service import get_current_user
from main import app


def get_auth_headers(token: str = "test-token") -> dict:
    """Сформировать HTTP-заголовки авторизации с Bearer токеном.

    Аргументы:
        token (str, optional): Значение токена. По умолчанию "test-token".

    Возвращает:
        dict: Словарь с заголовком Authorization.
    """
    return {"Authorization": f"Bearer {token}"}


class TestTransactions:
    """Набор тестов для роутера транзакций (/api/v1/transactions)."""

    @pytest.mark.asyncio
    async def test_create_transaction_unauthorized(self, client: AsyncClient):
        """Попытка создания транзакции без токена возвращает 403 Forbidden."""
        payload = {
            "amount": 500.0,
            "description": "Кофе",
            "category": "food",
            "type": "expense",
        }
        response = await client.post("/api/v1/transactions/", json=payload)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_transactions_unauthorized(self, client: AsyncClient):
        """Запрос списка транзакций без токена возвращает 403 Forbidden."""
        response = await client.get("/api/v1/transactions/")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Эндпоинт /health всегда доступен публично и возвращает 200 OK при доступной БД."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["database"] == "healthy"


class TestBudgets:
    """Набор тестов для роутера бюджетов (/api/v1/budgets)."""

    @pytest.mark.asyncio
    async def test_list_budgets_unauthorized(self, client: AsyncClient):
        """Запрос списка бюджетов без токена возвращает 403 Forbidden."""
        response = await client.get("/api/v1/budgets/")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_create_budget_unauthorized(self, client: AsyncClient):
        """Попытка создания бюджета без токена возвращает 403 Forbidden."""
        payload = {
            "category": "food",
            "limit_amount": 10000.0,
            "month": 9,
            "year": 2026,
        }
        response = await client.post("/api/v1/budgets/", json=payload)
        assert response.status_code == 403


class TestInsights:
    """Набор тестов для роутера AI-инсайтов (/api/v1/insights)."""

    @pytest.mark.asyncio
    async def test_insights_unauthorized(self, client: AsyncClient):
        """Запрос AI-инсайтов без токена возвращает 403 Forbidden."""
        response = await client.get("/api/v1/insights/")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_insights_authorized_success(self, client: AsyncClient):
        """Авторизованный запрос возвращает 200 OK и структуру ApiResponse[InsightResponse]."""
        test_user = MagicMock(spec=User)
        test_user.id = uuid.uuid4()
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_current_user] = lambda: test_user
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            response = await client.get("/api/v1/insights/", headers=get_auth_headers())
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert "insights" in data["data"]
            assert "generated_at" in data["data"]
            assert isinstance(data["data"]["insights"], list)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_insights_http_caching(self, client: AsyncClient):
        """Два последовательных HTTP-запроса проверяют работу кэширования через API."""
        test_user = MagicMock(spec=User)
        test_user.id = uuid.uuid4()
        mock_db = MagicMock()

        app.dependency_overrides[get_current_user] = lambda: test_user
        app.dependency_overrides[get_db] = lambda: mock_db

        with patch("app.services.ai_service.config.gemini_api_key", "valid-real-key"):
            with patch.object(
                AIService,
                "_call_gemini",
                new_callable=AsyncMock,
                return_value=["Совет 1", "Совет 2"],
            ) as mock_claude:
                tx = MagicMock()
                tx.date = MagicMock()
                tx.date.strftime.return_value = "2026-10-01"
                tx.type.value = "expense"
                tx.category.value = "food"
                tx.amount = 500.0
                tx.description = "Обед"

                mock_count = MagicMock()
                mock_count.scalar_one.return_value = 10
                mock_txs = MagicMock()
                mock_txs.scalars.return_value.all.return_value = [tx]
                mock_db.execute = AsyncMock(side_effect=[mock_count, mock_txs, mock_count, mock_txs])

                try:
                    # Первый HTTP-запрос (вызывает Claude API)
                    res1 = await client.get("/api/v1/insights/", headers=get_auth_headers())
                    assert res1.status_code == 200
                    assert res1.json()["data"]["insights"] == ["Совет 1", "Совет 2"]
                    assert mock_claude.await_count == 1

                    # Второй HTTP-запрос (кэш-хит)
                    res2 = await client.get("/api/v1/insights/", headers=get_auth_headers())
                    assert res2.status_code == 200
                    assert res2.json()["data"]["insights"] == ["Совет 1", "Совет 2"]
                    # Claude API НЕ вызывался повторно!
                    assert mock_claude.await_count == 1
                finally:
                    app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_insights_concurrent_with_health_check(self):
        """Параллельный вызов /api/v1/insights/ и /health подтверждает неблокируемость Event Loop."""
        test_user = MagicMock(spec=User)
        test_user.id = uuid.uuid4()
        mock_db = MagicMock()

        app.dependency_overrides[get_current_user] = lambda: test_user
        app.dependency_overrides[get_db] = lambda: mock_db

        async def slow_claude_call(*args, **kwargs):
            await asyncio.sleep(0.1)
            return ["Медленный совет"]

        with patch("app.services.ai_service.config.gemini_api_key", "valid-real-key"):
            with patch.object(AIService, "_call_gemini", side_effect=slow_claude_call):
                tx = MagicMock()
                tx.date = MagicMock()
                tx.date.strftime.return_value = "2026-10-01"
                tx.type.value = "expense"
                tx.category.value = "food"
                tx.amount = 300.0
                tx.description = "Кофе"

                mock_count = MagicMock()
                mock_count.scalar_one.return_value = 10
                mock_txs = MagicMock()
                mock_txs.scalars.return_value.all.return_value = [tx]
                mock_db.execute = AsyncMock(side_effect=[mock_count, mock_txs, mock_count, mock_txs])

                try:
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as ac:
                        insights_task = asyncio.create_task(ac.get("/api/v1/insights/", headers=get_auth_headers()))
                        # Даем задаче инсайтов начать выполнение
                        await asyncio.sleep(0.01)
                        # /health должен ответить сразу же
                        health_res = await ac.get("/health")
                        assert health_res.status_code == 200
                        assert health_res.json()["status"] == "ok"

                        insights_res = await insights_task
                        assert insights_res.status_code == 200
                        assert insights_res.json()["data"]["insights"] == ["Медленный совет"]
                finally:
                    app.dependency_overrides.clear()
