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
    # Актуальная версия пользовательского соглашения не принята: доступны только профиль, принятие, выход и удаление
    TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
    # Клиент принимает не текущую версию документа (вышла новая): запросить версии заново
    LEGAL_VERSION_OUTDATED = "LEGAL_VERSION_OUTDATED"

    # Транзакции
    TRANSACTION_NOT_FOUND = "TRANSACTION_NOT_FOUND"
    INVALID_TRANSACTION_AMOUNT = "INVALID_TRANSACTION_AMOUNT"

    # Бюджеты
    BUDGET_NOT_FOUND = "BUDGET_NOT_FOUND"
    BUDGET_LIMIT_EXCEEDED = "BUDGET_LIMIT_EXCEEDED"

    # Синхронизация
    SYNC_FAILED = "SYNC_FAILED"
    INVALID_CURSOR = "INVALID_CURSOR"
    ID_CONFLICT = "ID_CONFLICT"
    # Запись батча /sync не прошла проверку схемы: уходит в rejected, остальные записи сохраняются
    INVALID_CATEGORY = "INVALID_CATEGORY"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    INVALID_SYNC_ITEM = "INVALID_SYNC_ITEM"

    # Безопасность и лимиты
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # Тарифы: лимит функции на тарифе пользователя исчерпан (details: feature, limit, resets_at) → пейволл
    PLAN_LIMIT_REACHED = "PLAN_LIMIT_REACHED"

    # Валюты
    INVALID_CURRENCY = "INVALID_CURRENCY"

    # Семейный Pro
    FAMILY_NOT_FOUND = "FAMILY_NOT_FOUND"  # Пользователь не состоит в семье (или участник уже не в ней)
    FAMILY_PLAN_REQUIRED = "FAMILY_PLAN_REQUIRED"  # Создать семью можно только с действующим семейным Pro
    FAMILY_ALREADY_MEMBER = "FAMILY_ALREADY_MEMBER"  # Человек уже в семье: сначала выйти из текущей
    FAMILY_FULL = "FAMILY_FULL"  # Нет свободных мест (с учётом действующих приглашений)
    FAMILY_INVITE_INVALID = "FAMILY_INVITE_INVALID"  # Код не найден, истёк, использован или отозван
    FAMILY_OWNER_ONLY = "FAMILY_OWNER_ONLY"  # Действие доступно только владельцу семьи
    FAMILY_FROZEN = "FAMILY_FROZEN"  # Семья заморожена: общие записи только для чтения, приглашений нет
    FAMILY_AUTHOR_ONLY = "FAMILY_AUTHOR_ONLY"  # Отметку «личная / общая» меняет только автор записи


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


class ConflictException(AppException):
    """Исключение при конфликте с текущим состоянием на сервере (HTTP 409)."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            code=code,
            message=message,
            status_code=status.HTTP_409_CONFLICT,
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


class PlanLimitReachedException(AppException):
    """Лимит функции на тарифе исчерпан (HTTP 403): не частота запросов (429), а условие тарифа."""

    def __init__(self, details: dict[str, Any], message: str = "Лимит функции на текущем тарифе исчерпан"):
        super().__init__(
            code=ErrorCode.PLAN_LIMIT_REACHED,
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
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
