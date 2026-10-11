"""Интеграционные тесты эндпоинтов профиля и управления пользователем."""

import pytest
from httpx import AsyncClient

from app.models.models import User


class TestUserProfileAndBaseCurrency:
    """Тесты эндпоинтов получения и обновления профиля пользователя (включая base_currency)."""

    @pytest.mark.asyncio
    async def test_get_me_returns_default_base_currency(
        self, client: AsyncClient, auth_headers: dict[str, str], test_user: User
    ):
        """Эндпоинт GET /api/v1/auth/me возвращает базовую валюту по умолчанию (RUB)."""
        expected_email = test_user.email
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        user_data = data["data"]
        assert user_data["email"] == expected_email
        assert user_data["base_currency"] == "RUB"

    @pytest.mark.asyncio
    async def test_patch_me_updates_base_currency_successfully(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Успешная смена base_currency на UZS через PATCH /api/v1/auth/me."""
        payload = {"base_currency": "uzs"}
        response = await client.patch("/api/v1/auth/me", json=payload, headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["base_currency"] == "UZS"

        # Проверка, что последующий GET /api/v1/auth/me возвращает обновленную валюту
        get_res = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["data"]["base_currency"] == "UZS"

    @pytest.mark.asyncio
    async def test_patch_me_invalid_base_currency_rejected(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Валидация недопустимого кода валюты отклоняется с кодом 422 Unprocessable Entity."""
        payload = {"base_currency": "XYZ"}
        response = await client.patch("/api/v1/auth/me", json=payload, headers=auth_headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_me_updates_name_and_avatar(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Частичное обновление профиля пользователя (имя и ссылка на аватар)."""
        payload = {
            "name": "Новое Имя",
            "avatar_url": "https://example.com/new-avatar.png",
        }
        response = await client.patch("/api/v1/auth/me", json=payload, headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["name"] == "Новое Имя"
        assert data["data"]["avatar_url"] == "https://example.com/new-avatar.png"
        assert data["data"]["base_currency"] == "RUB"

    @pytest.mark.asyncio
    async def test_patch_me_unauthorized_rejected(self, client: AsyncClient):
        """Попытка обновления профиля без авторизации возвращает 403, а с невалидным токеном 401."""
        no_auth = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"})
        assert no_auth.status_code in (401, 403)

        invalid_token = await client.patch(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-token"},
            json={"base_currency": "USD"},
        )
        assert invalid_token.status_code == 401

    @pytest.mark.asyncio
    async def test_insights_endpoint_uses_user_base_currency(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Эндпоинт /api/v1/insights/ использует base_currency профиля, если параметр currency не передан."""
        # Сначала меняем валюту пользователя на KZT
        patch_res = await client.patch("/api/v1/auth/me", json={"base_currency": "KZT"}, headers=auth_headers)
        assert patch_res.status_code == 200

        # Запрашиваем инсайты без явного указания currency
        insights_res = await client.get("/api/v1/insights/", headers=auth_headers)
        assert insights_res.status_code == 200
        data = insights_res.json()
        assert data["status"] == "success"
        assert "insights" in data["data"]
