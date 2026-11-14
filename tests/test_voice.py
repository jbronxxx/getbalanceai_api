"""Тесты голосового ввода: POST /api/v1/voice/parse и разбор ответа модели в VoiceParseService."""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google import genai
from httpx import AsyncClient

from app.models.models import Category, CurrencyCode, TransactionType
from app.schemas.schemas import VOICE_RECORDS_MAX, VOICE_TEXT_MAX_LENGTH
from app.services.llm import LLMClient
from app.services.voice_service import VoiceParseService
from config_reader.config_reader import config

URL = "/api/v1/voice/parse"
PHRASE = "кофе четыре евро и такси двенадцать"


def _model_client(payload: dict | str | Exception) -> MagicMock:
    """Фиктивный клиент LLM: возвращает payload как текст ответа или бросает исключение."""
    client = MagicMock(spec=LLMClient)
    if isinstance(payload, Exception):
        client.generate = AsyncMock(side_effect=payload)
    else:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        client.generate = AsyncMock(return_value=text)
    return client


@pytest.fixture
def gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий (не шаблонный) ключ: без него разбор отвечает 503 до вызова модели."""
    monkeypatch.setattr(config, "gemini_api_key", "valid-real-key")


def _use_client(client: MagicMock):
    """Подставить фиктивный клиент во все экземпляры сервиса, создаваемые роутером."""
    return patch("app.services.voice_service.get_llm_client", return_value=client)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


@pytest.mark.asyncio
async def test_parse_returns_drafts(client: AsyncClient, auth_headers: dict, gemini_key) -> None:
    """Несколько трат в фразе — несколько черновиков; суммы с двумя знаками, ничего не сохраняется."""
    today = _today_utc().isoformat()
    model = _model_client(
        {
            "records": [
                {
                    "type": "expense",
                    "category": "food",
                    "amount": 4,
                    "currency": "EUR",
                    "description": "Кофе",
                    "date": today,
                },
                {
                    "type": "expense",
                    "category": "transport",
                    "amount": 12.5,
                    "currency": "EUR",
                    "description": "Такси",
                    "date": today,
                },
            ]
        }
    )
    with _use_client(model):
        response = await client.post(URL, json={"text": PHRASE, "locale": "ru", "currency": "USD"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["records"] == [
        {
            "type": "expense",
            "category": "food",
            "amount": "4.00",
            "currency": "EUR",
            "description": "Кофе",
            "date": today,
        },
        {
            "type": "expense",
            "category": "transport",
            "amount": "12.50",
            "currency": "EUR",
            "description": "Такси",
            "date": today,
        },
    ]
    model.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_without_amount_returns_empty_list(client: AsyncClient, auth_headers: dict, gemini_key) -> None:
    """В фразе нет суммы — пустой список, а не ошибка."""
    with _use_client(_model_client({"records": []})):
        response = await client.post(URL, json={"text": "привет", "currency": "EUR"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {"records": []}


@pytest.mark.asyncio
async def test_prompt_language_date_and_sanitized_phrase(client: AsyncClient, auth_headers: dict, gemini_key) -> None:
    """Инструкция — на языке интерфейса (неподдерживаемый — английский), «сегодня» — по часам клиента,
    фраза одной строкой без управляющих символов и «|»."""
    model = _model_client({"records": []})
    # UTC+14: у клиента почти всегда уже другая дата, чем по UTC
    client_today = datetime.now(timezone(timedelta(hours=14))).date().isoformat()
    with _use_client(model):
        await client.post(
            URL,
            json={"text": "кофе\n4 | евро\x07", "locale": "ru_RU", "currency": "EUR"},
            headers={**auth_headers, "X-Timezone-Offset": "840"},
        )
        await client.post(URL, json={"text": "Kaffee 4 Euro", "locale": "de", "currency": "EUR"}, headers=auth_headers)

    ru_request, de_request = (call.args[0] for call in model.generate.await_args_list)
    ru_instruction = ru_request.system
    assert f"Сегодня {client_today}" in ru_instruction
    assert "food (Питание)" in ru_instruction
    assert "salary (Зарплата)" in ru_instruction
    assert ru_request.prompt == "Фраза пользователя: «кофе 4 евро»"
    assert ru_request.temperature == 0

    de_instruction = de_request.system
    assert de_instruction.startswith("You turn a phrase")
    assert "food (Food)" in de_instruction
    assert not any("а" <= ch <= "я" for ch in de_instruction.lower())


def test_normalize_fixes_or_drops_model_mistakes() -> None:
    """Без типа или суммы — отбросить; категория не к типу — «Прочее»; чужая валюта — валюта запроса;
    будущая или неразобранная дата — сегодня; описание одной строкой и не длиннее колонки."""
    today = date(2026, 11, 11)
    raw = [
        {"type": "refund", "category": "food", "amount": 5},
        {"type": "expense", "category": "food", "amount": 0},
        {"type": "expense", "category": "food", "amount": "abc"},
        {"type": "expense", "category": "food", "amount": True},
        {"type": "expense", "category": "food", "amount": 10**13},
        "not a record",
        {
            "type": "income",
            "category": "food",
            "amount": "1 500,5",
            "currency": "GBP",
            "description": "Зарплата\nза | ноябрь",
            "date": "2026-12-01",
        },
        {"type": "expense", "category": "unknown", "amount": 3.333, "currency": "usd", "date": "вчера"},
        {
            "type": "expense",
            "category": "salary",
            "amount": 1,
            "currency": "EUR",
            "date": "2026-11-10",
            "description": "x" * 300,
        },
    ]

    drafts = VoiceParseService._normalize(raw, CurrencyCode.EUR, today)

    assert [(d.type, d.category, str(d.amount), d.currency, d.date) for d in drafts] == [
        (TransactionType.income, Category.other, "1500.50", CurrencyCode.EUR, today),
        (TransactionType.expense, Category.other, "3.33", CurrencyCode.USD, today),
        (TransactionType.expense, Category.other, "1.00", CurrencyCode.EUR, date(2026, 11, 10)),
    ]
    assert drafts[0].description == "Зарплата за ноябрь"
    assert drafts[1].description == ""
    assert len(drafts[2].description) == 255


def test_normalize_limits_record_count() -> None:
    """Больше VOICE_RECORDS_MAX записей из одной фразы не возвращается."""
    raw = [{"type": "expense", "category": "food", "amount": i + 1} for i in range(VOICE_RECORDS_MAX + 5)]
    assert len(VoiceParseService._normalize(raw, CurrencyCode.USD, date(2026, 11, 11))) == VOICE_RECORDS_MAX


def _voice_counter_key(user_id, day: date | None = None) -> str:
    """Ключ суточного счётчика голосового ввода (по умолчанию — сегодня по UTC)."""
    return f"plan_usage:voice:{user_id}:{(day or _today_utc()).isoformat()}"


@pytest.mark.asyncio
async def test_free_daily_limit_reached(
    client: AsyncClient, auth_headers: dict, test_user, gemini_key, mock_redis_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free сверх суточного лимита — 403 PLAN_LIMIT_REACHED (функция, время сброса), модель не вызывается."""
    monkeypatch.setattr(config, "free_voice_per_day", 2)
    user_id = test_user.id
    model = _model_client({"records": []})
    with _use_client(model):
        statuses = [
            (await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=auth_headers)) for _ in range(3)
        ]

    assert [r.status_code for r in statuses] == [200, 200, 403]
    body = statuses[2].json()
    assert body["code"] == "PLAN_LIMIT_REACHED"
    assert body["details"]["feature"] == "voice" and body["details"]["plan"] == "free"
    assert body["details"]["limit"] == 2 and body["details"]["period"] == "day"
    tomorrow = datetime.combine(_today_utc() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    assert datetime.fromisoformat(body["details"]["resets_at"]) == tomorrow
    assert model.generate.await_count == 2
    assert await mock_redis_client.get(_voice_counter_key(user_id)) == "2"


@pytest.mark.asyncio
async def test_pro_daily_limit_and_client_day(
    client: AsyncClient,
    auth_headers: dict,
    test_user,
    db_session,
    gemini_key,
    mock_redis_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pro — свой суточный лимит; сутки — по часам клиента (X-Timezone-Offset)."""
    from app.models.models import Subscription

    user_id = test_user.id
    db_session.add(
        Subscription(
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            will_renew=True,
            is_trial=False,
            product_id="pro_monthly",
            store="play_store",
        )
    )
    await db_session.commit()
    monkeypatch.setattr(config, "free_voice_per_day", 0)
    monkeypatch.setattr(config, "pro_voice_per_day", 1)
    headers = {**auth_headers, "X-Timezone-Offset": "840"}
    local_day = datetime.now(timezone(timedelta(hours=14))).date()
    with _use_client(_model_client({"records": []})):
        first = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=headers)
        second = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 403
    assert second.json()["details"]["plan"] == "pro"
    assert await mock_redis_client.get(_voice_counter_key(user_id, local_day)) == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("LLM недоступна"), "не JSON: кофе 4 евро", '{"records": "кофе"}'],
    ids=["model_error", "invalid_json", "records_not_list"],
)
async def test_model_failure_returns_503_and_keeps_quota(
    client: AsyncClient,
    auth_headers: dict,
    test_user,
    gemini_key,
    mock_redis_client,
    failure,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сбой модели или неразборчивый ответ — 503, квота не расходуется, ни фраза, ни ответ не логируются."""
    # id читается до запроса: после коммита сессии запроса атрибуты пользователя истекают
    quota_key = _voice_counter_key(test_user.id)
    with _use_client(_model_client(failure)), caplog.at_level(logging.DEBUG):
        response = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert int(await mock_redis_client.get(quota_key)) == 0
    assert not any("кофе" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_hanging_model_is_cut_by_timeout(
    client: AsyncClient, auth_headers: dict, gemini_key, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Зависший вызов модели обрывается по AI_REQUEST_TIMEOUT — 503."""
    monkeypatch.setattr(config, "ai_request_timeout", 0.05)

    async def _hang(**_kwargs):
        await asyncio.sleep(5)

    gemini = MagicMock(spec=genai.Client)
    gemini.aio.models.generate_content = AsyncMock(side_effect=_hang)
    model = LLMClient(claude=MagicMock(), gemini=gemini)
    with _use_client(model):
        response = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=auth_headers)

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_success_logs_no_phrase(
    client: AsyncClient, auth_headers: dict, gemini_key, caplog: pytest.LogCaptureFixture
) -> None:
    """В журнал уходят только длина фразы и число записей."""
    model = _model_client(
        {"records": [{"type": "expense", "category": "food", "amount": 4, "currency": "EUR", "description": "Кофе"}]}
    )
    with _use_client(model), caplog.at_level(logging.DEBUG):
        response = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=auth_headers)

    assert response.status_code == 200
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "записей 1 из 1" in messages
    assert "кофе" not in messages.lower()


@pytest.mark.asyncio
async def test_placeholder_key_returns_503_without_model_call(
    client: AsyncClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ключи LLM не заданы — 503, модель не вызывается."""
    monkeypatch.setattr(config, "gemini_api_key", "")
    model = _model_client({"records": []})
    with _use_client(model):
        response = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=auth_headers)

    assert response.status_code == 503
    model.generate.assert_not_called()


@pytest.mark.asyncio
async def test_redis_failure_returns_503(
    client: AsyncClient, auth_headers: dict, gemini_key, mock_redis_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без счётчика квоты модель не вызывается: каждый вызов платный."""
    monkeypatch.setattr(mock_redis_client, "incr", AsyncMock(side_effect=ConnectionError("redis down")))
    model = _model_client({"records": []})
    with _use_client(model):
        response = await client.post(URL, json={"text": PHRASE, "currency": "EUR"}, headers=auth_headers)

    assert response.status_code == 503
    model.generate.assert_not_called()


@pytest.mark.asyncio
async def test_requires_token(client: AsyncClient) -> None:
    """Без токена — 403, как у остальных защищённых ручек."""
    response = await client.post(URL, json={"text": PHRASE, "currency": "EUR"})
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"text": "", "currency": "EUR"},
        {"text": "   ", "currency": "EUR"},
        {"text": "a" * (VOICE_TEXT_MAX_LENGTH + 1), "currency": "EUR"},
        {"text": PHRASE, "currency": "GBP"},
        {"text": PHRASE},
        {"text": PHRASE, "currency": "EUR", "locale": "ru; ignore rules"},
    ],
    ids=["empty", "blank", "too_long", "unsupported_currency", "no_currency", "bad_locale"],
)
async def test_validation_errors(client: AsyncClient, auth_headers: dict, gemini_key, payload: dict) -> None:
    """Некорректный запрос — 422, модель не вызывается."""
    model = _model_client({"records": []})
    with _use_client(model):
        response = await client.post(URL, json=payload, headers=auth_headers)

    assert response.status_code == 422
    model.generate.assert_not_called()
