"""Сервис управления бюджетами и расчет фактически израсходованных средств."""

import hashlib
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, NotFoundException
from app.models.models import Budget, Category, Transaction, TransactionType, User
from app.schemas.schemas import BudgetCreate, BudgetResponse
from app.services.currency_service import CurrencyService
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
            return await self._enrich(existing_budget, user_id, user_base_currency=user_base_currency)

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
        return await self._enrich(budget, user_id, user_base_currency=user_base_currency)

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

    @staticmethod
    async def invalidate_etag(user_id: uuid.UUID) -> None:
        """Инвалидировать ETag кэш бюджетов пользователя в Redis при смене базовой валюты или обновлении."""
        try:
            from app.services.ai_service import redis_client

            await redis_client.set(f"budgets_version:{user_id}", str(uuid.uuid4()))
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
            base_currency = (user_res.scalar_one_or_none() or "RUB").strip().upper()
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

        if month:
            t_query = t_query.where(func.extract("month", Transaction.date) == month)
        if year:
            t_query = t_query.where(func.extract("year", Transaction.date) == year)

        t_res = await self.db.execute(t_query)
        t_count, t_sum_spent, t_max_created = t_res.first()

        redis_version = ""
        try:
            from app.services.ai_service import redis_client

            val = await redis_client.get(f"budgets_version:{user_id}")
            if val:
                redis_version = str(val)
        except Exception:
            pass

        raw_str = (
            f"{user_id}:{base_currency}:{b_count}:{b_sum_limits}:{b_max_created.isoformat() if b_max_created else ''}:"
            f"{t_count}:{t_sum_spent}:{t_max_created.isoformat() if t_max_created else ''}:{redis_version}"
        )
        return hashlib.md5(raw_str.encode()).hexdigest()

    async def enrich_multiple(
        self,
        budgets: list[Budget],
        user_id: uuid.UUID,
        user_base_currency: str | None = None,
    ) -> list[BudgetResponse]:
        """Обогатить список бюджетов агрегированными данными о расходах с учетом валюты каждой транзакции."""
        if not budgets:
            return []

        if not user_base_currency:
            user_res = await self.db.execute(select(User.base_currency).where(User.id == user_id))
            user_base_currency = (user_res.scalar_one_or_none() or "RUB").strip().upper()
        else:
            user_base_currency = user_base_currency.strip().upper()

        currency_service = CurrencyService()
        rates_response = await currency_service.get_rates()
        rates = rates_response.rates

        tx_query = select(
            Transaction.category,
            func.extract("month", Transaction.date).label("month"),
            func.extract("year", Transaction.date).label("year"),
            Transaction.amount,
            Transaction.currency,
            Transaction.exchange_rate,
            Transaction.base_amount,
        ).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.expense,
        )

        periods = {(b.month, b.year) for b in budgets}
        if len(periods) == 1:
            m, y = next(iter(periods))
            tx_query = tx_query.where(
                func.extract("month", Transaction.date) == m,
                func.extract("year", Transaction.date) == y,
            )

        result = await self.db.execute(tx_query)
        transactions_records = result.all()

        spent_map: dict[tuple[Category, int, int], Decimal] = {}
        for rec in transactions_records:
            cat = rec.category
            m = int(rec.month)
            y = int(rec.year)
            key = (cat, m, y)

            tx_curr = (rec.currency or user_base_currency).strip().upper()
            amt = Decimal(str(rec.amount))

            if tx_curr == user_base_currency:
                tx_spent = amt
            else:
                base_amt = Decimal(str(rec.base_amount)) if rec.base_amount is not None else Decimal("0.00")
                rate = Decimal(str(rec.exchange_rate)) if rec.exchange_rate is not None else Decimal("1.0")

                if base_amt > Decimal("0.00") and rate != Decimal("1.0"):
                    tx_spent = base_amt
                else:
                    cross_rate = CurrencyService.calculate_cross_rate(tx_curr, user_base_currency, rates)
                    tx_spent = (amt * cross_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            spent_map[key] = spent_map.get(key, Decimal("0.00")) + tx_spent

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
