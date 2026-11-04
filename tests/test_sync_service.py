"""Unit-тесты SyncService без HTTP-слоя: итог синхронизации, дубли в батче и откат при ошибке БД."""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppException, ErrorCode
from app.models.models import Budget, Transaction, User
from app.schemas.schemas import SyncPayload, SyncResponse
from app.services.sync_service import SyncService


def _transaction(tx_id: uuid.UUID, amount: float = 100.0) -> dict:
    return {
        "id": str(tx_id),
        "amount": amount,
        "description": "Кофе",
        "category": "food",
        "type": "expense",
        "date": "2026-10-01T12:00:00Z",
    }


async def _count(db: AsyncSession, model: type, user_id: uuid.UUID) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(model.user_id == user_id))).scalar_one()


@pytest.mark.asyncio
async def test_sync_returns_typed_response_and_persists(db_session: AsyncSession, test_user: User):
    """Сервис возвращает SyncResponse и фиксирует транзакции и бюджеты в БД."""
    tx_id = uuid.uuid4()
    payload = SyncPayload.model_validate(
        {
            "transactions": [_transaction(tx_id)],
            "budgets": [{"category": "food", "limit_amount": 500.0, "month": 10, "year": 2026}],
        }
    )

    result = await SyncService(db_session).sync(test_user, payload)

    assert isinstance(result, SyncResponse)
    assert [t.id for t in result.synced_transactions] == [tx_id]
    assert len(result.synced_budgets) == 1
    assert result.rejected == []
    assert await _count(db_session, Transaction, test_user.id) == 1
    assert await _count(db_session, Budget, test_user.id) == 1


@pytest.mark.asyncio
async def test_sync_duplicate_ids_in_batch_saved_once(db_session: AsyncSession, test_user: User):
    """Повтор одного ID в батче сохраняет транзакцию один раз — по первому вхождению."""
    tx_id = uuid.uuid4()
    payload = SyncPayload.model_validate({"transactions": [_transaction(tx_id, 100.0), _transaction(tx_id, 200.0)]})

    result = await SyncService(db_session).sync(test_user, payload)

    assert len(result.synced_transactions) == 1
    assert result.synced_transactions[0].amount == 100
    assert await _count(db_session, Transaction, test_user.id) == 1


@pytest.mark.asyncio
async def test_sync_database_error_rolls_back_and_raises_sync_failed(db_session: AsyncSession, test_user: User):
    """Ошибка БД откатывает весь батч и превращается в SYNC_FAILED (500)."""
    user_id = test_user.id
    payload = SyncPayload.model_validate({"transactions": [_transaction(uuid.uuid4())]})

    with patch.object(db_session, "flush", side_effect=SQLAlchemyError("boom")):
        with pytest.raises(AppException) as exc_info:
            await SyncService(db_session).sync(test_user, payload)

    assert exc_info.value.code == ErrorCode.SYNC_FAILED
    assert exc_info.value.status_code == 500
    assert await _count(db_session, Transaction, user_id) == 0
