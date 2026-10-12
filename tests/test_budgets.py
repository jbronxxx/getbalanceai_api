"""Модуль автоматических тестов для проверки эндпоинтов удаления и синхронизации бюджетов.

Запуск тестов: pytest tests/ -v
"""

import uuid

import pytest
from httpx import AsyncClient


class TestBudgets:
    """Набор тестов для роутера бюджетов (/api/v1/budgets)."""

    @pytest.mark.asyncio
    async def test_delete_budget_unauthorized(self, client: AsyncClient):
        """Попытка удаления бюджета без токена возвращает 403 Forbidden."""
        random_id = uuid.uuid4()
        response = await client.delete(f"/api/v1/budgets/{random_id}")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_budget_by_category_unauthorized(self, client: AsyncClient):
        """Попытка удаления бюджета по категории без токена возвращает 403 Forbidden."""
        response = await client.delete("/api/v1/budgets/category/food/9/2026")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_budget_by_id_unauthorized(self, client: AsyncClient):
        """Запрос бюджета по ID без токена возвращает 403 Forbidden."""
        random_id = uuid.uuid4()
        response = await client.get(f"/api/v1/budgets/{random_id}")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_budget_limits_recalculated_and_etag_invalidated_on_base_currency_change(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """При смене base_currency пользователя лимиты бюджетов пересчитываются, а ETag инвалидируется."""
        from datetime import datetime, timezone
        from decimal import Decimal
        from unittest.mock import AsyncMock, patch

        from app.schemas.schemas import CurrencyRatesResponse
        from app.services.currency_service import CurrencyService

        mock_rates = CurrencyRatesResponse(
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

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_rates):
            # 1. Устанавливаем базовую валюту USD
            patch_user = await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            assert patch_user.status_code == 200
            assert patch_user.json()["data"]["base_currency"] == "USD"

            # 2. Создаем бюджет в USD с лимитом 100.00
            create_res = await client.post(
                "/api/v1/budgets/",
                json={"category": "food", "limit_amount": 100.00, "month": 10, "year": 2026},
                headers=auth_headers,
            )
            assert create_res.status_code == 201

            # 3. Запрашиваем бюджеты и получаем начальный ETag
            get_res = await client.get("/api/v1/budgets/?month=10&year=2026", headers=auth_headers)
            assert get_res.status_code == 200
            initial_etag = get_res.headers.get("ETag")
            assert initial_etag is not None
            assert Decimal(str(get_res.json()["data"][0]["limit_amount"])) == Decimal("100.00")

            # 4. Повторный GET с If-None-Match возвращает 304 Not Modified
            cached_res = await client.get(
                "/api/v1/budgets/?month=10&year=2026",
                headers={**auth_headers, "If-None-Match": initial_etag},
            )
            assert cached_res.status_code == 304

            # 5. Меняем базовую валюту на UZS: 100 USD должно стать 1 280 000 UZS
            patch_to_uzs = await client.patch("/api/v1/auth/me", json={"base_currency": "UZS"}, headers=auth_headers)
            assert patch_to_uzs.status_code == 200
            assert patch_to_uzs.json()["data"]["base_currency"] == "UZS"

            # 6. Запрос с СТАРЫМ If-None-Match (initial_etag) НЕ должен вернуть 304!
            # ETag инвалидирован, поэтому сервер обязан вернуть 200 OK с пересчитанным лимитом и новым ETag
            recalc_res = await client.get(
                "/api/v1/budgets/?month=10&year=2026",
                headers={**auth_headers, "If-None-Match": initial_etag},
            )
            assert recalc_res.status_code == 200
            new_etag = recalc_res.headers.get("ETag")
            assert new_etag is not None
            assert new_etag != initial_etag

            budgets_data = recalc_res.json()["data"]
            assert len(budgets_data) == 1
            # 100.00 USD * 12800.0 = 1280000.00 UZS
            assert Decimal(str(budgets_data[0]["limit_amount"])) == Decimal("1280000.00")

            # 7. Новый ETag успешно кэшируется (304)
            cached_new_res = await client.get(
                "/api/v1/budgets/?month=10&year=2026",
                headers={**auth_headers, "If-None-Match": new_etag},
            )
            assert cached_new_res.status_code == 304

    @pytest.mark.asyncio
    async def test_multicurrency_spent_and_remaining_calculation(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Сервер корректно конвертирует мультивалютные транзакции при подсчете spent и remaining."""
        from datetime import datetime, timezone
        from decimal import Decimal
        from unittest.mock import AsyncMock, patch

        from app.schemas.schemas import CurrencyRatesResponse
        from app.services.currency_service import CurrencyService

        mock_rates = CurrencyRatesResponse(
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

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=mock_rates):
            # 1. Устанавливаем пользователю базовую валюту USD
            await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)

            # 2. Создаем бюджет на продукты: 500.00 USD
            await client.post(
                "/api/v1/budgets/",
                json={"category": "food", "limit_amount": 500.00, "month": 10, "year": 2026},
                headers=auth_headers,
            )

            # 3. Создаем транзакцию 1: 50.00 USD (валюта совпадает с базовой)
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": 50.00,
                    "currency": "USD",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-05T12:00:00Z",
                    "description": "Обед в Нью-Йорке",
                },
                headers=auth_headers,
            )

            # 4. Создаем транзакцию 2: 1 280 000.00 UZS (конвертируется по курсу 12800 UZS = 1 USD -> 100.00 USD)
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": 1280000.00,
                    "currency": "UZS",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-06T12:00:00Z",
                    "description": "Ужин в Ташкенте",
                },
                headers=auth_headers,
            )

            # 5. Создаем транзакцию 3: с явным exchange_rate и base_amount (EUR: 92 EUR = 100.00 USD)
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": 92.00,
                    "currency": "EUR",
                    "exchange_rate": 1.086957,
                    "base_amount": 100.00,
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-07T12:00:00Z",
                    "description": "Кафе в Париже",
                },
                headers=auth_headers,
            )

            # 6. Создаем транзакцию 4: в другом месяце (не должна влиять на октябрь)
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": 200.00,
                    "currency": "USD",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-09-20T12:00:00Z",
                    "description": "Сентябрьский чек",
                },
                headers=auth_headers,
            )

            # 7. Запрашиваем бюджет за октябрь 2026
            res = await client.get("/api/v1/budgets/?month=10&year=2026", headers=auth_headers)
            assert res.status_code == 200
            data = res.json()["data"]
            assert len(data) == 1
            food_budget = data[0]

            assert Decimal(str(food_budget["limit_amount"])) == Decimal("500.00")
            # spent: 50.00 (USD) + 100.00 (UZS) + 100.00 (EUR) = 250.00 USD
            assert Decimal(str(food_budget["spent"])) == Decimal("250.00")
            # remaining: 500.00 - 250.00 = 250.00 USD
            assert Decimal(str(food_budget["remaining"])) == Decimal("250.00")
