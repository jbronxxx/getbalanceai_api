"""Границы месяцев в часовом поясе клиента (заголовок X-Timezone-Offset)."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Category, Transaction, TransactionType, User
from app.services.ai_service import AIService, ReportScope
from app.services.period import month_bounds, timezone_from_offset, timezone_key

UTC_PLUS_5 = {"X-Timezone-Offset": "300"}


class TestPeriodHelpers:
    @pytest.mark.parametrize(
        "raw, minutes",
        [("300", 300), (" -180 ", -180), ("0", 0), (None, 0), ("abc", 0), ("10000", 0), ("-721", 0), ("840", 840)],
    )
    def test_timezone_from_offset(self, raw: str | None, minutes: int):
        """Некорректное или выходящее за UTC-12..UTC+14 смещение даёт UTC, а не ошибку запроса."""
        assert timezone_key(timezone_from_offset(raw)) == str(minutes)

    def test_month_bounds_in_client_timezone(self):
        """Ноябрь в UTC+5 начинается 31 октября в 19:00 UTC."""
        start, end = month_bounds(2026, 11, timezone(timedelta(hours=5)))

        assert start == datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 11, 30, 19, 0, tzinfo=timezone.utc)

    def test_december_rolls_over_to_next_year(self):
        start, end = month_bounds(2026, 12, timezone(timedelta(hours=5)))

        assert start == datetime(2026, 11, 30, 19, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 12, 31, 19, 0, tzinfo=timezone.utc)


async def _night_expense(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    """Расход 1 ноября в 01:00 по UTC+5 — это ещё 31 октября по UTC."""
    db_session.add(
        Transaction(
            user_id=user_id,
            amount=Decimal("100.00"),
            base_amount=Decimal("100.00"),
            currency="USD",
            description="Ночное такси",
            category=Category.transport,
            type=TransactionType.expense,
            date=datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc),
        )
    )
    await db_session.commit()


class TestBudgetsInClientTimezone:
    async def _spent(self, client: AsyncClient, headers: dict[str, str], month: int) -> Decimal:
        await client.post(
            "/api/v1/budgets/",
            json={"category": "transport", "limit_amount": "500", "month": month, "year": 2026},
            headers=headers,
        )
        response = await client.get(f"/api/v1/budgets/?month={month}&year=2026", headers=headers)
        assert response.status_code == 200
        return Decimal(response.json()["data"][0]["spent"])

    @pytest.mark.asyncio
    async def test_night_expense_counts_in_local_month(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Для клиента в UTC+5 трата 1 ноября в 01:00 по местному времени входит в ноябрьский бюджет."""
        await _night_expense(db_session, test_user.id)
        headers = {**auth_headers, **UTC_PLUS_5}

        assert await self._spent(client, headers, 11) == Decimal("100.00")
        assert await self._spent(client, headers, 10) == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_without_header_months_stay_in_utc(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        """Старые клиенты без заголовка получают прежнее поведение — месяцы по UTC."""
        await _night_expense(db_session, test_user.id)

        assert await self._spent(client, auth_headers, 10) == Decimal("100.00")
        assert await self._spent(client, auth_headers, 11) == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_etag_depends_on_timezone(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Ответ для другого пояса отличается, поэтому ETag одного пояса не даёт 304 в другом."""
        utc = await client.get("/api/v1/budgets/?month=11&year=2026", headers=auth_headers)
        local = await client.get("/api/v1/budgets/?month=11&year=2026", headers={**auth_headers, **UTC_PLUS_5})

        assert utc.headers["ETag"] != local.headers["ETag"]

    @pytest.mark.asyncio
    async def test_sync_returns_spent_in_client_timezone(
        self, client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession, test_user: User
    ):
        await _night_expense(db_session, test_user.id)
        payload = {"budgets": [{"category": "transport", "limit_amount": "500", "month": 11, "year": 2026}]}

        response = await client.post("/api/v1/sync/", json=payload, headers={**auth_headers, **UTC_PLUS_5})

        assert response.status_code == 200
        assert Decimal(response.json()["data"]["synced_budgets"][0]["spent"]) == Decimal("100.00")


class TestInsightsInClientTimezone:
    @pytest.mark.asyncio
    async def test_month_fingerprint_uses_client_timezone(self, db_session: AsyncSession, test_user: User):
        """Ночная трата попадает в отчёт ноября для UTC+5 и в отчёт октября для UTC."""
        user_id = test_user.id
        await _night_expense(db_session, user_id)
        service = AIService(db=db_session, client=object())
        plus5 = timezone(timedelta(hours=5))

        assert (await service._month_fingerprint(ReportScope.personal(user_id), 11, 2026, plus5)).tx_count == 1
        assert (await service._month_fingerprint(ReportScope.personal(user_id), 10, 2026, plus5)).tx_count == 0
        assert (await service._month_fingerprint(ReportScope.personal(user_id), 10, 2026)).tx_count == 1
