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

from app.exceptions import InvalidCurrencyException
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

    @pytest.mark.asyncio
    async def test_large_nominal_currency_does_not_overflow_numeric_precision(
        self, db_session: AsyncSession, test_user: User
    ):
        """Проверка, что суммы > 10^10 (например, при конвертации в UZS) не вызывают numeric overflow."""
        test_user.base_currency = "UZS"
        await db_session.flush()

        tx_service = TransactionService(db_session)
        # Симулируем ровно ту операцию из логов: 1 000 000 EUR по курсу 13298.649213 -> 13 298 649 212.61 UZS (> 10^10)
        large_tx = await tx_service.create(
            user_id=test_user.id,
            payload=TransactionCreate(
                amount=Decimal("1000000.00"),
                currency="EUR",
                exchange_rate=Decimal("13298.649213"),
                base_amount=Decimal("13298649213.00"),
                description="Крупная операция в EUR",
                category=Category.health,
                type=TransactionType.expense,
            ),
            user_base_currency="UZS",
        )

        assert large_tx.base_amount == Decimal("13298649213.00")
        assert large_tx.amount == Decimal("1000000.00")
        assert large_tx.exchange_rate == Decimal("13298.649213")

        # Проверяем бюджет с лимитом > 10^10 в сумах
        budget_service = BudgetService(db_session)
        budget = await budget_service.create(
            user_id=test_user.id,
            payload=BudgetCreate(
                category=Category.health,
                limit_amount=Decimal("20000000000.00"),  # 20 миллиардов UZS
                month=10,
                year=2026,
            ),
            user_base_currency="UZS",
        )

        assert budget.limit_amount == Decimal("20000000000.00")
        assert budget.spent == Decimal("13298649213.00")
        assert budget.remaining == Decimal("6701350787.00")

    @pytest.mark.asyncio
    async def test_unknown_currency_code_in_create_transaction_returns_422(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Неизвестный код валюты в POST /api/v1/transactions/ отклоняется с 422."""
        for bad_curr in ["ABC", "usdt", "INVALID", "BTC"]:
            payload = {
                "amount": 100.00,
                "currency": bad_curr,
                "description": "Тест неизвестной валюты",
                "category": "shopping",
                "type": "expense",
            }
            response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
            assert response.status_code == 422, f"Ожидался код 422 для валюты {bad_curr}"

    @pytest.mark.asyncio
    async def test_unknown_currency_code_in_sync_returns_422(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Неизвестный код валюты в элементе списка POST /api/v1/sync/ отклоняется с 422."""
        payload = {
            "transactions": [
                {
                    "amount": 500.00,
                    "currency": "FOOBAR",
                    "description": "Невалидная синхронизация",
                    "category": "transport",
                    "type": "expense",
                    "date": "2026-10-10T12:00:00Z",
                }
            ],
            "budgets": [],
        }
        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 422

    def test_calculate_cross_rate_raises_on_unsupported_currency(self):
        """calculate_cross_rate бросает InvalidCurrencyException для неизвестной валюты."""
        with pytest.raises(InvalidCurrencyException):
            CurrencyService.calculate_cross_rate("UNKNOWN", "RUB")

        with pytest.raises(InvalidCurrencyException):
            CurrencyService.calculate_cross_rate("USD", "UNKNOWN")

    @pytest.mark.asyncio
    async def test_create_transaction_defaults_to_user_base_currency_usd(self, db_session: AsyncSession, test_user: User):
        """Транзакция без currency у USD-пользователя создается в USD с курсом 1.0 и base_amount=amount."""
        test_user.base_currency = "USD"
        await db_session.flush()

        tx_service = TransactionService(db_session)
        payload = TransactionCreate(
            amount=Decimal("150.00"),
            currency=None,
            description="Оплата хостинга",
            category=Category.subscriptions,
            type=TransactionType.expense,
        )
        tx = await tx_service.create(test_user.id, payload, user_base_currency="USD")

        assert tx.currency == "USD"
        assert tx.exchange_rate == Decimal("1.0")
        assert tx.base_amount == Decimal("150.00")
        assert tx.amount == Decimal("150.00")

    @pytest.mark.asyncio
    async def test_sync_transaction_defaults_to_user_base_currency_usd(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Синхронизация транзакции без указания валюты у USD-пользователя сохраняет ее в USD."""
        test_user.base_currency = "USD"
        await db_session.flush()

        tx_id = str(uuid.uuid4())
        payload = {
            "transactions": [
                {
                    "id": tx_id,
                    "amount": 75.00,
                    "description": "Кофе в Нью-Йорке",
                    "category": "food",
                    "type": "expense",
                    "date": "2026-10-10T12:00:00Z",
                }
            ],
            "budgets": [],
        }
        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200

        data = response.json()["data"]["synced_transactions"]
        assert len(data) == 1
        assert data[0]["currency"] == "USD"
        assert Decimal(str(data[0]["exchange_rate"])) == Decimal("1.0")
        assert Decimal(str(data[0]["base_amount"])) == Decimal("75.00")

    def test_resolve_amounts_same_currency_enforces_unit_rate_and_ignores_tampering(self):
        """При совпадении валюты с базовой курс строго 1.0, а base_amount равен amount даже при подделке."""
        rate, base_amount = CurrencyService.resolve_amounts(
            amount=Decimal("100.00"),
            currency="RUB",
            base_currency="RUB",
            client_rate=Decimal("999.0"),
            client_base_amount=Decimal("1000000.00"),
        )
        assert rate == Decimal("1.0")
        assert base_amount == Decimal("100.00")

    def test_resolve_amounts_accepts_client_rate_within_tolerance(self):
        """Клиентский курс принимается, если его отклонение от серверного не превышает tolerance."""
        # rates: USD=1.0, RUB=96.0 -> server cross rate USD->RUB = 96.0
        rates = {"USD": Decimal("1.0"), "RUB": Decimal("96.0")}
        # Клиент прислал 95.0. Отклонение: |95 - 96| / 96 = ~1.04% <= 15%
        rate, base_amount = CurrencyService.resolve_amounts(
            amount=Decimal("100.00"),
            currency="USD",
            base_currency="RUB",
            client_rate=Decimal("95.000000"),
            rates=rates,
            max_tolerance_percent=15.0,
        )
        assert rate == Decimal("95.000000")
        assert base_amount == Decimal("9500.00")

    def test_resolve_amounts_rejects_client_rate_exceeding_tolerance(self):
        """Клиентский курс отбрасывается в пользу серверного, если отклонение превышает tolerance."""
        rates = {"USD": Decimal("1.0"), "RUB": Decimal("96.0")}
        # Клиент прислал нереалистичный курс 50.0 (отклонение ~48% > 15%)
        rate, base_amount = CurrencyService.resolve_amounts(
            amount=Decimal("100.00"),
            currency="USD",
            base_currency="RUB",
            client_rate=Decimal("50.000000"),
            rates=rates,
            max_tolerance_percent=15.0,
        )
        assert rate == Decimal("96.000000")
        assert base_amount == Decimal("9600.00")

    @pytest.mark.asyncio
    async def test_create_and_sync_give_identical_rate_and_base_amount(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Эндпоинты create и sync дают строго одинаковые rate и base_amount при одинаковых входных данных."""
        test_user.base_currency = "RUB"
        await db_session.flush()

        tx_payload_create = {
            "amount": 200.00,
            "currency": "USD",
            "exchange_rate": 96.50,
            "description": "Сверка курсов create",
            "category": "shopping",
            "type": "expense",
        }
        res_create = await client.post("/api/v1/transactions/", json=tx_payload_create, headers=auth_headers)
        assert res_create.status_code == 201
        data_create = res_create.json()["data"]

        tx_id = str(uuid.uuid4())
        sync_payload = {
            "transactions": [
                {
                    "id": tx_id,
                    "amount": 200.00,
                    "currency": "USD",
                    "exchange_rate": 96.50,
                    "description": "Сверка курсов sync",
                    "category": "shopping",
                    "type": "expense",
                    "date": "2026-10-10T12:00:00Z",
                }
            ],
            "budgets": [],
        }
        res_sync = await client.post("/api/v1/sync/", json=sync_payload, headers=auth_headers)
        assert res_sync.status_code == 200
        data_sync = res_sync.json()["data"]["synced_transactions"][0]

        assert data_create["exchange_rate"] == data_sync["exchange_rate"]
        assert data_create["base_amount"] == data_sync["base_amount"]
