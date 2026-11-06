"""Тесты инкрементальной синхронизации: GET /api/v1/sync/changes и журнала удалённых транзакций."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Category,
    Transaction,
    TransactionDeletion,
    TransactionType,
    User,
)
from app.services.auth_service import AuthService

CHANGES_URL = "/api/v1/sync/changes"


async def _add_transaction(
    db: AsyncSession, user_id: uuid.UUID, updated_at: datetime, description: str = "Обед"
) -> uuid.UUID:
    """Транзакция с заданным временем последнего изменения; возвращает её id.

    id создаётся заранее: после commit объекты сессии устаревают, и обращение к их полям — синхронная загрузка.
    """
    tx_id = uuid.uuid4()
    tx = Transaction(
        id=tx_id,
        user_id=user_id,
        amount=Decimal("10.00"),
        currency="USD",
        exchange_rate=Decimal("1.0"),
        base_amount=Decimal("10.00"),
        description=description,
        category=Category.food,
        type=TransactionType.expense,
        date=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
    )
    db.add(tx)
    await db.commit()
    return tx_id


async def _other_user_headers(db: AsyncSession) -> tuple[uuid.UUID, dict[str, str]]:
    """Второй пользователь: его id и заголовки авторизации."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"other_{user_id.hex[:8]}@example.com", name="Other"))
    await db.commit()
    token = AuthService(db).create_access_token(user_id)
    return user_id, {"Authorization": f"Bearer {token}"}


def _since(moment: datetime) -> dict[str, str]:
    return {"since": moment.isoformat()}


class TestSyncChanges:
    """Выборка изменений транзакций с прошлой синхронизации."""

    @pytest.mark.asyncio
    async def test_unauthorized(self, client: AsyncClient):
        """Без токена — 403, как у остальных ручек синхронизации."""
        response = await client.get(CHANGES_URL)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_without_since_returns_all_transactions(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Первая синхронизация: все транзакции по возрастанию updated_at, без удалённых, со временем сервера."""
        user_id = test_user.id
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        newer = await _add_transaction(db_session, user_id, base + timedelta(days=2))
        older = await _add_transaction(db_session, user_id, base)

        response = await client.get(CHANGES_URL, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        data = body["data"]
        assert [t["id"] for t in data["transactions"]] == [str(older), str(newer)]
        assert data["deleted_transaction_ids"] == []
        assert data["has_more"] is False
        assert data["next_cursor"] is None
        assert datetime.fromisoformat(data["server_time"]) > base

    @pytest.mark.asyncio
    async def test_since_returns_only_changed(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """С since — только транзакции, изменённые позже (с запасом в минуту назад)."""
        user_id = test_user.id
        mark = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
        await _add_transaction(db_session, user_id, mark - timedelta(hours=1), "до отметки")
        within_overlap = await _add_transaction(db_session, user_id, mark - timedelta(seconds=30), "на границе")
        after = await _add_transaction(db_session, user_id, mark + timedelta(hours=1), "после отметки")

        response = await client.get(CHANGES_URL, params=_since(mark), headers=auth_headers)

        assert response.status_code == 200
        ids = [t["id"] for t in response.json()["data"]["transactions"]]
        assert ids == [str(within_overlap), str(after)]

    @pytest.mark.asyncio
    async def test_deleted_via_endpoint_is_reported(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """DELETE /transactions/{id} попадает в deleted_transaction_ids."""
        user_id = test_user.id
        tx = await _add_transaction(db_session, user_id, datetime(2026, 10, 1, tzinfo=timezone.utc))
        mark = datetime.now(timezone.utc) - timedelta(hours=1)

        delete_response = await client.delete(f"/api/v1/transactions/{tx}", headers=auth_headers)
        assert delete_response.status_code == 200

        response = await client.get(CHANGES_URL, params=_since(mark), headers=auth_headers)
        data = response.json()["data"]
        assert data["deleted_transaction_ids"] == [str(tx)]
        assert data["transactions"] == []

    @pytest.mark.asyncio
    async def test_deleted_via_sync_is_reported(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Удаление через deleted_transaction_ids в POST /sync тоже попадает в журнал."""
        user_id = test_user.id
        tx = await _add_transaction(db_session, user_id, datetime(2026, 10, 1, tzinfo=timezone.utc))
        mark = datetime.now(timezone.utc) - timedelta(hours=1)

        sync_response = await client.post("/api/v1/sync/", json={"deleted_transaction_ids": [str(tx)]}, headers=auth_headers)
        assert sync_response.status_code == 200

        response = await client.get(CHANGES_URL, params=_since(mark), headers=auth_headers)
        assert response.json()["data"]["deleted_transaction_ids"] == [str(tx)]

    @pytest.mark.asyncio
    async def test_deleting_missing_or_foreign_transaction_is_not_logged(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Несуществующий и чужой ID в POST /sync не создают записей в журнале, чужая транзакция не удаляется."""
        other_id, _ = await _other_user_headers(db_session)
        foreign = await _add_transaction(db_session, other_id, datetime(2026, 10, 1, tzinfo=timezone.utc))

        response = await client.post(
            "/api/v1/sync/",
            json={"deleted_transaction_ids": [str(uuid.uuid4()), str(foreign)]},
            headers=auth_headers,
        )

        assert response.status_code == 200
        logged = (await db_session.execute(select(TransactionDeletion))).scalars().all()
        assert logged == []
        assert await db_session.get(Transaction, foreign) is not None

    @pytest.mark.asyncio
    async def test_recreated_transaction_is_not_reported_deleted(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Транзакция, созданная заново с id удалённой, приходит как изменённая, а не удалённая."""
        user_id = test_user.id
        tx = await _add_transaction(db_session, user_id, datetime(2026, 10, 1, tzinfo=timezone.utc))
        mark = datetime.now(timezone.utc) - timedelta(hours=1)
        await client.delete(f"/api/v1/transactions/{tx}", headers=auth_headers)

        recreate = await client.post(
            "/api/v1/sync/",
            json={
                "transactions": [
                    {
                        "id": str(tx),
                        "amount": 15.0,
                        "description": "Снова",
                        "category": "food",
                        "type": "expense",
                        "date": "2026-10-02T12:00:00Z",
                    }
                ]
            },
            headers=auth_headers,
        )
        assert recreate.status_code == 200

        data = (await client.get(CHANGES_URL, params=_since(mark), headers=auth_headers)).json()["data"]
        assert data["deleted_transaction_ids"] == []
        assert [t["id"] for t in data["transactions"]] == [str(tx)]

    @pytest.mark.asyncio
    async def test_pages_cover_all_changes_once(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Курсор проходит все изменения без пропусков и повторов; удалённые — только на первой странице."""
        user_id = test_user.id
        base = datetime(2026, 10, 1, tzinfo=timezone.utc)
        created = [await _add_transaction(db_session, user_id, base + timedelta(minutes=i)) for i in range(5)]
        # Две транзакции с одинаковым updated_at: порядок между ними задаёт id
        created.append(await _add_transaction(db_session, user_id, base + timedelta(minutes=4)))
        removed = await _add_transaction(db_session, user_id, base)
        await client.delete(f"/api/v1/transactions/{removed}", headers=auth_headers)

        since = base - timedelta(days=1)
        first = (await client.get(CHANGES_URL, params={**_since(since), "limit": 4}, headers=auth_headers)).json()["data"]
        assert first["has_more"] is True
        assert first["deleted_transaction_ids"] == [str(removed)]

        second = (
            await client.get(
                CHANGES_URL,
                params={**_since(since), "limit": 4, "cursor": first["next_cursor"]},
                headers=auth_headers,
            )
        ).json()["data"]
        assert second["has_more"] is False
        assert second["deleted_transaction_ids"] == []

        seen = [t["id"] for t in first["transactions"] + second["transactions"]]
        assert sorted(seen) == sorted(str(t) for t in created)
        assert len(seen) == len(set(seen))

    @pytest.mark.asyncio
    async def test_other_users_changes_are_invisible(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Чужие транзакции и чужие удаления в ответ не попадают."""
        user_id = test_user.id
        other_id, other_headers = await _other_user_headers(db_session)
        foreign = await _add_transaction(db_session, other_id, datetime(2026, 10, 1, tzinfo=timezone.utc))
        foreign_removed = await _add_transaction(db_session, other_id, datetime(2026, 10, 1, tzinfo=timezone.utc))
        await client.delete(f"/api/v1/transactions/{foreign_removed}", headers=other_headers)
        own = await _add_transaction(db_session, user_id, datetime(2026, 10, 1, tzinfo=timezone.utc))

        data = (
            await client.get(CHANGES_URL, params=_since(datetime(2026, 9, 1, tzinfo=timezone.utc)), headers=auth_headers)
        ).json()["data"]

        assert [t["id"] for t in data["transactions"]] == [str(own)]
        assert str(foreign) not in data["deleted_transaction_ids"]
        assert data["deleted_transaction_ids"] == []

    @pytest.mark.asyncio
    async def test_invalid_cursor(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Повреждённый курсор — 400 INVALID_CURSOR, а не первая страница заново."""
        response = await client.get(CHANGES_URL, params={"cursor": "not-a-cursor"}, headers=auth_headers)

        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_CURSOR"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [0, 501])
    async def test_limit_out_of_range(self, client: AsyncClient, auth_headers: dict[str, str], limit: int):
        """Размер страницы вне 1..500 — 422."""
        response = await client.get(CHANGES_URL, params={"limit": limit}, headers=auth_headers)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_deletions_removed_with_user(self, db_session: AsyncSession, test_user: User):
        """Журнал удалений удаляется вместе с пользователем (ON DELETE CASCADE)."""
        db_session.add(TransactionDeletion(transaction_id=uuid.uuid4(), user_id=test_user.id))
        await db_session.commit()

        await db_session.delete(test_user)
        await db_session.commit()

        assert (await db_session.execute(select(TransactionDeletion))).scalars().all() == []
