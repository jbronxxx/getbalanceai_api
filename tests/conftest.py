"""Модуль общих фикстур pytest для изолированного тестирования приложения."""

import uuid
from unittest.mock import patch

import fakeredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models.models import User
from app.services.auth_service import AuthService
from config_reader.config_reader import config
from main import app

# Создаем in-memory базу SQLite с пулом StaticPool

SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    """Пересоздание всех таблиц перед каждым тестом для полной изоляции."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
def reset_jwks_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Клиенты JWKS кэшируются на уровне класса; сбрасываем, чтобы мок из одного теста не попал в другой."""
    monkeypatch.setattr(AuthService, "_google_jwks_client", None)
    monkeypatch.setattr(AuthService, "_apple_jwks_client", None)


@pytest.fixture(autouse=True)
def oauth_client_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client ID Google/Apple, под которые выпускаются тестовые ID-токены провайдеров."""
    monkeypatch.setattr(config, "google_client_ids", ["test-google-android", "test-google-ios"])
    monkeypatch.setattr(config, "apple_client_ids", ["test.apple.bundle"])


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Фикстура сессии базы данных для каждого теста."""
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    """Фикстура TestClient с переопределенной сессией базы данных."""

    async def _override_get_db():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Фикстура созданного тестового пользователя в базе данных."""
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"user_{user_id.hex[:8]}@example.com",
        name="Test User",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_token(db_session: AsyncSession, test_user: User) -> str:
    """Фикстура активного JWT access-токена для тестового пользователя."""
    auth_service = AuthService(db_session)
    return auth_service.create_access_token(test_user.id)


@pytest_asyncio.fixture
async def auth_headers(auth_token: str) -> dict[str, str]:
    """Фикстура HTTP-заголовков с Bearer-токеном авторизации."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(autouse=True)
def mock_redis_client():
    """Фикстура для подмены redis_client в сервисах на FakeAsyncRedis."""
    fake_redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    with (
        patch("app.redis.redis_client", fake_redis),
        patch("app.redis._redis_client", fake_redis),
        patch("app.services.ai_service.redis_client", fake_redis),
        patch("app.services.currency_service.redis_client", fake_redis),
    ):
        yield fake_redis
