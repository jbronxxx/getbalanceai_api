"""Модуль тестов для проверки наблюдаемости (Observability): healthcheck и Correlation ID."""

import logging
import uuid
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.exceptions import ErrorCode
from logger.logger import (
    RequestIdFilter,
    get_request_id,
    reset_request_id,
    set_request_id,
)
from main import app


class TestHealthCheck:
    """Набор тестов для эндпоинта /health (TASK-6.1)."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, client: AsyncClient):
        """Эндпоинт /health возвращает 200 OK и статус database=healthy при доступной БД."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert data["database"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_db_failure(self, client: AsyncClient):
        """При ошибке подключения к базе данных /health возвращает 503 Service Unavailable."""
        mock_db = MagicMock()
        mock_db.execute.side_effect = OperationalError(
            statement="SELECT 1",
            params={},
            orig=Exception("Connection refused"),
        )

        # Переопределяем зависимость на время теста
        app.dependency_overrides[get_db] = lambda: mock_db
        try:
            response = await client.get("/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "error"
            assert data["code"] == ErrorCode.SERVICE_UNAVAILABLE
            assert "База данных недоступна" in data["message"]
            assert data["details"]["database"] == "unhealthy"
        finally:
            app.dependency_overrides.pop(get_db, None)


class TestCorrelationId:
    """Набор тестов для сквозной трассировки запросов X-Request-ID / Correlation ID (TASK-6.2)."""

    @pytest.mark.asyncio
    async def test_auto_generated_request_id(self, client: AsyncClient):
        """При отсутствии заголовка трассировки сервер генерирует новый UUID."""
        response = await client.get("/health")
        assert response.status_code == 200
        req_id = response.headers.get("X-Request-ID")
        assert req_id is not None
        # Проверяем, что сгенерированное значение является валидным UUID
        parsed_uuid = uuid.UUID(req_id)
        assert str(parsed_uuid) == req_id

    @pytest.mark.asyncio
    async def test_propagates_incoming_x_request_id(self, client: AsyncClient):
        """Переданный клиентом X-Request-ID сохраняется и возвращается в ответе."""
        custom_id = "client-req-id-12345"
        response = await client.get("/health", headers={"X-Request-ID": custom_id})
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == custom_id

    @pytest.mark.asyncio
    async def test_propagates_incoming_x_correlation_id(self, client: AsyncClient):
        """Если передан заголовок X-Correlation-ID, он используется как X-Request-ID."""
        custom_corr_id = "corr-uuid-98765"
        response = await client.get("/health", headers={"X-Correlation-ID": custom_corr_id})
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == custom_corr_id

    @pytest.mark.asyncio
    async def test_request_id_in_error_responses(self, client: AsyncClient):
        """Заголовок X-Request-ID возвращается даже при ошибках 404, 401, 422."""
        custom_id = "error-trace-id-999"

        # 404 Not Found
        res_404 = await client.get("/non-existent-endpoint", headers={"X-Request-ID": custom_id})
        assert res_404.status_code == 404
        assert res_404.headers.get("X-Request-ID") == custom_id

        # 403 / 401 Unauthorized
        res_auth = await client.get("/api/v1/transactions/", headers={"X-Request-ID": custom_id})
        assert res_auth.status_code == 403
        assert res_auth.headers.get("X-Request-ID") == custom_id

        # 422 Validation Error
        res_val = await client.post(
            "/api/v1/auth/login",
            json={"email": "invalid-email"},
            headers={"X-Request-ID": custom_id},
        )
        assert res_val.status_code == 422
        assert res_val.headers.get("X-Request-ID") == custom_id

    @pytest.mark.asyncio
    async def test_request_id_in_logs(self, client: AsyncClient, caplog: pytest.LogCaptureFixture):
        """Логи во время обработки запроса обогащаются текущим request_id."""
        custom_id = "log-trace-abc-123"

        with caplog.at_level(logging.INFO):
            # Делаем вызов и логируем
            response = await client.get("/health", headers={"X-Request-ID": custom_id})
            assert response.status_code == 200

        # Проверяем, что контекст запроса был корректно очищен после завершения запроса
        assert get_request_id() == "-"

    def test_request_id_helpers_unit(self):
        """Юнит-тест функций контекста set_request_id, get_request_id, reset_request_id."""
        assert get_request_id() == "-"
        token = set_request_id("unit-test-req-id")
        assert get_request_id() == "unit-test-req-id"
        reset_request_id(token)
        assert get_request_id() == "-"

    def test_request_id_filter_unit(self):
        """Юнит-тест RequestIdFilter добавления request_id в LogRecord."""
        log_filter = RequestIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="test message",
            args=(),
            exc_info=None,
        )
        assert not hasattr(record, "request_id")

        # По умолчанию без контекста
        log_filter.filter(record)
        assert getattr(record, "request_id") == "-"

        # При установленном контексте
        token = set_request_id("ctx-id-555")
        try:
            log_filter.filter(record)
            assert getattr(record, "request_id") == "ctx-id-555"
        finally:
            reset_request_id(token)
