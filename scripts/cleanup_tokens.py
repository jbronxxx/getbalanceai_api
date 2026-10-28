"""CLI-скрипт для запуска регламентной очистки истекших и отозванных JWT-токенов из БД.

Использование:
    python scripts/cleanup_tokens.py [--days 30]
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tasks.token_cleanup import run_token_cleanup  # noqa: E402


def main() -> None:
    """Точка входа CLI-скрипта очистки токенов."""
    parser = argparse.ArgumentParser(
        description="Регламентная очистка истекших и отозванных JWT-токенов из базы данных PostgreSQL"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Количество дней хранения истекших токенов (по умолчанию 30)",
    )
    args = parser.parse_args()

    count = asyncio.run(run_token_cleanup(retention_days=args.days))
    print(f"Очистка токенов завершена. Удалено записей: {count}")


if __name__ == "__main__":
    main()
