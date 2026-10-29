"""Модуль чтения и валидации конфигурации приложения."""

import os
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_SECRET_KEY = "change-me-in-production-use-long-random-string"


class Settings(BaseSettings):
    """Класс-контейнер для хранения всех настроек приложения."""

    debug: bool = Field(False, alias="APP_DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    host: str = Field("0.0.0.0", alias="APP_HOST")
    port: int = Field(8000, alias="APP_PORT")

    db_url: str = Field(
        "postgresql://finance_user:finance_pass@db:5432/finance_db",
        alias="DATABASE_URL",
    )
    # Пул на каждый воркер gunicorn: 4 × (5 + 10) = 60 соединений, с запасом до max_connections=100 Postgres
    # для alembic, бэкапа и скриптов. Прежние 10 + 20 давали до 120 и ошибку "too many clients" под нагрузкой
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(10, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(30, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(1800, alias="DB_POOL_RECYCLE")
    db_pool_pre_ping: bool = Field(True, alias="DB_POOL_PRE_PING")

    secret_key: str = Field(DEFAULT_SECRET_KEY, alias="SECRET_KEY")
    algorithm: str = Field("HS256", alias="AUTH_ALGORITHM")
    access_token_expire_minutes: int = Field(60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    cors_origins: list[str] = Field(["*"], alias="CORS_ORIGINS")
    # Клиенты авторизуются Bearer-токеном, cookie не используются, поэтому credentials по умолчанию выключены
    cors_allow_credentials: bool = Field(False, alias="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: list[str] = Field(["*"], alias="CORS_ALLOW_METHODS")
    cors_allow_headers: list[str] = Field(["*"], alias="CORS_ALLOW_HEADERS")

    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    ai_model: str = Field("gemini-3.5-flash-lite", alias="AI_MODEL")
    ai_daily_limit_per_user: int = Field(5, alias="AI_DAILY_LIMIT_PER_USER")
    # Предел ожидания ответа Gemini: зависший вызов иначе держал бы воркер и запрос клиента без ответа
    ai_request_timeout: float = Field(30.0, alias="AI_REQUEST_TIMEOUT")

    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    # Хранилище счётчиков rate limiting; в Docker — Redis, чтобы лимит был общим для всех воркеров gunicorn
    rate_limit_storage_uri: str = Field("memory://", alias="RATE_LIMIT_STORAGE_URI")

    # Client ID приложений (Android/iOS/Web) через запятую; токен принимается, если его aud совпадает с любым из них
    google_client_ids: Annotated[list[str], NoDecode] = Field([], alias="GOOGLE_CLIENT_ID")
    apple_client_ids: Annotated[list[str], NoDecode] = Field([], alias="APPLE_CLIENT_ID")

    currency_rates_url: str = Field("https://open.er-api.com/v6/latest/USD", alias="CURRENCY_RATES_URL")
    currency_rates_cache_ttl: int = Field(86400, alias="CURRENCY_RATES_CACHE_TTL")
    currency_rates_fallback_cache_ttl: int = Field(180, alias="CURRENCY_RATES_FALLBACK_CACHE_TTL")
    currency_rates_timeout: float = Field(4.0, alias="CURRENCY_RATES_TIMEOUT")
    client_rate_tolerance_percent: float = Field(15.0, alias="CLIENT_RATE_TOLERANCE_PERCENT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="before")
    @classmethod
    def assemble_db_url(cls, data: Any) -> Any:
        if isinstance(data, dict):
            db_url = data.get("DATABASE_URL") or data.get("db_url") or os.getenv("DATABASE_URL")
            if not db_url:
                pg_user = os.getenv("POSTGRES_USER")
                pg_pass = os.getenv("POSTGRES_PASSWORD")
                pg_host = os.getenv("POSTGRES_HOST")
                pg_port = os.getenv("POSTGRES_PORT", "5432")
                pg_db = os.getenv("POSTGRES_DB")

                if pg_user and pg_pass and pg_db and pg_host:
                    db_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

            if db_url:
                data["db_url"] = db_url

            redis_url = data.get("REDIS_URL") or data.get("redis_url") or os.getenv("REDIS_URL")
            if not redis_url:
                redis_host = os.getenv("REDIS_HOST")
                redis_port = os.getenv("REDIS_PORT", "6379")
                redis_db = os.getenv("REDIS_DB", "0")
                if redis_host:
                    redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"

            if redis_url:
                data["redis_url"] = redis_url

        return data

    @field_validator("log_level", mode="before")
    @classmethod
    def assemble_log_level(cls, v: Any) -> str:
        if isinstance(v, str) and v.strip():
            level = v.strip().upper()
            valid_levels = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"}
            if level in valid_levels:
                return "WARNING" if level == "WARN" else ("CRITICAL" if level == "FATAL" else level)
        return "INFO"

    @field_validator("cors_origins", "cors_allow_methods", "cors_allow_headers", mode="before")
    @classmethod
    def assemble_cors_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json

                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @field_validator("google_client_ids", "apple_client_ids", mode="before")
    @classmethod
    def assemble_client_ids(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if self.debug:
            return self
        if not self.secret_key or self.secret_key == DEFAULT_SECRET_KEY:
            raise ValueError(
                "Недопустимо использовать значение по умолчанию для SECRET_KEY в production режиме (debug=False). "
                "Задайте криптографически стойкий SECRET_KEY через переменные окружения."
            )
        # APPLE_CLIENT_ID необязателен: без него вход через Apple отклоняется (iOS-клиента пока нет)
        if not self.google_client_ids:
            raise ValueError(
                "В production режиме (debug=False) обязателен GOOGLE_CLIENT_ID: "
                "без него нельзя проверить, что токен Google выпущен для нашего приложения."
            )
        if "*" in self.cors_origins and self.cors_allow_credentials:
            raise ValueError(
                "В production режиме (debug=False) нельзя сочетать CORS_ORIGINS=* с CORS_ALLOW_CREDENTIALS=true: "
                "браузер отправит учётные данные на запросы с любого сайта. Перечислите домены в CORS_ORIGINS."
            )
        return self


config = Settings()
