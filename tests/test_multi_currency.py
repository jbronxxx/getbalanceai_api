"""Автоматические тесты для проверки мультивалютности на бэкенде."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Category, Transaction, TransactionType, User
from app.schemas.schemas import BudgetCreate, TransactionCreate
from app.services.ai_service import AIService
from app.services.budget_service import BudgetService
from app.services.currency_service import CurrencyService
from app.services.transaction_service import TransactionService


class TestMultiCurrencyBackend:
    """Тесты бизнес-логики и эндпоинтов мультивалютности."""

    @pytest.mark.asyncio
    async def test_create_transaction_with_explicit_currency(self, db_session: AsyncSession, test_user: User):
        """Создание транзакции с указанием валюты и курса сохраняет base_amount."""
        service = TransactionService(db_session)
        payload = TransactionCreate(
            amount=Decimal("120000.00"),
            currency="uzs",
            exchange_rate=Decimal("0.007500"),
            base_amount=Decimal("900.00"),
            description="Обед в Ташкенте",
            category=Category.food,
            type=TransactionType.expense,
        )
        tx = await service.create(test_user.id, payload)

        assert tx.currency == "UZS"
        assert tx.exchange_rate == Decimal("0.007500")
        assert tx.base_amount == Decimal("900.00")
        assert tx.amount == Decimal("120000.00")

    @pytest.mark.asyncio
    async def test_create_transaction_auto_calculates_and_quantizes_base_amount(
        self, db_session: AsyncSession, test_user: User
    ):
        """Если base_amount не передан, он автоматически рассчитывается и квантуется до 2 знаков."""
        service = TransactionService(db_session)
        payload = TransactionCreate(
            amount=Decimal("50.00"),
            currency="USD",
            exchange_rate=Decimal("96.555555"),
            description="Покупка книги",
            category=Category.shopping,
            type=TransactionType.expense,
        )
        tx = await service.create(test_user.id, payload)

        assert tx.currency == "USD"
        assert tx.exchange_rate == Decimal("96.555555")
        # 50.00 * 96.555555 = 4827.77775 -> 4827.78
        assert tx.base_amount == Decimal("4827.78")

    @pytest.mark.asyncio
    async def test_budget_spent_uses_base_amount(self, db_session: AsyncSession, test_user: User):
        """Проверка, что расходы бюджета суммируются строго по base_amount, а не по сумме в чеке."""
        tx_service = TransactionService(db_session)
        budget_service = BudgetService(db_session)

        now = datetime.now(timezone.utc)
        # Создаем транзакцию в сумах на 500 000 сум (~3 750 руб)
        await tx_service.create(
            test_user.id,
            TransactionCreate(
                amount=Decimal("500000.00"),
                currency="UZS",
                exchange_rate=Decimal("0.007500"),
                base_amount=Decimal("3750.00"),
                description="Ресторан",
                category=Category.food,
                type=TransactionType.expense,
                date=now,
            ),
        )

        # Создаем бюджет на 10 000 руб
        budget_resp = await budget_service.create(
            test_user.id,
            BudgetCreate(
                category=Category.food,
                limit_amount=Decimal("10000.00"),
                month=now.month,
                year=now.year,
            ),
        )

        # Потраченная сумма должна быть 3750.00 руб, а не 500000.00!
        assert budget_resp.spent == Decimal("3750.00")
        assert budget_resp.remaining == Decimal("6250.00")

    @pytest.mark.asyncio
    async def test_create_transaction_http_api_success(self, client: AsyncClient, auth_headers: dict[str, str]):
        """HTTP-эндпоинт создания транзакции корректно сохраняет и возвращает мультивалютные поля."""
        payload = {
            "amount": 100.50,
            "currency": "usd",
            "exchange_rate": 96.50,
            "description": "Подписка",
            "category": "subscriptions",
            "type": "expense",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 201

        data = response.json()
        assert data["status"] == "success"
        tx_data = data["data"]
        assert tx_data["currency"] == "USD"
        assert Decimal(str(tx_data["exchange_rate"])) == Decimal("96.50")
        # 100.50 * 96.50 = 9698.25
        assert Decimal(str(tx_data["base_amount"])) == Decimal("9698.25")

    @pytest.mark.asyncio
    async def test_create_transaction_negative_base_amount_rejected(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Попытка передать отрицательный base_amount отклоняется с кодом 422."""
        payload = {
            "amount": 100.00,
            "currency": "USD",
            "exchange_rate": 96.00,
            "base_amount": -50.00,
            "description": "Тест",
            "category": "subscriptions",
            "type": "expense",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_transaction_invalid_currency_code_rejected(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Код валюты короче 3 символов отклоняется валидацией Pydantic с кодом 422."""
        payload = {
            "amount": 100.00,
            "currency": "US",
            "exchange_rate": 96.00,
            "description": "Тест",
            "category": "subscriptions",
            "type": "expense",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_get_currency_rates_mocked_gateway(self, client: AsyncClient):
        """Публичный эндпоинт /api/v1/currencies/rates возвращает котировки без реальных сетевых запросов."""
        mocked_rates = {
            "USD": Decimal("1.0"),
            "EUR": Decimal("0.925"),
            "RUB": Decimal("97.2"),
            "UZS": Decimal("12850.0"),
            "KZT": Decimal("492.0"),
        }
        with patch.object(CurrencyService, "_fetch_rates_from_gateway", new_callable=AsyncMock, return_value=mocked_rates):
            response = await client.get("/api/v1/currencies/rates")
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "success"
            rates = data["data"]["rates"]
            assert Decimal(str(rates["USD"])) == Decimal("1.0")
            assert Decimal(str(rates["EUR"])) == Decimal("0.925")
            assert Decimal(str(rates["RUB"])) == Decimal("97.2")
            assert Decimal(str(rates["UZS"])) == Decimal("12850.0")
            assert Decimal(str(rates["KZT"])) == Decimal("492.0")

    @pytest.mark.asyncio
    async def test_get_currency_rates_fallback_on_network_error(self, client: AsyncClient):
        """При сетевой ошибке шлюза возвращаются безопасные резервные котировки."""
        with patch("httpx.AsyncClient.get", side_effect=httpx.RequestError("Network timeout")):
            service = CurrencyService()
            response_data = await service.get_rates()
            assert response_data.base == "USD"
            assert "USD" in response_data.rates
            assert "RUB" in response_data.rates
            assert response_data.rates["USD"] == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_sync_multi_currency_transactions(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Пакетная синхронизация корректно сохраняет мультивалютные поля транзакций."""
        tx_id = str(uuid.uuid4())
        payload = {
            "transactions": [
                {
                    "id": tx_id,
                    "amount": 250000.0,
                    "currency": "uzs",
                    "exchange_rate": 0.0075,
                    "description": "Такси Самарканд",
                    "category": "transport",
                    "type": "expense",
                    "date": "2026-10-10T12:00:00Z",
                }
            ],
            "budgets": [],
        }

        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "success"
        synced_txs = data["data"]["synced_transactions"]
        assert len(synced_txs) == 1
        assert synced_txs[0]["currency"] == "UZS"
        # 250000.0 * 0.0075 = 1875.00
        assert Decimal(str(synced_txs[0]["base_amount"])) == Decimal("1875.00")

        # Проверяем напрямую в БД
        res = await db_session.execute(select(Transaction).where(Transaction.id == uuid.UUID(tx_id)))
        db_tx = res.scalar_one()
        assert db_tx.currency == "UZS"
        assert db_tx.base_amount == Decimal("1875.00")

    @pytest.mark.asyncio
    async def test_ai_service_multi_currency_summary(self, db_session: AsyncSession, test_user: User):
        """AIService._build_summary отображает валюту операции и эквивалент в базовой валюте."""
        ai_service = AIService(db_session)
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            amount=Decimal("500000.00"),
            currency="UZS",
            exchange_rate=Decimal("0.007500"),
            base_amount=Decimal("3750.00"),
            description="Отель",
            category=Category.other,
            type=TransactionType.expense,
            date=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc),
        )

        summary = ai_service._build_summary([tx], currency="RUB")
        assert "500000.00 UZS" in summary
        assert "3750.00 RUB" in summary
