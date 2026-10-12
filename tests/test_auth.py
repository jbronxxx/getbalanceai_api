"""Интеграционные тесты эндпоинтов профиля и управления пользователем."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.models import User
from app.schemas.schemas import CurrencyRatesResponse
from app.services.currency_service import CurrencyService


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

    @pytest.mark.asyncio
    async def test_patch_me_recalculates_transactions_and_budgets(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Смена base_currency пересчитывает exchange_rate и base_amount транзакций и limit_amount бюджетов."""
        # 1. Создаем транзакцию в RUB
        tx_payload = {
            "amount": "960.00",
            "currency": "RUB",
            "description": "Продукты",
            "category": "food",
            "type": "expense",
        }
        tx_res = await client.post("/api/v1/transactions/", json=tx_payload, headers=auth_headers)
        assert tx_res.status_code == 201
        tx_data = tx_res.json()["data"]
        assert float(tx_data["base_amount"]) == 960.00

        # 2. Создаем бюджет в RUB
        budget_payload = {
            "category": "food",
            "limit_amount": "9600.00",
            "month": 10,
            "year": 2026,
        }
        b_res = await client.post("/api/v1/budgets/", json=budget_payload, headers=auth_headers)
        assert b_res.status_code == 201

        # Фиксируем котировки через мок CurrencyService.get_rates
        mock_response = CurrencyRatesResponse(
            base="USD",
            rates={
                "USD": Decimal("1.0"),
                "EUR": Decimal("0.92"),
                "RUB": Decimal("96.0"),
                "UZS": Decimal("12800.0"),
                "KZT": Decimal("495.0"),
            },
            updated_at=datetime.now(timezone.utc),
        )
        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_response):
            # 3. Меняем базовую валюту на USD через PATCH /api/v1/auth/me
            patch_res = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            assert patch_res.status_code == 200
            assert patch_res.json()["data"]["base_currency"] == "USD"

            # 4. Проверяем, что транзакция пересчиталась в USD:
            # 960 RUB по курсу к USD (1.0 / 96.0) = 10.00 USD
            tx_list_res = await client.get("/api/v1/transactions/", headers=auth_headers)
            assert tx_list_res.status_code == 200
            items = tx_list_res.json()["data"]["items"]
            assert len(items) >= 1
            updated_tx = next(item for item in items if item["id"] == tx_data["id"])
            assert float(updated_tx["base_amount"]) == 10.00

            # 5. Проверяем, что бюджет пересчитался в USD:
            # 9600 RUB по курсу к USD (1.0 / 96.0) = 100.00 USD
            b_list_res = await client.get("/api/v1/budgets/?month=10&year=2026", headers=auth_headers)
            assert b_list_res.status_code == 200
            budgets = b_list_res.json()["data"]
            assert len(budgets) >= 1
            updated_b = next(b for b in budgets if b["category"] == "food")
            assert float(updated_b["limit_amount"]) == 100.00
            assert float(updated_b["spent"]) == 10.00

    @pytest.mark.asyncio
    async def test_patch_me_invalidates_ai_insights_cache(
        self, client: AsyncClient, auth_headers: dict[str, str], test_user: User
    ):
        """Смена базовой валюты пользователя инвалидирует все ключи кэша инсайтов в Redis."""
        from app.services.ai_service import redis_client

        cache_key = f"insights_cache:{test_user.id}:RUB:ru"
        count_key = f"insights_tx_count:{test_user.id}"
        await redis_client.set(cache_key, "cached_insights")
        await redis_client.set(count_key, "10")

        mock_response = CurrencyRatesResponse(
            base="USD",
            rates={"USD": Decimal("1.0"), "RUB": Decimal("96.0")},
            updated_at=datetime.now(timezone.utc),
        )
        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_response):
            patch_res = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            assert patch_res.status_code == 200

        # Ключи инсайтов должны быть удалены после смены валюты
        assert await redis_client.get(cache_key) is None
        assert await redis_client.get(count_key) is None

    @pytest.mark.asyncio
    async def test_patch_me_currency_change_reversible_rub_usd_rub(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Цикл смены валюты RUB -> USD -> RUB полностью обратим для транзакций в RUB без потери точности."""
        # Создаем транзакцию в RUB
        tx_rub_res = await client.post(
            "/api/v1/transactions/",
            json={
                "amount": "1000.00",
                "currency": "RUB",
                "description": "Транзакция в рублях",
                "category": "food",
                "type": "expense",
            },
            headers=auth_headers,
        )
        assert tx_rub_res.status_code == 201
        tx_rub_id = tx_rub_res.json()["data"]["id"]

        # Создаем транзакцию в USD
        tx_usd_res = await client.post(
            "/api/v1/transactions/",
            json={
                "amount": "50.00",
                "currency": "USD",
                "description": "Транзакция в долларах",
                "category": "shopping",
                "type": "expense",
            },
            headers=auth_headers,
        )
        assert tx_usd_res.status_code == 201
        tx_usd_id = tx_usd_res.json()["data"]["id"]

        mock_response = CurrencyRatesResponse(
            base="USD",
            rates={"USD": Decimal("1.0"), "RUB": Decimal("100.0")},
            updated_at=datetime.now(timezone.utc),
        )

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_response):
            # 1. Смена базовой валюты на USD
            to_usd_res = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            assert to_usd_res.status_code == 200

            tx_list_usd = await client.get("/api/v1/transactions/", headers=auth_headers)
            items_usd = {item["id"]: item for item in tx_list_usd.json()["data"]["items"]}

            # Транзакция в новой базе (USD) получает exchange_rate = 1.0 и base_amount = 50.00
            assert float(items_usd[tx_usd_id]["exchange_rate"]) == 1.0
            assert float(items_usd[tx_usd_id]["base_amount"]) == 50.00
            # Транзакция в RUB сконвертирована: 1000 RUB / 100 = 10.00 USD
            assert float(items_usd[tx_rub_id]["base_amount"]) == 10.00

            # 2. Смена базовой валюты обратно на RUB
            to_rub_res = await client.patch("/api/v1/auth/me", json={"base_currency": "RUB"}, headers=auth_headers)
            assert to_rub_res.status_code == 200

            tx_list_rub = await client.get("/api/v1/transactions/", headers=auth_headers)
            items_rub = {item["id"]: item for item in tx_list_rub.json()["data"]["items"]}

            # Транзакция в RUB вернулась строго к исходному состоянию без дрейфа:
            assert float(items_rub[tx_rub_id]["exchange_rate"]) == 1.0
            assert float(items_rub[tx_rub_id]["base_amount"]) == 1000.00
            # Транзакция в USD сконвертирована по актуальному курсу (50 USD * 100 = 5000.00 RUB)
            assert float(items_rub[tx_usd_id]["exchange_rate"]) == 100.0
            assert float(items_rub[tx_usd_id]["base_amount"]) == 5000.00
