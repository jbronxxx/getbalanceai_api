"""Тесты вызова Gemini с повторами (`app.services.gemini.generate_text`)."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.services import gemini
from app.services.ai_usage import AIRequestKind, TokenUsage
from app.services.gemini import generate_text
from config_reader.config_reader import config

PROMPT = "Фраза пользователя: «кофе 4 евро»"


def _api_error(cls: type[genai_errors.APIError], code: int, status: str) -> genai_errors.APIError:
    """Ошибка Gemini в том виде, в каком её бросает SDK (тело ответа — {"error": {...}})."""
    return cls(code, {"error": {"code": code, "status": status, "message": "high demand"}})


def _ok(text: str = '{"records": []}') -> MagicMock:
    return MagicMock(text=text)


def _client(*outcomes) -> MagicMock:
    """Клиент, у которого каждая попытка по очереди возвращает ответ, бросает исключение или зависает."""
    client = MagicMock(spec=genai.Client)
    queue = list(outcomes)

    async def _generate(**_kwargs):
        outcome = queue.pop(0)
        if outcome == "hang":
            await asyncio.sleep(5)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client.aio.models.generate_content = AsyncMock(side_effect=_generate)
    return client


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без пауз между попытками; основная и запасная модели — фиксированные."""
    monkeypatch.setattr(gemini, "RETRY_PAUSES_SECONDS", (0, 0))
    monkeypatch.setattr(config, "ai_fallback_model", "fallback-model")
    monkeypatch.setattr(config, "ai_request_timeout", 30.0)
    monkeypatch.setattr(config, "ai_attempt_timeout", 10.0)


def _models(client: MagicMock) -> list[str]:
    return [call.kwargs["model"] for call in client.aio.models.generate_content.await_args_list]


@pytest.mark.asyncio
async def test_overloaded_model_retried_then_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """503 «high demand» дважды — третья попытка на запасной модели; в журнале код ошибки, без текста запроса."""
    overloaded = _api_error(genai_errors.ServerError, 503, "UNAVAILABLE")
    client = _client(overloaded, overloaded, _ok("готово"))

    with caplog.at_level(logging.INFO):
        text = await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice)

    assert text == "готово"
    assert _models(client) == ["main-model", "main-model", "fallback-model"]
    messages = [r.getMessage() for r in caplog.records]
    assert sum("503 UNAVAILABLE" in m for m in messages) == 2
    assert any("ответил с попытки 3/3" in m for m in messages)
    assert not any("кофе" in m for m in messages)


@pytest.mark.asyncio
async def test_first_attempt_success_makes_one_call() -> None:
    client = _client(_ok("ок"))
    assert await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice) == "ок"
    assert _models(client) == ["main-model"]


@pytest.mark.asyncio
async def test_each_attempt_has_http_timeout() -> None:
    """Таймаут попытки задаётся самому HTTP-запросу (мс): зависший запрос не держит поток SDK бесконечно."""
    client = _client(_ok())
    base_config = types.GenerateContentConfig(temperature=0)

    await generate_text(client, "main-model", PROMPT, base_config, AIRequestKind.voice)

    sent = client.aio.models.generate_content.await_args.kwargs["config"]
    assert sent.http_options.timeout == 10_000
    assert sent.temperature == 0
    assert base_config.http_options is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        _api_error(genai_errors.ServerError, 500, "INTERNAL"),
        _api_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection reset"),
    ],
    ids=["500", "429", "http_timeout", "connection_error"],
)
async def test_transient_failures_are_retried(failure: BaseException) -> None:
    client = _client(failure, _ok("ок"))
    assert await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice) == "ок"
    assert _models(client) == ["main-model", "main-model"]


@pytest.mark.asyncio
async def test_hanging_attempt_is_cut_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Зависшая попытка обрывается по AI_ATTEMPT_TIMEOUT, следующая отвечает."""
    monkeypatch.setattr(config, "ai_attempt_timeout", 0.05)
    monkeypatch.setattr(gemini, "_ATTEMPT_GRACE_SECONDS", 0.05)
    client = _client("hang", _ok("ок"))

    assert await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice) == "ок"
    assert len(_models(client)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        _api_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT"),
        _api_error(genai_errors.ClientError, 403, "PERMISSION_DENIED"),
        ValueError("не ошибка сети"),
    ],
    ids=["400", "403", "other"],
)
async def test_permanent_failures_are_not_retried(failure: BaseException) -> None:
    """Неверный ключ или запрос повтором не исправить — сразу исключение."""
    client = _client(failure, _ok())
    with pytest.raises(type(failure)):
        await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice)
    assert len(_models(client)) == 1


@pytest.mark.asyncio
async def test_all_attempts_fail_raises_last_error() -> None:
    overloaded = _api_error(genai_errors.ServerError, 503, "UNAVAILABLE")
    client = _client(overloaded, overloaded, overloaded)
    with pytest.raises(genai_errors.ServerError):
        await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice)
    assert len(_models(client)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", ["", "main-model"])
async def test_without_fallback_all_attempts_on_main_model(monkeypatch: pytest.MonkeyPatch, fallback: str) -> None:
    monkeypatch.setattr(config, "ai_fallback_model", fallback)
    overloaded = _api_error(genai_errors.ServerError, 503, "UNAVAILABLE")
    client = _client(overloaded, overloaded, _ok())
    await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice)
    assert _models(client) == ["main-model"] * 3


@pytest.mark.asyncio
async def test_no_retry_past_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Общий срок AI_REQUEST_TIMEOUT исчерпан — новых попыток нет, ошибка последней."""
    monkeypatch.setattr(config, "ai_request_timeout", 0.3)
    monkeypatch.setattr(gemini, "_ATTEMPT_GRACE_SECONDS", 0.05)
    client = _client("hang", _ok())
    with pytest.raises(TimeoutError):
        await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.voice)
    assert len(_models(client)) == 1


@pytest.mark.asyncio
async def test_usage_recorded_for_answering_model_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Расход пишется один раз — по модели, которая ответила; неудачные попытки не учитываются."""
    recorded = []
    monkeypatch.setattr(gemini, "record_usage", lambda model, kind, usage: recorded.append((model, kind, usage)))
    response = MagicMock(text="ок")
    response.usage_metadata = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=120, candidates_token_count=30, thoughts_token_count=7
    )
    overloaded = _api_error(genai_errors.ServerError, 503, "UNAVAILABLE")
    client = _client(overloaded, overloaded, response)

    assert await generate_text(client, "main-model", PROMPT, types.GenerateContentConfig(), AIRequestKind.insights) == "ок"
    assert recorded == [("fallback-model", AIRequestKind.insights, TokenUsage(input=120, output=30, thinking=7))]


@pytest.mark.asyncio
async def test_usage_recording_failure_keeps_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Сбой учёта токенов не теряет оплаченный ответ модели."""

    def _broken(*_args) -> None:
        raise RuntimeError("каталог метрик недоступен")

    monkeypatch.setattr(gemini, "record_usage", _broken)
    with caplog.at_level(logging.WARNING):
        assert (
            await generate_text(
                client=_client(_ok("ок")),
                model="main-model",
                contents=PROMPT,
                gen_config=types.GenerateContentConfig(),
                kind=AIRequestKind.voice,
            )
            == "ок"
        )
    assert "Не удалось записать расход токенов" in caplog.text
