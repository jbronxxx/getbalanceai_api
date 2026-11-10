"""Сервис регулярных платежей и доходов: список с ETag, сохранение и удаление из пакетной синхронизации."""

import hashlib
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode
from app.models.models import RecurringPayment
from app.schemas.schemas import RecurringPaymentSyncItem, SyncRejectedItem
from logger.logger import get_logger

logger = get_logger(__name__)


class RecurringPaymentService:
    """Регулярные платежи пользователя.

    Клиент забирает весь список (у пользователя их единицы) и сверяет с локальным: платёж, которого нет
    в списке, удалён на другом устройстве. Поэтому отдельного журнала удалений нет.
    """

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных.

        Аргументы:
            db (AsyncSession): Асинхронная сессия SQLAlchemy.
        """
        self.db = db

    async def get_all(self, user_id: uuid.UUID) -> list[RecurringPayment]:
        """Все регулярные платежи пользователя по дню месяца."""
        result = await self.db.execute(
            select(RecurringPayment)
            .where(RecurringPayment.user_id == user_id)
            .order_by(RecurringPayment.day_of_month.asc(), RecurringPayment.created_at.asc(), RecurringPayment.id.asc())
        )
        return list(result.scalars().all())

    async def get_etag(self, user_id: uuid.UUID) -> str:
        """ETag списка: число платежей и время последнего изменения.

        Правка меняет updated_at, создание — и число, и время, удаление — число.
        """
        result = await self.db.execute(
            select(func.count(RecurringPayment.id), func.max(RecurringPayment.updated_at)).where(
                RecurringPayment.user_id == user_id
            )
        )
        count, last_updated = result.one()
        stamp = last_updated.isoformat() if last_updated is not None else ""
        return hashlib.sha256(f"{user_id}:{count}:{stamp}".encode("utf-8")).hexdigest()

    async def upsert(
        self,
        user_id: uuid.UUID,
        items: list[RecurringPaymentSyncItem],
        rejected: list[SyncRejectedItem],
    ) -> list[RecurringPayment]:
        """Создать платежи или заменить существующие по id; чужой id — в rejected, повторы id в батче пропускаются."""
        if not items:
            return []
        result = await self.db.execute(select(RecurringPayment).where(RecurringPayment.id.in_({item.id for item in items})))
        existing: dict[uuid.UUID, RecurringPayment] = {}
        foreign_ids: set[uuid.UUID] = set()
        for payment in result.scalars().all():
            if payment.user_id == user_id:
                existing[payment.id] = payment
            else:
                foreign_ids.add(payment.id)

        synced: list[RecurringPayment] = []
        seen_ids: set[uuid.UUID] = set()
        for item in items:
            if item.id in seen_ids:
                continue
            if item.id in foreign_ids:
                logger.warning("Конфликт ID регулярного платежа %s: принадлежит другому пользователю", item.id)
                rejected.append(
                    SyncRejectedItem(
                        id=item.id,
                        code=ErrorCode.ID_CONFLICT,
                        reason="Идентификатор регулярного платежа отклонен (конфликт идентификатора)",
                    )
                )
                continue

            fields = item.model_dump(exclude={"id"})
            fields["amount"] = item.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            payment = existing.get(item.id)
            if payment is None:
                payment = RecurringPayment(id=item.id, user_id=user_id, **fields)
                self.db.add(payment)
                existing[item.id] = payment
            else:
                for name, value in fields.items():
                    setattr(payment, name, value)
            seen_ids.add(item.id)
            synced.append(payment)
        return synced

    async def delete(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
        """Удалить платежи пользователя; чужие и несуществующие id игнорируются. Записи прошлых месяцев остаются."""
        if not ids:
            return
        await self.db.execute(
            delete(RecurringPayment).where(RecurringPayment.user_id == user_id, RecurringPayment.id.in_(set(ids)))
        )
