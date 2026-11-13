"""Учёт расхода токенов LLM: строка журнала и счётчики Prometheus по модели и виду запроса, стоимость по прайсу.

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


@dataclass(frozen=True)
class ModelPrice:
    """Цена модели в долларах за 1M токенов."""

    input_per_mtok: float
    output_per_mtok: float


# Стандартный платный тариф, цены на 13.11.2026. Токены размышлений оплачиваются по цене выходных.
# Скидка за кэш промпта не учитывается: стоимость — оценка сверху. У Claude Haiku 5.5 цена — для промпта до 100K токенов.
MODEL_PRICES: dict[str, ModelPrice] = {
    "gemini-3.5-flash-lite": ModelPrice(input_per_mtok=0.30, output_per_mtok=2.50),
    "gemini-3.1-flash-lite": ModelPrice(input_per_mtok=0.25, output_per_mtok=1.50),
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


AI_CALLS = Counter("ai_calls", "Успешные вызовы LLM", ["model", "kind"])
AI_TOKENS = Counter(
    "ai_tokens", "Токены LLM: input — входные, output — ответ, thinking — размышления", ["model", "kind", "type"]
)
AI_COST_USD = Counter("ai_cost_usd", "Стоимость вызовов LLM в долларах по прайсу приложения", ["model", "kind"])


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
