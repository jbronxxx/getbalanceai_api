"""Подписка Pro: снимок доступа из RevenueCat в таблице subscriptions и тариф пользователя.

Источник правды о покупке — RevenueCat; бэкенд хранит последний известный доступ Pro и обновляет его
по вебхуку и по запросу приложения после покупки. Состояние берётся из REST API, а не из тела события:
события приходят с повторами и не по порядку, а ответ API — всегда текущий.
"""

import hmac
import uuid
from datetime import datetime, timezone, tzinfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ServiceUnavailableException, UnauthorizedException
from app.models.models import Subscription, User
from app.schemas.schemas import (
    FeatureLimit,
    PlanCode,
    RevenueCatEvent,
    SubscriptionStatus,
)
from app.services.family_access import load_family_context
from app.services.plan_limits import plan_usage
from app.services.revenuecat import (
    ProEntitlement,
    RevenueCatClient,
    RevenueCatError,
    get_revenuecat_client,
    revenuecat_configured,
)
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)


# Магазин и продукт строки Pro, выданного вручную (`scripts/grant_pro.py`)
MANUAL_STORE = "manual"


def _status(row: Subscription | None, now: datetime) -> SubscriptionStatus:
    """Тариф и срок по строке своей подписки."""
    if row is None:
        return SubscriptionStatus(plan=PlanCode.free)
    active = row.is_active(now)
    return SubscriptionStatus(
        plan=PlanCode.pro if active else PlanCode.free,
        expires_at=row.expires_at_utc(),
        will_renew=active and row.will_renew,
        is_trial=active and row.is_trial,
        family_plan=active and row.family_active(now),
        store=row.store.lower() if active else None,
    )


class SubscriptionService:
    """Тариф пользователя и синхронизация подписки с RevenueCat."""

    def __init__(self, db: AsyncSession, client: RevenueCatClient | None = None):
        """Сессия запроса; клиент RevenueCat подменяется в тестах."""
        self.db = db
        self.client = client or get_revenuecat_client()

    async def status(self, user_id: uuid.UUID) -> SubscriptionStatus:
        """Действующий тариф и срок подписки по сохранённому снимку.

        Своя подписка важнее семейной: участник видит её срок и может отменить. Без своего Pro участник
        незамороженной семьи получает Pro владельца (`via_family`) — в том числе в пробный период владельца.
        """
        # populate_existing: строка могла быть загружена в сессию до upsert в sync
        row = await self.db.scalar(
            select(Subscription).where(Subscription.user_id == user_id).execution_options(populate_existing=True)
        )
        own = _status(row, datetime.now(timezone.utc))
        if own.plan is PlanCode.pro:
            return own
        family = await load_family_context(self.db, user_id)
        if family is not None and not family.is_owner and not family.frozen:
            return SubscriptionStatus(plan=PlanCode.pro, via_family=True)
        return own

    async def plan(self, user_id: uuid.UUID) -> PlanCode:
        """Действующий тариф пользователя."""
        return (await self.status(user_id)).plan

    async def profile(self, user_id: uuid.UUID, tz: tzinfo) -> tuple[SubscriptionStatus, list[FeatureLimit]]:
        """Тариф и лимиты функций с расходом в текущих окнах — для профиля."""
        subscription = await self.status(user_id)
        return subscription, await plan_usage(user_id, subscription.plan, tz)

    async def sync(self, user_id: uuid.UUID) -> SubscriptionStatus:
        """Запросить доступ Pro в RevenueCat и сохранить снимок.

        Исключения: `ServiceUnavailableException` (503) — ключ RevenueCat не задан или RevenueCat недоступен.
        """
        if not revenuecat_configured():
            logger.warning("REVENUECAT_API_KEY не задан: статус подписки не обновить")
            raise ServiceUnavailableException(message="Сервис подписок недоступен")
        try:
            entitlement = await self.client.get_pro_entitlement(str(user_id))
        except RevenueCatError as err:
            logger.error(f"Не удалось получить подписку пользователя {user_id} из RevenueCat: {err}")
            raise ServiceUnavailableException(message="Сервис подписок недоступен") from err
        await self._save(user_id, entitlement)
        return await self.status(user_id)

    @staticmethod
    def verify_webhook(authorization: str | None) -> None:
        """Проверить заголовок Authorization вебхука: значение задаётся в панели RevenueCat.

        Исключения: `ServiceUnavailableException` (503) — `REVENUECAT_WEBHOOK_AUTH` не задан;
        `UnauthorizedException` (401) — заголовок не совпал.
        """
        expected = config.revenuecat_webhook_auth
        if not expected:
            logger.warning("REVENUECAT_WEBHOOK_AUTH не задан: вебхук RevenueCat отклонён")
            raise ServiceUnavailableException(message="Вебхук подписок не настроен")
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            logger.warning("Вебхук RevenueCat с неверным заголовком Authorization")
            raise UnauthorizedException(message="Неверный заголовок вебхука")

    async def handle_webhook(self, event: RevenueCatEvent) -> int:
        """Обновить подписку всех известных покупателей события; вернуть число обновлённых.

        Анонимные покупатели (`$RCAnonymousID:...`) и id, которых нет среди аккаунтов, пропускаются: гость
        не покупает, а удалённому аккаунту подписка не нужна. Сбой RevenueCat — 503: RevenueCat повторит вебхук.
        """
        user_ids: set[uuid.UUID] = set()
        for value in event.app_user_ids():
            try:
                user_ids.add(uuid.UUID(value))
            except ValueError:
                continue
        if not user_ids:
            logger.info(f"Вебхук RevenueCat {event.type} ({event.id}) без аккаунтов бэкенда — пропущен")
            return 0
        existing = (await self.db.scalars(select(User.id).where(User.id.in_(user_ids)))).all()
        for user_id in existing:
            subscription = await self.sync(user_id)
            logger.info(
                f"Вебхук RevenueCat {event.type} ({event.id}): пользователь {user_id} — тариф {subscription.plan.value}"
            )
        return len(existing)

    async def delete_customer(self, user_id: uuid.UUID) -> None:
        """Удалить покупателя в RevenueCat при удалении аккаунта; ключ не задан — удалять нечего.

        Исключения: `ServiceUnavailableException` (503) — RevenueCat недоступен: аккаунт не удаляется,
        приложение повторит запрос.
        """
        if not revenuecat_configured():
            return
        try:
            await self.client.delete_subscriber(str(user_id))
        except RevenueCatError as err:
            logger.error(f"Не удалось удалить покупателя RevenueCat для пользователя {user_id}: {err}")
            raise ServiceUnavailableException(message="Сервис подписок недоступен") from err
        logger.info(f"Покупатель RevenueCat пользователя {user_id} удалён")

    async def grant_manual(
        self, user_id: uuid.UUID, expires_at: datetime | None, family: bool = False
    ) -> SubscriptionStatus:
        """Выдать Pro вручную (`scripts/grant_pro.py`): строка с магазином `manual`, `expires_at` None — бессрочно.

        `family` — семейный Pro на тот же срок: можно создать семью, участники получают Pro.

        Перезаписывает текущий снимок пользователя. Синхронизация с RevenueCat (покупка, «Восстановить покупки»,
        вебхук) заменит строку данными RevenueCat, а без доступа там — удалит.
        """
        await self._save(
            user_id,
            ProEntitlement(
                expires_at=expires_at,
                will_renew=False,
                is_trial=False,
                product_id=MANUAL_STORE,
                store=MANUAL_STORE,
                family=family,
                family_expires_at=expires_at if family else None,
            ),
        )
        return await self.status(user_id)

    async def revoke_manual(self, user_id: uuid.UUID) -> bool:
        """Отозвать Pro, выданный вручную; подписку из RevenueCat не трогает. True — строка удалена."""
        result = await self.db.execute(
            delete(Subscription).where(Subscription.user_id == user_id, Subscription.store == MANUAL_STORE)
        )
        await self.db.flush()
        return bool(result.rowcount)

    async def _save(self, user_id: uuid.UUID, entitlement: ProEntitlement | None) -> None:
        """Записать снимок доступа (upsert по user_id); доступа нет — удалить строку."""
        if entitlement is None:
            await self.db.execute(delete(Subscription).where(Subscription.user_id == user_id))
            await self.db.flush()
            return
        bind = getattr(self.db, "bind", None) or self.db.get_bind()
        if bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            from sqlalchemy.dialects.postgresql import insert as dialect_insert

        values = {
            "expires_at": entitlement.expires_at,
            "will_renew": entitlement.will_renew,
            "is_trial": entitlement.is_trial,
            "product_id": entitlement.product_id,
            "store": entitlement.store,
            "family": entitlement.family,
            "family_expires_at": entitlement.family_expires_at,
            "updated_at": datetime.now(timezone.utc),
        }
        # Вебхук и запрос приложения после покупки приходят почти одновременно: вставка без гонки
        stmt = (
            dialect_insert(Subscription)
            .values(id=uuid.uuid4(), user_id=user_id, **values)
            .on_conflict_do_update(index_elements=["user_id"], set_=values)
        )
        await self.db.execute(stmt)
        await self.db.flush()
