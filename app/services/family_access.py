"""Доступ к записям с отметкой «личная / общая»: транзакциям, бюджетам и регулярным платежам.

Личная запись (`family_id` NULL) видна и правится только автором (`user_id`). Общая (`family_id` — семья)
видна и правится всеми текущими участниками семьи; в замороженной семье — только для чтения. Отметку у готовой
записи меняет только автор. Запись общей семьи, из которой пользователь вышел, ему не видна.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NoReturn

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, ForbiddenException, NotFoundException
from app.models.models import Family, FamilyMember, FamilyRole, Subscription
from app.schemas.schemas import SyncRejectedItem


@dataclass(frozen=True)
class FamilyContext:
    """Членство пользователя в семье и состояние семьи."""

    family_id: uuid.UUID
    owner_id: uuid.UUID
    role: FamilyRole
    frozen: bool  # У владельца нет действующего семейного Pro

    @property
    def is_owner(self) -> bool:
        """Пользователь — владелец семьи."""
        return self.role is FamilyRole.owner


async def load_family_context(db: AsyncSession, user_id: uuid.UUID) -> FamilyContext | None:
    """Семья пользователя одним запросом (членство, владелец, его подписка); не в семье — None."""
    row = (
        await db.execute(
            select(FamilyMember.family_id, FamilyMember.role, Family.owner_id, Subscription)
            .join(Family, Family.id == FamilyMember.family_id)
            .outerjoin(Subscription, Subscription.user_id == Family.owner_id)
            .where(FamilyMember.user_id == user_id)
            # Снимок подписки владельца мог быть загружен в сессию до обновления из RevenueCat
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        return None
    family_id, role, owner_id, owner_subscription = row
    active = owner_subscription is not None and owner_subscription.family_active(datetime.now(timezone.utc))
    return FamilyContext(family_id=family_id, owner_id=owner_id, role=FamilyRole(role), frozen=not active)


def raise_access_error(code: ErrorCode, details: dict[str, Any] | None = None) -> NoReturn:
    """Исключение для отказа в доступе к общей записи вне пакетной синхронизации."""
    if code is ErrorCode.FAMILY_NOT_FOUND:
        raise NotFoundException(code=code, message="Пользователь не состоит в семье", details=details)
    if code is ErrorCode.FAMILY_FROZEN:
        raise ForbiddenException(code=code, message="Семья заморожена: общие записи только для чтения", details=details)
    if code is ErrorCode.FAMILY_AUTHOR_ONLY:
        raise ForbiddenException(code=code, message="Отметку «общая» меняет только автор записи", details=details)
    raise ForbiddenException(code=code, message="Нет доступа к записи", details=details)


_REJECT_REASONS: dict[ErrorCode, str] = {
    ErrorCode.FAMILY_NOT_FOUND: "Пользователь не состоит в семье: общая запись недоступна",
    ErrorCode.FAMILY_FROZEN: "Семья заморожена: общие записи только для чтения",
    ErrorCode.FAMILY_AUTHOR_ONLY: "Отметку «общая» меняет только автор записи",
}


def rejected_item(item_id: uuid.UUID | None, code: ErrorCode, record: str) -> SyncRejectedItem:
    """Запись пакетной синхронизации, отклонённая по правам; `record` — «транзакции», «бюджета» и т. п."""
    reason = _REJECT_REASONS.get(code, f"Идентификатор {record} отклонен (конфликт идентификатора)")
    return SyncRejectedItem(id=item_id, code=code, reason=reason)


class SharedAccess:
    """Права пользователя на личные и общие записи по его членству в семье."""

    def __init__(self, user_id: uuid.UUID, family: FamilyContext | None):
        """Пользователь и его семья (None — не в семье)."""
        self.user_id = user_id
        self.family = family

    @classmethod
    async def load(cls, db: AsyncSession, user_id: uuid.UUID) -> "SharedAccess":
        """Права пользователя по текущему членству."""
        return cls(user_id, await load_family_context(db, user_id))

    @property
    def family_id(self) -> uuid.UUID | None:
        """Семья пользователя; None — не в семье."""
        return self.family.family_id if self.family else None

    def visible(self, model: Any):
        """Условие SQL: записи, которые видит пользователь, — свои личные и общие его семьи."""
        personal = and_(model.user_id == self.user_id, model.family_id.is_(None))
        if self.family is None:
            return personal
        return or_(personal, model.family_id == self.family.family_id)

    def personal(self, model: Any):
        """Условие SQL: личные записи пользователя."""
        return and_(model.user_id == self.user_id, model.family_id.is_(None))

    def can_see(self, record: Any) -> bool:
        """Запись видна пользователю."""
        if record.family_id is None:
            return record.user_id == self.user_id
        return self.family is not None and record.family_id == self.family.family_id

    def target_family(self, shared: bool) -> uuid.UUID | None:
        """Семья для записи с отметкой `shared` (после успешной проверки прав)."""
        return self.family_id if shared else None

    def write_error(self, record: Any | None, shared: bool) -> ErrorCode | None:
        """Причина отказа в создании (`record` None) или правке записи с отметкой `shared`; можно — None.

        Чужая невидимая запись — ID_CONFLICT; общая в замороженной семье и новая общая без семьи или в замороженной —
        FAMILY_FROZEN / FAMILY_NOT_FOUND; смена отметки не автором — FAMILY_AUTHOR_ONLY.
        """
        if record is not None and not self.can_see(record):
            return ErrorCode.ID_CONFLICT
        currently_shared = record is not None and record.family_id is not None
        if shared or currently_shared:
            if self.family is None:
                return ErrorCode.FAMILY_NOT_FOUND
            if self.family.frozen:
                return ErrorCode.FAMILY_FROZEN
        if record is not None and shared != currently_shared and record.user_id != self.user_id:
            return ErrorCode.FAMILY_AUTHOR_ONLY
        return None

    def delete_error(self, record: Any) -> ErrorCode | None:
        """Причина отказа в удалении видимой записи: общая в замороженной семье — FAMILY_FROZEN."""
        if record.family_id is not None and self.family is not None and self.family.frozen:
            return ErrorCode.FAMILY_FROZEN
        return None

    def writable(self, model: Any):
        """Условие SQL: записи, которые пользователь может править и удалять сейчас."""
        personal = self.personal(model)
        if self.family is None or self.family.frozen:
            return personal
        return or_(personal, model.family_id == self.family.family_id)
