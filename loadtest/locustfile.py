"""Сценарии нагрузочного теста GetBalance AI API для Locust.

MobileUser повторяет поведение мобильного клиента: списки транзакций и бюджетов с If-None-Match,
синхронизация небольших батчей, создание и удаление операций, редкий запрос курсов.
OfflineSyncUser — клиент после долгой работы без сети: редкие, но тяжёлые батчи /sync до 500 записей.
AI-инсайты не вызываются: каждый запрос — платный вызов Gemini.

Переменные окружения:
    LOADTEST_TOKENS          — JSON с токенами от scripts/issue_load_test_tokens.py (по умолчанию loadtest_tokens.json);
    LOADTEST_SHAPE           — профиль нагрузки из shapes.py (steps, soak, spike, double_spike, stress);
                               без него число пользователей задаётся флагами -u/-r или в веб-интерфейсе;
    LOADTEST_MAX_FAIL_RATIO  — допустимая доля ошибок (по умолчанию 0.001);
    LOADTEST_P95_MS          — допустимый p95 обычных запросов в мс (по умолчанию 500);
    LOADTEST_OFFLINE_P95_MS  — допустимый p95 тяжёлых батчей OfflineSyncUser в мс (по умолчанию 2000).
При нарушении порогов Locust завершается с кодом 1.
"""

import itertools
import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from locust import FastHttpUser, between, events, task
from locust.env import Environment
from locust.runners import WorkerRunner
from shapes import SHAPES

TOKENS_PATH = Path(os.environ.get("LOADTEST_TOKENS", "loadtest_tokens.json"))
MAX_FAIL_RATIO = float(os.environ.get("LOADTEST_MAX_FAIL_RATIO", "0.001"))
P95_MS = int(os.environ.get("LOADTEST_P95_MS", "500"))
OFFLINE_P95_MS = int(os.environ.get("LOADTEST_OFFLINE_P95_MS", "2000"))
OFFLINE_SUFFIX = " [offline]"
# На малой выборке p95 определяется единичными запросами (например, первым промахом кэша курсов)
MIN_REQUESTS_FOR_P95 = 100

logger = logging.getLogger("loadtest")

EXPENSE_CATEGORIES = ("food", "transport", "entertainment", "health", "subscriptions", "shopping", "other")
INCOME_CATEGORIES = ("salary", "freelance", "investments", "transfers", "cashback", "sales")
FOREIGN_CURRENCIES = ("USD", "EUR", "KZT", "UZS")
# Смещения часовых поясов ожидаемой аудитории в минутах: Ташкент, Алматы, Москва, Берлин
TIMEZONE_OFFSETS = ("300", "300", "300", "180", "60")
CREATED_IDS_LIMIT = 200

if os.environ.get("LOADTEST_SHAPE"):
    # Locust берёт профиль из классов-наследников LoadTestShape в модуле locustfile
    SelectedShape = SHAPES[os.environ["LOADTEST_SHAPE"]]


def _load_tokens() -> list[dict[str, str]]:
    """Прочитать токены тестовых пользователей; порядок перемешан, чтобы процессы --processes начинали с разных."""
    users = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))["users"]
    if not users:
        raise RuntimeError(f"В {TOKENS_PATH} нет пользователей")
    return random.sample(users, len(users))


# Несколько виртуальных пользователей могут делить токен: лимиты API считаются на пользователя,
# запас по ним рассчитан на ~20 виртуальных пользователей на токен (см. README.md)
_tokens = itertools.cycle(_load_tokens())


def _transaction_payload() -> dict[str, Any]:
    """Операция, как её отправляет клиент: чаще расход в базовой валюте, иногда — в другой валюте без курса."""
    is_income = random.random() < 0.15
    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": "income" if is_income else "expense",
        "category": random.choice(INCOME_CATEGORIES if is_income else EXPENSE_CATEGORIES),
        "amount": f"{random.uniform(1, 500 if is_income else 100):.2f}",
        "description": "Нагрузочный тест",
        "date": (datetime.now(timezone.utc) - timedelta(minutes=random.randint(0, 600))).isoformat(),
    }
    # Курс сервер берёт из кэша курсов: так в нагрузку попадает и CurrencyService
    if random.random() < 0.1:
        payload["currency"] = random.choice(FOREIGN_CURRENCIES)
    return payload


def _budget_payload() -> dict[str, Any]:
    """Лимит текущего месяца по расходной категории."""
    now = datetime.now(timezone.utc)
    return {
        "category": random.choice(EXPENSE_CATEGORIES),
        "limit_amount": f"{random.randrange(100, 1000, 10)}.00",
        "month": now.month,
        "year": now.year,
    }


class ApiUser(FastHttpUser):
    """Общая часть сценариев: токен тестового пользователя и проверка ответов."""

    abstract = True

    def on_start(self) -> None:
        """Получить токен и заголовки клиента."""
        token = next(_tokens)
        self.headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "X-Timezone-Offset": random.choice(TIMEZONE_OFFSETS),
        }
        self.created_ids: list[str] = []

    def _request(
        self,
        method: str,
        path: str,
        name: str,
        expected: tuple[int, ...] = (200,),
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Выполнить запрос и засчитать ошибкой неожиданный статус; вернуть ответ."""
        with self.client.request(
            method, path, name=name, headers={**self.headers, **(headers or {})}, catch_response=True, **kwargs
        ) as response:
            if response.status_code not in expected:
                response.failure(f"HTTP {response.status_code}")
            return response

    def _sync(self, transactions: int, budgets: int, name: str) -> None:
        """Отправить батч синхронизации и запомнить созданные операции для последующего удаления."""
        payload = {
            "transactions": [_transaction_payload() for _ in range(transactions)],
            "budgets": [_budget_payload() for _ in range(budgets)],
        }
        response = self._request("POST", "/api/v1/sync/", name, json=payload)
        if response.status_code == 200:
            synced = response.json()["data"]["synced_transactions"]
            self._remember(item["id"] for item in synced)

    def _remember(self, ids: Any) -> None:
        """Сохранить ID созданных операций, ограничив список."""
        self.created_ids.extend(ids)
        del self.created_ids[:-CREATED_IDS_LIMIT]


class MobileUser(ApiUser):
    """Обычный пользователь мобильного приложения."""

    weight = 50
    wait_time = between(5, 15)

    def on_start(self) -> None:
        """Получить токен и сбросить сохранённые ETag."""
        super().on_start()
        self.transactions_etag: str | None = None
        self.budgets_etag: str | None = None

    @task(25)
    def list_transactions(self) -> None:
        """Первая страница транзакций с If-None-Match; иногда — следующая страница по курсору."""
        headers = {"If-None-Match": self.transactions_etag} if self.transactions_etag else None
        response = self._request(
            "GET", "/api/v1/transactions/?limit=50", "/api/v1/transactions/", expected=(200, 304), headers=headers
        )
        if response.status_code != 200:
            return
        self.transactions_etag = response.headers.get("ETag")
        cursor = response.json()["data"]["next_cursor"]
        if cursor and random.random() < 0.2:
            self._request(
                "GET", "/api/v1/transactions/", "/api/v1/transactions/?cursor", params={"limit": 50, "cursor": cursor}
            )

    @task(20)
    def list_budgets(self) -> None:
        """Бюджеты текущего месяца с If-None-Match."""
        now = datetime.now(timezone.utc)
        headers = {"If-None-Match": self.budgets_etag} if self.budgets_etag else None
        response = self._request(
            "GET",
            f"/api/v1/budgets/?month={now.month}&year={now.year}",
            "/api/v1/budgets/",
            expected=(200, 304),
            headers=headers,
        )
        if response.status_code == 200:
            self.budgets_etag = response.headers.get("ETag")

    @task(30)
    def sync(self) -> None:
        """Синхронизация небольшого батча, накопленного клиентом между открытиями приложения."""
        self._sync(random.randint(5, 20), random.randint(0, 2), "/api/v1/sync/")

    @task(15)
    def create_transaction(self) -> None:
        """Создание одной операции онлайн."""
        response = self._request(
            "POST", "/api/v1/transactions/", "/api/v1/transactions/ [create]", expected=(201,), json=_transaction_payload()
        )
        if response.status_code == 201:
            self._remember([response.json()["data"]["id"]])

    @task(5)
    def currency_rates(self) -> None:
        """Справочник курсов: клиент обновляет его при открытии экрана конвертации."""
        self._request("GET", "/api/v1/currencies/rates", "/api/v1/currencies/rates")

    @task(5)
    def delete_transaction(self) -> None:
        """Удаление ранее созданной операции; без созданных операций действие пропускается."""
        if not self.created_ids:
            return
        tx_id = self.created_ids.pop(random.randrange(len(self.created_ids)))
        self._request("DELETE", f"/api/v1/transactions/{tx_id}", "/api/v1/transactions/{id}")


class OfflineSyncUser(ApiUser):
    """Клиент после долгой работы без сети: тяжёлый батч и удаления, накопленные в оффлайн-очереди."""

    weight = 1
    wait_time = between(30, 60)

    @task
    def offline_sync(self) -> None:
        """Батч 200–500 операций (максимум схемы — 500) и бюджеты на месяц."""
        self._sync(random.randint(200, 500), random.randint(3, 7), "/api/v1/sync/" + OFFLINE_SUFFIX)


@events.quitting.add_listener
def check_thresholds(environment: Environment, **_kwargs: Any) -> None:
    """Сверить итог с критериями успеха и завершить Locust с кодом 1 при нарушении."""
    if isinstance(environment.runner, WorkerRunner):
        return
    stats = environment.stats
    violations: list[str] = []

    if stats.total.fail_ratio > MAX_FAIL_RATIO:
        violations.append(f"доля ошибок {stats.total.fail_ratio:.4f} > {MAX_FAIL_RATIO}")
    rate_limited = sum(error.occurrences for error in stats.errors.values() if "HTTP 429" in str(error.error))
    if rate_limited:
        violations.append(f"ответов 429: {rate_limited} — лимиты API не должны срабатывать, выдайте больше токенов")
    for entry in stats.entries.values():
        limit = OFFLINE_P95_MS if entry.name.endswith(OFFLINE_SUFFIX) else P95_MS
        p95 = entry.get_response_time_percentile(0.95)
        if entry.num_requests >= MIN_REQUESTS_FOR_P95 and p95 > limit:
            violations.append(f"{entry.method} {entry.name}: p95 {p95:.0f} мс > {limit} мс")

    if violations:
        environment.process_exit_code = 1
        logger.error("Критерии успеха не выполнены:\n  %s", "\n  ".join(violations))
    else:
        logger.info("Критерии успеха выполнены")
