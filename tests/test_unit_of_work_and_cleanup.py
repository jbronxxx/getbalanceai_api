"""Тесты для контекстного менеджера Unit of Work и фоновой регламентной очистки токенов."""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import UnitOfWork, get_db
from app.models.models import Category, Token, Transaction, TransactionType, User
from app.redis import REDIS_KEY_TOKEN_CLEANUP_LOCK
from app.schemas.schemas import TransactionCreate
from app.services.auth_service import AuthService
from app.services.transaction_service import TransactionService
from app.tasks.token_cleanup import periodic_token_cleanup, run_token_cleanup
from scripts.cleanup_tokens import main as cli_main
from tests.conftest import TestingSessionLocal


class TestUnitOfWork:
    """Модульные тесты для контекстного менеджера Unit of Work и механизма транзакций."""

    @pytest.mark.asyncio
    async def test_uow_commits_on_successful_exit(self):
        """Проверка фиксации (commit) всех изменений при успешном завершении блока UnitOfWork."""
        user_id = uuid.uuid4()
        async with UnitOfWork(session_factory=TestingSessionLocal) as session:
            user = User(
                id=user_id,
                email="uow_commit_test@example.com",
                name="UoW Commit User",
                hashed_password="hashed_pwd",
            )
            session.add(user)

        # Проверяем в новой сессии, что данные сохранились
        async with UnitOfWork(session_factory=TestingSessionLocal) as verify_session:
            res = await verify_session.execute(select(User).filter(User.id == user_id))
            persisted_user = res.scalar_one_or_none()
            assert persisted_user is not None
            assert persisted_user.email == "uow_commit_test@example.com"

    @pytest.mark.asyncio
    async def test_uow_rollbacks_on_exception(self):
        """Проверка отката (rollback) всех изменений при возникновении ошибки внутри UnitOfWork."""
        user_id = uuid.uuid4()

        with pytest.raises(RuntimeError, match="Simulated failure"):
            async with UnitOfWork(session_factory=TestingSessionLocal) as session:
                user = User(
                    id=user_id,
                    email="uow_rollback_test@example.com",
                    name="UoW Rollback User",
                    hashed_password="hashed_pwd",
                )
                session.add(user)
                await session.flush()
                raise RuntimeError("Simulated failure")

        # Проверяем в новой сессии, что данные НЕ сохранились
        async with UnitOfWork(session_factory=TestingSessionLocal) as verify_session:
            res = await verify_session.execute(select(User).filter(User.id == user_id))
            persisted_user = res.scalar_one_or_none()
            assert persisted_user is None

    @pytest.mark.asyncio
    async def test_composite_transaction_atomic_operations(self):
        """Проверка составной операции: добавление нескольких связанных записей в одной транзакции."""
        user_id = uuid.uuid4()
        async with UnitOfWork(session_factory=TestingSessionLocal) as session:
            user = User(
                id=user_id,
                email="atomic_user@example.com",
                name="Atomic User",
                hashed_password="hashed_pwd",
            )
            session.add(user)
            await session.flush()

            tx_service = TransactionService(session)
            tx1 = await tx_service.create(
                user_id=user_id,
                payload=TransactionCreate(
                    amount=Decimal("150.00"),
                    category=Category.food,
                    type=TransactionType.expense,
                    description="Groceries",
                ),
            )
            tx2 = await tx_service.create(
                user_id=user_id,
                payload=TransactionCreate(
                    amount=Decimal("5000.00"),
                    category=Category.salary,
                    type=TransactionType.income,
                    description="Monthly Salary",
                ),
            )
            assert tx1.id is not None
            assert tx2.id is not None

        # Проверяем в новой изолированной сессии
        async with UnitOfWork(session_factory=TestingSessionLocal) as verify_session:
            res = await verify_session.execute(select(Transaction).filter(Transaction.user_id == user_id))
            user_txs = res.scalars().all()
            assert len(user_txs) == 2

    @pytest.mark.asyncio
    async def test_get_db_generator_commit_and_rollback(self, monkeypatch):
        """Проверка генератора get_db: commit при штатном завершении и rollback при исключении."""
        # Проверяем штатное завершение
        db_gen = get_db()
        session = await anext(db_gen)
        assert isinstance(session, AsyncSession)
        try:
            # Имитация завершения запроса
            await anext(db_gen)
        except StopAsyncIteration:
            pass

        # Проверяем ветку rollback при исключении
        db_gen_err = get_db()
        _ = await anext(db_gen_err)
        with pytest.raises(ValueError, match="Request error"):
            await db_gen_err.athrow(ValueError("Request error"))


class TestTokenCleanup:
    """Тесты фоновой регламентной очистки истекших и отозванных токенов."""

    @pytest.mark.asyncio
    async def test_cleanup_expired_tokens_deletes_only_stale_records(self, db_session: AsyncSession, test_user: User):
        """Проверка, что удаляются только токены старше 30 дней, а свежие и активные сохраняются."""
        auth_service = AuthService(db_session)
        now = datetime.now(timezone.utc)
        user_id = test_user.id

        # 1. Активный актуальный токен (не должен удаляться)
        active_token = Token(
            user_id=user_id,
            token_hash="active_token_current",
            expires_at=now + timedelta(days=7),
            created_at=now,
            status="active",
        )
        # 2. Недавно истекший токен (< 30 дней, не должен удаляться по 30-дневному порогу)
        recent_expired_token = Token(
            user_id=user_id,
            token_hash="recent_expired_token",
            expires_at=now - timedelta(days=5),
            created_at=now - timedelta(days=6),
            status="expired",
        )
        # 3. Недавно отозванный токен (< 30 дней, не должен удаляться)
        recent_revoked_token = Token(
            user_id=user_id,
            token_hash="recent_revoked_token",
            expires_at=now + timedelta(days=1),
            created_at=now - timedelta(days=2),
            status="revoked",
        )
        # 4. Старый истекший токен (> 30 дней, ДОЛЖЕН быть удален)
        old_expired_token = Token(
            user_id=user_id,
            token_hash="old_expired_token_45d",
            expires_at=now - timedelta(days=45),
            created_at=now - timedelta(days=46),
            status="expired",
        )
        # 5. Старый отозванный токен (> 30 дней, ДОЛЖЕН быть удален)
        old_revoked_token = Token(
            user_id=user_id,
            token_hash="old_revoked_token_60d",
            expires_at=now - timedelta(days=35),
            created_at=now - timedelta(days=60),
            status="revoked",
        )

        db_session.add_all(
            [
                active_token,
                recent_expired_token,
                recent_revoked_token,
                old_expired_token,
                old_revoked_token,
            ]
        )
        await db_session.commit()

        # Выполняем очистку токенов старше 30 дней
        deleted_count = await auth_service.cleanup_expired_tokens(retention_days=30)
        assert deleted_count == 2

        res = await db_session.execute(select(Token).filter(Token.user_id == user_id))
        remaining_tokens = res.scalars().all()
        remaining_strings = {t.token_hash for t in remaining_tokens}

        assert "active_token_current" in remaining_strings
        assert "recent_expired_token" in remaining_strings
        assert "recent_revoked_token" in remaining_strings
        assert "old_expired_token_45d" not in remaining_strings
        assert "old_revoked_token_60d" not in remaining_strings

    @pytest.mark.asyncio
    async def test_cleanup_custom_retention_period(self, db_session: AsyncSession, test_user: User):
        """Проверка очистки с кастомным retention_days (например, 7 дней)."""
        auth_service = AuthService(db_session)
        now = datetime.now(timezone.utc)

        token_10d = Token(
            user_id=test_user.id,
            token_hash="token_10d_ago",
            expires_at=now - timedelta(days=10),
            created_at=now - timedelta(days=11),
            status="expired",
        )
        token_2d = Token(
            user_id=test_user.id,
            token_hash="token_2d_ago",
            expires_at=now - timedelta(days=2),
            created_at=now - timedelta(days=3),
            status="expired",
        )
        db_session.add_all([token_10d, token_2d])
        await db_session.commit()

        deleted = await auth_service.cleanup_expired_tokens(retention_days=7)
        assert deleted == 1

        res = await db_session.execute(select(Token).filter(Token.token_hash == "token_2d_ago"))
        remaining = res.scalar_one_or_none()
        assert remaining is not None

    @pytest.mark.asyncio
    async def test_run_token_cleanup_task(self, db_session: AsyncSession, test_user: User):
        """Проверка выполнения вспомогательной функции run_token_cleanup с UnitOfWork."""
        now = datetime.now(timezone.utc)
        old_token = Token(
            user_id=test_user.id,
            token_hash="stale_task_token",
            expires_at=now - timedelta(days=40),
            created_at=now - timedelta(days=41),
            status="expired",
        )
        db_session.add(old_token)
        await db_session.commit()

        deleted = await run_token_cleanup(retention_days=30, session_factory=TestingSessionLocal)
        assert deleted >= 1

    @pytest.mark.asyncio
    async def test_periodic_token_cleanup_task_lifecycle(self):
        """Проверка запуска и отмены асинхронной корутины periodic_token_cleanup."""
        cleanup_task = asyncio.create_task(
            periodic_token_cleanup(
                interval_seconds=1,
                retention_days=30,
                session_factory=TestingSessionLocal,
            )
        )
        await asyncio.sleep(0.05)
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        assert cleanup_task.cancelled() or cleanup_task.done()

    def test_cli_cleanup_script_invocation(self, monkeypatch, capsys):
        """Проверка корректной работы CLI-скрипта cleanup_tokens."""
        monkeypatch.setattr(sys, "argv", ["cleanup_tokens.py", "--days", "30"])
        with patch("scripts.cleanup_tokens.run_token_cleanup", return_value=5) as mock_cleanup:
            cli_main()
            mock_cleanup.assert_called_once_with(retention_days=30)
            captured = capsys.readouterr()
            assert "Удалено записей: 5" in captured.out

    def test_database_engine_pool_configuration(self):
        """Проверка параметров пула соединений SQLAlchemy (pre-ping и pool_recycle)."""
        from app.database import engine

        pool = engine.pool
        assert pool._pre_ping is True
        assert pool._recycle == 1800


class TestTokenCleanupSingleRun:
    """Очистка токенов выполняется одним воркером: блокировка в Redis (SET NX EX) на интервал очистки."""

    @staticmethod
    async def _run_workers(count: int, duration: float = 0.1) -> None:
        """Запустить count планировщиков, как в lifespan воркеров gunicorn, и остановить их через duration."""
        tasks = [
            asyncio.create_task(
                periodic_token_cleanup(
                    interval_seconds=60,
                    session_factory=TestingSessionLocal,
                    check_interval_seconds=0.01,
                )
            )
            for _ in range(count)
        ]
        await asyncio.sleep(duration)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_free_lock_runs_cleanup_and_sets_lock(self, mock_redis_client: fakeredis.FakeAsyncRedis):
        """При свободной блокировке очистка выполняется один раз, блокировка ставится на интервал."""
        with patch("app.tasks.token_cleanup.run_token_cleanup", AsyncMock(return_value=0)) as mock_cleanup:
            await self._run_workers(1)

        mock_cleanup.assert_awaited_once_with(retention_days=30, session_factory=TestingSessionLocal)
        assert 0 < await mock_redis_client.ttl(REDIS_KEY_TOKEN_CLEANUP_LOCK) <= 60

    @pytest.mark.asyncio
    async def test_busy_lock_skips_cleanup(self, mock_redis_client: fakeredis.FakeAsyncRedis):
        """Если блокировку держит другой воркер, очистка не выполняется."""
        await mock_redis_client.set(REDIS_KEY_TOKEN_CLEANUP_LOCK, "other-worker", ex=60)

        with patch("app.tasks.token_cleanup.run_token_cleanup", AsyncMock(return_value=0)) as mock_cleanup:
            await self._run_workers(1)

        mock_cleanup.assert_not_awaited()
        assert await mock_redis_client.get(REDIS_KEY_TOKEN_CLEANUP_LOCK) == "other-worker"

    @pytest.mark.asyncio
    async def test_concurrent_workers_run_cleanup_once(self):
        """Из четырёх одновременно проверяющих воркеров очистку выполняет ровно один."""
        with patch("app.tasks.token_cleanup.run_token_cleanup", AsyncMock(return_value=0)) as mock_cleanup:
            await self._run_workers(4)

        assert mock_cleanup.await_count == 1

    @pytest.mark.asyncio
    async def test_redis_unavailable_skips_cleanup(self, mock_redis_client: fakeredis.FakeAsyncRedis):
        """Без Redis блокировку не проверить: очистка пропускается, а не выполняется всеми воркерами."""
        with (
            patch.object(mock_redis_client, "set", AsyncMock(side_effect=RedisConnectionError("Connection refused"))),
            patch("app.tasks.token_cleanup.run_token_cleanup", AsyncMock(return_value=0)) as mock_cleanup,
        ):
            await self._run_workers(1)

        mock_cleanup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_cleanup_releases_lock(self):
        """Сбой очистки снимает блокировку: следующая проверка повторяет очистку, а не ждёт интервал."""
        failing_cleanup = AsyncMock(side_effect=RuntimeError("database is unavailable"))
        with patch("app.tasks.token_cleanup.run_token_cleanup", failing_cleanup):
            await self._run_workers(1)

        assert failing_cleanup.await_count >= 2
