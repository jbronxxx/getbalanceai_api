"""Эндпоинт пакетной синхронизации оффлайн данных транзакций и бюджетов."""

import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, or_, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import AppException, ErrorCode
from app.models.models import Budget, Category, Transaction, User
from app.schemas.schemas import (
    ApiResponse,
    ErrorResponse,
    SyncPayload,
    SyncRejectedItem,
    SyncResponse,
)
from app.services.auth_service import get_current_user
from app.services.budget_service import BudgetService
from app.services.currency_service import CurrencyService
from logger.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Ошибка валидации или параметров синхронизации",
        },
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "Доступ запрещен"},
        422: {"model": ErrorResponse, "description": "Ошибка валидации входных данных"},
        500: {
            "model": ErrorResponse,
            "description": "Внутренняя ошибка сервера при сохранении данных",
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
        "и без нарушения уникальных ограничений базы данных. Конфликтующие записи возвращаются в rejected."
    ),
)
async def sync_data(
    payload: SyncPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = current_user.id
    user_base_currency = (current_user.base_currency or "USD").strip().upper()
    logger.info(
        "Начало синхронизации для пользователя %s (валюта %s): txs=%s, budgets=%s, del_txs=%s, del_ids=%s, del_budgets=%s",
        user_id,
        user_base_currency,
        len(payload.transactions),
        len(payload.budgets),
        len(payload.deleted_transaction_ids),
        len(payload.deleted_budget_ids),
        len(payload.deleted_budgets),
    )
    synced_transactions: list[Transaction] = []
    synced_budgets: list[Budget] = []
    rejected_items: list[SyncRejectedItem] = []
    budget_service = BudgetService(db)

    try:
        # Ленивая загрузка курсов: только если среди транзакций есть отличная от базовой валюта
        has_multicurrency = any(
            (item.currency or user_base_currency).strip().upper() != user_base_currency for item in payload.transactions
        )
        rates = None
        if has_multicurrency:
            currency_service = CurrencyService()
            rates_response = await currency_service.get_rates()
            rates = rates_response.rates

        # 1. Сохраняем или обновляем транзакции (Upsert) с разрешением конфликтов ID (TASK-12)
        incoming_tx_ids = [item.id for item in payload.transactions if item.id is not None]
        existing_txs_map: dict[uuid.UUID, Transaction] = {}
        conflict_tx_ids: set[uuid.UUID] = set()

        if incoming_tx_ids:
            query = select(Transaction).where(Transaction.id.in_(incoming_tx_ids))
            result = await db.execute(query)
            for t in result.scalars().all():
                if t.user_id == user_id:
                    existing_txs_map[t.id] = t
                else:
                    conflict_tx_ids.add(t.id)

        seen_tx_ids: set[uuid.UUID] = set()
        for item in payload.transactions:
            tx_id = item.id or uuid.uuid4()
            if tx_id in seen_tx_ids:
                continue

            # TASK-12: чужой/конфликтный ID отклоняется без падения всего батча
            if item.id and item.id in conflict_tx_ids:
                logger.warning("Конфликт ID транзакции %s: принадлежит другому пользователю", item.id)
                rejected_items.append(
                    SyncRejectedItem(
                        id=item.id,
                        reason="Идентификатор транзакции отклонен (конфликт идентификатора)",
                    )
                )
                continue

            # TASK-13: безопасное логирование без персональных финансовых данных
            logger.debug("Обработка транзакции с ID: %s", tx_id)

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

        # 2. Пакетное удаление транзакций (идемпотентное по user_id)
        if payload.deleted_transaction_ids:
            del_tx_ids = set(payload.deleted_transaction_ids)
            logger.debug("Пакетное удаление транзакций: IDs=%s", len(del_tx_ids))
            await db.execute(
                delete(Transaction).where(
                    Transaction.user_id == user_id,
                    Transaction.id.in_(del_tx_ids),
                )
            )

        # 3. Пакетное удаление бюджетов (TASK-11: объединение всех условий удаления в один запрос)
        to_delete_budgets = [item for item in payload.budgets if item.check_deleted]
        to_upsert_budgets = [item for item in payload.budgets if not item.check_deleted]

        del_budget_ids: set[uuid.UUID] = set(payload.deleted_budget_ids)
        del_budget_tuples: set[tuple[Category, int, int]] = set()

        for item in payload.deleted_budgets:
            if item.id:
                del_budget_ids.add(item.id)
            del_budget_tuples.add((item.category, item.month, item.year))

        for item in to_delete_budgets:
            if item.id:
                del_budget_ids.add(item.id)
            del_budget_tuples.add((item.category, item.month, item.year))

        delete_conditions = []
        if del_budget_ids:
            delete_conditions.append(Budget.id.in_(del_budget_ids))
        if del_budget_tuples:
            delete_conditions.append(tuple_(Budget.category, Budget.month, Budget.year).in_(del_budget_tuples))

        if delete_conditions:
            logger.debug(
                "Пакетное удаление бюджетов: IDs=%s, tuples=%s",
                len(del_budget_ids),
                len(del_budget_tuples),
            )
            await db.execute(
                delete(Budget).where(
                    Budget.user_id == user_id,
                    or_(*delete_conditions),
                )
            )

        # 3. Сохранение и обновление бюджетов (TASK-11: загрузка только целевых бюджетов вместо всей истории)
        if to_upsert_budgets:
            upsert_ids = [item.id for item in to_upsert_budgets if item.id is not None]
            upsert_tuples = [(item.category, item.month, item.year) for item in to_upsert_budgets]

            # Проверка конфликта чужих ID для бюджетов (TASK-12)
            conflict_budget_ids: set[uuid.UUID] = set()
            if upsert_ids:
                foreign_budgets_res = await db.execute(
                    select(Budget.id).where(
                        Budget.id.in_(upsert_ids),
                        Budget.user_id != user_id,
                    )
                )
                conflict_budget_ids = set(foreign_budgets_res.scalars().all())

            load_conditions = []
            if upsert_ids:
                load_conditions.append(Budget.id.in_(upsert_ids))
            if upsert_tuples:
                load_conditions.append(tuple_(Budget.category, Budget.month, Budget.year).in_(upsert_tuples))

            existing_user_budgets: list[Budget] = []
            if load_conditions:
                b_query = select(Budget).where(
                    Budget.user_id == user_id,
                    or_(*load_conditions),
                )
                b_result = await db.execute(b_query)
                existing_user_budgets = list(b_result.scalars().all())

            existing_by_id: dict[uuid.UUID, Budget] = {b.id: b for b in existing_user_budgets}
            existing_by_key: dict[tuple[Category, int, int], Budget] = {
                (b.category, b.month, b.year): b for b in existing_user_budgets
            }

            seen_budget_ids: set[uuid.UUID] = set()

            for item in to_upsert_budgets:
                if item.id and item.id in conflict_budget_ids:
                    logger.warning("Конфликт ID бюджета %s: принадлежит другому пользователю", item.id)
                    rejected_items.append(
                        SyncRejectedItem(
                            id=item.id,
                            reason="Идентификатор бюджета отклонен (конфликт идентификатора)",
                        )
                    )
                    continue

                item_key = (item.category, item.month, item.year)
                target_budget: Budget | None = None

                # 1. Поиск по составному ключу (category + month + year) имеет приоритет для предотвращения UniqueConstraint
                if item_key in existing_by_key:
                    target_budget = existing_by_key[item_key]
                    logger.debug("Обновление лимита бюджета по составному ключу: %s", target_budget.id)
                    target_budget.limit_amount = item.limit_amount
                    # Старые клиенты валюту не передают — валюта лимита сохраняется
                    if item.currency:
                        target_budget.currency = item.currency
                # 2. Если по составному ключу не найден, но передан ID — ищем по ID
                elif item.id and item.id in existing_by_id:
                    target_budget = existing_by_id[item.id]
                    logger.debug("Обновление бюджета по существующему ID: %s", item.id)
                    old_key = (target_budget.category, target_budget.month, target_budget.year)
                    existing_by_key.pop(old_key, None)
                    target_budget.category = item.category
                    target_budget.month = item.month
                    target_budget.year = item.year
                    target_budget.limit_amount = item.limit_amount
                    if item.currency:
                        target_budget.currency = item.currency
                    existing_by_key[item_key] = target_budget
                # 3. Иначе создаем новый бюджет, сохраняя клиентский ID при наличии
                else:
                    budget_id = item.id or uuid.uuid4()
                    logger.debug("Создание нового бюджета %s", budget_id)
                    target_budget = Budget(
                        id=budget_id,
                        user_id=user_id,
                        category=item.category,
                        limit_amount=item.limit_amount,
                        currency=item.currency or user_base_currency,
                        month=item.month,
                        year=item.year,
                    )
                    db.add(target_budget)
                    existing_by_id[budget_id] = target_budget
                    existing_by_key[item_key] = target_budget

                if target_budget.id not in seen_budget_ids:
                    seen_budget_ids.add(target_budget.id)
                    synced_budgets.append(target_budget)

        # TASK-11: flush + commit с expire_on_commit=False исключает N+1 запросов db.refresh()
        await db.flush()
        db.sync_session.expire_on_commit = False
        await db.commit()

        if (
            synced_budgets
            or synced_transactions
            or payload.deleted_transaction_ids
            or payload.deleted_budgets
            or payload.deleted_budget_ids
            or to_delete_budgets
        ):
            await BudgetService.invalidate_etag(user_id)

        enriched_budgets = await budget_service.enrich_multiple(
            synced_budgets,
            user_id,
            user_base_currency=user_base_currency,
        )

    except AppException:
        await db.rollback()
        raise
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(
            "Ошибка базы данных при синхронизации пользователя %s: %s",
            user_id,
            str(e),
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
            "Непредвиденная ошибка при синхронизации пользователя %s: %s",
            user_id,
            str(e),
            exc_info=True,
        )
        raise AppException(
            code=ErrorCode.SYNC_FAILED,
            message="Внутренняя ошибка сервера при синхронизации",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    logger.info("Синхронизация успешно завершена для пользователя %s", user_id)
    return {
        "status": "success",
        "data": {
            "synced_transactions": synced_transactions,
            "synced_budgets": enriched_budgets,
            "rejected": rejected_items,
        },
    }
