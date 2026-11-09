"""Сервис пакетной синхронизации оффлайн-данных клиента: транзакций и бюджетов."""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal

from fastapi import status
from sqlalchemy import and_, delete, or_, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppException, BadRequestException, ErrorCode
from app.models.models import Budget, Category, Transaction, TransactionDeletion, User
from app.schemas.schemas import (
    BudgetSyncItem,
    SyncChangesResponse,
    SyncPayload,
    SyncRejectedItem,
    SyncResponse,
    TransactionResponse,
    TransactionSyncItem,
)
from app.services.budget_service import BudgetService
from app.services.currency_service import CurrencyService
from app.services.transaction_deletion_log import TransactionDeletionLog
from logger.logger import get_logger

logger = get_logger(__name__)

BudgetKey = tuple[Category, int, int]

# Насколько расширять окно since назад: запас на транзакции, закоммиченные после отметки прошлой синхронизации
CHANGES_OVERLAP = timedelta(minutes=1)


class SyncService:
    """Применяет батч изменений клиента одной транзакцией БД.

    Записи с чужим ID или суммой, которую нельзя пересчитать в базовую валюту, не роняют батч,
    а возвращаются в rejected: иначе клиент бесконечно повторял бы весь батч.
    """

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных.

        Аргументы:
            db (AsyncSession): Асинхронная сессия SQLAlchemy.
        """
        self.db = db

    async def sync(self, user: User, payload: SyncPayload, tz: tzinfo = timezone.utc) -> SyncResponse:
        """Сохранить транзакции и бюджеты клиента, применить удаления и вернуть итог синхронизации.

        Аргументы:
            user (User): Текущий пользователь.
            payload (SyncPayload): Батч изменений клиента.
            tz (tzinfo): Часовой пояс клиента для границ месяцев в обогащённых бюджетах.

        Исключения:
            AppException: SYNC_FAILED (500) при ошибке БД или непредвиденном сбое; изменения откатываются.
        """
        user_id = user.id
        base_currency = (user.base_currency or "USD").strip().upper()
        logger.info(
            "Начало синхронизации для пользователя %s (валюта %s): "
            "txs=%s, budgets=%s, del_txs=%s, del_ids=%s, del_budgets=%s",
            user_id,
            base_currency,
            len(payload.transactions),
            len(payload.budgets),
            len(payload.deleted_transaction_ids),
            len(payload.deleted_budget_ids),
            len(payload.deleted_budgets),
        )
        rejected: list[SyncRejectedItem] = []
        to_delete_budgets = [item for item in payload.budgets if item.check_deleted]
        to_upsert_budgets = [item for item in payload.budgets if not item.check_deleted]

        try:
            synced_transactions = await self._upsert_transactions(user_id, base_currency, payload.transactions, rejected)
            await self._delete_transactions(user_id, payload.deleted_transaction_ids)
            await self._delete_budgets(user_id, payload, to_delete_budgets)
            synced_budgets = await self._upsert_budgets(user_id, base_currency, to_upsert_budgets, rejected)

            # flush + commit с expire_on_commit=False: объекты остаются загруженными, ответ собирается без refresh()
            await self.db.flush()
            self.db.sync_session.expire_on_commit = False
            await self.db.commit()

            if (
                synced_budgets
                or synced_transactions
                or payload.deleted_transaction_ids
                or payload.deleted_budgets
                or payload.deleted_budget_ids
                or to_delete_budgets
            ):
                await BudgetService.invalidate_etag(user_id)

            enriched_budgets = await BudgetService(self.db).enrich_multiple(
                synced_budgets,
                user_id,
                user_base_currency=base_currency,
                tz=tz,
            )
        except AppException:
            await self.db.rollback()
            raise
        except SQLAlchemyError as e:
            await self.db.rollback()
            logger.error("Ошибка базы данных при синхронизации пользователя %s: %s", user_id, str(e), exc_info=True)
            raise AppException(
                code=ErrorCode.SYNC_FAILED,
                message="Ошибка при сохранении данных в базу",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            await self.db.rollback()
            logger.error("Непредвиденная ошибка при синхронизации пользователя %s: %s", user_id, str(e), exc_info=True)
            raise AppException(
                code=ErrorCode.SYNC_FAILED,
                message="Внутренняя ошибка сервера при синхронизации",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info("Синхронизация успешно завершена для пользователя %s", user_id)
        return SyncResponse(
            synced_transactions=[TransactionResponse.model_validate(t) for t in synced_transactions],
            synced_budgets=enriched_budgets,
            rejected=rejected,
        )

    async def get_changes(
        self,
        user_id: uuid.UUID,
        since: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> SyncChangesResponse:
        """Транзакции, изменённые после since, и id удалённых после since.

        Без since — все транзакции пользователя (первая синхронизация устройства), удалённых нет.
        Порядок — по (updated_at, id), курсор продолжает с последней записи страницы. Удалённые
        отдаются только с первой страницей (без курсора).

        Окно since расширяется на CHANGES_OVERLAP назад: updated_at ставится при записи, а видна
        строка после коммита, и транзакция, закоммиченная чуть позже прошлой отметки, иначе
        потерялась бы. Повторно отданные записи клиент применяет идемпотентно.

        Аргументы:
            user_id (uuid.UUID): Владелец данных.
            since (datetime | None): server_time первой страницы прошлой синхронизации.
            cursor (str | None): next_cursor предыдущей страницы.
            limit (int): Размер страницы.

        Исключения:
            BadRequestException: INVALID_CURSOR при повреждённом курсоре.
        """
        server_time = datetime.now(timezone.utc)
        lower_bound: datetime | None = None
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            lower_bound = since - CHANGES_OVERLAP

        query = select(Transaction).where(Transaction.user_id == user_id)
        if lower_bound is not None:
            query = query.where(Transaction.updated_at > lower_bound)
        if cursor:
            cursor_updated_at, cursor_id = self._decode_changes_cursor(cursor, user_id)
            query = query.where(
                or_(
                    Transaction.updated_at > cursor_updated_at,
                    and_(Transaction.updated_at == cursor_updated_at, Transaction.id > cursor_id),
                )
            )
        query = query.order_by(Transaction.updated_at.asc(), Transaction.id.asc()).limit(limit + 1)

        result = await self.db.execute(query)
        items = list(result.scalars().all())
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]
        next_cursor = self._encode_changes_cursor(items[-1]) if has_more else None

        deleted_ids: list[uuid.UUID] = []
        if lower_bound is not None and not cursor:
            deleted_result = await self.db.execute(
                select(TransactionDeletion.transaction_id).where(
                    TransactionDeletion.user_id == user_id,
                    TransactionDeletion.deleted_at > lower_bound,
                )
            )
            deleted_ids = list(deleted_result.scalars().all())

        return SyncChangesResponse(
            transactions=[TransactionResponse.model_validate(t) for t in items],
            deleted_transaction_ids=deleted_ids,
            has_more=has_more,
            next_cursor=next_cursor,
            server_time=server_time,
        )

    @staticmethod
    def _encode_changes_cursor(transaction: Transaction) -> str:
        """Курсор следующей страницы изменений: (updated_at, id) последней записи."""
        data = {"u": transaction.updated_at.isoformat(), "i": str(transaction.id)}
        return base64.urlsafe_b64encode(json.dumps(data).encode("utf-8")).decode("ascii")

    @staticmethod
    def _decode_changes_cursor(cursor: str, user_id: uuid.UUID) -> tuple[datetime, uuid.UUID]:
        """Разобрать курсор изменений; повреждённый курсор — ошибка, а не первая страница заново."""
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
            updated_at = datetime.fromisoformat(data["u"])
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            return updated_at, uuid.UUID(data["i"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("Некорректный курсор изменений от пользователя %s: %s", user_id, type(e).__name__)
            raise BadRequestException(code=ErrorCode.INVALID_CURSOR, message="Некорректный курсор пагинации")

    async def _upsert_transactions(
        self,
        user_id: uuid.UUID,
        base_currency: str,
        items: list[TransactionSyncItem],
        rejected: list[SyncRejectedItem],
    ) -> list[Transaction]:
        """Создать или обновить транзакции по клиентским ID; повторы ID внутри батча пропускаются."""
        # Курсы загружаются, только если среди транзакций есть валюта, отличная от базовой
        rates = None
        if any((item.currency or base_currency).strip().upper() != base_currency for item in items):
            rates = (await CurrencyService().get_rates()).rates

        existing: dict[uuid.UUID, Transaction] = {}
        foreign_ids: set[uuid.UUID] = set()
        incoming_ids = [item.id for item in items if item.id is not None]
        if incoming_ids:
            result = await self.db.execute(select(Transaction).where(Transaction.id.in_(incoming_ids)))
            for t in result.scalars().all():
                if t.user_id == user_id:
                    existing[t.id] = t
                else:
                    foreign_ids.add(t.id)

        synced: list[Transaction] = []
        seen_ids: set[uuid.UUID] = set()
        created_ids: set[uuid.UUID] = set()
        for item in items:
            tx_id = item.id or uuid.uuid4()
            if tx_id in seen_ids:
                continue

            if item.id and item.id in foreign_ids:
                logger.warning("Конфликт ID транзакции %s: принадлежит другому пользователю", item.id)
                rejected.append(
                    SyncRejectedItem(
                        id=item.id,
                        code=ErrorCode.ID_CONFLICT,
                        reason="Идентификатор транзакции отклонен (конфликт идентификатора)",
                    )
                )
                continue

            # В лог попадает только ID: суммы и описания — персональные финансовые данные
            logger.debug("Обработка транзакции с ID: %s", tx_id)

            currency = (item.currency or base_currency).strip().upper()
            try:
                rate, base_amount = CurrencyService.resolve_amounts(
                    amount=item.amount,
                    currency=currency,
                    base_currency=base_currency,
                    client_rate=item.exchange_rate,
                    client_base_amount=item.base_amount,
                    rates=rates,
                )
            except AppException as err:
                logger.warning("Транзакция %s отклонена при синхронизации: %s", tx_id, err.code)
                rejected.append(SyncRejectedItem(id=item.id, code=err.code, reason=err.message))
                continue

            amount = item.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if item.id and item.id in existing:
                transaction = existing[item.id]
                transaction.amount = amount
                transaction.currency = currency
                transaction.exchange_rate = rate
                transaction.base_amount = base_amount
                transaction.description = item.description
                transaction.category = item.category
                transaction.type = item.type
                transaction.date = item.date or transaction.date
            else:
                transaction = Transaction(
                    id=tx_id,
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    exchange_rate=rate,
                    base_amount=base_amount,
                    description=item.description,
                    category=item.category,
                    type=item.type,
                    date=item.date or datetime.now(timezone.utc),
                )
                self.db.add(transaction)
                existing[tx_id] = transaction
                created_ids.add(tx_id)

            seen_ids.add(tx_id)
            synced.append(transaction)

        # Транзакция, созданная заново с id удалённой, больше не удалена для других устройств
        await TransactionDeletionLog(self.db).forget(user_id, created_ids)
        return synced

    async def _delete_transactions(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
        """Удалить транзакции пользователя и записать удаления в журнал; чужие и несуществующие ID игнорируются."""
        if not ids:
            return
        result = await self.db.execute(
            select(Transaction.id).where(Transaction.user_id == user_id, Transaction.id.in_(set(ids)))
        )
        existing_ids = set(result.scalars().all())
        if not existing_ids:
            return
        logger.debug("Пакетное удаление транзакций: IDs=%s", len(existing_ids))
        await self.db.execute(delete(Transaction).where(Transaction.user_id == user_id, Transaction.id.in_(existing_ids)))
        await TransactionDeletionLog(self.db).record(user_id, existing_ids)

    async def _delete_budgets(self, user_id: uuid.UUID, payload: SyncPayload, marked_deleted: list[BudgetSyncItem]) -> None:
        """Удалить бюджеты пользователя по ID и по ключу (category, month, year) одним запросом."""
        ids: set[uuid.UUID] = set(payload.deleted_budget_ids)
        keys: set[BudgetKey] = set()
        for item in [*payload.deleted_budgets, *marked_deleted]:
            if item.id:
                ids.add(item.id)
            keys.add((item.category, item.month, item.year))

        conditions = []
        if ids:
            conditions.append(Budget.id.in_(ids))
        if keys:
            conditions.append(tuple_(Budget.category, Budget.month, Budget.year).in_(keys))
        if not conditions:
            return

        logger.debug("Пакетное удаление бюджетов: IDs=%s, tuples=%s", len(ids), len(keys))
        await self.db.execute(delete(Budget).where(Budget.user_id == user_id, or_(*conditions)))

    async def _upsert_budgets(
        self,
        user_id: uuid.UUID,
        base_currency: str,
        items: list[BudgetSyncItem],
        rejected: list[SyncRejectedItem],
    ) -> list[Budget]:
        """Создать или обновить бюджеты без нарушения уникальности (user, category, month, year).

        Загружаются только бюджеты, затронутые батчем. Совпадение по ключу важнее совпадения по ID:
        иначе вставка второго бюджета на тот же месяц и категорию нарушила бы уникальное ограничение.
        """
        if not items:
            return []

        ids = [item.id for item in items if item.id is not None]
        keys = [(item.category, item.month, item.year) for item in items]

        foreign_ids: set[uuid.UUID] = set()
        if ids:
            foreign_res = await self.db.execute(select(Budget.id).where(Budget.id.in_(ids), Budget.user_id != user_id))
            foreign_ids = set(foreign_res.scalars().all())

        conditions = [tuple_(Budget.category, Budget.month, Budget.year).in_(keys)]
        if ids:
            conditions.append(Budget.id.in_(ids))
        result = await self.db.execute(select(Budget).where(Budget.user_id == user_id, or_(*conditions)))
        loaded = list(result.scalars().all())
        by_id: dict[uuid.UUID, Budget] = {b.id: b for b in loaded}
        by_key: dict[BudgetKey, Budget] = {(b.category, b.month, b.year): b for b in loaded}

        synced: list[Budget] = []
        seen_ids: set[uuid.UUID] = set()
        for item in items:
            if item.id and item.id in foreign_ids:
                logger.warning("Конфликт ID бюджета %s: принадлежит другому пользователю", item.id)
                rejected.append(
                    SyncRejectedItem(
                        id=item.id,
                        code=ErrorCode.ID_CONFLICT,
                        reason="Идентификатор бюджета отклонен (конфликт идентификатора)",
                    )
                )
                continue

            key = (item.category, item.month, item.year)
            if key in by_key:
                budget = by_key[key]
                logger.debug("Обновление лимита бюджета по составному ключу: %s", budget.id)
                budget.limit_amount = item.limit_amount
                # Старые клиенты валюту не передают — валюта лимита сохраняется
                if item.currency:
                    budget.currency = item.currency
            elif item.id and item.id in by_id:
                budget = by_id[item.id]
                logger.debug("Обновление бюджета по существующему ID: %s", item.id)
                by_key.pop((budget.category, budget.month, budget.year), None)
                budget.category = item.category
                budget.month = item.month
                budget.year = item.year
                budget.limit_amount = item.limit_amount
                if item.currency:
                    budget.currency = item.currency
                by_key[key] = budget
            else:
                budget_id = item.id or uuid.uuid4()
                logger.debug("Создание нового бюджета %s", budget_id)
                budget = Budget(
                    id=budget_id,
                    user_id=user_id,
                    category=item.category,
                    limit_amount=item.limit_amount,
                    currency=item.currency or base_currency,
                    month=item.month,
                    year=item.year,
                )
                self.db.add(budget)
                by_id[budget_id] = budget
                by_key[key] = budget

            if budget.id not in seen_ids:
                seen_ids.add(budget.id)
                synced.append(budget)
        return synced
