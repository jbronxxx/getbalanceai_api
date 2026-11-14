"""Сервис управления бюджетами и расчет фактически израсходованных средств."""

import enum
import hashlib
import uuid
from datetime import timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, case, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, NotFoundException
from app.models.models import Budget, Category, Transaction, TransactionType, User
from app.redis import (
    BUDGETS_VERSION_TTL_SECONDS,
    get_budgets_family_version_key,
    get_budgets_version_key,
    get_redis,
)
from app.schemas.schemas import BudgetCreate, BudgetResponse
from app.services.currency_service import CurrencyService
from app.services.family_access import SharedAccess, raise_access_error
from app.services.period import month_bounds, timezone_key, year_bounds
from logger.logger import get_logger

logger = get_logger(__name__)


class BudgetScope(str, enum.Enum):
    """Какие бюджеты нужны: все видимые, только личные или только общие семьи."""

    all = "all"
    personal = "personal"
    family = "family"


class BudgetService:
    """Класс бизнес-логики для установки бюджетов и подсчета остатков лимитов.

    Личный лимит считает личные траты владельца, общий — общие траты всех участников семьи. Общие лимиты
    создают, правят и удаляют все участники незамороженной семьи.
    """

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных."""
        self.db = db

    async def create(
        self,
        user_id: uuid.UUID,
        payload: BudgetCreate,
        user_base_currency: str | None = None,
        tz: tzinfo = timezone.utc,
    ) -> BudgetResponse:
        """Создать новый бюджет или атомарно обновить существующий лимит (Upsert).

        Использует dialect-aware ON CONFLICT DO UPDATE для предотвращения race condition
        при параллельных запросах на одну категорию и период. Без явной валюты лимит
        ведётся в базовой валюте пользователя. Общий лимит (`is_shared`) — один на семью: правка чужого
        общего лимита автора не меняет.

        Исключения: FAMILY_NOT_FOUND (404) — общий лимит без семьи; FAMILY_FROZEN (403) — семья заморожена.
        """
        access = await SharedAccess.load(self.db, user_id)
        error = access.write_error(None, payload.is_shared)
        if error is not None:
            raise_access_error(error)
        family_id = access.target_family(payload.is_shared)
        currency = payload.currency or (user_base_currency or "USD").strip().upper()
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
                family_id=family_id,
                category=payload.category,
                limit_amount=payload.limit_amount,
                currency=currency,
                month=payload.month,
                year=payload.year,
            )
            .on_conflict_do_update(
                # Частичные уникальные индексы: личный ключ — по автору, общий — по семье
                index_elements=["family_id" if family_id else "user_id", "category", "month", "year"],
                index_where=text("family_id IS NOT NULL" if family_id else "family_id IS NULL"),
                # Без явной валюты у существующего лимита валюта сохраняется, как и в /sync
                set_={"limit_amount": payload.limit_amount, **({"currency": payload.currency} if payload.currency else {})},
            )
            .returning(Budget)
        )
        result = await self.db.execute(stmt)
        budget = result.scalar_one()
        await self.db.flush()
        await self.db.refresh(budget)
        await self.invalidate_etag(user_id, family_id)
        logger.info(f"Сохранён бюджет {budget.id} для пользователя {user_id}")
        return await self._enrich(budget, user_id, user_base_currency=user_base_currency, tz=tz)

    async def get_by_id(
        self,
        user_id: uuid.UUID,
        budget_id: uuid.UUID,
        user_base_currency: str | None = None,
        tz: tzinfo = timezone.utc,
    ) -> BudgetResponse:
        """Получить личный или общий бюджет по его идентификатору."""
        access = await SharedAccess.load(self.db, user_id)
        query = select(Budget).where(Budget.id == budget_id, access.visible(Budget))
        result = await self.db.execute(query)
        budget = result.scalar_one_or_none()
        if not budget:
            logger.warning(f"Бюджет {budget_id} не найден для пользователя {user_id}")
            raise NotFoundException(
                code=ErrorCode.BUDGET_NOT_FOUND,
                message="Бюджет не найден",
                details={"budget_id": str(budget_id)},
            )
        return await self._enrich(budget, user_id, user_base_currency=user_base_currency, tz=tz)

    async def get_all(
        self,
        user_id: uuid.UUID,
        month: int | None,
        year: int | None,
        user_base_currency: str | None = None,
        tz: tzinfo = timezone.utc,
        scope: BudgetScope = BudgetScope.all,
        access: SharedAccess | None = None,
    ) -> list[BudgetResponse]:
        """Получить личные и общие бюджеты пользователя с фильтрацией по месяцу, году и области."""
        access = access or await SharedAccess.load(self.db, user_id)
        if scope is BudgetScope.personal:
            condition = access.personal(Budget)
        elif scope is BudgetScope.family:
            if access.family_id is None:
                return []
            condition = Budget.family_id == access.family_id
        else:
            condition = access.visible(Budget)
        query = select(Budget).where(condition)
        if month:
            query = query.where(Budget.month == month)
        if year:
            query = query.where(Budget.year == year)

        result = await self.db.execute(query)
        budgets = list(result.scalars().all())
        logger.debug(f"Получено {len(budgets)} бюджетов для пользователя {user_id} с фильтром месяц={month}, год={year}")
        return await self.enrich_multiple(budgets, user_id, user_base_currency=user_base_currency, tz=tz)

    async def delete(self, user_id: uuid.UUID, budget_id: uuid.UUID) -> None:
        """Удалить личный или общий бюджет по его идентификатору; общий в замороженной семье — 403 FAMILY_FROZEN."""
        access = await SharedAccess.load(self.db, user_id)
        query = select(Budget).where(Budget.id == budget_id, access.visible(Budget))
        result = await self.db.execute(query)
        budget = result.scalar_one_or_none()

        if not budget:
            logger.warning(f"Попытка удаления несуществующего бюджета {budget_id} для пользователя {user_id}")
            raise NotFoundException(
                code=ErrorCode.BUDGET_NOT_FOUND,
                message="Бюджет не найден",
                details={"budget_id": str(budget_id)},
            )
        error = access.delete_error(budget)
        if error is not None:
            raise_access_error(error, {"budget_id": str(budget_id)})
        family_id = budget.family_id
        await self.db.delete(budget)
        await self.db.flush()
        await self.invalidate_etag(user_id, family_id)
        logger.info(f"Бюджет {budget_id} удалён пользователем {user_id}")

    async def delete_by_category_period(
        self, user_id: uuid.UUID, category: Category, month: int, year: int, shared: bool = False
    ) -> bool:
        """Удалить личный (или общий при `shared`) бюджет по категории, месяцу и году; нет такого — False.

        Исключения: FAMILY_FROZEN (403) — общий бюджет замороженной семьи.
        """
        access = await SharedAccess.load(self.db, user_id)
        if shared:
            if access.family_id is None:
                return False
            scope = Budget.family_id == access.family_id
        else:
            scope = access.personal(Budget)
        query = select(Budget).where(
            scope,
            Budget.category == category,
            Budget.month == month,
            Budget.year == year,
        )
        result = await self.db.execute(query)
        budget = result.scalar_one_or_none()

        if not budget:
            return False
        error = access.delete_error(budget)
        if error is not None:
            raise_access_error(error)
        family_id = budget.family_id
        await self.db.delete(budget)
        await self.db.flush()
        await self.invalidate_etag(user_id, family_id)
        logger.info(f"Бюджет {budget.id} ({category} {month}/{year}) удалён пользователем {user_id}")
        return True

    @staticmethod
    async def invalidate_etag(user_id: uuid.UUID, family_id: uuid.UUID | None = None) -> None:
        """Инвалидировать ETag кэш бюджетов пользователя (и всех участников семьи — при правке общих записей)."""
        try:
            client = get_redis()
            version = str(uuid.uuid4())
            await client.set(get_budgets_version_key(user_id), version, ex=BUDGETS_VERSION_TTL_SECONDS)
            if family_id is not None:
                await client.set(get_budgets_family_version_key(family_id), version, ex=BUDGETS_VERSION_TTL_SECONDS)
            logger.debug(f"Инвалидирован ETag кэш бюджетов для пользователя {user_id}")
        except Exception as err:
            logger.warning(f"Не удалось обновить ключ budgets_version в Redis для {user_id}: {err}")

    async def get_etag(
        self,
        user_id: uuid.UUID,
        month: int | None = None,
        year: int | None = None,
        base_currency: str | None = None,
        tz: tzinfo = timezone.utc,
    ) -> str:
        """Быстрый расчет ETag по видимым бюджетам и расходам (личным и общим), базовой валюте и поясу клиента."""
        access = await SharedAccess.load(self.db, user_id)
        if not base_currency:
            user_res = await self.db.execute(select(User.base_currency).where(User.id == user_id))
            base_currency = (user_res.scalar_one_or_none() or "USD").strip().upper()
        else:
            base_currency = base_currency.strip().upper()

        b_query = select(
            func.count(Budget.id),
            func.coalesce(func.sum(Budget.limit_amount), Decimal("0.00")),
            func.max(Budget.created_at),
        ).where(access.visible(Budget))

        if month:
            b_query = b_query.where(Budget.month == month)
        if year:
            b_query = b_query.where(Budget.year == year)

        b_res = await self.db.execute(b_query)
        b_count, b_sum_limits, b_max_created = b_res.first()

        t_query = select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.base_amount), Decimal("0.00")),
            func.max(Transaction.updated_at),
        ).where(
            access.visible(Transaction),
            Transaction.type == TransactionType.expense,
        )

        if month and year:
            start_date, end_date = month_bounds(year, month, tz)
            t_query = t_query.where(Transaction.date >= start_date, Transaction.date < end_date)
        elif year:
            start_date, end_date = year_bounds(year, tz)
            t_query = t_query.where(Transaction.date >= start_date, Transaction.date < end_date)
        elif month:
            # Месяц без года — по UTC: фильтр без года клиенты не используют
            t_query = t_query.where(func.extract("month", Transaction.date) == month)

        t_res = await self.db.execute(t_query)
        t_count, t_sum_spent, t_max_updated = t_res.first()

        redis_version = ""
        try:
            keys = [get_budgets_version_key(user_id)]
            if access.family_id is not None:
                keys.append(get_budgets_family_version_key(access.family_id))
            redis_version = ":".join(str(val or "") for val in await get_redis().mget(keys))
        except Exception:
            pass

        raw_str = (
            f"{user_id}:{access.family_id or ''}:{base_currency}:{timezone_key(tz)}:"
            f"{b_count}:{b_sum_limits}:{b_max_created.isoformat() if b_max_created else ''}:"
            f"{t_count}:{t_sum_spent}:{t_max_updated.isoformat() if t_max_updated else ''}:{redis_version}"
        )
        return hashlib.md5(raw_str.encode(), usedforsecurity=False).hexdigest()

    async def enrich_multiple(
        self,
        budgets: list[Budget],
        user_id: uuid.UUID,
        user_base_currency: str | None = None,
        tz: tzinfo = timezone.utc,
    ) -> list[BudgetResponse]:
        """Обогатить список бюджетов агрегированными данными о расходах напрямую из БД.

        Расходы агрегируются одним запросом с группировкой по (семья, category, month, year, currency)
        и диапазонными фильтрами дат. Личный лимит считает личные траты пользователя, общий — общие траты
        семьи лимита. Траты переводятся в валюту лимита: операции в той же валюте — по сумме чека, личный
        лимит в базовой валюте — по base_amount, остальное — по текущему кросс-курсу (base_amount общих
        операций — в базовых валютах разных авторов). Курсы запрашиваются, только если конвертация нужна.
        """
        if not budgets:
            return []

        if user_base_currency:
            base_currency = user_base_currency.strip().upper()
        else:
            user_res = await self.db.execute(select(User.base_currency).where(User.id == user_id))
            base_currency = (user_res.scalar_one_or_none() or "USD").strip().upper()

        periods = {(b.month, b.year) for b in budgets}
        categories = {b.category for b in budgets}
        family_ids = {b.family_id for b in budgets if b.family_id is not None}
        scopes = []
        if any(b.family_id is None for b in budgets):
            scopes.append(and_(Transaction.user_id == user_id, Transaction.family_id.is_(None)))
        if family_ids:
            scopes.append(Transaction.family_id.in_(family_ids))

        # Месяц бюджета — по часам клиента: диапазоны дат в его поясе, переведённые в UTC. Период операции
        # определяется попаданием в диапазон, а не EXTRACT(month) от даты в UTC: иначе для UTC+5 траты
        # 1-го числа до 05:00 уходили в прошлый месяц. Диапазоны же используют индекс (user_id, type, date)
        period_ranges = {(m, y): month_bounds(y, m, tz) for m, y in periods}
        date_conditions = [and_(Transaction.date >= start, Transaction.date < end) for start, end in period_ranges.values()]
        period_expr = case(
            *[
                (and_(Transaction.date >= start, Transaction.date < end), literal(f"{y}-{m}"))
                for (m, y), (start, end) in period_ranges.items()
            ]
        )

        tx_query = (
            select(
                Transaction.family_id,
                Transaction.category,
                period_expr.label("period"),
                Transaction.currency,
                func.coalesce(func.sum(Transaction.amount), Decimal("0.00")).label("amount"),
                func.coalesce(func.sum(Transaction.base_amount), Decimal("0.00")).label("base_amount"),
            )
            .where(
                or_(*scopes),
                Transaction.type == TransactionType.expense,
                Transaction.category.in_(categories),
                or_(*date_conditions),
            )
            .group_by(Transaction.family_id, Transaction.category, period_expr, Transaction.currency)
        )

        result = await self.db.execute(tx_query)
        # (семья или None, категория, месяц, год) -> [(валюта операций, сумма в валюте чека, сумма в базовой валюте)]
        groups: dict[tuple[uuid.UUID | None, Category, int, int], list[tuple[str, Decimal, Decimal]]] = {}
        for rec in result.all():
            period_year, period_month = (int(part) for part in rec.period.split("-"))
            key = (rec.family_id, rec.category, period_month, period_year)
            groups.setdefault(key, []).append(
                (str(rec.currency).strip().upper(), Decimal(str(rec.amount)), Decimal(str(rec.base_amount)))
            )

        def currency_of(budget: Budget) -> str:
            return (budget.currency or base_currency).strip().upper()

        def uses_base_amount(budget: Budget) -> bool:
            # base_amount — в базовой валюте автора: годится только для личного лимита в моей базовой валюте
            return budget.family_id is None and currency_of(budget) == base_currency

        def spending(budget: Budget) -> list[tuple[str, Decimal, Decimal]]:
            return groups.get((budget.family_id, budget.category, budget.month, budget.year), [])

        # Курсы нужны только лимитам, траты которых нельзя взять по base_amount, с тратами в другой валюте
        needs_rates = any(
            tx_currency != currency_of(b) for b in budgets if not uses_base_amount(b) for tx_currency, _, _ in spending(b)
        )
        rates = (await CurrencyService().get_rates()).rates if needs_rates else None

        enriched = []
        for b in budgets:
            budget_currency = currency_of(b)
            spent = Decimal("0.00")
            for tx_currency, amount, base_amount in spending(b):
                if tx_currency == budget_currency:
                    spent += amount
                elif uses_base_amount(b):
                    spent += base_amount
                else:
                    spent += amount * CurrencyService.calculate_cross_rate(tx_currency, budget_currency, rates)
            spent = spent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            limit_amount = Decimal(str(b.limit_amount)) if b.limit_amount is not None else Decimal("0.00")
            remaining = max(Decimal("0.00"), limit_amount - spent)
            enriched.append(
                BudgetResponse(
                    id=b.id,
                    user_id=b.user_id,
                    family_id=b.family_id,
                    category=b.category,
                    limit_amount=limit_amount,
                    currency=budget_currency,
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
        tz: tzinfo = timezone.utc,
    ) -> BudgetResponse:
        """Обогатить объект бюджета агрегированными данными о фактических расходах."""
        enriched_list = await self.enrich_multiple([budget], user_id, user_base_currency=user_base_currency, tz=tz)
        return enriched_list[0]
