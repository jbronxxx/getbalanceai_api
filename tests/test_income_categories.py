"""Модульные и интеграционные тесты для валидации и поддержки расширенных категорий доходов."""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.models.models import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    Category,
    TransactionType,
    User,
)
from app.schemas.schemas import (
    BudgetCreate,
    BudgetSyncItem,
    TransactionCreate,
    TransactionSyncItem,
)


class TestCategorySchemaValidation:
    """Тесты валидации допустимых категорий для доходов, расходов и бюджетов."""

    @pytest.mark.parametrize("category", list(INCOME_CATEGORIES))
    def test_income_transaction_with_valid_income_categories(self, category: Category):
        """Все категории из INCOME_CATEGORIES успешно проходят валидацию для дохода."""
        tx = TransactionCreate(
            amount=Decimal("1500.00"),
            description="Тестовый доход",
            category=category,
            type=TransactionType.income,
        )
        assert tx.category == category
        assert tx.type == TransactionType.income

    @pytest.mark.parametrize("category", [c for c in Category if c not in INCOME_CATEGORIES])
    def test_income_transaction_with_invalid_category_raises(self, category: Category):
        """Расходные категории отклоняются валидатором при попытке создать доход."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreate(
                amount=Decimal("1500.00"),
                description="Недопустимый доход",
                category=category,
                type=TransactionType.income,
            )
        assert "недопустима для доходных операций" in str(exc_info.value)

    @pytest.mark.parametrize("category", list(EXPENSE_CATEGORIES))
    def test_expense_transaction_with_valid_expense_categories(self, category: Category):
        """Все категории из EXPENSE_CATEGORIES успешно проходят валидацию для расхода."""
        tx = TransactionCreate(
            amount=Decimal("500.00"),
            description="Тестовый расход",
            category=category,
            type=TransactionType.expense,
        )
        assert tx.category == category
        assert tx.type == TransactionType.expense

    @pytest.mark.parametrize("category", [c for c in Category if c not in EXPENSE_CATEGORIES])
    def test_expense_transaction_with_invalid_category_raises(self, category: Category):
        """Доходные категории отклоняются валидатором при попытке создать расход."""
        with pytest.raises(ValidationError) as exc_info:
            TransactionCreate(
                amount=Decimal("500.00"),
                description="Недопустимый расход",
                category=category,
                type=TransactionType.expense,
            )
        assert "недопустима для расходных операций" in str(exc_info.value)

    @pytest.mark.parametrize("category", list(EXPENSE_CATEGORIES))
    def test_budget_create_with_valid_expense_categories(self, category: Category):
        """Бюджет может быть создан для любой категории из EXPENSE_CATEGORIES."""
        budget = BudgetCreate(
            category=category,
            limit_amount=Decimal("10000.00"),
            month=10,
            year=2026,
        )
        assert budget.category == category

    @pytest.mark.parametrize("category", [c for c in Category if c not in EXPENSE_CATEGORIES])
    def test_budget_create_with_income_categories_raises(self, category: Category):
        """Попытка создать бюджет для доходной категории отклоняется валидатором."""
        with pytest.raises(ValidationError) as exc_info:
            BudgetCreate(
                category=category,
                limit_amount=Decimal("10000.00"),
                month=10,
                year=2026,
            )
        assert "недопустима для бюджета" in str(exc_info.value)

    def test_sync_items_validation(self):
        """Валидация категорий в элементах пакетной синхронизации."""
        valid_income_sync = TransactionSyncItem(
            type=TransactionType.income,
            category=Category.freelance,
            amount=Decimal("25000.00"),
            date="2026-10-10T12:00:00Z",
            description="Проектный гонорар",
        )
        assert valid_income_sync.category == Category.freelance

        with pytest.raises(ValidationError):
            TransactionSyncItem(
                type=TransactionType.income,
                category=Category.food,
                amount=Decimal("1000.00"),
                date="2026-10-10T12:00:00Z",
                description="Некорректная транзакция",
            )

        with pytest.raises(ValidationError):
            BudgetSyncItem(
                category=Category.salary,
                limit_amount=Decimal("50000.00"),
                month=10,
                year=2026,
            )


class TestIncomeCategoriesIntegration:
    """Интеграционные тесты API с новыми категориями доходов."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "income_cat",
        ["freelance", "investments", "transfers", "cashback", "sales"],
    )
    async def test_create_income_transaction_endpoints(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        test_user: User,
        income_cat: str,
    ):
        """Успешное создание транзакции дохода через API для каждой новой категории."""
        payload = {
            "amount": 15000.0,
            "description": f"Поступление по категории {income_cat}",
            "category": income_cat,
            "type": "income",
            "date": "2026-10-10T14:00:00Z",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["category"] == income_cat
        assert data["type"] == "income"

    @pytest.mark.asyncio
    async def test_create_income_with_food_category_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        test_user: User,
    ):
        """API возвращает 422 при попытке создать доход с категорией еды."""
        payload = {
            "amount": 2000.0,
            "description": "Попытка добавить еду в доход",
            "category": "food",
            "type": "income",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_create_expense_with_salary_category_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        test_user: User,
    ):
        """API возвращает 422 при попытке создать расход с категорией зарплаты."""
        payload = {
            "amount": 5000.0,
            "description": "Попытка добавить зарплату в расход",
            "category": "salary",
            "type": "expense",
        }
        response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_create_budget_for_income_category_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        test_user: User,
    ):
        """API возвращает 422 при попытке установить бюджет на категорию доходов."""
        payload = {
            "category": "salary",
            "limit_amount": 100000.0,
            "month": 10,
            "year": 2026,
        }
        response = await client.post("/api/v1/budgets/", json=payload, headers=auth_headers)
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_sync_with_new_income_categories(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        test_user: User,
    ):
        """Пакетная синхронизация успешно принимает новые категории доходов."""
        payload = {
            "transactions": [
                {
                    "id": str(uuid.uuid4()),
                    "amount": 35000.0,
                    "description": "Фриланс проект",
                    "category": "freelance",
                    "type": "income",
                    "date": "2026-10-10T10:00:00Z",
                },
                {
                    "id": str(uuid.uuid4()),
                    "amount": 1200.0,
                    "description": "Кэшбэк банка",
                    "category": "cashback",
                    "type": "income",
                    "date": "2026-10-10T11:00:00Z",
                },
            ],
            "budgets": [
                {
                    "category": "food",
                    "limit_amount": 20000.0,
                    "month": 10,
                    "year": 2026,
                }
            ],
            "deleted_budget_ids": [],
            "deleted_budgets": [],
        }
        response = await client.post("/api/v1/sync/", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data["synced_transactions"]) == 2
        categories = {tx["category"] for tx in data["synced_transactions"]}
        assert categories == {"freelance", "cashback"}
