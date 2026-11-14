"""CLI-скрипт ручной выдачи и отзыва Pro по email (себе, тестерам, знакомым) без покупки в магазине.

Пишет в `subscriptions` строку с магазином `manual`: тариф берётся из неё так же, как из подписки RevenueCat.
Синхронизация с RevenueCat (покупка, «Восстановить покупки», вебхук) заменит строку данными RevenueCat, а без
доступа там — удалит: после подключения RevenueCat Pro надёжнее выдавать в его панели (Grant entitlement).

Использование (на сервере — в контейнере api):
    python scripts/grant_pro.py --email user@example.com                     # бессрочно
    python scripts/grant_pro.py --email user@example.com --until 2027-01-01  # до даты (00:00 UTC)
    python scripts/grant_pro.py --email user@example.com --revoke            # отозвать выданный вручную

Коды возврата: 0 — выполнено, 1 — пользователь не найден, 2 — неверная дата или нечего отзывать.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import UnitOfWork, async_session_maker, engine  # noqa: E402
from app.redis import close_redis  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.subscription_service import SubscriptionService  # noqa: E402
from logger.logger import get_logger  # noqa: E402

logger = get_logger("scripts.grant_pro")

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INVALID = 2


def _parse_until(value: str | None) -> datetime | None:
    """Дата окончания `ГГГГ-ММ-ДД` → начало этого дня в UTC; не задана — бессрочно.

    Исключения: `ValueError` — неверный формат или дата не в будущем.
    """
    if value is None:
        return None
    until = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
    if until <= datetime.now(timezone.utc):
        raise ValueError("дата окончания должна быть в будущем")
    return until


async def grant_pro(
    email: str,
    until: str | None = None,
    revoke: bool = False,
    session_factory: Any = async_session_maker,
) -> int:
    """Найти пользователя по email (без учёта регистра) и выдать или отозвать Pro; вернуть код."""
    try:
        expires_at = None if revoke else _parse_until(until)
    except ValueError as err:
        print(f"Неверная дата --until: {err}. Формат: ГГГГ-ММ-ДД.")
        return EXIT_INVALID

    async with UnitOfWork(session_factory=session_factory) as session:
        user = await AuthService(session).find_user_by_email(email)
        if user is None:
            print("Пользователь с таким email не найден. Ничего не изменено.")
            return EXIT_NOT_FOUND
        user_id, user_email = user.id, user.email
        service = SubscriptionService(session)
        if revoke:
            if not await service.revoke_manual(user_id):
                print(f"У {user_email} нет Pro, выданного вручную. Ничего не изменено.")
                return EXIT_INVALID
        else:
            await service.grant_manual(user_id, expires_at)

    if revoke:
        logger.info(f"Ручной Pro пользователя {user_id} отозван (CLI)")
        print(f"Pro, выданный вручную, у {user_email} (ID: {user_id}) отозван.")
    else:
        term = f"до {expires_at:%Y-%m-%d} (UTC)" if expires_at else "бессрочно"
        logger.info(f"Пользователю {user_id} выдан Pro вручную {term} (CLI)")
        print(f"{user_email} (ID: {user_id}): Pro {term}. В приложении — после перезапуска или открытия «Профиля».")
    return EXIT_OK


async def _run(email: str, until: str | None, revoke: bool) -> int:
    """Выполнить команду и корректно освободить соединения с БД и Redis."""
    try:
        return await grant_pro(email, until=until, revoke=revoke)
    finally:
        await close_redis()
        await engine.dispose()


def main() -> None:
    """Точка входа CLI-скрипта ручной выдачи Pro."""
    parser = argparse.ArgumentParser(description="Ручная выдача и отзыв Pro по email")
    parser.add_argument("--email", required=True, help="Email пользователя (без учёта регистра)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--until", help="Дата окончания ГГГГ-ММ-ДД (00:00 UTC); без неё — бессрочно")
    group.add_argument("--revoke", action="store_true", help="Отозвать Pro, выданный вручную")
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(args.email, args.until, args.revoke)))


if __name__ == "__main__":
    main()
