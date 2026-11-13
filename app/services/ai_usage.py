"""Учёт вызовов LLM: расход токенов и стоимость по прайсу, сбои по причинам — журнал и счётчики Prometheus.

Счётчики работают и в multiprocess-режиме gunicorn (PROMETHEUS_MULTIPROC_DIR): значения воркеров суммируются.
"""

import enum
from dataclasses import dataclass

from prometheus_client import Counter

from logger.logger import get_logger

logger = get_logger(__name__)


class AIRequestKind(str, enum.Enum):
    """Вид запроса к модели — метка `kind` в метриках."""

    insights = "insights"
    voice = "voice"


class AIFailureReason(str, enum.Enum):
    """Причина неудачной попытки вызова модели — метка `reason` в `ai_failures_total`."""

    timeout = "timeout"
    connection = "connection"
    rate_limit = "rate_limit"
    overloaded = "overloaded"
    server_error = "server_error"
    auth = "auth"
    billing = "billing"
    bad_request = "bad_request"
    refusal = "refusal"
    max_tokens = "max_tokens"
    other = "other"


@dataclass(frozen=True)
class ModelPrice:
    """Цена модели в долларах за 1M токенов."""

    input_per_mtok: float
    output_per_mtok: float


# Стандартный платный тариф, цены на 13.11.2026. Токены размышлений оплачиваются по цене выходных.
# Скидка за кэш промпта не учитывается: стоимость — оценка сверху. У Claude Haiku 5.5 цена — для промпта до 100K токенов.
MODEL_PRICES: dict[str, ModelPrice] = {
    "gemini-3.5-flash-lite": ModelPrice(input_per_mtok=0.30, output_per_mtok=2.50),
    "claude-haiku-5-5": ModelPrice(input_per_mtok=0.10, output_per_mtok=0.50),
}


@dataclass(frozen=True)
class TokenUsage:
    """Токены одного вызова модели: входные, выходные (ответ) и размышления."""

    input: int
    output: int
    thinking: int = 0

    def cost_usd(self, model: str) -> float | None:
        """Стоимость вызова по прайсу `MODEL_PRICES`; None — цены модели нет."""
        price = MODEL_PRICES.get(model)
        if price is None:
            return None
        return (self.input * price.input_per_mtok + (self.output + self.thinking) * price.output_per_mtok) / 1_000_000


AI_CALLS = Counter("ai_calls", "Оплаченные ответы LLM (и отказ модели, и обрезанный ответ)", ["model", "kind"])
AI_TOKENS = Counter(
    "ai_tokens", "Токены LLM: input — входные, output — ответ, thinking — размышления", ["model", "kind", "type"]
)
AI_COST_USD = Counter("ai_cost_usd", "Стоимость вызовов LLM в долларах по прайсу приложения", ["model", "kind"])
AI_FAILURES = Counter("ai_failures", "Неудачные попытки вызова LLM по причинам", ["model", "kind", "reason"])
AI_REQUESTS_FAILED = Counter(
    "ai_requests_failed", "Запросы к ИИ без ответа: не помогли ни повторы, ни запасная модель", ["kind"]
)


def _token_count(meta: object, field: str) -> int:
    """Число токенов из usage_metadata; поле может отсутствовать или быть None (например, без размышлений)."""
    value = getattr(meta, field, None)
    return value if isinstance(value, int) else 0


def gemini_usage(response: object) -> TokenUsage:
    """Токены из `usage_metadata` ответа Gemini: prompt — вход, candidates — ответ, thoughts — размышления."""
    meta = getattr(response, "usage_metadata", None)
    return TokenUsage(
        input=_token_count(meta, "prompt_token_count"),
        output=_token_count(meta, "candidates_token_count"),
        thinking=_token_count(meta, "thoughts_token_count"),
    )


def claude_usage(response: object) -> TokenUsage:
    """Токены из `usage` ответа Claude.

    Вход — с токенами кэша промпта. `output_tokens` у Claude включает размышления: ответ — разница с
    `output_tokens_details.thinking_tokens`.
    """
    usage = getattr(response, "usage", None)
    thinking = _token_count(getattr(usage, "output_tokens_details", None), "thinking_tokens")
    return TokenUsage(
        input=sum(
            _token_count(usage, field)
            for field in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ),
        output=max(_token_count(usage, "output_tokens") - thinking, 0),
        thinking=thinking,
    )


def record_usage(model: str, kind: AIRequestKind, usage: TokenUsage) -> None:
    """Записать расход вызова в журнал и счётчики Prometheus (`ai_calls_total`, `ai_tokens_total`, `ai_cost_usd_total`)."""
    AI_CALLS.labels(model=model, kind=kind.value).inc()
    for token_type, count in (("input", usage.input), ("output", usage.output), ("thinking", usage.thinking)):
        AI_TOKENS.labels(model=model, kind=kind.value, type=token_type).inc(count)
    cost = usage.cost_usd(model)
    if cost is None:
        logger.warning(f"Нет цены модели {model} в MODEL_PRICES: стоимость вызова не учтена")
        cost_text = "нет цены"
    else:
        AI_COST_USD.labels(model=model, kind=kind.value).inc(cost)
        cost_text = f"${cost:.6f}"
    logger.info(
        f"Расход LLM {model} ({kind.value}): вход {usage.input}, ответ {usage.output}, "
        f"размышления {usage.thinking}, стоимость {cost_text}"
    )


def record_failure(model: str, kind: AIRequestKind, reason: AIFailureReason) -> None:
    """Учесть неудачную попытку вызова модели (`ai_failures_total`)."""
    AI_FAILURES.labels(model=model, kind=kind.value, reason=reason.value).inc()


def record_request_failed(kind: AIRequestKind) -> None:
    """Учесть запрос, оставшийся без ответа после всех попыток (`ai_requests_failed_total`)."""
    AI_REQUESTS_FAILED.labels(kind=kind.value).inc()
