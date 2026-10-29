"""Модуль тестов для проверки единого контракта ошибок API и строковых кодов ошибок."""

import uuid

import pytest
from httpx import AsyncClient

from app.exceptions import ErrorCode


class TestErrorContract:
    """Проверка структуры ErrorResponse: status, code, message, details."""

    @pytest.mark.asyncio
    async def test_unauthorized_error_contract(self, client: AsyncClient):
        """Проверка структуры ошибки при отсутствии токена авторизации (403)."""
        response = await client.get("/api/v1/transactions/")
        assert response.status_code == 403
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.FORBIDDEN
        assert "message" in data
        assert isinstance(data["message"], str)

    @pytest.mark.asyncio
    async def test_validation_error_contract(self, client: AsyncClient):
        """Проверка структуры ошибки при невалидных входных данных (422)."""
        # Тело без обязательного поля id_token
        response = await client.post("/api/v1/auth/google", json={"name": "x"})
        assert response.status_code == 422
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.VALIDATION_ERROR
        assert "message" in data
        assert "details" in data
        assert "fields" in data["details"]

    @pytest.mark.asyncio
    async def test_oauth_invalid_credentials_error_code(self, client: AsyncClient):
        """Проверка кода ошибки INVALID_CREDENTIALS при недействительном ID-токене провайдера."""
        response = await client.post("/api/v1/auth/google", json={"id_token": "invalid-google-token"})
        assert response.status_code == 401
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.INVALID_CREDENTIALS
        assert data["message"] == "Недействительный токен Google"

    @pytest.mark.asyncio
    async def test_not_found_error_contract(self, client: AsyncClient):
        """Проверка структуры ошибки 404 (несуществующий URL маршрут)."""
        random_endpoint = f"/api/v1/unknown-route-{uuid.uuid4()}"
        response = await client.get(random_endpoint)
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.NOT_FOUND
        assert "message" in data
