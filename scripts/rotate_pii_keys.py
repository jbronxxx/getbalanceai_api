"""CLI-скрипт ротации ключей шифрования имени и email пользователей (app/pii.py).

Перешифровывает `users.email` и `users.name` текущим ключом (первым в `PII_ENCRYPTION_KEYS`) и пересчитывает
слепой индекс `users.email_hash` текущим `PII_HMAC_KEY`. Повторный запуск безопасен.

Ротация ключа шифрования (API работает):
    1. Новый ключ — первым в PII_ENCRYPTION_KEYS, старый оставить следующим; деплой.
    2. python scripts/rotate_pii_keys.py
    3. Старый ключ убрать из PII_ENCRYPTION_KEYS; деплой.

Смена PII_HMAC_KEY — только с остановленным API: пока индекс не пересчитан, вход по email не находит
пользователя и создал бы второй аккаунт. Остановить API, сменить ключ, запустить скрипт, запустить API.

Использование:
    python scripts/rotate_pii_keys.py
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update  # noqa: E402

from app.database import async_session_maker, engine  # noqa: E402
from app.models.models import User  # noqa: E402
from app.pii import blind_index, get_pii_cipher  # noqa: E402
from logger.logger import get_logger  # noqa: E402

logger = get_logger("scripts.rotate_pii_keys")

BATCH_SIZE = 500


async def rotate_pii_keys(session_factory: Any = async_session_maker, batch_size: int = BATCH_SIZE) -> int:
    """Перешифровать имя и email всех пользователей текущим ключом; вернуть число обработанных пользователей.

    Значения читаются через ORM (расшифровка любым ключом из списка) и записываются обратно пакетным UPDATE
    по первичному ключу — тип колонки шифрует их текущим ключом. Пачки держат транзакции БД короткими.
    """
    current_key_id = get_pii_cipher().current_key_id
    total = 0
    async with session_factory() as session:
        rows = (await session.execute(select(User.id, User.email, User.name).order_by(User.id))).all()
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            await session.execute(
                update(User),
                [
                    {"id": user_id, "email": email, "name": name, "email_hash": blind_index(email)}
                    for user_id, email, name in chunk
                ],
            )
            await session.commit()
            total += len(chunk)
            print(f"Перешифровано пользователей: {total}/{len(rows)}")

    logger.info(f"Имя и email пользователей перешифрованы ключом {current_key_id}: {total}")
    print(f"Готово. Пользователей: {total}, текущий ключ: {current_key_id}.")
    return total


async def _run() -> int:
    """Выполнить ротацию и освободить соединения с БД."""
    try:
        await rotate_pii_keys()
        return 0
    finally:
        await engine.dispose()


def main() -> None:
    """Точка входа CLI-скрипта ротации ключей."""
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
