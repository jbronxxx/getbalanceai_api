"""Сервис управления бюджетами и расчет фактически израсходованных средств."""

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, NotFoundException
from app.models.models import Budget, Category, Transaction, TransactionType, User
from app.redis import get_budgets_version_key, get_redis
from app.schemas.schemas import BudgetCreate, BudgetResponse
from logger.logger import get_logger

logger = get_logger(__name__)


class BudgetService:
    """Класс бизнес-логики для установки бюджетов и подсчета остатков лимитов."""

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных."""
        self.db = db

    async def create(
        self,
        user_id: uuid.UUID,
        payload: BudgetCreate,
        user_base_currency: str | None = None,
    ) -> BudgetResponse:
        """Создать новый бюджет или атомарно обновить существующий лимит (Upsert).

        Использует dialect-aware ON CONFLICT DO UPDATE для предотвращения race condition
        при параллельных запросах на одну категорию и период.
        """
        bind = getattr(self.db, "bind", None) or self.db.get_bind()
        dialect_name = bind.dialect.name
        if dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            from sqlalchemy.dialects.postgresql import insert as dialect_insert

        stmt = (
            dialect_insert(Budget)
            .values(
                user_id=user_id,
                category=payload.category,
                limit_amount=payload.limit_amount,
                month=payload.month,
                year=payload.year,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "category", "month", "year"],
                set_={"limit_amount": payload.limit_amount},
            )
            .returning(Budget)
        )
        result = await self.db.execute(stmt)
        budget = result.scalar_one()
        await self.db.flush()
        await self.db.refresh(budget)
        await self.invalidate_etag(user_id)
        logger.info(f"Успешно сохранен бюджет: {budget.id} для пользователя {user_id}")
        return await self._enrich(budget, user_id, user_base_currency=user_base_currency)

    async def get_by_id(
        self,
        user_id: uuid.UUID,
        budget_id: uuid.UUID,
        user_base_currency: str | None = None,
    ) -> BudgetResponse:
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
        return await self._enrich(budget, user_id, user_base_currency=user_base_currency)

    async def get_all(
        self,
        user_id: uuid.UUID,
        month: int | None,
        year: int | None,
        user_base_currency: str | None = None,
    ) -> list[BudgetResponse]:
        """Получить список всех бюджетов пользователя с фильтрацией по месяцу и году."""
        query = select(Budget).where(Budget.user_id == user_id)
        if month:
            query = query.where(Budget.month == month)
        if year:
            query = query.where(Budget.year == year)

        result = await self.db.execute(query)
        budgets = list(result.scalars().all())
        logger.info(f"Получено {len(budgets)} бюджетов для пользователя {user_id} с фильтром месяц={month}, год={year}")
        return await self.enrich_multiple(budgets, user_id, user_base_currency=user_base_currency)

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
        await self.invalidate_etag(user_id)
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
        await self.invalidate_etag(user_id)
        logger.info(f"Бюджет удален по категории и периоду: {category} ({month}/{year})")
        return True

    @staticmethod
    async def invalidate_etag(user_id: uuid.UUID) -> None:
        """Инвалидировать ETag кэш бюджетов пользователя в Redis при смене базовой валюты или обновлении."""
        try:
            client = get_redis()
            await client.set(get_budgets_version_key(user_id), str(uuid.uuid4()))
            logger.info(f"Инвалидирован ETag кэш бюджетов для пользователя {user_id}")
        except Exception as err:
            logger.warning(f"Не удалось обновить ключ budgets_version в Redis для {user_id}: {err}")

    async def get_etag(
        self,
        user_id: uuid.UUID,
        month: int | None = None,
        year: int | None = None,
        base_currency: str | None = None,
    ) -> str:
        """Быстрый расчет ETag с учетом лимитов бюджетов, расходов и базовой валюты пользователя."""
        if not base_currency:
            user_res = await self.db.execute(select(User.base_currency).where(User.id == user_id))
            base_currency = (user_res.scalar_one_or_none() or "USD").strip().upper()
        else:
            base_currency = base_currency.strip().upper()

        b_query = select(
            func.count(Budget.id),
            func.coalesce(func.sum(Budget.limit_amount), Decimal("0.00")),
            func.max(Budget.created_at),
        ).where(Budget.user_id == user_id)

        if month:
            b_query = b_query.where(Budget.month == month)
        if year:
            b_query = b_query.where(Budget.year == year)

        b_res = await self.db.execute(b_query)
        b_count, b_sum_limits, b_max_created = b_res.first()

        t_query = select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.base_amount), Decimal("0.00")),
            func.max(Transaction.created_at),
        ).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.expense,
        )

        if month and year:
            start_date = datetime(year, month, 1, tzinfo=timezone.utc)
            end_date = (
                datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                if month == 12
                else datetime(year, month + 1, 1, tzinfo=timezone.utc)
            )
            t_query = t_query.where(Transaction.date >= start_date, Transaction.date < end_date)
        elif year:
            start_date = datetime(year, 1, 1, tzinfo=timezone.utc)
            end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            t_query = t_query.where(Transaction.date >= start_date, Transaction.date < end_date)
        elif month:
            t_query = t_query.where(func.extract("month", Transaction.date) == month)

        t_res = await self.db.execute(t_query)
        t_count, t_sum_spent, t_max_created = t_res.first()

        redis_version = ""
        try:
            client = get_redis()
            val = await client.get(get_budgets_version_key(user_id))
            if val:
                redis_version = str(val)
        except Exception:
            pass

        raw_str = (
            f"{user_id}:{base_currency}:{b_count}:{b_sum_limits}:{b_max_created.isoformat() if b_max_created else ''}:"
            f"{t_count}:{t_sum_spent}:{t_max_created.isoformat() if t_max_created else ''}:{redis_version}"
        )
        return hashlib.md5(raw_str.encode(), usedforsecurity=False).hexdigest()

    async def enrich_multiple(
        self,
        budgets: list[Budget],
        user_id: uuid.UUID,
        user_base_currency: str | None = None,
    ) -> list[BudgetResponse]:
        """Обогатить список бюджетов агрегированными данными о расходах напрямую из БД.

        Расходы агрегируются через SUM(Transaction.base_amount) с группировкой по (category, month, year)
        и диапазонными фильтрами дат, что гарантирует консистентность с ETag и задействует индекс БД.
        """
        if not budgets:
            return []

        periods = {(b.month, b.year) for b in budgets}
        categories = {b.category for b in budgets}

        # Диапазонные условия по датам для использования составного индекса (user_id, type, date)
        date_conditions = []
        for m, y in periods:
            start_date = datetime(y, m, 1, tzinfo=timezone.utc)
            end_date = datetime(y + 1, 1, 1, tzinfo=timezone.utc) if m == 12 else datetime(y, m + 1, 1, tzinfo=timezone.utc)
            date_conditions.append(and_(Transaction.date >= start_date, Transaction.date < end_date))

        month_expr = func.extract("month", Transaction.date)
        year_expr = func.extract("year", Transaction.date)

        tx_query = (
            select(
                Transaction.category,
                month_expr.label("month"),
                year_expr.label("year"),
                func.coalesce(func.sum(Transaction.base_amount), Decimal("0.00")).label("spent"),
            )
            .where(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.expense,
                Transaction.category.in_(categories),
                or_(*date_conditions),
            )
            .group_by(Transaction.category, month_expr, year_expr)
        )

        result = await self.db.execute(tx_query)
        spent_map: dict[tuple[Category, int, int], Decimal] = {}
        for rec in result.all():
            cat = rec.category
            m = int(rec.month)
            y = int(rec.year)
            spent_map[(cat, m, y)] = Decimal(str(rec.spent))

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

    async def _enrich(
        self,
        budget: Budget,
        user_id: uuid.UUID,
        user_base_currency: str | None = None,
    ) -> BudgetResponse:
        """Обогатить объект бюджета агрегированными данными о фактических расходах."""
        enriched_list = await self.enrich_multiple([budget], user_id, user_base_currency=user_base_currency)
        return enriched_list[0]
