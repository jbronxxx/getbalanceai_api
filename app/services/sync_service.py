"""Сервис пакетной синхронизации оффлайн-данных клиента: транзакций, бюджетов и регулярных платежей."""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal

from fastapi import status
from sqlalchemy import and_, delete, exists, or_, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppException, BadRequestException, ErrorCode
from app.models.models import Budget, Category, Transaction, TransactionDeletion, User
from app.schemas.schemas import (
    BudgetSyncItem,
    RecurringPaymentResponse,
    SyncChangesResponse,
    SyncPayload,
    SyncRejectedItem,
    SyncResponse,
    TransactionResponse,
    TransactionSyncItem,
)
from app.services.budget_service import BudgetService
from app.services.currency_service import CurrencyService
from app.services.family_access import SharedAccess, rejected_item
from app.services.recurring_payment_service import RecurringPaymentService
from app.services.transaction_deletion_log import TransactionDeletionLog
from logger.logger import get_logger

logger = get_logger(__name__)

# (семья общего лимита или None для личного, категория, месяц, год)
BudgetKey = tuple[uuid.UUID | None, Category, int, int]

# Насколько расширять окно since назад: запас на транзакции, закоммиченные после отметки прошлой синхронизации
CHANGES_OVERLAP = timedelta(minutes=1)


class SyncService:
    """Применяет батч изменений клиента одной транзакцией БД.

    Записи с чужим ID, без прав на общую запись семьи или с суммой, которую нельзя пересчитать в базовую валюту,
    не роняют батч, а возвращаются в rejected: иначе клиент бесконечно повторял бы весь батч.
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
            "txs=%s, budgets=%s, del_txs=%s, del_ids=%s, del_budgets=%s, recurring=%s, del_recurring=%s",
            user_id,
            base_currency,
            len(payload.transactions),
            len(payload.budgets),
            len(payload.deleted_transaction_ids),
            len(payload.deleted_budget_ids),
            len(payload.deleted_budgets),
            len(payload.recurring_payments),
            len(payload.deleted_recurring_payment_ids),
        )
        # Записи, не прошедшие схему, уже отклонены валидатором SyncPayload
        rejected: list[SyncRejectedItem] = list(payload.invalid_items)
        if payload.invalid_items:
            logger.warning(
                "Синхронизация пользователя %s: %s записей не прошли проверку (%s)",
                user_id,
                len(payload.invalid_items),
                ", ".join(sorted({item.code for item in payload.invalid_items})),
            )
        to_delete_budgets = [item for item in payload.budgets if item.check_deleted]
        to_upsert_budgets = [item for item in payload.budgets if not item.check_deleted]

        try:
            access = await SharedAccess.load(self.db, user_id)
            synced_transactions = await self._upsert_transactions(access, base_currency, payload.transactions, rejected)
            await self._delete_transactions(access, payload.deleted_transaction_ids, rejected)
            await self._delete_budgets(access, payload, to_delete_budgets)
            synced_budgets = await self._upsert_budgets(access, base_currency, to_upsert_budgets, rejected)
            recurring = RecurringPaymentService(self.db)
            await recurring.delete(access, payload.deleted_recurring_payment_ids)
            synced_recurring = await recurring.upsert(access, payload.recurring_payments, rejected)

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
                # Общие записи меняют траты и лимиты всех участников: версия ETag семьи тоже сбрасывается
                await BudgetService.invalidate_etag(user_id, access.family_id)

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
            synced_recurring_payments=[RecurringPaymentResponse.model_validate(p) for p in synced_recurring],
            rejected=rejected,
        )

    async def get_changes(
        self,
        user_id: uuid.UUID,
        since: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> SyncChangesResponse:
        """Транзакции (личные и общие семьи), изменённые после since, и id удалённых после since.

        Без since — все видимые транзакции (первая синхронизация устройства), удалённых нет. Удалённые —
        удаления пользователя и участников его семьи, а также общие записи, которые автор сделал личными;
        запись, которая сейчас видна пользователю, удалённой не считается.
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

        access = await SharedAccess.load(self.db, user_id)
        query = select(Transaction).where(access.visible(Transaction))
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
            mine = TransactionDeletion.user_id == user_id
            if access.family_id is not None:
                mine = or_(mine, TransactionDeletion.family_id == access.family_id)
            still_visible = exists().where(Transaction.id == TransactionDeletion.transaction_id, access.visible(Transaction))
            deleted_result = await self.db.execute(
                select(TransactionDeletion.transaction_id).where(
                    mine,
                    TransactionDeletion.deleted_at > lower_bound,
                    ~still_visible,
                )
            )
            deleted_ids = list(deleted_result.scalars().all())

        return SyncChangesResponse(
            transactions=[TransactionResponse.model_validate(t) for t in items],
            deleted_transaction_ids=deleted_ids,
            has_more=has_more,
            next_cursor=next_cursor,
            server_time=server_time,
            family_id=access.family_id,
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
        access: SharedAccess,
        base_currency: str,
        items: list[TransactionSyncItem],
        rejected: list[SyncRejectedItem],
    ) -> list[Transaction]:
        """Создать или обновить транзакции по клиентским ID; повторы ID внутри батча пропускаются.

        Общую запись семьи правит любой участник, автор не меняется. base_amount ведётся в базовой валюте
        автора: у чужой общей записи он пересчитывается сервером, курс и сумму клиента в его валюте не берём.
        """
        user_id = access.user_id
        existing: dict[uuid.UUID, Transaction] = {}
        incoming_ids = [item.id for item in items if item.id is not None]
        if incoming_ids:
            result = await self.db.execute(select(Transaction).where(Transaction.id.in_(incoming_ids)))
            existing = {t.id: t for t in result.scalars().all()}

        # Базовые валюты других авторов общих записей батча
        author_ids = {t.user_id for t in existing.values() if t.user_id not in (None, user_id) and access.can_see(t)}
        author_currencies: dict[uuid.UUID, str] = {}
        if author_ids:
            rows = await self.db.execute(select(User.id, User.base_currency).where(User.id.in_(author_ids)))
            author_currencies = {row_id: (currency or "USD").strip().upper() for row_id, currency in rows.all()}

        def base_of(transaction: Transaction | None) -> str:
            if transaction is None or transaction.user_id in (None, user_id):
                return base_currency
            return author_currencies.get(transaction.user_id, base_currency)

        # Курсы загружаются, только если среди транзакций есть валюта, отличная от базовой валюты автора
        rates = None
        if any((item.currency or base_currency).strip().upper() != base_of(existing.get(item.id)) for item in items):
            rates = (await CurrencyService().get_rates()).rates

        synced: list[Transaction] = []
        seen_ids: set[uuid.UUID] = set()
        created_ids: set[uuid.UUID] = set()
        unshared: dict[uuid.UUID, uuid.UUID] = {}
        for item in items:
            tx_id = item.id or uuid.uuid4()
            if tx_id in seen_ids:
                continue

            current = existing.get(item.id) if item.id else None
            error = access.write_error(current, item.is_shared)
            if error is not None:
                logger.warning("Транзакция %s отклонена при синхронизации: %s", item.id, error)
                rejected.append(rejected_item(item.id, error, "транзакции"))
                continue

            # В лог попадает только ID: суммы и описания — персональные финансовые данные
            logger.debug("Обработка транзакции с ID: %s", tx_id)

            currency = (item.currency or base_currency).strip().upper()
            own_amounts = current is None or current.user_id in (None, user_id)
            try:
                rate, base_amount = CurrencyService.resolve_amounts(
                    amount=item.amount,
                    currency=currency,
                    base_currency=base_of(current),
                    client_rate=item.exchange_rate if own_amounts else None,
                    client_base_amount=item.base_amount if own_amounts else None,
                    rates=rates,
                )
            except AppException as err:
                logger.warning("Транзакция %s отклонена при синхронизации: %s", tx_id, err.code)
                rejected.append(SyncRejectedItem(id=item.id, code=err.code, reason=err.message))
                continue

            amount = item.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            family_id = access.target_family(item.is_shared)
            if current is not None:
                transaction = current
                if transaction.family_id is not None and family_id is None:
                    unshared[transaction.id] = transaction.family_id
                transaction.family_id = family_id
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
                    family_id=family_id,
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

        log = TransactionDeletionLog(self.db)
        # Транзакция, созданная заново с id удалённой, больше не удалена для других устройств
        await log.forget(user_id, created_ids)
        # Общая запись, ставшая личной, у остальных участников пропала: для них это удаление
        for family_id in set(unshared.values()):
            await log.record(user_id, [tx_id for tx_id, fid in unshared.items() if fid == family_id], family_id)
        return synced

    async def _delete_transactions(
        self, access: SharedAccess, ids: list[uuid.UUID], rejected: list[SyncRejectedItem]
    ) -> None:
        """Удалить видимые транзакции и записать удаления в журнал (общие — для всей семьи).

        Чужие и несуществующие ID игнорируются; общие записи замороженной семьи не удаляются — в rejected
        с FAMILY_FROZEN, чтобы клиент вернул запись.
        """
        if not ids:
            return
        result = await self.db.execute(
            select(Transaction.id, Transaction.family_id).where(access.visible(Transaction), Transaction.id.in_(set(ids)))
        )
        by_family: dict[uuid.UUID | None, set[uuid.UUID]] = {}
        for tx_id, family_id in result.all():
            if family_id is not None and access.family is not None and access.family.frozen:
                rejected.append(rejected_item(tx_id, ErrorCode.FAMILY_FROZEN, "транзакции"))
                continue
            by_family.setdefault(family_id, set()).add(tx_id)
        if not by_family:
            return
        existing_ids = set().union(*by_family.values())
        logger.debug("Пакетное удаление транзакций: IDs=%s", len(existing_ids))
        await self.db.execute(delete(Transaction).where(Transaction.id.in_(existing_ids)))
        log = TransactionDeletionLog(self.db)
        for family_id, tx_ids in by_family.items():
            await log.record(access.user_id, tx_ids, family_id)

    async def _delete_budgets(
        self, access: SharedAccess, payload: SyncPayload, marked_deleted: list[BudgetSyncItem]
    ) -> None:
        """Удалить личные и общие бюджеты по ID и по ключу (область, category, month, year) одним запросом.

        Удаляется только то, что пользователь может править: общие лимиты замороженной семьи остаются
        (клиент сверит полный список бюджетов).
        """
        ids: set[uuid.UUID] = set(payload.deleted_budget_ids)
        personal_keys: set[tuple[Category, int, int]] = set()
        shared_keys: set[tuple[Category, int, int]] = set()
        for item in [*payload.deleted_budgets, *marked_deleted]:
            if item.id:
                ids.add(item.id)
            (shared_keys if item.is_shared else personal_keys).add((item.category, item.month, item.year))

        period = tuple_(Budget.category, Budget.month, Budget.year)
        conditions = []
        if ids:
            conditions.append(Budget.id.in_(ids))
        if personal_keys:
            conditions.append(and_(Budget.family_id.is_(None), period.in_(personal_keys)))
        if shared_keys and access.family_id is not None:
            conditions.append(and_(Budget.family_id == access.family_id, period.in_(shared_keys)))
        if not conditions:
            return

        logger.debug("Пакетное удаление бюджетов: IDs=%s, tuples=%s", len(ids), len(personal_keys) + len(shared_keys))
        await self.db.execute(delete(Budget).where(access.writable(Budget), or_(*conditions)))

    async def _upsert_budgets(
        self,
        access: SharedAccess,
        base_currency: str,
        items: list[BudgetSyncItem],
        rejected: list[SyncRejectedItem],
    ) -> list[Budget]:
        """Создать или обновить бюджеты без нарушения уникальности (область, category, month, year).

        Загружаются только видимые бюджеты, затронутые батчем. Совпадение по ключу важнее совпадения по ID:
        иначе вставка второго бюджета на тот же месяц и категорию нарушила бы уникальное ограничение. Ключ
        личного лимита — пользователь, общего — семья: личный и общий лимит одной категории не мешают друг другу.
        """
        if not items:
            return []

        ids = [item.id for item in items if item.id is not None]
        periods = [(item.category, item.month, item.year) for item in items]

        foreign_ids: set[uuid.UUID] = set()
        if ids:
            foreign_res = await self.db.execute(select(Budget.id).where(Budget.id.in_(ids), ~access.visible(Budget)))
            foreign_ids = set(foreign_res.scalars().all())

        conditions = [tuple_(Budget.category, Budget.month, Budget.year).in_(periods)]
        if ids:
            conditions.append(Budget.id.in_(ids))
        result = await self.db.execute(select(Budget).where(access.visible(Budget), or_(*conditions)))
        loaded = list(result.scalars().all())
        by_id: dict[uuid.UUID, Budget] = {b.id: b for b in loaded}
        by_key: dict[BudgetKey, Budget] = {(b.family_id, b.category, b.month, b.year): b for b in loaded}

        synced: list[Budget] = []
        seen_ids: set[uuid.UUID] = set()
        for item in items:
            if item.id and item.id in foreign_ids:
                logger.warning("Конфликт ID бюджета %s: принадлежит другому пользователю", item.id)
                rejected.append(rejected_item(item.id, ErrorCode.ID_CONFLICT, "бюджета"))
                continue

            key = (access.target_family(item.is_shared), item.category, item.month, item.year)
            current = by_key.get(key) or (by_id.get(item.id) if item.id else None)
            error = access.write_error(current, item.is_shared)
            if error is not None:
                logger.warning("Бюджет %s отклонён при синхронизации: %s", item.id, error)
                rejected.append(rejected_item(item.id, error, "бюджета"))
                continue

            if key in by_key:
                budget = by_key[key]
                logger.debug("Обновление лимита бюджета по составному ключу: %s", budget.id)
                budget.limit_amount = item.limit_amount
                # Старые клиенты валюту не передают — валюта лимита сохраняется
                if item.currency:
                    budget.currency = item.currency
            elif current is not None:
                budget = current
                logger.debug("Обновление бюджета по существующему ID: %s", item.id)
                by_key.pop((budget.family_id, budget.category, budget.month, budget.year), None)
                budget.family_id = key[0]
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
                    user_id=access.user_id,
                    family_id=key[0],
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
