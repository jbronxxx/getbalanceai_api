"""Модуль подключения к базе данных PostgreSQL и управления сессиями SQLAlchemy."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config_reader.config_reader import config

# Убедимся, что драйвер asyncpg используется в URL
db_url = config.db_url.replace("postgresql://", "postgresql+asyncpg://")

# Базовые параметры пула соединений для устойчивости к обрывам
engine_kwargs: dict[str, object] = {
    "pool_pre_ping": config.db_pool_pre_ping,
    "pool_recycle": config.db_pool_recycle,
}

if "sqlite" not in db_url:
    engine_kwargs.update(
        {
            "pool_size": config.db_pool_size,
            "max_overflow": config.db_max_overflow,
            "pool_timeout": config.db_pool_timeout,
        }
    )

# Создание асинхронного движка SQLAlchemy
engine = create_async_engine(db_url, **engine_kwargs)

# Фабрика асинхронных сессий
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    """Базовый декларативный класс для всех ORM-моделей приложения."""

    pass


class UnitOfWork:
    """Контекстный менеджер Unit of Work для управления транзакциями базы данных."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = async_session_maker):
        """Инициализация Unit of Work с фабрикой сессий SQLAlchemy."""
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        """Открыть сессию и войти в контекст транзакции."""
        self.session = self._session_factory()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Завершить транзакцию: commit при успехе, rollback при исключении."""
        if self.session is None:
            return False

        try:
            if exc_type is not None:
                await self.session.rollback()
            else:
                await self.session.commit()
        finally:
            await self.session.close()
        return False


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Генератор асинхронных сессий базы данных (FastAPI Dependency)."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
