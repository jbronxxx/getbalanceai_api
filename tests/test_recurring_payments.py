"""Регулярные платежи и доходы (USAB-04): схема, сохранение через /sync, список с ETag."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Category, RecurringPayment, TransactionType, User
from app.routers.dependencies import not_modified_response
from app.schemas.schemas import RecurringPaymentSyncItem

URL = "/api/v1/recurring-payments/"
SYNC_URL = "/api/v1/sync/"


def _payment(**changes) -> dict:
    """Регулярный платёж в формате /sync; changes заменяют поля, значение ... убирает поле."""
    item = {
        "id": str(uuid.uuid4()),
        "type": "expense",
        "category": "housing",
        "amount": "350000",
        "currency": "UZS",
        "description": "ЖКХ",
        "day_of_month": 15,
        "start_date": "2026-11-15",
        "end_date": None,
        "skipped_periods": [],
    }
    item.update(changes)
    return {key: value for key, value in item.items() if value is not ...}


async def _sync(client: AsyncClient, headers: dict[str, str], **payload) -> dict:
    response = await client.post(SYNC_URL, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestRecurringPaymentSchema:
    """Проверка регулярного платежа до записи в БД."""

    def test_valid_payment_normalized(self):
        """Валюта в верхнем регистре, пропуски без повторов по возрастанию, длинное название обрезается."""
        item = RecurringPaymentSyncItem.model_validate(
            _payment(currency=" uzs ", skipped_periods=["2027-01", "2026-12", "2027-01"], description="а" * 300)
        )
        assert item.currency == "UZS"
        assert item.skipped_periods == ["2026-12", "2027-01"]
        assert len(item.description) == 255
        assert item.category == Category.housing
        assert item.type == TransactionType.expense

    def test_income_payment_allowed(self):
        """Регулярный доход (зарплата) — тем же механизмом."""
        item = RecurringPaymentSyncItem.model_validate(_payment(type="income", category="salary", day_of_month=10))
        assert item.type == TransactionType.income

    @pytest.mark.parametrize(
        "changes",
        [
            {"type": "income", "category": "housing"},  # расходная категория у дохода
            {"type": "expense", "category": "salary"},  # доходная категория у расхода
            {"day_of_month": 0},
            {"day_of_month": 32},
            {"amount": "0"},
            {"currency": "RUB"},
            {"end_date": "2026-11-01"},  # раньше начала
            {"skipped_periods": ["2026-13"]},
            {"skipped_periods": ["11-2026"]},
            {"id": ...},  # id создаёт клиент — без него платёж не принимается
        ],
    )
    def test_invalid_payment_rejected(self, changes: dict):
        """Неверные поля и несовместимая категория — ошибка схемы."""
        with pytest.raises(ValidationError):
            RecurringPaymentSyncItem.model_validate(_payment(**changes))


class TestNotModifiedResponse:
    """Общий помощник ETag для списков (бюджеты, транзакции, регулярные платежи)."""

    @pytest.mark.parametrize("header", ['"abc"', 'W/"abc"', '"x", "abc"', "*"])
    def test_matching_etag_returns_304(self, header: str):
        """Совпавший, слабый, один из нескольких ETag или «*» — ответ 304 с тем же ETag."""
        response = not_modified_response(header, "abc")
        assert response is not None
        assert response.status_code == 304
        assert response.headers["ETag"] == '"abc"'

    @pytest.mark.parametrize("header", [None, "", '"other"'])
    def test_other_etag_returns_none(self, header: str | None):
        """Без заголовка или с другим ETag — обычный ответ."""
        assert not_modified_response(header, "abc") is None


class TestRecurringPaymentsSync:
    """Создание, правка и удаление регулярных платежей через /sync."""

    @pytest.mark.asyncio
    async def test_create_and_list(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Созданные платежи возвращаются в ответе /sync и в списке по дню месяца."""
        internet = _payment(category="communication", description="Интернет", day_of_month=5, amount="150000.5")
        housing = _payment(end_date="2027-10-15")
        data = await _sync(client, auth_headers, recurring_payments=[housing, internet])

        assert data["rejected"] == []
        assert {p["id"] for p in data["synced_recurring_payments"]} == {housing["id"], internet["id"]}

        response = await client.get(URL, headers=auth_headers)
        assert response.status_code == 200
        payments = response.json()["data"]
        assert [p["id"] for p in payments] == [internet["id"], housing["id"]]
        assert Decimal(str(payments[0]["amount"])) == Decimal("150000.50")
        assert payments[1]["end_date"] == "2027-10-15"
        assert payments[1]["skipped_periods"] == []

    @pytest.mark.asyncio
    async def test_update_replaces_fields(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Повтор с тем же id заменяет платёж целиком: сумма, день, пропущенный месяц."""
        payment = _payment()
        await _sync(client, auth_headers, recurring_payments=[payment])

        changed = {**payment, "amount": "400000", "day_of_month": 31, "skipped_periods": ["2026-12"]}
        data = await _sync(client, auth_headers, recurring_payments=[changed])
        saved = data["synced_recurring_payments"][0]
        assert Decimal(str(saved["amount"])) == Decimal("400000")
        assert saved["day_of_month"] == 31
        assert saved["skipped_periods"] == ["2026-12"]

        payments = (await client.get(URL, headers=auth_headers)).json()["data"]
        assert len(payments) == 1

    @pytest.mark.asyncio
    async def test_delete_keeps_others(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Удаление по id убирает только этот платёж; неизвестный id не ломает батч."""
        first, second = _payment(), _payment(description="Кредит", category="other")
        await _sync(client, auth_headers, recurring_payments=[first, second])

        await _sync(client, auth_headers, deleted_recurring_payment_ids=[first["id"], str(uuid.uuid4())])
        payments = (await client.get(URL, headers=auth_headers)).json()["data"]
        assert [p["id"] for p in payments] == [second["id"]]

    @pytest.mark.asyncio
    async def test_invalid_payment_rejected_others_saved(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Неверный платёж — в rejected со своим кодом, остальные сохраняются (батч не падает 422)."""
        wrong_category = _payment(type="income", category="housing")
        wrong_day = _payment(day_of_month=40)
        valid = _payment()
        data = await _sync(client, auth_headers, recurring_payments=[wrong_category, wrong_day, valid])

        assert [p["id"] for p in data["synced_recurring_payments"]] == [valid["id"]]
        assert sorted((r["id"], r["code"]) for r in data["rejected"]) == sorted(
            [(wrong_category["id"], "INVALID_CATEGORY"), (wrong_day["id"], "INVALID_SYNC_ITEM")]
        )

    @pytest.mark.asyncio
    async def test_foreign_id_rejected(self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession):
        """Платёж с id чужого платежа не перезаписывает его — ID_CONFLICT; чужое удаление игнорируется."""
        other_id = uuid.uuid4()
        db_session.add(User(id=other_id, email=f"other_{other_id.hex[:8]}@example.com", name="Other"))
        foreign_id = uuid.uuid4()
        db_session.add(
            RecurringPayment(
                id=foreign_id,
                user_id=other_id,
                type=TransactionType.expense,
                category=Category.housing,
                amount=Decimal("100"),
                currency="USD",
                day_of_month=1,
                start_date=date(2026, 11, 1),
            )
        )
        await db_session.commit()

        data = await _sync(
            client,
            auth_headers,
            recurring_payments=[_payment(id=str(foreign_id))],
            deleted_recurring_payment_ids=[str(foreign_id)],
        )
        assert data["synced_recurring_payments"] == []
        assert [(r["id"], r["code"]) for r in data["rejected"]] == [(str(foreign_id), "ID_CONFLICT")]

        db_session.expire_all()
        stored = (await db_session.execute(select(RecurringPayment).where(RecurringPayment.id == foreign_id))).scalar_one()
        assert stored.user_id == other_id
        assert stored.currency == "USD"
        assert (await client.get(URL, headers=auth_headers)).json()["data"] == []

    @pytest.mark.asyncio
    async def test_old_client_payload_still_works(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Клиент без регулярных платежей шлёт старый батч — ответ тот же плюс пустой список."""
        data = await _sync(client, auth_headers, transactions=[], budgets=[])
        assert data["synced_recurring_payments"] == []


class TestRecurringPaymentsList:
    """GET /recurring-payments: авторизация и ETag."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, client: AsyncClient):
        """Без токена — 401/403."""
        response = await client.get(URL)
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_etag_not_modified_and_changes(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Тот же ETag — 304; правка и удаление меняют ETag."""
        payment = _payment()
        await _sync(client, auth_headers, recurring_payments=[payment])

        first = await client.get(URL, headers=auth_headers)
        etag = first.headers["ETag"]
        cached = await client.get(URL, headers={**auth_headers, "If-None-Match": etag})
        assert cached.status_code == 304
        assert cached.headers["ETag"] == etag

        await _sync(client, auth_headers, recurring_payments=[{**payment, "amount": "1"}])
        edited = await client.get(URL, headers={**auth_headers, "If-None-Match": etag})
        assert edited.status_code == 200
        edited_etag = edited.headers["ETag"]
        assert edited_etag != etag

        await _sync(client, auth_headers, deleted_recurring_payment_ids=[payment["id"]])
        deleted = await client.get(URL, headers={**auth_headers, "If-None-Match": edited_etag})
        assert deleted.status_code == 200
        assert deleted.json()["data"] == []

    @pytest.mark.asyncio
    async def test_deleted_with_account(self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession):
        """Удаление аккаунта удаляет и регулярные платежи."""
        await _sync(client, auth_headers, recurring_payments=[_payment()])
        response = await client.delete("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        db_session.expire_all()
        remaining = (await db_session.execute(select(RecurringPayment))).scalars().all()
        assert remaining == []
