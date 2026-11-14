"""Тесты CLI-скрипта ручной выдачи и отзыва Pro по email."""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.models import Subscription, User
from app.schemas.schemas import PlanCode
from app.services.subscription_service import MANUAL_STORE, SubscriptionService
from scripts.grant_pro import EXIT_INVALID, EXIT_NOT_FOUND, EXIT_OK, grant_pro
from scripts.grant_pro import main as cli_main
from tests.conftest import TestingSessionLocal


async def _create_user(email: str) -> uuid.UUID:
    """Создать пользователя в тестовой БД."""
    user_id = uuid.uuid4()
    async with TestingSessionLocal() as session:
        session.add(User(id=user_id, email=email, name="CLI User"))
        await session.commit()
    return user_id


async def _subscription(user_id: uuid.UUID) -> Subscription | None:
    async with TestingSessionLocal() as session:
        return await session.scalar(select(Subscription).where(Subscription.user_id == user_id))


async def _plan(user_id: uuid.UUID) -> PlanCode:
    async with TestingSessionLocal() as session:
        return await SubscriptionService(session).plan(user_id)


class TestGrantPro:
    """Ручной Pro: строка `manual` в subscriptions, тариф из неё как из подписки RevenueCat."""

    @pytest.mark.asyncio
    async def test_grants_forever_by_email_case_insensitive(self):
        user_id = await _create_user("pro.user@example.com")

        code = await grant_pro("  PRO.User@Example.com ", session_factory=TestingSessionLocal)

        assert code == EXIT_OK
        row = await _subscription(user_id)
        assert row is not None
        assert (
            row.store,
            row.product_id,
            row.expires_at,
            row.will_renew,
            row.is_trial,
        ) == (
            MANUAL_STORE,
            MANUAL_STORE,
            None,
            False,
            False,
        )
        assert await _plan(user_id) == PlanCode.pro

    @pytest.mark.asyncio
    async def test_grants_until_date(self):
        user_id = await _create_user("until@example.com")
        until = (datetime.now(timezone.utc) + timedelta(days=30)).date()

        code = await grant_pro(
            "until@example.com",
            until=until.isoformat(),
            session_factory=TestingSessionLocal,
        )

        assert code == EXIT_OK
        row = await _subscription(user_id)
        assert row.expires_at_utc() == datetime.combine(until, datetime.min.time(), tzinfo=timezone.utc)
        assert await _plan(user_id) == PlanCode.pro

    @pytest.mark.asyncio
    async def test_regrant_overwrites_existing_row(self):
        user_id = await _create_user("again@example.com")
        until = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
        await grant_pro("again@example.com", until=until, session_factory=TestingSessionLocal)

        await grant_pro("again@example.com", session_factory=TestingSessionLocal)

        row = await _subscription(user_id)
        assert row.expires_at is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("until", ["2020-01-01", "01.12.2027", "завтра"])
    async def test_invalid_or_past_date_changes_nothing(self, until: str, capsys: pytest.CaptureFixture[str]):
        user_id = await _create_user(f"bad-{uuid.uuid4().hex[:6]}@example.com")
        async with TestingSessionLocal() as session:
            email = (await session.get(User, user_id)).email

        code = await grant_pro(email, until=until, session_factory=TestingSessionLocal)

        assert code == EXIT_INVALID
        assert "--until" in capsys.readouterr().out
        assert await _subscription(user_id) is None

    @pytest.mark.asyncio
    async def test_not_found_changes_nothing(self, capsys: pytest.CaptureFixture[str]):
        code = await grant_pro("nobody@example.com", session_factory=TestingSessionLocal)

        assert code == EXIT_NOT_FOUND
        assert "не найден" in capsys.readouterr().out
        async with TestingSessionLocal() as session:
            assert (await session.scalars(select(Subscription))).all() == []

    @pytest.mark.asyncio
    async def test_revoke_removes_manual_pro(self):
        user_id = await _create_user("revoke@example.com")
        await grant_pro("revoke@example.com", session_factory=TestingSessionLocal)

        code = await grant_pro("revoke@example.com", revoke=True, session_factory=TestingSessionLocal)

        assert code == EXIT_OK
        assert await _subscription(user_id) is None
        assert await _plan(user_id) == PlanCode.free

    @pytest.mark.asyncio
    async def test_revoke_keeps_store_subscription(self, capsys: pytest.CaptureFixture[str]):
        """Подписка из RevenueCat (не `manual`) отзывом не удаляется."""
        user_id = await _create_user("paid@example.com")
        async with TestingSessionLocal() as session:
            session.add(
                Subscription(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=10),
                    will_renew=True,
                    is_trial=False,
                    product_id="pro_monthly",
                    store="PLAY_STORE",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        code = await grant_pro("paid@example.com", revoke=True, session_factory=TestingSessionLocal)

        assert code == EXIT_INVALID
        assert "нет Pro, выданного вручную" in capsys.readouterr().out
        assert (await _subscription(user_id)).store == "PLAY_STORE"

    def test_cli_passes_arguments_and_exit_code(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "argv", ["grant_pro.py", "--email", "a@b.com", "--until", "2027-01-01"])
        with (
            patch("scripts.grant_pro._run", new=MagicMock()) as mock_run,
            patch("scripts.grant_pro.asyncio.run", return_value=EXIT_OK) as mock_asyncio_run,
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
        assert exc_info.value.code == EXIT_OK
        mock_run.assert_called_once_with("a@b.com", "2027-01-01", False, False)
        mock_asyncio_run.assert_called_once()

    def test_cli_until_and_revoke_are_exclusive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            sys,
            "argv",
            ["grant_pro.py", "--email", "a@b.com", "--until", "2027-01-01", "--revoke"],
        )
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 2
