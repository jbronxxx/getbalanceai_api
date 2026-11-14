"""Вызовы LLM для ИИ-отчёта и голосового разбора: Claude (основная модель) и Gemini (запасная).

Повторы и таймауты, переход на запасную модель, учёт токенов и сбоев, разбор JSON.
Лимиты тарифов — в `plan_limits`.
"""

import asyncio
import enum
import json
import re
from dataclasses import dataclass
from functools import lru_cache

import anthropic
import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.services.ai_usage import (
    AIFailureReason,
    AIRequestKind,
    TokenUsage,
    claude_usage,
    gemini_usage,
    record_failure,
    record_request_failed,
    record_usage,
)
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)


# Паузы перед повторами на той же модели: попыток на одну больше, чем пауз. К запасной модели — без паузы
RETRY_PAUSES_SECONDS = (0.5, 1.5)
# Временные сбои, после которых есть смысл повторить ту же модель; прочие — сразу к запасной
RETRYABLE_REASONS = frozenset(
    {
        AIFailureReason.timeout,
        AIFailureReason.connection,
        AIFailureReason.rate_limit,
        AIFailureReason.overloaded,
        AIFailureReason.server_error,
    }
)
# Предел ответа Claude вместе с размышлениями: отчёт при effort low — около 600 токенов, запас на редкие длинные
CLAUDE_MAX_TOKENS = 16000
# Таймаут HTTP-запроса и сбой соединения Gemini (SDK google-genai ходит через httpx)
_GEMINI_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.TransportError)
# Ошибки одной попытки, после которых решается: повтор, запасная модель или отказ
_ATTEMPT_ERRORS = (
    TimeoutError,
    anthropic.APIError,
    genai_errors.APIError,
    *_GEMINI_TRANSPORT_ERRORS,
)
# Запас сверх HTTP-таймаута попытки: запрос обрывает сам HTTP-клиент, wait_for — страховка
_ATTEMPT_GRACE_SECONDS = 1.0


class LLMProvider(str, enum.Enum):
    """Провайдер модели — по префиксу имени."""

    anthropic = "anthropic"
    google = "google"


def provider_of(model: str) -> LLMProvider:
    """Claude — Anthropic, остальные (gemini-*) — Google."""
    return LLMProvider.anthropic if model.startswith("claude-") else LLMProvider.google


def is_placeholder_key(api_key: str) -> bool:
    """Ключ не задан или остался шаблоном из .env.example (sk-..., your-...)."""
    return not api_key or api_key.startswith(("sk-", "your-"))


def _provider_configured(provider: LLMProvider) -> bool:
    """Задан ли настоящий ключ провайдера: у Anthropic ключи начинаются с sk-ant-, у Gemini «sk-...» — шаблон."""
    if provider is LLMProvider.anthropic:
        return config.anthropic_api_key.startswith("sk-ant-")
    return not is_placeholder_key(config.gemini_api_key)


def attempt_models() -> list[str]:
    """Модели по попыткам: основная, последняя попытка — на запасной (другой провайдер реже сбоит вместе).

    Модели без ключа пропускаются; пустой список — ИИ не настроен.
    """
    attempts = len(RETRY_PAUSES_SECONDS) + 1
    candidates = dict.fromkeys(m for m in (config.ai_model.strip(), config.ai_fallback_model.strip()) if m)
    usable = [m for m in candidates if _provider_configured(provider_of(m))]
    if len(usable) < 2:
        return usable * attempts
    return [usable[0]] * (attempts - 1) + [usable[1]]


def ai_configured() -> bool:
    """Задан ли ключ хотя бы для основной или запасной модели."""
    return bool(attempt_models())


@dataclass(frozen=True)
class LLMRequest:
    """Запрос к модели: системная инструкция и промпт; ответ — JSON-объект."""

    kind: AIRequestKind
    system: str
    prompt: str
    # Только для Gemini: Claude Haiku 5.5 не принимает temperature (400)
    temperature: float | None = None


class LLMFailure(Exception):
    """Ответ модели получен, но непригоден: отказ (`refusal`) или обрезан по `max_tokens`."""

    def __init__(self, reason: AIFailureReason, detail: str = ""):
        super().__init__(f"{reason.value} {detail}".strip())
        self.reason = reason


def _reason_by_status(status: int | None) -> AIFailureReason:
    """Причина по HTTP-коду: одинаково для Anthropic и Gemini (у Anthropic перегрузка — 529, у Gemini — 503)."""
    if status == 429:
        return AIFailureReason.rate_limit
    if status in (503, 529):
        return AIFailureReason.overloaded
    if status is not None and status >= 500:
        return AIFailureReason.server_error
    if status in (401, 403):
        return AIFailureReason.auth
    if status == 402:
        return AIFailureReason.billing
    if status is not None and 400 <= status < 500:
        return AIFailureReason.bad_request
    return AIFailureReason.other


def failure_reason(err: BaseException) -> AIFailureReason:
    """Причина неудачной попытки для метрик, журнала и решения о повторе."""
    if isinstance(err, LLMFailure):
        return err.reason
    # Лимит расходов организации (429) и приостановленная организация (400) — проблема оплаты: повтор не поможет
    if isinstance(
        err,
        (anthropic.EnforcedSpendLimitReachedError, anthropic.OrganizationOnHoldError),
    ):
        return AIFailureReason.billing
    if isinstance(err, (TimeoutError, anthropic.APITimeoutError, httpx.TimeoutException)):
        return AIFailureReason.timeout
    if isinstance(err, (anthropic.APIConnectionError, httpx.TransportError)):
        return AIFailureReason.connection
    if isinstance(err, anthropic.APIStatusError):
        return _reason_by_status(err.status_code)
    if isinstance(err, genai_errors.APIError):
        return _reason_by_status(err.code)
    return AIFailureReason.other


def _status_of(err: BaseException) -> str:
    """HTTP-код ошибки для журнала (без текста запроса и ответа)."""
    if isinstance(err, anthropic.APIStatusError):
        return f" ({err.status_code})"
    if isinstance(err, genai_errors.APIError):
        return f" ({err.code})"
    return ""


def _record_usage(model: str, kind: AIRequestKind, usage: TokenUsage) -> None:
    """Записать расход оплаченного ответа; сбой учёта не должен терять ответ модели."""
    try:
        record_usage(model, kind, usage)
    except Exception as err:
        logger.warning(f"Не удалось записать расход токенов {model}: {err}")


class LLMClient:
    """Клиенты Anthropic и Gemini и вызов с повторами; сервисы получают его в конструкторе, тесты — мок `generate`."""

    def __init__(
        self,
        claude: anthropic.AsyncAnthropic | None = None,
        gemini: genai.Client | None = None,
    ):
        # Повторы ведёт generate: у SDK Anthropic свои отключены (max_retries=0), у google-genai их нет.
        # Без настоящего ключа — заглушка: attempt_models() не даёт моделей провайдера без ключа
        self.claude = claude or anthropic.AsyncAnthropic(
            api_key=(config.anthropic_api_key if _provider_configured(LLMProvider.anthropic) else "placeholder"),
            max_retries=0,
        )
        self.gemini = gemini or genai.Client(
            api_key=(config.gemini_api_key if _provider_configured(LLMProvider.google) else "placeholder")
        )

    async def generate(self, request: LLMRequest) -> str:
        """Текст ответа модели с повторами и запасной моделью; всё — в пределах AI_REQUEST_TIMEOUT.

        Каждая попытка ограничена AI_ATTEMPT_TIMEOUT (таймаут HTTP-запроса и wait_for). Перегрузка, 5xx,
        429, таймаут и обрыв связи — повтор той же модели после паузы, последняя попытка — на запасной
        (AI_FALLBACK_MODEL). Отказ модели, обрезанный ответ, ошибки ключа, оплаты и запроса — сразу к
        запасной: повтор той же модели не поможет. Каждая неудачная попытка — в `ai_failures_total` по причине,
        запрос без ответа — в `ai_requests_failed_total`; наружу — исключение последней попытки.
        """
        models = attempt_models()
        if not models:
            raise RuntimeError("Ключи LLM не заданы")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.ai_request_timeout
        index = 0
        while True:
            model = models[index]
            attempt_timeout = min(config.ai_attempt_timeout, deadline - loop.time())
            try:
                text = await asyncio.wait_for(
                    self._call(model, request, attempt_timeout),
                    timeout=attempt_timeout + _ATTEMPT_GRACE_SECONDS,
                )
            except (*_ATTEMPT_ERRORS, LLMFailure) as err:
                reason = failure_reason(err)
                record_failure(model, request.kind, reason)
                logger.warning(
                    f"LLM {model} ({request.kind.value}): попытка {index + 1}/{len(models)} не удалась — "
                    f"{reason.value}{_status_of(err)}"
                )
                next_index = self._next_attempt(models, index, reason)
                pause = RETRY_PAUSES_SECONDS[index] if next_index is not None and models[next_index] == model else 0
                # Следующей попытке нужно хотя бы полсекунды после паузы, иначе это последняя
                if next_index is None or deadline - loop.time() - pause < 0.5:
                    record_request_failed(request.kind)
                    raise
                await asyncio.sleep(pause)
                index = next_index
                continue
            if index > 0:
                logger.info(f"LLM {model} ({request.kind.value}) ответила с попытки {index + 1}/{len(models)}")
            return text

    @staticmethod
    def _next_attempt(models: list[str], index: int, reason: AIFailureReason) -> int | None:
        """Номер следующей попытки: временный сбой — по порядку, прочие — первая попытка на другой модели."""
        if reason in RETRYABLE_REASONS:
            return index + 1 if index + 1 < len(models) else None
        return next(
            (i for i in range(index + 1, len(models)) if models[i] != models[index]),
            None,
        )

    async def _call(self, model: str, request: LLMRequest, timeout: float) -> str:
        if provider_of(model) is LLMProvider.anthropic:
            return await self._call_claude(model, request, timeout)
        return await self._call_gemini(model, request, timeout)

    async def _call_claude(self, model: str, request: LLMRequest, timeout: float) -> str:
        """Claude: размышления адаптивные, глубина — AI_EFFORT; sampling-параметры Haiku 5.5 не принимает."""
        response = await self.claude.with_options(timeout=timeout).messages.create(
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=request.system,
            messages=[{"role": "user", "content": request.prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": config.ai_effort},
        )
        # Отказ и обрезанный ответ тоже оплачены
        _record_usage(model, request.kind, claude_usage(response))
        if response.stop_reason == "refusal":
            # Категория классификатора (cyber, bio, general_harms...) — для журнала; текст ответа не логируется
            category = response.stop_details.category if response.stop_details else None
            raise LLMFailure(AIFailureReason.refusal, f"категория {category or 'не указана'}")
        if response.stop_reason == "max_tokens":
            raise LLMFailure(AIFailureReason.max_tokens)
        # Ответ может начинаться с блоков размышлений: текст — только из блоков text
        return "".join(block.text for block in response.content if block.type == "text")

    async def _call_gemini(self, model: str, request: LLMRequest, timeout: float) -> str:
        """Gemini: JSON-ответ; таймаут HTTP-запроса задаётся явно (у SDK по умолчанию его нет)."""
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            system_instruction=request.system,
            temperature=request.temperature,
            http_options=types.HttpOptions(timeout=max(int(timeout * 1000), 1)),
        )
        response = await self.gemini.aio.models.generate_content(model=model, contents=request.prompt, config=gen_config)
        _record_usage(model, request.kind, gemini_usage(response))
        return response.text or ""


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """Один клиент на процесс: пул HTTP-соединений к провайдерам переиспользуется между запросами."""
    return LLMClient()


def extract_json(text: str) -> dict:
    """Извлечь и распарсить JSON-объект из ответа модели, очищая markdown и лишний текст."""
    cleaned = text.strip()

    # Удаляем markdown-блоки вида ```json ... ``` или ``` ... ```
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if match:
            cleaned = match.group(1).strip()

    # Находим границы JSON-объекта от первой { до последней }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        cleaned = cleaned[start : end + 1]

    return json.loads(cleaned)


def sanitize_prompt_text(raw: str) -> str:
    """Текст пользователя для промпта одной строкой: без управляющих символов, переводов строк и разделителей «|».

    Защита от prompt injection: текст не может разорвать строку данных или начать новый блок инструкции.
    """
    cleaned = re.sub(r"[\r\n\t|]+", " ", raw)
    cleaned = "".join(ch for ch in cleaned if ch >= " ")
    return re.sub(r"\s+", " ", cleaned).strip()
