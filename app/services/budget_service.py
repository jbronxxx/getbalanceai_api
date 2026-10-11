"""Сервис управления бюджетами и расчет фактически израсходованных средств."""

import hashlib
import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, NotFoundException
from app.models.models import Budget, Category, Transaction, TransactionType
from app.schemas.schemas import BudgetCreate, BudgetResponse
from logger.logger import get_logger

logger = get_logger(__name__)


class BudgetService:
    """Класс бизнес-логики для установки бюджетов и подсчета остатков лимитов."""

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных."""
        self.db = db

    async def create(self, user_id: uuid.UUID, payload: BudgetCreate) -> BudgetResponse:
        """Создать новый бюджет или обновить существующий лимит расходов по категории на указанный период."""
        query = select(Budget).where(
            Budget.user_id == user_id,
            Budget.category == payload.category,
            Budget.month == payload.month,
            Budget.year == payload.year,
        )
        result = await self.db.execute(query)
        existing_budget = result.scalar_one_or_none()

        if existing_budget:
            logger.info(f"Обновление существующего бюджета {existing_budget.id} для пользователя {user_id}")
            existing_budget.limit_amount = payload.limit_amount
            await self.db.flush()
            await self.db.refresh(existing_budget)
            return await self._enrich(existing_budget, user_id)

        budget = Budget(
            user_id=user_id,
            category=payload.category,
            limit_amount=payload.limit_amount,
            month=payload.month,
            year=payload.year,
        )
        self.db.add(budget)
        await self.db.flush()
        await self.db.refresh(budget)
        logger.info(f"Создан новый бюджет: {budget}")
        return await self._enrich(budget, user_id)

    async def get_by_id(self, user_id: uuid.UUID, budget_id: uuid.UUID) -> BudgetResponse:
        """Получить бюджет по его идентификатору."""
        query = select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
        result = await self.db.execute(query)
        budget = result.scalar_one_or_none()
        if not budget:
            logger.warning(f"Бюджет {budget_id} не найден для пользователя {user_id}")
            raise NotFoundException(
                code=ErrorCode.BUDGET_NOT_FOUND,
                message="Бюджет не найден",
                details={"budget_id": str(budget_id)},
            )
        return await self._enrich(budget, user_id)

    async def get_all(self, user_id: uuid.UUID, month: int | None, year: int | None) -> list[BudgetResponse]:
        """Получить список всех бюджетов пользователя с фильтрацией по месяцу и году."""
        query = select(Budget).where(Budget.user_id == user_id)
        if month:
            query = query.where(Budget.month == month)
        if year:
            query = query.where(Budget.year == year)

        result = await self.db.execute(query)
        budgets = list(result.scalars().all())
        logger.info(f"Получено {len(budgets)} бюджетов для пользователя {user_id} с фильтром месяц={month}, год={year}")
        return await self.enrich_multiple(budgets, user_id)

    async def delete(self, user_id: uuid.UUID, budget_id: uuid.UUID) -> None:
        """Удалить бюджет по его идентификатору."""
        query = select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
        result = await self.db.execute(query)
        budget = result.scalar_one_or_none()

        if not budget:
            logger.warning(f"Попытка удаления несуществующего бюджета {budget_id} для пользователя {user_id}")
            raise NotFoundException(
                code=ErrorCode.BUDGET_NOT_FOUND,
                message="Бюджет не найден",
                details={"budget_id": str(budget_id)},
            )
        await self.db.delete(budget)
        await self.db.flush()
        logger.info(f"Бюджет удален: {budget_id}")

    async def delete_by_category_period(self, user_id: uuid.UUID, category: Category, month: int, year: int) -> bool:
        """Удалить бюджет по категории, месяцу и году."""
        query = select(Budget).where(
            Budget.user_id == user_id,
            Budget.category == category,
            Budget.month == month,
            Budget.year == year,
        )
        result = await self.db.execute(query)
        budget = result.scalar_one_or_none()

        if not budget:
            return False
        await self.db.delete(budget)
        await self.db.flush()
        logger.info(f"Бюджет удален по категории и периоду: {category} ({month}/{year})")
        return True

    async def get_etag(self, user_id: uuid.UUID, month: int | None = None, year: int | None = None) -> str:
        """Быстрый расчет ETag без выгрузки всех объектов бюджетов."""
        query = select(func.count(Budget.id), func.max(Budget.created_at)).where(Budget.user_id == user_id)

        if month:
            query = query.where(Budget.month == month)
        if year:
            query = query.where(Budget.year == year)

        result = await self.db.execute(query)
        count, max_created = result.first()
        raw_str = f"{user_id}:{count}:{max_created.isoformat() if max_created else ''}"
        return hashlib.md5(raw_str.encode()).hexdigest()

    async def enrich_multiple(self, budgets: list[Budget], user_id: uuid.UUID) -> list[BudgetResponse]:
        """Обогатить список бюджетов агрегированными данными о фактических расходах за один SQL-запрос."""
        if not budgets:
            return []

        query = (
            select(
                Transaction.category,
                func.extract("month", Transaction.date).label("month"),
                func.extract("year", Transaction.date).label("year"),
                func.sum(
                    case((Transaction.base_amount > Decimal("0.0"), Transaction.base_amount), else_=Transaction.amount)
                ).label("spent"),
            )
            .where(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.expense,
            )
            .group_by(
                Transaction.category,
                func.extract("month", Transaction.date),
                func.extract("year", Transaction.date),
            )
        )

        result = await self.db.execute(query)
        expenses_records = result.all()

        spent_map = {(rec.category, int(rec.month), int(rec.year)): Decimal(str(rec.spent)) for rec in expenses_records}

        enriched = []
        for b in budgets:
            spent = spent_map.get((b.category, b.month, b.year), Decimal("0.00"))
            limit_amount = Decimal(str(b.limit_amount)) if b.limit_amount is not None else Decimal("0.00")
            remaining = max(Decimal("0.00"), limit_amount - spent)
            enriched.append(
                BudgetResponse(
                    id=b.id,
                    category=b.category,
                    limit_amount=limit_amount,
                    month=b.month,
                    year=b.year,
                    spent=spent,
                    remaining=remaining,
                )
            )
        return enriched

    async def _enrich(self, budget: Budget, user_id: uuid.UUID) -> BudgetResponse:
        """Обогатить объект бюджета агрегированными данными о фактических расходах."""
        enriched_list = await self.enrich_multiple([budget], user_id)
        return enriched_list[0]
