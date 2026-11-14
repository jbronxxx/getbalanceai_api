"""Тесты семейного Pro: семья, приглашения, общие записи, заморозка, выход, роспуск и удаление аккаунта."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode
from app.models.models import (
    Budget,
    Family,
    FamilyInvite,
    FamilyMember,
    FamilyRole,
    Subscription,
    Transaction,
    TransactionType,
    User,
)
from app.services.ai_service import (
    MIN_TRANSACTIONS_FOR_INSIGHTS,
    AIService,
    ReportScope,
)
from app.services.auth_service import AuthService
from app.services.family_access import FamilyContext, SharedAccess
from app.services.family_service import FAMILY_MAX_MEMBERS
from app.services.revenuecat import parse_pro_entitlement
from tests.conftest import accepted_legal

FAMILIES = "/api/v1/families"


class Person:
    """Пользователь теста с заголовками авторизации."""

    def __init__(self, user: User, headers: dict[str, str]):
        self.user = user
        self.id = user.id
        self.email = user.email
        self.headers = headers


async def _person(db: AsyncSession, name: str) -> Person:
    user_id = uuid.uuid4()
    user = User(id=user_id, email=f"{name}_{user_id.hex[:6]}@example.com", name=name, **accepted_legal())
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = AuthService(db).create_access_token(user_id)
    return Person(user, {"Authorization": f"Bearer {token}"})


async def _grant(db: AsyncSession, user_id: uuid.UUID, family: bool = True, days: int = 30) -> None:
    """Снимок подписки: Pro (и семейный при `family`) на `days` дней; отрицательное — истёк."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    row = await db.scalar(select(Subscription).where(Subscription.user_id == user_id))
    if row is None:
        row = Subscription(user_id=user_id, will_renew=True, is_trial=False, product_id="family", store="play_store")
        db.add(row)
    row.expires_at = expires_at
    row.family = family
    row.family_expires_at = expires_at if family else None
    await db.commit()


@pytest_asyncio.fixture
async def owner(db_session: AsyncSession) -> Person:
    person = await _person(db_session, "Owner")
    await _grant(db_session, person.id)
    return person


@pytest_asyncio.fixture
async def member(db_session: AsyncSession) -> Person:
    return await _person(db_session, "Anna")


async def _create_family(client: AsyncClient, owner: Person) -> dict:
    res = await client.post(f"{FAMILIES}/", headers=owner.headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def _invite_code(client: AsyncClient, owner: Person) -> str:
    res = await client.post(f"{FAMILIES}/me/invites", headers=owner.headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]["code"]


async def _join(client: AsyncClient, owner: Person, member: Person) -> None:
    code = await _invite_code(client, owner)
    res = await client.post(f"{FAMILIES}/join", json={"code": code}, headers=member.headers)
    assert res.status_code == 200, res.text


@pytest_asyncio.fixture
async def family(client: AsyncClient, owner: Person, member: Person) -> dict:
    """Семья владельца с одним участником."""
    data = await _create_family(client, owner)
    await _join(client, owner, member)
    return data


def _tx(amount: str = "10", shared: bool = False, **extra) -> dict:
    return {
        "amount": amount,
        "currency": "USD",
        "description": "Продукты",
        "category": "food",
        "type": "expense",
        "date": "2026-11-10T10:00:00Z",
        "is_shared": shared,
        **extra,
    }


async def _create_tx(client: AsyncClient, person: Person, **kwargs) -> dict:
    res = await client.post("/api/v1/transactions/", json=_tx(**kwargs), headers=person.headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def _visible_ids(client: AsyncClient, person: Person) -> set[str]:
    res = await client.get("/api/v1/transactions/", headers=person.headers)
    assert res.status_code == 200
    return {item["id"] for item in res.json()["data"]["items"]}


class TestFamilyLifecycle:
    """Создание семьи, приглашения, вступление."""

    @pytest.mark.asyncio
    async def test_create_requires_family_plan(self, client: AsyncClient, db_session: AsyncSession, member: Person):
        res = await client.post(f"{FAMILIES}/", headers=member.headers)
        assert res.status_code == 403
        assert res.json()["code"] == ErrorCode.FAMILY_PLAN_REQUIRED

        await _grant(db_session, member.id, family=False)
        res = await client.post(f"{FAMILIES}/", headers=member.headers)
        assert res.json()["code"] == ErrorCode.FAMILY_PLAN_REQUIRED

    @pytest.mark.asyncio
    async def test_create_and_get(self, client: AsyncClient, owner: Person):
        assert (await client.get(f"{FAMILIES}/me", headers=owner.headers)).json()["data"] is None

        data = await _create_family(client, owner)
        assert data["role"] == "owner"
        assert data["owner_id"] == str(owner.id)
        assert data["is_frozen"] is False
        assert data["max_members"] == FAMILY_MAX_MEMBERS
        assert [m["user_id"] for m in data["members"]] == [str(owner.id)]

        res = await client.post(f"{FAMILIES}/", headers=owner.headers)
        assert res.status_code == 409
        assert res.json()["code"] == ErrorCode.FAMILY_ALREADY_MEMBER

    @pytest.mark.asyncio
    async def test_requires_auth(self, client: AsyncClient):
        # Без токена — 403 FORBIDDEN (HTTPBearer), как и у остальных эндпоинтов
        assert (await client.get(f"{FAMILIES}/me")).status_code == 403
        assert (await client.post(f"{FAMILIES}/join", json={"code": "ABCDEFGH"})).status_code == 403

    @pytest.mark.asyncio
    async def test_invite_preview_and_join(
        self, client: AsyncClient, db_session: AsyncSession, owner: Person, member: Person
    ):
        await _create_family(client, owner)
        res = await client.post(f"{FAMILIES}/me/invites", headers=owner.headers)
        invite = res.json()["data"]
        assert len(invite["code"]) == 8
        # В БД — только HMAC кода
        stored = await db_session.scalar(select(FamilyInvite.code_hash))
        assert stored != invite["code"] and len(stored) == 64

        # Регистр, пробелы и дефисы при вводе не важны
        typed = f"{invite['code'][:4].lower()} - {invite['code'][4:]}"
        preview = await client.post(f"{FAMILIES}/invites/preview", json={"code": typed}, headers=member.headers)
        assert preview.status_code == 200
        assert preview.json()["data"]["owner_name"] == "Owner"
        assert preview.json()["data"]["members_count"] == 1

        res = await client.post(f"{FAMILIES}/join", json={"code": typed}, headers=member.headers)
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["role"] == "member"
        # Участнику email других не показывается, приглашения — тоже
        assert all(m["email"] is None for m in data["members"])
        assert data["invites"] == []
        assert [m["name"] for m in data["members"]] == ["Owner", "Anna"]

        # Владельцу видны email участников
        owner_view = (await client.get(f"{FAMILIES}/me", headers=owner.headers)).json()["data"]
        assert owner_view["members"][1]["email"] == member.email

        # Код одноразовый
        other = await _person(db_session, "Other")
        res = await client.post(f"{FAMILIES}/join", json={"code": invite["code"]}, headers=other.headers)
        assert res.status_code == 404
        assert res.json()["code"] == ErrorCode.FAMILY_INVITE_INVALID

    @pytest.mark.asyncio
    async def test_join_errors(self, client: AsyncClient, db_session: AsyncSession, owner: Person, member: Person):
        await _create_family(client, owner)
        res = await client.post(f"{FAMILIES}/join", json={"code": "ABCDEFGH"}, headers=member.headers)
        assert res.status_code == 404

        res = await client.post(f"{FAMILIES}/join", json={"code": "ABC"}, headers=member.headers)
        assert res.status_code == 422

        # Владелец — уже в семье
        code = await _invite_code(client, owner)
        res = await client.post(f"{FAMILIES}/join", json={"code": code}, headers=owner.headers)
        assert res.status_code == 409
        assert res.json()["code"] == ErrorCode.FAMILY_ALREADY_MEMBER

    @pytest.mark.asyncio
    async def test_expired_invite_rejected(
        self, client: AsyncClient, db_session: AsyncSession, owner: Person, member: Person
    ):
        await _create_family(client, owner)
        code = await _invite_code(client, owner)
        invite = await db_session.scalar(select(FamilyInvite))
        invite.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db_session.commit()
        res = await client.post(f"{FAMILIES}/join", json={"code": code}, headers=member.headers)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_invites_limited_by_free_seats_and_revocable(self, client: AsyncClient, owner: Person):
        await _create_family(client, owner)
        for _ in range(FAMILY_MAX_MEMBERS - 1):
            await _invite_code(client, owner)
        res = await client.post(f"{FAMILIES}/me/invites", headers=owner.headers)
        assert res.status_code == 409
        assert res.json()["code"] == ErrorCode.FAMILY_FULL

        invites = (await client.get(f"{FAMILIES}/me", headers=owner.headers)).json()["data"]["invites"]
        assert len(invites) == FAMILY_MAX_MEMBERS - 1
        res = await client.delete(f"{FAMILIES}/me/invites/{invites[0]['id']}", headers=owner.headers)
        assert res.status_code == 200
        res = await client.delete(f"{FAMILIES}/me/invites/{invites[0]['id']}", headers=owner.headers)
        assert res.status_code == 404
        assert (await client.post(f"{FAMILIES}/me/invites", headers=owner.headers)).status_code == 201

    @pytest.mark.asyncio
    async def test_member_cannot_manage(self, client: AsyncClient, family: dict, owner: Person, member: Person):
        res = await client.post(f"{FAMILIES}/me/invites", headers=member.headers)
        assert res.status_code == 403
        assert res.json()["code"] == ErrorCode.FAMILY_OWNER_ONLY
        res = await client.delete(f"{FAMILIES}/me/members/{owner.id}", headers=member.headers)
        assert res.json()["code"] == ErrorCode.FAMILY_OWNER_ONLY

    @pytest.mark.asyncio
    async def test_invite_code_attempts_rate_limited(self, client: AsyncClient, member: Person):
        codes = [{"code": "ABCDEFGH"}] * 10
        for body in codes[:5]:
            assert (await client.post(f"{FAMILIES}/invites/preview", json=body, headers=member.headers)).status_code == 404
        for body in codes[5:]:
            assert (await client.post(f"{FAMILIES}/join", json=body, headers=member.headers)).status_code == 404
        res = await client.post(f"{FAMILIES}/join", json=codes[0], headers=member.headers)
        assert res.status_code == 429


class TestFamilyPlan:
    """Pro участника из подписки владельца и заморозка."""

    @pytest.mark.asyncio
    async def test_member_gets_pro_via_family(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        sub = (await client.get("/api/v1/auth/me", headers=member.headers)).json()["data"]["subscription"]
        assert sub["plan"] == "pro" and sub["via_family"] is True and sub["family_plan"] is False
        assert sub["store"] is None

        owner_sub = (await client.get("/api/v1/auth/me", headers=owner.headers)).json()["data"]["subscription"]
        assert owner_sub["plan"] == "pro" and owner_sub["family_plan"] is True and owner_sub["via_family"] is False
        assert owner_sub["store"] == "play_store"

        # Владелец перешёл на личный Pro: семья заморожена, у владельца Pro остаётся, участник — на Free
        await _grant(db_session, owner.id, family=False)
        sub = (await client.get("/api/v1/auth/me", headers=member.headers)).json()["data"]["subscription"]
        assert sub["plan"] == "free"
        owner_sub = (await client.get("/api/v1/auth/me", headers=owner.headers)).json()["data"]["subscription"]
        assert owner_sub["plan"] == "pro" and owner_sub["family_plan"] is False
        assert (await client.get(f"{FAMILIES}/me", headers=member.headers)).json()["data"]["is_frozen"] is True

    @pytest.mark.asyncio
    async def test_own_pro_takes_priority(self, client: AsyncClient, db_session: AsyncSession, family: dict, member: Person):
        await _grant(db_session, member.id, family=False, days=10)
        sub = (await client.get("/api/v1/auth/me", headers=member.headers)).json()["data"]["subscription"]
        assert sub["plan"] == "pro" and sub["via_family"] is False and sub["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_frozen_family_is_read_only(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        shared = await _create_tx(client, owner, shared=True)
        await _grant(db_session, owner.id, days=-1)

        assert shared["id"] in await _visible_ids(client, member)
        res = await client.post("/api/v1/transactions/", json=_tx(shared=True), headers=member.headers)
        assert res.status_code == 403
        assert res.json()["code"] == ErrorCode.FAMILY_FROZEN
        res = await client.delete(f"/api/v1/transactions/{shared['id']}", headers=owner.headers)
        assert res.json()["code"] == ErrorCode.FAMILY_FROZEN
        res = await client.post(f"{FAMILIES}/me/invites", headers=owner.headers)
        assert res.json()["code"] == ErrorCode.FAMILY_FROZEN

        sync = await client.post(
            "/api/v1/sync/",
            json={
                "transactions": [_tx(id=shared["id"], shared=True, amount="99")],
                "deleted_transaction_ids": [shared["id"]],
            },
            headers=member.headers,
        )
        codes = [item["code"] for item in sync.json()["data"]["rejected"]]
        assert codes == [ErrorCode.FAMILY_FROZEN, ErrorCode.FAMILY_FROZEN]
        # Личные записи в замороженной семье работают
        await _create_tx(client, member)

        # Продлил — всё вернулось
        await _grant(db_session, owner.id)
        await _create_tx(client, member, shared=True)


class TestSharedRecords:
    """Видимость и права на общие транзакции."""

    @pytest.mark.asyncio
    async def test_visibility(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        shared = await _create_tx(client, member, shared=True)
        personal = await _create_tx(client, member)
        assert shared["is_shared"] is True and shared["author_id"] == str(member.id)
        assert personal["is_shared"] is False

        owner_ids = await _visible_ids(client, owner)
        assert shared["id"] in owner_ids and personal["id"] not in owner_ids
        assert {shared["id"], personal["id"]} <= await _visible_ids(client, member)

        outsider = await _person(db_session, "Outsider")
        assert shared["id"] not in await _visible_ids(client, outsider)

    @pytest.mark.asyncio
    async def test_shared_requires_family(self, client: AsyncClient, member: Person):
        res = await client.post("/api/v1/transactions/", json=_tx(shared=True), headers=member.headers)
        assert res.status_code == 404
        assert res.json()["code"] == ErrorCode.FAMILY_NOT_FOUND

    @pytest.mark.asyncio
    async def test_member_edits_shared_but_not_flag(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        shared = await _create_tx(client, owner, shared=True)
        res = await client.post(
            "/api/v1/sync/", json={"transactions": [_tx(id=shared["id"], shared=True, amount="42")]}, headers=member.headers
        )
        data = res.json()["data"]
        assert data["rejected"] == []
        assert data["synced_transactions"][0]["author_id"] == str(owner.id)
        assert Decimal(data["synced_transactions"][0]["amount"]) == Decimal("42")

        res = await client.post(
            "/api/v1/sync/", json={"transactions": [_tx(id=shared["id"], shared=False)]}, headers=member.headers
        )
        assert res.json()["data"]["rejected"][0]["code"] == ErrorCode.FAMILY_AUTHOR_ONLY

        # Чужая личная запись — конфликт ID
        personal = await _create_tx(client, owner)
        res = await client.post("/api/v1/sync/", json={"transactions": [_tx(id=personal["id"])]}, headers=member.headers)
        assert res.json()["data"]["rejected"][0]["code"] == ErrorCode.ID_CONFLICT

    @pytest.mark.asyncio
    async def test_shared_base_amount_in_author_currency(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        member.user.base_currency = "EUR"
        await db_session.commit()
        shared = await _create_tx(client, owner, shared=True)
        res = await client.post(
            "/api/v1/sync/",
            json={"transactions": [_tx(id=shared["id"], shared=True, amount="50", exchange_rate="0.9", base_amount="45")]},
            headers=member.headers,
        )
        item = res.json()["data"]["synced_transactions"][0]
        # Валюта операции совпадает с базовой валютой автора (USD): курс клиента в EUR не берётся
        assert Decimal(item["base_amount"]) == Decimal("50") and Decimal(item["exchange_rate"]) == Decimal("1")

    @pytest.mark.asyncio
    async def test_changes_deliver_family_edits_and_deletions(
        self, client: AsyncClient, family: dict, owner: Person, member: Person
    ):
        start = (await client.get("/api/v1/sync/changes", headers=owner.headers)).json()["data"]
        assert start["family_id"] == family["id"]
        since = start["server_time"]

        deleted = await _create_tx(client, owner, shared=True)
        unshared = await _create_tx(client, owner, shared=True)
        res = await client.delete(f"/api/v1/transactions/{deleted['id']}", headers=member.headers)
        assert res.status_code == 200
        res = await client.post(
            "/api/v1/sync/", json={"transactions": [_tx(id=unshared["id"], shared=False)]}, headers=owner.headers
        )
        assert res.json()["data"]["rejected"] == []

        member_changes = (await client.get("/api/v1/sync/changes", params={"since": since}, headers=member.headers)).json()[
            "data"
        ]
        assert set(member_changes["deleted_transaction_ids"]) == {deleted["id"], unshared["id"]}
        assert unshared["id"] not in {t["id"] for t in member_changes["transactions"]}

        owner_changes = (await client.get("/api/v1/sync/changes", params={"since": since}, headers=owner.headers)).json()[
            "data"
        ]
        # Автор видит запись личной, удалённой она для него не считается
        assert owner_changes["deleted_transaction_ids"] == [deleted["id"]]
        assert unshared["id"] in {t["id"] for t in owner_changes["transactions"] if not t["is_shared"]}

    @pytest.mark.asyncio
    async def test_shared_budgets_and_recurring(self, client: AsyncClient, family: dict, owner: Person, member: Person):
        await _create_tx(client, owner, shared=True, amount="30")
        await _create_tx(client, member, shared=True, amount="20")
        await _create_tx(client, member, amount="5")
        await _create_tx(client, owner, amount="7")

        period = {"category": "food", "limit_amount": "100", "currency": "USD", "month": 11, "year": 2026}
        res = await client.post("/api/v1/budgets/", json={**period, "is_shared": True}, headers=member.headers)
        assert res.status_code == 201
        assert res.json()["data"]["is_shared"] is True
        res = await client.post("/api/v1/budgets/", json=period, headers=member.headers)
        assert res.status_code == 201

        params = {"month": 11, "year": 2026}
        budgets = (await client.get("/api/v1/budgets/", params=params, headers=member.headers)).json()["data"]
        spent = {b["is_shared"]: Decimal(b["spent"]) for b in budgets}
        # Общий лимит — общие траты всех участников; личный — только мои личные
        assert spent == {True: Decimal("50"), False: Decimal("5")}
        owner_budgets = (await client.get("/api/v1/budgets/", params=params, headers=owner.headers)).json()["data"]
        assert [(b["is_shared"], Decimal(b["spent"])) for b in owner_budgets] == [(True, Decimal("50"))]

        # Общий лимит правит другой участник: автор остаётся прежним
        res = await client.post(
            "/api/v1/sync/",
            json={"budgets": [{**period, "limit_amount": "150", "is_shared": True}]},
            headers=owner.headers,
        )
        synced = res.json()["data"]["synced_budgets"][0]
        assert Decimal(synced["limit_amount"]) == Decimal("150") and synced["author_id"] == str(member.id)

        payment = {
            "id": str(uuid.uuid4()),
            "type": "expense",
            "category": "housing",
            "amount": "500",
            "currency": "USD",
            "day_of_month": 5,
            "start_date": "2026-11-01",
            "is_shared": True,
        }
        res = await client.post("/api/v1/sync/", json={"recurring_payments": [payment]}, headers=owner.headers)
        assert res.json()["data"]["rejected"] == []
        listed = (await client.get("/api/v1/recurring-payments/", headers=member.headers)).json()["data"]
        assert [(p["id"], p["is_shared"], p["author_id"]) for p in listed] == [(payment["id"], True, str(owner.id))]

        res = await client.delete("/api/v1/budgets/category/food/11/2026", params={"shared": True}, headers=owner.headers)
        assert res.status_code == 200
        remaining = (await client.get("/api/v1/budgets/", params=params, headers=member.headers)).json()["data"]
        assert [b["is_shared"] for b in remaining] == [False]


class TestLeaveAndDissolve:
    """Выход, исключение, роспуск и удаление аккаунта."""

    @pytest.mark.asyncio
    async def test_member_leaves_with_copies(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        mine = await _create_tx(client, member, shared=True, amount="11")
        theirs = await _create_tx(client, owner, shared=True)

        assert (await client.post(f"{FAMILIES}/me/leave", headers=member.headers)).status_code == 200
        assert (await client.get(f"{FAMILIES}/me", headers=member.headers)).json()["data"] is None

        member_ids = await _visible_ids(client, member)
        assert mine["id"] not in member_ids and theirs["id"] not in member_ids
        copies = (await client.get("/api/v1/transactions/", headers=member.headers)).json()["data"]["items"]
        assert [(Decimal(c["amount"]), c["is_shared"]) for c in copies] == [(Decimal("11"), False)]
        # Общие записи остаются в семье
        assert {mine["id"], theirs["id"]} <= await _visible_ids(client, owner)
        changes = (await client.get("/api/v1/sync/changes", headers=member.headers)).json()["data"]
        assert changes["family_id"] is None

        # «Мой» отчёт не считает общую запись бывшей семьи вместе с её копией
        fingerprint = await AIService(db_session)._month_fingerprint(ReportScope.personal(member.id), 11, 2026)
        assert fingerprint.tx_count == 1

    @pytest.mark.asyncio
    async def test_owner_removes_member(self, client: AsyncClient, family: dict, owner: Person, member: Person):
        await _create_tx(client, member, shared=True)
        res = await client.delete(f"{FAMILIES}/me/members/{owner.id}", headers=owner.headers)
        assert res.status_code == 400
        res = await client.delete(f"{FAMILIES}/me/members/{uuid.uuid4()}", headers=owner.headers)
        assert res.status_code == 404
        res = await client.delete(f"{FAMILIES}/me/members/{member.id}", headers=owner.headers)
        assert res.status_code == 200
        assert (await client.get(f"{FAMILIES}/me", headers=member.headers)).json()["data"] is None
        assert len(await _visible_ids(client, member)) == 1

    @pytest.mark.asyncio
    async def test_owner_leaves_dissolves_family(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        mine = await _create_tx(client, member, shared=True)
        theirs = await _create_tx(client, owner, shared=True)
        period = {"category": "food", "limit_amount": "100", "month": 11, "year": 2026}
        await client.post("/api/v1/budgets/", json={**period, "is_shared": True}, headers=owner.headers)
        await client.post("/api/v1/budgets/", json=period, headers=owner.headers)

        assert (await client.post(f"{FAMILIES}/me/leave", headers=owner.headers)).status_code == 200
        assert await db_session.scalar(select(Family.id)) is None
        assert await db_session.scalar(select(FamilyMember.id)) is None

        assert mine["id"] in await _visible_ids(client, member)
        assert theirs["id"] not in await _visible_ids(client, member)
        owner_items = (await client.get("/api/v1/transactions/", headers=owner.headers)).json()["data"]["items"]
        assert [(i["id"], i["is_shared"]) for i in owner_items] == [(theirs["id"], False)]
        # Общий лимит совпал с личным лимитом автора — остался личный
        budgets = (await client.get("/api/v1/budgets/", headers=owner.headers)).json()["data"]
        assert [b["is_shared"] for b in budgets] == [False]
        sub = (await client.get("/api/v1/auth/me", headers=member.headers)).json()["data"]["subscription"]
        assert sub["plan"] == "free"

    @pytest.mark.asyncio
    async def test_member_account_deletion_keeps_shared(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        shared = await _create_tx(client, member, shared=True)
        await _create_tx(client, member)
        assert (await client.delete("/api/v1/auth/me", headers=member.headers)).status_code == 200

        items = (await client.get("/api/v1/transactions/", headers=owner.headers)).json()["data"]["items"]
        assert [(i["id"], i["author_id"], i["is_shared"]) for i in items] == [(shared["id"], None, True)]

        # Роспуск: записи без автора некому отдать — удаляются
        assert (await client.post(f"{FAMILIES}/me/leave", headers=owner.headers)).status_code == 200
        assert await db_session.scalar(select(Transaction.id).where(Transaction.id == uuid.UUID(shared["id"]))) is None

    @pytest.mark.asyncio
    async def test_owner_account_deletion_dissolves(
        self, client: AsyncClient, db_session: AsyncSession, family: dict, owner: Person, member: Person
    ):
        mine = await _create_tx(client, member, shared=True)
        await _create_tx(client, owner, shared=True)
        assert (await client.delete("/api/v1/auth/me", headers=owner.headers)).status_code == 200
        items = (await client.get("/api/v1/transactions/", headers=member.headers)).json()["data"]["items"]
        assert [(i["id"], i["is_shared"]) for i in items] == [(mine["id"], False)]
        assert (await client.get(f"{FAMILIES}/me", headers=member.headers)).json()["data"] is None

    @pytest.mark.asyncio
    async def test_leave_without_family(self, client: AsyncClient, member: Person):
        res = await client.post(f"{FAMILIES}/me/leave", headers=member.headers)
        assert res.status_code == 404
        assert res.json()["code"] == ErrorCode.FAMILY_NOT_FOUND


class TestFamilyInsights:
    """ИИ-отчёт по семье: доступ и общий кэш."""

    @pytest.mark.asyncio
    async def test_family_scope_requires_family(self, client: AsyncClient, member: Person):
        res = await client.get("/api/v1/insights/", params={"scope": "family"}, headers=member.headers)
        assert res.status_code == 404
        assert res.json()["code"] == ErrorCode.FAMILY_NOT_FOUND

    @pytest.mark.asyncio
    async def test_family_report_shared_between_members(
        self, client: AsyncClient, family: dict, owner: Person, member: Person
    ):
        for _ in range(MIN_TRANSACTIONS_FOR_INSIGHTS):
            await _create_tx(client, owner, shared=True)
        params = {"scope": "family", "month": 11, "year": 2026}
        with (
            patch("app.services.ai_service.ai_configured", return_value=True),
            patch.object(AIService, "_call_model", AsyncMock(return_value=["Семейный совет"])) as model,
        ):
            first = await client.get("/api/v1/insights/", params=params, headers=member.headers)
            assert first.json()["data"]["insights"] == ["Семейный совет"]
            second = await client.get("/api/v1/insights/", params={**params, "generate": False}, headers=owner.headers)
            assert second.json()["data"]["insights"] == ["Семейный совет"]
            # «Мой» отчёт участника — свой: общих записей владельца в нём нет
            mine = await client.get("/api/v1/insights/", params={"month": 11, "year": 2026}, headers=member.headers)
            assert mine.json()["data"]["state"] == "no_data"
        assert model.await_count == 1


class TestUnits:
    """Права на записи и разбор семейного права RevenueCat."""

    def test_parse_family_entitlement(self):
        subscriber = {
            "entitlements": {
                "pro": {"product_identifier": "family_monthly", "expires_date": "2026-12-14T10:00:00Z"},
                "family": {"product_identifier": "family_monthly", "expires_date": "2026-12-14T10:00:00Z"},
            },
            "subscriptions": {"family_monthly": {"period_type": "trial", "store": "play_store"}},
        }
        result = parse_pro_entitlement(subscriber, "pro", "family")
        assert result.family is True
        assert result.family_expires_at == datetime(2026, 12, 14, 10, tzinfo=timezone.utc)
        assert result.is_trial is True
        personal = parse_pro_entitlement({"entitlements": {"pro": subscriber["entitlements"]["pro"]}}, "pro", "family")
        assert personal.family is False and personal.family_expires_at is None

    def test_write_error_matrix(self):
        me, other, family_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        active = SharedAccess(me, FamilyContext(family_id, other, FamilyRole.member, frozen=False))
        frozen = SharedAccess(me, FamilyContext(family_id, other, FamilyRole.member, frozen=True))
        alone = SharedAccess(me, None)

        def record(user_id, fam):
            return Transaction(user_id=user_id, family_id=fam, type=TransactionType.expense)

        assert active.write_error(None, True) is None
        assert alone.write_error(None, True) is ErrorCode.FAMILY_NOT_FOUND
        assert frozen.write_error(None, True) is ErrorCode.FAMILY_FROZEN
        assert frozen.write_error(record(me, None), False) is None
        assert active.write_error(record(other, family_id), True) is None
        assert active.write_error(record(other, family_id), False) is ErrorCode.FAMILY_AUTHOR_ONLY
        assert active.write_error(record(me, family_id), False) is None
        assert frozen.write_error(record(me, family_id), False) is ErrorCode.FAMILY_FROZEN
        assert active.write_error(record(other, None), False) is ErrorCode.ID_CONFLICT
        assert active.write_error(record(other, uuid.uuid4()), True) is ErrorCode.ID_CONFLICT

    @pytest.mark.asyncio
    async def test_budget_unique_per_scope(self, db_session: AsyncSession, owner: Person):
        family = Family(owner_id=owner.id)
        db_session.add(family)
        await db_session.flush()
        common = {"category": "food", "limit_amount": Decimal("1"), "currency": "USD", "month": 1, "year": 2026}
        db_session.add_all([Budget(user_id=owner.id, **common), Budget(user_id=owner.id, family_id=family.id, **common)])
        await db_session.commit()
        assert len((await db_session.scalars(select(Budget))).all()) == 2
