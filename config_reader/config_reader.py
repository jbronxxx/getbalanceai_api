"""Модуль чтения и валидации конфигурации приложения."""

import os
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Класс-контейнер для хранения всех настроек приложения."""

    debug: bool = Field(True, alias="APP_DEBUG")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    host: str = Field("0.0.0.0", alias="APP_HOST")
    port: int = Field(8000, alias="APP_PORT")

    db_url: str = Field(
        "postgresql://finance_user:finance_pass@db:5432/finance_db",
        alias="DATABASE_URL",
    )

    secret_key: str = Field("change-me-in-production-use-long-random-string", alias="SECRET_KEY")
    algorithm: str = Field("HS256", alias="AUTH_ALGORITHM")
    access_token_expire_minutes: int = Field(60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    cors_origins: list[str] = Field(["*"], alias="CORS_ORIGINS")
    cors_allow_credentials: bool = Field(True, alias="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: list[str] = Field(["*"], alias="CORS_ALLOW_METHODS")
    cors_allow_headers: list[str] = Field(["*"], alias="CORS_ALLOW_HEADERS")

    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    ai_model: str = Field("gemini-3.8-flash", alias="AI_MODEL")

    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    google_client_id: str | None = Field(None, alias="GOOGLE_CLIENT_ID")
    apple_client_id: str | None = Field(None, alias="APPLE_CLIENT_ID")

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

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if not self.debug and (not self.secret_key or self.secret_key == "change-me-in-production-use-long-random-string"):
            raise ValueError(
                "Недопустимо использовать значение по умолчанию для SECRET_KEY в production режиме (debug=False). "
                "Задайте криптографически стойкий SECRET_KEY через переменные окружения."
            )
        return self


config = Settings()
