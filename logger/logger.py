"""Модуль настройки и инициализации логирования."""

import contextvars
import logging
import os
import re
import sys
import uuid

# Контекстная переменная для хранения сквозного ID запроса (Correlation ID / Request ID)
request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Допустимый формат входящего ID запроса: значение попадает в каждую строку лога и в заголовок ответа
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9-]{1,64}")


class RequestIdFilter(logging.Filter):
    """Фильтр логирования, обогащающий каждую запись текущим ID запроса."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx_var.get()
        return True


def get_request_id() -> str:
    """Получить текущий ID запроса из контекста.

    Возвращает:
        str: Значение X-Request-ID из контекста или '-' если вне контекста запроса.
    """
    return request_id_ctx_var.get()


def set_request_id(req_id: str) -> contextvars.Token:
    """Установить ID запроса в текущий контекст.

    Аргументы:
        req_id (str): Идентификатор запроса (Correlation ID / X-Request-ID).

    Возвращает:
        contextvars.Token: Токен для последующего сброса контекста.
    """
    return request_id_ctx_var.set(req_id)


def normalize_request_id(raw: str | None) -> str:
    """Вернуть входящий ID запроса, если он безопасен для логов, иначе сгенерировать новый UUID.

    Ограничение длины и алфавита защищает логи от подделки строк (переводы строк, разделители
    формата) и от раздувания записей произвольными данными клиента.

    Аргументы:
        raw (str | None): Значение заголовка X-Request-ID / X-Correlation-ID.

    Возвращает:
        str: Исходное значение или новый UUID4.
    """
    if raw and REQUEST_ID_PATTERN.fullmatch(raw):
        return raw
    return str(uuid.uuid4())


def mask_email(email: str) -> str:
    """Замаскировать email для логов: первая буква локальной части и домен (j***@gmail.com).

    Аргументы:
        email (str): Адрес электронной почты.

    Возвращает:
        str: Маскированный адрес; при отсутствии домена — только '***'.
    """
    local, sep, domain = email.rpartition("@")
    if not sep or not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def reset_request_id(token: contextvars.Token) -> None:
    """Сбросить ID запроса в контексте по сохраненному токену.

    Аргументы:
        token (contextvars.Token): Токен контекста, возвращенный set_request_id.
    """
    request_id_ctx_var.reset(token)


def _get_log_level() -> int:
    """Определить числовой уровень логирования из конфигурации или переменных окружения."""
    level_name = os.getenv("LOG_LEVEL")
    if not level_name:
        try:
            from config_reader.config_reader import config

            level_name = getattr(config, "log_level", None)
        except Exception:
            level_name = None

    if not level_name:
        level_name = "INFO"

    return getattr(logging, str(level_name).upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Получить или сконфигурировать логгер с единым форматом вывода.

    Аргументы:
        name (str): Имя логгера (как правило, __name__ вызывающего модуля).

    Возвращает:
        logging.Logger: Настроенный экземпляр логгера со стандартным выводом в stdout.
    """
    logger = logging.getLogger(name)

    # Если у логгера уже есть обработчики, не добавляем их повторно
    if logger.handlers:
        return logger

    level = _get_log_level()
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.addFilter(RequestIdFilter())

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | [%(request_id)s] | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
