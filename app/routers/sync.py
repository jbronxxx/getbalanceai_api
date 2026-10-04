"""Эндпоинт пакетной синхронизации оффлайн данных транзакций и бюджетов."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException, BadRequestException, ErrorCode
from app.models.models import Budget, Transaction, User
from app.schemas.schemas import ApiResponse, ErrorResponse, SyncPayload, SyncResponse
from app.services.auth_service import get_current_user
from app.services.budget_service import BudgetService
from logger.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Ошибка пакетной синхронизации (SYNC_FAILED)",
        },
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "Доступ запрещен"},
        422: {"model": ErrorResponse, "description": "Ошибка валидации входных данных"},
        500: {
            "model": ErrorResponse,
            "description": "Внутренняя ошибка сервера при сохранении",
        },
    }
)


@router.post(
    "/",
    response_model=ApiResponse[SyncResponse],
    status_code=status.HTTP_200_OK,
    summary="Синхронизировать данные",
)
async def sync_data(
    payload: SyncPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = current_user.id
    logger.info(
        f"Начало синхронизации для пользователя {user_id}, "
        f"Транзакций: {len(payload.transactions)}, "
        f"Бюджетов: {len(payload.budgets)}, "
        f"Удаляемых ID бюджетов: {len(payload.deleted_budget_ids)}, "
        f"Удаляемых бюджетов (объектов): {len(payload.deleted_budgets)}"
    )
    synced_transactions = []
    synced_budgets = []
    budget_service = BudgetService(db)

    try:
        # 1. Сохраняем или обновляем транзакции (Upsert)
        incoming_tx_ids = [item.id for item in payload.transactions if item.id is not None]
        existing_txs_map = {}
        if incoming_tx_ids:
            query = select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.id.in_(incoming_tx_ids),
            )
            result = await db.execute(query)
            existing_txs = list(result.scalars().all())
            existing_txs_map = {t.id: t for t in existing_txs}

        for item in payload.transactions:
            logger.debug(f"Синхронизация транзакции: {item}")
            if item.id and item.id in existing_txs_map:
                db_transaction = existing_txs_map[item.id]
                db_transaction.amount = item.amount
                db_transaction.description = item.description
                db_transaction.category = item.category
                db_transaction.type = item.type
                db_transaction.date = item.date or db_transaction.date
            else:
                db_transaction = Transaction(
                    id=item.id or uuid.uuid4(),
                    user_id=user_id,
                    amount=item.amount,
                    description=item.description,
                    category=item.category,
                    type=item.type,
                    date=item.date or datetime.now(timezone.utc),
                )
                db.add(db_transaction)
                if item.id:
                    existing_txs_map[item.id] = db_transaction

            synced_transactions.append(db_transaction)

        # 2. Удаляем бюджеты по ID из payload.deleted_budget_ids
        if payload.deleted_budget_ids:
            logger.debug(f"Удаление бюджетов по ID в процессе синхронизации: {payload.deleted_budget_ids}")
            await db.execute(
                delete(Budget).where(
                    Budget.user_id == user_id,
                    Budget.id.in_(payload.deleted_budget_ids),
                )
            )

        # 3. Удаляем бюджеты из payload.deleted_budgets
        for item in payload.deleted_budgets:
            logger.debug(
                f"Удаление бюджета по категории/периоду из deleted_budgets: {item.category} ({item.month}/{item.year})"
            )
            await db.execute(
                delete(Budget).where(
                    Budget.user_id == user_id,
                    Budget.category == item.category,
                    Budget.month == item.month,
                    Budget.year == item.year,
                )
            )

        # 4. Сохраняем, обновляем или удаляем бюджеты из payload.budgets
        to_delete_budgets = [item for item in payload.budgets if item.check_deleted]
        to_upsert_budgets = [item for item in payload.budgets if not item.check_deleted]

        for item in to_delete_budgets:
            logger.debug(f"Удаление бюджета при синхронизации: {item.category} ({item.month}/{item.year})")
            await db.execute(
                delete(Budget).where(
                    Budget.user_id == user_id,
                    Budget.category == item.category,
                    Budget.month == item.month,
                    Budget.year == item.year,
                )
            )

        if to_upsert_budgets:
            query = select(Budget).where(Budget.user_id == user_id)
            result = await db.execute(query)
            existing_user_budgets = list(result.scalars().all())
            existing_budget_map = {(b.category, b.month, b.year): b for b in existing_user_budgets}

            for item in to_upsert_budgets:
                key = (item.category, item.month, item.year)
                if key in existing_budget_map:
                    existing_budget = existing_budget_map[key]
                    logger.debug(f"Обновление лимита бюджета для {item.category} ({item.month}/{item.year})")
                    existing_budget.limit_amount = item.limit_amount
                    synced_budgets.append(existing_budget)
                else:
                    logger.debug(f"Создание нового бюджета для {item.category} ({item.month}/{item.year})")
                    db_budget = Budget(
                        user_id=user_id,
                        category=item.category,
                        limit_amount=item.limit_amount,
                        month=item.month,
                        year=item.year,
                    )
                    db.add(db_budget)
                    existing_budget_map[key] = db_budget
                    synced_budgets.append(db_budget)

        await db.commit()
        # Обновляем объекты из БД после коммита
        for b in synced_budgets:
            await db.refresh(b)
        for t in synced_transactions:
            await db.refresh(t)

        enriched_budgets = await budget_service.enrich_multiple(synced_budgets, user_id)

    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(
            f"Ошибка базы данных при синхронизации пользователя {user_id}: {str(e)}",
            exc_info=True,
        )
        raise AppException(
            code=ErrorCode.SYNC_FAILED,
            message="Ошибка при сохранении данных в базу",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    except Exception as e:
        await db.rollback()
        logger.error(
            f"Непредвиденная ошибка при синхронизации пользователя {user_id}: {str(e)}",
            exc_info=True,
        )
        raise BadRequestException(
            code=ErrorCode.SYNC_FAILED,
            message="Не удалось обработать запрос синхронизации",
        )

    logger.info(f"Синхронизация успешно завершена для пользователя {user_id}")
    return {
        "status": "success",
        "data": {
            "synced_transactions": synced_transactions,
            "synced_budgets": enriched_budgets,
        },
    }
