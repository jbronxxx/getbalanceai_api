"""Конфигурация окружения для выполнения миграций Alembic."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models.models  # noqa: F401 - регистрация всех моделей для autogenerate
from app.database import Base
from config_reader.config_reader import config as app_config

# Объект конфигурации Alembic, предоставляющий доступ к значениям alembic.ini
config = context.config

# Настройка логирования на основе файла конфигурации
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные моделей для поддержки автоматической генерации миграций (--autogenerate)
target_metadata = Base.metadata

# Динамическая подстановка URL базы данных из конфигурации приложения.
# Alembic работает через синхронный Engine, поэтому async-драйвер (+asyncpg) заменяется на дефолтный синхронный.
config.set_main_option("sqlalchemy.url", app_config.db_url.replace("postgresql+asyncpg://", "postgresql://"))


def run_migrations_offline() -> None:
    """Запуск миграций в 'offline' режиме (генерация SQL-скриптов без прямого подключения к БД)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Запуск миграций в 'online' режиме с прямым подключением к базе данных через Engine."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
