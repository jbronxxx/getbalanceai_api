"""Мелкие уточнения контракта API: курсор, пути полей в ошибках валидации, валюта лимита, кэш курсов."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.schemas.schemas import CurrencyRatesResponse
from app.services.currency_service import CurrencyService
from config_reader.config_reader import config


class TestInvalidCursor:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cursor", ["not-base64!!", "eyJkIjogMX0=", "e30="])
    async def test_invalid_cursor_returns_400(self, client: AsyncClient, auth_headers: dict[str, str], cursor: str):
        """Битый курсор — 400, а не первая страница: иначе клиент зациклился бы на тех же записях."""
        response = await client.get("/api/v1/transactions/", params={"cursor": cursor}, headers=auth_headers)

        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_CURSOR"


class TestValidationFieldPaths:
    @pytest.mark.asyncio
    async def test_sync_error_points_to_record_index(self, client: AsyncClient, auth_headers: dict[str, str]):
        """В пакете /sync причина отказа записи указывает её номер: transactions.1.amount."""
        valid = {"amount": 1, "description": "a", "category": "food", "type": "expense", "date": "2026-10-10T00:00:00Z"}
        invalid = {**valid, "amount": -5}
        response = await client.post("/api/v1/sync/", json={"transactions": [valid, invalid]}, headers=auth_headers)

        assert response.status_code == 200
        rejected = response.json()["data"]["rejected"]
        assert [r["code"] for r in rejected] == ["INVALID_AMOUNT"]
        assert rejected[0]["reason"].startswith("transactions.1.amount:")

    @pytest.mark.asyncio
    async def test_sync_structure_error_points_to_field(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Ошибка структуры батча (не отдельной записи) — по-прежнему 422 с путём поля."""
        response = await client.post("/api/v1/sync/", json={"deleted_transaction_ids": ["не-uuid"]}, headers=auth_headers)

        assert response.status_code == 422
        assert list(response.json()["details"]["fields"]) == ["deleted_transaction_ids.0"]

    @pytest.mark.asyncio
    async def test_flat_body_keeps_plain_field_names(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Для плоского тела имена полей прежние — без префикса body."""
        response = await client.post(
            "/api/v1/budgets/", json={"category": "food", "limit_amount": 0, "month": 1, "year": 2026}, headers=auth_headers
        )

        assert response.status_code == 422
        assert "limit_amount" in response.json()["details"]["fields"]


class TestBudgetCurrencyOnUpdate:
    @pytest.mark.asyncio
    async def test_post_without_currency_keeps_existing_currency(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Обновление лимита без валюты не переводит его в базовую валюту — как в /sync."""
        base = {"category": "food", "month": 1, "year": 2026}
        await client.post("/api/v1/budgets/", json={**base, "limit_amount": "100", "currency": "EUR"}, headers=auth_headers)

        response = await client.post("/api/v1/budgets/", json={**base, "limit_amount": "250"}, headers=auth_headers)

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["currency"] == "EUR"
        assert Decimal(data["limit_amount"]) == Decimal("250")


class TestCurrencyRatesCacheControl:
    @staticmethod
    def _rates(is_fallback: bool) -> CurrencyRatesResponse:
        return CurrencyRatesResponse(
            base="USD",
            rates={"USD": Decimal("1"), "EUR": Decimal("0.92")},
            updated_at=datetime.now(timezone.utc),
            is_fallback=is_fallback,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("is_fallback", [False, True])
    async def test_cache_max_age_matches_rates_source(self, client: AsyncClient, is_fallback: bool):
        """Резервные курсы кэшируются на короткий срок, свежие — на сутки."""
        expected = config.currency_rates_fallback_cache_ttl if is_fallback else config.currency_rates_cache_ttl
        with patch.object(CurrencyService, "get_rates", AsyncMock(return_value=self._rates(is_fallback))):
            response = await client.get("/api/v1/currencies/rates")

        assert response.headers["Cache-Control"] == f"public, max-age={expected}"
