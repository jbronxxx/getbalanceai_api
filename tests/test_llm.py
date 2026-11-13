"""Тесты вызова LLM (`app.services.llm.LLMClient.generate`): Claude с повторами, запасная Gemini, причины сбоев."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import httpx2
import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.services import llm
from app.services.ai_usage import AIFailureReason, AIRequestKind, TokenUsage
from app.services.llm import (
    LLMClient,
    LLMRequest,
    ai_configured,
    attempt_models,
    failure_reason,
)
from config_reader.config_reader import config

CLAUDE = "claude-haiku-5-5"
GEMINI = "gemini-3.5-flash-lite"
PROMPT = "Фраза пользователя: «кофе 4 евро»"
REQUEST = LLMRequest(AIRequestKind.voice, "Системная инструкция", PROMPT, temperature=0)
_ANTHROPIC_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _claude_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    """Ошибка Anthropic в том виде, в каком её бросает SDK."""
    return cls("error", response=httpx2.Response(status, request=_ANTHROPIC_REQUEST), body=None)


def _gemini_error(cls: type[genai_errors.APIError], code: int, status: str) -> genai_errors.APIError:
    """Ошибка Gemini в том виде, в каком её бросает SDK (тело ответа — {"error": {...}})."""
    return cls(code, {"error": {"code": code, "status": status, "message": "high demand"}})


def _claude_message(text: str = '{"records": []}', stop_reason: str = "end_turn", stop_details=None) -> SimpleNamespace:
    """Ответ Claude: блок размышлений (текст скрыт), затем текст ответа."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(input_tokens=100, output_tokens=40, output_tokens_details=SimpleNamespace(thinking_tokens=15)),
    )


def _queue(outcomes: list):
    """Каждый вызов по очереди возвращает ответ, бросает исключение или зависает."""

    async def _next(**_kwargs):
        outcome = outcomes.pop(0)
        if outcome == "hang":
            await asyncio.sleep(5)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return _next


def _client(claude: list | None = None, gemini: list | None = None) -> LLMClient:
    claude_client = MagicMock(spec=anthropic.AsyncAnthropic)
    claude_client.with_options.return_value = claude_client
    claude_client.messages.create = AsyncMock(side_effect=_queue(list(claude or [])))
    gemini_client = MagicMock(spec=genai.Client)
    gemini_client.aio.models.generate_content = AsyncMock(side_effect=_queue(list(gemini or [])))
    return LLMClient(claude=claude_client, gemini=gemini_client)


def _models(client: LLMClient) -> list[str]:
    """Модели попыток по порядку: запасная Gemini всегда после попыток Claude."""
    claude = [c.kwargs["model"] for c in client.claude.messages.create.await_args_list]
    gemini = [c.kwargs["model"] for c in client.gemini.aio.models.generate_content.await_args_list]
    return claude + gemini


@pytest.fixture(autouse=True)
def failures(monkeypatch: pytest.MonkeyPatch) -> list:
    """Без пауз; основная — Claude, запасная — Gemini, оба ключа заданы. Возвращает учтённые сбои."""
    monkeypatch.setattr(llm, "RETRY_PAUSES_SECONDS", (0, 0))
    monkeypatch.setattr(config, "ai_model", CLAUDE)
    monkeypatch.setattr(config, "ai_fallback_model", GEMINI)
    monkeypatch.setattr(config, "ai_effort", "low")
    monkeypatch.setattr(config, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(config, "gemini_api_key", "AIza-test")
    monkeypatch.setattr(config, "ai_request_timeout", 30.0)
    monkeypatch.setattr(config, "ai_attempt_timeout", 10.0)
    recorded: list = []
    monkeypatch.setattr(llm, "record_failure", lambda model, _kind, reason: recorded.append((model, reason)))
    return recorded


@pytest.mark.asyncio
async def test_claude_request_shape_and_text_without_thinking() -> None:
    """Claude: effort из настроек, адаптивные размышления, без temperature; ответ — только текстовые блоки."""
    client = _client(claude=[_claude_message("ответ")])

    assert await client.generate(REQUEST) == "ответ"

    client.claude.with_options.assert_called_once_with(timeout=10.0)
    kwargs = client.claude.messages.create.await_args.kwargs
    assert kwargs["model"] == CLAUDE
    assert kwargs["system"] == "Системная инструкция"
    assert kwargs["messages"] == [{"role": "user", "content": PROMPT}]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "low"}
    assert "temperature" not in kwargs
    client.gemini.aio.models.generate_content.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_request_has_http_timeout_and_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini: JSON-ответ, таймаут попытки — самому HTTP-запросу (мс), temperature из запроса."""
    monkeypatch.setattr(config, "anthropic_api_key", "")
    client = _client(gemini=[MagicMock(text="ок")])

    assert await client.generate(REQUEST) == "ок"

    kwargs = client.gemini.aio.models.generate_content.await_args.kwargs
    assert kwargs["model"] == GEMINI and kwargs["contents"] == PROMPT
    sent = kwargs["config"]
    assert sent.http_options.timeout == 10_000
    assert sent.temperature == 0
    assert sent.response_mime_type == "application/json"
    assert sent.system_instruction == "Системная инструкция"


@pytest.mark.asyncio
async def test_overloaded_claude_retried_then_gemini(failures: list, caplog: pytest.LogCaptureFixture) -> None:
    """529 дважды — третья попытка на Gemini; в журнале причина и код, без текста запроса."""
    overloaded = _claude_error(anthropic.OverloadedError, 529)
    client = _client(claude=[overloaded, overloaded], gemini=[MagicMock(text="готово")])

    with caplog.at_level(logging.INFO):
        assert await client.generate(REQUEST) == "готово"

    assert _models(client) == [CLAUDE, CLAUDE, GEMINI]
    assert failures == [(CLAUDE, AIFailureReason.overloaded)] * 2
    messages = [r.getMessage() for r in caplog.records]
    assert sum("overloaded (529)" in m for m in messages) == 2
    assert any("ответила с попытки 3/3" in m for m in messages)
    assert not any("кофе" in m for m in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (_claude_error(anthropic.InternalServerError, 500), AIFailureReason.server_error),
        (_claude_error(anthropic.RateLimitError, 429), AIFailureReason.rate_limit),
        (anthropic.APITimeoutError(request=_ANTHROPIC_REQUEST), AIFailureReason.timeout),
        (anthropic.APIConnectionError(request=_ANTHROPIC_REQUEST), AIFailureReason.connection),
    ],
    ids=["500", "429", "timeout", "connection"],
)
async def test_transient_claude_failures_are_retried(
    failures: list, failure: BaseException, reason: AIFailureReason
) -> None:
    client = _client(claude=[failure, _claude_message("ок")])

    assert await client.generate(REQUEST) == "ок"
    assert _models(client) == [CLAUDE, CLAUDE]
    assert failures == [(CLAUDE, reason)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (_claude_error(anthropic.AuthenticationError, 401), AIFailureReason.auth),
        (_claude_error(anthropic.PermissionDeniedError, 403), AIFailureReason.auth),
        (_claude_error(anthropic.APIStatusError, 402), AIFailureReason.billing),
        (_claude_error(anthropic.EnforcedSpendLimitReachedError, 429), AIFailureReason.billing),
        (_claude_error(anthropic.BadRequestError, 400), AIFailureReason.bad_request),
        (_claude_message("", stop_reason="max_tokens"), AIFailureReason.max_tokens),
    ],
    ids=["401", "403", "402", "spend_limit", "400", "max_tokens"],
)
async def test_permanent_claude_failures_go_straight_to_gemini(
    failures: list, failure: object, reason: AIFailureReason
) -> None:
    """Ключ, оплата, неверный запрос, обрезанный ответ — повтор Claude не поможет: сразу Gemini."""
    client = _client(claude=[failure], gemini=[MagicMock(text="ок")])

    assert await client.generate(REQUEST) == "ок"
    assert _models(client) == [CLAUDE, GEMINI]
    assert failures == [(CLAUDE, reason)]


@pytest.mark.asyncio
async def test_refusal_goes_to_gemini_and_is_billed(
    failures: list, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отказ Claude (stop_reason=refusal): в журнале категория, расход отказа учтён, ответ — от Gemini."""
    recorded = []
    monkeypatch.setattr(llm, "record_usage", lambda model, _kind, usage: recorded.append((model, usage)))
    refusal = _claude_message("", stop_reason="refusal", stop_details=SimpleNamespace(category="general_harms"))
    response = MagicMock(text="ок")
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(prompt_token_count=120, candidates_token_count=30)
    client = _client(claude=[refusal], gemini=[response])

    with caplog.at_level(logging.WARNING):
        assert await client.generate(REQUEST) == "ок"

    assert _models(client) == [CLAUDE, GEMINI]
    assert failures == [(CLAUDE, AIFailureReason.refusal)]
    assert recorded == [
        (CLAUDE, TokenUsage(input=100, output=25, thinking=15)),
        (GEMINI, TokenUsage(input=120, output=30)),
    ]
    assert "refusal" in caplog.text


@pytest.mark.asyncio
async def test_all_attempts_fail_raises_last_error_and_counts_request(monkeypatch: pytest.MonkeyPatch) -> None:
    failed_kinds = []
    monkeypatch.setattr(llm, "record_request_failed", failed_kinds.append)
    overloaded = _claude_error(anthropic.OverloadedError, 529)
    client = _client(claude=[overloaded, overloaded], gemini=[_gemini_error(genai_errors.ServerError, 503, "UNAVAILABLE")])

    with pytest.raises(genai_errors.ServerError):
        await client.generate(REQUEST)

    assert _models(client) == [CLAUDE, CLAUDE, GEMINI]
    assert failed_kinds == [AIRequestKind.voice]


@pytest.mark.asyncio
async def test_permanent_failure_on_fallback_raises() -> None:
    """Ключ Anthropic неверный, Gemini отклонила запрос — исключение без лишних попыток."""
    client = _client(
        claude=[_claude_error(anthropic.AuthenticationError, 401)],
        gemini=[_gemini_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT")],
    )
    with pytest.raises(genai_errors.ClientError):
        await client.generate(REQUEST)
    assert _models(client) == [CLAUDE, GEMINI]


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", ["", CLAUDE])
async def test_without_fallback_all_attempts_on_main_model(monkeypatch: pytest.MonkeyPatch, fallback: str) -> None:
    monkeypatch.setattr(config, "ai_fallback_model", fallback)
    overloaded = _claude_error(anthropic.OverloadedError, 529)
    client = _client(claude=[overloaded, overloaded, _claude_message()])

    await client.generate(REQUEST)
    assert _models(client) == [CLAUDE] * 3


@pytest.mark.asyncio
async def test_without_fallback_permanent_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ai_fallback_model", "")
    client = _client(claude=[_claude_error(anthropic.AuthenticationError, 401), _claude_message()])

    with pytest.raises(anthropic.AuthenticationError):
        await client.generate(REQUEST)
    assert _models(client) == [CLAUDE]


@pytest.mark.asyncio
async def test_hanging_attempt_is_cut_and_retried(monkeypatch: pytest.MonkeyPatch, failures: list) -> None:
    """Зависшая попытка обрывается по AI_ATTEMPT_TIMEOUT, следующая отвечает."""
    monkeypatch.setattr(config, "ai_attempt_timeout", 0.05)
    monkeypatch.setattr(llm, "_ATTEMPT_GRACE_SECONDS", 0.05)
    client = _client(claude=["hang", _claude_message("ок")])

    assert await client.generate(REQUEST) == "ок"
    assert _models(client) == [CLAUDE, CLAUDE]
    assert failures == [(CLAUDE, AIFailureReason.timeout)]


@pytest.mark.asyncio
async def test_no_retry_past_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Общий срок AI_REQUEST_TIMEOUT исчерпан — новых попыток нет, ошибка последней."""
    monkeypatch.setattr(config, "ai_request_timeout", 0.3)
    monkeypatch.setattr(llm, "_ATTEMPT_GRACE_SECONDS", 0.05)
    client = _client(claude=["hang", _claude_message()])

    with pytest.raises(TimeoutError):
        await client.generate(REQUEST)
    assert _models(client) == [CLAUDE]


@pytest.mark.asyncio
async def test_usage_recorded_for_answering_model_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка без ответа модели не оплачена и не учитывается; расход — по ответившей модели."""
    recorded = []
    monkeypatch.setattr(llm, "record_usage", lambda model, kind, usage: recorded.append((model, kind, usage)))
    overloaded = _claude_error(anthropic.OverloadedError, 529)
    client = _client(claude=[overloaded, _claude_message("ок")])

    assert await client.generate(LLMRequest(AIRequestKind.insights, "s", "p")) == "ок"
    assert recorded == [(CLAUDE, AIRequestKind.insights, TokenUsage(input=100, output=25, thinking=15))]


@pytest.mark.asyncio
async def test_usage_recording_failure_keeps_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Сбой учёта токенов не теряет оплаченный ответ модели."""

    def _broken(*_args) -> None:
        raise RuntimeError("каталог метрик недоступен")

    monkeypatch.setattr(llm, "record_usage", _broken)
    with caplog.at_level(logging.WARNING):
        assert await _client(claude=[_claude_message("ок")]).generate(REQUEST) == "ок"
    assert "Не удалось записать расход токенов" in caplog.text


@pytest.mark.parametrize(
    ("anthropic_key", "gemini_key", "expected"),
    [
        ("sk-ant-test", "AIza-test", [CLAUDE, CLAUDE, GEMINI]),
        ("", "AIza-test", [GEMINI] * 3),
        ("sk-...", "AIza-test", [GEMINI] * 3),
        ("sk-ant-test", "your-gemini-key", [CLAUDE] * 3),
        ("", "", []),
    ],
    ids=["both", "no_anthropic", "anthropic_placeholder", "no_gemini", "none"],
)
def test_attempt_models_skip_providers_without_key(
    monkeypatch: pytest.MonkeyPatch, anthropic_key: str, gemini_key: str, expected: list[str]
) -> None:
    monkeypatch.setattr(config, "anthropic_api_key", anthropic_key)
    monkeypatch.setattr(config, "gemini_api_key", gemini_key)

    assert attempt_models() == expected
    assert ai_configured() is bool(expected)


@pytest.mark.asyncio
async def test_generate_without_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "anthropic_api_key", "")
    monkeypatch.setattr(config, "gemini_api_key", "")
    with pytest.raises(RuntimeError):
        await _client().generate(REQUEST)


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (_gemini_error(genai_errors.ServerError, 503, "UNAVAILABLE"), AIFailureReason.overloaded),
        (_gemini_error(genai_errors.ServerError, 500, "INTERNAL"), AIFailureReason.server_error),
        (_gemini_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED"), AIFailureReason.rate_limit),
        (_gemini_error(genai_errors.ClientError, 403, "PERMISSION_DENIED"), AIFailureReason.auth),
        (_gemini_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT"), AIFailureReason.bad_request),
        (httpx.ReadTimeout("read timed out"), AIFailureReason.timeout),
        (httpx.ConnectError("connection reset"), AIFailureReason.connection),
        (TimeoutError(), AIFailureReason.timeout),
        (_claude_error(anthropic.OrganizationOnHoldError, 400), AIFailureReason.billing),
    ],
    ids=["503", "500", "429", "403", "400", "http_timeout", "connect", "wait_for", "org_on_hold"],
)
def test_failure_reason(failure: BaseException, reason: AIFailureReason) -> None:
    assert failure_reason(failure) is reason
