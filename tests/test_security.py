"""Тесты безопасности приложения (Security Enhancements).

Проверяет:
1. TASK-4.1: Настройку CORSMiddleware и обработку preflight/CORS-заголовков.
2. TASK-4.2: Ограничение частоты запросов (Rate Limiting) на эндпоинтах авторизации и AI-инсайтов.
3. TASK-4.3: Валидацию паролей на длину (до 72 байт) и сложность (наличие букв и цифр).
4. TASK-4.4: Запрет использования дефолтных секретных ключей при отключенном режиме отладки (production).
5. Обязательные OAuth client ID и запрет CORS * с credentials в production.
6. Ключ rate limiting: sub только из подписанного access-токена, IP клиента из X-Forwarded-For от прокси.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.exceptions import ErrorCode
from app.limiter import get_user_id_or_ip, limiter
from app.schemas.schemas import UserLogin, UserRegister
from config_reader.config_reader import Settings, config
from main import app

PRODUCTION_SECRET = "super-secret-production-random-key-1234567890"


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

    @pytest.mark.asyncio
    async def test_patch_me_rate_limiting_exceeded(self, client: AsyncClient, auth_headers: dict[str, str]):
        """Проверяет ограничение частоты запросов на обновление профиля (лимит 10/мин)."""
        # Выполняем 10 разрешенных запросов
        for i in range(10):
            res = await client.patch(
                "/api/v1/auth/me",
                json={"name": f"Name {i}"},
                headers=auth_headers,
            )
            assert res.status_code == 200

        # 11-й запрос превышает лимит
        response = await client.patch(
            "/api/v1/auth/me",
            json={"name": "Over Limit Name"},
            headers=auth_headers,
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
            cors_allow_credentials=False,
            google_client_ids=["google-client-id"],
            apple_client_ids=["apple.bundle.id"],
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
        settings = Settings(_env_file=None, debug=True)
        assert settings.redis_url == "redis://redis-custom:6380/0"

    def test_db_pool_settings_defaults_and_env(self, monkeypatch):
        """Проверяет значения по умолчанию и переопределение через env для параметров пула БД."""
        settings = Settings(_env_file=None, debug=True)
        assert settings.db_pool_size == 10
        assert settings.db_max_overflow == 20
        assert settings.db_pool_timeout == 30
        assert settings.db_pool_recycle == 1800
        assert settings.db_pool_pre_ping is True

        monkeypatch.setenv("DB_POOL_SIZE", "15")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "30")
        monkeypatch.setenv("DB_POOL_TIMEOUT", "45")
        monkeypatch.setenv("DB_POOL_RECYCLE", "900")
        monkeypatch.setenv("DB_POOL_PRE_PING", "false")
        custom_settings = Settings(_env_file=None, debug=True)
        assert custom_settings.db_pool_size == 15
        assert custom_settings.db_max_overflow == 30
        assert custom_settings.db_pool_timeout == 45
        assert custom_settings.db_pool_recycle == 900
        assert custom_settings.db_pool_pre_ping is False


class TestProductionSettingsValidation:
    """Проверка безопасных значений по умолчанию и обязательных настроек production."""

    @pytest.fixture(autouse=True)
    def clear_security_env(self, monkeypatch):
        """Переменные окружения по alias перекрывают аргументы Settings, поэтому убираем их (в CI задан APP_DEBUG)."""
        for name in (
            "APP_DEBUG",
            "SECRET_KEY",
            "GOOGLE_CLIENT_ID",
            "APPLE_CLIENT_ID",
            "CORS_ORIGINS",
            "CORS_ALLOW_CREDENTIALS",
        ):
            monkeypatch.delenv(name, raising=False)

    @staticmethod
    def _production_settings(**overrides) -> Settings:
        values = {
            "debug": False,
            "secret_key": PRODUCTION_SECRET,
            "google_client_ids": ["google-client-id"],
            "apple_client_ids": ["apple.bundle.id"],
            "cors_origins": ["https://getbalanceai.app"],
            "cors_allow_credentials": True,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_debug_and_cors_credentials_disabled_by_default(self):
        """Без APP_DEBUG приложение стартует в production режиме, CORS credentials выключены."""
        settings = Settings(_env_file=None, secret_key=PRODUCTION_SECRET, google_client_ids=["g"], apple_client_ids=["a"])
        assert settings.debug is False
        assert settings.cors_allow_credentials is False

    def test_production_without_secret_key_env_fails_by_default(self):
        """Без APP_DEBUG и SECRET_KEY старт падает: дефолтный режим — production."""
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(_env_file=None)

    def test_production_requires_google_client_id(self):
        """В production пустой GOOGLE_CLIENT_ID запрещён."""
        with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
            self._production_settings(google_client_ids=[])

    def test_production_allows_empty_apple_client_id(self):
        """APPLE_CLIENT_ID в production необязателен: без него вход через Apple отклоняется."""
        settings = self._production_settings(apple_client_ids=[])
        assert settings.apple_client_ids == []

    def test_debug_allows_empty_oauth_client_ids(self):
        """В режиме отладки client ID можно не задавать."""
        settings = Settings(_env_file=None, debug=True, google_client_ids=[], apple_client_ids=[])
        assert settings.google_client_ids == []

    def test_production_forbids_cors_wildcard_with_credentials(self):
        """В production CORS_ORIGINS=* вместе с credentials запрещён."""
        with pytest.raises(ValueError, match="CORS_ORIGINS"):
            self._production_settings(cors_origins=["*"], cors_allow_credentials=True)

    def test_production_allows_cors_wildcard_without_credentials(self):
        """CORS_ORIGINS=* без credentials допустим: браузер не отправит учётные данные."""
        settings = self._production_settings(cors_origins=["*"], cors_allow_credentials=False)
        assert settings.cors_origins == ["*"]

    def test_production_allows_explicit_origins_with_credentials(self):
        """Явный список доменов с credentials допустим."""
        settings = self._production_settings()
        assert settings.cors_allow_credentials is True

    def test_client_ids_parsed_from_comma_separated_env(self, monkeypatch):
        """Несколько client ID (Android/iOS/Web) задаются в env через запятую."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "android.apps.googleusercontent.com, ios.apps.googleusercontent.com,")
        monkeypatch.setenv("APPLE_CLIENT_ID", "app.getbalanceai")
        settings = Settings(_env_file=None, debug=True)
        assert settings.google_client_ids == ["android.apps.googleusercontent.com", "ios.apps.googleusercontent.com"]
        assert settings.apple_client_ids == ["app.getbalanceai"]

    def test_empty_client_id_env_gives_empty_list(self, monkeypatch):
        """Пустая переменная GOOGLE_CLIENT_ID= из .env.example даёт пустой список, а не ошибку."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
        settings = Settings(_env_file=None, debug=True)
        assert settings.google_client_ids == []


def _make_request(authorization: str | None = None, client_host: str = "203.0.113.7") -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (client_host, 5555)})


def _make_token(sub: str, *, secret: str | None = None, token_type: str = "access", expires_in: int = 60) -> str:
    payload = {
        "sub": sub,
        "type": token_type,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret or config.secret_key, algorithm=config.algorithm)


class TestRateLimitKey:
    """Ключ rate limiting не должен подменяться клиентом."""

    def test_valid_access_token_keys_by_user(self):
        """Подписанный access-токен — ключ по user_id."""
        user_id = str(uuid.uuid4())
        assert get_user_id_or_ip(_make_request(f"Bearer {_make_token(user_id)}")) == f"user:{user_id}"

    def test_forged_signature_falls_back_to_ip(self):
        """Токен с чужой подписью и произвольным sub не меняет ключ — остаётся IP."""
        forged = _make_token("attacker-chosen-sub", secret="not-the-server-secret-key-0123456789")
        assert get_user_id_or_ip(_make_request(f"Bearer {forged}")) == "203.0.113.7"

    def test_unsigned_token_falls_back_to_ip(self):
        """Токен без подписи (alg=none) не принимается."""
        unsigned = jwt.encode({"sub": "attacker", "type": "access"}, key=None, algorithm="none")
        assert get_user_id_or_ip(_make_request(f"Bearer {unsigned}")) == "203.0.113.7"

    def test_random_sub_per_request_does_not_bypass_limit(self):
        """Разные поддельные sub в каждом запросе дают один и тот же ключ."""
        keys = {
            get_user_id_or_ip(_make_request(f"Bearer {_make_token(str(uuid.uuid4()), secret='x' * 40)}")) for _ in range(5)
        }
        assert keys == {"203.0.113.7"}

    def test_expired_token_falls_back_to_ip(self):
        """Просроченный токен — ключ по IP."""
        expired = _make_token(str(uuid.uuid4()), expires_in=-10)
        assert get_user_id_or_ip(_make_request(f"Bearer {expired}")) == "203.0.113.7"

    def test_refresh_token_falls_back_to_ip(self):
        """Refresh-токен не используется как ключ: на защищённые ручки ходят с access-токеном."""
        refresh = _make_token(str(uuid.uuid4()), token_type="refresh")
        assert get_user_id_or_ip(_make_request(f"Bearer {refresh}")) == "203.0.113.7"

    def test_no_token_keys_by_ip(self):
        """Без заголовка Authorization — ключ по IP."""
        assert get_user_id_or_ip(_make_request()) == "203.0.113.7"

    @pytest.mark.asyncio
    async def test_client_ip_taken_from_forwarded_for_behind_proxy(self, client: AsyncClient):
        """За nginx лимит считается по IP из X-Forwarded-For, а не по адресу прокси.

        Как в gunicorn с --forwarded-allow-ips "*": nginx (172.18.0.5) передаёт адрес клиента в X-Forwarded-For.
        """
        proxied_app = ProxyHeadersMiddleware(app, trusted_hosts="*")
        login_data = {"email": "attacker@example.com", "password": "WrongPassword123"}
        transport = ASGITransport(app=proxied_app, client=("172.18.0.5", 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as proxied:
            for _ in range(5):
                res = await proxied.post("/api/v1/auth/login", json=login_data, headers={"X-Forwarded-For": "198.51.100.1"})
                assert res.status_code == 401
            blocked = await proxied.post("/api/v1/auth/login", json=login_data, headers={"X-Forwarded-For": "198.51.100.1"})
            assert blocked.status_code == 429

            other_client = await proxied.post(
                "/api/v1/auth/login", json=login_data, headers={"X-Forwarded-For": "198.51.100.2"}
            )
            assert other_client.status_code == 401
