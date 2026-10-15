"""CLI-скрипт удаления аккаунта пользователя по email (запросы на удаление без входа в приложение).

Выполняет те же шаги, что и DELETE /api/v1/auth/me: `AuthService.delete_account` удаляет пользователя
с каскадом (транзакции, бюджеты, токены) и очищает его ключи в Redis.

Использование:
    python scripts/delete_user_by_email.py --email user@example.com [--yes]

Коды возврата: 0 — удалён, 1 — не найден, 2 — найдено несколько пользователей, 3 — отменено.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import UnitOfWork, async_session_maker, engine  # noqa: E402
from app.redis import close_redis  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from logger.logger import get_logger  # noqa: E402

logger = get_logger("scripts.delete_user_by_email")

EXIT_DELETED = 0
EXIT_NOT_FOUND = 1
EXIT_AMBIGUOUS = 2
EXIT_CANCELLED = 3


def _confirmed(email: str) -> bool:
    """Запросить подтверждение: оператор должен повторно ввести email удаляемого пользователя."""
    answer = input(f"Аккаунт {email} и все его данные будут удалены безвозвратно. Повторите email для подтверждения: ")
    return answer.strip().lower() == email.strip().lower()


async def delete_user_by_email(email: str, assume_yes: bool = False, session_factory: Any = async_session_maker) -> int:
    """Найти пользователя по email (без учёта регистра) и удалить его аккаунт; вернуть код завершения."""
    async with UnitOfWork(session_factory=session_factory) as session:
        found = await AuthService(session).find_users_by_email(email)
        # Забираем значения до закрытия сессии: после commit ORM-объекты могут быть отсоединены
        users = [(user.id, user.email) for user in found]

    if not users:
        print("Пользователь с таким email не найден. Ничего не удалено.")
        return EXIT_NOT_FOUND
    if len(users) > 1:
        print(f"Найдено пользователей: {len(users)}. Автоматическое удаление отменено, разберитесь вручную.")
        return EXIT_AMBIGUOUS

    user_id, user_email = users[0]
    if not assume_yes and not _confirmed(user_email):
        print("Подтверждение не получено. Ничего не удалено.")
        return EXIT_CANCELLED

    # Удаление и очистка Redis — та же логика, что и в DELETE /auth/me; commit выполнит UnitOfWork
    async with UnitOfWork(session_factory=session_factory) as session:
        await AuthService(session).delete_account(user_id)

    logger.info(f"Аккаунт пользователя {user_id} удалён по запросу администратора (CLI)")
    print(f"Аккаунт {user_email} (ID: {user_id}) и все связанные данные удалены.")
    return EXIT_DELETED


async def _run(email: str, assume_yes: bool) -> int:
    """Выполнить удаление и корректно освободить соединения с БД и Redis."""
    try:
        return await delete_user_by_email(email, assume_yes=assume_yes)
    finally:
        await close_redis()
        await engine.dispose()


def main() -> None:
    """Точка входа CLI-скрипта удаления пользователя по email."""
    parser = argparse.ArgumentParser(description="Удаление аккаунта пользователя и всех его данных по email")
    parser.add_argument("--email", required=True, help="Email удаляемого пользователя (без учёта регистра)")
    parser.add_argument("--yes", action="store_true", help="Не запрашивать подтверждение")
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(args.email, args.yes)))


if __name__ == "__main__":
    main()
