"""Сервис управления финансовыми транзакциями (доходы и расходы)."""

import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, NotFoundException
from app.models.models import Transaction, User
from app.schemas.schemas import TransactionCreate
from app.services.currency_service import CurrencyService
from logger.logger import get_logger

logger = get_logger(__name__)


class TransactionService:
    """Класс бизнес-логики для создания, получения и удаления финансовых операций."""

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных.

        Аргументы:
            db (AsyncSession): Асинхронная сессия SQLAlchemy.
        """
        self.db = db

    async def create(
        self,
        user_id: uuid.UUID,
        payload: TransactionCreate,
        user_base_currency: str | None = None,
    ) -> Transaction:
        """Создать новую транзакцию (доход или расход) для пользователя."""
        if not user_base_currency:
            user_res = await self.db.execute(select(User.base_currency).where(User.id == user_id))
            user_base_currency = (user_res.scalar_one_or_none() or "RUB").strip().upper()
        else:
            user_base_currency = user_base_currency.strip().upper()

        curr = (payload.currency or user_base_currency).strip().upper()

        rates = None
        if curr != user_base_currency:
            currency_service = CurrencyService()
            rates_resp = await currency_service.get_rates()
            rates = rates_resp.rates

        rate, base_amt = CurrencyService.resolve_amounts(
            amount=payload.amount,
            currency=curr,
            base_currency=user_base_currency,
            client_rate=payload.exchange_rate,
            client_base_amount=payload.base_amount,
            rates=rates,
        )

        tx = Transaction(
            user_id=user_id,
            amount=payload.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            currency=curr,
            exchange_rate=rate,
            base_amount=base_amt,
            description=payload.description,
            category=payload.category,
            type=payload.type,
            date=payload.date or datetime.now(timezone.utc),
        )
        self.db.add(tx)
        await self.db.flush()
        await self.db.refresh(tx)
        logger.info(f"Создана новая транзакция: {tx}")
        return tx

    async def get_all(
        self,
        user_id: uuid.UUID,
        since: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Transaction], bool, str | None]:
        """Получение транзакций с поддержкой курсорной пагинации (keyset pagination) и фильтрации."""

        query = select(Transaction).where(Transaction.user_id == user_id)
        if since:
            query = query.where(Transaction.created_at >= since)

        if cursor:
            try:
                cursor_data = json.loads(base64.b64decode(cursor).decode("utf-8"))
                cursor_date = datetime.fromisoformat(cursor_data["d"])
                if cursor_date.tzinfo is None:
                    cursor_date = cursor_date.replace(tzinfo=timezone.utc)
                cursor_id = uuid.UUID(cursor_data["i"])

                query = query.where(
                    or_(
                        Transaction.date < cursor_date,
                        and_(Transaction.date == cursor_date, Transaction.id < cursor_id),
                    )
                )
            except Exception as e:
                logger.warning(f"Некорректный курсор {cursor}: {e}")

        query = query.order_by(Transaction.date.desc(), Transaction.id.desc())

        # Запрашиваем на 1 больше, чтобы определить, есть ли следующая страница
        query = query.limit(limit + 1)

        result = await self.db.execute(query)
        items = list(result.scalars().all())
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        next_cursor = None
        if items:
            last_item = items[-1]
            cursor_data = {"d": last_item.date.isoformat(), "i": str(last_item.id)}
            next_cursor = base64.b64encode(json.dumps(cursor_data).encode("utf-8")).decode("utf-8")

        return items, has_more, next_cursor

    async def delete(self, user_id: uuid.UUID, transaction_id: uuid.UUID) -> None:
        """Удалить транзакцию по ее идентификатору."""
        query = select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user_id)
        result = await self.db.execute(query)
        tx = result.scalar_one_or_none()

        if not tx:
            logger.warning(f"Попытка удаления несуществующей транзакции {transaction_id} для пользователя {user_id}")
            raise NotFoundException(
                code=ErrorCode.TRANSACTION_NOT_FOUND,
                message="Транзакция не найдена",
                details={"transaction_id": str(transaction_id)},
            )
        await self.db.delete(tx)
        await self.db.flush()
        logger.info(f"Транзакция удалена: {tx.id}")

    async def get_etag(
        self,
        user_id: uuid.UUID,
        since: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> str:
        """Быстрый расчет ETag без выгрузки всех объектов с учетом пагинации (без count)."""
        query = select(func.max(Transaction.created_at)).where(Transaction.user_id == user_id)

        if since:
            query = query.where(Transaction.created_at >= since)

        result = await self.db.execute(query)
        max_created = result.scalar_one_or_none()

        raw_str = f"{user_id}:{max_created.isoformat() if max_created else ''}:{limit}:{cursor or ''}"
        return hashlib.md5(raw_str.encode()).hexdigest()
