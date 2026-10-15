"""Тесты административного CLI-скрипта удаления пользователя по email."""

import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.models import (
    Budget,
    Category,
    Token,
    Transaction,
    TransactionType,
    User,
)
from app.redis import get_budgets_version_key, get_redis
from scripts.delete_user_by_email import (
    EXIT_AMBIGUOUS,
    EXIT_CANCELLED,
    EXIT_DELETED,
    EXIT_NOT_FOUND,
    delete_user_by_email,
)
from scripts.delete_user_by_email import main as cli_main
from tests.conftest import TestingSessionLocal


async def _create_user(email: str, with_data: bool = False) -> uuid.UUID:
    """Создать пользователя (опционально с транзакцией, бюджетом и токеном) в тестовой БД."""
    user_id = uuid.uuid4()
    async with TestingSessionLocal() as session:
        session.add(User(id=user_id, email=email, name="CLI User", hashed_password="hashed_pwd"))
        await session.flush()
        if with_data:
            session.add(
                Transaction(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    amount=Decimal("10.00"),
                    description="Обед",
                    type=TransactionType.expense,
                    category=Category.food,
                    date=datetime.now(timezone.utc),
                )
            )
            session.add(
                Budget(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    category=Category.food,
                    limit_amount=Decimal("100.00"),
                    month=datetime.now(timezone.utc).month,
                    year=datetime.now(timezone.utc).year,
                )
            )
            session.add(
                Token(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    token_hash=uuid.uuid4().hex,
                    expires_at=datetime.now(timezone.utc),
                )
            )
        await session.commit()
    return user_id


async def _count(model, user_id: uuid.UUID) -> int:
    """Количество записей модели, принадлежащих пользователю."""
    column = model.id if model is User else model.user_id
    async with TestingSessionLocal() as session:
        result = await session.execute(select(model).where(column == user_id))
        return len(result.scalars().all())


class TestDeleteUserByEmail:
    """Удаление аккаунта по email: те же шаги, что и в DELETE /auth/me."""

    @pytest.mark.asyncio
    async def test_deletes_user_case_insensitive_with_cascade_and_redis(self):
        """Email ищется без учёта регистра; удаляются каскадные данные и ключи Redis."""
        user_id = await _create_user("Mixed.Case@Example.com", with_data=True)
        other_id = await _create_user("other@example.com", with_data=True)
        redis = get_redis()
        await redis.set(get_budgets_version_key(user_id), "5")
        await redis.set(f"insights_cache:{user_id}:ru:USD", "cached")
        await redis.set(get_budgets_version_key(other_id), "7")

        code = await delete_user_by_email("  mixed.case@EXAMPLE.com ", assume_yes=True, session_factory=TestingSessionLocal)

        assert code == EXIT_DELETED
        assert await _count(User, user_id) == 0
        for model in (Transaction, Budget, Token):
            assert await _count(model, user_id) == 0
        assert await redis.get(get_budgets_version_key(user_id)) is None
        assert await redis.get(f"insights_cache:{user_id}:ru:USD") is None
        # Данные другого пользователя не затронуты
        assert await _count(User, other_id) == 1
        assert await _count(Transaction, other_id) == 1
        assert await redis.get(get_budgets_version_key(other_id)) == "7"

    @pytest.mark.asyncio
    async def test_not_found_returns_code_and_deletes_nothing(self, capsys: pytest.CaptureFixture[str]):
        """Несуществующий email: код 1, другие пользователи не затронуты."""
        existing_id = await _create_user("exists@example.com")

        code = await delete_user_by_email("missing@example.com", assume_yes=True, session_factory=TestingSessionLocal)

        assert code == EXIT_NOT_FOUND
        assert "не найден" in capsys.readouterr().out
        assert await _count(User, existing_id) == 1

    @pytest.mark.asyncio
    async def test_ambiguous_email_is_not_deleted(self):
        """Несколько пользователей с email, различающимся регистром: код 2, ничего не удалено."""
        first_id = await _create_user("dup@example.com")
        second_id = await _create_user("DUP@example.com")

        code = await delete_user_by_email("dup@example.com", assume_yes=True, session_factory=TestingSessionLocal)

        assert code == EXIT_AMBIGUOUS
        assert await _count(User, first_id) == 1
        assert await _count(User, second_id) == 1

    @pytest.mark.asyncio
    async def test_wrong_confirmation_cancels_deletion(self):
        """Неверное подтверждение в консоли отменяет удаление."""
        user_id = await _create_user("confirm@example.com")

        with patch("builtins.input", return_value="another@example.com"):
            code = await delete_user_by_email("confirm@example.com", session_factory=TestingSessionLocal)

        assert code == EXIT_CANCELLED
        assert await _count(User, user_id) == 1

    @pytest.mark.asyncio
    async def test_correct_confirmation_deletes_user(self):
        """Повторный ввод email (в любом регистре) подтверждает удаление."""
        user_id = await _create_user("confirm2@example.com")

        with patch("builtins.input", return_value="CONFIRM2@example.com"):
            code = await delete_user_by_email("confirm2@example.com", session_factory=TestingSessionLocal)

        assert code == EXIT_DELETED
        assert await _count(User, user_id) == 0

    def test_cli_passes_arguments_and_exit_code(self, monkeypatch: pytest.MonkeyPatch):
        """CLI передаёт --email и --yes и завершается с кодом выполнения."""
        monkeypatch.setattr(sys, "argv", ["delete_user_by_email.py", "--email", "a@b.com", "--yes"])
        with (
            patch("scripts.delete_user_by_email._run", new=MagicMock()) as mock_run,
            patch("scripts.delete_user_by_email.asyncio.run", return_value=EXIT_NOT_FOUND) as mock_asyncio_run,
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()
        assert exc_info.value.code == EXIT_NOT_FOUND
        mock_run.assert_called_once_with("a@b.com", True)
        mock_asyncio_run.assert_called_once()

    def test_cli_requires_email(self, monkeypatch: pytest.MonkeyPatch):
        """Без --email argparse завершает работу с кодом 2."""
        monkeypatch.setattr(sys, "argv", ["delete_user_by_email.py"])
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 2
