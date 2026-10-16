"""Модуль автоматических тестов для проверки эндпоинтов удаления и синхронизации бюджетов.

Запуск тестов: pytest tests/ -v
"""

import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient


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
    async def test_budget_limits_kept_and_etag_invalidated_on_base_currency_change(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """При смене base_currency лимиты остаются в своей валюте, а ETag инвалидируется (траты зависят от базовой)."""
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

            # 5. Меняем базовую валюту на UZS: лимит остаётся 100 USD
            patch_to_uzs = await client.patch("/api/v1/auth/me", json={"base_currency": "UZS"}, headers=auth_headers)
            assert patch_to_uzs.status_code == 200
            assert patch_to_uzs.json()["data"]["base_currency"] == "UZS"

            # 6. Запрос с СТАРЫМ If-None-Match (initial_etag) НЕ должен вернуть 304!
            # ETag инвалидирован, поэтому сервер обязан вернуть 200 OK с новым ETag
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
            assert budgets_data[0]["currency"] == "USD"
            assert Decimal(str(budgets_data[0]["limit_amount"])) == Decimal("100.00")

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

    @pytest.mark.asyncio
    async def test_create_budget_upsert_on_conflict(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Повторный POST /budgets/ с тем же периодом и категорией обновляет лимит без 500 ошибки (Upsert)."""
        # 1. Первый запрос создает бюджет
        res1 = await client.post(
            "/api/v1/budgets/",
            json={"category": "food", "limit_amount": 300.00, "month": 11, "year": 2026},
            headers=auth_headers,
        )
        assert res1.status_code == 201
        data1 = res1.json()["data"]
        budget_id = data1["id"]
        assert Decimal(str(data1["limit_amount"])) == Decimal("300.00")

        # 2. Второй запрос на тот же период и категорию обновляет лимит
        res2 = await client.post(
            "/api/v1/budgets/",
            json={"category": "food", "limit_amount": 450.00, "month": 11, "year": 2026},
            headers=auth_headers,
        )
        assert res2.status_code == 201
        data2 = res2.json()["data"]
        assert data2["id"] == budget_id
        assert Decimal(str(data2["limit_amount"])) == Decimal("450.00")

    @pytest.mark.asyncio
    async def test_etag_304_headers_and_weak_etag(self, client: AsyncClient, auth_headers: dict[str, str]):
        """304 Not Modified возвращает заголовок ETag и поддерживает W/ префикс и списки в If-None-Match."""
        # 1. Создаем бюджет
        await client.post(
            "/api/v1/budgets/",
            json={"category": "transport", "limit_amount": 200.00, "month": 12, "year": 2026},
            headers=auth_headers,
        )

        # 2. Запрашиваем бюджет и получаем ETag
        get_res = await client.get("/api/v1/budgets/?month=12&year=2026", headers=auth_headers)
        assert get_res.status_code == 200
        etag = get_res.headers.get("ETag")
        assert etag is not None

        # 3. 304 с точным ETag
        res_304_exact = await client.get(
            "/api/v1/budgets/?month=12&year=2026",
            headers={**auth_headers, "If-None-Match": etag},
        )
        assert res_304_exact.status_code == 304
        assert res_304_exact.headers.get("ETag") == etag

        # 4. 304 со слабым ETag (W/...)
        res_304_weak = await client.get(
            "/api/v1/budgets/?month=12&year=2026",
            headers={**auth_headers, "If-None-Match": f"W/{etag}"},
        )
        assert res_304_weak.status_code == 304
        assert res_304_weak.headers.get("ETag") == etag

        # 5. 304 со списком ETag
        res_304_list = await client.get(
            "/api/v1/budgets/?month=12&year=2026",
            headers={**auth_headers, "If-None-Match": f'"stale-tag", {etag}'},
        )
        assert res_304_list.status_code == 304
        assert res_304_list.headers.get("ETag") == etag

    @pytest.mark.asyncio
    async def test_etag_invalidated_on_transaction_mutations(self, client: AsyncClient, auth_headers: dict[str, str]):
        """ETag бюджетов инвалидируется при создании и удалении транзакции."""
        # 1. Создаем бюджет
        await client.post(
            "/api/v1/budgets/",
            json={"category": "entertainment", "limit_amount": 100.00, "month": 5, "year": 2026},
            headers=auth_headers,
        )
        initial_res = await client.get("/api/v1/budgets/?month=5&year=2026", headers=auth_headers)
        initial_etag = initial_res.headers["ETag"]

        # 2. Создаем расходную транзакцию
        tx_res = await client.post(
            "/api/v1/transactions/",
            json={
                "amount": 25.00,
                "currency": "USD",
                "category": "entertainment",
                "type": "expense",
                "date": "2026-05-10T10:00:00Z",
                "description": "Кино",
            },
            headers=auth_headers,
        )
        assert tx_res.status_code == 201
        tx_id = tx_res.json()["data"]["id"]

        # 3. Запрос со старым ETag должен вернуть 200 с обновленным spent и новым ETag
        after_tx_res = await client.get(
            "/api/v1/budgets/?month=5&year=2026",
            headers={**auth_headers, "If-None-Match": initial_etag},
        )
        assert after_tx_res.status_code == 200
        new_etag = after_tx_res.headers["ETag"]
        assert new_etag != initial_etag
        assert Decimal(str(after_tx_res.json()["data"][0]["spent"])) == Decimal("25.00")

        # 4. Удаляем транзакцию
        del_tx_res = await client.delete(f"/api/v1/transactions/{tx_id}", headers=auth_headers)
        assert del_tx_res.status_code == 200

        # 5. Запрос со вторым ETag должен вернуть 200 с нулевым spent
        after_del_res = await client.get(
            "/api/v1/budgets/?month=5&year=2026",
            headers={**auth_headers, "If-None-Match": new_etag},
        )
        assert after_del_res.status_code == 200
        final_etag = after_del_res.headers["ETag"]
        assert final_etag != new_etag
        assert Decimal(str(after_del_res.json()["data"][0]["spent"])) == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_enrich_multiple_does_not_request_currency_rates(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Чтение бюджетов не вызывает внешний сервис курсов валют (расходы агрегируются по base_amount)."""
        from unittest.mock import AsyncMock, patch

        from app.services.currency_service import CurrencyService

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock) as mock_get_rates:
            res = await client.get("/api/v1/budgets/", headers=auth_headers)
            assert res.status_code == 200
            mock_get_rates.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_budget_parallel_requests_no_500(self, auth_headers: dict[str, str]):
        """Параллельные одновременные запросы POST /budgets/ на одну категорию не приводят к 500 ошибке гонки."""
        import asyncio

        from app.database import get_db
        from main import app
        from tests.conftest import TestingSessionLocal

        # Каждый параллельный запрос получает собственную сессию БД из пула (как в продакшене)
        async def _parallel_get_db():
            async with TestingSessionLocal() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_db] = _parallel_get_db
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as par_client:
                payload1 = {"category": "health", "limit_amount": 400.00, "month": 12, "year": 2026}
                payload2 = {"category": "health", "limit_amount": 600.00, "month": 12, "year": 2026}

                # Выполняем два одновременных запроса создания бюджета на один и тот же период и категорию
                res1, res2 = await asyncio.gather(
                    par_client.post("/api/v1/budgets/", json=payload1, headers=auth_headers),
                    par_client.post("/api/v1/budgets/", json=payload2, headers=auth_headers),
                )

                # Ни один из параллельных запросов не должен упасть с 500 Internal Server Error
                assert res1.status_code == 201
                assert res2.status_code == 201

                # Проверяем список: должна остаться ровно одна запись бюджета по категории health (upsert)
                get_res = await par_client.get("/api/v1/budgets/?month=12&year=2026", headers=auth_headers)
                assert get_res.status_code == 200
                health_budgets = [b for b in get_res.json()["data"] if b["category"] == "health"]
                assert len(health_budgets) == 1
                assert float(health_budgets[0]["limit_amount"]) in (400.00, 600.00)
        finally:
            app.dependency_overrides.pop(get_db, None)


def _mock_rates():
    """Фиксированные курсы к USD для тестов валюты лимита."""
    from datetime import datetime, timezone

    from app.schemas.schemas import CurrencyRatesResponse

    return CurrencyRatesResponse(
        base="USD",
        rates={
            "USD": Decimal("1.0"),
            "EUR": Decimal("0.92"),
            "UZS": Decimal("12800.0"),
            "KZT": Decimal("495.0"),
        },
        updated_at=datetime.now(timezone.utc),
    )


class TestBudgetCurrency:
    """Лимит в собственной валюте, независимой от базовой валюты пользователя."""

    @pytest.mark.asyncio
    async def test_budget_in_foreign_currency_counts_spent_in_its_currency(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Лимит 500 USD при базовой UZS: траты переводятся в USD (чек в USD — как есть, остальное — по курсу)."""
        from unittest.mock import AsyncMock, patch

        from app.services.currency_service import CurrencyService

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=_mock_rates()):
            await client.patch("/api/v1/auth/me", json={"base_currency": "UZS"}, headers=auth_headers)

            create_res = await client.post(
                "/api/v1/budgets/",
                json={"category": "food", "limit_amount": 500.00, "currency": "usd", "month": 10, "year": 2026},
                headers=auth_headers,
            )
            assert create_res.status_code == 201
            assert create_res.json()["data"]["currency"] == "USD"

            for amount, currency in (("50.00", "USD"), ("1280000.00", "UZS"), ("92.00", "EUR")):
                tx_res = await client.post(
                    "/api/v1/transactions/",
                    json={
                        "amount": amount,
                        "currency": currency,
                        "category": "food",
                        "type": "expense",
                        "date": "2026-10-05T12:00:00Z",
                        "description": "Еда",
                    },
                    headers=auth_headers,
                )
                assert tx_res.status_code == 201

            res = await client.get("/api/v1/budgets/?month=10&year=2026", headers=auth_headers)
            assert res.status_code == 200
            food = res.json()["data"][0]
            assert food["currency"] == "USD"
            assert Decimal(str(food["limit_amount"])) == Decimal("500.00")
            # 50 USD + 1 280 000 UZS (100 USD) + 92 EUR (100 USD)
            assert Decimal(str(food["spent"])) == Decimal("250.00")
            assert Decimal(str(food["remaining"])) == Decimal("250.00")

    @pytest.mark.asyncio
    async def test_budget_without_currency_uses_base_currency(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Старый клиент без поля currency: лимит создаётся в базовой валюте пользователя."""
        from unittest.mock import AsyncMock, patch

        from app.services.currency_service import CurrencyService

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=_mock_rates()):
            await client.patch("/api/v1/auth/me", json={"base_currency": "KZT"}, headers=auth_headers)

        res = await client.post(
            "/api/v1/budgets/",
            json={"category": "transport", "limit_amount": 20000.00, "month": 10, "year": 2026},
            headers=auth_headers,
        )
        assert res.status_code == 201
        assert res.json()["data"]["currency"] == "KZT"

    @pytest.mark.asyncio
    async def test_budget_upsert_changes_currency(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Повторный POST на ту же категорию и период обновляет и сумму, и валюту лимита."""
        first = await client.post(
            "/api/v1/budgets/",
            json={"category": "health", "limit_amount": 100.00, "currency": "USD", "month": 11, "year": 2026},
            headers=auth_headers,
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/budgets/",
            json={"category": "health", "limit_amount": 90.00, "currency": "EUR", "month": 11, "year": 2026},
            headers=auth_headers,
        )
        assert second.status_code == 201
        data = second.json()["data"]
        assert data["id"] == first.json()["data"]["id"]
        assert data["currency"] == "EUR"
        assert Decimal(str(data["limit_amount"])) == Decimal("90.00")

    @pytest.mark.asyncio
    async def test_budget_unsupported_currency_rejected(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Неподдерживаемая валюта лимита отклоняется с 422."""
        res = await client.post(
            "/api/v1/budgets/",
            json={"category": "food", "limit_amount": 100.00, "currency": "RUB", "month": 10, "year": 2026},
            headers=auth_headers,
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_base_currency_budget_does_not_request_rates(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Лимит в базовой валюте считается по base_amount: чтение не обращается к курсам."""
        from unittest.mock import AsyncMock, patch

        from app.services.currency_service import CurrencyService

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock, return_value=_mock_rates()):
            await client.patch("/api/v1/auth/me", json={"base_currency": "USD"}, headers=auth_headers)
            await client.post(
                "/api/v1/budgets/",
                json={"category": "food", "limit_amount": 500.00, "month": 10, "year": 2026},
                headers=auth_headers,
            )
            await client.post(
                "/api/v1/transactions/",
                json={
                    "amount": "1280000.00",
                    "currency": "UZS",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-05T12:00:00Z",
                    "description": "Ужин",
                },
                headers=auth_headers,
            )

        with patch.object(CurrencyService, "get_rates", new_callable=AsyncMock) as mock_get_rates:
            res = await client.get("/api/v1/budgets/?month=10&year=2026", headers=auth_headers)
            assert res.status_code == 200
            assert Decimal(str(res.json()["data"][0]["spent"])) == Decimal("100.00")
            mock_get_rates.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_budget_currency_saved_and_kept_without_field(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Синхронизация сохраняет валюту лимита; элемент без currency (старый клиент) её не сбрасывает."""
        base_payload = {"transactions": [], "deleted_budget_ids": [], "deleted_budgets": []}

        res = await client.post(
            "/api/v1/sync/",
            json={
                **base_payload,
                "budgets": [{"category": "food", "limit_amount": 300.0, "currency": "EUR", "month": 10, "year": 2026}],
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["data"]["synced_budgets"][0]["currency"] == "EUR"

        res = await client.post(
            "/api/v1/sync/",
            json={**base_payload, "budgets": [{"category": "food", "limit_amount": 350.0, "month": 10, "year": 2026}]},
            headers=auth_headers,
        )
        assert res.status_code == 200
        synced = res.json()["data"]["synced_budgets"][0]
        assert synced["currency"] == "EUR"
        assert Decimal(str(synced["limit_amount"])) == Decimal("350.00")
