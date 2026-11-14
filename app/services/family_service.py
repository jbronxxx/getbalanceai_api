"""Семейный Pro: семья, участники, приглашения по коду, выход, исключение и роспуск.

Владелец с действующим семейным Pro создаёт семью и приглашает до `FAMILY_MAX_MEMBERS - 1` человек одноразовыми
кодами. Участник подтверждает вступление сам. Состав и роли меняет только владелец; участник может выйти.

* Выход и исключение участника: общие записи остаются в семье, транзакции, которые он добавил сам, копируются
  ему как личные (с новыми id).
* Роспуск (владелец вышел или удалил аккаунт): общие записи достаются авторам как личные; записи бывших
  участников, удаливших аккаунт, удаляются — их некому отдать. Общий лимит, совпавший с личным лимитом автора
  на ту же категорию и месяц, удаляется: личный важнее.
* Удаление аккаунта участника: его общие записи остаются в семье без автора («Бывший участник»).

Клиент узнаёт о смене семьи по `family_id` в GET /sync/changes и синхронизируется заново.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from app.exceptions import (
    BadRequestException,
    ConflictException,
    ErrorCode,
    ForbiddenException,
    NotFoundException,
)
from app.models.models import (
    Budget,
    Family,
    FamilyInvite,
    FamilyMember,
    FamilyRole,
    RecurringPayment,
    Subscription,
    Transaction,
    User,
)
from app.pii import secret_hash
from app.schemas.schemas import (
    FAMILY_INVITE_CODE_ALPHABET,
    FAMILY_INVITE_CODE_LENGTH,
    FamilyInviteCreatedResponse,
    FamilyInvitePreviewResponse,
    FamilyInviteResponse,
    FamilyMemberResponse,
    FamilyResponse,
)
from app.services.ai_service import AIService
from app.services.budget_service import BudgetService
from app.services.family_access import FamilyContext, load_family_context
from logger.logger import get_logger

logger = get_logger(__name__)

# Мест в семье вместе с владельцем (тариф «Семейный Pro», MON-01)
FAMILY_MAX_MEMBERS = 5
# Срок действия кода приглашения
FAMILY_INVITE_TTL = timedelta(days=7)
# Назначение HMAC кода приглашения (`app/pii.py`)
_INVITE_HASH_CONTEXT = "family_invite"


def _invite_hash(code: str) -> str:
    """HMAC нормализованного кода приглашения."""
    return secret_hash(code, _INVITE_HASH_CONTEXT)


def _new_invite_code() -> str:
    """Случайный код из 8 символов без похожих букв и цифр (≈ 40 бит)."""
    return "".join(secrets.choice(FAMILY_INVITE_CODE_ALPHABET) for _ in range(FAMILY_INVITE_CODE_LENGTH))


def _family_not_found() -> NotFoundException:
    return NotFoundException(code=ErrorCode.FAMILY_NOT_FOUND, message="Пользователь не состоит в семье")


def _invite_invalid() -> NotFoundException:
    return NotFoundException(
        code=ErrorCode.FAMILY_INVITE_INVALID, message="Приглашение не найдено, истекло или уже использовано"
    )


class FamilyService:
    """Управление семьёй, её участниками и приглашениями."""

    def __init__(self, db: AsyncSession):
        """Сессия запроса."""
        self.db = db

    async def get(self, user_id: uuid.UUID) -> FamilyResponse | None:
        """Семья пользователя с участниками; email участников и приглашения — только владельцу. Не в семье — None."""
        context = await load_family_context(self.db, user_id)
        if context is None:
            return None
        return await self._response(context)

    async def create(self, user_id: uuid.UUID) -> FamilyResponse:
        """Создать семью, где пользователь — владелец.

        Исключения: FAMILY_ALREADY_MEMBER (409) — уже в семье; FAMILY_PLAN_REQUIRED (403) — нет своего семейного Pro.
        """
        if await load_family_context(self.db, user_id) is not None:
            raise ConflictException(code=ErrorCode.FAMILY_ALREADY_MEMBER, message="Пользователь уже состоит в семье")
        subscription = await self.db.scalar(
            select(Subscription).where(Subscription.user_id == user_id).execution_options(populate_existing=True)
        )
        now = datetime.now(timezone.utc)
        if subscription is None or not subscription.is_active(now) or not subscription.family_active(now):
            raise ForbiddenException(code=ErrorCode.FAMILY_PLAN_REQUIRED, message="Нужен действующий семейный Pro")
        family = Family(owner_id=user_id)
        self.db.add(family)
        await self.db.flush()
        self.db.add(FamilyMember(family_id=family.id, user_id=user_id, role=FamilyRole.owner.value))
        await self.db.flush()
        logger.info(f"Пользователь {user_id} создал семью {family.id}")
        return await self._response(await self._require_context(user_id))

    async def create_invite(self, user_id: uuid.UUID) -> FamilyInviteCreatedResponse:
        """Создать одноразовый код приглашения на 7 дней.

        Действующих приглашений не больше свободных мест. Исключения: FAMILY_NOT_FOUND (404),
        FAMILY_OWNER_ONLY (403), FAMILY_FROZEN (403), FAMILY_FULL (409).
        """
        context = await self._require_owner(user_id)
        if context.frozen:
            raise ForbiddenException(code=ErrorCode.FAMILY_FROZEN, message="Семья заморожена: приглашения недоступны")
        await self._lock_family(context.family_id)
        now = datetime.now(timezone.utc)
        await self.db.execute(
            delete(FamilyInvite).where(FamilyInvite.family_id == context.family_id, FamilyInvite.expires_at <= now)
        )
        if await self._seats_taken(context.family_id, now) >= FAMILY_MAX_MEMBERS:
            raise ConflictException(code=ErrorCode.FAMILY_FULL, message="В семье нет свободных мест")
        code = _new_invite_code()
        invite = FamilyInvite(
            family_id=context.family_id, code_hash=_invite_hash(code), created_at=now, expires_at=now + FAMILY_INVITE_TTL
        )
        self.db.add(invite)
        await self.db.flush()
        logger.info(f"Владелец {user_id} создал приглашение {invite.id} в семью {context.family_id}")
        return FamilyInviteCreatedResponse(
            id=invite.id, created_at=invite.created_at, expires_at=invite.expires_at_utc(), code=code
        )

    async def revoke_invite(self, user_id: uuid.UUID, invite_id: uuid.UUID) -> None:
        """Отозвать приглашение семьи. Исключения: FAMILY_OWNER_ONLY (403), FAMILY_INVITE_INVALID (404)."""
        context = await self._require_owner(user_id)
        result = await self.db.execute(
            delete(FamilyInvite).where(FamilyInvite.id == invite_id, FamilyInvite.family_id == context.family_id)
        )
        if not result.rowcount:
            raise _invite_invalid()
        logger.info(f"Владелец {user_id} отозвал приглашение {invite_id}")

    async def preview_invite(self, code: str) -> FamilyInvitePreviewResponse:
        """Семья по коду — для экрана согласия. Исключения: FAMILY_INVITE_INVALID (404)."""
        invite = await self._find_invite(code)
        owner_name, members_count = (
            await self.db.execute(
                select(
                    User.name,
                    select(func.count(FamilyMember.id)).where(FamilyMember.family_id == Family.id).scalar_subquery(),
                )
                .join(Family, Family.owner_id == User.id)
                .where(Family.id == invite.family_id)
            )
        ).one()
        return FamilyInvitePreviewResponse(
            owner_name=owner_name,
            members_count=members_count,
            max_members=FAMILY_MAX_MEMBERS,
            expires_at=invite.expires_at_utc(),
        )

    async def join(self, user_id: uuid.UUID, code: str) -> FamilyResponse:
        """Вступить в семью по коду; код удаляется. Свой личный Pro участника не трогается.

        Исключения: FAMILY_ALREADY_MEMBER (409), FAMILY_INVITE_INVALID (404), FAMILY_FROZEN (403), FAMILY_FULL (409).
        """
        if await load_family_context(self.db, user_id) is not None:
            raise ConflictException(
                code=ErrorCode.FAMILY_ALREADY_MEMBER, message="Пользователь уже в семье: сначала выйти из текущей"
            )
        invite = await self._find_invite(code)
        family_id = invite.family_id
        await self._lock_family(family_id)
        owner_id = await self.db.scalar(select(Family.owner_id).where(Family.id == family_id))
        owner_context = await load_family_context(self.db, owner_id)
        if owner_context is None or owner_context.frozen:
            raise ForbiddenException(code=ErrorCode.FAMILY_FROZEN, message="Семья заморожена: вступить нельзя")
        members = await self.db.scalar(select(func.count(FamilyMember.id)).where(FamilyMember.family_id == family_id))
        if members >= FAMILY_MAX_MEMBERS:
            raise ConflictException(code=ErrorCode.FAMILY_FULL, message="В семье нет свободных мест")
        await self.db.delete(invite)
        self.db.add(FamilyMember(family_id=family_id, user_id=user_id, role=FamilyRole.member.value))
        await self.db.flush()
        await BudgetService.invalidate_etag(user_id)
        logger.info(f"Пользователь {user_id} вступил в семью {family_id} по приглашению {invite.id}")
        return await self._response(await self._require_context(user_id))

    async def remove_member(self, owner_id: uuid.UUID, member_id: uuid.UUID) -> None:
        """Исключить участника; его транзакции из общих копируются ему как личные.

        Исключения: FAMILY_OWNER_ONLY (403); BAD_REQUEST (400) — владелец исключает себя (для этого выход);
        FAMILY_NOT_FOUND (404) — такого участника в семье нет.
        """
        context = await self._require_owner(owner_id)
        if member_id == owner_id:
            raise BadRequestException(message="Владелец не исключает себя: выход из семьи распускает её")
        is_member = await self.db.scalar(
            select(exists().where(FamilyMember.family_id == context.family_id, FamilyMember.user_id == member_id))
        )
        if not is_member:
            raise NotFoundException(code=ErrorCode.FAMILY_NOT_FOUND, message="Участник не найден в семье")
        await self._detach_member(context.family_id, member_id)
        logger.info(f"Владелец {owner_id} исключил участника {member_id} из семьи {context.family_id}")

    async def leave(self, user_id: uuid.UUID) -> None:
        """Выйти из семьи: участник — со своими транзакциями из общих как личными; владелец — роспуск семьи.

        Исключения: FAMILY_NOT_FOUND (404).
        """
        context = await self._require_context(user_id)
        if context.is_owner:
            await self._dissolve(context.family_id)
            logger.info(f"Владелец {user_id} распустил семью {context.family_id}")
            return
        await self._detach_member(context.family_id, user_id)
        logger.info(f"Участник {user_id} вышел из семьи {context.family_id}")

    async def handle_account_deletion(self, user_id: uuid.UUID) -> None:
        """Подготовить удаление аккаунта: владелец — роспуск семьи; общие записи участника остаются без автора.

        Отвязываются общие записи всех семей, включая те, из которых пользователь вышел раньше: иначе удаление
        пользователя (ON DELETE CASCADE) удалило бы их и у бывшей семьи.
        """
        context = await load_family_context(self.db, user_id)
        if context is not None and context.is_owner:
            await self._dissolve(context.family_id)
        for model in (Transaction, Budget, RecurringPayment):
            await self.db.execute(
                update(model)
                .where(model.user_id == user_id, model.family_id.is_not(None))
                .values(user_id=None)
                .execution_options(synchronize_session=False)
            )
        if context is not None and not context.is_owner:
            await BudgetService.invalidate_etag(user_id, context.family_id)

    async def _detach_member(self, family_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Вывести участника из семьи и скопировать ему как личные транзакции, которые он добавил в общие."""
        authored = (
            await self.db.scalars(
                select(Transaction).where(Transaction.family_id == family_id, Transaction.user_id == user_id)
            )
        ).all()
        copy_fields = ("amount", "currency", "exchange_rate", "base_amount", "description", "category", "type", "date")
        self.db.add_all(
            [
                Transaction(user_id=user_id, family_id=None, **{name: getattr(tx, name) for name in copy_fields})
                for tx in authored
            ]
        )
        await self.db.execute(
            delete(FamilyMember).where(FamilyMember.family_id == family_id, FamilyMember.user_id == user_id)
        )
        await self.db.flush()
        await BudgetService.invalidate_etag(user_id, family_id)
        logger.info(f"Участнику {user_id} скопировано {len(authored)} транзакций из общих семьи {family_id}")

    async def _dissolve(self, family_id: uuid.UUID) -> None:
        """Распустить семью: общие записи — авторам как личные, записи без автора — удалить, семью — удалить."""
        member_ids = list(
            (await self.db.scalars(select(FamilyMember.user_id).where(FamilyMember.family_id == family_id))).all()
        )
        for model in (Transaction, Budget, RecurringPayment):
            await self.db.execute(
                delete(model)
                .where(model.family_id == family_id, model.user_id.is_(None))
                .execution_options(synchronize_session=False)
            )
        # Общий лимит, у автора которого уже есть личный на ту же категорию и месяц, нарушил бы уникальность
        personal = aliased(Budget)
        await self.db.execute(
            delete(Budget)
            .where(
                Budget.family_id == family_id,
                exists().where(
                    personal.family_id.is_(None),
                    personal.user_id == Budget.user_id,
                    personal.category == Budget.category,
                    personal.month == Budget.month,
                    personal.year == Budget.year,
                ),
            )
            .execution_options(synchronize_session=False)
        )
        for model in (Transaction, Budget, RecurringPayment):
            await self.db.execute(
                update(model)
                .where(model.family_id == family_id)
                .values(family_id=None)
                .execution_options(synchronize_session=False)
            )
        # Участники, приглашения и журнал удалений семьи удаляются каскадом
        await self.db.execute(delete(Family).where(Family.id == family_id))
        await self.db.flush()
        for member_id in member_ids:
            await BudgetService.invalidate_etag(member_id, family_id)
        await AIService.invalidate_family_cache(family_id)

    async def _response(self, context: FamilyContext) -> FamilyResponse:
        """Ответ о семье для пользователя с ролью из `context`."""
        members = (
            await self.db.scalars(
                select(FamilyMember)
                .options(joinedload(FamilyMember.user))
                .where(FamilyMember.family_id == context.family_id)
                .order_by(FamilyMember.joined_at.asc(), FamilyMember.id.asc())
            )
        ).all()
        # Владелец первым, остальные — по вступлению
        ordered = sorted(members, key=lambda m: m.role != FamilyRole.owner.value)
        invites: list[FamilyInviteResponse] = []
        if context.is_owner:
            rows = await self.db.scalars(
                select(FamilyInvite)
                .where(FamilyInvite.family_id == context.family_id, FamilyInvite.expires_at > datetime.now(timezone.utc))
                .order_by(FamilyInvite.created_at.asc())
            )
            invites = [
                FamilyInviteResponse(id=i.id, created_at=i.created_at, expires_at=i.expires_at_utc()) for i in rows.all()
            ]
        return FamilyResponse(
            id=context.family_id,
            role=context.role,
            owner_id=context.owner_id,
            is_frozen=context.frozen,
            max_members=FAMILY_MAX_MEMBERS,
            members=[
                FamilyMemberResponse(
                    user_id=m.user_id,
                    name=m.user.name,
                    email=m.user.email if context.is_owner else None,
                    role=FamilyRole(m.role),
                    joined_at=m.joined_at,
                )
                for m in ordered
            ],
            invites=invites,
        )

    async def _require_context(self, user_id: uuid.UUID) -> FamilyContext:
        """Семья пользователя; не в семье — FAMILY_NOT_FOUND (404)."""
        context = await load_family_context(self.db, user_id)
        if context is None:
            raise _family_not_found()
        return context

    async def _require_owner(self, user_id: uuid.UUID) -> FamilyContext:
        """Семья, где пользователь — владелец; участник — FAMILY_OWNER_ONLY (403)."""
        context = await self._require_context(user_id)
        if not context.is_owner:
            raise ForbiddenException(code=ErrorCode.FAMILY_OWNER_ONLY, message="Действие доступно только владельцу семьи")
        return context

    async def _find_invite(self, code: str) -> FamilyInvite:
        """Действующее приглашение по коду; нет — FAMILY_INVITE_INVALID (404)."""
        invite = await self.db.scalar(
            select(FamilyInvite).where(
                FamilyInvite.code_hash == _invite_hash(code), FamilyInvite.expires_at > datetime.now(timezone.utc)
            )
        )
        if invite is None:
            raise _invite_invalid()
        return invite

    async def _lock_family(self, family_id: uuid.UUID) -> None:
        """Блокировка строки семьи до конца транзакции: параллельные вступления и приглашения не превысят мест."""
        await self.db.execute(select(Family.id).where(Family.id == family_id).with_for_update())

    async def _seats_taken(self, family_id: uuid.UUID, now: datetime) -> int:
        """Занятые места: участники вместе с владельцем и действующие приглашения."""
        members = await self.db.scalar(select(func.count(FamilyMember.id)).where(FamilyMember.family_id == family_id))
        invites = await self.db.scalar(
            select(func.count(FamilyInvite.id)).where(FamilyInvite.family_id == family_id, FamilyInvite.expires_at > now)
        )
        return int(members or 0) + int(invites or 0)
