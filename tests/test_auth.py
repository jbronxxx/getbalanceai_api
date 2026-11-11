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
        """Эндпоинт GET /api/v1/auth/me возвращает базовую валюту по умолчанию (USD)."""
        expected_email = test_user.email
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        user_data = data["data"]
        assert user_data["email"] == expected_email
        assert user_data["base_currency"] == "USD"

    @pytest.mark.asyncio
    async def test_patch_me_updates_base_currency_successfully(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Успешная смена base_currency на EUR через PATCH /api/v1/auth/me."""
        payload = {"base_currency": "eur"}
        response = await client.patch("/api/v1/auth/me", json=payload, headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["base_currency"] == "EUR"

        # Проверка, что последующий GET /api/v1/auth/me возвращает обновленную валюту
        get_res = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["data"]["base_currency"] == "EUR"

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
        assert data["data"]["base_currency"] == "USD"

    @pytest.mark.asyncio
    async def test_patch_me_name_trimmed_and_empty_rejected(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Имя сохраняется без пробелов по краям; пустое или из одних пробелов — 422, старое имя остаётся."""
        response = await client.patch("/api/v1/auth/me", json={"name": "  Аня  "}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["data"]["name"] == "Аня"

        for empty in ("", "   "):
            response = await client.patch("/api/v1/auth/me", json={"name": empty}, headers=auth_headers)
            assert response.status_code == 422

        me = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert me.json()["data"]["name"] == "Аня"

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
        # Сначала меняем валюту пользователя на EUR
        patch_res = await client.patch("/api/v1/auth/me", json={"base_currency": "EUR"}, headers=auth_headers)
        assert patch_res.status_code == 200

        # Запрашиваем инсайты без явного указания currency
        insights_res = await client.get("/api/v1/insights/", headers=auth_headers)
        assert insights_res.status_code == 200
        data = insights_res.json()
        assert data["status"] == "success"
        assert "insights" in data["data"]

    @pytest.mark.asyncio
    async def test_patch_me_recalculates_transactions_and_keeps_budget_currency(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Смена base_currency пересчитывает base_amount транзакций, а лимиты остаются в своей валюте."""
        mock_response = CurrencyRatesResponse(
            base="USD",
            rates={
                "USD": Decimal("1.0"),
                "EUR": Decimal("0.8"),
            },
            updated_at=datetime.now(timezone.utc),
        )
        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_response):
            # 1. Устанавливаем базовую валюту EUR до создания данных
            to_eur_res = await client.patch("/api/v1/auth/me", json={"base_currency": "EUR"}, headers=auth_headers)
            assert to_eur_res.status_code == 200

            # 2. Создаем транзакцию и бюджет в EUR
            tx_payload = {
                "amount": "80.00",
                "currency": "EUR",
                "description": "Продукты",
                "category": "food",
                "type": "expense",
            }
            tx_res = await client.post("/api/v1/transactions/", json=tx_payload, headers=auth_headers)
            assert tx_res.status_code == 201
            tx_data = tx_res.json()["data"]
            assert float(tx_data["base_amount"]) == 80.00

            # Период лимита — текущий месяц: транзакция создана без даты, то есть сейчас
            now = datetime.now(timezone.utc)
            budget_payload = {
                "category": "food",
                "limit_amount": "800.00",
                "month": now.month,
                "year": now.year,
            }
            b_res = await client.post("/api/v1/budgets/", json=budget_payload, headers=auth_headers)
            assert b_res.status_code == 201

            # 3. Меняем базовую валюту на USD через PATCH /api/v1/auth/me
            patch_res = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            assert patch_res.status_code == 200
            assert patch_res.json()["data"]["base_currency"] == "USD"

            # 4. 80 EUR по курсу к USD (1.0 / 0.8) = 100.00 USD
            tx_list_res = await client.get("/api/v1/transactions/", headers=auth_headers)
            assert tx_list_res.status_code == 200
            items = tx_list_res.json()["data"]["items"]
            assert len(items) >= 1
            updated_tx = next(item for item in items if item["id"] == tx_data["id"])
            assert float(updated_tx["base_amount"]) == 100.00

            # 5. Лимит создан без валюты — в тогдашней базовой EUR — и не пересчитывается
            b_list_res = await client.get(f"/api/v1/budgets/?month={now.month}&year={now.year}", headers=auth_headers)
            assert b_list_res.status_code == 200
            budgets = b_list_res.json()["data"]
            assert len(budgets) >= 1
            updated_b = next(b for b in budgets if b["category"] == "food")
            assert updated_b["currency"] == "EUR"
            assert Decimal(str(updated_b["limit_amount"])) == Decimal("800.00")
            # Трата в валюте лимита считается по сумме чека
            assert Decimal(str(updated_b["spent"])) == Decimal("80.00")

    @pytest.mark.asyncio
    async def test_patch_me_invalidates_ai_insights_cache(
        self, client: AsyncClient, auth_headers: dict[str, str], test_user: User
    ):
        """Смена базовой валюты пользователя инвалидирует все ключи кэша инсайтов в Redis."""
        from app.services.ai_service import redis_client

        cache_key = f"insights_cache:{test_user.id}:EUR:ru"
        count_key = f"insights_tx_count:{test_user.id}"
        await redis_client.set(cache_key, "cached_insights")
        await redis_client.set(count_key, "10")

        mock_response = CurrencyRatesResponse(
            base="USD",
            rates={"USD": Decimal("1.0"), "EUR": Decimal("0.92")},
            updated_at=datetime.now(timezone.utc),
        )
        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_response):
            patch_res = await client.patch("/api/v1/auth/me", json={"base_currency": "EUR"}, headers=auth_headers)
            assert patch_res.status_code == 200

        # Ключи инсайтов должны быть удалены после смены валюты
        assert await redis_client.get(cache_key) is None
        assert await redis_client.get(count_key) is None

    @pytest.mark.asyncio
    async def test_patch_me_currency_change_reversible_eur_usd_eur(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Цикл смены валюты EUR -> USD -> EUR полностью обратим для транзакций в EUR без потери точности."""
        mock_response = CurrencyRatesResponse(
            base="USD",
            rates={"USD": Decimal("1.0"), "EUR": Decimal("0.5")},
            updated_at=datetime.now(timezone.utc),
        )

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_response):
            # 0. Базовая валюта по умолчанию USD: переводим пользователя на EUR до создания данных
            to_eur_init = await client.patch("/api/v1/auth/me", json={"base_currency": "EUR"}, headers=auth_headers)
            assert to_eur_init.status_code == 200

            # Создаем транзакцию в EUR
            tx_eur_res = await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": "1000.00",
                    "currency": "EUR",
                    "description": "Транзакция в евро",
                    "category": "food",
                    "type": "expense",
                },
                headers=auth_headers,
            )
            assert tx_eur_res.status_code == 201
            tx_eur_id = tx_eur_res.json()["data"]["id"]

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

            # 1. Смена базовой валюты на USD
            to_usd_res = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            assert to_usd_res.status_code == 200

            tx_list_usd = await client.get("/api/v1/transactions/", headers=auth_headers)
            items_usd = {item["id"]: item for item in tx_list_usd.json()["data"]["items"]}

            # Транзакция в новой базе (USD) получает exchange_rate = 1.0 и base_amount = 50.00
            assert float(items_usd[tx_usd_id]["exchange_rate"]) == 1.0
            assert float(items_usd[tx_usd_id]["base_amount"]) == 50.00
            # Транзакция в EUR сконвертирована: 1000 EUR / 0.5 = 2000.00 USD
            assert float(items_usd[tx_eur_id]["base_amount"]) == 2000.00

            # 2. Смена базовой валюты обратно на EUR
            to_eur_res = await client.patch("/api/v1/auth/me", json={"base_currency": "EUR"}, headers=auth_headers)
            assert to_eur_res.status_code == 200

            tx_list_eur = await client.get("/api/v1/transactions/", headers=auth_headers)
            items_eur = {item["id"]: item for item in tx_list_eur.json()["data"]["items"]}

            # Транзакция в EUR вернулась строго к исходному состоянию без дрейфа:
            assert float(items_eur[tx_eur_id]["exchange_rate"]) == 1.0
            assert float(items_eur[tx_eur_id]["base_amount"]) == 1000.00
            # Транзакция в USD сконвертирована по актуальному курсу (50 USD * 0.5 = 25.00 EUR)
            assert float(items_eur[tx_usd_id]["exchange_rate"]) == 0.5
            assert float(items_eur[tx_usd_id]["base_amount"]) == 25.00


class TestGoogleAuthEndpoint:
    """Интеграционные тесты для эндпоинта POST /api/v1/auth/google."""

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_endpoint_success(self, mock_jwt_decode, mock_jwk_client, client: AsyncClient):
        """Успешная авторизация через Google возвращает токены и объект профиля пользователя."""
        mock_jwt_decode.return_value = {
            "email": "api_google_user@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": "API Google User",
            "picture": "https://lh3.googleusercontent.com/avatar.png",
        }

        payload = {"id_token": "valid_mock_token"}
        response = await client.post("/api/v1/auth/google", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        token_data = data["data"]
        assert "access_token" in token_data
        assert "refresh_token" in token_data
        assert token_data["token_type"] == "bearer"
        assert token_data["user"] is not None
        assert token_data["user"]["email"] == "api_google_user@example.com"
        assert token_data["user"]["avatar_url"] == "https://lh3.googleusercontent.com/avatar.png"
        assert token_data["user"]["name"] == "API Google User"

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_endpoint_with_fallback_payload(self, mock_jwt_decode, mock_jwk_client, client: AsyncClient):
        """Передача avatar_url и name в теле запроса работает как fallback, если в Google JWT они отсутствуют."""
        mock_jwt_decode.return_value = {
            "email": "fallback_api_user@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": None,
            "picture": None,
        }

        payload = {
            "id_token": "valid_mock_token",
            "avatar_url": "https://example.com/client_avatar.png",
            "name": "Client Name",
        }
        response = await client.post("/api/v1/auth/google", json=payload)
        assert response.status_code == 200

        data = response.json()
        token_data = data["data"]
        assert token_data["user"]["avatar_url"] == "https://example.com/client_avatar.png"
        assert token_data["user"]["name"] == "Client Name"

    @pytest.mark.asyncio
    async def test_google_login_endpoint_validation_error(self, client: AsyncClient):
        """Передача слишком длинного avatar_url вызывает ошибку валидации 422."""
        payload = {
            "id_token": "valid_mock_token",
            "avatar_url": "https://example.com/" + "a" * 501,
        }
        response = await client.post("/api/v1/auth/google", json=payload)
        assert response.status_code == 422
