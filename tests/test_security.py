"""Тесты безопасности приложения (Security Enhancements).

Проверяет:
1. TASK-4.1: Настройку CORSMiddleware и обработку preflight/CORS-заголовков.
2. TASK-4.2: Ограничение частоты запросов (Rate Limiting) на эндпоинтах авторизации и AI-инсайтов.
3. TASK-4.3: Валидацию паролей на длину (до 72 байт) и сложность (наличие букв и цифр).
4. TASK-4.4: Запрет использования дефолтных секретных ключей при отключенном режиме отладки (production).
"""

import pytest
from httpx import AsyncClient

from app.exceptions import ErrorCode
from app.limiter import limiter
from app.schemas.schemas import UserLogin, UserRegister
from config_reader.config_reader import Settings


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Сбрасывает внутреннее состояние rate limiter перед каждым тестом."""
    limiter.reset()
    yield
    limiter.reset()


class TestCorsMiddleware:
    """Тестирование работы CORSMiddleware."""

    @pytest.mark.asyncio
    async def test_cors_headers_present_on_get_request(self, client: AsyncClient):
        """Проверяет наличие CORS-заголовков в ответе на обычный GET запрос."""
        response = await client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert response.headers["access-control-allow-origin"] in (
            "*",
            "http://localhost:3000",
        )

    @pytest.mark.asyncio
    async def test_cors_preflight_options_request(self, client: AsyncClient):
        """Проверяет корректный ответ сервера на предварительный preflight (OPTIONS) запрос."""
        response = await client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,Authorization,X-Request-ID",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert response.headers["access-control-allow-origin"] in (
            "*",
            "http://localhost:3000",
        )
        assert "access-control-allow-methods" in response.headers


class TestPasswordValidation:
    """Тестирование правил валидации и сложности паролей."""

    @pytest.mark.asyncio
    async def test_register_password_without_numbers_rejected(self):
        """Проверяет отклонение пароля без цифр."""
        with pytest.raises(
            ValueError,
            match="Пароль должен содержать как минимум одну букву и одну цифру",
        ):
            UserRegister(
                email="user@example.com",
                password="OnlyLettersPassword",
                name="Test User",
            )

    @pytest.mark.asyncio
    async def test_register_password_without_letters_rejected(self):
        """Проверяет отклонение пароля без букв."""
        with pytest.raises(
            ValueError,
            match="Пароль должен содержать как минимум одну букву и одну цифру",
        ):
            UserRegister(
                email="user@example.com",
                password="1234567890",
                name="Test User",
            )

    @pytest.mark.asyncio
    async def test_register_password_too_short_rejected(self):
        """Проверяет отклонение пароля короче 6 символов."""
        with pytest.raises(ValueError):
            UserRegister(
                email="user@example.com",
                password="A1b",
                name="Test User",
            )

    @pytest.mark.asyncio
    async def test_register_password_exceeding_72_bytes_rejected(self):
        """Проверяет отклонение пароля длиннее 72 байт (ограничение bcrypt)."""
        long_password = "A1" + "a" * 75
        with pytest.raises(ValueError):
            UserRegister(
                email="user@example.com",
                password=long_password,
                name="Test User",
            )

    @pytest.mark.asyncio
    async def test_register_valid_password_accepted(self):
        """Проверяет успешное создание схемы с корректным паролем."""
        payload = UserRegister(
            email="user@example.com",
            password="ValidPass123",
            name="Test User",
        )
        assert payload.password == "ValidPass123"

    @pytest.mark.asyncio
    async def test_login_password_exceeding_72_chars_rejected(self):
        """Проверяет ограничение максимальной длины пароля в схеме входа."""
        long_password = "A" * 75
        with pytest.raises(ValueError):
            UserLogin(
                email="user@example.com",
                password=long_password,
            )


class TestRateLimiting:
    """Тестирование ограничения частоты запросов (Rate Limiting)."""

    @pytest.mark.asyncio
    async def test_login_rate_limiting_exceeded(self, client: AsyncClient):
        """Проверяет блокировку частых попыток входа с одного IP (лимит 5/мин)."""
        login_data = {"email": "attacker@example.com", "password": "WrongPassword123"}

        # Выполняем 5 разрешенных попыток
        for _ in range(5):
            res = await client.post("/api/v1/auth/login", json=login_data)
            assert res.status_code in (401, 200)

        # 6-я попытка должна вернуть 429 Too Many Requests
        response = await client.post("/api/v1/auth/login", json=login_data)
        assert response.status_code == 429
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.RATE_LIMIT_EXCEEDED
        assert "лимит" in data["message"].lower() or "limit" in data["message"].lower()

    @pytest.mark.asyncio
    async def test_register_rate_limiting_exceeded(self, client: AsyncClient):
        """Проверяет ограничение частоты запросов на регистрацию (лимит 5/мин)."""
        # Выполняем 5 запросов
        for i in range(5):
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"ratelimit{i}@example.com",
                    "password": "Password123",
                    "name": f"User {i}",
                },
            )

        # 6-й запрос превышает лимит
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "ratelimit_over@example.com",
                "password": "Password123",
                "name": "Over Limit",
            },
        )
        assert response.status_code == 429
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.RATE_LIMIT_EXCEEDED


class TestDefaultSecretsValidation:
    """Тестирование проверки дефолтных секретов в продакшне."""

    @pytest.mark.asyncio
    async def test_production_mode_with_default_secret_raises_error(self, monkeypatch):
        """Проверяет выброс исключения в продакшне (debug=False) с дефолтным SECRET_KEY."""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("APP_DEBUG", raising=False)
        with pytest.raises(ValueError, match="Недопустимо использовать значение по умолчанию"):
            Settings(
                _env_file=None,
                debug=False,
                host="0.0.0.0",
                port=8000,
                db_url="postgresql://user:pass@localhost:5432/db",
                secret_key="change-me-in-production-use-long-random-string",
                algorithm="HS256",
                access_token_expire_minutes=60,
                refresh_token_expire_days=7,
                cors_origins=["*"],
                cors_allow_credentials=True,
                cors_allow_methods=["*"],
                cors_allow_headers=["*"],
                anthropic_api_key="",
                ai_model="claude-haiku-4-5",
                redis_url="redis://localhost:6379/0",
            )

    @pytest.mark.asyncio
    async def test_production_mode_with_empty_secret_raises_error(self, monkeypatch):
        """Проверяет выброс исключения в продакшне (debug=False) с пустым SECRET_KEY."""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("APP_DEBUG", raising=False)
        with pytest.raises(ValueError, match="Недопустимо использовать значение по умолчанию"):
            Settings(
                _env_file=None,
                debug=False,
                host="0.0.0.0",
                port=8000,
                db_url="postgresql://user:pass@localhost:5432/db",
                secret_key="",
                algorithm="HS256",
                access_token_expire_minutes=60,
                refresh_token_expire_days=7,
                cors_origins=["*"],
                cors_allow_credentials=True,
                cors_allow_methods=["*"],
                cors_allow_headers=["*"],
                anthropic_api_key="",
                ai_model="claude-haiku-4-5",
                redis_url="redis://localhost:6379/0",
            )

    @pytest.mark.asyncio
    async def test_production_mode_with_secure_secret_passes(self, monkeypatch):
        """Проверяет успешное прохождение валидации при наличии безопасного SECRET_KEY."""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("APP_DEBUG", raising=False)
        Settings(
            _env_file=None,
            debug=False,
            host="0.0.0.0",
            port=8000,
            db_url="postgresql://user:pass@localhost:5432/db",
            secret_key="super-secret-production-random-key-1234567890",
            algorithm="HS256",
            access_token_expire_minutes=60,
            refresh_token_expire_days=7,
            cors_origins=["*"],
            cors_allow_credentials=True,
            cors_allow_methods=["*"],
            cors_allow_headers=["*"],
            anthropic_api_key="",
            ai_model="claude-haiku-4-5",
            redis_url="redis://localhost:6379/0",
        )

    @pytest.mark.asyncio
    async def test_debug_mode_with_default_secret_allowed(self, monkeypatch):
        """Проверяет допустимость дефолтного ключа в режиме отладки (debug=True)."""
        monkeypatch.delenv("SECRET_KEY", raising=False)
        monkeypatch.delenv("APP_DEBUG", raising=False)
        Settings(
            _env_file=None,
            debug=True,
            host="0.0.0.0",
            port=8000,
            db_url="postgresql://user:pass@localhost:5432/db",
            secret_key="change-me-in-production-use-long-random-string",
            algorithm="HS256",
            access_token_expire_minutes=60,
            refresh_token_expire_days=7,
            cors_origins=["*"],
            cors_allow_credentials=True,
            cors_allow_methods=["*"],
            cors_allow_headers=["*"],
            anthropic_api_key="",
            ai_model="claude-haiku-4-5",
            redis_url="redis://localhost:6379/0",
        )

    def test_assemble_redis_url_from_redis_host(self, monkeypatch):
        """Проверяет сборку redis_url из REDIS_HOST и REDIS_PORT при отсутствии REDIS_URL."""
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("REDIS_HOST", "redis-custom")
        monkeypatch.setenv("REDIS_PORT", "6380")
        settings = Settings(_env_file=None)
        assert settings.redis_url == "redis://redis-custom:6380/0"
