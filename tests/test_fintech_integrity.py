"""Тесты финансовой точности (Decimal/Numeric) и ограничений БД (индексы, UniqueConstraint)."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Budget, Category, Transaction, TransactionType, User
from app.schemas.schemas import BudgetCreate, TransactionCreate
from app.services.budget_service import BudgetService
from app.services.transaction_service import TransactionService


class TestFinTechDecimalPrecision:
    """Тесты точности вычислений без погрешностей IEEE-754 (Float)."""

    @pytest.mark.asyncio
    async def test_decimal_addition_precision_in_budget_enrichment(self, db_session: AsyncSession, test_user: User):
        """Проверка отсутствия погрешности 0.1 + 0.2 = 0.30000000000000004 при расчете spent и remaining."""
        tx_service = TransactionService(db_session)
        budget_service = BudgetService(db_session)

        user_id = test_user.id
        # Создаем транзакции: 0.10 + 0.20
        await tx_service.create(
            user_id,
            TransactionCreate(
                amount=Decimal("0.10"),
                description="Микро-транзакция 1",
                category=Category.food,
                type=TransactionType.expense,
                date=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
            ),
        )
        await tx_service.create(
            user_id,
            TransactionCreate(
                amount=Decimal("0.20"),
                description="Микро-транзакция 2",
                category=Category.food,
                type=TransactionType.expense,
                date=datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc),
            ),
        )

        budget = await budget_service.create(
            user_id,
            BudgetCreate(
                category=Category.food,
                limit_amount=Decimal("1.00"),
                month=10,
                year=2026,
            ),
        )

        # Проверяем точные значения Decimal
        assert budget.spent == Decimal("0.30")
        assert budget.remaining == Decimal("0.70")
        assert str(budget.spent) == "0.30"
        assert str(budget.remaining) == "0.70"

    @pytest.mark.asyncio
    async def test_transaction_create_negative_or_zero_amount_rejected(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ):
        """Сумма транзакции <= 0 отклоняется валидацией Pydantic."""
        zero_res = await client.post(
            "/api/v1/transactions/",
            json={
                "amount": 0,
                "description": "Ноль",
                "category": "food",
                "type": "expense",
            },
            headers=auth_headers,
        )
        assert zero_res.status_code == 422

        neg_res = await client.post(
            "/api/v1/transactions/",
            json={
                "amount": -10.50,
                "description": "Минус",
                "category": "food",
                "type": "expense",
            },
            headers=auth_headers,
        )
        assert neg_res.status_code == 422

    @pytest.mark.asyncio
    async def test_budget_create_negative_or_zero_amount_rejected(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Лимит бюджета <= 0 отклоняется валидацией Pydantic."""
        zero_res = await client.post(
            "/api/v1/budgets/",
            json={"category": "food", "limit_amount": 0, "month": 10, "year": 2026},
            headers=auth_headers,
        )
        assert zero_res.status_code == 422

        neg_res = await client.post(
            "/api/v1/budgets/",
            json={"category": "food", "limit_amount": -500, "month": 10, "year": 2026},
            headers=auth_headers,
        )
        assert neg_res.status_code == 422

    @pytest.mark.asyncio
    async def test_multi_currency_exchange_rate_and_base_amount_precision(self, db_session: AsyncSession, test_user: User):
        """Проверка строгого округления Decimal (ROUND_HALF_UP) при расчете base_amount по курсу валюты."""
        service = TransactionService(db_session)
        # 33.33 * 0.007519 = 0.25060827 -> должно квантоваться ровно в 0.25
        payload = TransactionCreate(
            amount=Decimal("33.33"),
            currency="UZS",
            exchange_rate=Decimal("0.007519"),
            description="Точный расчет курса",
            category=Category.transport,
            type=TransactionType.expense,
        )
        tx = await service.create(test_user.id, payload)
        assert tx.base_amount == Decimal("0.25")
        assert str(tx.base_amount) == "0.25"


class TestDatabaseConstraintsAndIndexes:
    """Тесты структуры базы данных, индексов и ограничений целостности."""

    def test_transaction_model_table_args_indexes(self):
        """Проверка наличия составных индексов в модели Transaction."""
        indexes = [arg.name for arg in Transaction.__table_args__ if hasattr(arg, "name")]
        assert "ix_transactions_user_id_date" in indexes
        assert "ix_transactions_user_id_created_at" in indexes

    def test_budget_model_unique_constraint_metadata(self):
        """Проверка наличия UniqueConstraint на (user_id, category, month, year) в модели Budget."""
        constraints = [arg.name for arg in Budget.__table_args__ if hasattr(arg, "name")]
        assert "uq_budget_user_cat_period" in constraints

    @pytest.mark.asyncio
    async def test_budget_duplicate_insert_violates_unique_constraint(self, db_session: AsyncSession, test_user: User):
        """Прямая вставка дубликата бюджета в БД вызывает IntegrityError."""
        user_id = test_user.id
        b1 = Budget(
            user_id=user_id,
            category=Category.entertainment,
            limit_amount=Decimal("5000.00"),
            month=10,
            year=2026,
        )
        db_session.add(b1)
        await db_session.commit()

        b2 = Budget(
            user_id=user_id,
            category=Category.entertainment,
            limit_amount=Decimal("7000.00"),
            month=10,
            year=2026,
        )
        db_session.add(b2)
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()
