"""Принятие юридических документов: запись согласий, проверка версий, хранение после удаления аккаунта."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictException, ErrorCode
from app.legal import LEGAL_DOCUMENTS, current_version
from app.models.models import User, UserConsent
from logger.logger import get_logger

logger = get_logger(__name__)

# Срок хранения записи о согласии после удаления аккаунта: общий срок исковой давности (РУз, Финляндия) — 3 года
CONSENT_RETENTION_DAYS = 3 * 365

# Длина колонок language, platform, app_version
_CLIENT_FIELD_MAX = 64


def _accepted_attr(document: str) -> str:
    """Имя колонки users с принятой версией документа."""
    return f"accepted_{document}_version"


def terms_accepted(user: User) -> bool:
    """Принята ли пользователем текущая версия соглашения (без неё доступ к данным закрыт)."""
    return user.accepted_terms_version == current_version("terms").version


class ConsentService:
    """Запись согласий с документами из `LEGAL_HISTORY` и регламентная очистка старых записей."""

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных."""
        self.db = db

    async def accept_current(self, user: User, client: dict[str, str]) -> None:
        """Записать принятие текущих версий всех документов (отметка на экране входа).

        Запись добавляется только для документа, текущая версия которого ещё не принята: повторный вход
        с той же версией не множит историю.
        """
        await self._record(user, {document: current_version(document).version for document in LEGAL_DOCUMENTS}, client)

    async def accept(self, user: User, versions: dict[str, str], client: dict[str, str]) -> None:
        """Записать принятие версий, которые клиент показал пользователю (экран новой версии, уведомление).

        Версия должна быть текущей: если за время показа вышла новая, принимать старую нельзя — клиент
        получит LEGAL_VERSION_OUTDATED и покажет новую.
        """
        for document, version in versions.items():
            current = current_version(document).version
            if version != current:
                raise ConflictException(
                    code=ErrorCode.LEGAL_VERSION_OUTDATED,
                    message="Версия документа устарела",
                    details={"document": document, "current_version": current},
                )
        await self._record(user, versions, client)

    async def _record(self, user: User, versions: dict[str, str], client: dict[str, str]) -> None:
        """Добавить записи о согласии и обновить принятые версии у пользователя."""
        now = datetime.now(timezone.utc)
        for document, version in versions.items():
            attr = _accepted_attr(document)
            if getattr(user, attr) == version:
                continue
            self.db.add(
                UserConsent(
                    user_id=user.id,
                    email_hash=user.email_hash,
                    document=document,
                    version=version,
                    accepted_at=now,
                    language=self._client_field(client, "language"),
                    platform=self._client_field(client, "platform"),
                    app_version=self._client_field(client, "app_version"),
                )
            )
            setattr(user, attr, version)
            logger.info(f"Пользователь {user.id} принял {document} версии {version}")
        await self.db.flush()

    @staticmethod
    def _client_field(client: dict[str, str], field: str) -> str | None:
        """Значение заголовка клиента в пределах длины колонки."""
        value = client.get(field)
        return value[:_CLIENT_FIELD_MAX] if value else None

    async def mark_account_deleted(self, user_id: uuid.UUID) -> None:
        """Отметить момент удаления аккаунта в его согласиях — от него отсчитывается срок хранения.

        user_id обнулит сама БД (ON DELETE SET NULL) при удалении пользователя.
        """
        await self.db.execute(
            update(UserConsent).where(UserConsent.user_id == user_id).values(account_deleted_at=datetime.now(timezone.utc))
        )

    async def cleanup_expired(self, retention_days: int = CONSENT_RETENTION_DAYS) -> int:
        """Удалить записи о согласии, срок хранения которых после удаления аккаунта истёк."""
        threshold = datetime.now(timezone.utc) - timedelta(days=retention_days)
        result = await self.db.execute(delete(UserConsent).where(UserConsent.account_deleted_at < threshold))
        await self.db.flush()
        logger.info(f"Очистка согласий: удалено {result.rowcount} записей (порог: {threshold.isoformat()})")
        return result.rowcount
