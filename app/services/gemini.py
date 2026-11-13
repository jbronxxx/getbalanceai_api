"""Общие помощники для вызовов Google Gemini: клиент, вызов с повторами и учётом токенов, разбор JSON, квота в Redis."""

import asyncio
import json
import re

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from redis import asyncio as aioredis

from app.services.ai_usage import AIRequestKind, gemini_usage, record_usage
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Срок жизни суточного счётчика вызовов: ключ содержит дату UTC, через сутки он больше не нужен
DAILY_QUOTA_TTL_SECONDS = 86400

# Паузы перед повторными попытками: попыток на одну больше, чем пауз
RETRY_PAUSES_SECONDS = (0.5, 1.5)
# Временные сбои Gemini, после которых есть смысл повторить: перегрузка модели (503, «high demand»),
# внутренняя ошибка (500), лимит частоты (429), сбой шлюза
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Таймаут HTTP-запроса и сбой соединения (SDK ходит в Gemini через httpx)
_REQUEST_TIMEOUT_ERRORS = (httpx.TimeoutException, httpx.TransportError)
# Запас сверх HTTP-таймаута попытки: запрос обрывает сам HTTP-клиент, wait_for — страховка
_ATTEMPT_GRACE_SECONDS = 1.0


def is_placeholder_key(api_key: str) -> bool:
    """Ключ не задан или остался шаблоном из .env.example (sk-..., your-...)."""
    return not api_key or api_key.startswith(("sk-", "your-"))


def gemini_configured() -> bool:
    """Задан ли настоящий ключ Gemini API."""
    return not is_placeholder_key(config.gemini_api_key)


def create_gemini_client() -> genai.Client:
    """Клиент Gemini; без настоящего ключа — с заглушкой: сервисы проверяют `gemini_configured()` до вызова LLM."""
    return genai.Client(api_key=config.gemini_api_key if gemini_configured() else "placeholder")


def _attempt_models(model: str) -> list[str]:
    """Модели по попыткам: основная, а последняя попытка — на запасной (другая модель реже перегружена вместе)."""
    attempts = len(RETRY_PAUSES_SECONDS) + 1
    fallback = config.ai_fallback_model.strip()
    if fallback and fallback != model:
        return [model] * (attempts - 1) + [fallback]
    return [model] * attempts


def _describe_failure(err: BaseException) -> str:
    """Причина неудачной попытки для журнала: код и статус Gemini или таймаут, без текста запроса и ответа."""
    if isinstance(err, genai_errors.APIError):
        return f"{err.code} {err.status or ''}".strip()
    if isinstance(err, httpx.TransportError) and not isinstance(err, httpx.TimeoutException):
        return "обрыв соединения"
    return "таймаут"


async def generate_text(
    client: genai.Client,
    model: str,
    contents: str,
    gen_config: types.GenerateContentConfig,
    kind: AIRequestKind,
) -> str:
    """Текст ответа Gemini с повторами при временных сбоях; всё — в пределах AI_REQUEST_TIMEOUT.

    Каждая попытка ограничена AI_ATTEMPT_TIMEOUT на уровне HTTP-запроса (у SDK по умолчанию таймаута нет)
    и через wait_for. Сам SDK запросы не повторяет. Перегрузка (503), 500, 429 и
    таймаут — повтор после паузы, последняя попытка — на запасной модели (AI_FALLBACK_MODEL). Прочие ошибки
    (неверный ключ, запрос) и исчерпанный общий срок — исключение последней попытки.
    Расход токенов успешной попытки пишется с меткой ответившей модели и вида запроса `kind` (см. ai_usage).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.ai_request_timeout
    models = _attempt_models(model)
    for attempt, name in enumerate(models, start=1):
        attempt_timeout = min(config.ai_attempt_timeout, deadline - loop.time())
        attempt_config = gen_config.model_copy(
            update={"http_options": types.HttpOptions(timeout=max(int(attempt_timeout * 1000), 1))}
        )
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=name, contents=contents, config=attempt_config),
                timeout=attempt_timeout + _ATTEMPT_GRACE_SECONDS,
            )
        except (TimeoutError, genai_errors.APIError, *_REQUEST_TIMEOUT_ERRORS) as err:
            if isinstance(err, genai_errors.APIError) and err.code not in RETRYABLE_STATUS_CODES:
                raise
            logger.warning(f"Gemini {name}: попытка {attempt}/{len(models)} не удалась — {_describe_failure(err)}")
            pause = RETRY_PAUSES_SECONDS[attempt - 1] if attempt < len(models) else 0
            # Следующей попытке нужно хотя бы полсекунды после паузы, иначе это последняя
            if attempt == len(models) or deadline - loop.time() - pause < 0.5:
                raise
            await asyncio.sleep(pause)
            continue
        if attempt > 1:
            logger.info(f"Gemini {name} ответил с попытки {attempt}/{len(models)}")
        try:
            record_usage(name, kind, gemini_usage(response))
        except Exception as err:
            # Сбой учёта не должен терять уже оплаченный ответ модели
            logger.warning(f"Не удалось записать расход токенов Gemini {name}: {err}")
        return response.text or ""
    raise TimeoutError("Попытки вызова Gemini закончились")


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


async def take_daily_quota(redis: aioredis.Redis, key: str) -> int:
    """Занять один вызов суточной квоты и вернуть номер вызова за сутки.

    Атомарный INCR до вызова LLM: при чтении и последующей записи параллельные запросы видели одно
    значение счётчика и все проходили лимит. Ошибка Redis пробрасывается: без счётчика лимит не проверить,
    а каждый вызов LLM платный.
    """
    calls = await redis.incr(key)
    if calls == 1:
        await redis.expire(key, DAILY_QUOTA_TTL_SECONDS)
    return calls


async def release_daily_quota(redis: aioredis.Redis, key: str) -> None:
    """Вернуть занятый вызов: неудачный вызов LLM не должен расходовать суточный лимит."""
    try:
        await redis.decr(key)
    except Exception as err:
        logger.warning(f"Не удалось вернуть квоту AI в Redis ({key}): {err}")
