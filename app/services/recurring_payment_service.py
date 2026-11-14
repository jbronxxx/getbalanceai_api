"""Сервис регулярных платежей и доходов: список с ETag, сохранение и удаление из пакетной синхронизации."""

import hashlib
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import RecurringPayment
from app.schemas.schemas import RecurringPaymentSyncItem, SyncRejectedItem
from app.services.family_access import SharedAccess, rejected_item
from logger.logger import get_logger

logger = get_logger(__name__)


class RecurringPaymentService:
    """Регулярные платежи пользователя: личные и общие семьи.

    Клиент забирает весь список (у пользователя их единицы) и сверяет с локальным: платёж, которого нет
    в списке, удалён на другом устройстве или другим участником семьи. Поэтому отдельного журнала удалений нет.
    """

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных.

        Аргументы:
            db (AsyncSession): Асинхронная сессия SQLAlchemy.
        """
        self.db = db

    async def get_all(self, user_id: uuid.UUID) -> list[RecurringPayment]:
        """Личные и общие регулярные платежи пользователя по дню месяца."""
        access = await SharedAccess.load(self.db, user_id)
        result = await self.db.execute(
            select(RecurringPayment)
            .where(access.visible(RecurringPayment))
            .order_by(RecurringPayment.day_of_month.asc(), RecurringPayment.created_at.asc(), RecurringPayment.id.asc())
        )
        return list(result.scalars().all())

    async def get_etag(self, user_id: uuid.UUID) -> str:
        """ETag списка: число платежей и время последнего изменения.

        Правка меняет updated_at, создание — и число, и время, удаление — число; в ETag входит и семья.
        """
        access = await SharedAccess.load(self.db, user_id)
        result = await self.db.execute(
            select(func.count(RecurringPayment.id), func.max(RecurringPayment.updated_at)).where(
                access.visible(RecurringPayment)
            )
        )
        count, last_updated = result.one()
        stamp = last_updated.isoformat() if last_updated is not None else ""
        raw = f"{user_id}:{access.family_id or ''}:{count}:{stamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def upsert(
        self,
        access: SharedAccess,
        items: list[RecurringPaymentSyncItem],
        rejected: list[SyncRejectedItem],
    ) -> list[RecurringPayment]:
        """Создать платежи или заменить существующие по id; повторы id в батче пропускаются.

        Чужой невидимый id, общий платёж замороженной семьи и смена отметки не автором — в rejected.
        """
        if not items:
            return []
        result = await self.db.execute(select(RecurringPayment).where(RecurringPayment.id.in_({item.id for item in items})))
        existing: dict[uuid.UUID, RecurringPayment] = {payment.id: payment for payment in result.scalars().all()}

        synced: list[RecurringPayment] = []
        seen_ids: set[uuid.UUID] = set()
        for item in items:
            if item.id in seen_ids:
                continue
            payment = existing.get(item.id)
            error = access.write_error(payment, item.is_shared)
            if error is not None:
                logger.warning("Регулярный платёж %s отклонён при синхронизации: %s", item.id, error)
                rejected.append(rejected_item(item.id, error, "регулярного платежа"))
                continue

            fields = item.model_dump(exclude={"id", "is_shared"})
            fields["amount"] = item.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            fields["family_id"] = access.target_family(item.is_shared)
            if payment is None:
                payment = RecurringPayment(id=item.id, user_id=access.user_id, **fields)
                self.db.add(payment)
                existing[item.id] = payment
            else:
                for name, value in fields.items():
                    setattr(payment, name, value)
            seen_ids.add(item.id)
            synced.append(payment)
        return synced

    async def delete(self, access: SharedAccess, ids: list[uuid.UUID]) -> None:
        """Удалить личные и общие платежи пользователя; чужие, несуществующие и общие замороженной семьи id
        игнорируются (клиент сверит полный список). Записи прошлых месяцев остаются."""
        if not ids:
            return
        await self.db.execute(
            delete(RecurringPayment).where(access.writable(RecurringPayment), RecurringPayment.id.in_(set(ids)))
        )
