"""Замер расхода, себестоимости и качества ИИ на Claude Haiku: ИИ-отчёт и голосовой разбор на тестовых данных.

Не для прода, запускается вручную: каждый прогон — платные вызовы моделей (весь прогон — меньше $1).
Промпты те же, что в сервисах (`AIService.build_summary` / `build_request`, `VoiceParseService.build_request`);
данные синтетические и одинаковые от прогона к прогону (seed), без данных пользователей.

Итог: средний и максимальный расход на один ИИ-отчёт и один голосовой разбор по каждому варианту модели и
себестоимость пользователя в месяц — при обычном использовании и при полностью выбранных суточных лимитах (AI-01).
Качество (AI-02): голосовой разбор сверяется с эталоном фраз (суммы, тип, категория, дата, валюта), у отчётов —
язык ответа и суммы, которых нет в данных; время ответа, сбои и повторы. `--blind` готовит отчёты для слепой оценки.

Использование:
    python scripts/measure_ai_cost.py --dry-run                      # данные и размер промптов, без вызовов
    python scripts/measure_ai_cost.py                                # оба варианта effort, 24 месяца и 50 фраз
    python scripts/measure_ai_cost.py --variants haiku-low --months 2 --phrases 4
    python scripts/measure_ai_cost.py --out /путь/вне/репозитория/ai_cost.json   # плюс ответы моделей
    python scripts/measure_ai_cost.py --evaluate ai_cost.json --blind /путь/вне/репозитория   # без вызовов

Ключ — ANTHROPIC_API_KEY из .env (только локально). Коды возврата: 0 — готово, 2 — нет ключа.
"""

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from app.models.models import (  # noqa: E402
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    Category,
    CurrencyCode,
    Transaction,
    TransactionType,
)
from app.schemas.schemas import BudgetResponse  # noqa: E402
from app.services.ai_service import (  # noqa: E402
    MIN_TRANSACTIONS_FOR_INSIGHTS,
    AIService,
)
from app.services.ai_usage import AIRequestKind, TokenUsage, claude_usage  # noqa: E402
from app.services.gemini import extract_json  # noqa: E402
from app.services.voice_service import VoiceParseService  # noqa: E402
from config_reader.config_reader import config  # noqa: E402

SEED = 20261113
# Операций в отчёте не больше, чем отдаёт сервис (выборка последних 50 за месяц)
MAX_TRANSACTIONS = 50
# «Сегодня» для голосового разбора — фиксированное, чтобы промпт не менялся от прогона к прогону
VOICE_TODAY = date(2026, 11, 13)
CALL_TIMEOUT_SECONDS = 90
CLAUDE_MAX_TOKENS = 16000
CONCURRENCY = 4
# Попыток на вызов: повторы считает скрипт (у клиента SDK max_retries=0), чтобы видеть сбои API
CALL_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
RETRYABLE_ERRORS = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)
DAYS_IN_MONTH = 30


@dataclass(frozen=True)
class Variant:
    """Вариант модели для замера; effort — уровень размышлений Claude (у Haiku 5.5 по умолчанию medium)."""

    name: str
    model: str
    effort: str


VARIANTS = {
    "haiku-low": Variant("haiku-low", "claude-haiku-5-5", effort="low"),
    "haiku-medium": Variant("haiku-medium", "claude-haiku-5-5", effort="medium"),
}


@dataclass(frozen=True)
class Request:
    """Один запрос к модели: системная инструкция и промпт в том виде, в каком их шлёт сервис."""

    kind: AIRequestKind
    label: str
    locale: str
    system: str
    prompt: str


@dataclass
class Result:
    """Итог одного вызова; ответ сохраняется в --out для сравнения качества (AI-02)."""

    variant: str
    model: str
    kind: str
    label: str
    locale: str
    seconds: float
    usage: TokenUsage | None = None
    cost_usd: float | None = None
    valid_json: bool = False
    error: str | None = None
    text: str = ""
    # Попыток до ответа; 0 — в сохранённом прогоне попытки не считались (до AI-02)
    attempts: int = 0


# Диапазоны сумм по категориям в евро/долларах; остальные категории — по умолчанию
AMOUNT_RANGES = {
    Category.food: (3, 90),
    Category.transport: (2, 60),
    Category.housing: (300, 1400),
    Category.subscriptions: (5, 25),
    Category.communication: (10, 40),
    Category.health: (10, 200),
    Category.car: (30, 300),
    Category.education: (20, 400),
    Category.salary: (1800, 5200),
    Category.freelance: (150, 1500),
    Category.investments: (20, 600),
    Category.cashback: (2, 40),
}
DEFAULT_AMOUNT_RANGE = (5, 150)
# Расходы чаще в повседневных категориях: веса выбора категории
EXPENSE_WEIGHTS = {Category.food: 8, Category.transport: 4, Category.shopping: 3, Category.entertainment: 2}

YESTERDAY = VOICE_TODAY - timedelta(days=1)
DAY_BEFORE_YESTERDAY = VOICE_TODAY - timedelta(days=2)
# «В пятницу», когда сегодня пятница: и сегодня, и прошлая пятница — верно
FRIDAYS = (VOICE_TODAY, VOICE_TODAY - timedelta(days=7))


@dataclass(frozen=True)
class Expected:
    """Эталон записи голосового разбора; у неоднозначных фраз допустимы несколько категорий или дат."""

    amount: Decimal
    categories: tuple[Category, ...]
    type: TransactionType = TransactionType.expense
    currency: CurrencyCode = CurrencyCode.EUR
    dates: tuple[date, ...] = (VOICE_TODAY,)


def _expense(amount: str, *categories: Category, **fields) -> Expected:
    return Expected(Decimal(amount), categories, **fields)


def _income(amount: str, *categories: Category) -> Expected:
    return Expected(Decimal(amount), categories, type=TransactionType.income)


@dataclass(frozen=True)
class VoicePhrase:
    locale: str
    text: str
    expected: tuple[Expected, ...]


# Фраза на русском, та же на английском и эталон записей (валюта по умолчанию — EUR, «сегодня» — VOICE_TODAY)
VOICE_CASES: list[tuple[str, str, tuple[Expected, ...]]] = [
    ("кофе 3 евро 50 центов", "coffee 3.50 euros", (_expense("3.50", Category.food),)),
    ("потратил 42 евро в супермаркете на продукты", "spent 42 euros on groceries", (_expense("42", Category.food),)),
    (
        "вчера такси до аэропорта 35 евро",
        "taxi to the airport yesterday 35 euros",
        (_expense("35", Category.transport, Category.travel, dates=(YESTERDAY,)),),
    ),
    ("получил зарплату 3200 евро", "got my salary 3200 euros", (_income("3200", Category.salary),)),
    (
        "обед 14 евро и кино 11 евро",
        "lunch 14 euros and cinema 11 euros",
        (_expense("14", Category.food), _expense("11", Category.entertainment)),
    ),
    ("заправка 60 евро", "gas 60 euros", (_expense("60", Category.car, Category.transport),)),
    (
        "в пятницу купил кроссовки за 89 евро",
        "bought sneakers on friday for 89 euros",
        (_expense("89", Category.shopping, dates=FRIDAYS),),
    ),
    (
        "оплатил интернет 25 евро и мобильную связь 12",
        "paid internet 25 euros and phone bill 12",
        (_expense("25", Category.communication), _expense("12", Category.communication)),
    ),
    (
        "подписка на музыку 10 долларов",
        "music subscription 10 dollars",
        (_expense("10", Category.subscriptions, currency=CurrencyCode.USD),),
    ),
    ("аптека 18 евро 40", "pharmacy 18.40", (_expense("18.40", Category.health),)),
    (
        "фриланс заказ 450 евро пришёл сегодня",
        "freelance payment 450 euros came in today",
        (_income("450", Category.freelance),),
    ),
    ("аренда квартиры 950 евро", "rent 950 euros", (_expense("950", Category.housing),)),
    ("подарок маме 70 евро", "gift for mom 70 euros", (_expense("70", Category.gifts, Category.family),)),
    ("кэшбэк 6 евро", "cashback 6 euros", (_income("6", Category.cashback),)),
    (
        "позавчера ужин в ресторане 58 евро на двоих",
        "dinner at a restaurant the day before yesterday 58 euros for two",
        (_expense("58", Category.food, Category.entertainment, dates=(DAY_BEFORE_YESTERDAY,)),),
    ),
    ("проезд на метро 2,80", "metro ticket 2.80", (_expense("2.80", Category.transport),)),
    (
        "купил учебник за 35 евро и тетради за 6",
        "textbook 35 euros and notebooks 6",
        (_expense("35", Category.education), _expense("6", Category.education, Category.shopping)),
    ),
    ("ветеринар 80 евро", "vet visit 80 euros", (_expense("80", Category.health, Category.family, Category.other),)),
    ("продал велосипед за 150 евро", "sold my bike for 150 euros", (_income("150", Category.sales),)),
    (
        "парковка 4 евро, мойка машины 15 евро, бензин 55",
        "parking 4 euros, car wash 15 euros, fuel 55",
        (
            _expense("4", Category.car, Category.transport),
            _expense("15", Category.car),
            _expense("55", Category.car, Category.transport),
        ),
    ),
    (
        "курсы английского 120 долларов",
        "english course 120 dollars",
        (_expense("120", Category.education, currency=CurrencyCode.USD),),
    ),
    (
        "спортзал абонемент 45 евро",
        "gym membership 45 euros",
        (_expense("45", Category.health, Category.subscriptions, Category.entertainment),),
    ),
    ("перевёл другу 30 евро", "sent a friend 30 euros", (_expense("30", Category.other, Category.gifts),)),
    ("хлеб, молоко и сыр на 9 евро", "bread milk and cheese for 9 euros", (_expense("9", Category.food),)),
    ("стрижка 25", "haircut 25", (_expense("25", Category.beauty),)),
]
# Фразы по очереди ru и en
VOICE_PHRASES = [
    VoicePhrase(locale, text, expected) for ru, en, expected in VOICE_CASES for locale, text in (("ru", ru), ("en", en))
]


def _money(rng: random.Random, category: Category) -> Decimal:
    low, high = AMOUNT_RANGES.get(category, DEFAULT_AMOUNT_RANGE)
    return Decimal(str(round(rng.uniform(low, high), 2)))


def _month_request(index: int, rng: random.Random) -> Request:
    """Тестовый месяц: до 50 операций (первые месяцы — ровно 50, для максимума), бюджеты, ru и en по очереди."""
    locale = "ru" if index % 2 == 0 else "en"
    currency = CurrencyCode.EUR.value if index % 3 else CurrencyCode.USD.value
    other_currency = CurrencyCode.USD.value if currency == CurrencyCode.EUR.value else CurrencyCode.EUR.value
    # Прошедшие месяцы: с октября 2026 назад
    month_index = 2026 * 12 + 9 - index
    month_start = date(month_index // 12, month_index % 12 + 1, 1)
    tx_count = MAX_TRANSACTIONS if index < 4 else rng.randint(MIN_TRANSACTIONS_FOR_INSIGHTS, MAX_TRANSACTIONS)

    expense_categories = sorted(EXPENSE_CATEGORIES, key=lambda c: c.value)
    income_categories = sorted(INCOME_CATEGORIES - {Category.other}, key=lambda c: c.value)
    weights = [EXPENSE_WEIGHTS.get(c, 1) for c in expense_categories]
    transactions: list[Transaction] = []
    for n in range(tx_count):
        is_income = n < max(1, tx_count // 12)
        category = rng.choice(income_categories) if is_income else rng.choices(expense_categories, weights)[0]
        tx_currency = other_currency if rng.random() < 0.1 else currency
        amount = _money(rng, category)
        transactions.append(
            Transaction(
                amount=amount,
                currency=tx_currency,
                base_amount=(amount * Decimal("1.08")).quantize(Decimal("0.01")) if tx_currency != currency else amount,
                type=TransactionType.income if is_income else TransactionType.expense,
                category=category,
                date=datetime.combine(month_start + timedelta(days=rng.randint(0, 27)), datetime.min.time(), timezone.utc),
            )
        )
    transactions.sort(key=lambda tx: tx.date, reverse=True)

    budgets: list[BudgetResponse] = []
    for category in rng.sample(expense_categories, rng.randint(0, 5)):
        spent = sum((tx.base_amount for tx in transactions if tx.category == category), Decimal("0.00"))
        limit = Decimal(rng.choice((50, 100, 200, 300, 500, 1000)))
        budgets.append(
            BudgetResponse(
                id=uuid.uuid4(),
                category=category,
                limit_amount=limit,
                currency=currency,
                month=month_start.month,
                year=month_start.year,
                spent=spent,
                remaining=limit - spent,
            )
        )

    summary = AIService.build_summary(transactions, budgets, currency, locale)
    system, prompt = AIService.build_request(summary, locale, currency)
    label = f"{month_start:%Y-%m} {locale} {currency}, операций {tx_count}, лимитов {len(budgets)}"
    return Request(AIRequestKind.insights, label, locale, system, prompt)


def build_requests(months: int, phrases: int) -> list[Request]:
    """Тестовые запросы: месяцы для ИИ-отчёта и фразы для голосового разбора (поровну ru и en)."""
    rng = random.Random(SEED)
    requests = [_month_request(i, rng) for i in range(months)]
    for phrase in VOICE_PHRASES[:phrases]:
        system, prompt = VoiceParseService.build_request(phrase.text, phrase.locale, CurrencyCode.EUR, VOICE_TODAY)
        requests.append(Request(AIRequestKind.voice, phrase.text, phrase.locale, system, prompt))
    return requests


def _is_valid_answer(kind: AIRequestKind, text: str) -> bool:
    """Ответ — JSON с полем, которое читает сервис: insights у отчёта, records у разбора."""
    try:
        data = extract_json(text)
    except (ValueError, AttributeError):
        return False
    field = "insights" if kind is AIRequestKind.insights else "records"
    return isinstance(data, dict) and isinstance(data.get(field), list)


async def _call_claude(client: anthropic.AsyncAnthropic, variant: Variant, request: Request) -> tuple[str, TokenUsage]:
    """Claude Haiku: размышления адаптивные, глубина — effort; temperature не передаётся (Haiku 5.5 её не принимает)."""
    response = await client.messages.create(
        model=variant.model,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=request.system,
        messages=[{"role": "user", "content": request.prompt}],
        thinking={"type": "adaptive"},
        output_config={"effort": variant.effort},
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    text = "".join(block.text for block in response.content if block.type == "text")
    return text, claude_usage(response)


async def run_one(
    client: anthropic.AsyncAnthropic, variant: Variant, request: Request, limiter: asyncio.Semaphore
) -> Result:
    """Вызов с повторами при перегрузке, лимите и обрыве связи; время — от первой попытки до ответа."""
    result = Result(variant.name, variant.model, request.kind.value, request.label, request.locale, 0.0)
    async with limiter:
        started = time.perf_counter()
        for attempt in range(1, CALL_ATTEMPTS + 1):
            result.attempts = attempt
            try:
                text, usage = await _call_claude(client, variant, request)
                result.text, result.usage = text, usage
                result.cost_usd = usage.cost_usd(variant.model)
                result.valid_json = _is_valid_answer(request.kind, text)
                result.error = None
                break
            except RETRYABLE_ERRORS as err:
                result.error = f"{type(err).__name__}: {err}"[:300]
                if attempt < CALL_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
            except Exception as err:
                result.error = f"{type(err).__name__}: {err}"[:300]
                break
        result.seconds = round(time.perf_counter() - started, 2)
    print(
        f"  {variant.name:<13} {request.kind.value:<8} {'ошибка' if result.error else 'ок':<6} "
        f"{result.seconds:>5.1f} с  {request.label[:60]}",
        file=sys.stderr,
    )
    return result


def _stats(values: list[float]) -> tuple[float, float]:
    return (statistics.mean(values), max(values)) if values else (0.0, 0.0)


def report(results: list[Result], reports_per_month: int, voice_per_month: int) -> str:
    """Таблицы в Markdown: расход на вызов и себестоимость пользователя в месяц."""
    lines = [
        "## Расход на один вызов",
        "",
        "| Вариант | Модель | Вид | Успешно | JSON ок | Вход ср/макс | Ответ ср/макс | Размышления ср/макс "
        "| $ за вызов ср/макс | Время ср/макс, с |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    avg_cost: dict[tuple[str, str], tuple[float, float]] = {}
    for name in dict.fromkeys(r.variant for r in results):
        for kind in AIRequestKind:
            group = [r for r in results if r.variant == name and r.kind == kind.value]
            if not group:
                continue
            ok = [r for r in group if r.usage]
            tokens = {f: _stats([getattr(r.usage, f) for r in ok]) for f in ("input", "output", "thinking")}
            cost = _stats([r.cost_usd or 0.0 for r in ok])
            seconds = _stats([r.seconds for r in ok])
            avg_cost[(name, kind.value)] = cost
            lines.append(
                f"| {name} | {group[0].model} | {kind.value} | {len(ok)}/{len(group)} "
                f"| {sum(r.valid_json for r in group)}/{len(group)} "
                + " ".join(f"| {tokens[f][0]:.0f} / {tokens[f][1]:.0f}" for f in ("input", "output", "thinking"))
                + f" | {cost[0]:.6f} / {cost[1]:.6f} | {seconds[0]:.1f} / {seconds[1]:.1f} |"
            )

    full_reports = config.ai_daily_limit_per_user * DAYS_IN_MONTH
    full_voice = config.voice_daily_limit_per_user * DAYS_IN_MONTH
    lines += [
        "",
        "## Себестоимость пользователя в месяц, $",
        "",
        f"Обычное использование: {reports_per_month} отчётов и {voice_per_month} голосовых разборов в месяц. "
        f"Лимиты выбраны полностью: {config.ai_daily_limit_per_user} отчётов и {config.voice_daily_limit_per_user} "
        f"разборов в сутки × {DAYS_IN_MONTH} дней = {full_reports} и {full_voice}. Ср — по средней цене вызова, "
        "макс — по максимальной.",
        "",
        "| Вариант | Обычное ср | Обычное макс | Лимиты ср | Лимиты макс |",
        "|---|---|---|---|---|",
    ]
    for name in dict.fromkeys(r.variant for r in results):
        insights = avg_cost.get((name, AIRequestKind.insights.value), (0.0, 0.0))
        voice = avg_cost.get((name, AIRequestKind.voice.value), (0.0, 0.0))
        cells = [
            reports * insights[i] + voices * voice[i]
            for reports, voices in ((reports_per_month, voice_per_month), (full_reports, full_voice))
            for i in (0, 1)
        ]
        lines.append(f"| {name} | " + " | ".join(f"{c:.4f}" for c in cells) + " |")

    failed = [r for r in results if r.error]
    total = sum(r.cost_usd or 0.0 for r in results)
    lines += ["", f"Стоимость прогона: ${total:.4f}. Ошибок: {len(failed)}."]
    lines += [f"- {r.variant} {r.kind} «{r.label[:50]}»: {r.error}" for r in failed[:20]]
    return "\n".join(lines)


VOICE_FIELDS = ("amount", "type", "category", "date", "currency")
# Сумма в формате данных: символ валюты рядом, две цифры после разделителя, тысячи — через пробел, запятую или точку
_NUMBER = r"\d+(?:[ \u00a0\u202f,.]\d{3})*[.,]\d{2}(?!\d)"
_SYMBOL = r"[$£¥\u20a0-\u20cf]"
MONEY_PATTERN = re.compile(rf"{_SYMBOL}\s?({_NUMBER})|(?<![\d.,])({_NUMBER})\s?{_SYMBOL}")
CYRILLIC_PATTERN = re.compile(r"[а-яё]", re.IGNORECASE)
LETTER_PATTERN = re.compile(r"[^\W\d_]")


@dataclass
class VoiceScore:
    """Разбор фразы против эталона: поля записей считаются по записям, совпавшим по сумме."""

    records: int
    count_ok: bool = False
    amount: int = 0
    type: int = 0
    category: int = 0
    date: int = 0
    currency: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def exact(self) -> bool:
        return self.count_ok and all(getattr(self, f) == self.records for f in VOICE_FIELDS)


def score_voice(text: str, expected: tuple[Expected, ...]) -> VoiceScore:
    """Записи сравниваются после нормализации сервиса — в том виде, в каком их увидит пользователь."""
    score = VoiceScore(len(expected))
    try:
        raw = extract_json(text).get("records", [])
    except (ValueError, AttributeError):
        score.problems.append("не JSON")
        return score
    drafts = VoiceParseService._normalize(raw if isinstance(raw, list) else [], CurrencyCode.EUR, VOICE_TODAY)
    score.count_ok = len(drafts) == len(expected)
    if not score.count_ok:
        score.problems.append(f"записей {len(drafts)} вместо {len(expected)}")
    unused = list(drafts)
    for want in expected:
        got = next((d for d in unused if d.amount == want.amount), None)
        if got is None:
            score.problems.append(f"нет суммы {want.amount}")
            continue
        unused.remove(got)
        checks = {
            "amount": True,
            "type": got.type == want.type,
            "category": got.category in want.categories,
            "date": got.date in want.dates,
            "currency": got.currency == want.currency,
        }
        for name, ok in checks.items():
            setattr(score, name, getattr(score, name) + ok)
        wrong = [
            f"{name}={getattr(got, name).value if name != 'date' else got.date}" for name, ok in checks.items() if not ok
        ]
        if wrong:
            score.problems.append(f"{want.amount}: " + ", ".join(wrong))
    return score


def _amounts(text: str) -> set[Decimal]:
    return {Decimal(re.sub(r"\D", "", before or after)) / 100 for before, after in MONEY_PATTERN.findall(text)}


@dataclass
class InsightsCheck:
    """Автопроверки отчёта: число советов, язык ответа и суммы, которых нет в данных (посчитаны моделью или ошибочны)."""

    count: int = 0
    language_ok: bool = False
    foreign_amounts: list[Decimal] = field(default_factory=list)


def check_insights(text: str, request: Request) -> InsightsCheck:
    try:
        insights = extract_json(text).get("insights", [])
    except (ValueError, AttributeError):
        return InsightsCheck()
    joined = " ".join(str(item) for item in insights) if isinstance(insights, list) else ""
    letters = len(LETTER_PATTERN.findall(joined)) or 1
    cyrillic_share = len(CYRILLIC_PATTERN.findall(joined)) / letters
    # Названия категорий и валюты — на языке интерфейса, поэтому в английском ответе кириллицы нет совсем
    language_ok = cyrillic_share > 0.5 if request.locale == "ru" else cyrillic_share == 0
    known = _amounts(request.prompt)
    return InsightsCheck(len(insights), language_ok, sorted(_amounts(joined) - known))


def quality_report(results: list[Result], requests: list[Request]) -> str:
    """Качество ответов: голосовой разбор — по эталону, отчёт — автопроверки; плюс время, сбои и повторы."""
    expected = {(p.locale, p.text): p.expected for p in VOICE_PHRASES}
    prompts = {r.label: r for r in requests if r.kind is AIRequestKind.insights}
    variants = list(dict.fromkeys(r.variant for r in results))
    lines = [
        "## Голосовой разбор: сверка с эталоном",
        "",
        "Записи — после нормализации сервиса; поля — по записям, совпавшим по сумме (из всех эталонных записей).",
        "",
        "| Вариант | Язык | JSON ок | Число записей | Фраза целиком | Сумма | Тип | Категория | Дата | Валюта "
        "| Время ср/макс, с |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    mistakes: list[str] = []
    for name in variants:
        for locale in ("ru", "en"):
            group = [r for r in results if r.variant == name and r.kind == AIRequestKind.voice.value and r.locale == locale]
            if not group:
                continue
            scores = [score_voice(r.text, expected[(r.locale, r.label)]) for r in group]
            total = sum(s.records for s in scores)
            seconds = _stats([r.seconds for r in group if not r.error])
            lines.append(
                f"| {name} | {locale} | {sum(r.valid_json for r in group)}/{len(group)} "
                f"| {sum(s.count_ok for s in scores)}/{len(group)} | {sum(s.exact for s in scores)}/{len(group)} "
                + " ".join(f"| {sum(getattr(s, f) for s in scores)}/{total}" for f in VOICE_FIELDS)
                + f" | {seconds[0]:.1f} / {seconds[1]:.1f} |"
            )
            mistakes += [f"- {name} «{r.label}»: {'; '.join(s.problems)}" for r, s in zip(group, scores) if s.problems]
    lines += ["", "Расхождения:" if mistakes else "Расхождений с эталоном нет.", *mistakes]

    lines += [
        "",
        "## ИИ-отчёт: автопроверки",
        "",
        "Суммы не из данных — посчитаны моделью (итоги, разницы) или ошибочны; проверяются при слепой оценке.",
        "",
        "| Вариант | Язык | JSON ок | Советов мин–макс | Язык ответа | Ответов с суммами не из данных "
        "| Время ср/макс, с |",
        "|---|---|---|---|---|---|---|",
    ]
    foreign: list[str] = []
    for name in variants:
        for locale in ("ru", "en"):
            group = [
                r for r in results if r.variant == name and r.kind == AIRequestKind.insights.value and r.locale == locale
            ]
            if not group:
                continue
            checks = [check_insights(r.text, prompts[r.label]) for r in group]
            counts = [c.count for c in checks]
            seconds = _stats([r.seconds for r in group if not r.error])
            lines.append(
                f"| {name} | {locale} | {sum(r.valid_json for r in group)}/{len(group)} | {min(counts)}–{max(counts)} "
                f"| {sum(c.language_ok for c in checks)}/{len(group)} "
                f"| {sum(bool(c.foreign_amounts) for c in checks)}/{len(group)} | {seconds[0]:.1f} / {seconds[1]:.1f} |"
            )
            foreign += [
                f"- {name} «{r.label}»: {', '.join(str(a) for a in c.foreign_amounts)}"
                for r, c in zip(group, checks)
                if c.foreign_amounts
            ]
    lines += ["", *foreign]

    counted = [r for r in results if r.attempts]
    retried = [r for r in counted if r.attempts > 1]
    lines += [
        "",
        "## Сбои и повторы",
        "",
        f"Ошибок после всех попыток: {sum(bool(r.error) for r in results)} из {len(results)}. "
        + (
            f"Вызовов с повтором: {len(retried)} из {len(counted)}, повторов всего {sum(r.attempts - 1 for r in retried)}."
            if counted
            else "Повторы в этом прогоне не считались."
        ),
    ]
    return "\n".join(lines)


def write_blind(results: list[Result], requests: list[Request], months: int, out_dir: Path) -> None:
    """Слепая оценка отчётов: месяцы поровну ru и en, ответы всех вариантов на месяц — под случайными кодами.

    Ключ «код → вариант» — отдельным файлом, открывать после оценки.
    """
    rng = random.Random()
    insights = [r for r in requests if r.kind is AIRequestKind.insights]
    chosen = [
        request
        for locale in ("ru", "en")
        for request in rng.sample([r for r in insights if r.locale == locale], (months + (locale == "ru")) // 2)
    ]
    rng.shuffle(chosen)
    answers = [[r for r in results if r.label == request.label and not r.error] for request in chosen]
    codes = [f"R{n:02d}" for n in rng.sample(range(1, sum(map(len, answers)) + 1), sum(map(len, answers)))]
    key: dict[str, str] = {}
    lines = ["# Слепая оценка ИИ-отчётов", "", "Оценка каждого ответа: полезность 1–5, ошибки в цифрах, язык, замечания."]
    for number, (request, group) in enumerate(zip(chosen, answers), start=1):
        lines += ["", f"## Месяц {number} ({request.locale})", "", "```", request.prompt, "```"]
        for result in rng.sample(group, len(group)):
            code = codes.pop()
            key[code] = result.variant
            tips = extract_json(result.text).get("insights", [])
            lines += ["", f"### {code}", "", *(f"{n}. {tip}" for n, tip in enumerate(tips, start=1))]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "blind_reports.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "blind_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results(path: Path) -> list[Result]:
    """Вызовы из файла --out прошлого прогона."""
    items = json.loads(path.read_text(encoding="utf-8"))
    return [Result(**{**item, "usage": TokenUsage(**item["usage"]) if item.get("usage") else None}) for item in items]


def dry_run(requests: list[Request], variants: list[Variant]) -> str:
    lines = ["## Тестовые данные (без вызовов моделей)", ""]
    for kind in AIRequestKind:
        for locale in ("ru", "en"):
            group = [r for r in requests if r.kind is kind and r.locale == locale]
            if group:
                avg, peak = _stats([len(r.system) + len(r.prompt) for r in group])
                lines.append(
                    f"- {kind.value} {locale}: запросов {len(group)}, символов промпта ср {avg:.0f} / макс {peak:.0f}"
                )
    lines.append(f"- вызовов при прогоне: {len(requests) * len(variants)} ({', '.join(v.name for v in variants)})")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Замер расхода, себестоимости и качества ИИ (AI-01, AI-02)")
    parser.add_argument("--variants", default=",".join(VARIANTS), help=f"через запятую: {', '.join(VARIANTS)}")
    parser.add_argument("--months", type=int, default=24, help="тестовых месяцев для ИИ-отчёта")
    parser.add_argument("--phrases", type=int, default=len(VOICE_PHRASES), help="голосовых фраз (до 50)")
    parser.add_argument("--reports-per-month", type=int, default=4, help="отчётов в месяц при обычном использовании")
    parser.add_argument("--voice-per-month", type=int, default=60, help="разборов в месяц при обычном использовании")
    parser.add_argument("--out", type=Path, help="JSON со всеми вызовами и ответами моделей (вне репозитория)")
    parser.add_argument("--dry-run", action="store_true", help="только собрать данные, без вызовов моделей")
    parser.add_argument("--evaluate", type=Path, help="оценить ответы из файла --out прошлого прогона, без вызовов")
    parser.add_argument("--blind", type=Path, help="папка для слепой оценки отчётов (вне репозитория)")
    parser.add_argument("--blind-months", type=int, default=7, help="месяцев для слепой оценки (ответы всех вариантов)")
    args = parser.parse_args()

    variants = [VARIANTS[name.strip()] for name in args.variants.split(",") if name.strip()]
    requests = build_requests(args.months, args.phrases)
    if args.dry_run:
        print(dry_run(requests, variants))
        return 0
    if args.evaluate:
        saved = load_results(args.evaluate)
        print(quality_report(saved, requests))
        if args.blind:
            write_blind(saved, requests, args.blind_months, args.blind)
        return 0

    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY не задан в .env", file=sys.stderr)
        return 2
    client = anthropic.AsyncAnthropic(timeout=CALL_TIMEOUT_SECONDS, max_retries=0)

    limiter = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(run_one(client, v, r, limiter) for v in variants for r in requests))
    print(report(list(results), args.reports_per_month, args.voice_per_month))
    print()
    print(quality_report(list(results), requests))
    if args.blind:
        write_blind(list(results), requests, args.blind_months, args.blind)
    if args.out:
        args.out.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nВызовы и ответы сохранены: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
