"""Тесты учёта токенов LLM (`app.services.ai_usage`) и меток вида запроса в сервисах отчёта и голосового ввода."""

import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google import genai
from google.genai import types
from prometheus_client import REGISTRY

from app.models.models import CurrencyCode
from app.services import gemini
from app.services.ai_service import AIService
from app.services.ai_usage import (
    MODEL_PRICES,
    AIRequestKind,
    TokenUsage,
    claude_usage,
    gemini_usage,
    record_usage,
)
from app.services.voice_service import VoiceParseService


def _sample(name: str, **labels: str) -> float:
    """Текущее значение серии счётчика; серии ещё нет — 0."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_cost_counts_thinking_at_output_price() -> None:
    """Стоимость: вход по цене входа, ответ и размышления — по цене выхода."""
    usage = TokenUsage(input=1_000_000, output=200_000, thinking=100_000)
    price = MODEL_PRICES["gemini-3.5-flash-lite"]

    assert usage.cost_usd("gemini-3.5-flash-lite") == pytest.approx(price.input_per_mtok + 0.3 * price.output_per_mtok)


def test_cost_unknown_model_is_none() -> None:
    assert TokenUsage(input=10, output=10).cost_usd("unknown-model") is None


def test_gemini_usage_reads_usage_metadata() -> None:
    response = SimpleNamespace(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=900, candidates_token_count=250, thoughts_token_count=40
        )
    )

    assert gemini_usage(response) == TokenUsage(input=900, output=250, thinking=40)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(),
        SimpleNamespace(usage_metadata=None),
        SimpleNamespace(usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=15)),
    ],
)
def test_gemini_usage_missing_fields_are_zero(response: SimpleNamespace) -> None:
    """Нет usage_metadata или поля размышлений (модель не думала) — нули, без исключения."""
    usage = gemini_usage(response)

    assert usage.output == 0 and usage.thinking == 0
    assert usage.input in (0, 15)


def test_claude_usage_splits_thinking_from_output() -> None:
    """output_tokens у Claude включает размышления: ответ — разница; вход — с токенами кэша."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=700,
            cache_read_input_tokens=200,
            cache_creation_input_tokens=None,
            output_tokens=300,
            output_tokens_details=SimpleNamespace(thinking_tokens=120),
        )
    )

    assert claude_usage(response) == TokenUsage(input=900, output=180, thinking=120)


def test_claude_usage_without_details() -> None:
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=50, output_tokens=40, output_tokens_details=None))

    assert claude_usage(response) == TokenUsage(input=50, output=40, thinking=0)


def test_record_usage_increments_counters(caplog: pytest.LogCaptureFixture) -> None:
    """Вызов, токены по типам и стоимость — в счётчиках с метками модели и вида запроса; в журнале — итог вызова."""
    model, kind = "gemini-3.5-flash-lite", AIRequestKind.voice
    labels = {"model": model, "kind": kind.value}
    before_calls = _sample("ai_calls_total", **labels)
    before_input = _sample("ai_tokens_total", **labels, type="input")
    before_thinking = _sample("ai_tokens_total", **labels, type="thinking")
    before_cost = _sample("ai_cost_usd_total", **labels)
    usage = TokenUsage(input=500, output=80, thinking=20)

    with caplog.at_level(logging.INFO):
        record_usage(model, kind, usage)

    assert _sample("ai_calls_total", **labels) == before_calls + 1
    assert _sample("ai_tokens_total", **labels, type="input") == before_input + 500
    assert _sample("ai_tokens_total", **labels, type="thinking") == before_thinking + 20
    assert _sample("ai_cost_usd_total", **labels) == pytest.approx(before_cost + usage.cost_usd(model))
    assert "вход 500, ответ 80, размышления 20" in caplog.text


def test_record_usage_without_price_counts_tokens_only(caplog: pytest.LogCaptureFixture) -> None:
    """Модель без цены: токены учитываются, стоимость — нет, в журнале предупреждение."""
    labels = {"model": "model-without-price", "kind": AIRequestKind.insights.value}
    before_output = _sample("ai_tokens_total", **labels, type="output")

    with caplog.at_level(logging.WARNING):
        record_usage("model-without-price", AIRequestKind.insights, TokenUsage(input=10, output=5))

    assert _sample("ai_tokens_total", **labels, type="output") == before_output + 5
    assert _sample("ai_cost_usd_total", **labels) == 0.0
    assert "Нет цены модели model-without-price" in caplog.text


def _client(text: str) -> MagicMock:
    client = MagicMock(spec=genai.Client)
    response = MagicMock(text=text)
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(prompt_token_count=1, candidates_token_count=1)
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_services_record_their_request_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отчёт пишется с kind=insights, голосовой разбор — с kind=voice."""
    kinds = []
    monkeypatch.setattr(gemini, "record_usage", lambda _model, kind, _usage: kinds.append(kind))

    insights = AIService(db=MagicMock(), client=_client('{"insights": ["совет"]}'), model_name="gemini-3.5-flash-lite")
    await insights._call_gemini("сводка", "ru", "EUR")
    voice = VoiceParseService(db=MagicMock(), client=_client('{"records": []}'), model_name="gemini-3.5-flash-lite")
    await voice._call_gemini("кофе 4 евро", "ru", CurrencyCode.EUR, date(2026, 11, 13))

    assert kinds == [AIRequestKind.insights, AIRequestKind.voice]


def test_build_request_matches_locale() -> None:
    """Запрос отчёта и разбора строится без сервиса и клиента — так его берёт скрипт замера."""
    system_ru, prompt_ru = AIService.build_request("сводка", "ru", "EUR")
    system_en, prompt_en = AIService.build_request("summary", "en", "EUR")
    voice_system, voice_prompt = VoiceParseService.build_request("coffee 4 euro", "en", CurrencyCode.EUR, date(2026, 11, 13))

    assert "сводка" in prompt_ru and "summary" in prompt_en
    assert system_ru != system_en
    assert "coffee 4 euro" in voice_prompt and "2026-11-13" in voice_system
