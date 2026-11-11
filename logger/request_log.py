"""Журнал HTTP-запросов: одна JSON-строка на запрос для разбора в Loki (`| json`).

В журнал попадают только метаданные запроса и заголовки из белого списка. Тела запросов не пишутся
никогда: в них суммы, описания записей, текст голосовой фразы и токены входа. Тело ответа пишется
только при ошибке (4xx/5xx) — это собственный ответ API с кодом ошибки и деталями, без данных пользователя.
"""

import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Request, Response

from logger.logger import _get_log_level, get_request_id

# Заголовки запроса, которые попадают в журнал. Белый список, а не маска: новый секретный заголовок
# (Authorization, Cookie, токены) не окажется в журнале, даже если про него забыть
LOGGED_HEADERS = ("user-agent", "accept-language", "content-type", "content-length", "if-none-match")

# Служебные пути без записи в журнал: их каждые несколько секунд опрашивают Prometheus и healthcheck Docker
SKIPPED_PATHS = frozenset({"/health", "/metrics"})

# Предел длины тела ответа с ошибкой и строки запроса в журнале
MAX_BODY_CHARS = 2048
MAX_QUERY_CHARS = 512

request_logger = logging.getLogger("request")


class _JsonLineFormatter(logging.Formatter):
    """Строка журнала — сам JSON записи, без префикса формата: Loki разбирает её через `| json`."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def _configure() -> None:
    """Подключить вывод журнала запросов в stdout (один раз на процесс)."""
    if request_logger.handlers:
        return
    level = _get_log_level()
    request_logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_JsonLineFormatter())
    request_logger.addHandler(handler)


_configure()


def _truncate(text: str, limit: int) -> str:
    """Обрезать строку до предела с пометкой об обрезке."""
    return text if len(text) <= limit else f"{text[:limit]}…"


def _error_body(body: bytes) -> object:
    """Тело ответа с ошибкой для журнала: разобранный JSON или обрезанный текст."""
    text = body.decode("utf-8", errors="replace")
    if len(text) <= MAX_BODY_CHARS:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return _truncate(text, MAX_BODY_CHARS)


def _level(status: int) -> int:
    """Уровень записи по статусу ответа: 5xx — ERROR, 4xx — WARNING, остальное — INFO."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO


def _write(request: Request, status: int, started: float, response: Response | None, body: bytes | None) -> None:
    """Собрать и записать JSON-строку о завершённом запросе."""
    route = request.scope.get("route")
    record: dict[str, object] = {
        "type": "request",
        "level": logging.getLevelName(_level(status)),
        "request_id": get_request_id(),
        "method": request.method,
        "path": request.url.path,
        "route": getattr(route, "path", None),
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "user_id": getattr(request.state, "user_id", None),
        "client_ip": request.client.host if request.client else None,
    }
    if request.url.query:
        record["query"] = _truncate(request.url.query, MAX_QUERY_CHARS)
    if response is not None and "content-length" in response.headers:
        record["response_size"] = int(response.headers["content-length"])
    headers = {name: request.headers[name] for name in LOGGED_HEADERS if name in request.headers}
    if headers:
        record["headers"] = headers
    if body is not None:
        record["error"] = _error_body(body)
    request_logger.log(_level(status), json.dumps(record, ensure_ascii=False, default=str))


async def log_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Выполнить запрос и записать его в журнал.

    Тело ответа с ошибкой читается целиком и отдаётся клиенту без изменений. Необработанное исключение
    записывается как 500 и пробрасывается дальше — ответ клиенту формирует общий обработчик ошибок.
    """
    if request.url.path in SKIPPED_PATHS:
        return await call_next(request)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _write(request, 500, started, None, None)
        raise

    body = None
    if response.status_code >= 400:
        body = b"".join([chunk async for chunk in response.body_iterator])

        async def _replay() -> AsyncIterator[bytes]:
            yield body

        response.body_iterator = _replay()

    _write(request, response.status_code, started, response, body)
    return response
