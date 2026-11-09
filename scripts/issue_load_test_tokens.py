"""CLI-скрипт подготовки сервера к нагрузочному тестированию: тестовые пользователи, их история и токены.

Команды:
    issue   — создать пользователей с токенами (JSON для Locust) и историей операций за год, а также фоновых
              пользователей без токенов: таблицы получают объём, близкий к реальному, и запросы идут
              по настоящим индексам, а не по пустым таблицам в кэше Postgres;
    cleanup — удалить всех тестовых пользователей вместе с данными (каскад в БД) и их ключи в Redis.

Тестовые пользователи создаются только с email loadtest+...@getbalanceai.test (зарезервированный домен .test):
по этому шаблону их находит cleanup, реальные пользователи не затрагиваются.

Использование:
    python scripts/issue_load_test_tokens.py issue --users 150 --background-users 3000 --out /tmp/loadtest_tokens.json
    python scripts/issue_load_test_tokens.py cleanup

Коды возврата: 0 — успех, 1 — тестовые пользователи уже существуют (сначала выполнить cleanup).
"""

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redis.exceptions import RedisError  # noqa: E402
from sqlalchemy import delete, func, insert, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.database import async_session_maker, engine  # noqa: E402
from app.models.models import (  # noqa: E402
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    Budget,
    Category,
    Transaction,
    TransactionType,
    User,
)
from app.redis import close_redis, get_budgets_version_key, get_redis  # noqa: E402
from app.services.ai_service import AIService  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from config_reader.config_reader import config  # noqa: E402
from logger.logger import get_logger  # noqa: E402

logger = get_logger("scripts.issue_load_test_tokens")

EXIT_OK = 0
EXIT_ALREADY_SEEDED = 1

EMAIL_DOMAIN = "getbalanceai.test"
TOKEN_USER_PREFIX = "loadtest+u-"
BACKGROUND_USER_PREFIX = "loadtest+bg-"
EMAIL_PATTERN = f"loadtest+%@{EMAIL_DOMAIN}"

# Размеры пачек: строки истории генерируются потоково, чтобы сид миллиона транзакций не занимал гигабайты памяти
# на сервере с 4 ГБ RAM, а каждая транзакция БД оставалась короткой
USER_BATCH_SIZE = 500
INSERT_CHUNK_SIZE = 5000
DELETE_CHUNK_SIZE = 200
HISTORY_DAYS = 365
BUDGET_MONTHS = 12
ANALYZED_TABLES = ("users", "tokens", "transactions", "budgets")

# Примерные курсы к USD: сид не обращается к внешнему API курсов, для нагрузки важна форма данных, а не точность
USD_RATES: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "EUR": Decimal("0.92"),
    "KZT": Decimal("480"),
    "UZS": Decimal("12700"),
}
BASE_CURRENCY_WEIGHTS: dict[str, int] = {"USD": 50, "KZT": 25, "UZS": 15, "EUR": 10}
FOREIGN_CURRENCY_SHARE = 0.1
INCOME_SHARE = 0.15

# Диапазоны сумм в USD: правдоподобные траты и доходы, пересчитываются в валюту операции
EXPENSE_RANGES_USD: dict[Category, tuple[float, float]] = {
    Category.food: (3, 80),
    Category.transport: (1, 40),
    Category.entertainment: (5, 120),
    Category.health: (5, 200),
    Category.subscriptions: (3, 30),
    Category.shopping: (10, 300),
    Category.housing: (50, 600),
    Category.communication: (5, 40),
    Category.education: (20, 400),
    Category.family: (10, 250),
    Category.car: (10, 300),
    Category.gifts: (10, 200),
    Category.travel: (50, 1500),
    Category.beauty: (5, 120),
    Category.other: (1, 100),
}
INCOME_RANGES_USD: dict[Category, tuple[float, float]] = {
    Category.salary: (800, 5000),
    Category.freelance: (100, 2000),
    Category.investments: (10, 500),
    Category.transfers: (20, 500),
    Category.cashback: (1, 50),
    Category.sales: (10, 800),
    Category.other: (5, 300),
}
DESCRIPTIONS: dict[Category, tuple[str, ...]] = {
    Category.food: ("Продукты", "Кофе", "Обед", "Доставка еды", "Супермаркет"),
    Category.transport: ("Такси", "Метро", "Бензин", "Парковка"),
    Category.entertainment: ("Кино", "Концерт", "Боулинг", "Игры"),
    Category.health: ("Аптека", "Стоматолог", "Анализы", "Спортзал"),
    Category.subscriptions: ("Музыка", "Видеосервис", "Облако"),
    Category.shopping: ("Одежда", "Обувь", "Электроника", "Товары для дома"),
    Category.housing: ("Аренда", "Коммунальные услуги", "Электричество", "Ремонт"),
    Category.communication: ("Мобильная связь", "Домашний интернет"),
    Category.education: ("Курсы", "Учебники", "Репетитор"),
    Category.family: ("Детский сад", "Игрушки", "Школьные принадлежности"),
    Category.car: ("Техобслуживание", "Страховка", "Мойка", "Шины"),
    Category.gifts: ("Подарок на день рождения", "Цветы"),
    Category.travel: ("Авиабилеты", "Отель", "Экскурсия"),
    Category.beauty: ("Парикмахерская", "Косметика", "Маникюр"),
    Category.salary: ("Зарплата", "Аванс", "Премия"),
    Category.freelance: ("Заказ на фрилансе", "Консультация"),
    Category.investments: ("Дивиденды", "Купон по облигациям"),
    Category.transfers: ("Перевод от друга", "Подарок"),
    Category.cashback: ("Кэшбэк по карте", "Бонусы"),
    Category.sales: ("Продажа вещей", "Продажа техники"),
    Category.other: ("Разное", "Прочее"),
}

# Фоновые пользователи неравномерны, как в жизни: большинство ведёт учёт недавно, немногие — давно и подробно
BACKGROUND_HISTORY_BUCKETS: tuple[tuple[int, int, int], ...] = ((60, 50, 200), (30, 200, 600), (10, 600, 1000))

CENT = Decimal("0.01")
RATE_PRECISION = Decimal("0.000001")


@dataclass(frozen=True)
class IssuedTokens:
    """Пара токенов тестового пользователя для сценариев Locust."""

    user_id: uuid.UUID
    email: str
    access_token: str
    refresh_token: str


def load_test_email(index: int, background: bool = False) -> str:
    """Email тестового пользователя: по префиксу loadtest+ его находит cleanup."""
    prefix = BACKGROUND_USER_PREFIX if background else TOKEN_USER_PREFIX
    return f"{prefix}{index:05d}@{EMAIL_DOMAIN}"


def _exchange_rate(currency: str, base_currency: str) -> Decimal:
    """Курс валюты операции к базовой валюте пользователя по примерным курсам сида."""
    return (USD_RATES[base_currency] / USD_RATES[currency]).quantize(RATE_PRECISION, rounding=ROUND_HALF_UP)


def generate_user_row(rng: random.Random, email: str, created_at: datetime) -> dict[str, Any]:
    """Строка пользователя со случайной базовой валютой по долям, близким к ожидаемой аудитории."""
    base_currency = rng.choices(list(BASE_CURRENCY_WEIGHTS), weights=list(BASE_CURRENCY_WEIGHTS.values()))[0]
    return {
        "id": uuid.uuid4(),
        "email": email,
        "name": email.split("@")[0],
        "base_currency": base_currency,
        "created_at": created_at,
    }


def generate_transactions(
    rng: random.Random, user_id: uuid.UUID, base_currency: str, count: int, now: datetime
) -> Iterator[dict[str, Any]]:
    """Сгенерировать историю операций пользователя за последний год: в основном расходы в базовой валюте."""
    foreign_currencies = [code for code in USD_RATES if code != base_currency]
    income_categories = sorted(INCOME_CATEGORIES, key=lambda c: c.value)
    expense_categories = sorted(EXPENSE_CATEGORIES, key=lambda c: c.value)
    for _ in range(count):
        is_income = rng.random() < INCOME_SHARE
        tx_type = TransactionType.income if is_income else TransactionType.expense
        category = rng.choice(income_categories if is_income else expense_categories)
        low, high = (INCOME_RANGES_USD if is_income else EXPENSE_RANGES_USD)[category]
        currency = rng.choice(foreign_currencies) if rng.random() < FOREIGN_CURRENCY_SHARE else base_currency

        amount = (Decimal(str(rng.uniform(low, high))) * USD_RATES[currency]).quantize(CENT, rounding=ROUND_HALF_UP)
        rate = _exchange_rate(currency, base_currency)
        date = now - timedelta(seconds=rng.randint(0, HISTORY_DAYS * 24 * 3600))
        yield {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "amount": amount,
            "currency": currency,
            "exchange_rate": rate,
            "base_amount": (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP),
            "description": rng.choice(DESCRIPTIONS[category]),
            "category": category,
            "type": tx_type,
            "date": date,
            "created_at": date,
            "updated_at": date,
        }


def generate_budgets(rng: random.Random, user_id: uuid.UUID, base_currency: str, now: datetime) -> list[dict[str, Any]]:
    """Сгенерировать месячные лимиты за последний год: на каждый месяц — 4–7 расходных категорий."""
    expense_categories = sorted(EXPENSE_CATEGORIES, key=lambda c: c.value)
    rows: list[dict[str, Any]] = []
    year, month = now.year, now.month
    for _ in range(BUDGET_MONTHS):
        for category in rng.sample(expense_categories, rng.randint(4, len(expense_categories))):
            limit_usd = Decimal(rng.randrange(100, 1000, 10))
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "user_id": user_id,
                    "category": category,
                    "limit_amount": (limit_usd * USD_RATES[base_currency]).quantize(CENT),
                    "currency": base_currency,
                    "month": month,
                    "year": year,
                    "created_at": now,
                }
            )
        month, year = (12, year - 1) if month == 1 else (month - 1, year)
    return rows


def background_history_size(rng: random.Random) -> int:
    """Число операций фонового пользователя по распределению BACKGROUND_HISTORY_BUCKETS."""
    weights = [bucket[0] for bucket in BACKGROUND_HISTORY_BUCKETS]
    _, low, high = rng.choices(BACKGROUND_HISTORY_BUCKETS, weights=weights)[0]
    return rng.randint(low, high)


async def _insert_chunked(session: AsyncSession, model: type, rows: Iterator[dict[str, Any]]) -> int:
    """Вставить строки пачками по INSERT_CHUNK_SIZE; вернуть число вставленных строк."""
    total = 0
    chunk: list[dict[str, Any]] = []
    for row in rows:
        chunk.append(row)
        if len(chunk) >= INSERT_CHUNK_SIZE:
            await session.execute(insert(model), chunk)
            total += len(chunk)
            chunk = []
    if chunk:
        await session.execute(insert(model), chunk)
        total += len(chunk)
    return total


async def _analyze(session: AsyncSession) -> None:
    """Обновить статистику планировщика: после массовой вставки или удаления план запросов иначе устаревает."""
    for table in ANALYZED_TABLES:
        await session.execute(text(f"ANALYZE {table}"))


async def _seed_users(
    session: AsyncSession,
    rng: random.Random,
    emails: list[str],
    history_size: Callable[[], int],
    now: datetime,
) -> tuple[list[uuid.UUID], int, int]:
    """Создать пользователей с историей операций и бюджетами пачками; вернуть их ID и число строк истории."""
    user_ids: list[uuid.UUID] = []
    tx_total = 0
    budget_total = 0
    for start in range(0, len(emails), USER_BATCH_SIZE):
        users = [
            generate_user_row(rng, email, now - timedelta(days=HISTORY_DAYS))
            for email in emails[start : start + USER_BATCH_SIZE]
        ]
        await session.execute(insert(User), users)

        def history() -> Iterator[dict[str, Any]]:
            for user in users:
                yield from generate_transactions(rng, user["id"], user["base_currency"], history_size(), now)

        tx_total += await _insert_chunked(session, Transaction, history())
        budgets = (row for user in users for row in generate_budgets(rng, user["id"], user["base_currency"], now))
        budget_total += await _insert_chunked(session, Budget, budgets)
        await session.commit()

        user_ids.extend(user["id"] for user in users)
        print(f"Создано пользователей: {start + len(users)}/{len(emails)}, транзакций: {tx_total}, бюджетов: {budget_total}")
    return user_ids, tx_total, budget_total


async def issue_load_test_tokens(
    users: int,
    transactions_per_user: int,
    background_users: int,
    out_path: Path,
    session_factory: Any = async_session_maker,
    rng: random.Random | None = None,
) -> int:
    """Создать тестовых пользователей с историей, выдать им токены и записать JSON для Locust."""
    rng = rng or random.Random()
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        existing = (
            await session.execute(select(func.count()).select_from(User).where(User.email.like(EMAIL_PATTERN)))
        ).scalar_one()
        if existing:
            print(f"В БД уже есть тестовые пользователи ({existing}). Сначала выполните команду cleanup.")
            return EXIT_ALREADY_SEEDED

        token_emails = [load_test_email(i) for i in range(1, users + 1)]
        token_user_ids, tx_total, budget_total = await _seed_users(
            session, rng, token_emails, lambda: transactions_per_user, now
        )

        background_emails = [load_test_email(i, background=True) for i in range(1, background_users + 1)]
        _, bg_tx_total, bg_budget_total = await _seed_users(
            session, rng, background_emails, lambda: background_history_size(rng), now
        )

        auth_service = AuthService(session)
        issued: list[IssuedTokens] = []
        for user_id, email in zip(token_user_ids, token_emails):
            access_token, refresh_token = await auth_service._issue_token_pair(user_id)
            issued.append(IssuedTokens(user_id, email, access_token, refresh_token))
        await session.commit()

        await _analyze(session)
        await session.commit()

    expires_at = now + timedelta(minutes=config.access_token_expire_minutes)
    document = {
        "issued_at": now.isoformat(),
        "access_expires_at": expires_at.isoformat(),
        "users": [
            {
                "user_id": str(item.user_id),
                "email": item.email,
                "access_token": item.access_token,
                "refresh_token": item.refresh_token,
            }
            for item in issued
        ],
    }
    # Файл даёт доступ к тестовым аккаунтам на сервере: создаётся с правами только для владельца
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=2)

    logger.info(f"Выданы токены нагрузочного теста: пользователей {users}, фоновых {background_users}")
    print(
        f"Готово. Пользователи с токенами: {users} (транзакций {tx_total}, бюджетов {budget_total}); "
        f"фоновые: {background_users} (транзакций {bg_tx_total}, бюджетов {bg_budget_total}).\n"
        f"Токены: {out_path}. Access-токены действительны до {expires_at.isoformat()}."
    )
    return EXIT_OK


async def cleanup_load_test_users(session_factory: Any = async_session_maker) -> int:
    """Удалить всех тестовых пользователей с данными и их ключи в Redis; вернуть число удалённых пользователей."""
    async with session_factory() as session:
        result = await session.execute(select(User.id, User.email).where(User.email.like(EMAIL_PATTERN)))
        users = [(row.id, row.email) for row in result]

        user_ids = [user_id for user_id, _ in users]
        for start in range(0, len(user_ids), DELETE_CHUNK_SIZE):
            chunk = user_ids[start : start + DELETE_CHUNK_SIZE]
            # Транзакции, бюджеты и токены удаляет БД (ON DELETE CASCADE); пачки держат транзакции БД короткими
            await session.execute(delete(User).where(User.id.in_(chunk)).execution_options(synchronize_session=False))
            await session.commit()
            print(f"Удалено пользователей: {start + len(chunk)}/{len(user_ids)}")

        await _analyze(session)
        await session.commit()

    # Сбой очистки Redis не отменяет удаление: ключи версий бюджетов без владельца истекут сами
    if users:
        try:
            await get_redis().delete(*(get_budgets_version_key(user_id) for user_id in user_ids))
        except RedisError as err:
            logger.warning(f"Не удалось удалить версии бюджетов тестовых пользователей из Redis: {err}")
    # Фоновые пользователи запросов не делают, кэш инсайтов мог появиться только у пользователей с токенами
    for user_id, email in users:
        if email.startswith(TOKEN_USER_PREFIX):
            await AIService.invalidate_cache(user_id)

    logger.info(f"Удалены тестовые пользователи нагрузочного теста: {len(users)}")
    print(f"Готово. Удалено тестовых пользователей: {len(users)}.")
    return len(users)


async def _run(args: argparse.Namespace) -> int:
    """Выполнить команду и корректно освободить соединения с БД и Redis."""
    try:
        if args.command == "issue":
            return await issue_load_test_tokens(
                users=args.users,
                transactions_per_user=args.transactions,
                background_users=args.background_users,
                out_path=args.out,
                rng=random.Random(args.random_seed),
            )
        await cleanup_load_test_users()
        return EXIT_OK
    finally:
        await close_redis()
        await engine.dispose()


def _positive_int(value: str) -> int:
    """Целое число не меньше нуля для аргументов командной строки."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("значение не может быть отрицательным")
    return number


def main() -> None:
    """Точка входа CLI-скрипта подготовки нагрузочного теста."""
    parser = argparse.ArgumentParser(description="Тестовые пользователи, данные и токены для нагрузочного тестирования")
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue", help="Создать тестовых пользователей с историей и выдать токены")
    issue.add_argument("--users", type=_positive_int, default=150, help="Пользователей с токенами (по умолчанию 150)")
    issue.add_argument(
        "--transactions", type=_positive_int, default=1000, help="Операций у пользователя с токеном (по умолчанию 1000)"
    )
    issue.add_argument(
        "--background-users", type=_positive_int, default=0, help="Фоновых пользователей без токенов (по умолчанию 0)"
    )
    issue.add_argument("--out", type=Path, required=True, help="Путь JSON-файла с токенами")
    issue.add_argument("--random-seed", type=int, default=None, help="Seed генератора для воспроизводимых данных")

    commands.add_parser("cleanup", help="Удалить всех тестовых пользователей и их данные")

    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
