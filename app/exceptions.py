"""Модуль пользовательских доменных исключений и кодов ошибок API.

Определяет единый реестр машиночитаемых строковых кодов ошибок (ErrorCode)
и базовые классы исключений (AppException), которые автоматически
сериализуются в согласованный ErrorResponse контракт.
"""

from enum import StrEnum
from typing import Any, Optional

from fastapi import HTTPException, status


class ErrorCode(StrEnum):
    """Машиночитаемые уникальные строковые коды ошибок для API."""

    # Общие системные ошибки
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"

    # Аутентификация и пользователи
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    EXPIRED_TOKEN = "EXPIRED_TOKEN"
    INVALID_TOKEN = "INVALID_TOKEN"
    TOKEN_REVOKED = "TOKEN_REVOKED"
    TOKEN_NOT_FOUND = "TOKEN_NOT_FOUND"

    # Транзакции
    TRANSACTION_NOT_FOUND = "TRANSACTION_NOT_FOUND"
    INVALID_TRANSACTION_AMOUNT = "INVALID_TRANSACTION_AMOUNT"

    # Бюджеты
    BUDGET_NOT_FOUND = "BUDGET_NOT_FOUND"
    BUDGET_LIMIT_EXCEEDED = "BUDGET_LIMIT_EXCEEDED"

    # Синхронизация
    SYNC_FAILED = "SYNC_FAILED"

    # Безопасность и лимиты
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # Валюты
    INVALID_CURRENCY = "INVALID_CURRENCY"


class AppException(HTTPException):
    """Базовое доменное исключение приложения.

    Наследуется от HTTPException, обеспечивая совместимость с механизмом
    обработки исключений FastAPI и поддержку строковых кодов ошибок.

    Атрибуты:
        code (ErrorCode | str): Уникальный машиночитаемый код ошибки.
        message (str): Человекочитаемое сообщение об ошибке.
        status_code (int): HTTP-код ответа (по умолчанию 400).
        details (dict | None): Дополнительные структурированные данные.
    """

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = str(code)
        self.message = message
        self.details = details


class BadRequestException(AppException):
    """Исключение для некорректных запросов (HTTP 400)."""

    def __init__(
        self,
        code: ErrorCode | str = ErrorCode.BAD_REQUEST,
        message: str = "Некорректный запрос",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class UnauthorizedException(AppException):
    """Исключение при ошибках авторизации/токена (HTTP 401)."""

    def __init__(
        self,
        code: ErrorCode | str = ErrorCode.UNAUTHORIZED,
        message: str = "Требуется авторизация",
        details: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            details=details,
            headers=headers or {"WWW-Authenticate": "Bearer"},
        )


class ForbiddenException(AppException):
    """Исключение при недостатке прав доступа (HTTP 403)."""

    def __init__(
        self,
        code: ErrorCode | str = ErrorCode.FORBIDDEN,
        message: str = "Доступ запрещен",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            details=details,
        )


class NotFoundException(AppException):
    """Исключение для ненайденных сущностей (HTTP 404)."""

    def __init__(
        self,
        code: ErrorCode | str = ErrorCode.NOT_FOUND,
        message: str = "Ресурс не найден",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )


class ServiceUnavailableException(AppException):
    """Исключение при недоступности сервиса или зависимости (HTTP 503)."""

    def __init__(
        self,
        code: ErrorCode | str = ErrorCode.SERVICE_UNAVAILABLE,
        message: str = "Сервис временно недоступен",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            details=details,
        )


class BudgetLimitExceededException(AppException):
    """Исключение при превышении установленного лимита бюджета (HTTP 400)."""

    def __init__(
        self,
        message: str = "Превышен лимит бюджета для данной категории",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=ErrorCode.BUDGET_LIMIT_EXCEEDED,
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            details=details,
        )


class InvalidCurrencyException(BadRequestException):
    """Исключение при указании неподдерживаемой валюты (HTTP 400)."""

    def __init__(
        self,
        message: str = "Указана неподдерживаемая валюта",
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=ErrorCode.INVALID_CURRENCY,
            message=message,
            details=details,
        )
