"""Эндпоинт пакетной синхронизации оффлайн данных транзакций и бюджетов."""

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException, BadRequestException, ErrorCode
from app.models.models import Budget, Category, Transaction, User
from app.schemas.schemas import ApiResponse, ErrorResponse, SyncPayload, SyncResponse
from app.services.auth_service import get_current_user
from app.services.budget_service import BudgetService
from app.services.currency_service import CurrencyService
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
    description=(
        "Пакетная синхронизация транзакций и бюджетов. Выполняет идемпотентный UPSERT "
        "для бюджетов по ID или составному ключу (category + month + year) без дубликатов "
        "и без нарушения уникальных ограничений базы данных."
    ),
)
async def sync_data(
    payload: SyncPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = current_user.id
    user_base_currency = (current_user.base_currency or "RUB").strip().upper()
    logger.info(
        f"Начало синхронизации для пользователя {user_id} (валюта {user_base_currency}), "
        f"Транзакций: {len(payload.transactions)}, "
        f"Бюджетов: {len(payload.budgets)}, "
        f"Удаляемых ID бюджетов: {len(payload.deleted_budget_ids)}, "
        f"Удаляемых бюджетов (объектов): {len(payload.deleted_budgets)}"
    )
    synced_transactions = []
    synced_budgets = []
    budget_service = BudgetService(db)

    try:
        currency_service = CurrencyService()
        rates_response = await currency_service.get_rates()
        rates = rates_response.rates

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

        seen_tx_ids: set[uuid.UUID] = set()
        for item in payload.transactions:
            logger.debug(f"Синхронизация транзакции: {item}")
            tx_id = item.id or uuid.uuid4()
            if tx_id in seen_tx_ids:
                continue

            curr = (item.currency or user_base_currency).strip().upper()
            rate, base_amt = CurrencyService.resolve_amounts(
                amount=item.amount,
                currency=curr,
                base_currency=user_base_currency,
                client_rate=item.exchange_rate,
                client_base_amount=item.base_amount,
                rates=rates,
            )

            quantized_amount = item.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if item.id and item.id in existing_txs_map:
                db_transaction = existing_txs_map[item.id]
                db_transaction.amount = quantized_amount
                db_transaction.currency = curr
                db_transaction.exchange_rate = rate
                db_transaction.base_amount = base_amt
                db_transaction.description = item.description
                db_transaction.category = item.category
                db_transaction.type = item.type
                db_transaction.date = item.date or db_transaction.date
            else:
                db_transaction = Transaction(
                    id=tx_id,
                    user_id=user_id,
                    amount=quantized_amount,
                    currency=curr,
                    exchange_rate=rate,
                    base_amount=base_amt,
                    description=item.description,
                    category=item.category,
                    type=item.type,
                    date=item.date or datetime.now(timezone.utc),
                )
                db.add(db_transaction)
                existing_txs_map[tx_id] = db_transaction

            seen_tx_ids.add(tx_id)
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
            conditions = [
                Budget.user_id == user_id,
                Budget.category == item.category,
                Budget.month == item.month,
                Budget.year == item.year,
            ]
            if item.id:
                conditions = [
                    Budget.user_id == user_id,
                    (Budget.id == item.id)
                    | ((Budget.category == item.category) & (Budget.month == item.month) & (Budget.year == item.year)),
                ]
            await db.execute(delete(Budget).where(*conditions))

        # 4. Сохраняем, обновляем или удаляем бюджеты из payload.budgets
        to_delete_budgets = [item for item in payload.budgets if item.check_deleted]
        to_upsert_budgets = [item for item in payload.budgets if not item.check_deleted]

        for item in to_delete_budgets:
            logger.debug(f"Удаление бюджета при синхронизации: {item.category} ({item.month}/{item.year})")
            if item.id:
                await db.execute(
                    delete(Budget).where(
                        Budget.user_id == user_id,
                        (Budget.id == item.id)
                        | ((Budget.category == item.category) & (Budget.month == item.month) & (Budget.year == item.year)),
                    )
                )
            else:
                await db.execute(
                    delete(Budget).where(
                        Budget.user_id == user_id,
                        Budget.category == item.category,
                        Budget.month == item.month,
                        Budget.year == item.year,
                    )
                )

        if to_upsert_budgets:
            # Загружаем существующие бюджеты пользователя для корректного идемпотентного UPSERT
            query = select(Budget).where(Budget.user_id == user_id)
            result = await db.execute(query)
            existing_user_budgets = list(result.scalars().all())

            existing_by_id: dict[uuid.UUID, Budget] = {b.id: b for b in existing_user_budgets}
            existing_by_key: dict[tuple[Category, int, int], Budget] = {
                (b.category, b.month, b.year): b for b in existing_user_budgets
            }

            seen_budget_ids: set[uuid.UUID] = set()

            for item in to_upsert_budgets:
                item_key = (item.category, item.month, item.year)
                target_budget: Budget | None = None

                # 1. Поиск по составному ключу (category + month + year) имеет приоритет для предотвращения UniqueConstraint
                if item_key in existing_by_key:
                    target_budget = existing_by_key[item_key]
                    logger.debug(f"Обновление лимита бюджета по составному ключу {item_key}: {target_budget.id}")
                    target_budget.limit_amount = item.limit_amount
                # 2. Если по составному ключу не найден, но передан ID — ищем по ID
                elif item.id and item.id in existing_by_id:
                    target_budget = existing_by_id[item.id]
                    logger.debug(f"Обновление бюджета по существующему ID {item.id}")
                    old_key = (target_budget.category, target_budget.month, target_budget.year)
                    existing_by_key.pop(old_key, None)
                    target_budget.category = item.category
                    target_budget.month = item.month
                    target_budget.year = item.year
                    target_budget.limit_amount = item.limit_amount
                    existing_by_key[item_key] = target_budget
                # 3. Иначе создаем новый бюджет, сохраняя клиентский ID при наличии
                else:
                    budget_id = item.id or uuid.uuid4()
                    logger.debug(f"Создание нового бюджета {budget_id} для {item_key}")
                    target_budget = Budget(
                        id=budget_id,
                        user_id=user_id,
                        category=item.category,
                        limit_amount=item.limit_amount,
                        month=item.month,
                        year=item.year,
                    )
                    db.add(target_budget)
                    existing_by_id[budget_id] = target_budget
                    existing_by_key[item_key] = target_budget

                if target_budget.id not in seen_budget_ids:
                    seen_budget_ids.add(target_budget.id)
                    synced_budgets.append(target_budget)

        await db.commit()
        # Обновляем объекты из БД после коммита
        for b in synced_budgets:
            await db.refresh(b)
        for t in synced_transactions:
            await db.refresh(t)

        if synced_budgets:
            await BudgetService.invalidate_etag(user_id)

        enriched_budgets = await budget_service.enrich_multiple(
            synced_budgets,
            user_id,
            user_base_currency=user_base_currency,
        )

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
