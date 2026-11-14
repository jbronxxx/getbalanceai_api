"""Тесты тарифов и подписки: RevenueCat (клиент, вебхук, обновление), профиль с лимитами, удаление покупателя."""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Subscription, User
from app.schemas.schemas import LimitPeriod
from app.services.plan_limits import usage_window
from app.services.revenuecat import (
    ProEntitlement,
    RevenueCatClient,
    RevenueCatError,
    parse_pro_entitlement,
)
from config_reader.config_reader import config

WEBHOOK_URL = "/api/v1/subscriptions/webhooks/revenuecat"
REFRESH_URL = "/api/v1/subscriptions/refresh"
WEBHOOK_AUTH = "Bearer webhook-secret"


def _entitlement(days: int = 30, **overrides) -> ProEntitlement:
    """Доступ Pro, действующий `days` дней (отрицательное — истёк)."""
    values = {
        "expires_at": datetime.now(timezone.utc) + timedelta(days=days),
        "will_renew": True,
        "is_trial": False,
        "product_id": "pro_monthly:monthly",
        "store": "play_store",
    }
    return ProEntitlement(**{**values, **overrides})


@pytest.fixture
def revenuecat(monkeypatch: pytest.MonkeyPatch):
    """Ключи RevenueCat заданы, клиент — фиктивный: без сети."""
    monkeypatch.setattr(config, "revenuecat_api_key", "sk_test")
    monkeypatch.setattr(config, "revenuecat_webhook_auth", WEBHOOK_AUTH)
    client = MagicMock(spec=RevenueCatClient)
    client.get_pro_entitlement = AsyncMock(return_value=_entitlement())
    client.delete_subscriber = AsyncMock()
    with patch("app.services.subscription_service.get_revenuecat_client", return_value=client):
        yield client


def _event(app_user_id: str, event_type: str = "INITIAL_PURCHASE", **extra) -> dict:
    return {"api_version": "1.0", "event": {"id": "evt-1", "type": event_type, "app_user_id": app_user_id, **extra}}


async def _subscription(db: AsyncSession, user_id: uuid.UUID) -> Subscription | None:
    return await db.scalar(
        select(Subscription).where(Subscription.user_id == user_id).execution_options(populate_existing=True)
    )


class TestParseEntitlement:
    """Разбор ответа GET /subscribers RevenueCat."""

    @staticmethod
    def _subscriber(entitlement: dict | None = None, subscription: dict | None = None) -> dict:
        product = "pro_monthly:monthly"
        return {
            "entitlements": {"pro": {"product_identifier": product, **entitlement}} if entitlement is not None else {},
            "subscriptions": {product: subscription} if subscription is not None else {},
        }

    def test_active_subscription(self):
        subscriber = self._subscriber(
            {"expires_date": "2026-12-14T10:00:00Z"},
            {"period_type": "normal", "store": "play_store", "unsubscribe_detected_at": None},
        )
        result = parse_pro_entitlement(subscriber, "pro")
        assert result == ProEntitlement(
            expires_at=datetime(2026, 12, 14, 10, tzinfo=timezone.utc),
            will_renew=True,
            is_trial=False,
            product_id="pro_monthly:monthly",
            store="play_store",
        )

    def test_trial_cancelled_and_grace_period(self):
        """Пробный период с отменой: продления не будет; льготный период магазина продлевает доступ."""
        subscriber = self._subscriber(
            {"expires_date": "2026-12-01T00:00:00Z", "grace_period_expires_date": "2026-12-08T00:00:00Z"},
            {"period_type": "trial", "store": "play_store", "unsubscribe_detected_at": "2026-11-20T00:00:00Z"},
        )
        result = parse_pro_entitlement(subscriber, "pro")
        assert result.is_trial is True
        assert result.will_renew is False
        assert result.expires_at == datetime(2026, 12, 8, tzinfo=timezone.utc)

    def test_lifetime_promotional_access(self):
        """Доступ, выданный вручную без срока: бессрочный, без продления."""
        result = parse_pro_entitlement(self._subscriber({"expires_date": None}), "pro")
        assert result.expires_at is None
        assert result.will_renew is False
        assert result.store == "unknown"

    def test_no_entitlement(self):
        assert parse_pro_entitlement(self._subscriber(), "pro") is None
        assert parse_pro_entitlement({}, "pro") is None


class TestRevenueCatClient:
    """HTTP-запросы клиента к REST API RevenueCat."""

    @staticmethod
    def _client(handler) -> RevenueCatClient:
        client = RevenueCatClient()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://rc.test/v1")
        return client

    @pytest.mark.asyncio
    async def test_get_pro_entitlement(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET" and request.url.path == "/v1/subscribers/user-1"
            return httpx.Response(200, json={"subscriber": {"entitlements": {"pro": {"expires_date": None}}}})

        result = await self._client(handler).get_pro_entitlement("user-1")
        assert result is not None and result.expires_at is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response",
        [httpx.Response(500), httpx.Response(200, json={"unexpected": True}), httpx.Response(200, text="not json")],
        ids=["server_error", "no_subscriber", "invalid_json"],
    )
    async def test_get_errors(self, response: httpx.Response):
        with pytest.raises(RevenueCatError):
            await self._client(lambda _request: response).get_pro_entitlement("user-1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [200, 404])
    async def test_delete_subscriber(self, status_code: int):
        """Удаление; покупателя нет (404) — тоже успех."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            return httpx.Response(status_code, json={})

        await self._client(handler).delete_subscriber("user-1")
        assert calls == [("DELETE", "/v1/subscribers/user-1")]

    @pytest.mark.asyncio
    async def test_delete_error(self):
        with pytest.raises(RevenueCatError):
            await self._client(lambda _request: httpx.Response(503)).delete_subscriber("user-1")


class TestUsageWindow:
    """Окна лимитов в часовом поясе клиента."""

    def test_day_window_in_client_timezone(self):
        """23:30 UTC в UTC+5 — уже следующий день: сброс в полночь по часам клиента."""
        now = datetime(2026, 11, 14, 23, 30, tzinfo=timezone.utc)
        window, resets_at = usage_window(LimitPeriod.day, timezone(timedelta(hours=5)), now)
        assert window == "2026-11-15"
        assert resets_at == datetime(2026, 11, 15, 19, tzinfo=timezone.utc)

    def test_month_window_rolls_over_year(self):
        now = datetime(2026, 12, 31, 20, tzinfo=timezone.utc)
        window, resets_at = usage_window(LimitPeriod.month, timezone(timedelta(hours=-3)), now)
        assert window == "2026-12"
        assert resets_at == datetime(2027, 1, 1, 3, tzinfo=timezone.utc)
        window, _ = usage_window(LimitPeriod.month, timezone(timedelta(hours=5)), now)
        assert window == "2027-01"


class TestProfile:
    """GET /auth/me: тариф, срок подписки, лимиты с остатком и временем сброса."""

    @pytest.mark.asyncio
    async def test_free_profile_with_usage(
        self, client: AsyncClient, auth_headers: dict, test_user: User, mock_redis_client
    ):
        user_id = test_user.id
        tz = timezone(timedelta(hours=5))
        local = datetime.now(tz)
        await mock_redis_client.set(f"plan_usage:voice:{user_id}:{local.date().isoformat()}", 3)
        await mock_redis_client.set(f"plan_usage:ai_report:{user_id}:{local.strftime('%Y-%m')}", 1)

        response = await client.get("/api/v1/auth/me", headers={**auth_headers, "X-Timezone-Offset": "300"})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == str(user_id)
        assert data["legal"]["terms_accepted"] is True
        assert data["subscription"] == {
            "plan": "free",
            "expires_at": None,
            "will_renew": False,
            "is_trial": False,
            "family_plan": False,
            "via_family": False,
            "store": None,
        }
        limits = {item["feature"]: item for item in data["limits"]}
        assert limits["voice"]["period"] == "day"
        assert (limits["voice"]["limit"], limits["voice"]["used"], limits["voice"]["remaining"]) == (5, 3, 2)
        tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        assert datetime.fromisoformat(limits["voice"]["resets_at"]) == tomorrow
        assert limits["ai_report"]["period"] == "month"
        assert (limits["ai_report"]["limit"], limits["ai_report"]["remaining"]) == (1, 0)
        next_month = date(local.year + local.month // 12, local.month % 12 + 1, 1)
        assert datetime.fromisoformat(limits["ai_report"]["resets_at"]) == datetime.combine(
            next_month, datetime.min.time(), tzinfo=tz
        )

    @pytest.mark.asyncio
    async def test_pro_profile(self, client: AsyncClient, auth_headers: dict, test_user: User, db_session: AsyncSession):
        expires_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        db_session.add(
            Subscription(
                user_id=test_user.id,
                expires_at=expires_at,
                will_renew=True,
                is_trial=True,
                product_id="pro_yearly:yearly",
                store="play_store",
            )
        )
        await db_session.commit()

        data = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["data"]

        assert data["subscription"]["plan"] == "pro"
        assert datetime.fromisoformat(data["subscription"]["expires_at"]) == expires_at
        assert data["subscription"]["will_renew"] is True and data["subscription"]["is_trial"] is True
        assert data["subscription"]["store"] == "play_store"
        limits = {item["feature"]: item for item in data["limits"]}
        assert (limits["ai_report"]["limit"], limits["ai_report"]["period"]) == (5, "day")
        assert (limits["voice"]["limit"], limits["voice"]["remaining"]) == (30, 30)

    @pytest.mark.asyncio
    async def test_expired_subscription_is_free(
        self, client: AsyncClient, auth_headers: dict, test_user: User, db_session: AsyncSession
    ):
        """Срок прошёл — тариф Free, дата окончания прошлой подписки остаётся, продления и пробного нет."""
        db_session.add(
            Subscription(
                user_id=test_user.id,
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                will_renew=True,
                is_trial=True,
                product_id="pro_monthly:monthly",
                store="play_store",
            )
        )
        await db_session.commit()

        subscription = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["data"]["subscription"]

        assert subscription["plan"] == "free"
        assert subscription["expires_at"] is not None
        assert subscription["will_renew"] is False and subscription["is_trial"] is False
        assert subscription["store"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("stored", "expected"), [("manual", "manual"), ("PROMOTIONAL", "promotional")])
    async def test_store_of_active_subscription(
        self,
        client: AsyncClient,
        auth_headers: dict,
        test_user: User,
        db_session: AsyncSession,
        stored: str,
        expected: str,
    ):
        """Ручной и промо-Pro отдают свой магазин в нижнем регистре: кнопки управления в Google Play у них нет."""
        db_session.add(
            Subscription(
                user_id=test_user.id, expires_at=None, will_renew=False, is_trial=False, product_id=stored, store=stored
            )
        )
        await db_session.commit()

        subscription = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["data"]["subscription"]

        assert subscription["plan"] == "pro" and subscription["store"] == expected

    @pytest.mark.asyncio
    async def test_redis_unavailable_returns_limits_without_usage(
        self, client: AsyncClient, auth_headers: dict, mock_redis_client
    ):
        with patch.object(mock_redis_client, "mget", AsyncMock(side_effect=ConnectionError("redis down"))):
            response = await client.get("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 200
        for item in response.json()["data"]["limits"]:
            assert item["used"] is None and item["remaining"] is None and item["limit"] > 0

    @pytest.mark.asyncio
    async def test_requires_auth(self, client: AsyncClient):
        """Без токена — 403 (HTTPBearer), как и у остальных закрытых эндпоинтов."""
        assert (await client.get("/api/v1/auth/me")).status_code == 403


class TestWebhook:
    """POST /subscriptions/webhooks/revenuecat."""

    @pytest.mark.asyncio
    async def test_purchase_and_expiration(self, client: AsyncClient, test_user: User, db_session: AsyncSession, revenuecat):
        """Покупка — снимок из REST API; повтор — обновление той же строки; доступа больше нет — строка удалена."""
        user_id = test_user.id
        headers = {"Authorization": WEBHOOK_AUTH}

        response = await client.post(WEBHOOK_URL, json=_event(str(user_id)), headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        revenuecat.get_pro_entitlement.assert_awaited_once_with(str(user_id))
        row = await _subscription(db_session, user_id)
        assert row is not None and row.will_renew is True and row.store == "play_store"

        revenuecat.get_pro_entitlement.return_value = _entitlement(will_renew=False, is_trial=True)
        assert (await client.post(WEBHOOK_URL, json=_event(str(user_id), "CANCELLATION"), headers=headers)).is_success
        row = await _subscription(db_session, user_id)
        assert row.will_renew is False and row.is_trial is True
        assert len((await db_session.scalars(select(Subscription))).all()) == 1

        revenuecat.get_pro_entitlement.return_value = None
        assert (await client.post(WEBHOOK_URL, json=_event(str(user_id), "EXPIRATION"), headers=headers)).is_success
        assert await _subscription(db_session, user_id) is None

    @pytest.mark.asyncio
    async def test_transfer_syncs_both_users(
        self, client: AsyncClient, test_user: User, db_session: AsyncSession, revenuecat
    ):
        """Перенос покупки: обновляются и прежний, и новый владелец; анонимный и неизвестный id пропускаются."""
        other_id = uuid.uuid4()
        user_ids = {str(test_user.id), str(other_id)}
        db_session.add(User(id=other_id, email="other@example.com", name="Other"))
        await db_session.commit()
        event = _event(
            "$RCAnonymousID:abc",
            "TRANSFER",
            transferred_from=sorted(user_ids - {str(other_id)}),
            transferred_to=[str(other_id), str(uuid.uuid4())],
        )

        response = await client.post(WEBHOOK_URL, json=event, headers={"Authorization": WEBHOOK_AUTH})

        assert response.status_code == 200
        assert {call.args[0] for call in revenuecat.get_pro_entitlement.await_args_list} == user_ids

    @pytest.mark.asyncio
    async def test_unknown_user_is_skipped(self, client: AsyncClient, revenuecat):
        response = await client.post(WEBHOOK_URL, json=_event("$RCAnonymousID:abc"), headers={"Authorization": WEBHOOK_AUTH})
        assert response.status_code == 200
        revenuecat.get_pro_entitlement.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}], ids=["missing", "wrong"])
    async def test_wrong_authorization(self, client: AsyncClient, test_user: User, revenuecat, headers: dict):
        """Неверный или пустой заголовок Authorization — 401, RevenueCat не опрашивается."""
        response = await client.post(WEBHOOK_URL, json=_event(str(test_user.id)), headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"
        revenuecat.get_pro_entitlement.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_configured(self, client: AsyncClient, test_user: User, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(config, "revenuecat_webhook_auth", "")
        response = await client.post(WEBHOOK_URL, json=_event(str(test_user.id)), headers={"Authorization": "x"})
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_revenuecat_failure_returns_503(
        self, client: AsyncClient, test_user: User, db_session: AsyncSession, revenuecat
    ):
        """RevenueCat недоступен — 503: RevenueCat повторит вебхук; снимок не меняется."""
        user_id = test_user.id
        revenuecat.get_pro_entitlement.side_effect = RevenueCatError("timeout")
        response = await client.post(WEBHOOK_URL, json=_event(str(user_id)), headers={"Authorization": WEBHOOK_AUTH})
        assert response.status_code == 503
        assert await _subscription(db_session, user_id) is None

    @pytest.mark.asyncio
    async def test_invalid_body(self, client: AsyncClient, revenuecat):
        response = await client.post(WEBHOOK_URL, json={"api_version": "1.0"}, headers={"Authorization": WEBHOOK_AUTH})
        assert response.status_code == 422


class TestRefresh:
    """POST /subscriptions/refresh — обновление статуса приложением после покупки."""

    @pytest.mark.asyncio
    async def test_refresh_returns_pro(
        self, client: AsyncClient, auth_headers: dict, test_user: User, db_session: AsyncSession, revenuecat
    ):
        user_id = test_user.id
        response = await client.post(REFRESH_URL, headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["data"]["plan"] == "pro"
        assert response.json()["data"]["will_renew"] is True
        revenuecat.get_pro_entitlement.assert_awaited_once_with(str(user_id))
        assert await _subscription(db_session, user_id) is not None

    @pytest.mark.asyncio
    async def test_refresh_not_configured(self, client: AsyncClient, auth_headers: dict, monkeypatch):
        monkeypatch.setattr(config, "revenuecat_api_key", "")
        response = await client.post(REFRESH_URL, headers=auth_headers)
        assert response.status_code == 503
        assert response.json()["code"] == "SERVICE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_refresh_requires_auth(self, client: AsyncClient, revenuecat):
        assert (await client.post(REFRESH_URL)).status_code == 403
        revenuecat.get_pro_entitlement.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_requires_accepted_terms(
        self, client: AsyncClient, auth_headers: dict, test_user: User, db_session: AsyncSession, revenuecat
    ):
        test_user.accepted_terms_version = "2000-01-01"
        await db_session.commit()
        response = await client.post(REFRESH_URL, headers=auth_headers)
        assert response.status_code == 403
        assert response.json()["code"] == "TERMS_NOT_ACCEPTED"


class TestAccountDeletion:
    """Удаление аккаунта удаляет покупателя RevenueCat (GDPR)."""

    @pytest.mark.asyncio
    async def test_deletes_revenuecat_customer(
        self, client: AsyncClient, auth_headers: dict, test_user: User, db_session: AsyncSession, revenuecat
    ):
        user_id = test_user.id
        db_session.add(
            Subscription(user_id=user_id, will_renew=False, is_trial=False, product_id="promo", store="promotional")
        )
        await db_session.commit()

        response = await client.delete("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 200
        revenuecat.delete_subscriber.assert_awaited_once_with(str(user_id))
        assert await db_session.get(User, user_id, populate_existing=True) is None
        assert await _subscription(db_session, user_id) is None

    @pytest.mark.asyncio
    async def test_revenuecat_failure_keeps_account(
        self, client: AsyncClient, auth_headers: dict, test_user: User, db_session: AsyncSession, revenuecat
    ):
        """RevenueCat недоступен — 503, аккаунт остаётся: приложение повторит удаление."""
        user_id = test_user.id
        revenuecat.delete_subscriber.side_effect = RevenueCatError("timeout")

        response = await client.delete("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 503
        assert await db_session.get(User, user_id, populate_existing=True) is not None

    @pytest.mark.asyncio
    async def test_without_revenuecat_key_skips_customer(
        self, client: AsyncClient, auth_headers: dict, revenuecat, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(config, "revenuecat_api_key", "")
        response = await client.delete("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        revenuecat.delete_subscriber.assert_not_called()
