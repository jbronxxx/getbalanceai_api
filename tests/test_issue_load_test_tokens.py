"""Тесты CLI-скрипта подготовки нагрузочного теста: сид пользователей с историей, выдача токенов и очистка."""

import json
import random
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.models import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    SUPPORTED_CURRENCIES,
    Budget,
    Category,
    Token,
    Transaction,
    TransactionType,
    User,
)
from app.redis import get_budgets_version_key, get_redis
from scripts.issue_load_test_tokens import (
    BACKGROUND_HISTORY_BUCKETS,
    EXIT_ALREADY_SEEDED,
    EXIT_OK,
    background_history_size,
    cleanup_load_test_users,
    generate_budgets,
    generate_transactions,
    issue_load_test_tokens,
    load_test_email,
)
from scripts.issue_load_test_tokens import main as cli_main
from tests.conftest import TestingSessionLocal


async def _count(model: type, *criteria) -> int:
    """Количество записей модели, подходящих под условия."""
    async with TestingSessionLocal() as session:
        query = select(func.count()).select_from(model)
        if criteria:
            query = query.where(*criteria)
        return (await session.execute(query)).scalar_one()


async def _create_real_user() -> uuid.UUID:
    """Создать обычного пользователя с транзакцией: очистка не должна его затронуть."""
    user_id = uuid.uuid4()
    async with TestingSessionLocal() as session:
        session.add(User(id=user_id, email="real.user@example.com", name="Real User"))
        await session.flush()
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
        await session.commit()
    return user_id


async def _issue(tmp_path: Path, users: int = 3, transactions: int = 20, background_users: int = 2) -> tuple[int, Path]:
    """Выполнить issue на тестовой БД с фиксированным seed."""
    out_path = tmp_path / "tokens.json"
    code = await issue_load_test_tokens(
        users=users,
        transactions_per_user=transactions,
        background_users=background_users,
        out_path=out_path,
        session_factory=TestingSessionLocal,
        rng=random.Random(42),
    )
    return code, out_path


class TestIssueLoadTestTokens:
    """Команда issue: пользователи с историей, бюджетами и токенами."""

    @pytest.mark.asyncio
    async def test_creates_users_history_and_token_file(self, tmp_path: Path):
        """Создаются пользователи с токенами и фоновые; у каждого история и бюджеты, токены — в JSON."""
        code, out_path = await _issue(tmp_path, users=3, transactions=20, background_users=2)

        assert code == EXIT_OK
        token_users = await _count(User, User.email.like("loadtest+u-%"))
        background_users = await _count(User, User.email.like("loadtest+bg-%"))
        assert (token_users, background_users) == (3, 2)
        assert await _count(Token) == 3

        document = json.loads(out_path.read_text(encoding="utf-8"))
        assert [item["email"] for item in document["users"]] == [load_test_email(i) for i in (1, 2, 3)]
        assert datetime.fromisoformat(document["access_expires_at"]) > datetime.fromisoformat(document["issued_at"])

        async with TestingSessionLocal() as session:
            for item in document["users"]:
                user_id = uuid.UUID(item["user_id"])
                tx_count = await session.scalar(select(func.count()).where(Transaction.user_id == user_id))
                budget_count = await session.scalar(select(func.count()).where(Budget.user_id == user_id))
                assert tx_count == 20
                assert budget_count > 0

        bg_min = min(bucket[1] for bucket in BACKGROUND_HISTORY_BUCKETS)
        assert await _count(Transaction) >= 3 * 20 + 2 * bg_min

    @pytest.mark.asyncio
    async def test_token_file_is_owner_only(self, tmp_path: Path):
        """Файл с токенами даёт доступ к аккаунтам: права только у владельца."""
        _, out_path = await _issue(tmp_path, users=1, transactions=1, background_users=0)

        assert stat.S_IMODE(out_path.stat().st_mode) == 0o600

    @pytest.mark.asyncio
    async def test_issued_access_token_authenticates(self, tmp_path: Path, client: AsyncClient):
        """Выданный access-токен принимается API и принадлежит тестовому пользователю."""
        _, out_path = await _issue(tmp_path, users=1, transactions=5, background_users=0)
        item = json.loads(out_path.read_text(encoding="utf-8"))["users"][0]

        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {item['access_token']}"})

        assert response.status_code == 200
        assert response.json()["data"]["email"] == item["email"]

    @pytest.mark.asyncio
    async def test_refuses_when_test_users_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """Повторный issue без cleanup отказывает и ничего не создаёт."""
        await _issue(tmp_path, users=2, transactions=3, background_users=0)
        users_before = await _count(User)

        code, _ = await _issue(tmp_path, users=2, transactions=3, background_users=0)

        assert code == EXIT_ALREADY_SEEDED
        assert "cleanup" in capsys.readouterr().out
        assert await _count(User) == users_before


class TestGeneratedData:
    """Генераторы данных сида: значения проходят те же правила, что и данные из API."""

    def test_transactions_are_consistent(self):
        """Категория соответствует типу, валюта поддерживается, base_amount = amount × курс, дата — за последний год."""
        now = datetime.now(timezone.utc)
        rows = list(generate_transactions(random.Random(1), uuid.uuid4(), "KZT", 500, now))

        assert len(rows) == 500
        for row in rows:
            allowed = INCOME_CATEGORIES if row["type"] == TransactionType.income else EXPENSE_CATEGORIES
            assert row["category"] in allowed
            assert row["currency"] in SUPPORTED_CURRENCIES
            assert row["amount"] > 0
            assert abs(row["base_amount"] - row["amount"] * row["exchange_rate"]) <= Decimal("0.01")
            assert now - timedelta(days=366) <= row["date"] <= now
            if row["currency"] == "KZT":
                assert row["exchange_rate"] == Decimal("1")
        assert {row["type"] for row in rows} == {TransactionType.income, TransactionType.expense}

    def test_budgets_cover_year_without_duplicates(self):
        """Бюджеты — только расходные категории, по 12 месяцам, без нарушения уникальности периода."""
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        rows = generate_budgets(random.Random(2), uuid.uuid4(), "USD", now)

        keys = [(row["category"], row["month"], row["year"]) for row in rows]
        assert len(keys) == len(set(keys))
        assert all(row["category"] in EXPENSE_CATEGORIES for row in rows)
        assert {(row["month"], row["year"]) for row in rows} == {(2, 2026), (1, 2026)} | {(m, 2025) for m in range(3, 13)}

    def test_background_history_size_within_buckets(self):
        """Размер истории фонового пользователя лежит в границах распределения."""
        rng = random.Random(3)
        low = min(bucket[1] for bucket in BACKGROUND_HISTORY_BUCKETS)
        high = max(bucket[2] for bucket in BACKGROUND_HISTORY_BUCKETS)

        assert all(low <= background_history_size(rng) <= high for _ in range(200))


class TestCleanupLoadTestUsers:
    """Команда cleanup: удаляет только тестовых пользователей и их данные."""

    @pytest.mark.asyncio
    async def test_removes_test_users_with_cascade_and_redis(self, tmp_path: Path):
        """Тестовые пользователи, их транзакции, бюджеты, токены и ключи Redis удаляются; реальные — нет."""
        real_user_id = await _create_real_user()
        _, out_path = await _issue(tmp_path, users=2, transactions=5, background_users=2)
        test_user_id = json.loads(out_path.read_text(encoding="utf-8"))["users"][0]["user_id"]
        redis = get_redis()
        await redis.set(get_budgets_version_key(test_user_id), "1")
        await redis.set(f"insights_cache:{test_user_id}:ru:USD", "cached")
        await redis.set(get_budgets_version_key(real_user_id), "7")

        deleted = await cleanup_load_test_users(session_factory=TestingSessionLocal)

        assert deleted == 4
        assert await _count(User, User.email.like("loadtest+%")) == 0
        for model in (Transaction, Budget, Token):
            assert await _count(model, model.user_id != real_user_id) == 0
        assert await redis.get(get_budgets_version_key(test_user_id)) is None
        assert await redis.get(f"insights_cache:{test_user_id}:ru:USD") is None
        assert await _count(User, User.id == real_user_id) == 1
        assert await _count(Transaction, Transaction.user_id == real_user_id) == 1
        assert await redis.get(get_budgets_version_key(real_user_id)) == "7"

    @pytest.mark.asyncio
    async def test_cleanup_without_test_users_is_noop(self):
        """Без тестовых пользователей cleanup ничего не удаляет."""
        real_user_id = await _create_real_user()

        assert await cleanup_load_test_users(session_factory=TestingSessionLocal) == 0
        assert await _count(User, User.id == real_user_id) == 1


class TestCli:
    """Разбор аргументов командной строки."""

    def test_issue_arguments_are_passed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """issue передаёт параметры и завершается с кодом выполнения."""
        out = tmp_path / "t.json"
        argv = ["x.py", "issue", "--users", "10", "--background-users", "5", "--out", str(out), "--random-seed", "7"]
        monkeypatch.setattr(sys, "argv", argv)
        with (
            patch("scripts.issue_load_test_tokens._run", new=MagicMock()) as mock_run,
            patch("scripts.issue_load_test_tokens.asyncio.run", return_value=EXIT_OK),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()

        assert exc_info.value.code == EXIT_OK
        args = mock_run.call_args.args[0]
        assert (args.command, args.users, args.transactions, args.background_users) == ("issue", 10, 1000, 5)
        assert (args.out, args.random_seed) == (out, 7)

    @pytest.mark.parametrize(
        "argv",
        [
            ["x.py"],
            ["x.py", "issue"],
            ["x.py", "issue", "--out", "t.json", "--users", "-1"],
        ],
    )
    def test_invalid_arguments_exit_with_usage_error(self, monkeypatch: pytest.MonkeyPatch, argv: list[str]):
        """Без команды, без --out или с отрицательным числом argparse завершает работу с кодом 2."""
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc_info:
            cli_main()
        assert exc_info.value.code == 2
