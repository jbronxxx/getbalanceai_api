"""Журнал удалённых транзакций для инкрементальной синхронизации клиентов."""

import uuid
from collections.abc import Collection
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import TransactionDeletion


class TransactionDeletionLog:
    """Записывает удаления транзакций, чтобы GET /sync/changes отдал их другим устройствам пользователя."""

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных.

        Аргументы:
            db (AsyncSession): Асинхронная сессия SQLAlchemy.
        """
        self.db = db

    async def record(self, user_id: uuid.UUID, transaction_ids: Collection[uuid.UUID]) -> None:
        """Отметить транзакции пользователя удалёнными сейчас; прежние отметки тех же id заменяются."""
        ids = set(transaction_ids)
        if not ids:
            return
        await self.db.execute(delete(TransactionDeletion).where(TransactionDeletion.transaction_id.in_(ids)))
        deleted_at = datetime.now(timezone.utc)
        self.db.add_all([TransactionDeletion(transaction_id=tx_id, user_id=user_id, deleted_at=deleted_at) for tx_id in ids])
        await self.db.flush()

    async def forget(self, user_id: uuid.UUID, transaction_ids: Collection[uuid.UUID]) -> None:
        """Снять отметки удаления с транзакций, созданных заново с теми же id."""
        ids = set(transaction_ids)
        if not ids:
            return
        await self.db.execute(
            delete(TransactionDeletion).where(
                TransactionDeletion.user_id == user_id,
                TransactionDeletion.transaction_id.in_(ids),
            )
        )
