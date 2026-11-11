"""Общие помощники для вызовов Google Gemini: клиент, разбор JSON ответа и суточная квота вызовов в Redis."""

import json
import re

from google import genai
from redis import asyncio as aioredis

from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Срок жизни суточного счётчика вызовов: ключ содержит дату UTC, через сутки он больше не нужен
DAILY_QUOTA_TTL_SECONDS = 86400


def is_placeholder_key(api_key: str) -> bool:
    """Ключ не задан или остался шаблоном из .env.example (sk-..., your-...)."""
    return not api_key or api_key.startswith(("sk-", "your-"))


def gemini_configured() -> bool:
    """Задан ли настоящий ключ Gemini API."""
    return not is_placeholder_key(config.gemini_api_key)


def create_gemini_client() -> genai.Client:
    """Клиент Gemini; без настоящего ключа — с заглушкой: сервисы проверяют `gemini_configured()` до вызова LLM."""
    return genai.Client(api_key=config.gemini_api_key if gemini_configured() else "placeholder")


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
